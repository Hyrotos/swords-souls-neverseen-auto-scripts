"""Automatic last-moment WASD dodging for the agility training."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import sys
import threading
import time

from agility_memory import AgilityMemoryReader


WINDOW_TITLE = "Swords & Souls Neverseen"
VK_F8 = 0x77
VK_F9 = 0x78
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
SCAN_CODES = {"W": 0x11, "A": 0x1E, "S": 0x1F, "D": 0x20}
user32 = ctypes.windll.user32 if sys.platform == "win32" else None
ULONG_PTR = wintypes.WPARAM


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
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
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", INPUT_UNION)]


def find_game_window() -> int | None:
    matches: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        if WINDOW_TITLE.casefold() in title.value.casefold():
            matches.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return matches[0] if matches else None


def window_process_id(hwnd: int) -> int:
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    return int(process_id.value)


def send_scan_code(scan_code: int, is_down: bool) -> None:
    flags = KEYEVENTF_SCANCODE | (0 if is_down else KEYEVENTF_KEYUP)
    event = INPUT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(
            wVk=0,
            wScan=scan_code,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        ),
    )
    if user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
        raise ctypes.WinError()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lead-ms",
        type=float,
        default=32.0,
        help="prediction lead before the game's hit check (default: 32)",
    )
    parser.add_argument("--hold-ms", type=float, default=38.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    backend = None
    if sys.platform == "win32":
        hwnd = find_game_window()
    elif sys.platform.startswith("linux"):
        try:
            from linux_platform import LinuxPlatform

            backend = LinuxPlatform(WINDOW_TITLE)
            hwnd = backend.find_game_window()
        except Exception as error:
            print(f"Could not initialize the Linux/X11 backend: {error}")
            print("Check the X11 display and install agility/requirements.txt.")
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

    def send_key(key: str, is_down: bool) -> None:
        if backend is not None:
            backend.send_key(key, is_down)
        else:
            send_scan_code(SCAN_CODES[key], is_down)

    # Start safely paused. F8 enables the automation when the player is ready.
    enabled = False
    active = False
    held: set[str] = set()
    press_lock = threading.Lock()
    last_press = -100.0
    press_count = 0

    def release(key: str) -> None:
        with press_lock:
            if key in held:
                try:
                    send_key(key, False)
                finally:
                    held.discard(key)

    def on_dodge(payload: dict) -> None:
        nonlocal last_press, press_count
        if not enabled or not game_is_focused():
            return
        key = str(payload.get("key", ""))
        if key not in SCAN_CODES:
            return
        with press_lock:
            now = time.perf_counter()
            if now - last_press < 0.085:
                return
            last_press = now
            press_count += 1
            if not args.dry_run:
                send_key(key, True)
                held.add(key)
                timer = threading.Timer(args.hold_ms / 1000.0, release, args=(key,))
                timer.daemon = True
                timer.start()
            if payload.get("kind") == "attack":
                print(
                    f"{key}  ATTACK #{press_count}  "
                    f"side={payload.get('direction')}",
                    flush=True,
                )
            else:
                print(
                    f"{key}  dodge #{press_count}  "
                    f"direction={payload.get('direction')}  "
                    f"progress={float(payload.get('progress', 0)):.3f}  "
                    f"predicted={float(payload.get('predicted', 0)):.3f}",
                    flush=True,
                )

    try:
        game_pid = (
            backend.get_process_id(hwnd)
            if backend is not None
            else window_process_id(hwnd)
        )
        reader = AgilityMemoryReader(game_pid, on_dodge)
        actual_lead = reader.set_lead_ms(args.lead_ms)
    except Exception as error:
        print(f"Could not attach to game memory: {error}")
        if backend is not None:
            from wine_frida import print_attach_hint

            backend.close()
            print_attach_hint(error)
        return 1

    print("Swords & Souls agility helper is ready.")
    print(f"Adaptive last-moment prediction: {actual_lead:.0f} ms")
    print("F8: pause/resume | F9: quit")
    print("Automation PAUSED. Press F8 to start.")
    if args.dry_run:
        print("DRY RUN: keys will not be pressed.")

    try:
        while True:
            if hotkey_pressed("F9"):
                break
            if hotkey_pressed("F8"):
                enabled = not enabled
                print("Automation ON." if enabled else "Automation PAUSED.")
            focused = game_is_focused()
            wanted = bool(enabled and focused)
            if wanted != active:
                reader.set_active(wanted)
                active = wanted
                if enabled and not focused:
                    print("Waiting for the game window to be focused...")
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            reader.set_active(False)
            reader.close()
        except Exception:
            pass
        for key in tuple(held):
            try:
                send_key(key, False)
            except Exception:
                pass
        if backend is not None:
            backend.close()
    print("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
