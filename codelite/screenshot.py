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

"""Capture a native pywebview window as a PNG.

pywebview exposes no screenshot call of its own, so on Linux this reaches into
its GTK backend for the underlying ``WebKit2.WebView`` and calls
``get_snapshot`` -- the officially supported WebKitGTK API, just one pywebview
does not surface. Both the app's ``--screenshot`` mode and the hidden browser's
``screenshot`` action use this module, so the platform-specific code exists in
exactly one place.

Like the hidden browser, this only works on Linux/WebKitGTK today: WebView2 and
WKWebView do have capture APIs, but neither is wired up here yet.
"""

from __future__ import annotations

import io
import sys
import threading
from pathlib import Path
from typing import Any, Protocol

DEFAULT_SCREENSHOT_NAME = "screenshot.png"
DEFAULT_TIMEOUT_SECONDS = 15.0


class ScreenshotError(RuntimeError):
    """The window could not be captured as a PNG."""


class WindowLike(Protocol):
    uid: str


def available() -> bool:
    """Whether this platform's webview can be captured at all.

    Checked before a long-running app start so a screenshot request on an
    unsupported platform fails immediately instead of after the window opened.
    """
    if not sys.platform.startswith("linux"):
        return False
    try:
        _require_webkit()
        from webview.platforms.gtk import BrowserView  # noqa: F401 - presence check only
    except Exception:  # noqa: BLE001 - availability is platform dependent
        return False
    return True


def _require_webkit() -> Any:
    """The WebKit2 namespace, trying 4.1 first and falling back to 4.0.

    That is the same fallback pywebview and the Debian package use, so a
    screenshot does not fail on a system where the app window itself works.
    """
    try:
        import gi
    except ImportError as error:
        raise ScreenshotError(f"PyGObject is not available: {error}") from error

    for version in ("4.1", "4.0"):
        try:
            gi.require_version("WebKit2", version)
            from gi.repository import WebKit2

            return WebKit2
        except (ValueError, ImportError):
            continue
    raise ScreenshotError("WebKitGTK is not available (tried WebKit2 4.1 and 4.0).")


def resolve_path(destination: Path) -> Path:
    """Turn what the user passed into the file that will actually be written.

    A directory means "put the default file name in there", so
    ``--screenshot ~/Desktop`` behaves the way people expect instead of
    failing with ``IsADirectoryError``.
    """
    path = destination.expanduser()
    if path.is_dir():
        path = path / DEFAULT_SCREENSHOT_NAME
    return path


def capture_window_png(
    window: WindowLike, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> bytes:
    """Snapshot one window's web content and return PNG bytes.

    Blocks the calling thread while WebKit renders its snapshot on the GTK
    main loop; callers must not already hold that loop (the app starts this
    from the thread ``webview.start`` runs its callback on, the hidden browser
    from the thread reading commands from stdin).
    """
    if not sys.platform.startswith("linux"):
        raise ScreenshotError(
            "Screenshots are only supported on Linux (WebKitGTK) for now."
        )

    WebKit2 = _require_webkit()
    try:
        from gi.repository import GLib  # noqa: PLC0415
        from webview.platforms.gtk import BrowserView  # noqa: PLC0415
    except Exception as error:  # noqa: BLE001 - pywebview's GTK backend may be absent
        raise ScreenshotError(
            f"WebKitGTK screenshot support is not available: {error}"
        ) from error

    # pywebview keeps its per-window GTK state in a private registry keyed by
    # the window's uid -- `window.gui` is the *platform module*, not the
    # instance. `.webview` on that instance is the actual WebKit2.WebView.
    instance = BrowserView.instances.get(getattr(window, "uid", None))
    webkit_widget: Any = getattr(instance, "webview", None) if instance is not None else None
    if webkit_widget is None:
        raise ScreenshotError("Could not reach the underlying WebKitGTK widget.")

    outcome: dict[str, Any] = {}
    done = threading.Event()

    def _finish(webview_widget: Any, task: Any, _user_data: Any = None) -> None:
        try:
            surface = webview_widget.get_snapshot_finish(task)
            buffer = io.BytesIO()
            surface.write_to_png(buffer)
            outcome["png"] = buffer.getvalue()
        except Exception as error:  # noqa: BLE001 - reported back, not raised on the GTK thread
            outcome["error"] = str(error)
        finally:
            done.set()

    def _start() -> bool:
        webkit_widget.get_snapshot(
            WebKit2.SnapshotRegion.FULL_DOCUMENT, WebKit2.SnapshotOptions.NONE, None, _finish
        )
        return False

    # WebKit2's snapshot call has to run on the GTK main-loop thread, which is
    # never the thread that asked for the screenshot.
    GLib.idle_add(_start)
    if not done.wait(timeout):
        raise ScreenshotError(
            f"Timed out after {timeout:.0f}s waiting for the screenshot."
        )
    if "error" in outcome:
        raise ScreenshotError(outcome["error"])
    return outcome["png"]
