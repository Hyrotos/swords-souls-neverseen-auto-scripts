"""Automatic centre-shot helper for Swords & Souls Neverseen archery."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import sys
import threading
import time

from archery_memory import ArcheryMemory


WINDOW_TITLE = "Swords & Souls Neverseen"
VK_F8 = 0x77
VK_F9 = 0x78
INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
SAFE_AIM_X_RATIO = 0.50
SAFE_AIM_Y_RATIO = 0.50

user32 = ctypes.windll.user32 if sys.platform == "win32" else None


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", INPUT_UNION)]


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


def mouse_button(flags: int) -> None:
    event = INPUT(
        type=INPUT_MOUSE,
        mi=MOUSEINPUT(0, 0, 0, flags, 0, 0),
    )
    if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cooldown-ms", type=float, default=20.0)
    parser.add_argument("--hold-max-ms", type=float, default=300.0)
    parser.add_argument("--shot-confirm-max-ms", type=float, default=1000.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
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
            print("Check the X11 display and install archery/requirements.txt.")
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

    def press_mouse(is_down: bool) -> None:
        if backend is not None:
            backend.send_mouse_button(is_down)
        else:
            mouse_button(MOUSEEVENTF_LEFTDOWN if is_down else MOUSEEVENTF_LEFTUP)

    # Start safely paused. F8 enables the automation when the player is ready.
    enabled = False
    held = False
    release_at = 0.0
    last_shot = 0.0
    last_report = 0.0
    saw_not_ready = False
    latest_shot_sequence = 0
    released_shot_sequence = 0
    released_at = 0.0
    waiting_for_arrow = False
    lock = threading.Lock()

    def release_mouse(expect_arrow: bool = True) -> None:
        nonlocal held, released_shot_sequence, released_at
        nonlocal waiting_for_arrow
        if held:
            if not args.dry_run:
                press_mouse(False)
            held = False
            if expect_arrow and not args.dry_run:
                released_shot_sequence = latest_shot_sequence
                released_at = time.perf_counter()
                waiting_for_arrow = True

    def on_aim(payload: dict) -> None:
        nonlocal held, release_at, last_shot, last_report
        nonlocal saw_not_ready, latest_shot_sequence, waiting_for_arrow
        if not enabled or not game_is_focused():
            return
        geometry = game_geometry()
        if geometry is None:
            return
        left, top, width, height = geometry
        if width <= 0 or height <= 0:
            return

        # Keep the bow and the real mouse cursor in a safe central position.
        # The in-memory arrow guidance still follows the exact target centre,
        # so moving the system cursor to targets is unnecessary and could make
        # a shot click a pause/menu button near an edge of the game window.
        x = left + int(round((width - 1) * SAFE_AIM_X_RATIO))
        y = top + int(round((height - 1) * SAFE_AIM_Y_RATIO))

        with lock:
            now = time.perf_counter()
            latest_shot_sequence = int(payload.get("shotSequence", 0))
            set_cursor(x, y)
            bow_can_shoot = bool(payload.get("bowCanShoot"))
            if waiting_for_arrow:
                confirmed = latest_shot_sequence > released_shot_sequence
                timed_out = (
                    now - released_at
                    >= args.shot_confirm_max_ms / 1000.0
                )
                if confirmed or timed_out:
                    waiting_for_arrow = False
                else:
                    return
            if held:
                if not bow_can_shoot:
                    saw_not_ready = True
                if bow_can_shoot and saw_not_ready:
                    release_mouse()
                return
            if (
                bool(payload.get("bowReadyToDraw"))
                and int(payload.get("activeArrowCount", 0)) == 0
                and now - last_shot >= args.cooldown_ms / 1000.0
            ):
                if not args.dry_run:
                    press_mouse(True)
                held = True
                saw_not_ready = not bow_can_shoot
                release_at = now + args.hold_max_ms / 1000.0
                last_shot = now
                print(
                    f"SHOT  {payload.get('className')} "
                    f"world=({float(payload['worldX']):.2f},"
                    f"{float(payload['worldY']):.2f})",
                    flush=True,
                )
                last_report = now
            elif now - last_report >= 2.0:
                print(f"TRACK {payload.get('className')}", flush=True)
                last_report = now

    try:
        game_pid = (
            backend.get_process_id(hwnd)
            if backend is not None
            else process_id(hwnd)
        )
        reader = ArcheryMemory(game_pid, on_aim)
    except Exception as error:
        print(f"Could not attach to game memory: {error}")
        if backend is not None:
            from wine_frida import print_attach_hint

            backend.close()
            print_attach_hint(error)
        return 1

    print("Swords & Souls archery helper v10 is ready.")
    print("F8: pause/resume | F9: quit")
    print("Automation PAUSED. Press F8 to start.")
    if args.dry_run:
        print("DRY RUN: mouse will not click.")

    reader_active = False
    try:
        while True:
            now = time.perf_counter()
            if held and now >= release_at:
                with lock:
                    release_mouse()
            if hotkey_pressed("F9"):
                break
            if hotkey_pressed("F8"):
                enabled = not enabled
                if not enabled:
                    with lock:
                        release_mouse(expect_arrow=False)
                print("Automation ON." if enabled else "Automation PAUSED.")
            focused = game_is_focused()
            wanted = bool(enabled and focused)
            if wanted != reader_active:
                reader.set_active(wanted, guide_arrows=not args.dry_run)
                reader_active = wanted
                if enabled and not focused:
                    print("Waiting for the game window...")
            time.sleep(0.002)
    except KeyboardInterrupt:
        pass
    finally:
        with lock:
            release_mouse(expect_arrow=False)
        try:
            reader.set_active(False, guide_arrows=False)
            time.sleep(0.03)
        except Exception:
            pass
        reader.close()
        if backend is not None:
            backend.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
