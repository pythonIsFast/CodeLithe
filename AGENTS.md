# AGENTS.md – Arbeitsnotizen für (KI-)Agenturen an diesem Repo

Zweck: festhalten, wie in diesem Repo gearbeitet wird, wie verifiziert wird und
welches Wissen nicht im Code steckt. Vor größeren Änderungen lesen.

## Projekt in Kürze

**CodeLithe** – ein kleiner Coding-Agent mit eigenem UI. Python + pywebview,
Anmeldung über das ChatGPT-Konto (kein API-Key), lokale Flask-Backend-Threads,
natives OS-Webview als Fenster (kein Electron). Apache-2.0, UI-Texte Englisch.

- `codelite/app/` – Fenster (`window.py`), HTTP-Layer (`server.py`), UI
  (`templates/`, `static/`), Laufzeit (`runtime.py`)
- `codelite/agent/` – Agent-Loop, Router, Judge, System-Prompt
- `codelite/provider/` – OpenAI-kompatibler Proxy, bewusst **nur
  Standardbibliothek** (`python3 -m codelite.provider` läuft ohne deps)
- `codelite/tools/` – eine Datei pro Tool, registriert in `registry.py`
- `codelite/db/` – SQLite-Store (Responses-API-Items als History)
- `codelite/browser/` – verstecktes Script-Browser-Fenster (JSON-Protokoll)
- `codelite/screenshot.py` – WebKitGTK-Snapshot, geteilt zwischen App-Modus
  `--screenshot` und dem `screenshot`-Action des Hidden-Browsers
- `tests/` – pytest, aber **nicht in CI** (CI macht nur Smoke-Tests der Builds)
- `packaging/` – zipapp (`build-pyz.sh`), .deb (`build-deb.sh`), Windows (ISS);
  Release nur wenn `VERSION` geändert wurde

## Architektur & Skripte (was was macht)

### CLI-Einstiegspunkte

- **`run.py`** – putzt das Repo-Root auf `sys.path` und ruft
  `codelite.__main__.main()`; fängt ImportError ab und nennt den
  wahrscheinlichen Fix (`pip install -r requirements.txt`).
- **`python3 -m codelite`** (`codelite/__main__.py`) – Flags
  `--host/--port` (Default 127.0.0.1:10532), `--model`, `--mode`
  (ask|permit_writes|auto|bypass), `--data-dir`, `--headless` (nur Server,
  kein Fenster), `--screenshot [PATH]` (öffnet Fenster, wartet auf den
  Ready-Marker, PNG, beendet; Linux/WebKitGTK only, inkompatibel mit
  `--headless`), `--screenshot-delay`, `-v`. Verstecktes Flag
  `--browser-host`: re-exec'd das eigene Binary als Hidden-Browser-Child
  (verpackte Builds haben keinen System-Python zum Spawnen).
- **`python3 -m codelite.provider`** – startet nur den OpenAI-kompatiblen
  Proxy (`run_server`), ohne Agent/Fenster.

### `codelite/config.py` / `codelite/settings.py`

- **config.py** – App-Konfig (getrennt von `provider/config.py`):
  `DEFAULT_AGENT_MODEL="gpt-6-astra"`, `DEFAULT_JUDGE_MODEL="gpt-5.6-luna"`,
  Default-Port 10532, `SHELL_TIMEOUT_SECONDS=120`,
  `MAX_TOOL_OUTPUT_CHARS=30_000`, `CONTEXT_COMPACT_FRACTION=0.80`,
  `CONTEXT_STOP_FRACTION=0.95` (kein Step-Cap, bewusst),
  `CONTEXT_WINDOWS`-Fallback-Tabelle,
  `REASONING_EFFORTS`, `FAST/FLEX_SERVICE_TIER`. Funktionen
  `normalize_effort()`, `chat_models()`, `context_window_for()`,
  `default_data_dir()` (ehrt `CODELITE_HOME`/`XDG_DATA_HOME` →
  `~/.local/share/codelite`). `AppConfig`-Dataclass, Properties
  `db_path`, `base_url`.
