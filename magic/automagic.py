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
user32 = ctypes.windll.user32


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
    if sys.platform != "win32":
        print("Windows only.")
        return 2
    enable_dpi_awareness()
    hwnd = find_game_window()
    if hwnd is None:
        print("Game window was not found. Start the game first.")
        return 1

    # Start safely paused. F8 enables the automation when the player is ready.
    enabled = False
    scored_total = 0
    last_report = 0.0

    def on_aim(payload: dict) -> None:
        if not enabled or user32.GetForegroundWindow() != hwnd:
            return
        geometry = client_geometry(hwnd)
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
        user32.SetCursorPos(x, y)

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
        reader = MagicMemoryReader(process_id(hwnd), on_aim, on_scored)
    except Exception as error:
        print(f"Could not attach to game memory: {error}")
        return 1

    print("Swords & Souls magic helper v1 is ready.")
    print("F8: pause/resume | F9: quit")
    print("Automation PAUSED. Press F8 to start.")
    reader_active = False
    try:
        while True:
            if user32.GetAsyncKeyState(VK_F9) & 1:
                break
            if user32.GetAsyncKeyState(VK_F8) & 1:
                enabled = not enabled
                print("Automation ON." if enabled else "Automation PAUSED.")
            focused = user32.GetForegroundWindow() == hwnd
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
    print("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
