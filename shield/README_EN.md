# Swords & Souls Shield Auto

An automatic helper for the shield training mode in **Swords & Souls
Neverseen**.

## Features

- reads active projectiles directly from the game's memory;
- tracks the hero, shield, and nearest dangerous projectile;
- moves the shield automatically to block dangerous projectiles;
- awards one star for every successfully blocked regular projectile through
  the game's normal scoring path;
- allows stars to pass through the shield while their normal collision with
  the hero's body remains active;
- never moves the mouse while the game window is not in the foreground.

## Installation and launch

### Windows

1. Install 64-bit Python.
2. Open a command prompt in this folder and run once:

   ```powershell
   pip install -r requirements.txt
   ```

3. Start the game and enter the shield training mode.
4. Double-click `START.bat`, then return to the game window.

### Linux (Steam + Proton)

1. From the repository root, install the project requirements into its virtual
   environment (the Linux-only `python-xlib` dependency is included):

   ```sh
   .venv/bin/python -m pip install -r requirements.txt
   ```

2. Start the game through Proton and enter the shield training mode.
3. Run `bash shield/START_LINUX.sh` from the repository root, then return focus
   to the game. Press `F8` to start or pause automation; `F9` exits the helper.

The Linux backend works on X11 and on Wayland desktops (the game itself runs
under XWayland): it finds the game window through X11 and moves the mouse with
XTest. No screen capture is involved. The memory reader attaches to the Wine
process with the Linux build of Frida; `wine_frida.py` first corrects the
process's auxiliary vector through `ptrace`, because Frida otherwise aborts
inside a Wine process and takes the game down with it. If attaching is denied,
allow it until the next reboot with `sudo sysctl kernel.yama.ptrace_scope=0`.

The console should display:

```text
Swords & Souls shield helper v3 is ready.
```

## Controls

- `F8` — pause or resume automation;
- `F9` — stop the helper and close its window.

## Files

- `autoshield.py` — automatic shield movement;
- `shield_memory.py` — projectile and training-state reader;
- `linux_platform.py` — X11/XWayland window, hotkey, and mouse backend;
- `wine_frida.py` — prepares the Wine/Proton game process for Frida;
- `START.bat` — Windows launcher;
- `START_LINUX.sh` — Linux launcher;
- `requirements.txt` — required Python packages.

The helper does not modify the game's files. It attaches to the running game
process and works only while the script is running. A future game update may
require the memory reader to be adjusted.
