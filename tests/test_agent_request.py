# Copyright 2026 Code Lite contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""What the agent actually puts on the wire.

These assert the request body rather than the outcome of a real call: the
reasoning level and the service tier are single keys that are either present
and correct or silently absent, and a live run cannot tell the difference.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from codelite.agent import loop as agent_loop
from codelite.agent.loop import AgentRunner
from codelite.app.runtime import Runtime
from codelite.config import AppConfig
from codelite.db.store import Store
from codelite.permission.manager import PermissionManager
from codelite.permission.modes import Mode
from codelite.tools.context import ToolContext


class FakeSession:
    """Records the body it was handed and never talks to anything."""

    def __init__(self, efforts: list[str] | None = None, fast: bool = True) -> None:
        self.bodies: list[dict[str, Any]] = []
        self._efforts = efforts if efforts is not None else ["low", "medium", "high"]
        self._fast = fast

    def send_responses(self, body: dict[str, Any], *, stream: bool = False) -> Any:
        self.bodies.append(body)
        # An empty stream: _request_turn returns None and the caller stops.
        return iter(())

    def model_capabilities(self, model: str) -> dict[str, Any]:
        return {"efforts": list(self._efforts), "default_effort": "medium", "fast": self._fast}

    def context_window(self, model: str) -> int:
        return 272_000


def test_auto_safety_review_receives_last_ten_chat_messages(tmp_path: Path, monkeypatch) -> None:
    runtime = Runtime(AppConfig(data_dir=tmp_path))
    chat = runtime.create_conversation(str(tmp_path), mode=Mode.AUTO)
    other_chat = runtime.create_conversation(str(tmp_path))
    runtime.store.append_items(other_chat.id, [{"role": "user", "content": "Different conversation"}])
    messages = [
        {"role": "user" if index % 2 == 0 else "assistant", "text": f"Message {index}"}
        for index in range(12)
    ]
    items = [{"role": message["role"], "content": message["text"]} for message in messages[:-1]]
    items.extend([
        {"type": "function_call_output", "output": "Tool-only text"},
        {"type": "reasoning", "summary": [{"text": "Private reasoning"}]},
        {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": messages[-1]["text"]},
            {"type": "output_image", "image_url": "Image payload"},
        ]},
    ])
    runtime.store.append_items(chat.id, items)

    def review(body, *, stream):
        assert stream is False
        prompt = body["input"][0]["content"][0]["text"]
        task, transcript = prompt.split("\n\nRecent chat messages (oldest first):\n")
        context, command = transcript.split("\n\nShell command the agent wants to run:\n")
        assert task == "Task the user asked for:\nRun the tests"
        assert json.loads(context) == messages[-10:]
        assert command == "pytest -q"
        return {"output": [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": '{"allow":true,"reason":"Runs the tests."}'},
        ]}]}

    monkeypatch.setattr(runtime.session, "send_responses", review)
    assert runtime._state_for(chat).permissions._run_judge("pytest -q", "Run the tests") == (
        True, "Runs the tests.",
    )


def test_auto_safety_denial_returns_the_reason_without_a_permission_request(
    tmp_path: Path, monkeypatch,
) -> None:
    events = []

    def publish(event, data):
        events.append((event, data))

    permissions = PermissionManager(
        Mode.AUTO, publish, judge=lambda *_: (False, "The command is outside the task."),
    )

    def unexpected_action(*args, **kwargs):
        raise AssertionError("A denied shell action cannot run or ask for permission")

    runner = _runner(tmp_path, FakeSession(), publish=publish)
    monkeypatch.setattr(permissions, "_ask_user", unexpected_action)
    monkeypatch.setattr("codelite.tools.shell.subprocess.run", unexpected_action)
    result = runner._execute_call(
        {"call_id": "shell-1", "name": "shell", "arguments": '{"command":"echo example"}'},
        ToolContext(workspace=tmp_path, permissions=permissions, session=None),
    )

    assert result["output"] == "The safety check blocked this shell command. The command is outside the task."
    assert [event for event, _ in events] == ["tool_started", "judge_started", "judge_finished", "tool_finished"]
    assert events[-1][1]["ok"] is False
    assert permissions.list_pending() == []


