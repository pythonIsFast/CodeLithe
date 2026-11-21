import json
from email.message import Message
from io import BytesIO

from codelite.app.server import create_app
from codelite.config import AppConfig
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
    assert calls == [("https://monday.example/v1/models", "Bearer first-key", None)]
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
