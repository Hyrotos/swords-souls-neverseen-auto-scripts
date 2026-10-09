# Swords & Souls Sword Auto

An automatic helper for the sword training mode in **Swords & Souls
Neverseen**.

## Features

- automatically presses `W`, `A`, and `D` for the corresponding attack lanes;
- reads regular flying objects and mannequin attacks from the game's memory;
- prevents duplicate key presses for the same attack;
- can fall back to visual detection if memory reading is unavailable;
- never sends keys while the game window is not in the foreground.

## Installation and launch

### Windows

1. Install 64-bit Python.
2. Open a command prompt in this folder and install the dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

3. Start the game and enter the sword training mode.
4. Double-click `START_FIXED.bat`, then return to the game window.

### Linux (Steam + Proton)

1. From the repository root, install the project requirements into its virtual
   environment (the Linux-only dependencies are included):

   ```sh
   .venv/bin/python -m pip install -r requirements.txt
   ```

2. Start the game through Proton and enter the sword training mode.
3. Run `bash sword/START_LINUX.sh` from the repository root, then return focus
   to the game. Press `F8` to start or pause automation; `F9` exits the helper.

The Linux backend works on X11 and on Wayland desktops (the game itself runs
under XWayland) and sends keys through XTest. Like on Windows, mannequin
attacks and flying items are read from the game's memory: `wine_frida.py`
prepares the Wine process so the Linux build of Frida can attach without
terminating the game. If attaching is denied, allow it until the next reboot
with `sudo sysctl kernel.yama.ptrace_scope=0`.

Screen capture is only a fallback for when the memory reader cannot attach. On
X11 it uses MSS; on Wayland it uses the ScreenCast portal and PipeWire, which
needs GStreamer with its PipeWire plugin (`gst-launch-1.0` and `pipewiresrc`)
and asks you to share the **Swords & Souls Neverseen** window.

The console should display:

```text
Swords & Souls helper v27 (exact memory timing) is ready.
```

## Controls

- `F8` — pause or resume automation;
- `F9` — stop the helper and close its window.

## Files

- `autoplayer.py` — main automation logic;
- `autoplayer_fixed.py` — correct input structures for 64-bit Windows;
- `mannequin_memory.py` — Unity/Mono training-state reader;
- `START_FIXED.bat` — recommended launcher;
- `requirements.txt` — required Python packages.

The helper does not modify the game's files. It attaches to the running game
process and works only while the script is running. A future game update may
require the memory reader to be adjusted.