- **settings.py** – „Behaviour"-Settings, einmal deklariert:
  `Setting`-Dataclass (key, label, kind: bool/int/float/model/models,
  default, group, help) in `SETTINGS`; Gruppen `GROUPS` = provider, agent,
  auto, tools, integrations, context, danger. `schema(models)` wird als
  Formular-Schema an die UI geschickt; `coerce()` clamped Zahlen statt zu
  verwerfen (`context_compact_fraction` + `context_stop_fraction` dürfen
  sich nicht kreuzen, `MIN_FRACTION_GAP=0.02`); prozessweite Spiegel
  `apply()`/`active()` für die LSP/MCP-Singletons.

### `codelite/app/` – Fenster + HTTP + UI

- **window.py** – `serve_in_background()` (Flask in Daemon-Thread,
  `_wait_for_port()` mit 15s-Timeout), `run()`: headless → block forever;
  sonst WebView2-Registry-Check (Windows), GTK-Identität
  (`GLib.set_prgname("code-lite")`), `webview.create_window(js_api=JsApi())`,
  `icon`-Parameter nur wenn die Signatur ihn hat (pywebview-5.0.x).
  Screenshot-Modus: wartet auf `document.body.dataset.ready` aus
  `15-bootstrap.js`, `capture_window_png()`, PNG. `JsApi`: nativer
  Ordnerpicker, `open_external` (nur Login-URL), Clipboard-Bild-Lesen
  (GTK-Fallback via `GLib.idle_add`), `quit_for_update`.
  `show_startup_error()` mit Windows-MessageBox-Fallback.
- **server.py** – Flask-Layer, Handler dünn, Verhalten in `Runtime`.
  Asset-Auflösung `_asset_root()`: plain checkout, PyInstaller
  (`sys._MEIPASS`), zipapp → `_unpack_zipapp_assets()`. `MAX_CONTENT_LENGTH=50MB`.
  Route-Gruppen: Pages (`/`, `/remote/login`), Before/After-Request
  (`require_remote_login`, Security-Headers), Meta (`/api/meta`,
  `/api/models`), Remote Control, Updates, ChatGPT-Account
  (`/api/auth*`, `/api/usage*` inkl. banked-resets), Codex-Import
  (`/api/import/codex`), Conversations (`GET/POST /api/conversations`,
  `GET/PATCH/DELETE .../<id>` – GET mit optionaler Pagination
  `?limit&before`, liefert `entries/has_more/context_window/busy/
  pending_permissions`; `.../entries` eine Seite; `.../files`, `.../uploads`
  mit 50MB-Grenze), Settings (`/api/settings/behaviour`,
  `/api/settings/memory`, pro Conversation `project-settings[/kind]` für
  memory/mcp/lsp), Runs (`POST .../messages` → `start_run`, bei Busy →
  Astra-Steering via `steer_run`, sonst 409; `cancel`, `compact`,
  `run-status`, SSE `.../events`), Permissions/Questions
  (`POST .../permission/<id>` once|session|deny, `POST .../question/<id>`).
- **runtime.py** – `Runtime` verdrahtet alles: `Store`,
  `Session(ProviderConfig(auth.json im data_dir))`,
  `_conversations: dict[str, ConversationRuntime]` (permissions, questions,
  subscriber-Queues, runner, thread). `_apply_behaviour()`/`save_behaviour_settings()`
  (persistiert in `app_state["behaviour"]`, spiegelt in `settings.apply`),
  `subscribe()` → SSE-Generator (ready-Frame, Re-Annonce pending
  permissions/questions, Keepalive alle 15s), `publish()` fan-out,
  `start_run()` (AgentRunner + Thread, am Ende `refresh_plan_usage`),
  `steer_run`, `start_compaction`, `cancel_run`, `refresh_plan_usage()`
  (Rate-Limits aus Response-Headern → `app_state["plan_usage"]`).

#### templates/ + static (kein Build-Step!)

- `index.html` dünn: 8 CSS- und 15 JS-Dateien per Jinja-Loop, Partials
  `sidebar/topbar/chat/details/dialogs/auth/settings.html`; dazu
  `remote_login.html` eigenständig.