def _runner(
    tmp_path: Path,
    session: FakeSession,
    publish: Any = None,
    **conversation: Any,
) -> AgentRunner:
    store = Store(tmp_path / "db.sqlite")
    created = store.create_conversation(
        workspace=str(tmp_path),
        model=conversation.pop("model", "gpt-5.6-luna"),
        permission_mode="ask",
        **conversation,
    )
    return AgentRunner(
        session=session,
        store=store,
        conversation=created,
        permissions=PermissionManager(Mode.ASK, lambda *_: None),
        publish=publish or (lambda *_: None),
        config=AppConfig(data_dir=tmp_path),
    )


def test_no_reasoning_or_tier_is_sent_by_default(tmp_path: Path) -> None:
    session = FakeSession()
    runner = _runner(tmp_path, session)
    runner._request_turn([])
    body = session.bodies[0]
    # Absent, not empty: the transport then fills in the model's own default.
    assert "reasoning" not in body
    assert "service_tier" not in body


def test_user_message_is_published_with_its_client_id(tmp_path: Path) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    runner = _runner(tmp_path, FakeSession(), lambda event, data: events.append((event, data)))

    runner.run("hello", message_id="browser-message-1")

    assert events[0] == (
        "user_message",
        {"text": "hello", "attachments": [], "message_id": "browser-message-1"},
    )
    entry = runner._store.load_entries(runner._conversation.id)[0]
    assert entry["meta"]["message_id"] == "browser-message-1"


def test_model_output_rate_is_published_and_saved(tmp_path: Path, monkeypatch) -> None:
    events = []
    session = FakeSession()
    response = {
        "output": [{
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "Hello"}],
        }],
        "usage": {"input_tokens": 64, "output_tokens": 20},
    }
    monkeypatch.setattr(session, "send_responses", lambda *_args, **_kwargs: response)
    timestamps = iter((10.0, 12.5))
    monkeypatch.setattr(agent_loop, "perf_counter", lambda: next(timestamps))
    runner = _runner(tmp_path, session, lambda event, data: events.append((event, data)))
    runner.run("Hello")

    usage = next(data for event, data in events if event == "usage")
    assert usage["output_tokens_per_second"] == 8.0
    assistant = runner._store.load_entries(runner._conversation.id)[-1]
    assert assistant["meta"]["output_tokens_per_second"] == 8.0
    assert "output_tokens_per_second" not in runner._publish_usage({"total_tokens": 10}, model_seconds=1)


def test_chosen_effort_is_sent(tmp_path: Path) -> None:
    session = FakeSession()
    runner = _runner(tmp_path, session, reasoning_effort="high")
    runner._request_turn([])
    assert session.bodies[0]["reasoning"] == {"effort": "high"}


def test_fast_mode_requests_the_priority_tier(tmp_path: Path) -> None:
    session = FakeSession()
    runner = _runner(tmp_path, session, fast_mode=1)
    runner._request_turn([])
    assert session.bodies[0]["service_tier"] == "priority"


def test_an_unsupported_effort_is_dropped_not_sent(tmp_path: Path) -> None:
    # Luna has no "ultra". Sending it anyway is an HTTP 400 on the first turn,
    # so the level has to fall back to the model's own default instead.
    session = FakeSession(efforts=["low", "medium", "high", "xhigh", "max"])
    runner = _runner(tmp_path, session, reasoning_effort="ultra")
    runner._run_effort = runner._supported_effort(runner._run_effort)
    runner._request_turn([])
    assert "reasoning" not in session.bodies[0]


def test_a_supported_effort_survives_the_clamp(tmp_path: Path) -> None:
    session = FakeSession(efforts=["low", "medium", "high", "xhigh", "max", "ultra"])
    runner = _runner(tmp_path, session, reasoning_effort="ultra")
    runner._run_effort = runner._supported_effort(runner._run_effort)
    runner._request_turn([])
    assert session.bodies[0]["reasoning"] == {"effort": "ultra"}


