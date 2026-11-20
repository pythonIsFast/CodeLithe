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

"""OpenCode Go inference and quota access using separate credentials."""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
from pathlib import Path
from time import monotonic
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

BASE_URL = "https://opencode.ai"
RESPONSES_PATH = "/inference/go/openai/v1/responses"
MODEL_PREFIX = "opencode-go/"
GO_REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


def is_go_model(model: Any) -> bool:
    return isinstance(model, str) and model.startswith(MODEL_PREFIX)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Keep the bearer token on the original endpoint.
        return None


class OpenCodeGo:
    def __init__(self, credentials: Path) -> None:
        self._path = credentials
        self._lock = threading.Lock()
        self._cached_models: dict[str, dict[str, Any]] = {}
        self._models_at = float("-inf")

    def _keys(self) -> dict[str, str]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def status(self) -> dict[str, bool]:
        keys = self._keys()
        return {f"{name}_configured": bool(keys.get(name)) for name in ("api_key", "usage_key")}

    def save(self, changes: dict[str, Any]) -> None:
        with self._lock:
            keys = self._keys()
            for name in ("api_key", "usage_key"):
                if name not in changes:
                    continue
                value = changes[name]
                if not isinstance(value, str) or len(value) > 1024:
                    raise ValueError("Invalid OpenCode Go key.")
                value = value.strip()
                if value and not all(33 <= ord(char) <= 126 for char in value):
                    raise ValueError("OpenCode Go keys cannot contain spaces or control characters.")
                keys[name] = value
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if not any(keys.values()):
                self._path.unlink(missing_ok=True)
            else:
                fd, temp = tempfile.mkstemp(dir=self._path.parent)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as file:
                        json.dump(keys, file)
                    os.replace(temp, self._path)
                finally:
                    Path(temp).unlink(missing_ok=True)
            if "api_key" in changes:
                self._cached_models = {}
                self._models_at = float("-inf")

    @staticmethod
    def _request(path: str, key: str, body=None, *, session_id="", timeout=12):
        headers = {"Authorization": f"Bearer {key}", "User-Agent": "Code-Lite"}
        if session_id:
            headers["x-opencode-session"] = session_id
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(BASE_URL + path, data=body, headers=headers)
        try:
            return build_opener(_NoRedirect()).open(request, timeout=timeout)
        except HTTPError as error:
            error.close()
            hints = {
                401: "Check the API key.",
                402: "Check the subscription and remaining allowance.",
                403: "Check the key permissions and workspace access.",
                429: "The rate limit was reached. Try again later.",
            }
            raise RuntimeError(f"OpenCode Go returned HTTP {error.code}. {hints.get(error.code, '')}".strip()) from None
        except URLError:
            raise RuntimeError("Could not reach OpenCode Go.") from None

    def _json(self, path: str, key: str) -> dict[str, Any]:
        with self._request(path, key) as response:
            try:
                data = json.load(response)
            except ValueError:
                raise RuntimeError("OpenCode Go returned invalid JSON.") from None
        if not isinstance(data, dict):
            raise RuntimeError("OpenCode Go returned an unexpected response.")
        return data

    def models(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            key = self._keys().get("api_key")
            if not key:
                return {}
            if monotonic() - self._models_at >= 300:
                data = self._json("/inference/go/v1/models", key)
                rows = data.get("data")
                if not isinstance(rows, list):
                    raise RuntimeError("OpenCode Go returned an unexpected model list.")
                self._cached_models = {
                    MODEL_PREFIX + row["id"]: row
                    for row in rows
                    if isinstance(row, dict) and isinstance(row.get("id"), str)
                    and row["id"] and row.get("type") in (None, "language", "chat", "text")
                    and not (isinstance(row.get("capabilities"), dict)
                             and row["capabilities"].get("tool_call") is False)
                }
                self._models_at = monotonic()
            return self._cached_models

    def usage(self) -> dict[str, Any]:
        key = self._keys().get("usage_key")
        if not key:
            return {}
        data = self._json("/zen/go/v1/usage", key)
        usage = data.get("usage")
        if not isinstance(usage, dict):
            raise RuntimeError("No OpenCode Go allowance was returned for this key.")
        windows = {}
        for name in ("rolling", "weekly", "monthly"):
            entry = usage.get(name)
            if not isinstance(entry, dict):
                continue
            percent = entry.get("percent")
            if type(percent) in (int, float) and math.isfinite(percent):
                reset = entry.get("resetsAt")
                windows[name] = {
                    "percent": max(0, min(100, percent)),
                    "resets_at": reset if isinstance(reset, str) else None,
                }
        if not windows:
            raise RuntimeError("OpenCode Go returned no usage windows for this key.")
        return windows

    def reasoning_capabilities(self, model: str) -> dict[str, Any]:
        info = self.models().get(model, {})
        capabilities = info.get("capabilities")
        reasoning = capabilities.get("reasoning") if isinstance(capabilities, dict) else None
        if not isinstance(reasoning, dict) or reasoning.get("supported") is not True:
            return {"efforts": [], "default_effort": "", "reasoning_toggle_only": False}

        declared = reasoning.get("effort")
        efforts = []
        if isinstance(declared, list):
            efforts = list(dict.fromkeys(value for value in declared if value in GO_REASONING_EFFORTS))
        toggle = reasoning.get("toggle") is True
        toggle_only = toggle and not any(effort != "none" for effort in efforts)
        if toggle:
            if "none" not in efforts:
                efforts.insert(0, "none")
            if toggle_only:
                # The Responses bridge uses none for off and a non-none effort
                # to enable thinking on models with a binary control.
                efforts.append("high")
        default = reasoning.get("default")
        default_effort = ""
        if isinstance(default, dict) and default.get("type") == "effort":
            if default.get("value") in efforts:
                default_effort = default["value"]
        return {
            "efforts": efforts,
            "default_effort": default_effort,
            "reasoning_toggle_only": toggle_only,
        }

    def send(self, body: dict[str, Any], *, stream: bool, session_id: str):
        key = self._keys().get("api_key")
        if not key:
            raise RuntimeError("Add an API key in Settings → OpenCode Go.")
        request = {
            **body,
            "model": body["model"].removeprefix(MODEL_PREFIX),
            "store": False,
            "stream": stream,
        }
        request.pop("service_tier", None)
        reasoning = request.pop("reasoning", None)
        effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
        if effort in GO_REASONING_EFFORTS:
            try:
                allowed = self.reasoning_capabilities(body["model"])["efforts"]
            except (OSError, ValueError, RuntimeError):
                allowed = []
            if effort in allowed:
                request["reasoning"] = {"effort": effort}
        if isinstance(request.get("input"), list):
            request["input"] = [
                {k: v for k, v in item.items() if k not in ("id", "namespace")}
                for item in request["input"]
                if isinstance(item, dict)
                and item.get("type", "message") in ("message", "function_call", "function_call_output")
            ]
        if not request.get("tools"):
            request.pop("tools", None)
        encoded = json.dumps(request).encode("utf-8")
        if stream:
            return self._stream(key, encoded, session_id)
        with self._request(RESPONSES_PATH, key, encoded,
                           session_id=session_id, timeout=120) as response:
            return json.load(response)

    def _stream(self, key: str, body: bytes, session_id: str) -> Iterator[bytes]:
        with self._request(RESPONSES_PATH, key, body,
                           session_id=session_id, timeout=120) as response:
            yield from response