- **JS (Ladeordnung = Reihenfolge, gemeinsamer globaler Scope):**
  - `01-core.js` – `el`/`state`, `$`, HTTP-Helper `api/get/post/patch/del`, `toast()`
  - `02-auth.js` – Account-Status, OAuth-Polling, Pollinations-Free-Toggle
  - `03-settings.js` – Projekt-Settings, Remote-UI, Banked-Resets, Updates, globales Memory
  - `04-behaviour.js` – Behaviour-Formular (Live-Queries, da Panels dynamisch), Codex-Import-UI
  - `05-messages.js` – Markdown-Subset, Bubbles, Reasoning, Auto-Scroll
  - `06-tools.js` – Footer (Zeit/Tokens), Tool-Zeilen (`toolRow/outputSummary/toolCategory`), `todoCard/renderTodos`, Copy/Retry, Transcript-Paginierung (`prependEntries`/`trackEntryWindow`)
  - `07-details.js` – Details-Panel, Permission-Labels, New-Session-Draft
  - `08-conversations.js` – Sidebar-Liste, `openConversation` (neuester Chat ohne await), Paginierung (`TRANSCRIPT_PAGE_SIZE`, `maybeLoadOlderEntries`), Rename/Delete-Menüs
  - `09-usage.js` – Token-/Context-Ring, `loadPlanUsage`
  - `10-stream.js` – SSE pro Conversation (`connectStream`), Event-Dispatch, `setBusy`/`reconcileRun`, `renderDiff`, Permission-/Question-Dialoge
  - `11-models.js` – Model-/Mode-/Effort-Auswahl, Fast/Flex-Toggles (exklusiv)
  - `12-composer.js` – Attachments, Paste-Dateien, `sendMessage` (Draft-Prinzip)
  - `13-selects.js` – Custom-Dropdowns
  - `14-events.js` – `wireEvents()`: globale DOM-Events, Drag&Drop
  - `15-bootstrap.js` – `startClock()`, `loadAuthenticatedApp`, `init()`; setzt `document.body.dataset.ready`
- **CSS (gleiche Reihenfolge-Logik):** `01-base.css` Design-Tokens (Palette
  unten), `02-sidebar`, `03-layout`, `04-messages` (inkl.
  `.tool-stack:has(.tool-call.running)`-Rahmen), `05-composer`,
  `06-dialogs`, `07-settings`, `08-responsive`.

### `codelite/agent/`

- **loop.py** – `AgentRunner`, führt einen User-Turn zu Ende aus. Ablauf
  `run()`: (1) Run-Modell setzen (Pollinations-Free-Override,
  Conversation-Modell, oder Router bei `AUTO` → `model_selected`-Event),
  Effort clampen; (2) User-Item persistieren, publishen, Titel ableiten;
  (3) Projekt-Kontext + `LocalExtensionHost` + `ToolContext`; (4)
  `_loop()`: pro Step Cancel-Check → `_maybe_compact_history()` (Summarize
  ab 80% des Fensters; Original-Transcript bleibt in SQLite, nur das
  Arbeitsset wird ersetzt) → `_context_exhausted()` (Hard-Stop 95%) →
  `_apply_pending_updates` (queued Steers) → `_request_turn()` (Body mit
  `system_prompt.build`) → `_consume_stream()` (publish
  text/reasoning/tool_pending, Terminal-Event abwarten) → Usage
  publishen/persistieren → ohne Calls: done; sonst `_execute_call` je Call
  → Output-Items. Ephemere `view_image`-Inputs bleiben nur bis zum nächsten
  Turn im Memory. `_invoke()`: Hook-Kette `before_tool` → `tool.run` →
  `after_tool`; Fehler als Tool-Output-Text; `_clip()` auf
  `max_tool_output_chars`. `_repair_history()`: verwaiste
  `function_call`s (Crash) bekommen dauerhaft Placeholder-Outputs.
- **router.py** – Auto-Model-Picker: Router-Modell `gpt-5.6-luna`,
  Fallback `gpt-5.6-terra`; `ROUTER_INSTRUCTIONS` (cheapest-fitting +
  Effort-Regeln, JSON-only); Fehler → Terra/`medium` mit `fallback=True`.
- **judge.py** – Shell-Sicherheits-Modell für Auto-Mode:
  `judge_shell_command()` sieht Befehl *und* Task-Prompt; deny bei
  destruktiv/task-fern/netz/credentials/gate-bypass/privilege-escalation.
- **system_prompt.py** – `BASE_PROMPT`, `MODE_NOTES`, `READ_SCOPE_*`,
  `build(mode, workspace, project_context, read_outside_workspace)`.

