import json
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError

import pytest

from codelite.agent.loop import AgentRunner
from codelite.app.server import create_app
from codelite.config import AppConfig
from codelite.permission.manager import PermissionManager
from codelite.permission.modes import Mode
from codelite.provider import custom
from codelite.provider.sse import collect_completed_response_from_sse


def provider_response(payload, content_type="application/json"):
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    stream = BytesIO(data)
    stream.headers = Message()
    stream.headers["Content-Type"] = content_type
    return stream


def test_multiple_providers_discover_route_and_keep_keys_private(tmp_path, monkeypatch):
    app = create_app(AppConfig(data_dir=tmp_path))
    client = app.test_client()
    session = app.config["runtime"].session
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, request.get_header("Authorization"), request.data))
            if request.full_url.endswith("/models"):
                return provider_response({"data": [{"id": "team/shared-model"}]})
            return provider_response({"choices": [{"message": {"content": "OK"}}]})

    monkeypatch.setattr(custom, "build_opener", lambda *_: Opener())
    monday = client.post("/api/settings/custom-providers", json={
        "name": "Monday", "base_url": "https://monday.example/v1/chat/completions", "api_key": "first-key",
    }).json
    friday = client.post("/api/settings/custom-providers", json={
        "name": "Friday", "base_url": "http://127.0.0.1:8000/v1", "api_key": "second-key",
        "model_ids": ["team/shared-model"],
    }).json
    models = [f"custom/{provider['id']}/team/shared-model" for provider in (monday, friday)]
    settings = client.get("/api/settings/custom-providers")
    assert b"first-key" not in settings.data and b"second-key" not in settings.data
    assert client.get("/api/auth").json["custom_providers"] is True
    assert set(client.get("/api/models").json["models"]) == set(models)
    assert set(calls) == {
        ("https://monday.example/v1/models", "Bearer first-key", None),
        ("http://127.0.0.1:8000/v1/models", "Bearer second-key", None),
    }
    session.use_pollinations_free = True
    for model in models:
        assert session.send_responses({"model": model, "input": "Hello"})["output_text"] == "OK"
        assert json.loads(calls[-1][2])["model"] == "team/shared-model"
    assert [(url, key) for url, key, _ in calls[-2:]] == [
        ("https://monday.example/v1/chat/completions", "Bearer first-key"),
        ("http://127.0.0.1:8000/v1/chat/completions", "Bearer second-key"),
    ]
    reloaded = custom.CustomProviders(tmp_path / "custom-providers.json")
    assert reloaded.list() == settings.json["providers"]
    updated = client.put(f"/api/settings/custom-providers/{monday['id']}", json={
        "name": "Monday", "base_url": "https://other.example/v1",
    })
    assert updated.json["api_key_configured"] is False
    assert client.delete(f"/api/settings/custom-providers/{monday['id']}").status_code == 200


@pytest.mark.parametrize("path, expected_thinking", [
    ("/chat/completions", {"reasoning_effort": "xhigh"}),
    ("/responses", {"reasoning": {"effort": "xhigh"}}),
])
def test_custom_model_effort_reaches_the_endpoint(tmp_path, monkeypatch, path, expected_thinking):
    config = AppConfig(data_dir=tmp_path)
    app = create_app(config)
    client = app.test_client()
    runtime = app.config["runtime"]
    provider = client.post("/api/settings/custom-providers", json={
        "name": "IDE3", "base_url": f"https://example.test/v1{path}",
        "model_ids": ["team/code", "team/plain"],
    }).json
    prefix = f"custom/{provider['id']}/"
    model = prefix + "team/code"
    bodies = []

    def reply(_provider, requested_path, body=None):
        if requested_path == "/models":
            return provider_response({"data": [
                {"id": "team/code", "supported_reasoning_levels": [{"effort": "low"}, {"effort": "xhigh"}]},
                {"id": "team/plain"},
            ]})
        assert requested_path == path
        bodies.append(body)
        if path == "/responses":
            return provider_response({"id": "response-1", "status": "completed", "output": []})
        return provider_response({"choices": [{"message": {"content": "Done"}}]})

    monkeypatch.setattr(custom.CustomProvider, "request", reply)
    capabilities = client.get("/api/models").json["capabilities"]
    assert capabilities[model]["efforts"] == ["low", "xhigh"]
    assert capabilities[prefix + "team/plain"]["efforts"] == []
    assert custom.CustomProviders(tmp_path / "custom-providers.json").reasoning_efforts(model) == ("low", "xhigh")
    for requested, expected in [("xhigh", expected_thinking), ("high", {}), ("", {})]:
        conversation = runtime.create_conversation(str(tmp_path), model, reasoning_effort=requested)
        runner = AgentRunner(
            runtime.session, runtime.store, conversation,
            PermissionManager(Mode.ASK, lambda *_: None), lambda *_: None, config,
        )
        runner.run("Hello")
        assert {key: value for key, value in bodies[-1].items() if key.startswith("reasoning")} == expected
    assert len(bodies) == 3

    response = client.put(f"/api/settings/custom-providers/{provider['id']}", json={
        "name": "IDE3", "base_url": f"https://example.test/v1{path}", "model_ids": ["team/code"],
        "model_efforts": {"team/code": ["bogus"]},
    })
    assert response.status_code == 400
    assert runtime.session.custom_providers.reasoning_efforts(model) == ("low", "xhigh")


