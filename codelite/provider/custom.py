"""Locally configured OpenAI-compatible model endpoints."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from http.client import HTTPException
from pathlib import Path
from time import monotonic
from typing import Any, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from .pollinations import _event, chat_to_responses, response_as_sse, responses_to_chat
from .sse import collect_completed_response_from_sse, iterate_server_sent_events

MODEL_PREFIX = "custom/"
MODEL_ID_LIMIT = 200 - len(MODEL_PREFIX) - 32 - 1
CATALOG_TTL = 300
CATALOG_MAX_BYTES = 4 * 1024 * 1024


def is_custom_model(model: Any) -> bool:
    return isinstance(model, str) and model.startswith(MODEL_PREFIX)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _endpoint(value: Any) -> tuple[str, str]:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ValueError("Enter an HTTP or HTTPS API base URL.")
    try:
        url = urlsplit(value)
        valid = (
            url.scheme in ("http", "https") and bool(url.hostname)
            and url.username is None and url.password is None
            and "?" not in value and "#" not in value
            and not any(char.isspace() or ord(char) < 32 for char in value)
            and "\\" not in value
        )
        url.port  # Ask urllib to validate an explicit port.
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Enter an HTTP or HTTPS API base URL without credentials, query or fragment.")
    base = value.rstrip("/")
    # Accept a pasted endpoint URL as well as its API base.
    api_path = "/chat/completions"
    for suffix in ("/chat/completions", "/models", "/responses"):
        if url.path.rstrip("/").endswith(suffix):
            base = base.removesuffix(suffix)
            if suffix == "/responses":
                api_path = suffix
            break
    if not urlsplit(base).path:
        base += "/v1"
    return base, api_path


def _is_model_id(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MODEL_ID_LIMIT and not any(
        char.isspace() or ord(char) < 32 for char in value
    )


@dataclass(frozen=True)
class CustomProvider:
    name: str
    base_url: str
    api_key: str
    model_ids: tuple[str, ...]
    api_path: str = "/chat/completions"

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base_url": self.base_url + ("/responses" if self.api_path == "/responses" else ""),
            "api_key_configured": bool(self.api_key),
            "model_ids": list(self.model_ids),
        }

    def request(self, path: str, body: dict[str, Any] | None = None):
        headers = {"User-Agent": "CodeLithe", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if body is not None:
            headers["Content-Type"] = "application/json"
            if body.get("stream"):
                headers["Accept"] = "text/event-stream"
        request = Request(
            self.base_url + path, headers=headers,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
        )
        try:
            # An endpoint must never forward its bearer key to a redirect target.
            return build_opener(_NoRedirect()).open(request, timeout=120 if body is not None else 5)
        except HTTPError as error:
            error.close()
            hint = " Check the API key." if error.code in (401, 403) else ""
            if 300 <= error.code < 400:
                hint = " Use the final API URL."
            raise RuntimeError(f"{self.name} returned HTTP {error.code}.{hint}") from None
        except (URLError, OSError, ValueError):
            raise RuntimeError(f"Could not reach {self.name}.") from None

    def discover(self) -> list[str]:
        with self.request("/models") as response:
            raw = response.read(CATALOG_MAX_BYTES + 1)
        if len(raw) > CATALOG_MAX_BYTES:
            raise RuntimeError(f"{self.name} returned a model list that is too large.")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise RuntimeError(f"{self.name} returned an invalid model list.") from None
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError(f"{self.name} did not return an OpenAI-compatible model list.")
        return list(dict.fromkeys(
            entry["id"] for entry in rows if isinstance(entry, dict)
            and _is_model_id(entry.get("id"))
            and entry.get("type") in (None, "model", "language", "chat", "text")
        ))


class CustomProviders:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._catalog_cache: dict[str, tuple[float, list[str]]] = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._providers = {
                key: CustomProvider(
                    row["name"], row["base_url"], row["api_key"], tuple(row["model_ids"]),
                    row.get("api_path", "/chat/completions"),
                )
                for key, row in data.items()
            }
        except FileNotFoundError:
            self._providers = {}
        except (ValueError, TypeError, KeyError, AttributeError):
            raise RuntimeError("The custom provider settings file is invalid.") from None

    def configured(self) -> bool:
        with self._lock:
            return bool(self._providers)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"id": key, **provider.public()} for key, provider in self._providers.items()]

    def _persist(self, providers: dict[str, CustomProvider]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump({key: asdict(provider) for key, provider in providers.items()}, file)
            os.replace(temporary, self._path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self._providers = providers

    def save(self, payload: dict[str, Any], provider_id: str | None = None) -> dict[str, Any]:
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(
            ord(char) < 32 for char in name
        ):
            raise ValueError("Enter a provider name.")
        base_url, api_path = _endpoint(payload.get("base_url"))
        model_ids = payload.get("model_ids", [])
        if not isinstance(model_ids, list) or not all(_is_model_id(model) for model in model_ids):
            raise ValueError("Enter one valid model ID per line.")
        with self._lock:
            existing = self._providers[provider_id] if provider_id is not None else None
            default_key = existing.api_key if existing and existing.base_url == base_url else ""
            key = payload.get("api_key", default_key)
            if not isinstance(key, str) or len(key) > 4096 or any(
                not 33 <= ord(char) <= 126 for char in key.strip()
            ):
                raise ValueError("Enter a valid API key without spaces or control characters.")
            provider = CustomProvider(
                name.strip(), base_url, key.strip(), tuple(dict.fromkeys(model_ids)), api_path,
            )
            provider_id = provider_id or uuid4().hex
            self._persist({**self._providers, provider_id: provider})
            self._catalog_cache.pop(provider_id, None)
            return {"id": provider_id, **provider.public()}

    def delete(self, provider_id: str) -> None:
        with self._lock:
            providers = self._providers.copy()
            del providers[provider_id]
            self._persist(providers)
            self._catalog_cache.pop(provider_id, None)

    def _catalog_for(self, entry: tuple[str, CustomProvider]) -> tuple[dict[str, str], str]:
        key, provider = entry
        with self._lock:
            cached = self._catalog_cache.get(key)
        if provider.model_ids:
            models = provider.model_ids
        elif cached and monotonic() - cached[0] < CATALOG_TTL:
            models = cached[1]
        else:
            try:
                models = provider.discover()
            except (RuntimeError, OSError, HTTPException) as error:
                return (
                    {f"{MODEL_PREFIX}{key}/{model}": f"{model} · {provider.name}"
                     for model in (cached[1] if cached else [])},
                    str(error),
                )
            with self._lock:
                if self._providers.get(key) == provider:
                    self._catalog_cache[key] = (monotonic(), models)
        return {f"{MODEL_PREFIX}{key}/{model}": f"{model} · {provider.name}" for model in models}, ""

    def catalog(self) -> tuple[dict[str, str], list[str]]:
        with self._lock:
            providers = list(self._providers.items())
        if not providers:
            return {}, []
        models, errors = {}, []
        with ThreadPoolExecutor(max_workers=min(len(providers), 6)) as pool:
            for available, error in pool.map(self._catalog_for, providers):
                models.update(available)
                if error:
                    errors.append(error)
        return models, errors

    def send(self, body: dict[str, Any], *, stream: bool):
        model = body["model"]
        provider_id, _, upstream_model = model.removeprefix(MODEL_PREFIX).partition("/")
        with self._lock:
            provider = self._providers.get(provider_id)
        if not provider or not upstream_model:
            raise RuntimeError("This custom provider is unavailable. Select another model or re-add the provider.")
        if provider.api_path == "/responses":
            return _send_responses(provider, body, upstream_model, stream)
        request = responses_to_chat(body, model=upstream_model, include_images=True)
        # Standard model lists don't advertise supported reasoning levels.
        request.pop("reasoning_effort", None)
        request["stream"] = stream
        if stream:
            return _stream(provider, request, model)
        with provider.request("/chat/completions", request) as response:
            return _completion(json.load(response), model)


def _send_responses(provider: CustomProvider, body: dict[str, Any], model: str, stream: bool):
    request = {**body, "model": model, "store": False, "stream": stream}
    request.pop("reasoning", None)
    request.pop("service_tier", None)
    if isinstance(request.get("input"), list):
        request["input"] = [
            {key: value for key, value in item.items() if key not in ("id", "namespace")}
            for item in request["input"]
            if isinstance(item, dict)
            and item.get("type", "message") in ("message", "function_call", "function_call_output")
        ]
    if stream:
        return _stream_responses(provider, request)
    with provider.request("/responses", request) as response:
        if response.headers.get_content_type() == "text/event-stream":
            return collect_completed_response_from_sse(response)
        return json.load(response)


def _stream_responses(provider: CustomProvider, body: dict[str, Any]) -> Iterator[bytes]:
    with provider.request("/responses", body) as response:
        if response.headers.get_content_type() == "application/json":
            payload = json.load(response)
            if not isinstance(payload, dict) or not isinstance(payload.get("output"), list):
                raise RuntimeError(f"{provider.name} returned no Responses API output.")
            yield from response_as_sse(payload)
        else:
            yield from response


def _completion(payload: Any, model: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload.get("choices") or payload.get("error"):
        raise RuntimeError("The custom provider returned no chat completion.")
    return chat_to_responses(payload, model=model)


def _stream(provider: CustomProvider, body: dict[str, Any], model: str) -> Iterator[bytes]:
    with provider.request("/chat/completions", body) as response:
        if response.headers.get_content_type() == "application/json":
            yield from response_as_sse(_completion(json.load(response), model))
            return

        parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        usage: dict[str, Any] = {}
        finish_reason = ""
        for event in iterate_server_sent_events(response):
            if event.data == "[DONE]":
                break
            if not event.data:
                continue
            chunk = json.loads(event.data)
            if chunk.get("error"):
                raise RuntimeError(f"{provider.name} reported an error while streaming.")
            usage = chunk.get("usage") or usage
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") or {}
            text = delta.get("content")
            if isinstance(text, str) and text:
                parts.append(text)
                yield _event({"type": "response.output_text.delta", "delta": text})
            for update in delta.get("tool_calls") or []:
                call = calls.setdefault(update["index"], {"id": "", "name": "", "arguments": ""})
                function = update.get("function") or {}
                call["id"] += update.get("id") or ""
                call["name"] += function.get("name") or ""
                call["arguments"] += function.get("arguments") or ""

        if not finish_reason or (calls and finish_reason not in ("tool_calls", "stop")):
            raise RuntimeError(f"{provider.name} ended its stream before the response was complete.")
        message = {"content": "".join(parts), "tool_calls": [
            {"id": call["id"], "function": {"name": call["name"], "arguments": call["arguments"]}}
            for _, call in sorted(calls.items())
        ]}
        completed = _completion({"choices": [{"message": message}], "usage": usage}, model)
        yield _event({"type": "response.completed", "response": completed})
