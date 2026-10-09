"""Automatic cursor and guaranteed scoring for the magic training."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
import time

from magic_memory import MagicMemoryReader


WINDOW_TITLE = "Swords & Souls Neverseen"
VK_F8 = 0x77
VK_F9 = 0x78
SAFE_AIM_X_RATIO = 0.50
SAFE_AIM_Y_RATIO = 0.50
user32 = ctypes.windll.user32 if sys.platform == "win32" else None


def enable_dpi_awareness() -> None:
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


def find_game_window() -> int | None:
    matches: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd: int, _parameter: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            value = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, value, length + 1)
            if WINDOW_TITLE.casefold() in value.value.casefold():
                matches.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return matches[0] if matches else None


def process_id(hwnd: int) -> int:
    value = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(value))
    return int(value.value)


def client_geometry(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    origin = wintypes.POINT(0, 0)
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    return origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top


def main() -> int:
    backend = None
    if sys.platform == "win32":
        enable_dpi_awareness()
        hwnd = find_game_window()
    elif sys.platform.startswith("linux"):
        try:
            from linux_platform import LinuxPlatform

            backend = LinuxPlatform(WINDOW_TITLE)
            hwnd = backend.find_game_window()
        except Exception as error:
            print(f"Could not initialize the Linux/X11 backend: {error}")
            print("Check the X11 display and install magic/requirements.txt.")
            return 2
    else:
        print(f"Unsupported platform: {sys.platform}")
        return 2
    if hwnd is None:
        print("Game window was not found. Start the game first.")
        if backend is not None:
            backend.close()
        return 1

    def game_is_focused() -> bool:
        if backend is not None:
            return backend.is_focused(hwnd)
        return user32.GetForegroundWindow() == hwnd

    def hotkey_pressed(key: str) -> bool:
        if backend is not None:
            return backend.consume_hotkey(key)
        virtual_key = VK_F8 if key == "F8" else VK_F9
        return bool(user32.GetAsyncKeyState(virtual_key) & 1)

    def game_geometry() -> tuple[int, int, int, int] | None:
        if backend is None:
            return client_geometry(hwnd)
        box = backend.get_client_box(hwnd)
        if box is None:
            return None
        return box["left"], box["top"], box["width"], box["height"]

    def set_cursor(x: int, y: int) -> None:
        if backend is not None:
            backend.set_cursor_pos(x, y)
        else:
            user32.SetCursorPos(x, y)

    # Start safely paused. F8 enables the automation when the player is ready.
    enabled = False
    scored_total = 0
    last_report = 0.0

    def on_aim(payload: dict) -> None:
        if not enabled or not game_is_focused():
            return
        geometry = game_geometry()
        if geometry is None:
            return
        left, top, width, height = geometry
        if width <= 0 or height <= 0:
            return

        # Scoring is completed directly through the game's normal target
        # success path, so the system cursor does not need to follow targets.
        # Keep it in the same safe central position used by Archery Auto.
        x = left + int(round((width - 1) * SAFE_AIM_X_RATIO))
        y = top + int(round((height - 1) * SAFE_AIM_Y_RATIO))
        set_cursor(x, y)

    def on_scored(payload: dict) -> None:
        nonlocal scored_total, last_report
        count = int(payload.get("count", 0))
        scored_total += count
        now = time.perf_counter()
        if now - last_report >= 0.15:
            types = ", ".join(str(value) for value in payload.get("types", []))
            print(f"SCORED +{count} (total={scored_total}) {types}", flush=True)
            last_report = now

    try:
        game_pid = (
            backend.get_process_id(hwnd)
            if backend is not None
            else process_id(hwnd)
        )
        reader = MagicMemoryReader(game_pid, on_aim, on_scored)
    except Exception as error:
        print(f"Could not attach to game memory: {error}")
        if backend is not None:
            from wine_frida import print_attach_hint

            backend.close()
            print_attach_hint(error)
        return 1

    print("Swords & Souls magic helper v1 is ready.")
    print("F8: pause/resume | F9: quit")
    print("Automation PAUSED. Press F8 to start.")
    reader_active = False
    try:
        while True:
            if hotkey_pressed("F9"):
                break
            if hotkey_pressed("F8"):
                enabled = not enabled
                print("Automation ON." if enabled else "Automation PAUSED.")
            focused = game_is_focused()
            wanted = bool(enabled and focused)
            if wanted != reader_active:
                reader.set_active(wanted)
                reader_active = wanted
                if enabled and not focused:
                    print("Waiting for the game window...")
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            reader.set_active(False)
        except Exception:
            pass
        reader.close()
        if backend is not None:
            backend.close()
    print("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
