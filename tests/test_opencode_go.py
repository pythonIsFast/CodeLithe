"""Exercise provider routing and account settings without real credentials."""

import json
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from codelite.agent.router import select_model
from codelite.app.server import create_app
from codelite.config import AppConfig
from codelite.provider.auth import AuthError


class OpenCodeGoTests(unittest.TestCase):
    def setUp(self):
        data_dir = Path(self.enterContext(TemporaryDirectory()))
        self.app = create_app(AppConfig(data_dir=data_dir))
        self.app.testing = True
        self.client = self.app.test_client()
        self.session = self.app.config["runtime"].session
        self.go = self.session.opencode_go

    def test_catalog_and_usage_use_separate_keys_and_hide_no_secrets(self):
        response = self.client.put("/api/settings/opencode-go", json={
            "api_key": "inference-test-key",
            "usage_key": "usage-test-key",
        })
        self.assertEqual(response.json, {"api_key_configured": True, "usage_key_configured": True})
        status = self.client.get("/api/auth").json
        self.assertTrue(status["opencode_go"])
        self.assertTrue(status["opencode_go_usage"])
        catalog = {"data": [{"id": "kimi-k3", "name": "Kimi K3", "limit": {"context": 123456}}]}
        usage = {"usage": {"rolling": {"percent": 27, "resetsAt": "2026-11-20T19:00:00Z"}}}
        with (
            patch.object(self.go, "_json", side_effect=[catalog, usage]) as upstream,
            patch.object(self.session, "list_models", return_value=["gpt-6-astra"]),
            patch.object(self.session._transport, "resolve_model_info", return_value=None),
        ):
            models = self.client.get("/api/models").json
            self.assertEqual(models["models"], ["auto", "gpt-6-astra", "opencode-go/kimi-k3"])
            self.assertEqual(self.session.context_window("opencode-go/kimi-k3"), 123456)
            self.assertEqual(self.client.get("/api/usage/opencode-go").json["usage"]["rolling"]["percent"], 27)
        self.assertEqual([call.args for call in upstream.call_args_list], [
            ("/inference/go/v1/models", "inference-test-key"),
            ("/zen/go/v1/usage", "usage-test-key"),
        ])
        with patch.object(self.session, "list_models", side_effect=AuthError("No ChatGPT login")):
            self.assertEqual(self.client.get("/api/models").json["models"], ["opencode-go/kimi-k3"])
        self.client.put("/api/settings/model-picker", json={"hidden_models": ["opencode-go/kimi-k3"]})
        with patch.object(self.session, "list_models", return_value=[]):
            data = self.client.get("/api/models").json
            self.assertEqual(data["hidden_models"], ["opencode-go/kimi-k3"])
            self.assertIn("opencode-go/kimi-k3", data["models"])

    def test_behaviour_model_choices_use_catalog_visibility(self):
        go_model = "opencode-go/kimi-k3"
        self.go.save({"api_key": "inference-test-key"})
        with (
            patch.object(self.go, "_json", return_value={"data": [{"id": "kimi-k3", "name": "Kimi K3"}]}),
            patch.object(self.session, "list_models", return_value=["gpt-6-astra", "gpt-5.6-luna"]),
            patch.object(self.session._transport, "resolve_model_info", return_value=None),
        ):
            self.client.put("/api/settings/model-picker", json={"hidden_models": ["gpt-5.6-luna"]})
            schema = self.client.get("/api/settings/behaviour").json["schema"]
            self.assertEqual(schema["models"], ["gpt-6-astra", go_model])
            self.assertEqual(schema["model_labels"][go_model], "Kimi K3 · OpenCode Go")
            values = {"agent_model": go_model, "auto_models": [go_model]}
            result = self.client.put("/api/settings/behaviour", json={"values": values}).json["values"]
            self.assertEqual(result["agent_model"], go_model)
            self.assertEqual(result["auto_models"], [go_model])
            self.client.put("/api/settings/model-picker", json={"hidden_models": [go_model]})
            data = self.client.get("/api/settings/behaviour").json
            self.assertNotIn(go_model, data["schema"]["models"])
            self.assertEqual(data["values"]["agent_model"], go_model)
            saved = self.client.put("/api/settings/behaviour", json={
                "values": {"use_pollinations_free": True},
            }).json["values"]
            self.assertEqual(saved["agent_model"], go_model)
            self.assertEqual(saved["auto_models"], [go_model])

    def test_auto_routes_to_go_and_publishes_the_model(self):
        model = "opencode-go/kimi-k3"
        response = {"output_text": json.dumps({"model": model, "effort": "low", "reason": "Good fit"})}
        with patch.object(self.go, "send", return_value=response) as request:
            decision, _ = select_model(
                self.session, "Rename this label", router_model=model,
                routable=[model], session_id="test-conversation",
            )
        self.assertEqual(request.call_args.kwargs["session_id"], "test-conversation")
        self.assertEqual(decision.as_event_payload()["model"], model)
        self.assertFalse(decision.fallback)

    def test_stream_uses_go_session_and_replays_tool_call_pair(self):
        self.go.save({"api_key": "inference-test-key"})
        body = {
            "model": "opencode-go/kimi-k3",
            "service_tier": "priority",
            "input": [
                {"type": "reasoning", "encrypted_content": "codex-only"},
                {"type": "function_call", "id": "fc_1", "call_id": "call_1",
                 "name": "read_file", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
            ],
        }
        event = b'data: {"type":"response.completed","response":{"output":[]}}\n\n'
        with patch.object(self.go, "_request", return_value=BytesIO(event)) as upstream:
            result = self.session.send_responses(body, stream=True, session_id="test-conversation")
            self.assertEqual(b"".join(result), event)
        path, key, raw = upstream.call_args.args
        request = json.loads(raw)
        self.assertEqual(path, "/inference/go/openai/v1/responses")
        self.assertEqual(key, "inference-test-key")
        self.assertEqual(upstream.call_args.kwargs["session_id"], "test-conversation")
        self.assertEqual(request["model"], "kimi-k3")
        self.assertNotIn("service_tier", request)
        self.assertEqual([item["call_id"] for item in request["input"]], ["call_1", "call_1"])
        self.assertNotIn("id", request["input"][0])
        with patch.object(self.session._transport, "send_responses_request", return_value={}) as codex:
            self.session.send_responses({"model": "gpt-6-astra"})
            codex.assert_called_once()

    def test_settings_template_includes_go_onboarding(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        for marker in (b'id="auth-opencode-go"', b'id="go-keys"', b'id="model-visibility"'):
            self.assertIn(marker, page.data)