### `codelite/provider/` – ChatGPT-OAuth → OpenAI-API, **nur stdlib**

- **config.py** – `ProviderConfig` (auth_file_path, Codex-Client-Version).
- **auth.py** – OAuth: JWT-Claims, Token-Exchange/Refresh,
  `get_auth_status`, `load_auth_tokens()` → `EffectiveAuth` (auto-Refresh).
- **session.py** – `Session`: `list_models`, `model_capabilities`
  (Efforts/Fast/Flex; EU-Astra ohne Fast), `context_window`,
  `send_responses` (Pollinations-Free ↔ CodexTransport),
  `send_chat`/`stream_chat`, `generate_image`/`edit_image`,
  Reset-Credits. Fabrik `load_session()`.
- **transport.py** – `CodexTransport`: Retry mit Rate-Limit-Header-Capture,
  Modellkatalog-Fetch, `normalize_responses_body`, `send_responses_request`.
- **models.py** – Catalog-Parsing, `resolve_codex_client_version`.
- **chat.py** – Chat-Completions ↔ Responses-Übersetzung (auch streaming).
- **sse.py** – SSE-Iteration.
- **pollinations.py** – Free-Fallback: Responses↔Chat-Übersetzung inkl.
  Tool-Calls als Content, Requests ≥15s auseinander (`_wait_for_slot`).
- **images.py / multipart.py / limits.py** – Bild-Requests, eigener
  Multipart-Parser, `parse_rate_limits(headers)` (5h/weekly) für den Plan-Ring.
- **login.py** – `ChatGPTLoginManager`: lokaler Callback-HTTP-Server,
  Authorize-URL, Status, Cancel.
- **server.py** – stdlib-`http.server`-Proxy: `/health`, `/v1/models`,
  `/v1/chat/completions` (stream→SSE), `/v1/responses` (lehnt
  `previous_response_id` ab — stateless), `/v1/images/*`; bindet 127.0.0.1.

### `codelite/tools/` (eine Datei pro Tool, registriert in `registry.py`)

- **base.py / context.py** – `Tool`-Dataclass + `to_responses_tool`;
  `ToolContext` (per-Run): workspace, permissions, session, questions,
  attachments, publish; `resolve` (schreibbeschränkt auf Workspace),
  `resolve_read` (optional darüber hinaus via
  `settings.active("allow_read_outside_workspace")`).
- **registry.py** – Tools: `browser, read_file, write_file, edit_file,
  list_dir, grep, find_files, code_intelligence, extensions,
  project_memory, generate_image, showcase_file, shell, todo_write,
  ask_user, view_image, web_search, web_fetch`;
  `ASYNC_SAFE_TOOL_NAMES` (nur lesende Tools dürfen von Astra async
  zurückkommen).
- **files.py** – Reads nie gated; Writes/Edits über `require_write` (mit
  Diff für den Dialog, `MAX_DIFF_LINES=400`); Read-Limit 400 KB/2000 Zeilen.
- **shell.py** – `require_shell` (vier Modes + Judge in auto); cwd trägt über.
- **search.py** – `grep`/`find_files` pure Python, `MAX_MATCHES=200`.
- **browser.py** – Hidden-Browser: `navigate/snapshot/click/fill/evaluate/
  screenshot/reset`; Lesen frei, Aktives wie ein Write gated.
- **code_intelligence.py** – Facade über `integrations.lsp`.
- **extensions.py** – Skills + MCP-Tools; MCP-Server-Start permission-gated.
- **images.py / vision.py** – `generate_image` (OAuth-Session);
  `view_image` (Workspace-Bild ≤20 MB als Data-URL in den nächsten Turn).
- **memory.py** – `project_memory` (global = data_dir-Datei, project =
  `.codelite/memory.md`; Diff-Output).
- **questions.py** – `ask_user` über `QuestionManager` (blockiert auf Event).
- **showcase.py / todo.py / web.py** – Chat-Showcase von Dateien;
  `todo_write` (Liste wird wholesale ersetzt, max 30 Items);
  `web_search`/`web_fetch` dependency-free, bounded (2 MB), SSRF-Schutz.

### `codelite/permission/` + `codelite/questions.py`

