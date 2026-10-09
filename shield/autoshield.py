"""Automatic shield aiming for Swords & Souls Neverseen defense training."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import math
import sys
import threading
import time

from shield_memory import ShieldMemoryReader


WINDOW_TITLE = "Swords & Souls Neverseen"
VK_F8 = 0x77
VK_F9 = 0x78
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
user32 = ctypes.windll.user32 if sys.platform == "win32" else None
ULONG_PTR = wintypes.WPARAM


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


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
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        text = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, text, length + 1)
        if WINDOW_TITLE.casefold() in text.value.casefold():
            matches.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return matches[0] if matches else None


def window_process_id(hwnd: int) -> int:
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    return int(process_id.value)


def send_mouse_move(dx: int, dy: int) -> None:
    event = INPUT(
        type=INPUT_MOUSE,
        mi=MOUSEINPUT(
            dx=dx,
            dy=dy,
            mouseData=0,
            dwFlags=MOUSEEVENTF_MOVE,
            time=0,
            dwExtraInfo=0,
        ),
    )
    if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError()


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def print_ptrace_hint() -> None:
    """Explain the usual reason Frida cannot attach on Linux."""
    try:
        with open("/proc/sys/kernel/yama/ptrace_scope", encoding="ascii") as file:
            scope = int(file.read().strip())
    except (OSError, ValueError):
        return
    if scope != 0:
        print(
            f"kernel.yama.ptrace_scope is {scope}, which blocks attaching to "
            "the running game. Allow it until the next reboot with:"
        )
        print("    sudo sysctl kernel.yama.ptrace_scope=0")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--gain", type=float, default=18.0)
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
            print("Check the X11 display and install shield/requirements.txt.")
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

    def move_mouse(dx: int, dy: int) -> None:
        if backend is not None:
            backend.send_mouse_move(dx, dy)
        else:
            send_mouse_move(dx, dy)

    def hotkey_pressed(key: str) -> bool:
        if backend is not None:
            return backend.consume_hotkey(key)
        virtual_key = VK_F8 if key == "F8" else VK_F9
        return bool(user32.GetAsyncKeyState(virtual_key) & 1)

    # Start safely paused. F8 enables the automation when the player is ready.
    enabled = False
    state_lock = threading.Lock()
    last_move = 0.0
    last_report = 0.0
    last_mode = ""

    def on_aim(payload: dict) -> None:
        nonlocal last_move, last_report, last_mode
        if not enabled or not game_is_focused():
            return
        error_x = float(payload["targetX"]) - float(payload["currentX"])
        error_y = float(payload["targetY"]) - float(payload["currentY"])
        if math.hypot(error_x, error_y) < 0.035:
            return
        with state_lock:
            now = time.perf_counter()
            if now - last_move < 0.003:
                return
            last_move = now
            dx = int(round(clamp(error_x * args.gain, -90.0, 90.0)))
            dy = int(round(clamp(-error_y * args.gain, -90.0, 90.0)))
            if dx == 0:
                dx = 1 if error_x > 0 else -1
            if dy == 0:
                dy = -1 if error_y > 0 else 1
            if not args.dry_run:
                move_mouse(dx, dy)
            mode = str(payload.get("mode", ""))
            if mode != last_mode or now - last_report >= 1.0:
                label = "BLOCK"
                print(
                    f"{label}  id={payload.get('id')} "
                    f"distance={float(payload.get('distance', 0)):.2f}",
                    flush=True,
                )
                last_mode = mode
                last_report = now

    try:
        process_id = (
            backend.get_process_id(hwnd)
            if backend is not None
            else window_process_id(hwnd)
        )
        reader = ShieldMemoryReader(process_id, on_aim)
    except Exception as error:
        print(f"Could not attach to game memory: {error}")
        if backend is not None:
            backend.close()
            if isinstance(error, PermissionError):
                print_ptrace_hint()
        return 1

    print("Swords & Souls shield helper v3 is ready.")
    print("F8: pause/resume | F9: quit")
    print("Automation PAUSED. Press F8 to start.")
    if args.dry_run:
        print("DRY RUN: mouse will not move.")

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
            reader.close()
        except Exception:
            pass
        if backend is not None:
            backend.close()
    print("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
