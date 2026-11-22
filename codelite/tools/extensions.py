# Copyright 2026 Code Lite contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""One token-efficient gateway for project skills and MCP tools."""

from __future__ import annotations

import json
import shlex
from typing import Any

from ..integrations.mcp import (
    McpError,
    add_server,
    get_client,
    load_server_configs,
)
from ..project.context import discover_skills, read_skill
from .base import Tool, ToolError, object_schema
from .context import ToolContext

_MCP_ACTIONS = ["list", "list_mcp_tools", "call_mcp_tool"]
_ACTIONS = [*_MCP_ACTIONS, "read_skill", "add_mcp_server"]


def _compact_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool.get("name"),
            "description": tool.get("description", ""),
            "inputSchema": tool.get("inputSchema", {}),
        }
        for tool in tools
    ]


def _run_extensions(args: dict[str, Any], ctx: ToolContext) -> str:
    action = args.get("action")
    try:
        if action == "list":
            skills = [skill.as_dict() for skill in discover_skills(ctx.workspace, ctx.data_dir)]
            servers = sorted(load_server_configs(ctx.workspace))
            return json.dumps({"skills": skills, "mcp_servers": servers}, indent=2)
        if action == "read_skill":
            name = args.get("name")
            if not isinstance(name, str) or not name:
                raise ToolError("`name` is required for read_skill.")
            try:
                return read_skill(ctx.workspace, ctx.data_dir, name)
            except KeyError as error:
                raise ToolError(f"No skill named `{name}` was found.") from error
        if action in ("add_mcp_server", "list_mcp_tools", "call_mcp_tool"):
            server = args.get("server")
            if not isinstance(server, str) or not server:
                raise ToolError("`server` is required for MCP actions.")
            if action == "add_mcp_server":
                add_server(
                    ctx.workspace, server, args.get("command"), args.get("args", []),
                    ctx.permissions.require_write,
                )
                return json.dumps({"server": server, "saved": True, "next_action": "list_mcp_tools"})
            client = get_client(ctx.workspace, server, lambda argv: ctx.permissions.require_shell(
                shlex.join(argv), ctx.task_prompt, str(ctx.workspace),
            ))
            if action == "list_mcp_tools":
                return json.dumps(_compact_tools(client.list_tools()), indent=2)
            name = args.get("name")
            arguments = args.get("arguments") or {}
            if not isinstance(name, str) or not name:
                raise ToolError("`name` is required for call_mcp_tool.")
            if not isinstance(arguments, dict):
                raise ToolError("`arguments` must be an object.")
            return json.dumps(client.call_tool(name, arguments), indent=2)
    except McpError as error:
        raise ToolError(str(error)) from error
    raise ToolError(f"`action` must be one of: {', '.join(_ACTIONS)}.")


EXTENSIONS = Tool(
    name="extensions",
    description=(
        "Lazily discover project/user skills and configured MCP servers. Read only a "
        "relevant skill or list/call tools on one MCP server. When the user asks to connect "
        "an MCP server, use add_mcp_server with its name, stdio executable and optional "
        "arguments (no secrets). This saves it for the current project."
    ),
    parameters=object_schema(
        {
            "action": {
                "type": "string",
                "enum": _ACTIONS,
            },
            "server": {"type": "string"},
            "command": {"type": "string", "description": "Executable for add_mcp_server."},
            "args": {"type": "array", "items": {"type": "string"}, "description": "Executable arguments for add_mcp_server."},
            "name": {"type": "string"},
            "arguments": {"type": "object", "additionalProperties": True},
        },
        required=["action"],
    ),
    run=_run_extensions,
)


def _run_mcp(args: dict[str, Any], ctx: ToolContext) -> str:
    action = args.get("action")
    if action not in _MCP_ACTIONS:
        raise ToolError("Use list, list_mcp_tools, or call_mcp_tool with configured MCP servers.")
    if action == "list":
        try:
            return json.dumps({"mcp_servers": sorted(load_server_configs(ctx.workspace))})
        except McpError as error:
            raise ToolError(str(error)) from error
    return _run_extensions(args, ctx)


MCP = Tool(
    name="mcp",
    description="List configured project MCP servers, discover their tools, or call a tool on one server.",
    parameters=object_schema(
        {
            "action": {"type": "string", "enum": _MCP_ACTIONS},
            "server": {"type": "string"},
            "name": {"type": "string"},
            "arguments": {"type": "object", "additionalProperties": True},
        },
        required=["action"],
    ),
    run=_run_mcp,
)
