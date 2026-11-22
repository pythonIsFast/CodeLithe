"""Agent profiles stored with CodeLithe's application settings."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import Lock
from uuid import uuid4

from ..db.store import Store
from ..tools import registry

READ_TOOLS = frozenset({"read_file", "list_dir", "grep", "find_files"})
CODING_TOOLS = READ_TOOLS | {"write_file", "edit_file", "mcp"}
DEFAULT_AGENT = "general"


@dataclass(frozen=True)
class AgentProfile:
    id: str
    name: str
    instructions: str = ""
    model: str = ""
    effort: str = ""
    tools: tuple[str, ...] | None = None
    builtin: bool = False

    def public(self) -> dict:
        return asdict(self)


BUILTINS = (
    AgentProfile(DEFAULT_AGENT, "General", builtin=True),
    AgentProfile("coder", "Coder", "Implement the assigned changes in the project and report the edited files.", tools=tuple(sorted(CODING_TOOLS)), builtin=True),
    AgentProfile("explorer", "Explorer", "Find relevant files and report evidence with paths.", tools=tuple(sorted(READ_TOOLS)), builtin=True),
    AgentProfile("reviewer", "Reviewer", "Review changes for correctness and regressions. Report actionable findings with paths and lines.", tools=tuple(sorted(READ_TOOLS)), builtin=True),
)


class AgentProfiles:
    def __init__(self, store: Store) -> None:
        self._store = store
        self._lock = Lock()

    def _custom(self) -> dict:
        return self._store.get_state("agent_profiles") or {}

    def list(self) -> list[AgentProfile]:
        return [*BUILTINS, *(AgentProfile(**entry) for entry in self._custom().values())]

    def get(self, profile_id: str) -> AgentProfile | None:
        return next((profile for profile in self.list() if profile.id == profile_id), None)

    def save(self, payload: dict, profile_id: str | None = None) -> AgentProfile:
        name = payload.get("name")
        instructions = payload.get("instructions", "")
        model = payload.get("model", "")
        effort = payload.get("effort", "")
        tools = payload.get("tools")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60:
            raise ValueError("Enter an agent name of 1 to 60 characters.")
        if not isinstance(instructions, str) or len(instructions) > 8000:
            raise ValueError("Agent instructions must be at most 8,000 characters.")
        if not isinstance(model, str) or len(model) > 200 or effort not in (
            "", "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
        ):
            raise ValueError("Choose a valid model and reasoning effort.")
        if tools is not None and (
            not isinstance(tools, list)
            or any(not isinstance(name, str) or name not in registry.names()
                   or name in ("agent_message", "extensions") for name in tools)
        ):
            raise ValueError("Choose tools from the built-in tool list.")
        with self._lock:
            profiles = self._custom()
            if profile_id is not None and profile_id not in profiles:
                raise ValueError("Only custom agents can be edited.")
            profile = AgentProfile(
                profile_id or uuid4().hex, name.strip(), instructions.strip(), model, effort,
                None if tools is None else tuple(dict.fromkeys(tools)),
            )
            profiles[profile.id] = profile.public()
            self._store.set_state("agent_profiles", profiles)
            return profile

    def delete(self, profile_id: str) -> None:
        with self._lock:
            profiles = self._custom()
            if profile_id not in profiles:
                raise ValueError("Only custom agents can be removed.")
            if self._store.uses_agent(profile_id):
                raise ValueError("This profile is still used by an existing chat.")
            profiles.pop(profile_id)
            self._store.set_state("agent_profiles", profiles)
