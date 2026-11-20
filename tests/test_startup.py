"""The local shell and its first transcript must not depend on provider I/O."""

import json
import re
from pathlib import Path
from unittest.mock import patch

from codelite.app.server import STARTUP_TRANSCRIPT_BYTES, create_app
from codelite.config import AppConfig, context_window_for


def test_startup_embeds_safe_local_snapshot_without_fetching_models(tmp_path: Path):
    app = create_app(AppConfig(data_dir=tmp_path))
    runtime = app.config["runtime"]
    conversation = runtime.create_conversation(workspace=str(tmp_path), model="gpt-6-astra")
    text = "</script><script>alert(1)</script>"
    runtime.store.append_items(conversation.id, [{
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": text}],
    }])

    with (
        patch.object(app.config["login_manager"], "status", return_value={"authenticated": True}),
        patch.object(runtime.session, "list_models") as models,
        patch.object(runtime.session, "context_window") as context,
    ):
        client = app.test_client()
        response = client.get("/")
        assert "no-store" in response.headers["Cache-Control"]
        html = response.get_data(as_text=True)
        bootstrap = re.search(r'<script id="startup-data" type="application/json">(.*?)</script>', html)
        assert bootstrap
        latest = json.loads(bootstrap[1])["latest"]
        assert latest["id"] == conversation.id
        assert latest["entries"][0]["payload"]["content"][0]["text"] == text
        assert text not in html
        assert latest["context_window"] == context_window_for(conversation.model)
        assert client.get(f"/api/conversations/{conversation.id}?limit=60").json == latest
        models.assert_not_called()
        context.assert_not_called()

        runtime.store.append_items(conversation.id, [{
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": "x" * STARTUP_TRANSCRIPT_BYTES}],
        }])
        html = client.get("/").get_data(as_text=True)
        bootstrap = re.search(r'<script id="startup-data" type="application/json">(.*?)</script>', html)
        assert bootstrap
        data = json.loads(bootstrap[1])
        assert data["latest"] is None
        assert data["conversations"][0]["id"] == conversation.id


def test_startup_assets_are_loadable_and_revalidate(tmp_path: Path):
    client = create_app(AppConfig(data_dir=tmp_path)).test_client()
    for kind, first, last in (
        ("js", "01-core.js", "15-bootstrap.js"),
        ("css", "01-base.css", "08-responsive.css"),
    ):
        url = f"/static/{kind}/startup"
        response = client.get(url)
        assert response.status_code == 200
        assert response.data.startswith(client.get(f"/static/{kind}/{first}").data)
        assert response.data.endswith(client.get(f"/static/{kind}/{last}").data)
        assert client.get(url, headers={"If-None-Match": response.headers["ETag"]}).status_code == 304
