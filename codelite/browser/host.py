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

"""Runs in the child process: one hidden pywebview window, driven by JSON
commands read from stdin, one response per line written to stdout.

:func:`main` is the only part that actually needs pywebview and a display.
Everything else -- the JS that builds a snapshot, the request/response
framing, the dispatch table -- takes a window-*like* object (anything with
``load_url`` and ``evaluate_js``), so it can be tested with a fake one and
no window ever has to open for that.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
import threading
import time
from typing import Any, Callable, Protocol

from ..screenshot import available as screenshots_available
from ..screenshot import capture_window_png
from ..webview_assets import webview_script_files

logger = logging.getLogger(__name__)

BROWSER_VIEWPORT = (1280, 720)
_HISTORY_START_TIMEOUT = 0.5


class BrowserActionError(Exception):
    """One command failed. Caught by :func:`dispatch` and reported, not raised."""


class WindowLike(Protocol):
    def load_url(self, url: str) -> None: ...
    def evaluate_js(self, script: str) -> Any: ...


#: Elements worth exposing. Broad on purpose -- a heading or a label is not
#: clickable but is what makes the surrounding buttons make sense.
_SNAPSHOT_SELECTOR = (
    "a,button,input,select,textarea,[role],[onclick],[contenteditable],"
    "h1,h2,h3,summary,label"
)

#: Hard cap independent of the caller's `max_chars`: without one, a page with
#: thousands of matching elements would spend a full second building a
#: snapshot nobody could read anyway.
_MAX_SNAPSHOT_ELEMENTS = 400

_SNAPSHOT_JS = f"""
(() => {{
  const MAX_ELEMENTS = {_MAX_SNAPSHOT_ELEMENTS};
  if (!window.__clRefCounter) window.__clRefCounter = 0;
  const nodes = document.querySelectorAll({_SNAPSHOT_SELECTOR!r});
  const lines = [];
  for (const el of nodes) {{
    if (lines.length >= MAX_ELEMENTS) break;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue;
    let ref = el.getAttribute('data-cl-ref');
    if (!ref) {{
      ref = 'ref_' + (++window.__clRefCounter);
      el.setAttribute('data-cl-ref', ref);
    }}
    const tag = el.tagName.toLowerCase();
    const type = el.getAttribute('type') || '';
    const role = el.getAttribute('role') || '';
    const raw = el.innerText || el.value || el.getAttribute('placeholder') ||
      el.getAttribute('aria-label') || '';
    const text = raw.trim().replace(/\\s+/g, ' ').slice(0, 80);
    const bits = [tag + (type ? ':' + type : '')];
    if (role) bits.push('role=' + role);
    lines.push('[' + ref + '] ' + bits.join(' ') + ' "' + text + '"');
  }}
  return {{url: location.href, title: document.title, elements: lines}};
}})()
"""


def _parse_js_result(raw: Any) -> dict[str, Any]:
    """Normalize what ``evaluate_js`` handed back.

    pywebview already turns a JS object into a Python dict on most backends,
    but a script that returns a *string* (some evaluate_js implementations
    stringify first) needs one more `json.loads`. Either way this must not
    raise on a stray non-JSON string -- that string is the error to report.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {"ok": False, "error": raw}
        return parsed if isinstance(parsed, dict) else {"ok": False, "error": raw}
    return {"ok": False, "error": f"Unexpected script result: {raw!r}"}


def _unsupported_screenshot(_window: WindowLike, *, full_page: bool) -> bytes:
    raise BrowserActionError(
        "Screenshots are not available on this platform's webview backend yet."
    )


