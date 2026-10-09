"""X11/XWayland window, focus, hotkey, keyboard, and mouse support for Linux."""

from __future__ import annotations

import os
import threading
import time

try:
    from Xlib import X, XK, display
    from Xlib.ext import xtest
except ImportError as error:
    raise RuntimeError(
        "python-xlib is required for Linux support; install requirements.txt"
    ) from error


class LinuxPlatform:
    """Interact with the Proton game through its X11/XWayland window."""

    _KEYSYMS = {
        "W": "w", "A": "a", "S": "s", "D": "d", "F8": "F8", "F9": "F9"
    }

    def __init__(self, title_fragment: str) -> None:
        self.uses_wayland = (
            os.environ.get("XDG_SESSION_TYPE", "").casefold() == "wayland"
            or bool(os.environ.get("WAYLAND_DISPLAY"))
        )
        try:
            self._display = display.Display()
        except Exception as error:
            raise RuntimeError(
                "cannot connect to DISPLAY; Proton window lookup requires XWayland"
            ) from error

        if not self._display.has_extension("XTEST"):
            self._display.close()
            raise RuntimeError("the X server does not provide the XTEST extension")

        # Frida delivers its callbacks on a separate thread while the main loop
        # polls hotkeys and focus, so every display request is serialized.
        self._lock = threading.RLock()
        self._root = self._display.screen().root
        self._title_fragment = title_fragment.casefold()
        self._window = None
        self._window_pid = None
        self._atoms = {
            name: self._display.intern_atom(name)
            for name in (
                "_NET_CLIENT_LIST_STACKING",
                "_NET_WM_NAME",
                "_NET_WM_PID",
                "_NET_ACTIVE_WINDOW",
            )
        }
        self._keycodes = {}
        for name, keysym_name in self._KEYSYMS.items():
            keysym = XK.string_to_keysym(keysym_name)
            keycode = self._display.keysym_to_keycode(keysym)
            if not keycode:
                self._display.close()
                raise RuntimeError(f"no X11 keycode is mapped for {name}")
            self._keycodes[name] = keycode
        self._hotkey_states = {"F8": False, "F9": False}
        self._focus_cache = False
        self._focus_checked_at = 0.0

    def _window_title(self, window) -> str:
        prop = window.get_full_property(
            self._atoms["_NET_WM_NAME"], X.AnyPropertyType
        )
        if prop is not None:
            value = prop.value
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            return bytes(value).decode("utf-8", errors="replace")
        legacy_name = window.get_wm_name()
        if isinstance(legacy_name, bytes):
            return legacy_name.decode(errors="replace")
        return legacy_name or ""

    def _client_windows(self):
        prop = self._root.get_full_property(
            self._atoms["_NET_CLIENT_LIST_STACKING"], X.AnyPropertyType
        )
        if prop is not None:
            return [
                self._display.create_resource_object("window", int(window_id))
                for window_id in prop.value
            ]
        return self._root.query_tree().children

    def find_game_window(self) -> int | None:
        candidates = []
        with self._lock:
            for window in self._client_windows():
                try:
                    map_state = window.get_attributes().map_state
                    title = self._window_title(window)
                    if self._title_fragment not in title.casefold():
                        continue
                    pid_prop = window.get_full_property(
                        self._atoms["_NET_WM_PID"], X.AnyPropertyType
                    )
                    if pid_prop is None or not len(pid_prop.value):
                        continue
                    candidates.append(
                        (map_state == X.IsViewable, window, int(pid_prop.value[0]))
                    )
                except Exception:
                    continue

        if not candidates:
            return None
        # Prefer the topmost visible match, but still recognize minimized
        # windows so the helper can wait for the user to restore/focus them.
        visible = [candidate for candidate in candidates if candidate[0]]
        _is_visible, self._window, self._window_pid = (
            visible[-1] if visible else candidates[-1]
        )
        return int(self._window.id)

    def get_process_id(self, _window_id: int) -> int:
        if self._window_pid is None:
            raise RuntimeError("game window process ID is unavailable")
        return self._window_pid

    def get_client_box(self, window_id: int) -> dict[str, int] | None:
        if self._window is None or int(self._window.id) != window_id:
            return None
        with self._lock:
            try:
                geometry = self._window.get_geometry()
                origin = self._root.translate_coords(self._window, 0, 0)
                return {
                    "left": int(origin.x),
                    "top": int(origin.y),
                    "width": int(geometry.width),
                    "height": int(geometry.height),
                }
            except Exception:
                return None

    def is_focused(self, window_id: int) -> bool:
        with self._lock:
            now = time.monotonic()
            if now - self._focus_checked_at < 0.02:
                return self._focus_cache
            self._focus_checked_at = now
            try:
                active = self._root.get_full_property(
                    self._atoms["_NET_ACTIVE_WINDOW"], X.AnyPropertyType
                )
                if active is not None and len(active.value):
                    self._focus_cache = int(active.value[0]) == window_id
                    return self._focus_cache

                focus = self._display.get_input_focus().focus
                focus_id = getattr(focus, "id", focus)
                while focus_id not in (X.NONE, X.PointerRoot, self._root.id):
                    if int(focus_id) == window_id:
                        self._focus_cache = True
                        return True
                    parent = self._display.create_resource_object(
                        "window", int(focus_id)
                    ).query_tree().parent
                    parent_id = getattr(parent, "id", parent)
                    if parent_id is None or int(parent_id) == int(focus_id):
                        break
                    focus_id = parent_id
            except Exception:
                pass
            self._focus_cache = False
            return False

    def consume_hotkey(self, key: str) -> bool:
        keycode = self._keycodes[key]
        with self._lock:
            keymap = self._display.query_keymap()
            down = bool(keymap[keycode >> 3] & (1 << (keycode & 7)))
            was_down = self._hotkey_states[key]
            self._hotkey_states[key] = down
            return down and not was_down

    def send_key(self, key: str, is_down: bool) -> None:
        event = X.KeyPress if is_down else X.KeyRelease
        with self._lock:
            xtest.fake_input(self._display, event, detail=self._keycodes[key])
            self._display.flush()

    def send_mouse_button(self, is_down: bool) -> None:
        event = X.ButtonPress if is_down else X.ButtonRelease
        with self._lock:
            xtest.fake_input(self._display, event, detail=1)
            self._display.flush()

    def set_cursor_pos(self, x: int, y: int) -> None:
        with self._lock:
            xtest.fake_input(self._display, X.MotionNotify, x=x, y=y)
            self._display.flush()

    def send_mouse_move(self, dx: int, dy: int) -> None:
        with self._lock:
            # detail=True makes the XTest motion relative to the pointer.
            xtest.fake_input(self._display, X.MotionNotify, detail=True, x=dx, y=dy)
            self._display.flush()

    def close(self) -> None:
        with self._lock:
            self._display.close()
