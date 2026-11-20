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

"""Tests for the screenshot mode.

The capture itself needs a display and WebKitGTK, so that part is exercised
by hand. Everything around it -- platform detection, path handling, waiting
for the interface, error reporting, and the CLI flags -- is testable without
a window and is covered here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from codelite import screenshot
from codelite.__main__ import _parser
from codelite.app import window


class FakeWindow:
    def __init__(self) -> None:
        self.destroyed = False

    def destroy(self) -> None:
        self.destroyed = True


class FakePlatform:
    def __init__(self, platform: str) -> None:
        self.platform = platform


def test_capture_rejects_platforms_without_webkitgtk(monkeypatch) -> None:
    monkeypatch.setattr(screenshot, "sys", FakePlatform("win32"))
    with pytest.raises(screenshot.ScreenshotError, match="Linux"):
        screenshot.capture_window_png(object())


def test_available_is_false_off_linux(monkeypatch) -> None:
    monkeypatch.setattr(screenshot, "sys", FakePlatform("darwin"))
    assert screenshot.available() is False


def test_resolve_path_treats_a_directory_as_a_destination_folder(tmp_path: Path) -> None:
    assert screenshot.resolve_path(tmp_path) == tmp_path / screenshot.DEFAULT_SCREENSHOT_NAME


def test_resolve_path_keeps_an_explicit_filename(tmp_path: Path) -> None:
    target = tmp_path / "ui.png"
    assert screenshot.resolve_path(target) == target


def test_wait_for_app_ready_polls_until_the_marker(monkeypatch) -> None:
    answers = iter([False, False, True])
    polls: list[str] = []

    class Events:
        class loaded:
            @staticmethod
            def wait(_timeout: float) -> bool:
                return True

    class ReadyWindow:
        events = Events()

        def evaluate_js(self, script: str) -> Any:
            polls.append(script)
            return next(answers)

    window._wait_for_app_ready(ReadyWindow(), timeout=2)
    assert len(polls) == 3


def test_capture_when_ready_writes_the_png_and_closes_the_window(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(window, "_wait_for_app_ready", lambda _window, _timeout=15.0: None)
    monkeypatch.setattr(window, "capture_window_png", lambda _window: b"\x89PNG-fake")

    fake = FakeWindow()
    target = tmp_path / "shot.png"
    outcome = window._capture_when_ready(fake, target, delay=0)

    assert outcome == {"path": target}
    assert target.read_bytes() == b"\x89PNG-fake"
    assert fake.destroyed is True


def test_capture_when_ready_reports_errors_and_still_closes_the_window(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(window, "_wait_for_app_ready", lambda _window, _timeout=15.0: None)

    def fail(_window: Any) -> bytes:
        raise screenshot.ScreenshotError("no widget")

    monkeypatch.setattr(window, "capture_window_png", fail)

    fake = FakeWindow()
    outcome = window._capture_when_ready(fake, tmp_path / "shot.png", delay=0)

    assert isinstance(outcome["error"], screenshot.ScreenshotError)
    assert "path" not in outcome
    assert fake.destroyed is True


def test_screenshot_flag_without_a_path_uses_the_default_name() -> None:
    args = _parser().parse_args(["--screenshot"])
    assert args.screenshot == Path("screenshot.png")
    assert args.screenshot_delay == window.SCREENSHOT_DELAY_SECONDS


def test_screenshot_path_and_delay_are_parsed() -> None:
    args = _parser().parse_args(["--screenshot", "ui.png", "--screenshot-delay", "4.5"])
    assert args.screenshot == Path("ui.png")
    assert args.screenshot_delay == 4.5
