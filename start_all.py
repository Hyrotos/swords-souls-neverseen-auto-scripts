"""Run all five training helpers at once.

Every helper only reacts while its own training mode is open in the game, so
with all of them running the matching automation plays whichever training is
entered.  F8 pauses or resumes all helpers together and F9 stops them.

The helpers share one process on purpose: Frida then uses a single agent in
the game for all of them.  Separate processes would each patch the same game
functions independently, which can crash the game when they detach.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import sys
import threading
import time


ROOT = Path(__file__).resolve().parent
WINDOW_TITLE = "Swords & Souls Neverseen"

# (folder, module with main(), console label)
HELPERS = [
    ("sword", "autoplayer", "sword"),
    ("archery", "autoarcher", "archery"),
    ("magic", "automagic", "magic"),
    ("shield", "autoshield", "shield"),
    ("agility", "autododge", "agility"),
]

# Status lines the helpers repeat while their training is not open.
REPEATED_STATUS = ("ready:", "ready.", "Memory mode active")
REPEAT_INTERVAL = 60.0


class Console:
    """Label each helper's output and thin out repeated status lines."""

    def __init__(self, stream) -> None:
        self._stream = stream
        self._lock = threading.Lock()
        self._pending: dict[int, str] = {}
        self._last_shown: dict[str, float] = {}

    def write(self, text: str) -> int:
        thread = threading.current_thread()
        with self._lock:
            lines = (self._pending.pop(thread.ident, "") + text).split("\n")
            self._pending[thread.ident] = lines.pop()
            for line in lines:
                self._emit(getattr(thread, "helper_label", None), line)
        return len(text)

    def _emit(self, label: str | None, line: str) -> None:
        if label is not None:
            line = f"[{label}] {line}"
        if any(marker in line for marker in REPEATED_STATUS):
            now = time.monotonic()
            if now - self._last_shown.get(line, -REPEAT_INTERVAL) < REPEAT_INTERVAL:
                return
            self._last_shown[line] = now
        self._stream.write(line + "\n")
        self._stream.flush()

    def flush(self) -> None:
        self._stream.flush()


def prepare_linux_game() -> bool:
    """Make the Wine game process attachable once, before any helper starts."""
    from linux_platform import LinuxPlatform
    from wine_frida import prepare_for_frida, print_attach_hint

    try:
        backend = LinuxPlatform(WINDOW_TITLE)
    except Exception as error:
        print(f"Could not initialize the Linux/X11 backend: {error}")
        print("Check the X11 display and install requirements.txt.")
        return False
    try:
        window = backend.find_game_window()
        if window is None:
            print("Game window was not found. Start the game first.")
            return False
        prepare_for_frida(backend.get_process_id(window))
    except Exception as error:
        print(f"Could not prepare the game process: {error}")
        print_attach_hint(error)
        return False
    finally:
        backend.close()
    return True


def main() -> int:
    for folder, _module, _label in HELPERS:
        sys.path.insert(0, str(ROOT / folder))
    # The helpers parse their own options; they all run with their defaults.
    sys.argv = sys.argv[:1]

    stop = threading.Event()
    if sys.platform.startswith("linux"):
        if not prepare_linux_game():
            return 1
        from linux_platform import LinuxPlatform

        consume_hotkey = LinuxPlatform.consume_hotkey

        def consume_hotkey_or_stop(self, key: str) -> bool:
            # Lets Ctrl+C end every helper through its normal F9 path.
            return (key == "F9" and stop.is_set()) or consume_hotkey(self, key)

        LinuxPlatform.consume_hotkey = consume_hotkey_or_stop
    elif sys.platform == "win32":
        # Applies the sword helper's Windows input fix, like START_FIXED.bat.
        importlib.import_module("autoplayer_fixed")

    sys.stdout = Console(sys.stdout)
    results: dict[str, object] = {}

    def run(module_name: str, label: str) -> None:
        try:
            results[label] = importlib.import_module(module_name).main()
        except Exception as error:
            results[label] = error
            print(f"Helper failed: {error}")

    print("Starting all helpers. F8: pause/resume all | F9: quit all")
    threads = []
    for _folder, module_name, label in HELPERS:
        thread = threading.Thread(
            target=run, args=(module_name, label), name=label, daemon=True
        )
        thread.helper_label = label
        thread.start()
        threads.append(thread)
        # Attach one helper after the other instead of all at once.
        time.sleep(0.5)

    try:
        while any(thread.is_alive() for thread in threads):
            time.sleep(0.1)
    except KeyboardInterrupt:
        stop.set()
        for thread in threads:
            thread.join(timeout=5.0)

    failed = [label for label, result in results.items() if result not in (0, None)]
    if failed:
        print("Helpers that did not run cleanly: " + ", ".join(failed))
        return 1
    print("All helpers stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