@pytest.mark.parametrize("model, documented", [
    ("Qwen/Qwen3.8-27B", ("low", "medium", "xhigh")),
    ("qwen3.8-27b", ("low", "medium", "xhigh")),
    ("zai-org/GLM-5.3", ("low", "high", "max")),
    ("glm-5.3", ("low", "high", "max")),
])
def test_custom_model_reasoning_fallback_uses_documented_levels(tmp_path, monkeypatch, model, documented):
    providers = custom.CustomProviders(tmp_path / "providers.json")
    advertised = {
        "id": model, "capabilities": {"reasoning": {"supported": True, "effort": ["low", "medium"]}},
    }
    rows = [{"id": model}, {"id": "custom-unknown"}]

    def reply(_provider, path, body=None):
        assert path == "/models" and body is None
        return provider_response({"data": rows})

    monkeypatch.setattr(custom.CustomProvider, "request", reply)
    options = {"name": "IDE3", "base_url": "https://example.test/v1"}
    provider = providers.save(options)
    prefix = f"custom/{provider['id']}/"
    assert providers.reasoning_efforts(prefix + model) == documented
    assert providers.reasoning_efforts(prefix + "custom-unknown") == ()

    rows[0] = advertised
    providers.save(options, provider["id"])
    assert providers.reasoning_efforts(prefix + model) == ("low", "medium")

    advertised["capabilities"]["reasoning"]["supported"] = False
    providers.save(options, provider["id"])
    assert providers.reasoning_efforts(prefix + model) == ()


def test_streaming_tool_calls_are_assembled(tmp_path, monkeypatch):
    providers = custom.CustomProviders(tmp_path / "providers.json")
    provider = providers.save({"name": "Tools", "base_url": "https://tools.example/v1"})
    model = f"custom/{provider['id']}/code"
    chunks = [
        {"delta": {"content": "Reading"}},
        {"delta": {"tool_calls": [{"index": 0, "id": "read-1",
                                  "function": {"name": "read_file", "arguments": '{"path":'}}]}},
        {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"notes.md"}'}}]}},
        {"delta": {}, "finish_reason": "tool_calls"},
    ]
    response = b"".join(b"data: " + json.dumps({"choices": [chunk]}).encode() + b"\n\n" for chunk in chunks)
    response += b'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":9}}\n\ndata: [DONE]\n\n'
    monkeypatch.setattr(custom.CustomProvider, "request",
                        lambda *_: provider_response(response, "text/event-stream"))
    stream = providers.send({"model": model, "input": "Read it"}, stream=True)
    assert b'"delta":"Reading"' in next(stream)
    completed = collect_completed_response_from_sse(stream)
    call = completed["output"][1]
    assert (call["call_id"], call["name"], json.loads(call["arguments"])) == (
        "read-1", "read_file", {"path": "notes.md"},
    )
    assert completed["usage"]["output_tokens"] == 9

    def reply(_provider, _path, body):
        assistant, tool = body["messages"]
        assert assistant["role"] == "assistant"
        assert assistant["content"] == "Reading"
        assert assistant["tool_calls"][0]["id"] == tool["tool_call_id"] == "read-1"
        return provider_response({"choices": [{"message": {"content": "Done"}}]})

    monkeypatch.setattr(custom.CustomProvider, "request", reply)
    result = providers.send({"model": model, "input": [
        *completed["output"],
        {"type": "function_call_output", "call_id": "read-1", "output": "File contents"},
    ]}, stream=False)
    assert result["output_text"] == "Done"


@pytest.mark.parametrize("payload", [
    {"error": {"message": "Invalid tool history. Key: test-secret"}},
    {"detail": "Invalid tool history. Key: test-secret"},
])
def test_http_400_explains_provider_error_without_echoing_key(monkeypatch, payload):
    provider = custom.CustomProvider("IDE3", "https://example.test/v1", "test-secret", ())

    class RejectRequest:
        def open(self, request, timeout):
            body = provider_response(payload)
            raise HTTPError(request.full_url, 400, "Bad Request", body.headers, body)

    monkeypatch.setattr(custom, "build_opener", lambda *_: RejectRequest())
    with pytest.raises(RuntimeError, match=r"IDE3 returned HTTP 400\. Invalid tool history") as error:
        provider.request("/chat/completions", {"model": "code"})
    assert "test-secret" not in str(error.value)


def test_tool_only_assistant_message_sends_empty_content(tmp_path, monkeypatch):
    providers = custom.CustomProviders(tmp_path / "providers.json")
    provider = providers.save({"name": "IDE3", "base_url": "https://example.test/v1"})

    def reply(_provider, _path, body):
        assistant, tool = body["messages"]
        assert assistant["content"] == ""
        assert assistant["tool_calls"][0]["id"] == tool["tool_call_id"] == "call-1"
        return provider_response({"choices": [{"message": {"content": "Done"}}]})

    monkeypatch.setattr(custom.CustomProvider, "request", reply)
    result = providers.send({"model": f"custom/{provider['id']}/code", "input": [
        {"type": "function_call", "call_id": "call-1", "name": "read_file", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call-1", "output": "Synthetic tool output"},
    ]}, stream=False)
    assert result["output_text"] == "Done"


def test_full_responses_endpoint_uses_native_stream(tmp_path, monkeypatch):
    providers = custom.CustomProviders(tmp_path / "providers.json")
    provider = providers.save({
        "name": "Responses", "base_url": "https://provider.example/api/v1/responses/",
    })
    model = f"custom/{provider['id']}/code"
    terminal = {"type": "response.completed", "response": {"output": []}}

    def reply(upstream, path, body):
        assert upstream.base_url == "https://provider.example/api/v1"
        assert path == "/responses"
        assert body["model"] == "code"
        assert body["input"] == [{"role": "user", "content": "Hi"}]
        return provider_response(custom._event(terminal), "text/event-stream")

    monkeypatch.setattr(custom.CustomProvider, "request", reply)
    stream = providers.send({
        "model": model, "input": [{"id": "foreign-id", "role": "user", "content": "Hi"}],
    }, stream=True)
    assert collect_completed_response_from_sse(stream) == terminal["response"]
