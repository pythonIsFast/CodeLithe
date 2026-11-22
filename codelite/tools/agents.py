"""Tools for delegation and messages inside the current agent run."""

import json

from .base import Tool, ToolError, object_schema


def _run(args, context):
    if context.agent_control is None:
        raise ToolError("Agent coordination is unavailable in this run.")
    return json.dumps(context.agent_control(args, context.model), ensure_ascii=False)


SUBAGENTS = Tool(
    "subagents",
    "Manage read-only subagents. Use profiles to find profile IDs. Start with profile and task; "
    "list shows name, model, state and tool-call count. Send a message or wait for results by id. "
    "Handle their questions and wait for their results before completing your own reply.",
    object_schema({
        "action": {"type": "string", "enum": ["profiles", "start", "list", "send", "wait", "stop"]},
        "id": {"type": "string"},
        "profile": {"type": "string"},
        "task": {"type": "string"},
        "message": {"type": "string"},
    }, required=["action"]),
    _run,
)

AGENT_MESSAGE = Tool(
    "agent_message",
    "Send a finding or a question to the main agent with send. Use wait to receive a reply "
    "if required. Incoming messages are also delivered between your model turns.",
    object_schema({
        "action": {"type": "string", "enum": ["send", "wait"]},
        "message": {"type": "string"},
    }, required=["action"]),
    _run,
)