- **modes.py** – `Mode`-Enum: `ask`, `permit_writes`, `auto`, `bypass`.
- **manager.py** – `require_write(path, diff)`/`require_shell(command)` →
  `Decision` je Mode (Judge in auto); `_ask_user` erstellt `PendingRequest`
  (mit Diff) und blockiert; `reply(request_id, once|session|deny)`;
  Session-Grants; `list_pending`/`cancel_pending`; `PermissionDenied` mit
  menschenlesbarer Message.
- **questions.py** – `QuestionManager` für strukturierte
  Rückfragen (`ask_user`-Tool, UI-Dialog).

### `codelite/project/` + `codelite/integrations/`

- **context.py** – Pfade (`.codelite/memory.md`, `mcp.json`, `lsp.json`),
  Skills mit YAML-Frontmatter, `_repository_instructions` (AGENTS.md/
  CLAUDE.md, Budget), `build_project_context()` → Textblock fürs System-Prompt.
- **plugins.py** – `LocalExtensionHost`: entdeckt lokale Plugin-Tools im
  Workspace (AST-Analyse), Hook-Kette `before_tool`/`after_tool`,
  permission-gated.
- **integrations/lsp.py** – LSP over stdio (Diagnostics/Definition/
  References/Symbols), Standard-Specs + Custom aus `lsp.json`, Lazy-Singletons.
- **integrations/mcp.py** – MCP (JSON-RPC over stdio), Server aus
  `.codelite/mcp.json`, Lazy-Singletons.

### `codelite/db/` – SQLite-Store

- WAL, frische Connection pro Operation (Thread-freundlich). History =
  Responses-API-Items, direkt als `input` weiterverwendbar. Schema:
  `conversations` (Metadata + Migrations-Spalten: Tokens, Compaction,
  `source_id`, `reasoning_effort`, `fast_mode`, `flex_mode`), `items`
  (payload JSON, created_at, meta; Index `(conversation_id, id)`),
  `app_state` (key/value). `_apply_migrations()` per `ALTER TABLE`.
- Operationen: `create/get/list/update/delete_conversation`,
  `import_conversation` (idempotent via `source_id`), `record_usage`
  (context ersetzt, total akkumuliert), `save_compaction`,
  `append_items`, `load_items`, `load_entries(limit, before_id)` mit
  `_extend_page_to_closed_calls` (Seiten verlängern, bis jeder offene
  `function_call` geschlossen ist), `has_items_before`, `count_items`.

### `codelite/browser/` + `codelite/screenshot.py`

- **host.py** – Child-Prozess mit **einem sichtbaren 1×1-Fenster** (nicht
  `hidden=True`, nicht offscreen — ausführlich kommentierte Stolpersteine:
  pywebview-GTK feuert `_pywebviewready` nie, `hide()` setzt
  `document.hidden=true` und pausiert rAF-Loops, offscreen scheitert auf
  manchen GPU-Stacks). `BrowserSession` (navigate/snapshot mit
  `data-cl-ref`-Refs, max 400 Elemente, click/fill/evaluate/screenshot/
  reset), JSON-Lines über stdin/stdout, `dispatch()` wirft nie.
- **client.py** – `BrowserClient`: Lazy-Start (gefrorren → eigenes Binary
  mit `--browser-host`), `call()` serialized, Timeout aus Settings,
  Desync-Schutz bei Late-Antworten, Singleton `get_client()/close_client()`.
- **screenshot.py** – WebKitGTK-only: 4.1 → 4.0-Fallback; greift auf das
  echte `WebKit2.WebView` zu, Snapshot per `GLib.idle_add` auf dem
  GTK-Main-Loop-Thread. Geteilt zwischen `--screenshot` und dem
  `screenshot`-Action des Hidden-Browsers.

### Weitere Module

- **importer.py** – Codex-CLI-Import aus `~/.codex/sessions`
  (`rollout-*.jsonl`). Verschlüsselte `reasoning`-Items und Codex-Tool-Calls
  werden **nicht** als replaybare Items importiert — Tool-Call+Output wird
  als fenced Block in die Assistant-Message gefaltet. Idempotent via
  `source_id`.
