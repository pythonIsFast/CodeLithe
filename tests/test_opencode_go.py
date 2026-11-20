"""Exercise provider routing and account settings without real credentials."""

import json
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

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
        self.assertTrue(self.client.get("/api/auth").json["opencode_go"])
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
