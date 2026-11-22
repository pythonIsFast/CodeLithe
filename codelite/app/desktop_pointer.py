"""A separate XInput2 pointer and its visible GTK marker."""

from __future__ import annotations

import ctypes as c
import os
from uuid import uuid4

from ..tools.base import ToolError


def x11_function(library, name, returns, *arguments):
    fn = getattr(library, name)
    fn.restype, fn.argtypes = returns, list(arguments)
    return fn


class _AddMaster(c.Structure):
    _fields_ = [("type", c.c_int), ("name", c.c_char_p), ("send_core", c.c_int), ("enable", c.c_int)]


class _RemoveMaster(c.Structure):
    _fields_ = [
        ("type", c.c_int), ("device_id", c.c_int), ("return_mode", c.c_int),
        ("return_pointer", c.c_int), ("return_keyboard", c.c_int),
    ]


class _DeviceInfo(c.Structure):
    _fields_ = [
        ("device_id", c.c_int), ("name", c.c_char_p), ("use", c.c_int),
        ("attachment", c.c_int), ("enabled", c.c_int),
        ("class_count", c.c_int), ("classes", c.c_void_p),
    ]


class X11Pointer:
    """Create and route only the automation connection to a new master pair."""

    def __init__(self, x11, display) -> None:
        try:
            xi = c.CDLL("libXi.so.6")
        except OSError as error:
            raise ToolError("Computer Use needs the XInput2 library (libxi6).") from error
        self.display = display
        self.device_id = 0
        pointer, integer, xid = c.c_void_p, c.c_int, c.c_ulong
        int_ref, xid_ref = c.POINTER(integer), c.POINTER(xid)
        version = x11_function(xi, "XIQueryVersion", integer, pointer, int_ref, int_ref)
        if version(display, c.byref(integer(2)), c.byref(integer(0))) != 0:
            raise ToolError("This X server does not support a separate agent pointer.")
        self._change = x11_function(xi, "XIChangeHierarchy", integer, pointer, pointer, integer)
        query = x11_function(xi, "XIQueryDevice", c.POINTER(_DeviceInfo), pointer, integer, int_ref)
        free = x11_function(xi, "XIFreeDeviceInfo", None, c.POINTER(_DeviceInfo))
        select = x11_function(xi, "XISetClientPointer", integer, pointer, xid, integer)
        self._selected = x11_function(xi, "XIGetClientPointer", integer, pointer, xid, int_ref)
        self._style = x11_function(xi, "XIDefineCursor", integer, pointer, integer, xid, xid)
        root = x11_function(x11, "XDefaultRootWindow", xid, pointer)
        self._query_pointer = x11_function(
            x11, "XQueryPointer", integer, pointer, xid, xid_ref, xid_ref,
            int_ref, int_ref, int_ref, int_ref, c.POINTER(c.c_uint),
        )
        self._sync = x11_function(x11, "XSync", integer, pointer, integer)
        self.root = root(display)

        name = f"CodeLithe Agent {os.getpid()} {uuid4().hex[:6]}".encode("ascii")
        request = _AddMaster(1, name, 1, 1)
        if self._change(display, c.byref(request), 1) != 0:
            raise ToolError("Could not create a separate agent pointer.")
        count = integer()
        devices = query(display, 1, c.byref(count))  # XIAllMasterDevices
        if devices:
            try:
                for index in range(count.value):
                    device = devices[index]
                    if device.use == 1 and device.name == name + b" pointer":
                        self.device_id = device.device_id
                        break
            finally:
                free(devices)
        if not self.device_id:
            raise ToolError("The X server did not return the separate agent pointer.")

        # Window None sets the client pointer for this X connection only.
        if select(display, 0, self.device_id) != 0:
            self.close()
            raise ToolError("Could not isolate desktop input from your system pointer.")
        try:
            self.check_selected()
        except ToolError:
            self.close()
            raise

    def check_selected(self) -> None:
        actual = c.c_int()
        if not self._selected(self.display, 0, c.byref(actual)) or actual.value != self.device_id:
            raise ToolError("The separate agent pointer is no longer available.")

    def position(self) -> tuple[int, int]:
        self.check_selected()
        x, y = c.c_int(), c.c_int()
        same_screen = self._query_pointer(
            self.display, self.root, c.byref(c.c_ulong()), c.byref(c.c_ulong()),
            c.byref(x), c.byref(y), c.byref(c.c_int()), c.byref(c.c_int()), c.byref(c.c_uint()),
        )
        if not same_screen:
            raise ToolError("The agent pointer is on a different X11 screen.")
        return x.value, y.value

    def set_root_cursor(self, cursor: int) -> None:
        self._style(self.display, self.device_id, self.root, cursor)

    def close(self) -> None:
        if self.device_id:
            request = _RemoveMaster(2, self.device_id, 2, 0, 0)  # XIFloating
            self._change(self.display, c.byref(request), 1)
            self._sync(self.display, 0)
            self.device_id = 0


class GreenPointer:
    """Click-through marker aligned to the separate pointer's hotspot."""

    _SIZE = 58
    _TIP = (15, 13)

    def __init__(self, Gtk, Gdk) -> None:
        import cairo

        self.window = window = Gtk.Window(type=Gtk.WindowType.POPUP)
        window.set_title("CodeLithe Agent Pointer")
        window.set_app_paintable(True)
        window.set_accept_focus(False)
        window.set_focus_on_map(False)
        window.set_default_size(self._SIZE, self._SIZE)
        window.set_keep_above(True)
        screen = window.get_screen()
        rgba = screen.get_rgba_visual()
        composited = bool(rgba and screen.is_composited())
        if composited:
            window.set_visual(rgba)

        def draw(_widget, cr) -> bool:
            self._draw(cairo, cr, composited)
            return True

        window.connect("draw", draw)
        window.realize()
        native = window.get_window()
        if not composited:
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, self._SIZE, self._SIZE)
            self._draw(cairo, cairo.Context(surface), False)
            native.shape_combine_region(Gdk.cairo_region_create_from_surface(surface), 0, 0)
        native.input_shape_combine_region(cairo.Region(), 0, 0)
        native.set_pass_through(True)

    @classmethod
    def _draw(cls, cairo, cr, glow: bool) -> None:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        x, y = cls._TIP
        cr.move_to(x, y)
        for dx, dy in ((1, 27), (8, 19), (14, 29), (19, 26), (13, 16), (23, 16)):
            cr.line_to(x + dx, y + dy)
        cr.close_path()
        if glow:
            for width, opacity in ((21, .06), (15, .10), (10, .18), (6, .30)):
                cr.set_line_width(width)
                cr.set_source_rgba(.21, 1, .45, opacity)
                cr.stroke_preserve()
        cr.set_source_rgb(.34, 1, .52)
        cr.fill_preserve()
        cr.set_line_width(2)
        cr.set_source_rgb(.04, .36, .16)
        cr.stroke()

    def move(self, x: int, y: int) -> None:
        self.window.move(x - self._TIP[0], y - self._TIP[1])
        if not self.window.is_visible():
            self.window.show()

    def close(self) -> None:
        self.window.destroy()