- **remote.py** – Remote-Control: Tunnel-Binary laden/starten, Passwort,
  Cookie-Login (`REMOTE_COOKIE`, 12 h, Secure/HttpOnly/Strict),
  `is_remote_host` für das Server-Gate.
- **updater.py** – GitHub-Release-Vergleich, Asset-Download mit
  SHA-256-Check, Installation nach Prozessende.

### tests/ + packaging/ + CI

- **tests/** – `test_agent_request.py` (Wire-Format), `test_agent_extensions.py`
  (Plugin-Tools/Hooks), `test_browser.py` (Fake-Window-Stubs), `test_config.py`,
  `test_importer.py`, `test_pollinations.py`, `test_project_intelligence.py`
  (LSP/MCP/Skills mit Fake-Servern in `fixtures/`), `test_remote.py`,
  `test_settings.py`, `test_store_pagination.py`, `test_screenshot.py`,
  `test_updater.py`, `test_uploads.py`, `test_web.py` (SSRF-Schutz).
- **packaging/build-pyz.sh** – zipapp; deps per `pip --target --no-compile`
  (dist-info bleibt — Werkzeug braucht importlib.metadata).
- **packaging/build-deb.sh** – `/opt/code-lite/code-lite.pyz`,
  `/usr/bin/code-lite`-Wrapper, Desktop-File; **Binary heißt `code-lite`**
  (Kollision mit der C++-IDE `codelite`); `Depends: python3, python3-gi,
  gir1.2-webkit2-4.1 | 4.0`.
- **packaging/windows/codelite.iss** – Inno-Setup (per-user, x64), wrappt
  `dist/CodeLite.exe` (PyInstaller `--onefile --windowed`,
  `--collect-all webview clr_loader pythonnet` — fehlen sonst, crashen
  unverfangbar in der CLR).
- **.github/workflows/release.yml** – `version`-Job (Release nur wenn
  `VERSION` geändert wurde) → `linux` (.pyz + Smoke-Test mit **festen
  Asset-Pfaden** `/static/js/01-core.js`, `15-bootstrap.js`, `01-base.css`,
  `08-responsive.css` — beim Umbenennen mitziehen; .deb + lintian) →
  `windows` (PyInstaller + ISCC) → `release` (Artifacts +
  `SHA256SUMS.txt`, `gh release create`).

## Frontend-Architektur (kein Build-Step!)

- `templates/index.html` ist dünn und zieht Partials aus
  `templates/partials/` (sidebar, topbar, chat, details, dialogs, auth,
  settings) per Jinja-`include`.
- `static/js/01-core.js` … `15-bootstrap.js`: **klassische Scripts in
  numerischer Reihenfolge**, die sich absichtlich einen globalen Scope
  teilen. Regeln:
  - keine doppelten Top-Level-Namen über Dateien hinweg,
  - Reihenfolge in `index.html` ist die Ladeordnung (`init()` erst in
    `15-bootstrap.js`),
  - DOM-Referenzen zentral in `el` (`01-core.js`), Zustand in `state`.
- `static/css/01-base.css` … `08-responsive.css`: gleiche Reihenfolge-Logik,
  Design-Tokens liegen in `01-base.css`.
- Verpackung packt `codelite/app/templates` + `static` komplett ein (zipapp
  entpackt sie nach `_unpack_zipapp_assets()`); PyInstaller macht das über
  `--add-data`. Neue Assets müssen unter diesen Pfaden bleiben.

## Design-Eigenständigkeit (wichtig, rechtlich relevant)

Die Oberfläche ist **bewusst keine Kopie** eines fremden Produkts. Früher
wurde die Palette aus Screenshots der Claude-Code-Desktop-App gepixelt; das
wurde rückgängig gemacht und darf nicht wiederholt werden.

- **Palette (Tokens in `01-base.css`), Code-Lite-eigen:** kühle
  Graphit-Flächen mit Blaustich (`--bg #16181d`, Sidebar `#1b1e24`,
  Composer `#232732`, Borders `#2a2e38`/`#3a4050`, Text `#e9ecf1` /
  `#9aa3b2` / `#7d8595`), **Teal-Akzent** `#2fa88f` (auch Icon), Amber
  `#c9932e` (Manual-/Plan-Modus), Indigo `#4d8af0` (Context-Ring/Toggles),
  Diff-Grün `#3fbf50` / Rot `#e5534b`. Dark-only.
- **Marken-Zeichen:** kein Sparkle/✳. Das Footer-Mark und der
  Empty-State sind ein **Ring mit Punkt** (SVG in `06-tools.js` bzw.
  `templates/partials/chat.html`); das App-Icon ist der Prompt-Chevron auf
  Teal (`assets/icon.svg`, `icon.png`/`icon.ico` daraus generiert – bei
  Palette-Änderungen regenerieren).
- **Keine Screenshots fremder Produkte als Referenz verwenden.** Der
  (gitignored) Ordner `reference/claude-code-desktop/` ist veraltet: nur
  noch für funktionale Vergleiche ansehen, **niemals Farben, Glyphen oder
  Motive daraus übernehmen**, und bei Gelegenheit löschen. UI-Muster
  (Tool-Zeilen, Footer, Settings-Zweispalter) sind allgemeine
  Funktionsmuster, keine Markenmerkmale – sie bleiben, aber in eigener
  Gestaltung.
- Code-Kommentare nennen fremde Produkte nicht mehr („the reference
  desktop agent" als neutrale Formulierung); so schreiben, bleiben.
- **Keine erfundenen UI-Daten** (z. B. keine Diffstat-Badges `+N -M`, weil
  die Tools keine Diff-Statistik liefern). Fehlende Features nachrüsten,
  nicht simulieren.

## Verifizierungs-Workflow (vor jeder Übergabe)

1. `node --check` über jede geänderte JS-Datei (`static/js/*.js`).
2. `pytest tests -q` – pytest steckt in `/tmp/opencode/venv`
   (`python3 -m venv --system-site-packages /tmp/opencode/venv && pip install
   pytest`); das System-Python hat kein pytest.
3. Visuell: `python3 run.py --screenshot <pfad.png> [--data-dir … --port …]`
   – öffnet das Fenster, wartet auf den Ready-Marker
   (`document.body.dataset.ready` aus `15-bootstrap.js`), speichert PNG,
   beendet sich. Nur Linux/WebKitGTK (4.1, Fallback 4.0).
4. Dialoge/Interaktion: ad-hoc-Muster mit `serve_in_background()` +
   `webview.create_window(...)` + `capture_window_png()` (siehe frühere
   Checks), oder den **Hidden-Browser** (`BrowserClient`) für DOM-/JS-Checks
   ohne Sichtbarkeit (navigate + `evaluate`).
5. Immer mit **Temp-Datenverzeichnis** arbeiten:
   `--data-dir /tmp/opencode/<name> --port 105xx`. Nie das echte
   Nutzer-Verzeichnis (`~/.local/share/codelite`) anfassen – dort liegen
   Anmeldedaten und private Chats.

## Bekannte Stolpersteine

- **pywebview 5.0.x** (Debian) kennt `webview.start(icon=…)` nicht → in
  `app/window.py` wird der Parameter nur übergeben, wenn die Signatur ihn
  hat; sonst stirbt der Start mit TypeError, bevor ein Fenster offen ist.
- **Versteckte Fenster rendern nicht**: pywebview + WebKitGTK brauchen ein
  sichtbares (wenn auch 1×1) Window; `hidden=True` oder offscreen killt
  `_pywebviewready` bzw. `document.hidden` pausiert rAF-Loops. Details im
  Kommentar in `browser/host.py` – dort auch: der GTK-Snapshot muss auf den
  GTK-Main-Loop-Thread (`GLib.idle_add`).
- **`[hidden]` vs. Author-CSS**: eine eigene `display`-Regel (z. B.
  `display: flex` auf `.settings-row`, `.remote-actions-row`) schlägt die
  UA-Regel `[hidden]{display:none}`. Immer explizit
  `<selector>[hidden] { display: none; }` dazuschreiben (Muster existiert
  für `.overlay`, `.settings-row`, `.remote-actions-row`, …).
- **Umgebung mit Display**: „headless“-Tests können trotzdem echte Fenster
  öffnen. `tests/test_browser.py` stubt deshalb den Browser-Client
  (`_no_browser(monkeypatch)`) – sonst startet der Hidden-Browser und lädt
  die Test-URL sichtbar. Neue Tool-Tests genauso stubben.
- **Windows**: WebView2-Runtime muss vorhanden sein; pywebview startet sonst
  mit einer CLR-Exception, die nicht fangbar ist (`app/window.py` prüft die
  Registry vorab).
- **Namenskollision** `codelite` (C++-IDE) → das DEB/Binary heißt
  `code-lite`.
- CI-Smoke-Test (`release.yml`) curlt feste Asset-Pfade
  (`/static/js/01-core.js`, `15-bootstrap.js`, `01-base.css`,
  `08-responsive.css`) – beim Umbenennen von Assets mitziehen.
- `reference/` und `screenshot.png` sind gitignored; keine Screenshots oder
  Referenzbilder committen.

## Offen / vereinbart

- Light-Theme (helle Variante der eigenen Palette) ist
  noch nicht eingebaut; App ist bewusst Dark-only.
- Optional nachrüsten: Diff-Statistik in `tools/files.py` schreiben
  (Write/Edit-Output), dann können die Tool-Zeilen `+N -M`-Badges
  zeigen (aktuell bewusst weggelassen).

## Erledigt (Muster, auf dem spätere Änderungen aufbauen)

- **Transcript-Paginierung**: Beim Öffnen eines Chats kommt nur die neueste
  Seite (`?limit=`, `TRANSCRIPT_PAGE_SIZE` in `08-conversations.js`) plus
  `has_more`; ältere Seiten prependet `maybeLoadOlderEntries` beim Scrollen
  an den Top (`store.load_entries(limit, before_id)`, Cursor = kleinste
  gerenderte Item-ID, Index `(conversation_id, id)` deckt das ab). Seiten
  werden im Store über das Fenster hinaus erweitert, bis jeder
  `function_call` durch sein `function_call_output` geschlossen ist;
  Overlap dedupliziert das Frontend über `state.entryIds`. Der neueste Chat
  wird beim Start weiterhin geöffnet, aber **ohne `await`** (UI sofort).
- **Tool-Zeilen im Referenz-Stil**: Pfad-Chip **ohne Hintergrundbox** (mono
  inline, `#c9c9c9`), Zeilentext heller (`#bdbdbd`, 13.5px), Sub-Zeile
  `#9a9a9a` 12.5px, Stack-Abstand 4px. Laufende Activity sitzt in einer
  umrandeten Karte – rein per CSS `.tool-stack:has(.tool-call.running)`
  (WebKitGTK 4.1 unterstützt `:has`, verifiziert; ohne Support fällt es
  rahmenlos zurück).

- **Settings-Dialog**: zweispaltig, Suche, Hairline-Zeilen.
  Die Behaviour-Gruppen aus `codelite/settings.GROUPS` sind **eigene
  Nav-Einträge/Panels** (dynamisch unter der Nav-Gruppe „Agent“,
  `data-behaviour-panel`, Tab-Namen `group:<key>`). Regeln dafür:
  - `selectSettingsTab` und der Settings-Suchfilter arbeiten mit
    **Live-Queries** (`document.querySelectorAll("[data-settings-tab]")`),
    nicht mit gecachten Listen – die Panels kommen und gehen bei jedem
    Re-Render.
  - Save/Reset pro Panel via Delegation auf `.settings-content`
    (`data-behaviour-save`/`data-behaviour-reset`), wirkt global über alle
    Gruppen; der Status wird nach dem Re-Render in das sichtbare Panel
    geschrieben (`renderBehaviour` stellt die aktive Gruppe wieder her).

## Arbeitsweisen, die sich bewährt haben

- Kleine, exakt verifizierte Schritte statt großer Würfe; nach jedem Schritt
  Screenshot/Interaktionstest.
- Nebeneffekte ehrlich benennen (z. B. Kompatibilitätsfix für pywebview
  5.0 beim Screenshot-Modus) und im Code mit Begründung kommentieren – das
  Repo kommentiert das *Warum* ausführlich, dem Stil folgen.
- UI-Deviations vom Referenz-Produkt explizit machen und begründen (fehlende
  Features nicht fake-nachbauen).
- Scratch-Space: `/tmp/opencode/` (vorherig erstellt, erlaubt).

## Git-Ablauf

1. `git pull --rebase`
2. Arbeiten Arbeiten Arbeiten
3. `git add .`
4. `git commit -m "Short message"`
5. `git push`
