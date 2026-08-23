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
user32 = ctypes.windll.user32
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
    if sys.platform != "win32":
        print("Windows only.")
        return 2
    args = parse_args()
    hwnd = find_game_window()
    if hwnd is None:
        print("Game window was not found. Start the game first.")
        return 1

    # Start safely paused. F8 enables the automation when the player is ready.
    enabled = False
    active = False
    held: set[int] = set()
    press_lock = threading.Lock()
    last_press = -100.0
    press_count = 0

    def release(scan: int) -> None:
        with press_lock:
            if scan in held:
                try:
                    send_scan_code(scan, False)
                finally:
                    held.discard(scan)

    def on_dodge(payload: dict) -> None:
        nonlocal last_press, press_count
        if not enabled or user32.GetForegroundWindow() != hwnd:
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
            scan = SCAN_CODES[key]
            if not args.dry_run:
                send_scan_code(scan, True)
                held.add(scan)
                timer = threading.Timer(args.hold_ms / 1000.0, release, args=(scan,))
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
        reader = AgilityMemoryReader(window_process_id(hwnd), on_dodge)
        actual_lead = reader.set_lead_ms(args.lead_ms)
    except Exception as error:
        print(f"Could not attach to game memory: {error}")
        return 1

    print("Swords & Souls agility helper is ready.")
    print(f"Adaptive last-moment prediction: {actual_lead:.0f} ms")
    print("F8: pause/resume | F9: quit")
    print("Automation PAUSED. Press F8 to start.")
    if args.dry_run:
        print("DRY RUN: keys will not be pressed.")

    try:
        while True:
            if user32.GetAsyncKeyState(VK_F9) & 1:
                break
            if user32.GetAsyncKeyState(VK_F8) & 1:
                enabled = not enabled
                print("Automation ON." if enabled else "Automation PAUSED.")
            focused = user32.GetForegroundWindow() == hwnd
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
        for scan in tuple(held):
            try:
                send_scan_code(scan, False)
            except OSError:
                pass
    print("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
