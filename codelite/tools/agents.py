"""Tools for delegation and messages inside the current agent run."""

import json

from .base import Tool, ToolError, object_schema


def _run(args, context):
    if context.agent_control is None:
        raise ToolError("Agent coordination is unavailable in this run.")
    return json.dumps(context.agent_control(args, context.model), ensure_ascii=False)


SUBAGENTS = Tool(
    "subagents",
    "Manage subagents. Use Coder for implementation and configured MCP tools, and Explorer or Reviewer "
    "for read-only work. Assign each writer distinct files. Use profiles to find profile IDs. Start with profile and task; "
    "For parallel work, issue all start calls in the same response. The main agent automatically "
    "pauses after delegation and receives questions and results as they arrive. Use list to inspect "
    "status when needed and send to reply by ID.",
    object_schema({
        "action": {"type": "string", "enum": ["profiles", "start", "list", "send", "stop"]},
        "id": {"type": "string"},
        "profile": {"type": "string"},
        "task": {"type": "string"},
        "message": {"type": "string"},
    }, required=["action"]),
    _run,
)

AGENT_MESSAGE = Tool(
    "agent_message",
    "Use send for questions or findings the main agent needs before you finish. Use wait to receive a reply "
    "if required. Incoming messages are also delivered between your model turns.",
    object_schema({
        "action": {"type": "string", "enum": ["send", "wait"]},
        "message": {"type": "string"},
    }, required=["action"]),
    _run,
)
