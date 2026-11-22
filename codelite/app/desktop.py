"""User-owned Computer Use sessions for the Linux X11 desktop."""

from __future__ import annotations

import base64
import ctypes
import math
import os
import re
import sys
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from time import monotonic
from typing import Any, Callable

from ..tools.base import ToolError
from .desktop_pointer import GreenPointer, X11Pointer, x11_function


def _on_gtk(callback: Callable[[], Any]) -> Any:
    from gi.repository import GLib

    if GLib.MainContext.default().is_owner():
        return callback()

    result: Future[Any] = Future()

    def dispatch() -> bool:
        if result.set_running_or_notify_cancel():
            try:
                result.set_result(callback())
            except Exception as error:
                result.set_exception(error)
        return False

    GLib.idle_add(dispatch)
    try:
        return result.result(timeout=5)
    except TimeoutError:
        result.cancel()
        raise ToolError("The desktop is not responding.") from None


@dataclass
class DesktopChat:
    conversation_id: str
    task: str = ""
    reply: str = ""
    action: str = "Ready"
    last_action: str = ""
    busy: bool = False


class DesktopControl:
    """A single, revocable lease, issued only by the local desktop UI."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._chat: DesktopChat | None = None
        self._panel: DesktopPanel | None = None
        self._input: X11Input | None = None

    @staticmethod
    def supported() -> bool:
        return (
            sys.platform.startswith("linux")
            and bool(os.environ.get("DISPLAY"))
            and not os.environ.get("WAYLAND_DISPLAY")
        )

    def owner(self) -> str | None:
        with self._lock:
            return self._chat.conversation_id if self._chat else None

    def enable(
        self, conversation_id: str, stop: Callable[[], None], send: Callable[[str], None],
    ) -> None:
        if not self.supported():
            raise ToolError("Computer Use requires a Linux X11 desktop.")

        def activate() -> None:
            with self._lock:
                if self._chat is not None:
                    if self._chat.conversation_id == conversation_id:
                        return
                    raise ToolError("Turn off Computer Use in the other chat first.")
                from gi.repository import Gdk, Gtk

                display = Gdk.Display.get_default()
                if display is None or display.__gtype__.name != "GdkX11Display":
                    raise ToolError("Computer Use requires a Linux X11 desktop.")
                chat = DesktopChat(conversation_id)
                panel = DesktopPanel(Gtk, Gdk, chat, self._lock, stop, send)
                try:
                    desktop_input = X11Input(Gdk, Gtk, panel)
                except Exception:
                    panel.close()
                    raise
                self._chat, self._panel, self._input = chat, panel, desktop_input

        _on_gtk(activate)

    def disable(self, conversation_id: str) -> bool:
        with self._lock:
            if self._chat is None or self._chat.conversation_id != conversation_id:
                return False
            panel, desktop_input = self._panel, self._input
            self._chat = self._panel = self._input = None

        def cleanup() -> None:
            panel.close()
            desktop_input.close()

        _on_gtk(cleanup)
        return True

    def shutdown(self) -> None:
        """Release the X11 device when the native window's main loop exits."""
        with self._lock:
            panel, desktop_input = self._panel, self._input
            self._chat = self._panel = self._input = None
        if panel is not None and desktop_input is not None:
            desktop_input.close()
            panel.close()

    def for_run(self, conversation_id: str):
        with self._lock:
            chat = self._chat
        if chat is None or chat.conversation_id != conversation_id:
            return None

        def run(args: dict[str, Any]) -> tuple[str, str | None]:
            def with_access(callback: Callable[[], Any]) -> Any:
                with self._lock:
                    if self._chat is not chat:
                        raise ToolError("Computer Use was turned off for this run.")
                    return callback()

            result = _on_gtk(lambda: with_access(lambda: self._input.perform(chat, args, with_access)))
            if isinstance(result, Future):
                try:
                    result = result.result(timeout=5)
                except TimeoutError:
                    result.cancel()
                    raise ToolError("The agent pointer did not finish moving.") from None
            message, image = result
            if image is None:
                return message, None
            ok, content = image.save_to_bufferv("jpeg", ["quality"], ["85"])
            if not ok:
                raise ToolError("The desktop screenshot could not be encoded.")
            encoded = base64.b64encode(bytes(content)).decode("ascii")
            return message, f"data:image/jpeg;base64,{encoded}"

        return run

    def record(self, conversation_id: str, event: str, data: dict[str, Any]) -> None:
        with self._lock:
            chat = self._chat
            if chat is None or chat.conversation_id != conversation_id:
                return
            if event == "user_message":
                chat.task = str(data.get("text", ""))[:600]
                chat.reply = ""
                chat.action = "Reading the request"
                chat.last_action = ""
                chat.busy = True
            elif event == "step":
                chat.action = "Choosing the next action"
            elif event == "text_delta":
                chat.reply = (chat.reply + str(data.get("delta", "")))[-600:]
                chat.action = "Writing a reply"
            elif event == "tool_started" and data.get("name") != "computer_use":
                chat.action = f"Running {data.get('name', 'a tool')}"
            elif event in ("permission_request", "question_request"):
                chat.action = "Waiting for your reply in CodeLithe"
            elif event == "desktop_notice":
                chat.action = str(data["message"])
            elif event in ("run_finished", "cancelled", "error"):
                chat.busy = False
                if event == "cancelled":
                    chat.action = "Stopped"
                elif event == "error":
                    chat.action = str(data.get("message", "Run failed"))[:160]
                else:
                    chat.action = "Ready for the next message"


