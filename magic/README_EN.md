# Swords & Souls Magic Auto

An automatic helper for the magic training mode in **Swords & Souls
Neverseen**.

## Features

- reads every active target type from memory: still targets, cloud fill rings,
  collectible stars, gravity targets, chasers, and pass-through rings;
- keeps the real cursor in the safe centre of the game window, matching the
  archery helper;
- completes the game's normal `starHit → GetScored(true) → Sleep` success path
  for every spawned target;
- automatically completes cloud rings and collects their stars;
- removes completed objects from the wave and returns them to their pools;
- only operates while the game window is in the foreground.

## Installation and launch

1. Install 64-bit Python for Windows.
2. Open a command prompt in this folder and run once:

   ```powershell
   pip install -r requirements.txt
   ```

3. Start the game and enter the magic training mode.
4. Double-click `START.bat`, then return to the game window.

The console should display:

```text
Swords & Souls magic helper v1 is ready.
```

## Controls

- `F8` — pause or resume automation;
- `F9` — stop the helper and close its window.

The helper does not modify the game's files. It attaches to the running game
process and works only while the script is running. Rewards and progress may
still be written to the normal save file by the game.

## Linux (Steam + Proton)

1. From the repository root, install the project requirements into its virtual
   environment (the Linux-only `python-xlib` dependency is included):

   ```sh
   .venv/bin/python -m pip install -r requirements.txt
   ```

2. Start the game through Proton and enter the magic training mode.
3. Run `bash magic/START_LINUX.sh` from the repository root, then return focus
   to the game and keep the mouse pointer inside the game window. Press `F8` to
   start or pause automation; `F9` exits the helper.

The Linux backend works on X11 and on Wayland desktops (the game itself runs
under XWayland). It moves the cursor and reads F8/F9 through X11/XTest. No screen
capture is involved. `linux_platform.py` is that X11 backend; `wine_frida.py`
prepares the Wine process so the Linux build of Frida can attach without
terminating the game. If attaching is denied, allow it until the next reboot
with `sudo sysctl kernel.yama.ptrace_scope=0`.