class BrowserSession:
    """One page, one hidden window. Actions are re-entered from the read loop
    thread, never concurrently, so no locking of its own is needed."""

    def __init__(
        self,
        window: WindowLike,
        screenshot_fn: Callable[..., bytes] | None = None,
        evaluate_fn: Callable[[str], Any] | None = None,
        history_fn: Callable[[str, float], dict[str, Any] | None] | None = None,
        script_fn: Callable[[str], Any] | None = None,
    ) -> None:
        self.window = window
        self._screenshot_fn = screenshot_fn or _unsupported_screenshot
        self._evaluate_fn = evaluate_fn or window.evaluate_js
        self._script_fn = script_fn or self._evaluate_fn
        self._history_fn = history_fn

    def navigate(self, url: str, timeout: float = 20.0) -> dict[str, Any]:
        target = url.strip()
        if not target:
            raise BrowserActionError("`url` must not be empty.")
        if target in ("back", "forward"):
            if self._history_fn is not None:
                result = self._history_fn(target, timeout)
                if result is not None:
                    return result
            return self._navigate_history(target, timeout)

        # On WebKitGTK, evaluating JavaScript while a navigation is being
        # committed can leave pywebview's synchronous bridge waiting forever.
        # Its loaded event is driven by the native backend and remains bounded.
        events = getattr(self.window, "events", None)
        loaded = getattr(events, "loaded", None)
        if loaded is not None:
            loaded.clear()
        self.window.load_url(target)
        if loaded is not None:
            if not loaded.wait(timeout):
                return {"url": target, "title": ""}
        else:
            self._wait_for_load(timeout)
        return {
            "url": self._evaluate_fn("location.href"),
            "title": self._evaluate_fn("document.title"),
        }

    def _navigate_history(self, direction: str, timeout: float) -> dict[str, Any]:
        # Same-document traversal fires popstate instead of loaded. A new
        # document also loses the marker, including when the URL is identical.
        # Schedule the traversal after this call has returned its JS result.
        marker = json.dumps(str(time.monotonic_ns()))
        start_timeout_ms = int(_HISTORY_START_TIMEOUT * 1000)
        self._evaluate_fn(f"""(() => {{
            window.__clHistoryPending = {marker};
            const complete = () => {{
                if (window.__clHistoryPending === {marker}) delete window.__clHistoryPending;
            }};
            window.addEventListener("popstate", complete, {{once: true}});
            setTimeout(() => window.removeEventListener("popstate", complete), {start_timeout_ms});
            setTimeout(() => history.{direction}(), 0);
        }})()""")
        start = time.monotonic()
        deadline = start + max(0.0, timeout)
        while time.monotonic() < deadline:
            result = _parse_js_result(self._evaluate_fn(f"""({{
                moved: window.__clHistoryPending !== {marker},
                ready: document.readyState === "complete",
                url: location.href,
                title: document.title
            }})"""))
            if result.get("moved") and result.get("ready"):
                return {"url": result["url"], "title": result["title"]}
            if not result.get("moved") and time.monotonic() - start >= _HISTORY_START_TIMEOUT:
                break
            time.sleep(0.025)
        raise BrowserActionError(f"Browser did not go {direction}.")

    def _wait_for_load(self, timeout: float) -> None:
        """Poll for `readyState === "complete"`, but never treat a slow page
        as a failure -- a stream that never finishes loading can still have
        delivered exactly the content the caller wanted."""
        deadline = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < deadline:
            try:
                if self._evaluate_fn("document.readyState") == "complete":
                    return
            except Exception:  # noqa: BLE001 - the page may not be ready to eval yet
                pass
            time.sleep(0.15)

    def snapshot(self, max_chars: int = 20_000) -> dict[str, Any]:
        data = _parse_js_result(self._evaluate_fn(_SNAPSHOT_JS))
        elements = data.get("elements") or []
        kept: list[str] = []
        used = 0
        for index, line in enumerate(elements):
            used += len(line) + 1
            if used > max_chars:
                kept.append(f"… [{len(elements) - index} more elements omitted]")
                break
            kept.append(line)
        return {"url": data.get("url", ""), "title": data.get("title", ""), "elements": kept}

    def click(self, ref: str) -> None:
        script = f"""
(() => {{
  const target = {json.dumps(ref)};
  const el = [...document.querySelectorAll('[data-cl-ref]')]
    .find((e) => e.getAttribute('data-cl-ref') === target);
  if (!el) return {{ok: false, error: 'No element with that reference. Call snapshot again.'}};
  el.scrollIntoView({{block: 'center'}});
  el.click();
  return {{ok: true}};
}})()
"""
        result = _parse_js_result(self._evaluate_fn(script))
        if not result.get("ok"):
            raise BrowserActionError(result.get("error") or f"Could not click {ref}.")

    def fill(self, ref: str, value: str) -> None:
        script = f"""
(() => {{
  const target = {json.dumps(ref)};
  const value = {json.dumps(value)};
  const el = [...document.querySelectorAll('[data-cl-ref]')]
    .find((e) => e.getAttribute('data-cl-ref') === target);
  if (!el) return {{ok: false, error: 'No element with that reference. Call snapshot again.'}};
  el.scrollIntoView({{block: 'center'}});
  el.focus();
  if (el.isContentEditable) {{
    el.innerText = value;
  }} else {{
    el.value = value;
  }}
  el.dispatchEvent(new Event('input', {{bubbles: true}}));
  el.dispatchEvent(new Event('change', {{bubbles: true}}));
  return {{ok: true}};
}})()
"""
        result = _parse_js_result(self._evaluate_fn(script))
        if not result.get("ok"):
            raise BrowserActionError(result.get("error") or f"Could not fill {ref}.")

    def evaluate(self, script: str) -> Any:
        return self._script_fn(script)

    def screenshot(self, *, full_page: bool = False) -> dict[str, str]:
        png_bytes = self._screenshot_fn(self.window, full_page=full_page)
        return {"png_base64": base64.b64encode(png_bytes).decode("ascii")}


