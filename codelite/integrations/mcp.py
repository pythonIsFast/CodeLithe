# Copyright 2026 Code Lite contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Minimal lazy MCP stdio client using newline-delimited JSON-RPC.

The implementation intentionally avoids an MCP SDK. Servers start only when a
model asks to discover or call one, and one process is reused per workspace.
"""

from __future__ import annotations

import atexit
import difflib
import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable

from .. import settings
from ..project.context import MCP_CONFIG_PATH

MCP_PROTOCOL_VERSION = "2024-11-05"
RPC_TIMEOUT_SECONDS = 20.0


class McpError(RuntimeError):
    pass


@dataclass
class _Pending:
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Any = None


class McpClient:
    def __init__(
        self,
        name: str,
        command: list[str],
        workspace: Path,
        environment: dict[str, str],
    ) -> None:
        self.name = name
        self.command = command
        self.workspace = workspace.resolve()
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, _Pending] = {}
        self._next_id = 1
        try:
            self._process = subprocess.Popen(  # noqa: S603 - explicit configured argv
                command,
                cwd=workspace,
                env={**os.environ, **environment},
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as error:
            raise McpError(f"Could not start MCP server `{name}`: {error}") from error
        self._reader = threading.Thread(
            target=self._read_loop,
            name=f"codelite-mcp-{name}",
            daemon=True,
        )
        self._reader.start()
        self.request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": {"name": "CodeLithe", "version": "0.1"},
            },
        )
        self.notify("notifications/initialized", {})

    @property
    def alive(self) -> bool:
        return self._process.poll() is None

    def _write(self, message: dict[str, Any]) -> None:
        if not self.alive or self._process.stdin is None:
            raise McpError(f"MCP server `{self.name}` is not running.")
        encoded = json.dumps(message, separators=(",", ":")) + "\n"
        with self._write_lock:
            try:
                self._process.stdin.write(encoded)
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                raise McpError(f"MCP server `{self.name}` closed its input.") from error

    def request(
        self, method: str, params: dict[str, Any], timeout: float | None = None
    ) -> Any:
        if timeout is None:
            timeout = float(settings.active("mcp_timeout_seconds"))
        with self._pending_lock:
            request_id = self._next_id
            self._next_id += 1
            pending = _Pending()
            self._pending[request_id] = pending
        try:
            self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            if not pending.event.wait(timeout):
                raise McpError(f"MCP server `{self.name}` timed out during `{method}`.")
            if pending.error is not None:
                message = (
                    pending.error.get("message")
                    if isinstance(pending.error, dict)
                    else str(pending.error)
                )
                raise McpError(f"MCP `{method}` failed: {message}")
            return pending.result
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _read_loop(self) -> None:
        stdout = self._process.stdout
        if stdout is None:
            return
        for line in stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            request_id = message.get("id")
            if request_id is not None and ("result" in message or "error" in message):
                with self._pending_lock:
                    pending = self._pending.get(request_id)
                if pending is not None:
                    pending.result = message.get("result")
                    pending.error = message.get("error")
                    pending.event.set()
                continue
            if request_id is not None and isinstance(message.get("method"), str):
                try:
                    method = message["method"]
                    if method == "roots/list":
                        response: dict[str, Any] = {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "result": {
                                "roots": [
                                    {"uri": self.workspace.as_uri(), "name": self.workspace.name}
                                ]
                            },
                        }
                    else:
                        # Sampling and other client capabilities are never
                        # granted to an extension implicitly.
                        response = {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32601, "message": "Client method not supported"},
                        }
                    self._write(response)
                except McpError:
                    break
        with self._pending_lock:
            pending_calls = list(self._pending.values())
        for pending in pending_calls:
            pending.error = {"message": "MCP server stopped."}
            pending.event.set()

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self.request("tools/list", params)
            page = result.get("tools") if isinstance(result, dict) else None
            tools.extend(tool for tool in page or [] if isinstance(tool, dict))
            next_cursor = result.get("nextCursor") if isinstance(result, dict) else None
            if not isinstance(next_cursor, str) or not next_cursor:
                return tools
            cursor = next_cursor

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        if self.alive:
            self._process.terminate()
            try:
                self._process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._process.kill()


_config_lock = threading.Lock()


def _config_path(workspace: Path) -> Path:
    root = Path(workspace).resolve()
    path = (root / MCP_CONFIG_PATH).resolve()
    if root not in path.parents:
        raise McpError(f"{MCP_CONFIG_PATH} resolves outside the workspace.")
    return path


def _read_config(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as error:
        raise McpError(f"Could not read {MCP_CONFIG_PATH}: {error}") from error


def _parse_config(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except ValueError as error:
        raise McpError(f"Could not read {MCP_CONFIG_PATH}: {error}") from error
    servers = payload.get("mcpServers") if isinstance(payload, dict) else None
    if not isinstance(servers, dict):
        raise McpError(f"{MCP_CONFIG_PATH} must contain an `mcpServers` object.")
    return payload


def _write_config(path: Path, content: str) -> None:
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        temporary.replace(path)
        temporary = None
    except OSError as error:
        raise McpError(f"Could not save {MCP_CONFIG_PATH}: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_project_config(workspace: Path, content: str) -> None:
    with _config_lock:
        _write_config(_config_path(workspace), content)


def add_server(
    workspace: Path, name: str, command: str, args: list[str], approve: Callable[[str, str], None],
) -> None:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
        raise McpError("Choose a server name of 1–64 letters, digits, dots, underscores, or hyphens.")
    if not isinstance(args, list) or not all(isinstance(value, str) and "\0" not in value for value in args):
        raise McpError("MCP server `args` must be an array of strings without null bytes.")
    config = {"command": command, "args": args}
    server_command(config)
    if "\0" in command:
        raise McpError("MCP server `command` must not contain null bytes.")

    path = _config_path(workspace)
    before = _read_config(path)
    payload = _parse_config(before) if before is not None else {"mcpServers": {}}
    if name in payload["mcpServers"]:
        raise McpError(f"MCP server `{name}` already exists.")
    payload["mcpServers"][name] = config
    after = json.dumps(payload, indent=2) + "\n"

    # Show only the new entry in the dialog, so existing env values stay out of the transcript.
    diff = "\n".join(difflib.unified_diff(
        [], json.dumps({name: config}, indent=2).splitlines(),
        fromfile=str(MCP_CONFIG_PATH), tofile=str(MCP_CONFIG_PATH), lineterm="",
    ))
    approve(str(path), diff)
    with _config_lock:
        if _config_path(workspace) != path or _read_config(path) != before:
            raise McpError(f"{MCP_CONFIG_PATH} changed while adding the server. Retry the addition.")
        _write_config(path, after)


def load_server_configs(workspace: Path) -> dict[str, dict[str, Any]]:
    content = _read_config(_config_path(workspace))
    servers = _parse_config(content)["mcpServers"] if content is not None else {}
    return {
        str(name): value
        for name, value in servers.items()
        if isinstance(value, dict) and value.get("disabled") is not True
    }


def server_command(config: dict[str, Any]) -> list[str]:
    command = config.get("command")
    args = config.get("args") or []
    if not isinstance(command, str) or not command.strip():
        raise McpError("MCP server config requires a non-empty `command` string.")
    if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
        raise McpError("MCP server `args` must be a list of strings.")
    return [command, *args]


_clients: dict[tuple[str, str], tuple[str, McpClient]] = {}
_clients_lock = threading.Lock()


def _live_client(key: tuple[str, str], fingerprint: str) -> McpClient | None:
    entry = _clients.get(key)
    return entry[1] if entry and entry[0] == fingerprint and entry[1].alive else None


def get_client(workspace: Path, name: str, authorize: Callable[[list[str]], None]) -> McpClient:
    workspace = Path(workspace).resolve()
    config = load_server_configs(workspace).get(name)
    if config is None:
        raise McpError(f"No MCP server named `{name}` is configured.")
    fingerprint = json.dumps(config, sort_keys=True)
    key = (str(workspace), name)
    with _clients_lock:
        if client := _live_client(key, fingerprint):
            return client
    command = server_command(config)
    authorize(command)
    with _clients_lock:
        if client := _live_client(key, fingerprint):
            return client
        if (existing := _clients.get(key)):
            existing[1].close()
        environment = config.get("env") or {}
        if not isinstance(environment, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in environment.items()
        ):
            raise McpError("MCP server `env` must map strings to strings.")
        client = McpClient(name, command, workspace, environment)
        _clients[key] = (fingerprint, client)
        return client


def _close_all() -> None:
    with _clients_lock:
        clients = [entry[1] for entry in _clients.values()]
        _clients.clear()
    for client in clients:
        client.close()


atexit.register(_close_all)
