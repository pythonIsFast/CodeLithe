import json
import sys
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep

from codelite.app.runtime import Runtime
from codelite.app.server import create_app
from codelite.config import AppConfig
from codelite.permission.manager import AgentPermissions, PermissionDenied, PermissionManager
from codelite.permission.modes import Mode
from codelite.tools.base import ToolError
from codelite.tools.context import ToolContext
from codelite.tools.files import WRITE_FILE


def tool_call(tool_name, identifier, **arguments):
    return {"output": [{"type": "function_call", "name": tool_name, "call_id": identifier,
                        "arguments": json.dumps(arguments)}]}


def reply(text):
    return {"output": [{"type": "message", "role": "assistant", "content": [
        {"type": "output_text", "text": text},
    ]}]}


def test_agent_profiles_resolve_defaults_and_protect_existing_chats(tmp_path):
    app = create_app(AppConfig(data_dir=tmp_path))
    client = app.test_client()
    profiles = client.get("/api/agents").json["agents"]
    assert {profile["name"] for profile in profiles} >= {"General", "Coder", "Explorer", "Reviewer"}
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
    worker = runtime.agent_profiles.save({"name": "Fixture Reviewer", "model": "worker-model", "tools": ["read_file"]})
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
        return {"output": []}

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


def test_coder_writes_and_uses_mcp_while_main_waits(tmp_path, monkeypatch):
    runtime = Runtime(AppConfig(data_dir=tmp_path / "data"))
    config = tmp_path / ".codelite" / "mcp.json"
    config.parent.mkdir()
    fixture = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"
    config.write_text(json.dumps({"mcpServers": {"fixture": {
        "command": sys.executable, "args": [str(fixture)],
    }}}))
    chat = runtime.create_conversation(str(tmp_path), model="main-model", mode=Mode.ASK)
    release_explorer = Event()
    waiting = Event()
    main_steps = []
    events = []
    publish = runtime.publish

    def capture(cid, name, data):
        events.append((name, data))
        if name == "subagent_waiting":
            waiting.set()
        publish(cid, name, data)

    def respond(body, *, stream):
        tools = {tool["name"]: tool for tool in body["tools"]}
        history = json.dumps(body["input"])
        if "Run the coding task." in history and "subagents" not in tools:
            assert {"write_file", "edit_file", "mcp"} <= tools.keys()
            assert not {"shell", "computer_use", "extensions"} & tools.keys()
            if "wrote" not in history:
                return tool_call("write_file", "wrote", path="result.txt", content="new work")
            if "called-mcp" not in history:
                return tool_call("mcp", "called-mcp", action="call_mcp_tool", server="fixture", name="echo",
                                 arguments={"value": "returned by MCP"})
            assert "returned by MCP" in history
            return reply("Coder finished.")
        if "subagents" not in tools:
            assert "Inspect the workspace." in history
            assert release_explorer.wait(5)
            return reply("Explorer finished.")

        main_steps.append(history)
        if len(main_steps) == 1:
            return {"output": [
                *tool_call("subagents", "start-coder", action="start", profile="coder", task="Run the coding task.")["output"],
                *tool_call("subagents", "start-explorer", action="start", profile="explorer", task="Inspect the workspace.")["output"],
            ]}
        assert "Coder finished." in history
        if len(main_steps) == 2:
            assert "Explorer finished." not in history
            release_explorer.set()
            return {"output": []}
        assert "Explorer finished." in history
        return reply("Both subagents completed.")

    monkeypatch.setattr(runtime, "publish", capture)
    monkeypatch.setattr(runtime.session, "send_responses", respond)
    monkeypatch.setattr(runtime.session, "model_capabilities", lambda _model: {})
    monkeypatch.setattr(runtime.session, "context_window", lambda _model: 100000)
    runtime.start_run(chat, "Ask Coder to build and Explorer to inspect.")
    live = runtime._state_for(chat)
    try:
        assert waiting.wait(3)
        for kind in ("write", "shell"):
            deadline = monotonic() + 3
            while not (prompts := runtime.pending_permissions(chat)):
                assert monotonic() < deadline
                sleep(.01)
            prompt, = prompts
            assert prompt["agent_name"] == "Coder" and prompt["kind"] == kind
            assert len(main_steps) == 1
            assert runtime.reply_permission(chat, prompt["id"], "once")
        live.thread.join(6)
        assert not live.busy and len(main_steps) == 3
        assert (tmp_path / "result.txt").read_text() == "new work"
        assert "Both subagents completed." in json.dumps(runtime.store.load_items(chat.id))
        assert [data["call_id"] for name, data in events if name == "tool_started"] == ["start-coder", "start-explorer"]
    finally:
        release_explorer.set()
        runtime.cancel_run(chat.id)


def test_cancelling_one_subagent_leaves_other_permissions_pending():
    requests = []
    manager = PermissionManager(Mode.ASK, lambda event, data: requests.append((event, data)))
    first = AgentPermissions(manager, "First", "Build")
    second = AgentPermissions(manager, "Second", "Build")
    denied = []

    def write(scope):
        try:
            scope.require_write(f"{scope.name}.txt")
        except PermissionDenied:
            denied.append(scope.name)

    first_thread = Thread(target=write, args=(first,), daemon=True)
    second_thread = Thread(target=write, args=(second,), daemon=True)
    first_thread.start()
    second_thread.start()
    try:
        deadline = monotonic() + 3
        while len(manager.list_pending()) != 2:
            assert monotonic() < deadline
            sleep(.01)
        first.cancel_pending()
        first_thread.join(2)
        prompt, = manager.list_pending()
        assert prompt["agent_name"] == "Second"
        assert manager.reply(prompt["id"], "once")
        second_thread.join(2)
        assert denied == ["First"] and not second_thread.is_alive()
        assert sum(event == "permission_cancelled" for event, _ in requests) == 1
    finally:
        manager.cancel_pending()


def test_write_rejects_a_file_changed_during_approval(tmp_path, monkeypatch):
    path = tmp_path / "result.txt"
    path.write_text("original")
    permission = PermissionManager(Mode.ASK, lambda *_: None)
    monkeypatch.setattr(permission, "require_write", lambda *_: path.write_text("other agent's work"))
    context = ToolContext(workspace=tmp_path, permissions=permission, session=None)
    try:
        WRITE_FILE.run({"path": "result.txt", "content": "stale work"}, context)
    except ToolError as error:
        assert "changed during this operation" in str(error)
    else:
        raise AssertionError("A stale write overwrote another agent's changes")
    assert path.read_text() == "other agent's work"