_ACTIONS: dict[str, Callable[[BrowserSession, dict[str, Any]], Any]] = {
    "navigate": lambda s, c: s.navigate(str(c.get("url", "")), float(c.get("timeout", 20))),
    "snapshot": lambda s, c: s.snapshot(int(c.get("max_chars", 20_000))),
    "click": lambda s, c: (s.click(str(c.get("ref", ""))), {"clicked": c.get("ref", "")})[1],
    "fill": lambda s, c: (
        s.fill(str(c.get("ref", "")), str(c.get("value", ""))),
        {"filled": c.get("ref", "")},
    )[1],
    "evaluate": lambda s, c: s.evaluate(str(c.get("script", ""))),
    "screenshot": lambda s, c: s.screenshot(full_page=c.get("full_page") is True),
    "shutdown": lambda s, c: {"stopped": True},
}


def dispatch(session: BrowserSession, command: dict[str, Any]) -> dict[str, Any]:
    """One request in, one response out. Never raises: every failure --
    a bad action, a page error, a crashed script -- has to reach the client
    as a normal reply, or the client would hang waiting for a line that
    never comes."""
    request_id = command.get("id")
    action = command.get("action")
    handler = _ACTIONS.get(str(action))
    if handler is None:
        return {"id": request_id, "ok": False, "error": f"Unknown browser action `{action}`."}
    try:
        result = handler(session, command)
    except Exception as error:  # noqa: BLE001 - see docstring
        return {"id": request_id, "ok": False, "error": str(error)}
    return {"id": request_id, "ok": True, "result": result}


def serve(session: BrowserSession, stdin: Any = None, stdout: Any = None) -> None:
    """Read one JSON command per line until `shutdown` or the pipe closes."""
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            command = json.loads(line)
        except ValueError:
            continue
        if not isinstance(command, dict):
            continue
        response = dispatch(session, command)
        stdout.write(json.dumps(response) + "\n")
        stdout.flush()
        if command.get("action") == "shutdown":
            destroy = getattr(session.window, "destroy", None)
            if destroy is not None:
                destroy()
            return


def _screenshot_backend() -> Callable[..., bytes] | None:
    """The platform screenshot call, or None where there is none.

    The WebKitGTK implementation lives in :mod:`codelite.screenshot` because
    the app's own ``--screenshot`` mode uses the same call.
    """
    return capture_window_png if screenshots_available() else None


