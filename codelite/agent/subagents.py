"""Live, private delegation and mailboxes for one main agent run."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from ..db.store import Store
from ..permission.manager import PermissionManager
from ..permission.modes import Mode
from ..tools.base import ToolError
from .loop import AgentRunner
from .profiles import AgentProfile, READ_TOOLS

ACTIVE = frozenset({"running", "waiting"})


@dataclass
class Subagent:
    id: str
    profile: AgentProfile
    model: str
    task_label: str
    status: str = "running"
    tool_calls: int = 0
    inbox: list[dict] = field(default_factory=list)
    result: str = ""
    runner: AgentRunner | None = None
    thread: threading.Thread | None = None

    def summary(self) -> dict:
        return {
            "id": self.id, "name": self.profile.name, "model": self.model,
            "status": self.status, "running": self.status == "running", "tool_calls": self.tool_calls,
        }


def _text(args: dict, key: str, limit: int) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ToolError(f"Provide {key} of 1 to {limit:,} characters.")
    return value.strip()


class Subagents:
    def __init__(self, runtime, conversation, profile: AgentProfile, publish) -> None:
        self._runtime = runtime
        self._conversation = conversation
        self._read_tools = READ_TOOLS if profile.tools is None else READ_TOOLS.intersection(profile.tools)
        self._publish = publish
        self._condition = threading.Condition()
        self._jobs: dict[str, Subagent] = {}
        self._inbox: list[dict] = []
        self._closed = False

    def snapshot(self) -> list[dict]:
        with self._condition:
            return [{**job.summary(), "task": job.task_label} for job in self._jobs.values()]

    def _notify(self) -> None:
        self._condition.notify_all()
        self._publish("subagents", {"agents": self.snapshot()})

    def inbox(self) -> list[dict]:
        with self._condition:
            messages, self._inbox = self._inbox, []
            return messages

    def wait_for_updates(self) -> None:
        with self._condition:
            self._condition.wait_for(lambda: (
                self._closed or bool(self._inbox)
                or not any(job.status in ACTIVE for job in self._jobs.values())
            ))

    def _from_child(self, job: Subagent, message: str) -> None:
        event = {**job.summary(), "message": message}
        self._inbox.append(event)
        self._publish("agent_message", event)

    def handle(self, args: dict, main_model: str):
        action = args.get("action")
        if action == "profiles":
            return [
                {"id": profile.id, "name": profile.name, "model": profile.model}
                for profile in self._runtime.agent_profiles.list()
            ]
        if action == "list":
            return self.snapshot()
        with self._condition:
            if self._closed:
                raise ToolError("This delegation session is closed.")
            if action == "start":
                profile_id = _text(args, "profile", 60)
                available = {profile.id: profile for profile in self._runtime.agent_profiles.list()}
                if profile_id not in available:
                    raise ToolError("Unknown agent profile. Use profiles to find its ID.")
                return self._start(available[profile_id], _text(args, "task", 10000), main_model)
            job = self._jobs.get(_text(args, "id", 60))
            if job is None:
                raise ToolError("Unknown subagent. Use list to find its ID.")
            if action == "send":
                if job.status not in ACTIVE:
                    raise ToolError("This subagent has already finished.")
                message = _text(args, "message", 4000)
                if len(job.inbox) >= 16:
                    raise ToolError("The subagent has too many unread messages.")
                job.inbox.append({"from": "main", "message": message})
                self._publish("agent_message", {
                    **job.summary(), "name": f"Main → {job.profile.name}", "message": message,
                })
                self._condition.notify_all()
                return {"delivered": True, **job.summary()}
            if action == "stop":
                self.stop(job.id)
                return job.summary()
            if action == "wait":
                self._condition.wait_for(
                    lambda: job.status != "running" or bool(self._inbox) or self._closed, timeout=12,
                )
                return {**job.summary(), "result": job.result}
            raise ToolError("Unknown subagent action.")

    def _start(self, profile, task, main_model):
        if len(self._jobs) >= 16:
            raise ToolError("This run has reached its 16-subagent limit.")
        if sum(job.thread is not None and job.thread.is_alive() for job in self._jobs.values()) >= 3:
            raise ToolError("Three subagents are already active. Wait for one to finish.")
        label = " ".join(task.split())
        if len(label) > 180:
            label = label[:179].rstrip() + "…"
        job = Subagent(uuid4().hex, profile, profile.model or main_model, label)
        self._jobs[job.id] = job
        job.thread = threading.Thread(target=self._run, args=(job, task), daemon=True)
        job.thread.start()
        self._notify()
        return job.summary()

    def _drain_child(self, job: Subagent) -> list[dict]:
        with self._condition:
            messages, job.inbox = job.inbox, []
            return messages

    def _child_tool(self, job: Subagent, args: dict, _model: str):
        with self._condition:
            if job.status not in ACTIVE:
                raise ToolError("This subagent was stopped.")
            if args.get("action") == "send":
                self._from_child(job, _text(args, "message", 4000))
                self._condition.notify_all()
                return {"delivered": True}
            if args.get("action") != "wait":
                raise ToolError("Choose send or wait.")
            job.status = "waiting"
            self._notify()
            self._condition.wait_for(lambda: bool(job.inbox) or job.status not in ACTIVE, timeout=12)
            if job.status == "waiting":
                job.status = "running"
                self._notify()
            return {"messages": self._drain_child(job)}

    def _run(self, job: Subagent, task: str) -> None:
        failure = ""

        def publish(event, data):
            nonlocal failure
            with self._condition:
                if job.status not in ACTIVE:
                    return
                if event == "tool_started":
                    job.tool_calls += 1
                    self._notify()
                elif event == "model_selected" and data.get("model"):
                    job.model = data["model"]
                    self._notify()
                elif event == "error":
                    failure = str(data.get("message", "Provider error."))[:8000]

        job_model = job.model
        result = ""
        try:
            with TemporaryDirectory(prefix="codelite-subagent-") as folder:
                store = Store(Path(folder) / "chat.sqlite")
                child = store.create_conversation(
                    workspace=self._conversation.workspace, model=job.model, permission_mode=Mode.ASK.value,
                    reasoning_effort=job.profile.effort,
                )
                allowed = self._read_tools if job.profile.tools is None else self._read_tools.intersection(job.profile.tools)
                runner = AgentRunner(
                    self._runtime.session, store, child, PermissionManager(Mode.ASK, lambda *_: None),
                    publish, self._runtime.config,
                    agent_instructions=(
                        "You are a read-only subagent. Report evidence to the main agent. "
                        "Use agent_message to send findings or ask the main agent for clarification.\n"
                        + job.profile.instructions
                    ),
                    allowed_tools=allowed | {"agent_message"}, coordination_tool="agent_message",
                    agent_control=lambda args, model: self._child_tool(job, args, model),
                    agent_inbox=lambda: self._drain_child(job), workspace_only=True,
                )
                with self._condition:
                    if job.status not in ACTIVE:
                        return
                    job.runner = runner
                runner.run(task)
                # Include the actual resolved model even when a provider override applied.
                job_model = runner._run_model
                result = AgentRunner._response_text({"output": [
                    item for item in store.load_items(child.id) if item.get("role") == "assistant"
                ]})[-8000:]
        except Exception:
            failure = failure or "The subagent could not complete its run."
        finally:
            with self._condition:
                job.runner = None
                if job.status in ACTIVE:
                    job.model = job_model
                    job.status = "failed" if failure else "completed"
                    job.result = failure or result
                    self._from_child(job, job.result)
                self._notify()

    def stop(self, job_id: str) -> bool:
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None or job.status not in ACTIVE:
                return False
            job.status = "cancelled"
            if job.runner:
                job.runner.cancel()
            self._notify()
            return True

    def close(self) -> None:
        with self._condition:
            self._closed = True
            for job in self._jobs.values():
                self.stop(job.id)
            self._condition.notify_all()