def test_fast_is_omitted_for_a_model_without_the_tier() -> None:
    """gpt-5.4-mini has no fast tier, and the backend does not object.

    Sending `priority` to it returns 200 and the response still reads
    `service_tier: "default"` -- exactly what a supported model returns too.
    Nothing downstream can tell the difference, so the request has to be
    filtered here rather than relying on the server to refuse it.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        session = FakeSession(fast=False)
        runner = _runner(Path(tmp), session, fast_mode=1)
        runner._request_turn([])
        assert "service_tier" not in session.bodies[0]


def _call(name, call_id, **arguments):
    import json
    return {
        "type": "function_call", "name": name, "call_id": call_id,
        "arguments": json.dumps(arguments),
    }


def test_thinking_is_controlled_by_the_agent_tools(tmp_path: Path):
    replies = iter([
        [_call("write_file", "write", path="result.txt", content="first")],
        [_call("low_thinking", "low"), _call("get_thinking-mode", "get-low")],
        [_call("edit_file", "edit", path="result.txt", old_string="first", new_string="finished")],
        [_call("edit_file", "failed", path="result.txt", old_string="missing", new_string="changed")],
        [_call("normal_thinking", "normal"), _call("get_thinking-mode", "get-normal")],
        [],
    ])

    class ToolSession(FakeSession):
        def send_responses(self, body, *, stream=False):
            self.bodies.append(body)
            return {"output": next(replies), "status": "completed"}

    session = ToolSession()
    runner = _runner(tmp_path, session, reasoning_effort="high")
    runner._permissions.set_mode(Mode.PERMIT_WRITES)
    runner.run("Write the result")

    assert (tmp_path / "result.txt").read_text() == "finished"
    assert [body["reasoning"]["effort"] for body in session.bodies] == [
        "high", "high", "low", "low", "low", "high",
    ]
    outputs = {
        item["call_id"]: item["output"]
        for item in runner._store.load_items(runner._conversation.id)
        if item.get("type") == "function_call_output"
    }
    assert outputs["failed"].startswith("Error:")
    assert outputs["get-low"] == "Mode: low. Next request: low. Normal setting: high."
    assert outputs["get-normal"] == "Mode: normal. Next request: high. Normal setting: high."
    assert runner._store.get_conversation(runner._conversation.id).reasoning_effort == "high"


def test_thinking_tool_does_not_turn_reasoning_off_for_a_binary_model(tmp_path: Path):
    session = FakeSession(efforts=["none", "high"])
    runner = _runner(tmp_path, session, reasoning_effort="high")

    output = runner._control_thinking("low")
    assert "No lower supported thinking level" in output
    runner._request_turn([])
    assert session.bodies[0]["reasoning"] == {"effort": "high"}


def test_file_reads_overlap_but_never_cross_a_write(tmp_path: Path, monkeypatch):
    from threading import Barrier, Event

    from codelite.tools import registry
    from codelite.tools.base import Tool
    from codelite.tools.context import ToolContext

    runner = _runner(tmp_path, FakeSession())
    first_pair = Barrier(2, timeout=2)
    first_finished, second_finished, written = Event(), Event(), Event()

    def read(arguments, context):
        name = arguments["path"]
        if name == "after":
            assert written.is_set()
        else:
            first_pair.wait()
            if name == "first":
                assert second_finished.wait(2)
                first_finished.set()
            else:
                second_finished.set()
        return name

    def write(arguments, context):
        assert first_finished.is_set() and second_finished.is_set()
        written.set()
        return "written"

    tools = {
        "read_file": Tool(name="read_file", description="", parameters={}, run=read),
        "write_file": Tool(name="write_file", description="", parameters={}, run=write),
    }
    monkeypatch.setattr(registry, "get", tools.get)
    results = runner._execute_calls([
        _call("read_file", "one", path="first"),
        _call("read_file", "two", path="second"),
        _call("write_file", "three"),
        _call("read_file", "four", path="after"),
    ], ToolContext(workspace=tmp_path, permissions=runner._permissions, session=runner._session))

    assert [(item["call_id"], item["output"]) for item in results] == [
        ("one", "first"), ("two", "second"), ("three", "written"), ("four", "after"),
    ]
    assert written.is_set()