def _gtk_webview(window: Any) -> Any | None:
    gui = window.gui
    if getattr(gui, "renderer", None) == "gtkwebkit2":
        return gui.BrowserView.instances[window.uid].webview
    return None


def _evaluate_browser_js(window: Any, script: str, *, format_result: bool = False) -> Any:
    widget = _gtk_webview(window)
    if widget is not None:
        if format_result:
            return _evaluate_gtk_user_script(widget, script)
        return _evaluate_gtk_js(widget, script)
    return window.evaluate_js(script)


def _navigate_browser_history(window: Any, direction: str, timeout: float) -> dict[str, Any] | None:
    widget = _gtk_webview(window)
    if widget is None:
        return None

    from gi.repository import GLib

    done = threading.Event()
    result: dict[str, Any] | None = None
    error: Exception | None = None

    def start() -> bool:
        nonlocal error
        if done.is_set():
            return False
        try:
            history = widget.get_back_forward_list()
            previous = history.get_current_item()
            get_destination = history.get_back_item if direction == "back" else history.get_forward_item
            if get_destination() is None:
                raise BrowserActionError(f"No browser history entry to go {direction} to.")
            go = widget.go_back if direction == "back" else widget.go_forward
            go()
        except Exception as failure:
            error = failure
            done.set()
            return False

        # The list changes for both same-document and full-page navigation.
        # Only the latter needs to wait for WebKit to finish loading.
        start_deadline = time.monotonic() + _HISTORY_START_TIMEOUT

        def check() -> bool:
            nonlocal result, error
            if done.is_set():
                return False
            try:
                if widget.is_loading():
                    return True
                if history.get_current_item() == previous:
                    if time.monotonic() < start_deadline:
                        return True
                    raise BrowserActionError(f"Browser did not go {direction}.")
                result = {"url": widget.get_uri(), "title": widget.get_title() or ""}
            except Exception as failure:
                error = failure
            done.set()
            return False

        GLib.timeout_add(20, check)
        return False

    GLib.idle_add(start)
    if not done.wait(max(0.0, timeout)):
        done.set()
        raise BrowserActionError(f"Timed out going {direction} in browser history.")
    if error is not None:
        raise BrowserActionError(str(error)) from error
    return result


def _evaluate_gtk_user_script(widget: Any, script: str) -> Any:
    # Keep support for returning DOM nodes and other values WebKit cannot
    # marshal directly. Check eval before running any user code, so falling
    # back to native evaluation on a strict page cannot repeat its effects.
    wrapped = f"""
(() => {{
    try {{
        (0, eval)("0");
    }} catch (failure) {{
        if (failure instanceof EvalError) return {{evalBlocked: true}};
        throw failure;
    }}
    try {{
        const value = (0, eval)({json.dumps(script)});
        const ancestors = [];
        return JSON.parse(JSON.stringify({{value: value ?? null}}, function (_, item) {{
            if (item === globalThis) return 'Window';
            if (item instanceof Node) return item.outerHTML ?? item.textContent;
            if (item instanceof NodeList || item instanceof HTMLCollection) return Array.from(item);
            if (typeof item === 'bigint') return item.toString();
            if (item !== null && typeof item === 'object') {{
                while (ancestors.length && ancestors[ancestors.length - 1] !== this) ancestors.pop();
                if (ancestors.includes(item)) return '[Circular Reference]';
                ancestors.push(item);
            }}
            return item;
        }}));
    }} catch (failure) {{
        return {{error: String(failure)}};
    }}
}})()
"""
    response = _evaluate_gtk_js(widget, wrapped)
    if response.get("evalBlocked"):
        return _evaluate_gtk_js(widget, script)
    if "error" in response:
        raise BrowserActionError(response["error"])
    return response.get("value")


