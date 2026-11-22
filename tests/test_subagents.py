import json
from time import monotonic, sleep

from codelite.app.runtime import Runtime
from codelite.app.server import create_app
from codelite.config import AppConfig


def tool_call(name, identifier, **arguments):
    return {"output": [{"type": "function_call", "name": name, "call_id": identifier,
                        "arguments": json.dumps(arguments)}]}


def reply(text):
    return {"output": [{"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": text},
    ]}]}


def test_agent_profiles_resolve_defaults_and_protect_existing_chats(tmp_path):
    app = create_app(AppConfig(data_dir=tmp_path))
    client = app.test_client()
    profiles = client.get("/api/agents").json["agents"]
    assert {profile["name"] for profile in profiles} >= {"General", "Explorer", "Reviewer"}
    custom = client.post("/api/agents", json={
        "name": "Auditor", "model": "fixture-model", "effort": "medium",
        "tools": ["read_file"], "instructions": "Check correctness.",
    }).json
    chat = client.post("/api/conversations", json={
        "workspace": str(tmp_path), "agent_id": custom["id"],
    }).json
    assert (chat["agent_id"], chat["model"], chat["reasoning_effort"]) == (custom["id"], "fixture-model", "medium")
    assert client.delete(f"/api/agents/{custom['id']}").status_code == 400
    assert client.delete("/api/agents/general").status_code == 400
    assert client.post("/api/conversations", json={"agent_id": "unknown"}).status_code == 400


def test_main_and_subagent_exchange_messages_and_track_tool_calls(tmp_path, monkeypatch):
    runtime = Runtime(AppConfig(data_dir=tmp_path))
    (tmp_path / "fact.txt").write_text("verified evidence")
    worker = runtime.agent_profiles.save({"name": "Fixture Reviewer", "model": "worker-model"})
    chat = runtime.create_conversation(str(tmp_path), model="main-model")
    child_steps = main_steps = 0
    job_id = ""
    replied = False

    def respond(body, *, stream):
        nonlocal child_steps, main_steps, job_id, replied
        tools = {tool["name"] for tool in body["tools"]}
        history = json.dumps(body["input"])
        if body["model"] == "worker-model":
            child_steps += 1
            assert tools >= {"read_file", "agent_message"}
            assert not {"shell", "write_file", "subagents", "computer_use", "extensions"} & tools
            if child_steps == 1:
                assert "parent-only material" not in history
                return tool_call("agent_message", "child-1", action="send", message="Which file?")
            if child_steps == 2:
                return tool_call("agent_message", "child-2", action="wait")
            if child_steps == 3:
                assert "Use fact.txt" in history
                return tool_call("write_file", "child-3", path="forbidden.txt", content="wrong")
            if child_steps == 4:
                assert "is unavailable for this agent" in history
                return tool_call("read_file", "child-4", path="fact.txt")
            assert "verified evidence" in history
            return reply("Found verified evidence.")

        main_steps += 1
        assert "subagents" in tools
        if main_steps == 1:
            return tool_call("subagents", "main-1", action="start", profile=worker.id, task="Find the evidence.")
        if not job_id:
            output = next(item["output"] for item in body["input"] if item.get("call_id") == "main-1"
                          and item.get("type") == "function_call_output")
            job_id = json.loads(output)["id"]
        if "Which file?" in history and not replied:
            deadline = monotonic() + 2
            while runtime._state_for(chat).subagents.snapshot()[0]["status"] != "waiting":
                assert monotonic() < deadline
                sleep(.01)
            status, = runtime._state_for(chat).subagents.snapshot()
            assert status["name"] == "Fixture Reviewer" and status["model"] == "worker-model"
            assert status["tool_calls"] == 2
            replied = True
            return tool_call("subagents", f"main-{main_steps}", action="send", id=job_id, message="Use fact.txt")
        if "Found verified evidence." in history:
            return reply("The reviewer found the evidence.")
        assert main_steps <= 8
        return tool_call("subagents", f"main-{main_steps}", action="wait", id=job_id)

    monkeypatch.setattr(runtime.session, "send_responses", respond)
    monkeypatch.setattr(runtime.session, "model_capabilities", lambda _model: {})
    monkeypatch.setattr(runtime.session, "context_window", lambda _model: 100000)
    runtime.start_run(chat, "Delegate to a reviewer. parent-only material")
    live = runtime._state_for(chat)
    try:
        live.thread.join(timeout=7)
        assert not live.busy
        status, = live.subagents.snapshot()
        assert status["status"] == "completed" and status["tool_calls"] == 4 and not status["running"]
        assert replied and not (tmp_path / "forbidden.txt").exists()
        assert "The reviewer found the evidence." in json.dumps(runtime.store.load_items(chat.id))
    finally:
        runtime.cancel_run(chat.id)
