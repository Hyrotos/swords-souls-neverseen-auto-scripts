"""Low-latency W/A/D helper for Swords & Souls Neverseen.

The script watches the three reaction circles and taps the corresponding key
when a red flying item enters a circle.  It never sends keys while the game is
not the foreground window.
"""

from __future__ import annotations

import argparse
from collections import deque
from contextlib import nullcontext
import ctypes
from ctypes import wintypes
import math
import sys
import threading
import time
from dataclasses import dataclass, field

import cv2
import mss
import numpy as np


WINDOW_TITLE = "Swords & Souls Neverseen"

# Positions measured from the supplied screenshot.  They are relative to the
# game client area, so moving or resizing the window does not break them.
TARGET_LAYOUT = {
    "W": (0.5000, 0.6836),
    "A": (0.4004, 0.8422),
    "D": (0.5985, 0.8422),
}

SCAN_CODES = {"W": 0x11, "A": 0x1E, "D": 0x20}
VK_F8 = 0x77
VK_F9 = 0x78

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

user32 = ctypes.windll.user32 if sys.platform == "win32" else None


def enable_dpi_awareness() -> None:
    """Make window and screenshot coordinates use real screen pixels."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


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
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", INPUT_UNION)]


def send_scan_code(scan_code: int, is_down: bool) -> None:
    flags = KEYEVENTF_SCANCODE
    if not is_down:
        flags |= KEYEVENTF_KEYUP
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
    sent = user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
    if sent != 1:
        raise ctypes.WinError()


def find_game_window(title_fragment: str) -> int | None:
    matches: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        text = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, text, length + 1)
        if title_fragment.casefold() in text.value.casefold():
            matches.append(hwnd)
        return True

    user32.EnumWindows(enum_callback, 0)
    return matches[0] if matches else None


def get_window_process_id(hwnd: int) -> int:
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    return int(process_id.value)


def get_client_box(hwnd: int) -> dict[str, int] | None:
    if user32.IsIconic(hwnd):
        return None
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width < 400 or height < 300:
        return None
    return {
        "left": int(origin.x),
        "top": int(origin.y),
        "width": int(width),
        "height": int(height),
    }


def red_mask(frame_bgr: np.ndarray) -> np.ndarray:
    """Return a cleaned mask for the saturated red parts of flying apples."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    low_red = cv2.inRange(hsv, (0, 105, 55), (14, 255, 255))
    high_red = cv2.inRange(hsv, (165, 105, 55), (179, 255, 255))
    mask = cv2.bitwise_or(low_red, high_red)
    kernel = np.ones((3, 3), dtype=np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def object_centres(mask: np.ndarray) -> list[tuple[float, float, int, np.ndarray]]:
    """Return item centres and their exact visible contours."""
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    result: list[tuple[float, float, int, np.ndarray]] = []
    for index in range(1, count):
        x, y, width, height, area = (int(v) for v in stats[index])
        if not 90 <= area <= 4_500:
            continue
        if not (12 <= width <= 100 and 12 <= height <= 100):
            continue
        cx, cy = centroids[index]
        component = np.uint8(labels == index) * 255
        contours, _hierarchy = cv2.findContours(
            component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        result.append((float(cx), float(cy), area, contour))
    return result


def mannequin_inner_edges(
    frame_bgr: np.ndarray,
    target_points: dict[str, tuple[float, float]],
    scale: float,
) -> dict[str, float | None]:
    """Find the inner edge of a dark, tall mannequin on each side.

    Only the hood/body silhouette outside the player area is considered.  Thin
    horizontal shapes are rejected, so the player's current weapon is ignored.
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    dark = cv2.inRange(gray, 0, 76)
    dark = cv2.morphologyEx(
        dark, cv2.MORPH_OPEN, np.ones((3, 3), dtype=np.uint8)
    )
    dark = cv2.morphologyEx(
        dark, cv2.MORPH_CLOSE, np.ones((5, 5), dtype=np.uint8)
    )

    height, width = gray.shape
    result: dict[str, float | None] = {"A": None, "D": None}
    min_area = 0.00055 * scale * scale
    max_area = 0.035 * scale * scale
    min_height = 0.065 * scale
    max_height = 0.23 * scale
    min_width = 0.025 * scale
    max_width = 0.22 * scale

    for key in ("A", "D"):
        tx, ty = target_points[key]
        y0 = max(0, int(ty - 0.18 * scale))
        y1 = min(height, int(ty + 0.11 * scale))
        if key == "A":
            x0 = max(0, int(0.04 * width))
            x1 = min(width, int(tx - 0.018 * scale))
        else:
            x0 = max(0, int(tx + 0.018 * scale))
            x1 = min(width, int(0.96 * width))
        if x1 <= x0 or y1 <= y0:
            continue

        roi = dark[y0:y1, x0:x1]
        count, _labels, stats, centroids = cv2.connectedComponentsWithStats(roi, 8)
        candidates: list[float] = []
        for index in range(1, count):
            x, y, blob_w, blob_h, area = (int(v) for v in stats[index])
            if not min_area <= area <= max_area:
                continue
            if not min_width <= blob_w <= max_width:
                continue
            if not min_height <= blob_h <= max_height:
                continue
            if blob_w / max(blob_h, 1) > 2.35:
                continue
            global_top = y0 + y
            # A mannequin has a hood well above the horizontal weapon line.
            if global_top > ty - 0.045 * scale:
                continue
            if key == "A":
                candidates.append(float(x0 + x + blob_w))
            else:
                candidates.append(float(x0 + x))

        if candidates:
            result[key] = max(candidates) if key == "A" else min(candidates)

    return result


def mannequin_club_centres(
    frame_bgr: np.ndarray,
    target_points: dict[str, tuple[float, float]],
    scale: float,
) -> dict[str, float | None]:
    """Locate the fixed tan/brown head on a mannequin's long stick.

    The search corridor is only on the outside of A/D.  The player's weapon is
    closer to the hero and, even if it enters a corridor, its first movement is
    outward and is rejected by the motion-state logic in the main loop.
    """
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    club = cv2.inRange(hsv, (5, 70, 35), (30, 255, 250))
    club = cv2.morphologyEx(
        club, cv2.MORPH_CLOSE, np.ones((5, 5), dtype=np.uint8)
    )

    height, width = club.shape
    result: dict[str, float | None] = {"A": None, "D": None}
    min_area = 0.00012 * scale * scale
    max_area = 0.018 * scale * scale

    for key in ("A", "D"):
        tx, ty = target_points[key]
        y0 = max(0, int(ty - 0.075 * scale))
        y1 = min(height, int(ty + 0.075 * scale))
        if key == "A":
            x0 = max(0, int(tx - 0.22 * scale))
            x1 = min(width, int(tx - 0.004 * scale))
        else:
            x0 = max(0, int(tx + 0.004 * scale))
            x1 = min(width, int(tx + 0.22 * scale))
        if x1 <= x0 or y1 <= y0:
            continue

        roi = club[y0:y1, x0:x1]
        count, _labels, stats, centroids = cv2.connectedComponentsWithStats(roi, 8)
        candidates: list[float] = []
        for index in range(1, count):
            _x, _y, blob_w, blob_h, area = (int(v) for v in stats[index])
            if not min_area <= area <= max_area:
                continue
            if not 0.014 * scale <= blob_h <= 0.09 * scale:
                continue
            if not 0.012 * scale <= blob_w <= 0.14 * scale:
                continue
            if blob_w / max(blob_h, 1) > 5.0:
                continue
            cx, _cy = centroids[index]
            candidates.append(float(x0 + cx))

        if candidates:
            result[key] = max(candidates) if key == "A" else min(candidates)

    return result


def mannequin_wrap_inner_edges(
    frame_bgr: np.ndarray,
    target_points: dict[str, tuple[float, float]],
    scale: float,
) -> dict[str, float | None]:
    """Find the inner edge of the mannequin stick (white wrap plus wood)."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    # The wrapping is bright and nearly colourless.  Its black stripes split
    # the white area, so a horizontal closing kernel joins the visible pieces.
    wrap = cv2.inRange(hsv, (0, 0, 125), (179, 82, 255))
    wrap = cv2.morphologyEx(
        wrap, cv2.MORPH_CLOSE, np.ones((3, 9), dtype=np.uint8)
    )
    height, width = wrap.shape
    result: dict[str, float | None] = {"W": None, "A": None, "D": None}
    min_area = 0.00010 * scale * scale
    max_area = 0.010 * scale * scale

    for key in ("A", "D"):
        tx, ty = target_points[key]
        y0 = max(0, int(ty - 0.075 * scale))
        y1 = min(height, int(ty + 0.075 * scale))
        if key == "A":
            x0 = max(0, int(tx - 0.25 * scale))
            x1 = min(width, int(tx + 0.03 * scale))
        else:
            x0 = max(0, int(tx - 0.03 * scale))
            x1 = min(width, int(tx + 0.25 * scale))
        if x1 <= x0 or y1 <= y0:
            continue

        roi = wrap[y0:y1, x0:x1]
        count, _labels, stats, centroids = cv2.connectedComponentsWithStats(roi, 8)
        candidates: list[float] = []
        for index in range(1, count):
            x, _y, blob_w, blob_h, area = (int(v) for v in stats[index])
            if not min_area <= area <= max_area:
                continue
            if not 0.020 * scale <= blob_w <= 0.12 * scale:
                continue
            if not 0.007 * scale <= blob_h <= 0.045 * scale:
                continue
            if blob_w / max(blob_h, 1) < 1.65:
                continue
            _cx, cy = centroids[index]
            if abs((y0 + cy) - ty) > 0.024 * scale:
                continue
            # Return the edge facing the hero, not the centre.  Contact occurs
            # when this edge reaches the outside rim of the white target circle.
            candidates.append(float(x0 + x + blob_w) if key == "A" else float(x0 + x))

        if candidates:
            result[key] = max(candidates) if key == "A" else min(candidates)

    # The upper mannequin approaches vertically. Its wrapping may rotate, so
    # accept either a horizontal or vertical bright strip. The W circle itself
    # is kept outside this search rectangle.
    tx, ty = target_points["W"]
    x0 = max(0, int(tx - 0.15 * scale))
    x1 = min(width, int(tx + 0.15 * scale))
    y0 = max(0, int(ty - 0.32 * scale))
    y1 = min(height, int(ty - 0.055 * scale))
    if x1 > x0 and y1 > y0:
        roi = wrap[y0:y1, x0:x1]
        count, _labels, stats, centroids = cv2.connectedComponentsWithStats(roi, 8)
        candidates: list[float] = []
        for index in range(1, count):
            _x, y, blob_w, blob_h, area = (int(v) for v in stats[index])
            if not min_area <= area <= max_area:
                continue
            if not 0.008 * scale <= blob_w <= 0.13 * scale:
                continue
            if not 0.008 * scale <= blob_h <= 0.13 * scale:
                continue
            cx, _cy = centroids[index]
            if abs((x0 + cx) - tx) > 0.14 * scale:
                continue
            candidates.append(float(y0 + y + blob_h))
        if candidates:
            result["W"] = max(candidates)
    return result


@dataclass
class LaneState:
    key: str
    nx: float
    ny: float
    last_press: float = -100.0
    release_at: float | None = None
    presses: int = 0
    occupied: bool = False
    item_clear_since: float | None = None
    item_last_distance: float = math.inf
    item_last_time: float = 0.0
    item_closing_speed: float = 0.0
    stick_triggered: bool = False
    stick_armed: bool = False
    stick_missing_since: float | None = None
    stick_clear_since: float | None = None
    stick_last_edge: float | None = None
    stick_last_time: float = 0.0
    stick_speed: float = 0.0
    approach_frame: np.ndarray | None = field(default=None, repr=False)
    approach_time: float = 0.0
    approach_last_motion: float | None = None
    approach_started: float | None = None
    approach_counts: deque[tuple[float, int]] = field(
        default_factory=deque, repr=False
    )
    gate_frame: np.ndarray | None = field(default=None, repr=False)
    gate_time: float = 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Automatically press W/A/D when an apple enters its circle."
    )
    parser.add_argument("--window-title", default=WINDOW_TITLE)
    parser.add_argument(
        "--trigger-radius",
        type=float,
        default=0.025,
        help="Inner target radius as a fraction of the smaller client dimension (default: 0.025).",
    )
    parser.add_argument(
        "--release-radius",
        type=float,
        default=0.065,
        help="Radius an item must leave before the lane is re-armed (default: 0.065).",
    )
    parser.add_argument("--hold-ms", type=float, default=16.0)
    parser.add_argument("--cooldown-ms", type=float, default=120.0)
    parser.add_argument(
        "--mannequin-lead",
        type=float,
        default=0.084,
        help="Marker distance corresponding to stick contact (default: 0.084).",
    )
    parser.add_argument("--start-paused", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect and print hits without sending keyboard input.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 0.01 <= args.trigger_radius <= 0.10:
        print("--trigger-radius must be between 0.01 and 0.10")
        return 2
    if args.release_radius <= args.trigger_radius:
        print("--release-radius must be larger than --trigger-radius")
        return 2

    backend = None
    if sys.platform == "win32":
        enable_dpi_awareness()
        hwnd = find_game_window(args.window_title)
    elif sys.platform.startswith("linux"):
        try:
            if __package__:
                from .linux_platform import LinuxPlatform
            else:
                from linux_platform import LinuxPlatform

            backend = LinuxPlatform(args.window_title)
            hwnd = backend.find_game_window()
        except Exception as error:
            print(f"Could not initialize the Linux/X11 backend: {error}")
            print("Check the X11 display and install sword/requirements.txt.")
            return 2
    else:
        print(f"Unsupported platform: {sys.platform}")
        return 2

    if hwnd is None:
        print(f'Game window containing "{args.window_title}" was not found.')
        print("Start the game first, then run this script again.")
        if backend is not None:
            backend.close()
        return 1

    process_id = (
        backend.get_process_id(hwnd)
        if backend is not None
        else get_window_process_id(hwnd)
    )

    def game_is_focused() -> bool:
        if backend is not None:
            return backend.is_focused(hwnd)
        return user32.GetForegroundWindow() == hwnd

    def send_key(key: str, is_down: bool) -> None:
        if args.dry_run:
            return
        if backend is not None:
            backend.send_key(key, is_down)
        else:
            send_scan_code(SCAN_CODES[key], is_down)

    def hotkey_pressed(key: str) -> bool:
        if backend is not None:
            return backend.consume_hotkey(key)
        virtual_key = VK_F8 if key == "F8" else VK_F9
        return bool(user32.GetAsyncKeyState(virtual_key) & 1)

    def current_client_box() -> dict[str, int] | None:
        if backend is not None:
            return backend.get_client_box(hwnd)
        return get_client_box(hwnd)

    wayland_capture = None

    lanes = [LaneState(key, *TARGET_LAYOUT[key]) for key in ("W", "A", "D")]
    lanes_by_key = {lane.key: lane for lane in lanes}
    # Start safely paused. F8 enables the automation when the player is ready.
    active = False
    held_keys: set[str] = set()
    last_box_update = 0.0
    client_box: dict[str, int] | None = None
    focus_reported = False
    frames = 0
    fps_started = time.perf_counter()
    learned_item_speed = 0.0
    black_capture_since: float | None = None
    capture_unavailable = False
    memory_reader = None
    memory_reader_active = False
    memory_press_lock = threading.Lock()

    def release_memory_key(key: str) -> None:
        try:
            send_key(key, False)
        except Exception:
            pass

    def on_memory_press(key: str, source: str) -> None:
        """Press immediately on a one-shot event read from the game."""
        nonlocal active
        if not active or not game_is_focused():
            return
        lane = lanes_by_key[key]
        now_memory = time.perf_counter()
        with memory_press_lock:
            # A rising edge is already one-shot.  This tiny guard also covers
            # a simultaneous screen event without delaying the next attack.
            if now_memory - lane.last_press < 0.080:
                return
            lane.last_press = now_memory
            lane.presses += 1
            print(
                f"{key}  {source}-memory #{lane.presses}",
                flush=True,
            )
            send_key(key, True)
            release_timer = threading.Timer(
                args.hold_ms / 1000.0,
                release_memory_key,
                args=(key,),
            )
            release_timer.daemon = True
            release_timer.start()

    def on_memory_parry(key: str) -> None:
        on_memory_press(key, "mannequin")

    def on_memory_item(key: str) -> None:
        on_memory_press(key, "item")

    try:
        from mannequin_memory import MannequinMemoryReader

        memory_reader = MannequinMemoryReader(
            process_id, on_memory_parry, on_memory_item
        )
    except Exception as error:
        print(f"Memory reader unavailable: {error}")
        if backend is not None:
            from wine_frida import print_attach_hint

            print_attach_hint(error)
        print("Using the visual mannequin detector as a fallback.")

    # Screen capture is only the fallback, so Wayland's sharing dialog appears
    # only when the memory reader could not be attached.
    if memory_reader is None and backend is not None and backend.uses_wayland:
        box = current_client_box()
        if box is None:
            print("The game window has no capturable client area yet.")
            backend.close()
            return 2
        print(
            "Wayland capture: approve the screen-sharing dialog and select "
            "the Swords & Souls Neverseen window."
        )
        try:
            if __package__:
                from .wayland_capture import WaylandWindowCapture
            else:
                from wayland_capture import WaylandWindowCapture

            wayland_capture = WaylandWindowCapture(
                box["width"], box["height"]
            )
            print(
                f"Wayland capture connected: "
                f"{wayland_capture.width}x{wayland_capture.height}."
            )
        except Exception as error:
            print(f"Could not start Wayland screen capture: {error}")
            backend.close()
            return 2

    print("Swords & Souls helper v27 (exact memory timing) is ready.")
    print("F8: pause/resume | F9: quit")
    print("DRY RUN: keys will not be sent." if args.dry_run else "Automation is PAUSED. Press F8 to start.")

    try:
        capture_context = (
            nullcontext(None) if wayland_capture is not None else mss.MSS()
        )
        with capture_context as x11_capture:
            while True:
                now = time.perf_counter()

                if hotkey_pressed("F9"):
                    break
                if hotkey_pressed("F8"):
                    active = not active
                    print("Automation is ON." if active else "Automation is PAUSED.")

                # Release held keys without blocking the capture loop.
                for lane in lanes:
                    if lane.release_at is not None and now >= lane.release_at:
                        if lane.key in held_keys:
                            send_key(lane.key, False)
                            held_keys.discard(lane.key)
                        lane.release_at = None

                if now - last_box_update >= 0.5:
                    client_box = current_client_box()
                    last_box_update = now

                focused = game_is_focused()
                wanted_memory_state = bool(active and focused and client_box is not None)
                if (
                    memory_reader is not None
                    and wanted_memory_state != memory_reader_active
                ):
                    try:
                        memory_reader.set_active(wanted_memory_state)
                        memory_reader_active = wanted_memory_state
                    except Exception as error:
                        print(f"Memory reader stopped: {error}")
                        try:
                            memory_reader.close()
                        except Exception:
                            pass
                        memory_reader = None
                        memory_reader_active = False
                if not active or client_box is None or not focused:
                    if active and not focused and not focus_reported:
                        print("Waiting for the game window to be focused...")
                        focus_reported = True
                    time.sleep(0.02)
                    continue
                if focus_reported:
                    print("Game focused; automation resumed.")
                    focus_reported = False

                # With the memory reader connected, both mannequins and flying
                # items produce their own one-shot events.  Skipping the screen
                # detector removes pixel/FPS latency and its duplicate-event
                # failure mode.  The visual path below remains a fallback when
                # memory attachment is unavailable.
                if memory_reader is not None:
                    frames += 1
                    if now - fps_started >= 5.0:
                        print("Memory mode active.", flush=True)
                        frames = 0
                        fps_started = now
                    time.sleep(0.001)
                    continue

                frame = (
                    wayland_capture.grab()
                    if wayland_capture is not None
                    else np.asarray(x11_capture.grab(client_box))[:, :, :3]
                )
                if backend is not None and not np.any(frame):
                    if black_capture_since is None:
                        black_capture_since = now
                    elif now - black_capture_since >= 1.5:
                        print(
                            "ERROR: screen capture returned only black pixels. "
                            "Check that the sharing dialog selected the game window.",
                            flush=True,
                        )
                        if wayland_capture is None:
                            print(
                                "MSS cannot capture the Wayland-composited frame; "
                                "the Wayland portal backend was not active.",
                                flush=True,
                            )
                        capture_unavailable = True
                        break
                else:
                    black_capture_since = None

                height, width = frame.shape[:2]
                scale = min(width, height)
                trigger_px = args.trigger_radius * scale
                release_px = args.release_radius * scale

                # Crop once around all three targets.  This avoids analysing the
                # decorative red parts elsewhere in the scene and keeps latency low.
                target_points = {
                    lane.key: (lane.nx * width, lane.ny * height) for lane in lanes
                }
                margin = int(release_px + 70)
                left = max(0, int(min(p[0] for p in target_points.values())) - margin)
                right = min(width, int(max(p[0] for p in target_points.values())) + margin)
                top = max(0, int(min(p[1] for p in target_points.values())) - margin)
                bottom = min(height, int(max(p[1] for p in target_points.values())) + margin)

                centres = object_centres(red_mask(frame[top:bottom, left:right]))
                centres = [
                    (
                        x + left,
                        y + top,
                        area,
                        contour + np.array([[[left, top]]], dtype=contour.dtype),
                    )
                    for x, y, area, contour in centres
                ]
                mannequin_edges = (
                    mannequin_wrap_inner_edges(frame, target_points, scale)
                    if memory_reader is None
                    else {"W": None, "A": None, "D": None}
                )

                for lane in lanes:
                    tx, ty = target_points[lane.key]
                    top_predicted_contact = False
                    top_motion_count = 0
                    distances: list[float] = []
                    for x, y, _area, contour in centres:
                        if lane.key == "W":
                            valid = (
                                y <= ty + 0.020 * scale
                                and abs(x - tx) <= 0.16 * scale
                            )
                        elif lane.key == "A":
                            valid = (
                                x <= tx + 0.020 * scale
                                and abs(y - ty) <= 0.16 * scale
                            )
                        else:
                            valid = (
                                x >= tx - 0.020 * scale
                                and abs(y - ty) <= 0.16 * scale
                            )
                        if valid:
                            signed_distance = cv2.pointPolygonTest(
                                contour, (float(tx), float(ty)), True
                            )
                            # A negative result means the target centre is
                            # outside the object.  Its magnitude is the exact
                            # distance to the nearest visible object pixel.
                            distances.append(max(0.0, -signed_distance))
                    nearest = min(distances, default=math.inf)

                    # Estimate the approach speed from consecutive contour
                    # distances. Fast blue-trail objects can cross many pixels
                    # between frames, so compensate only for that measured
                    # motion instead of making every object trigger early.
                    if math.isfinite(nearest):
                        elapsed_item = now - lane.item_last_time
                        if (
                            math.isfinite(lane.item_last_distance)
                            and 0.001 <= elapsed_item <= 0.10
                        ):
                            instant_speed = (
                                lane.item_last_distance - nearest
                            ) / elapsed_item
                            if instant_speed > 0:
                                lane.item_closing_speed = max(
                                    instant_speed,
                                    0.55 * lane.item_closing_speed,
                                )
                                # Preserve the recent game-speed level across
                                # objects. The next fast object then receives
                                # useful prediction from its very first frame.
                                if instant_speed <= 4.0 * scale:
                                    if learned_item_speed <= 0:
                                        learned_item_speed = instant_speed
                                    elif instant_speed > learned_item_speed:
                                        learned_item_speed = (
                                            0.60 * learned_item_speed
                                            + 0.40 * instant_speed
                                        )
                                    else:
                                        learned_item_speed = (
                                            0.98 * learned_item_speed
                                            + 0.02 * instant_speed
                                        )
                            else:
                                lane.item_closing_speed *= 0.45
                        lane.item_last_distance = nearest
                        lane.item_last_time = now

                    prediction_lead = min(
                        0.120 * scale,
                        max(
                            0.0,
                            lane.item_closing_speed,
                            0.90 * learned_item_speed,
                        )
                        * 0.045,
                    )
                    item_trigger_px = trigger_px + prediction_lead

                    # Exact fast logic from the first version for apples and
                    # all other round red objects.
                    if nearest > release_px:
                        if lane.item_clear_since is None:
                            lane.item_clear_since = now
                        if now - lane.item_clear_since >= 0.10:
                            lane.occupied = False
                            if not math.isfinite(nearest):
                                lane.item_last_distance = math.inf
                                lane.item_closing_speed = 0.0
                    else:
                        lane.item_clear_since = None
                    item_cooldown = (
                        now - lane.last_press
                        >= max(args.cooldown_ms, 220.0) / 1000.0
                    )
                    if nearest <= item_trigger_px and not lane.occupied and item_cooldown:
                        lane.occupied = True
                        # The impact flash contains white/brown pixels.  Treat
                        # the whole impact as one event so the stick detector
                        # cannot generate a second press from that flash.
                        lane.stick_armed = False
                        lane.stick_triggered = True
                        lane.stick_clear_since = None
                        lane.last_press = now
                        lane.presses += 1
                        print(
                            f"{lane.key}  item #{lane.presses}  "
                            f"distance={nearest:.1f}px",
                            flush=True,
                        )
                        if not args.dry_run:
                            send_key(lane.key, True)
                            held_keys.add(lane.key)
                            lane.release_at = now + args.hold_ms / 1000.0

                    # When memory reading is active, mannequin attacks are
                    # handled by the game's own CanGetBlocked flag above.  Do
                    # not also run colour/motion heuristics: that was the
                    # source of late and duplicate presses in earlier builds.
                    if memory_reader is not None:
                        continue

                    # The airborne W mannequin comes diagonally from either
                    # side.  Motion of its dark hood/body above the hero arms
                    # the contact sensor early; the hero and their weapon are
                    # below this rectangle and cannot arm it.
                    if lane.key == "W":
                        ax0 = max(0, int(tx - 0.36 * scale))
                        ax1 = min(width, int(tx + 0.36 * scale))
                        ay0 = max(0, int(ty - 0.42 * scale))
                        ay1 = min(height, int(ty - 0.040 * scale))
                        approach = frame[ay0:ay1, ax0:ax1]
                        if approach.size and (
                            lane.approach_frame is None
                            or lane.approach_frame.shape != approach.shape
                        ):
                            lane.approach_frame = approach.copy()
                            lane.approach_time = now
                        elif approach.size and now - lane.approach_time >= 0.014:
                            difference = cv2.cvtColor(
                                cv2.absdiff(approach, lane.approach_frame),
                                cv2.COLOR_BGR2GRAY,
                            )
                            current_gray = cv2.cvtColor(approach, cv2.COLOR_BGR2GRAY)
                            moving_dark = (difference >= 18) & (current_gray <= 100)
                            motion_count = int(np.count_nonzero(moving_dark))
                            top_motion_count = motion_count
                            approach_red = red_mask(approach)
                            red_count = int(
                                np.count_nonzero(
                                    (difference >= 18) & (approach_red > 0)
                                )
                            )
                            red_ratio = red_count / max(motion_count, 1)
                            required_motion = max(
                                180, int(0.00120 * scale * scale)
                            )
                            # Apples contain proportionally much more moving
                            # red than the dark hood/body of a mannequin.
                            if motion_count >= required_motion and red_ratio < 0.34:
                                lane.approach_last_motion = now
                                if not lane.stick_triggered:
                                    lane.stick_armed = True
                                    if lane.approach_started is None:
                                        lane.approach_started = now

                            lane.approach_counts.append((now, motion_count))
                            while (
                                lane.approach_counts
                                and now - lane.approach_counts[0][0] > 0.12
                            ):
                                lane.approach_counts.popleft()

                            # Immediately before the blow, the hood/body makes
                            # a repeatable fast swing.  Detecting that surge is
                            # quicker than waiting for the rendered impact and
                            # automatically follows increases in game speed.
                            if (
                                lane.stick_armed
                                and lane.approach_started is not None
                                and red_ratio < 0.34
                                and motion_count >= max(
                                    300, int(0.00135 * scale * scale)
                                )
                                and lane.approach_counts
                            ):
                                approach_age = now - lane.approach_started
                                game_speed_factor = min(
                                    2.5,
                                    max(
                                        1.0,
                                        learned_item_speed / (1.25 * scale),
                                    ),
                                )
                                adaptive_top_delay = max(
                                    0.055,
                                    0.40 / game_speed_factor - 0.065,
                                )
                                recent_low = min(
                                    value for _stamp, value in lane.approach_counts
                                )
                                surge = motion_count - recent_low
                                swing_surge = (
                                    approach_age
                                    >= max(0.10, adaptive_top_delay - 0.02)
                                    and surge
                                    >= max(100, int(0.00060 * scale * scale))
                                )
                                # The game accepts a parry during the wind-up,
                                # well before the airborne stick reaches W.
                                timed_lead = approach_age >= adaptive_top_delay
                                if swing_surge or timed_lead:
                                    top_predicted_contact = True
                            lane.approach_frame = approach.copy()
                            lane.approach_time = now

                        if (
                            lane.approach_last_motion is not None
                            and now - lane.approach_last_motion >= 0.42
                            and not lane.stick_triggered
                        ):
                            lane.stick_armed = False
                            lane.approach_started = None
                        edge = None
                    else:
                        edge = mannequin_edges[lane.key]
                    stick_distance = math.inf
                    predicted_contact = False
                    if lane.key == "W":
                        lane.stick_speed = 0.0
                    elif edge is None:
                        if lane.stick_missing_since is None:
                            lane.stick_missing_since = now
                        if now - lane.stick_missing_since >= 0.22:
                            lane.stick_armed = False
                            lane.stick_last_edge = None
                            lane.stick_speed = 0.0
                    else:
                        lane.stick_missing_since = None
                        direction = 1.0 if lane.key == "A" else -1.0
                        stick_distance = direction * (tx - edge)
                        if lane.stick_last_edge is not None:
                            elapsed = now - lane.stick_last_time
                            if elapsed > 0:
                                lane.stick_speed = direction * (
                                    edge - lane.stick_last_edge
                                ) / elapsed
                                if lane.stick_speed > 0.10 * scale:
                                    lane.stick_armed = True
                        lane.stick_last_edge = edge
                        lane.stick_last_time = now

                        marker_contact = args.mannequin_lead * scale
                        if lane.stick_armed and lane.stick_speed > 0.10 * scale:
                            if stick_distance <= marker_contact:
                                predicted_contact = True
                            else:
                                eta = (
                                    stick_distance - marker_contact
                                ) / lane.stick_speed
                                predicted_contact = eta <= 0.105

                    # Direct contact sensor covering the complete circle.  It
                    # presses on the first moving stick pixels inside it.
                    ring_radius = 0.035 * scale
                    gate_x = tx
                    gate_y = ty
                    gate_half_w = max(14, int(0.043 * scale))
                    gate_half_h = max(14, int(0.043 * scale))
                    gx0 = max(0, int(gate_x) - gate_half_w)
                    gx1 = min(width, int(gate_x) + gate_half_w)
                    gy0 = max(0, int(gate_y) - gate_half_h)
                    gy1 = min(height, int(gate_y) + gate_half_h)
                    gate = frame[gy0:gy1, gx0:gx1]
                    gate_motion = False
                    if gate.size:
                        if lane.gate_frame is None or lane.gate_frame.shape != gate.shape:
                            lane.gate_frame = gate.copy()
                            lane.gate_time = now
                        elif now - lane.gate_time >= 0.010:
                            diff = cv2.absdiff(gate, lane.gate_frame)
                            changed = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY) >= 20
                            gate_hsv = cv2.cvtColor(gate, cv2.COLOR_BGR2HSV)
                            white = cv2.inRange(
                                gate_hsv, (0, 0, 120), (179, 75, 255)
                            ) > 0
                            brown = cv2.inRange(
                                gate_hsv, (5, 85, 25), (28, 255, 215)
                            ) > 0
                            local_cx = gate_x - gx0
                            local_cy = gate_y - gy0
                            yy, xx = np.ogrid[: gate.shape[0], : gate.shape[1]]
                            inside_circle = (
                                (xx - local_cx) ** 2 + (yy - local_cy) ** 2
                                <= ring_radius**2
                            )
                            moving_brown = changed & brown & inside_circle
                            moving_white = changed & white & inside_circle
                            brown_pixels = int(np.count_nonzero(moving_brown))
                            all_stick_pixels = brown_pixels + int(
                                np.count_nonzero(moving_white)
                            )
                            # The narrow colour ranges reject grass and the
                            # idle target ring, so only a few entering pixels
                            # are needed for genuine first contact.
                            gate_motion = brown_pixels >= 2 or all_stick_pixels >= 6
                            lane.gate_frame = gate.copy()
                            lane.gate_time = now

                    # An apple has priority; its glow must never be counted as
                    # a second stick event.
                    # The visual stick often appears only after the game's
                    # valid input moment. Keep the exact contact gate as a
                    # fallback, but also use the calibrated motion prediction.
                    stick_contact = lane.stick_armed and (
                        gate_motion or predicted_contact or top_predicted_contact
                    )
                    if nearest <= release_px:
                        stick_contact = False

                    if lane.stick_triggered:
                        if lane.key == "W":
                            quiet_limit = max(
                                120, int(0.00080 * scale * scale)
                            )
                            clear = (
                                now - lane.last_press >= 0.30
                                and top_motion_count < quiet_limit
                            )
                        else:
                            clear = edge is None and not gate_motion
                        if clear:
                            if lane.stick_clear_since is None:
                                lane.stick_clear_since = now
                            clear_delay = 0.06 if lane.key == "W" else 0.22
                            if now - lane.stick_clear_since >= clear_delay:
                                lane.stick_triggered = False
                                lane.stick_clear_since = None
                                if lane.key == "W":
                                    lane.approach_started = None
                                    lane.approach_counts.clear()
                        else:
                            lane.stick_clear_since = None

                    # Do not let the secondary detector interpret an apple's
                    # glow as another event immediately after the item hit.
                    stick_cooldown = now - lane.last_press >= 0.30
                    if stick_contact and not lane.stick_triggered and stick_cooldown:
                        lane.stick_triggered = True
                        lane.stick_armed = False
                        lane.stick_clear_since = None
                        lane.last_press = now
                        lane.presses += 1
                        print(
                            f"{lane.key}  stick #{lane.presses}  "
                            f"distance={stick_distance:.1f}px speed={lane.stick_speed:.0f}px/s",
                            flush=True,
                        )
                        if not args.dry_run:
                            send_key(lane.key, True)
                            held_keys.add(lane.key)
                            lane.release_at = now + args.hold_ms / 1000.0

                frames += 1
                if now - fps_started >= 5.0:
                    fps = frames / (now - fps_started)
                    print(f"Capture rate: {fps:.0f} FPS", flush=True)
                    frames = 0
                    fps_started = now

    except KeyboardInterrupt:
        pass
    except Exception as error:
        print(f"Capture/runtime error: {error}")
        capture_unavailable = True
    finally:
        if memory_reader is not None:
            try:
                memory_reader.set_active(False)
                memory_reader.close()
            except Exception:
                pass
        for key in held_keys:
            try:
                send_key(key, False)
            except Exception:
                pass
        if wayland_capture is not None:
            wayland_capture.close()
        if backend is not None:
            backend.close()

    print("Stopped.")
    return 2 if capture_unavailable else 0


if __name__ == "__main__":
    raise SystemExit(main())