class DesktopPanel:
    def __init__(self, Gtk, Gdk, chat, lock, stop, send) -> None:
        from gi.repository import GLib, Pango

        self.window = win = Gtk.Window(title="CodeLithe Computer Use")
        win.set_name("desktop-control")
        win.set_decorated(False)
        win.set_keep_above(True)
        win.set_skip_taskbar_hint(True)
        win.set_skip_pager_hint(True)
        win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        win.set_focus_on_map(False)
        win.set_resizable(False)
        win.set_default_size(350, -1)
        win.connect("delete-event", lambda *_: (stop(), True)[1])

        style = Gtk.CssProvider()
        style.load_from_data(b"""
            #desktop-control { background: #1b1e24; color: #e9ecf1; border: 1px solid #3a4050; }
            #desktop-control label { color: #e9ecf1; }
            #desktop-control .desktop-action { color: #58d4bb; }
            #desktop-control entry { background: #16181d; color: #e9ecf1; }
            #desktop-control button { background: #232732; color: #e9ecf1; }
            #desktop-control .desktop-off { color: #fff; background: #9d3e49; }
        """)
        Gtk.StyleContext.add_provider_for_screen(win.get_screen(), style, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._style = style
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(12)
        win.add(box)
        header = Gtk.Box(spacing=8)
        box.pack_start(header, False, False, 0)
        title = Gtk.Label(xalign=0)
        title.set_markup("<b>CodeLithe · Computer Use</b>")
        header.pack_start(title, True, True, 0)
        off = Gtk.Button(label="Turn off")
        off.get_style_context().add_class("desktop-off")
        off.connect("clicked", lambda *_: stop())
        header.pack_end(off, False, False, 0)

        current = Gtk.Label(xalign=0)
        current.set_single_line_mode(True)
        current.set_ellipsize(Pango.EllipsizeMode.END)
        current.get_style_context().add_class("desktop-action")
        box.pack_start(current, False, False, 0)
        last_action = Gtk.Label(xalign=0)
        last_action.set_single_line_mode(True)
        last_action.set_ellipsize(Pango.EllipsizeMode.END)
        box.pack_start(last_action, False, False, 0)
        transcript = Gtk.ScrolledWindow()
        transcript.set_min_content_height(92)
        transcript.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box.pack_start(transcript, True, True, 0)
        messages = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        transcript.add(messages)
        task, reply = Gtk.Label(xalign=0), Gtk.Label(xalign=0)
        for label in (task, reply):
            label.set_line_wrap(True)
            label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_max_width_chars(38)
            label.set_selectable(True)
            messages.pack_start(label, False, False, 0)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Message in this chat")
        box.pack_start(entry, False, False, 0)

        def submit(_entry) -> None:
            text = entry.get_text().strip()
            if text:
                entry.set_text("")
                threading.Thread(target=send, args=(text,), daemon=True).start()

        entry.connect("activate", submit)

        adjustment = transcript.get_vadjustment()
        was_busy = follow_output = False

        def scroll_to_end(scroll) -> None:
            if follow_output:
                scroll.set_value(scroll.get_upper() - scroll.get_page_size())

        adjustment.connect("changed", scroll_to_end)

        def refresh() -> bool:
            nonlocal was_busy, follow_output
            with lock:
                lines = (
                    chat.action,
                    "Last desktop action · " + chat.last_action if chat.last_action else "",
                    "You · " + chat.task if chat.task else "Desktop control is on.",
                    "Agent · " + chat.reply if chat.reply else "Waiting for a reply.",
                )
                busy = chat.busy
            # The last text update and run_finished can share a GTK refresh.
            follow_output = busy or was_busy
            changed = busy != was_busy
            was_busy = busy
            for label, text in zip((current, last_action, task, reply), lines):
                if label.get_text() != text:
                    label.set_text(text)
                    changed = True
            if changed:
                scroll_to_end(adjustment)
            entry.set_sensitive(not busy)
            return True

        refresh()
        self._timer = GLib.timeout_add(120, refresh)
        win.show_all()
        monitor = win.get_display().get_primary_monitor() or win.get_display().get_monitor(0)
        area = monitor.get_workarea()
        width, height = win.get_size()
        win.move(area.x + area.width - width - 16, area.y + area.height - height - 16)

    def close(self) -> None:
        from gi.repository import GLib, Gtk

        GLib.source_remove(self._timer)
        Gtk.StyleContext.remove_provider_for_screen(self.window.get_screen(), self._style)
        self.window.destroy()

    def contains(self, x: int, y: int) -> bool:
        left, top = self.window.get_position()
        width, height = self.window.get_size()
        return left <= x < left + width and top <= y < top + height


@dataclass(frozen=True)
class _PointerStep:
    point: tuple[int, int]
    arrive: Callable[[], None] | None = None
    pause: float = 0


class X11Input:
    """XTest emits normal desktop events without a shell subprocess."""

    _BUTTON = {"left": 1, "middle": 2, "right": 3}
    _WHEEL = {"up": 4, "down": 5, "left": 6, "right": 7}
    _MODIFIERS = {"Ctrl": "Control_L", "Alt": "Alt_L", "Shift": "Shift_L", "Super": "Super_L"}

    def __init__(self, Gdk, Gtk, panel: DesktopPanel) -> None:
        self._gdk = Gdk
        self._panel = panel
        self._size: tuple[int, int] | None = None
        self._pointer: X11Pointer | None = None
        self._marker: GreenPointer | None = None
        self._blank_cursor = None
        self._animation: Future[tuple[str, Any]] | None = None
        self._animation_source = 0
        self._dragging = False
        try:
            self._x11 = ctypes.CDLL("libX11.so.6")
            xtest = ctypes.CDLL("libXtst.so.6")
        except OSError as error:
            raise ToolError("Computer Use needs the X11 and XTest libraries (libx11-6 and libxtst6).") from error

        pointer, integer, key = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint
        self._open = x11_function(self._x11, "XOpenDisplay", pointer, ctypes.c_char_p)
        self._close = x11_function(self._x11, "XCloseDisplay", integer, pointer)
        self._flush = x11_function(self._x11, "XFlush", integer, pointer)
        self._keysym = x11_function(self._x11, "XStringToKeysym", ctypes.c_ulong, ctypes.c_char_p)
        self._keycode = x11_function(self._x11, "XKeysymToKeycode", ctypes.c_ubyte, pointer, ctypes.c_ulong)
        self._motion = x11_function(xtest, "XTestFakeMotionEvent", integer, pointer, integer, integer, integer, ctypes.c_ulong)
        self._button = x11_function(xtest, "XTestFakeButtonEvent", integer, pointer, key, integer, ctypes.c_ulong)
        self._key = x11_function(xtest, "XTestFakeKeyEvent", integer, pointer, key, integer, ctypes.c_ulong)
        query = x11_function(xtest, "XTestQueryExtension", integer, pointer, *([ctypes.POINTER(integer)] * 4))
        self._display = self._open(None)
        if not self._display:
            raise ToolError("Could not connect to the X11 desktop.")
        try:
            if not query(self._display, *(ctypes.byref(integer()) for _ in range(4))):
                raise ToolError("The X11 desktop does not provide XTest.")
            self._pointer = X11Pointer(self._x11, self._display)
            self._marker = GreenPointer(Gtk, Gdk)
            from gi.repository import GdkX11

            display = Gdk.Display.get_default()
            self._blank_cursor = Gdk.Cursor.new_for_display(display, Gdk.CursorType.BLANK_CURSOR)
            self._pointer.set_root_cursor(GdkX11.X11Cursor.get_xcursor(self._blank_cursor))
            self._paint_pointer(self._pointer.position())
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self._animation_source:
            from gi.repository import GLib

            GLib.source_remove(self._animation_source)
        self._finish_animation(error=ToolError("Computer Use was turned off for this run."))
        if self._marker is not None:
            self._marker.close()
        if self._pointer is not None:
            self._pointer.close()
        self._close(self._display)

    def _capture(self):
        from gi.repository import GdkPixbuf

        root = self._gdk.get_default_root_window()
        width, height = root.get_width(), root.get_height()
        image = self._gdk.pixbuf_get_from_window(root, 0, 0, width, height)
        if image is None:
            raise ToolError("Could not capture the X11 desktop.")
        ratio = min(1.0, 1600 / max(width, height))
        self._size = (int(width * ratio), int(height * ratio))
        if (image.get_width(), image.get_height()) != self._size:
            image = image.scale_simple(*self._size, GdkPixbuf.InterpType.BILINEAR)
        return f"Desktop screenshot: {self._size[0]} × {self._size[1]} pixels.", image

    def _point(self, args: dict[str, Any], prefix: str = "") -> tuple[int, int]:
        if self._size is None:
            raise ToolError("Take a desktop screenshot before using coordinates.")
        x, y = args.get(prefix + "x"), args.get(prefix + "y")
        width, height = self._size
        if type(x) is not int or type(y) is not int or not (0 <= x < width and 0 <= y < height):
            raise ToolError(f"Coordinates must be inside the {width} × {height} screenshot.")
        root = self._gdk.get_default_root_window()
        x, y = x * root.get_width() // width, y * root.get_height() // height
        if self._panel.contains(x, y):
            raise ToolError("The floating CodeLithe panel is reserved for the user.")
        scale = root.get_scale_factor()
        return x * scale, y * scale

    def _paint_pointer(self, point: tuple[int, int]) -> None:
        scale = self._gdk.get_default_root_window().get_scale_factor()
        self._marker.move(point[0] // scale, point[1] // scale)

    def _click(self, button: int) -> None:
        self._button(self._display, button, 1, 0)
        self._button(self._display, button, 0, 0)

    def _drag_button(self, pressed: bool) -> None:
        self._dragging = pressed
        self._button(self._display, 1, int(pressed), 0)

    def _finish_animation(self, message: str = "", *, error: Exception | None = None) -> None:
        pending, self._animation = self._animation, None
        self._animation_source = 0
        if self._dragging:
            self._drag_button(False)
            self._flush(self._display)
        if pending is not None and not pending.done():
            if error is None:
                pending.set_result((message, None))
            else:
                pending.set_exception(error)

    def _animate(
        self, steps: list[_PointerStep], message: str, with_access: Callable,
    ) -> Future[tuple[str, Any]]:
        from gi.repository import GLib

        if self._animation is not None:
            raise ToolError("The agent pointer is already moving.")
        pending: Future[tuple[str, Any]] = Future()
        route = iter(steps)
        step = next(route)
        start = self._pointer.position()
        previous = start
        self._animation = pending
        started = monotonic()
        duration = self._move_duration(start, step)

        def frame() -> bool:
            nonlocal step, start, started, duration, previous
            self._pointer.check_selected()
            fraction = min(1.0, (monotonic() - started) / duration) if duration else 1.0
            eased = fraction * fraction * (3 - 2 * fraction)
            point = tuple(round(origin + (target - origin) * eased) for origin, target in zip(start, step.point))
            if point != previous:
                self._motion(self._display, -1, *point, 0)
                self._paint_pointer(point)
                previous = point
            if fraction >= 1 and step.arrive is not None:
                step.arrive()
            self._flush(self._display)
            if fraction < 1:
                return True
            start = step.point
            step = next(route, None)
            if step is None:
                self._finish_animation(message)
                return False
            started = monotonic()
            duration = self._move_duration(start, step)
            return True

        def tick() -> bool:
            try:
                if not pending.cancelled():
                    return with_access(frame)
                self._finish_animation()
            except Exception as error:
                self._finish_animation(error=error)
            return False

        if tick():
            self._animation_source = GLib.timeout_add(16, tick)
        return pending

    @staticmethod
    def _move_duration(start: tuple[int, int], step: _PointerStep) -> float:
        distance = math.dist(start, step.point)
        return min(.52, .12 + distance / 3800) if distance else step.pause

    def _code(self, name: str) -> int:
        key = self._MODIFIERS.get(name, name)
        if not re.fullmatch(r"[A-Za-z0-9_]+", key):
            raise ToolError(f"Unknown keyboard key: {name}.")
        code = self._keycode(self._display, self._keysym(key.encode("ascii")))
        if not code:
            raise ToolError(f"Key {name} is unavailable in the keyboard layout.")
        return code

    def _chord(self, codes: list[int]) -> None:
        for code in codes:
            self._key(self._display, code, 1, 0)
        for code in reversed(codes):
            self._key(self._display, code, 0, 0)

    def _type(self, text: Any) -> int:
        if not isinstance(text, str) or not 1 <= len(text) <= 500:
            raise ToolError("Provide between 1 and 500 characters.")
        keymap = self._gdk.Keymap.get_for_display(self._gdk.Display.get_default())
        shift = self._code("Shift")
        altgr = self._keycode(self._display, self._keysym(b"ISO_Level3_Shift"))
        modifiers = ([], [shift], [altgr], [shift, altgr]) if altgr else ([], [shift])
        chords = []
        for char in text:
            if char in "\n\t":
                chords.append([self._code("Return" if char == "\n" else "Tab")])
                continue
            _, entries = keymap.get_entries_for_keyval(self._gdk.unicode_to_keyval(ord(char)))
            key = next(
                (entry for entry in entries or () if entry.group == 0 and 0 <= entry.level < len(modifiers)),
                None,
            )
            if key is None:
                raise ToolError(f"The current keyboard layout cannot type {char!r}.")
            chords.append([*modifiers[key.level], key.keycode])
        for codes in chords:
            self._chord(codes)
        return len(text)

    def perform(
        self, chat: DesktopChat, args: dict[str, Any], with_access: Callable,
    ) -> tuple[str, Any] | Future[tuple[str, Any]]:
        action = args.get("action")
        if action == "screenshot":
            chat.action = chat.last_action = "Capturing the desktop"
            return self._capture()
        if action in ("press", "type"):
            if self._panel.window.is_active():
                raise ToolError("Focus the target application before sending keys.")
            self._pointer.check_selected()
            if action == "type":
                chat.action = chat.last_action = "Typing text"
                count = self._type(args.get("text"))
                message = f"Typed {count} characters."
            else:
                key = args.get("key")
                if not isinstance(key, str) or not 1 <= len(key.split("+")) <= 4:
                    raise ToolError("Provide a key or a chord such as Ctrl+l.")
                codes = [self._code(part) for part in key.split("+")]
                chat.action = chat.last_action = f"Pressing {key}"
                self._chord(codes)
                message = f"Pressed {key}."
        elif action in ("move", "click", "double_click", "drag", "scroll"):
            point = self._point(args)
            x, y = args["x"], args["y"]
            chat.action = chat.last_action = f"{action.replace('_', ' ').capitalize()} at ({x}, {y})"
            if action == "scroll":
                wheel = self._WHEEL.get(args.get("direction"))
                amount = args.get("amount", 3)
                if wheel is None or type(amount) is not int or not 1 <= amount <= 12:
                    raise ToolError("Choose a scroll direction and an amount from 1 to 12.")
                def scroll() -> None:
                    for _ in range(amount):
                        self._click(wheel)

                steps = [_PointerStep(point, scroll)]
            elif action == "drag":
                target = self._point(args, "to_")
                steps = [
                    _PointerStep(point, lambda: self._drag_button(True)),
                    _PointerStep(target, lambda: self._drag_button(False)),
                ]
            else:
                button = self._BUTTON.get(args.get("button", "left"))
                if button is None:
                    raise ToolError("Choose the left, middle or right mouse button.")
                def click() -> None:
                    self._click(button)

                steps = [_PointerStep(point, click if action != "move" else None)]
                if action == "double_click":
                    steps.append(_PointerStep(point, click, pause=.08))
            return self._animate(steps, f"{chat.action} completed.", with_access)
        else:
            raise ToolError("Unknown Computer Use action.")
        self._flush(self._display)
        return message, None
