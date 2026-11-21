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

"""Owns the child process that runs the hidden browser window.

One process for the whole app, started lazily on the first call and reused
after that -- the point of a hidden window is that opening it is not free, so
nothing should pay that cost until something actually asks for the browser.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .. import settings

#: How long a request may go unanswered before this is treated as a hang.
#: Falls back to this only if the setting cannot be read (e.g. during a
#: schema migration); the live value normally comes from settings.active().
DEFAULT_TIMEOUT_SECONDS = 30.0
DOM_ACTION_TIMEOUT_SECONDS = 2.0
DOM_ACTIONS = frozenset({"evaluate", "snapshot", "click", "fill"})


class BrowserError(RuntimeError):
    """Anything that went wrong talking to the browser process."""


def _import_root() -> Path:
    """Whatever has to be on ``sys.path`` for ``import codelite`` to work.

    Mirrors the zipapp detection in :mod:`codelite.app.server`: inside a
    ``.pyz`` this module's ``__file__`` looks like
    ``/path/app.pyz/codelite/browser/client.py``, and the first parent that is
    an actual file *is* the archive -- a valid ``sys.path`` entry on its own
    via ``zipimport``. In a plain checkout there is no such parent, so this
    falls back to the project root three levels up.
    """
    module = Path(__file__).resolve()
    archive = next((parent for parent in module.parents if parent.is_file()), None)
    if archive is not None:
        return archive
    return module.parents[2]


class BrowserClient:
    """Talks JSON-lines to one ``codelite.browser.host`` child process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._commands: queue.SimpleQueue[str | None] = queue.SimpleQueue()
        self._responses: queue.SimpleQueue[str | None] = queue.SimpleQueue()
        self._errors: deque[str] = deque(maxlen=8)
        self._next_id = 0

    def _child_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            # A frozen build's `sys.executable` is the app's own binary, not a
            # general Python interpreter -- there is nothing to hand `-c` to,
            # and no on-disk `codelite` package for a system Python to import
            # either. Re-exec the binary itself instead: `--browser-host` (see
            # codelite.__main__) makes it run codelite.browser.host.main()
            # directly rather than the normal app, the same trick Electron
            # apps use for a worker process with no separate runtime to spawn.
            return [sys.executable, "--browser-host"]
        root = _import_root()
        bootstrap = (
            f"import sys; sys.path.insert(0, {str(root)!r}); "
            "from codelite.browser.host import main; main()"
        )
        return [sys.executable, "-c", bootstrap]

    def _ensure_started(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self._stop_process()
        command = self._child_command()
        try:
            self._proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                start_new_session=os.name == "posix",
            )
        except OSError as error:
            raise BrowserError(f"Could not start the browser process: {error}") from error
        self._commands = queue.SimpleQueue()
        self._responses = queue.SimpleQueue()
        self._errors = deque(maxlen=8)
        # Both pipe operations can block. Each helper owns its own queues, so
        # a worker from a timed-out process can't answer on the next one.
        threading.Thread(
            target=self._exchange, args=(self._proc, self._commands, self._responses), daemon=True,
        ).start()
        threading.Thread(
            target=self._drain_errors, args=(self._proc, self._errors), daemon=True,
        ).start()

    @staticmethod
    def _exchange(
        proc: subprocess.Popen[str],
        commands: queue.SimpleQueue[str | None],
        responses: queue.SimpleQueue[str | None],
    ) -> None:
        assert proc.stdin is not None and proc.stdout is not None
        try:
            with proc.stdin, proc.stdout:
                while (payload := commands.get()) is not None:
                    proc.stdin.write(payload + "\n")
                    proc.stdin.flush()
                    line = proc.stdout.readline()
                    if not line:
                        return
                    responses.put(line)
        except (OSError, ValueError):
            pass
        finally:
            responses.put(None)

    @staticmethod
    def _drain_errors(proc: subprocess.Popen[str], errors: deque[str]) -> None:
        assert proc.stderr is not None
        with proc.stderr:
            while line := proc.stderr.readline(1024):
                errors.append(line)

    def _stop_process(self) -> None:
        """Discard the helper and any renderer it started. Caller holds _lock."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        self._commands.put(None)
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            elif proc.poll() is None:
                proc.kill()
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass

    def _timeout(self) -> float:
        try:
            return float(settings.active("browser_timeout_seconds"))
        except Exception:  # noqa: BLE001 - a settings hiccup must not hang a call
            return DEFAULT_TIMEOUT_SECONDS

    def call(self, action: str, **params: Any) -> Any:
        """Send one command, wait for its matching response, return `result`.

        Serialized by a single lock: the protocol is one request in flight at
        a time, matching how the host reads one line, replies, then reads the
        next -- there is no request id multiplexing on the wire.
        """
        timeout = self._timeout()
        if action in DOM_ACTIONS:
            timeout = min(timeout, DOM_ACTION_TIMEOUT_SECONDS)
        deadline = time.monotonic() + timeout
        if not self._lock.acquire(timeout=timeout):
            raise BrowserError("The browser is busy with another action. Try again.")
        try:
            if time.monotonic() >= deadline:
                raise BrowserError("The browser is busy with another action. Try again.")
            self._ensure_started()
            self._next_id += 1
            request_id = self._next_id
            payload = json.dumps({"id": request_id, "action": action, **params})
            self._commands.put(payload)
            try:
                line = self._responses.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                self._stop_process()
                raise BrowserError(
                    f"Browser `{action}` timed out after {timeout:g} seconds. "
                    "The browser session was reset. Navigate to the page again."
                ) from None
            if line is None:
                self._stop_process()
                stderr = "".join(self._errors.copy()).strip()
                detail = f" {stderr}" if stderr else ""
                raise BrowserError(f"The browser process stopped responding.{detail}")
            try:
                response = json.loads(line)
            except ValueError as error:
                self._stop_process()
                raise BrowserError("The browser sent an unreadable response. Its session was reset.") from error
            if not isinstance(response, dict) or response.get("id") != request_id:
                self._stop_process()
                raise BrowserError("The browser sent an unexpected response. Its session was reset.")
        finally:
            self._lock.release()

        if not response.get("ok"):
            raise BrowserError(response.get("error") or f"`{action}` failed.")
        return response.get("result")

    def close(self) -> None:
        with self._lock:
            proc = self._proc
            if proc is None:
                return
            if proc.poll() is None:
                self._commands.put(json.dumps({"id": 0, "action": "shutdown"}))
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
            self._stop_process()


_lock = threading.Lock()
_client: BrowserClient | None = None


def get_client() -> BrowserClient:
    global _client
    with _lock:
        if _client is None:
            _client = BrowserClient()
        return _client


def close_client() -> None:
    global _client
    with _lock:
        client, _client = _client, None
    if client is not None:
        client.close()


atexit.register(close_client)