def _evaluate_gtk_js(widget: Any, script: str) -> Any:
    """Submit source directly to WebKit without requiring a page JS bridge."""
    from gi.repository import GLib

    modern_api = hasattr(widget, "evaluate_javascript")
    done = threading.Event()
    value: str | None = None
    error: Exception | None = None

    def finish(widget: Any, task: Any, _data: Any = None) -> None:
        nonlocal value, error
        try:
            result = (
                widget.evaluate_javascript_finish(task) if modern_api
                else widget.run_javascript_finish(task).get_js_value()
            )
            if result is not None and not result.is_undefined():
                value = result.to_json(0)
                if value is None:
                    raise BrowserActionError("Return a JSON-compatible JavaScript value.")
        except Exception as failure:  # noqa: BLE001 - the caller must be woken up
            error = failure
        finally:
            done.set()

    def start() -> bool:
        nonlocal error
        try:
            if modern_api:
                widget.evaluate_javascript(script, -1, None, None, None, finish)
            else:
                widget.run_javascript(script, None, finish)
        except Exception as failure:  # noqa: BLE001 - the caller must be woken up
            error = failure
            done.set()
        return False

    GLib.idle_add(start)
    done.wait()  # The parent enforces the per-action deadline if WebKit stalls.
    if error is not None:
        raise BrowserActionError(f"WebKit could not evaluate JavaScript: {error}") from None
    return json.loads(value) if value is not None else None


def _prepare_gtk_browser() -> None:
    """Configure the child process's GTK window before pywebview maps it."""
    try:
        from webview.platforms.gtk import BrowserView
    except (ImportError, ValueError):
        return  # pywebview can choose a different backend.

    show = BrowserView.show

    def show_in_background(view: Any) -> None:
        window = view.window
        window.set_decorated(False)
        window.set_accept_focus(False)
        window.set_focus_on_map(False)
        window.set_skip_taskbar_hint(True)
        window.set_skip_pager_hint(True)
        window.resize(*BROWSER_VIEWPORT)
        composited = window.is_composited()
        x11 = window.get_display().__gtype__.name == "GdkX11Display"
        if not x11:
            visual = window.get_screen().get_rgba_visual()
            if visual is not None:
                window.set_visual(visual)
            window.props.opacity = 0.0

        # The invisible window must not intercept clicks on the main app.
        window.realize()
        surface = window.get_window()
        if x11:
            if composited:
                # Set opacity on the X11 surface, including the WebKit layers.
                surface.set_opacity(0.0)
            else:
                # Clip the native window instead of shrinking the WebKit viewport.
                try:
                    import cairo
                except ImportError:
                    window.set_size_request(1, 1)
                    window.resize(1, 1)
                else:
                    surface.shape_combine_region(cairo.Region(cairo.RectangleInt(0, 0, 1, 1)), 0, 0)
        surface.set_pass_through(True)
        show(view)

    # The window workaround only applies in this child process.
    BrowserView.show = show_in_background


def main() -> None:  # pragma: no cover - exercised manually, needs a real display
    import webview  # local import: only the child process needs this

    # Keep the surface mapped and on-screen so GTK emits pywebview's ready
    # event and WebKit keeps document.hidden false. Calling hide() pauses
    # requestAnimationFrame, and off-screen placement can stall some drivers.
    if sys.platform.startswith("linux"):
        _prepare_gtk_browser()
    window = webview.create_window(
        "codelite-browser", "about:blank", width=1, height=1, x=0, y=0
    )
    session = BrowserSession(
        window, screenshot_fn=_screenshot_backend(),
        evaluate_fn=lambda script: _evaluate_browser_js(window, script),
        script_fn=lambda script: _evaluate_browser_js(window, script, format_result=True),
        history_fn=lambda direction, timeout: _navigate_browser_history(window, direction, timeout),
    )

    def serve_when_loaded() -> None:
        # webview.start() launches its callback just before creating the native
        # window. Wait for about:blank to finish so its late loaded event cannot
        # be mistaken for the first requested navigation.
        window.events.loaded.wait(20)
        serve(session)

    with webview_script_files(webview):
        webview.start(serve_when_loaded, debug=False)


if __name__ == "__main__":  # pragma: no cover
    main()
