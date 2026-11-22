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

"""The local HTTP layer the UI talks to.

Handlers stay deliberately thin -- they decode a request, call
:class:`~codelite.app.runtime.Runtime`, and encode the result. All behaviour
lives in the runtime/agent/permission modules so the UI is never the only way
to drive the agent.

Binds to localhost only and has no authentication: it is the private back end
of a desktop window, not a service.
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import uuid
import zipfile
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, make_response, redirect, render_template, request, send_file
from werkzeug.utils import secure_filename

from ..config import (
    REASONING_EFFORTS,
    AppConfig,
    chat_models,
    context_window_for,
)
from ..db.store import Conversation
from ..importer import import_codex_sessions, preview_codex_sessions
from ..integrations.mcp import McpError, save_project_config as save_mcp_config
from ..settings import schema as behaviour_schema
from ..updater import UpdateError, check_update, install_update
from ..permission.modes import Mode
from ..provider.auth import AuthError
from ..provider.custom import is_custom_model
from ..provider.login import ChatGPTLoginManager
from ..provider.opencode_go import is_go_model
from ..provider.pollinations import POLLINATIONS_MODEL
from ..provider.transport import UpstreamError
from ..remote import REMOTE_COOKIE, RemoteError, RemoteManager
from ..project.context import (
    GLOBAL_MEMORY_PATH,
    LSP_CONFIG_PATH,
    MAX_MEMORY_CHARS,
    MCP_CONFIG_PATH,
    MEMORY_PATH,
    discover_skills,
)
from ..tools import registry
from .runtime import Runtime, RunInProgress

logger = logging.getLogger(__name__)

SSE_HEADERS = {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
STARTUP_TRANSCRIPT_LIMIT = 60
STARTUP_TRANSCRIPT_BYTES = 128 * 1024


_asset_root_cache: Path | None = None


def _unpack_zipapp_assets() -> Path:
    """Extract the interface files from a zipapp into a temporary directory.

    Inside a ``.pyz`` nothing is a real file, and neither Jinja's loader nor
    ``send_from_directory`` can read from a zip -- so the two asset folders are
    unpacked once per process. The directory is deliberately left behind for
    the OS to reap: deleting it would have to outlive every request.
    """
    module = Path(__file__).resolve()
    # In a zipapp `__file__` looks like /path/app.pyz/codelite/app/server.py,
    # so the first parent that is an actual file is the archive itself.
    archive = next((parent for parent in module.parents if parent.is_file()), None)
    if archive is None or not zipfile.is_zipfile(archive):
        raise RuntimeError(
            "CodeLithe could not locate its interface files. This build looks "
            "incomplete -- please reinstall it."
        )
    target = Path(tempfile.mkdtemp(prefix="codelite-assets-"))
    prefixes = ("codelite/app/templates/", "codelite/app/static/")
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(target, [n for n in bundle.namelist() if n.startswith(prefixes)])
    return target / "codelite" / "app"


def _asset_root() -> Path:
    """Where ``templates/`` and ``static/`` actually live at runtime.

    Three shapes have to work: a plain checkout, a PyInstaller build (assets
    sit under the bootloader's extraction dir) and a zipapp (see above).
    Flask's own guess is right only for the first, so it is stated explicitly.
    """
    global _asset_root_cache
    if _asset_root_cache is not None:
        return _asset_root_cache
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        root = Path(bundle) / "codelite" / "app"
    else:
        here = Path(__file__).resolve().parent
        root = here if (here / "templates").is_dir() else _unpack_zipapp_assets()
    _asset_root_cache = root
    return root


def create_app(config: AppConfig | None = None, runtime: Runtime | None = None) -> Flask:
    assets = _asset_root()
    app = Flask(
        __name__,
        template_folder=str(assets / "templates"),
        static_folder=str(assets / "static"),
    )
    # Enforced by Flask/Werkzeug before form parsing or JSON decoding, so a
    # remote client cannot consume memory with an oversized request body.
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.config["runtime"] = runtime or Runtime(config)
    app.config["login_manager"] = ChatGPTLoginManager(
        app.config["runtime"].session.config
    )
    app.config["remote_manager"] = RemoteManager(
        app.config["runtime"].config.data_dir,
        app.config["runtime"].config.base_url,
    )

    def rt() -> Runtime:
        return app.config["runtime"]

    def login_manager() -> ChatGPTLoginManager:
        return app.config["login_manager"]

    def remote_manager() -> RemoteManager:
        return app.config["remote_manager"]

    def _conversation_or_404(conversation_id: str):
        conversation = rt().store.get_conversation(conversation_id)
        if conversation is None:
            return None, (jsonify({"error": "Conversation not found."}), 404)
        return conversation, None

    def _body() -> dict[str, Any]:
        payload = request.get_json(silent=True)
        return payload if isinstance(payload, dict) else {}

    def _upload_dir(conversation_id: str) -> Path:
        return (rt().config.data_dir / "uploads" / conversation_id).resolve()

    def _attachment_path(conversation_id: str, path: str) -> Path | None:
        prefix = f"uploads/{conversation_id}/"
        if not path.startswith(prefix):
            return None
        root = _upload_dir(conversation_id)
        candidate = (root / path.removeprefix(prefix)).resolve()
        return candidate if root in candidate.parents else None

    @app.before_request
    def require_remote_login():
        manager = remote_manager()
        if not manager.is_remote_host(request.host):
            return None
        if request.path == "/remote/login" or request.path.startswith("/static/"):
            return None
        if manager.authenticated(request.cookies.get(REMOTE_COOKIE)):
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "Remote login required."}), 401
        return redirect("/remote/login")

    @app.after_request
    def remote_security_headers(response):
        if remote_manager().is_remote_host(request.host):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # -- pages ---------------------------------------------------------------

    @app.route("/remote/login", methods=["GET", "POST"])
    def remote_login():
        error = ""
        if request.method == "POST":
            try:
                token = remote_manager().login(str(request.form.get("password") or ""))
            except RemoteError as exc:
                token, error = None, str(exc)
            if token:
                response = make_response(redirect("/"))
                response.set_cookie(
                    REMOTE_COOKIE,
                    token,
                    secure=True,
                    httponly=True,
                    samesite="Strict",
                    max_age=12 * 60 * 60,
                )
                return response
            error = error or "Incorrect password."
        return render_template("remote_login.html", error=error)

    @app.get("/")
    def index():
        auth = auth_snapshot()
        conversations = []
        latest = None
        if auth["authenticated"] or auth["free_mode"] or auth["opencode_go"] or auth["custom_providers"]:
            conversations = rt().store.list_conversations()
            if conversations:
                candidate = conversation_snapshot(conversations[0], STARTUP_TRANSCRIPT_LIMIT)
                # A tool can return large outputs. Let the browser load those
                # pages separately so they cannot delay rendering the shell.
                if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) <= STARTUP_TRANSCRIPT_BYTES:
                    latest = candidate
        response = make_response(render_template("index.html", startup={
            "meta": meta_snapshot(),
            "auth": auth,
            "conversations": [conversation.as_dict() for conversation in conversations],
            "latest": latest,
        }))
        response.cache_control.no_store = True
        return response

    @lru_cache(maxsize=2)
    def bundled_assets(kind: str) -> tuple[bytes, str]:
        # Numeric filenames define the order for classic scripts and CSS.
        # Keep the individual files available for packaging checks and debugging.
        files = sorted((assets / "static" / kind).glob(f"[0-9][0-9]-*.{kind}"))
        separator = b"\n;\n" if kind == "js" else b"\n"
        body = separator.join(file.read_bytes() for file in files)
        return body, sha256(body).hexdigest()

    @app.get("/static/<any(css,js):kind>/startup")
    def startup_assets(kind: str):
        body, etag = bundled_assets(kind)
        response = Response(body, mimetype="text/css" if kind == "css" else "text/javascript")
        response.set_etag(etag)
        response.cache_control.no_cache = True
        return response.make_conditional(request)

    # -- meta ------------------------------------------------------------------

    def meta_snapshot() -> dict[str, Any]:
        runtime = rt()
        return {
            "cwd": str(Path.cwd()),
            "home": str(Path.home()),
            "default_model": (
                POLLINATIONS_MODEL
                if runtime.config.use_pollinations_free
                else runtime.config.agent_model
            ),
            "judge_model": runtime.config.judge_model,
            "hidden_models": runtime.store.get_state("hidden_picker_models") or [],
            "default_mode": runtime.config.default_permission_mode.value,
            "modes": [
                {"value": mode.value, "label": mode.label} for mode in Mode
            ],
            "tools": registry.names(),
        }

    @app.get("/api/meta")
    def meta():
        return jsonify(meta_snapshot())

    def picker_catalog() -> dict[str, Any]:
        runtime = rt()
        session = runtime.session
        free_mode = runtime.config.use_pollinations_free
        errors = []
        slugs = []
        capabilities = {}
        try:
            chat = chat_models(session.list_models(), session.config.image_model)
            slugs.extend(chat)
            if chat and not free_mode:
                slugs.insert(0, "auto")
            capabilities.update({model: session.model_capabilities(model) for model in chat})
        except AuthError:
            pass  # OpenCode Go can be used without a ChatGPT account.
        except Exception:
            errors.append("Could not load ChatGPT models.")
            if login_manager().status().get("authenticated"):
                slugs.append("auto")
                if not (is_go_model(runtime.config.agent_model) or is_custom_model(runtime.config.agent_model)):
                    slugs.append(runtime.config.agent_model)

        labels = {"auto": "Auto"}
        try:
            for model, info in session.opencode_go.models().items():
                slugs.append(model)
                labels[model] = info.get("name") or model.split("/", 1)[1]
                capabilities[model] = session.model_capabilities(model)
        except (OSError, ValueError, RuntimeError) as error:
            errors.append(str(error))

        custom_models, custom_errors = session.custom_providers.catalog()
        slugs.extend(custom_models)
        labels.update(custom_models)
        capabilities.update({model: session.model_capabilities(model) for model in custom_models})
        errors.extend(custom_errors)

        runtime.model_context_windows = {
            model: context_window for model, capability in capabilities.items()
            if (context_window := capability.get("context_window"))
        }
        return {
            "models": list(dict.fromkeys(slugs)),
            "labels": labels,
            "hidden_models": runtime.store.get_state("hidden_picker_models") or [],
            "free_mode": free_mode,
            "efforts": list(REASONING_EFFORTS),
            "capabilities": capabilities,
            "errors": errors,
        }

    @app.get("/api/models")
    def models():
        return jsonify(picker_catalog())

    @app.put("/api/settings/model-picker")
    def save_model_picker():
        hidden = _body().get("hidden_models")
        if not isinstance(hidden, list) or len(hidden) > 500 or any(
            not isinstance(model, str) or not model or len(model) > 200 for model in hidden
        ):
            return jsonify({"error": "Invalid hidden model list."}), 400
        rt().store.set_state("hidden_picker_models", list(dict.fromkeys(hidden)))
        return jsonify({"saved": True})

    @app.route("/api/settings/custom-providers", methods=["GET", "POST"])
    @app.route("/api/settings/custom-providers/<provider_id>", methods=["PUT", "DELETE"])
    def custom_provider_settings(provider_id=None):
        providers = rt().session.custom_providers
        if request.method == "GET":
            return jsonify({"providers": providers.list()})
        if remote_manager().is_remote_host(request.host):
            return jsonify({"error": "Manage custom providers in the local CodeLithe window."}), 403
        try:
            if request.method == "DELETE":
                providers.delete(provider_id)
                return jsonify({"deleted": True})
            saved = providers.save(_body(), provider_id)
        except KeyError:
            return jsonify({"error": "Custom provider not found."}), 404
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        return jsonify(saved), 201 if request.method == "POST" else 200

    @app.route("/api/settings/opencode-go", methods=["GET", "PUT"])
    def opencode_go_settings():
        go = rt().session.opencode_go
        if request.method == "PUT":
            if remote_manager().is_remote_host(request.host):
                return jsonify({"error": "Change API keys in the local CodeLithe window."}), 403
            try:
                go.save(_body())
            except ValueError as error:
                return jsonify({"error": str(error)}), 400
        return jsonify(go.status())

    @app.get("/api/usage/opencode-go")
    def opencode_go_usage():
        try:
            return jsonify({"usage": rt().session.opencode_go.usage()})
        except (OSError, ValueError, RuntimeError) as error:
            return jsonify({"error": str(error)}), 502

    # -- Remote Control -------------------------------------------------------

    @app.get("/api/remote")
    def remote_status():
        return jsonify(remote_manager().status())

    @app.post("/api/remote/download")
    def remote_download():
        try:
            return jsonify(remote_manager().download())
        except RemoteError as error:
            return jsonify({"error": str(error)}), 502

    @app.post("/api/remote/remove")
    def remote_remove():
        try:
            return jsonify(remote_manager().remove())
        except RemoteError as error:
            return jsonify({"error": str(error)}), 409

    @app.post("/api/remote/start")
    def remote_start():
        try:
            return jsonify(remote_manager().start()), 201
        except RemoteError as error:
            return jsonify({"error": str(error)}), 409

    @app.post("/api/remote/stop")
    def remote_stop():
        return jsonify(remote_manager().stop())

    # -- application updates -------------------------------------------------

    @app.get("/api/update")
    def update_status():
        try:
            return jsonify(check_update())
        except UpdateError as error:
            return jsonify({"error": str(error)}), 502

    @app.post("/api/update")
    def start_update():
        try:
            return jsonify(install_update()), 202
        except UpdateError as error:
            return jsonify({"error": str(error)}), 409

    # -- ChatGPT account ------------------------------------------------------

    def auth_snapshot() -> dict[str, Any]:
        status = login_manager().status()
        status["free_mode"] = rt().config.use_pollinations_free
        go_status = rt().session.opencode_go.status()
        status["opencode_go"] = go_status["api_key_configured"]
        status["opencode_go_usage"] = go_status["usage_key_configured"]
        status["custom_providers"] = rt().session.custom_providers.configured()
        return status

    @app.get("/api/auth")
    def auth_status():
        return jsonify(auth_snapshot())

    @app.post("/api/auth/login")
    def start_auth_login():
        try:
            authorization_url = login_manager().start()
        except RuntimeError as error:
            return jsonify({"error": str(error)}), 409
        return jsonify({"authorization_url": authorization_url}), 202

    @app.post("/api/auth/cancel")
    def cancel_auth_login():
        login_manager().cancel()
        return jsonify({"cancelled": True})

    @app.get("/api/usage")
    def usage():
        """The ChatGPT plan's allowance, as last reported by Codex.

        Returns ``{"usage": null}`` rather than an error when nothing is known
        yet -- the figures only arrive on the headers of a real request, so a
        fresh install genuinely has none until the first message.
        """
        return jsonify({"usage": rt().plan_usage()})

    @app.get("/api/usage/banked-resets")
    def banked_resets():
        """Banked rate-limit reset credits this account has available.

        Undocumented ChatGPT feature (see Session.list_reset_credits), so a
        failure here is reported, not raised -- this is a nice-to-have
        indicator, not something that should break the usage view.
        """
        try:
            return jsonify({"credits": rt().session.list_reset_credits()})
        except UpstreamError as error:
            return jsonify({"credits": [], "error": str(error)})

    @app.post("/api/usage/banked-resets/<credit_id>/redeem")
    def redeem_banked_reset(credit_id: str):
        """Spend one banked reset credit now -- always an explicit user click."""
        try:
            result = rt().session.redeem_reset_credit(credit_id)
        except UpstreamError as error:
            return jsonify({"error": str(error)}), 502
        # The redeemed window's usage figure only updates on Codex's side
        # with the account's next real request -- redemption goes through
        # `wham`, not `/responses`, so there are no fresh rate-limit headers
        # to read here. The UI says as much rather than showing a stale ring.
        return jsonify(result)

    # -- importing from the Codex CLI ---------------------------------------------

    @app.get("/api/import/codex")
    def codex_import_preview():
        """How many Codex sessions are on disk and how many are new."""
        return jsonify(preview_codex_sessions(rt().store))

    @app.post("/api/import/codex")
    def codex_import():
        """Import every Codex session that is not in the database yet.

        Synchronous on purpose: this is a one-off button, and a progress
        channel for it would outweigh the feature. It can take a while on a
        large history, which is why the button reports that it is working.
        """
        runtime = rt()
        report = import_codex_sessions(
            runtime.store,
            permission_mode=runtime.config.default_permission_mode.value,
            default_model=runtime.config.agent_model,
        )
        return jsonify(report.as_dict())

    # -- conversations ------------------------------------------------------------

    @app.get("/api/conversations")
    def list_conversations():
        return jsonify(
            {"conversations": [c.as_dict() for c in rt().store.list_conversations()]}
        )

    @app.post("/api/conversations")
    def create_conversation():
        body = _body()
        try:
            conversation = rt().create_conversation(
                workspace=body.get("workspace"),
                model=body.get("model"),
                mode=Mode.parse(body.get("mode"), rt().config.default_permission_mode),
                reasoning_effort=body.get("reasoning_effort"),
                fast_mode=bool(body.get("fast_mode")),
                flex_mode=bool(body.get("flex_mode")),
                agent_id=body.get("agent_id", "general"),
            )
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        return jsonify({
            **conversation.as_dict(),
            "context_window": conversation_context_window(conversation.model),
        }), 201

    @app.get("/api/agents")
    def list_agents():
        return jsonify({"agents": [profile.public() for profile in rt().agent_profiles.list()]})

    @app.post("/api/agents")
    @app.route("/api/agents/<agent_id>", methods=["PUT", "DELETE"])
    def save_agent(agent_id=None):
        try:
            if request.method == "DELETE":
                rt().agent_profiles.delete(agent_id)
                return jsonify({"deleted": True})
            profile = rt().agent_profiles.save(_body(), agent_id)
            return jsonify(profile.public()), 201 if agent_id is None else 200
        except ValueError as error:
            return jsonify({"error": str(error)}), 400

    @app.post("/api/conversations/<conversation_id>/subagents/<subagent_id>/stop")
    def stop_subagent(conversation_id: str, subagent_id: str):
        conversation, error = _conversation_or_404(conversation_id)
        if error:
            return error
        if not rt().stop_subagent(conversation, subagent_id):
            return jsonify({"error": "This subagent is no longer active."}), 409
        return jsonify({"stopped": True})

    def conversation_context_window(model: str) -> int:
        return rt().model_context_windows.get(model) or context_window_for(model)

    def conversation_snapshot(
        conversation: Conversation, limit: int | None = None, before: int | None = None,
    ) -> dict[str, Any]:
        runtime = rt()
        entries = runtime.store.load_entries(conversation.id, limit=limit, before_id=before)
        oldest = min((entry["id"] for entry in entries), default=None)
        return {
            **conversation.as_dict(),
            "entries": entries,
            "has_more": limit is not None and runtime.store.has_items_before(conversation.id, oldest),
            "context_window": conversation_context_window(conversation.model),
            "busy": runtime.is_busy(conversation),
            "pending_permissions": runtime.pending_permissions(conversation),
        }

    @app.get("/api/conversations/<conversation_id>")
    def get_conversation(conversation_id: str):
        conversation, error = _conversation_or_404(conversation_id)
        if error:
            return error
        # Optional pagination: without params the whole transcript is
        # returned (reconcile paths rely on that).
        return jsonify(conversation_snapshot(
            conversation, request.args.get("limit", type=int), request.args.get("before", type=int),
        ))

    @app.get("/api/conversations/<conversation_id>/entries")
    def get_conversation_entries(conversation_id: str):
        """One page of transcript items, oldest first -- pagination cursor.

        The UI opens a chat with the newest page and prepends older pages
        while the user scrolls up, so a long transcript never blocks the
        conversation from being shown.
        """
        conversation, error = _conversation_or_404(conversation_id)
        if error:
            return error
        runtime = rt()
        limit = request.args.get("limit", type=int)
        before = request.args.get("before", type=int)
        entries = runtime.store.load_entries(conversation_id, limit=limit, before_id=before)
        oldest = min((entry["id"] for entry in entries), default=None)
        has_more = limit is not None and runtime.store.has_items_before(
            conversation_id, oldest
        )
        return jsonify({"entries": entries, "has_more": has_more})

    @app.get("/api/conversations/<conversation_id>/files/<path:file_path>")
    def conversation_file(conversation_id: str, file_path: str):
        """Serve one workspace file for an explicitly requested chat showcase."""
        conversation, error = _conversation_or_404(conversation_id)
        if error:
            return error
        workspace = Path(conversation.workspace).resolve()
        candidate = _attachment_path(conversation_id, file_path)
        if candidate is None:
            candidate = (workspace / file_path).resolve()
            if candidate == workspace or workspace not in candidate.parents:
                return jsonify({"error": "File path is outside the workspace."}), 400
        if not candidate.is_file():
            return jsonify({"error": "File not found."}), 404
        return send_file(candidate, as_attachment=False, conditional=True)

    @app.get("/api/conversations/<conversation_id>/uploads/<path:filename>")
    def uploaded_file(conversation_id: str, filename: str):
        """Serve a central upload without routing its storage prefix as a workspace path."""
        _conversation, error = _conversation_or_404(conversation_id)
        if error:
            return error
        root = _upload_dir(conversation_id)
        candidate = (root / filename).resolve()
        if root not in candidate.parents or not candidate.is_file():
            return jsonify({"error": "File not found."}), 404
        return send_file(candidate, as_attachment=False, conditional=True)

    @app.patch("/api/conversations/<conversation_id>")
    def update_conversation(conversation_id: str):
        conversation, error = _conversation_or_404(conversation_id)
        if error:
            return error
        body = _body()
        runtime = rt()

        if "mode" in body:
            runtime.set_mode(conversation, Mode.parse(body.get("mode"), Mode.ASK))
        fields = {
            key: str(body[key]) for key in ("title", "model") if body.get(key) is not None
        }
        if "reasoning_effort" in body:
            fields["reasoning_effort"] = runtime.normalize_model_effort(
                fields.get("model", conversation.model), body.get("reasoning_effort"),
            )
            if conversation.model == "gpt-6-astra" and runtime.is_busy(conversation):
                runtime.update_run_reasoning(conversation, fields["reasoning_effort"])
        # Mutually exclusive -- a request sets at most one; whichever one it
        # sets wins over whatever the other was previously.
        if "fast_mode" in body:
            fields["fast_mode"] = 1 if body.get("fast_mode") else 0
            if fields["fast_mode"]:
                fields["flex_mode"] = 0
        if "flex_mode" in body:
            fields["flex_mode"] = 1 if body.get("flex_mode") else 0
            if fields["flex_mode"]:
                fields["fast_mode"] = 0
        if fields:
            runtime.store.update_conversation(conversation_id, **fields)

        refreshed = runtime.store.get_conversation(conversation_id)
        payload = refreshed.as_dict() if refreshed else {}
        if refreshed and "model" in fields:
            payload["context_window"] = conversation_context_window(refreshed.model)
        return jsonify(payload)

    @app.delete("/api/conversations/<conversation_id>")
    def delete_conversation(conversation_id: str):
        rt().delete_conversation(conversation_id)
        return jsonify({"deleted": conversation_id})

    @app.get("/api/settings")
    def global_settings():
        data_root = rt().config.data_dir.resolve()
        path = (data_root / GLOBAL_MEMORY_PATH).resolve()
        try:
            memory = path.read_text(encoding="utf-8") if path.is_file() else ""
        except (OSError, UnicodeDecodeError):
            memory = ""
        return jsonify({"memory": memory, "memory_path": str(path)})

    @app.get("/api/settings/behaviour")
    def behaviour_settings():
        """Current values plus the schema that describes the form.

        The schema travels with the values so the UI cannot invent a bound the
        server would reject -- there is one table, in `codelite/settings.py`.
        """
        catalog = picker_catalog()
        hidden = set(catalog["hidden_models"])
        # Auto is a routing mode, and cannot itself be routed to.
        visible = [model for model in catalog["models"] if model not in hidden and model != "auto"]
        return jsonify({
            "values": rt().behaviour_settings(),
            "schema": {**behaviour_schema(visible), "model_labels": catalog["labels"]},
        })

    @app.put("/api/settings/behaviour")
    def save_behaviour_settings():
        values = _body().get("values")
        if not isinstance(values, dict):
            return jsonify({"error": "`values` must be an object."}), 400
        try:
            saved = rt().save_behaviour_settings(values)
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        return jsonify({"values": saved})

    @app.put("/api/settings/memory")
    def save_global_memory():
        content = _body().get("content")
        if not isinstance(content, str):
            return jsonify({"error": "`content` must be a string."}), 400
        if len(content) > MAX_MEMORY_CHARS:
            return jsonify(
                {"error": f"Global memory is limited to {MAX_MEMORY_CHARS:,} characters."}
            ), 400
        data_root = rt().config.data_dir.resolve()
        target = (data_root / GLOBAL_MEMORY_PATH).resolve()
        if data_root not in target.parents:
            return jsonify({"error": "Global memory path leaves the data directory."}), 400
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return jsonify({"error": f"Could not save global memory: {exc}"}), 500
        return jsonify({"saved": str(target), "content": content})

    @app.get("/api/conversations/<conversation_id>/project-settings")
    def project_settings(conversation_id: str):
        conversation, error = _conversation_or_404(conversation_id)
        if error:
            return error
        workspace = Path(conversation.workspace).resolve()

        def read_or(relative: Path, fallback: str) -> str:
            path = (workspace / relative).resolve()
            if workspace not in path.parents:
                return fallback
            try:
                return path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return fallback

        return jsonify(
            {
                "memory": read_or(MEMORY_PATH, ""),
                "mcp": read_or(MCP_CONFIG_PATH, '{\n  "mcpServers": {}\n}\n'),
                "lsp": read_or(LSP_CONFIG_PATH, '{\n  "servers": {}\n}\n'),
                "skills": [
                    skill.as_dict()
                    for skill in discover_skills(workspace, rt().config.data_dir)
                ],
                "project_dir": str(workspace / ".codelite"),
            }
        )

    @app.put("/api/conversations/<conversation_id>/project-settings/<kind>")
    def save_project_settings(conversation_id: str, kind: str):
        conversation, error = _conversation_or_404(conversation_id)
        if error:
            return error
        content = _body().get("content")
        if not isinstance(content, str):
            return jsonify({"error": "`content` must be a string."}), 400
        paths = {"memory": MEMORY_PATH, "mcp": MCP_CONFIG_PATH, "lsp": LSP_CONFIG_PATH}
        relative = paths.get(kind)
        if relative is None:
            return jsonify({"error": "Unknown project setting."}), 404
        if kind == "memory":
            if len(content) > MAX_MEMORY_CHARS:
                return jsonify(
                    {"error": f"Project memory is limited to {MAX_MEMORY_CHARS:,} characters."}
                ), 400
        else:
            try:
                parsed = json.loads(content)
            except ValueError as exc:
                return jsonify({"error": f"Invalid JSON: {exc}"}), 400
            required_key = "mcpServers" if kind == "mcp" else "servers"
            if not isinstance(parsed, dict) or not isinstance(parsed.get(required_key), dict):
                return jsonify({"error": f"Config requires a `{required_key}` object."}), 400
            content = json.dumps(parsed, indent=2) + "\n"
        workspace = Path(conversation.workspace).resolve()
        target = (workspace / relative).resolve()
        if workspace not in target.parents:
            return jsonify({"error": "Project setting path leaves the workspace."}), 400
        try:
            if kind == "mcp":
                save_mcp_config(workspace, content)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
        except (OSError, McpError) as exc:
            return jsonify({"error": f"Could not save {relative}: {exc}"}), 500
        return jsonify({"saved": str(relative), "content": content})

    @app.post("/api/conversations/<conversation_id>/uploads")
    def upload_file(conversation_id: str):
        """Accept a user-selected file in CodeLithe's central data directory."""
        conversation, error = _conversation_or_404(conversation_id)
        if error:
            return error
        uploaded = request.files.get("file")
        if uploaded is None or not uploaded.filename:
            return jsonify({"error": "Attach one file in the `file` field."}), 400
        filename = secure_filename(uploaded.filename) or "pasted-file"
        upload_dir = _upload_dir(conversation_id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        target = upload_dir / f"{uuid.uuid4().hex}_{filename}"
        temporary = None
        size = 0
        try:
            with tempfile.NamedTemporaryFile(dir=upload_dir, delete=False) as handle:
                temporary = Path(handle.name)
                while chunk := uploaded.stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise ValueError("Files must be 50 MB or smaller.")
                    handle.write(chunk)
            temporary.replace(target)
        except ValueError as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            return jsonify({"error": str(exc)}), 413
        except OSError as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            logger.warning("Could not save uploaded file", exc_info=True)
            return jsonify({"error": f"Could not save upload: {exc}"}), 500

        return jsonify(
            {
                "path": f"uploads/{conversation_id}/{target.name}",
                "name": filename,
                "size": size,
                "type": uploaded.mimetype or "application/octet-stream",
            }
        ), 201

    # -- runs ----------------------------------------------------------------------

    @app.get("/api/conversations/<conversation_id>/run-status")
    def run_status(conversation_id: str):
        conversation, error = _conversation_or_404(conversation_id)
        if error:
            return error
        return jsonify({"busy": rt().is_busy(conversation)})

    @app.post("/api/conversations/<conversation_id>/messages")
    def send_message(conversation_id: str):
        conversation, error = _conversation_or_404(conversation_id)
        if error:
            return error
        local = not remote_manager().is_remote_host(request.host)
        if not local and rt().desktop.owner() == conversation_id:
            return jsonify({"error": "Send messages to this Computer Use session in the local window."}), 403
        body = _body()
        text = str(body.get("text") or "").strip()
        if not text:
            return jsonify({"error": "`text` must not be empty."}), 400
        workspace = Path(conversation.workspace).resolve()
        attachments: list[dict[str, str]] = []
        supplied_attachments = body.get("attachments")
        if supplied_attachments is not None and not isinstance(supplied_attachments, list):
            return jsonify({"error": "`attachments` must be a list."}), 400
        for item in supplied_attachments or []:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                return jsonify({"error": "Each attachment needs a file path."}), 400
            supplied_path = item["path"]
            candidate = _attachment_path(conversation_id, supplied_path)
            if candidate is None:
                candidate = (workspace / supplied_path).resolve()
                if candidate == workspace or workspace not in candidate.parents:
                    return jsonify({"error": "An attachment is outside the workspace."}), 400
            if not candidate.is_file():
                return jsonify({"error": "An attachment file was not found."}), 400
            attachments.append(
                {
                    "path": supplied_path,
                    "resolved_path": str(candidate),
                    "name": str(item.get("name") or candidate.name),
                    "type": str(item.get("type") or "application/octet-stream"),
                }
            )
            if candidate != (workspace / supplied_path).resolve():
                text = text.replace(supplied_path, str(candidate))
        supplied_message_id = body.get("message_id")
        message_id = (
            supplied_message_id[:128]
            if isinstance(supplied_message_id, str) and supplied_message_id
            else uuid.uuid4().hex
        )
        try:
            rt().start_run(conversation, text, attachments, message_id, allow_desktop=local)
        except RunInProgress as busy:
            # Astra receives a correction at the next safe model boundary. A
            # follow-up with uploads still needs a fresh turn because its file
            # permissions belong to that turn's ToolContext.
            if not attachments and rt().steer_run(conversation, text, message_id):
                return jsonify({"started": True, "steered": True, "message_id": message_id}), 202
            return jsonify({"error": str(busy)}), 409
        return jsonify({"started": True, "message_id": message_id}), 202

    @app.post("/api/conversations/<conversation_id>/cancel")
    def cancel_run(conversation_id: str):
        return jsonify({"cancelled": rt().cancel_run(conversation_id)})

    @app.post("/api/conversations/<conversation_id>/compact")
    def compact_conversation(conversation_id: str):
        conversation, error = _conversation_or_404(conversation_id)
        if error:
            return error
        try:
            rt().start_compaction(conversation)
        except RunInProgress as busy:
            return jsonify({"error": str(busy)}), 409
        return jsonify({"started": True}), 202

    @app.get("/api/conversations/<conversation_id>/events")
    def events(conversation_id: str):
        conversation, error = _conversation_or_404(conversation_id)
        if error:
            return error
        return Response(rt().subscribe(conversation), headers=SSE_HEADERS)

    @app.get("/api/conversation-activity")
    def conversation_activity():
        return Response(rt().subscribe_activity(), headers=SSE_HEADERS)

    # -- permissions -----------------------------------------------------------------

    @app.post("/api/conversations/<conversation_id>/permission/<request_id>")
    def reply_permission(conversation_id: str, request_id: str):
        conversation, error = _conversation_or_404(conversation_id)
        if error:
            return error
        body = _body()
        accepted = rt().reply_permission(
            conversation,
            request_id,
            str(body.get("reply") or ""),
            str(body.get("feedback") or ""),
        )
        if not accepted:
            return jsonify({"error": "No such pending request, or invalid reply."}), 404
        return jsonify({"ok": True})

    @app.post("/api/conversations/<conversation_id>/question/<question_id>")
    def reply_question(conversation_id: str, question_id: str):
        conversation, error = _conversation_or_404(conversation_id)
        if error:
            return error
        accepted = rt().reply_question(conversation, question_id, str(_body().get("answer") or ""))
        if not accepted:
            return jsonify({"error": "No such pending question, or invalid answer."}), 404
        return jsonify({"ok": True})

    return app
