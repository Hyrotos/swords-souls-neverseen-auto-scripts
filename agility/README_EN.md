# Swords & Souls Agility Auto

Automatic dodging and counterattacking for the Swords & Souls Neverseen agility training.

The helper reads attack direction and animation speed directly from game memory. It predicts the game's hit-check moment and presses the correct WASD key at the last safe instant. When the mannequin opens a counterattack prompt, it reads the requested side and presses `A` or `D`. Training speed increases are handled automatically.

## Start

1. Start the game and enter agility training.
2. Double-click `START.bat`.
3. Focus the game window.

Controls:

- `F8` — pause or resume.
- `F9` — quit and close the helper window.

Install the dependency if needed:

```bat
pip install -r requirements.txt
```

Optional timing adjustment:

```bat
python autododge.py --lead-ms 32
```

A larger `lead-ms` dodges earlier. The default was calibrated against the game's real hit-check event.

## Linux (Steam + Proton)

1. From the repository root, install the project requirements into its virtual
   environment (the Linux-only `python-xlib` dependency is included):

   ```sh
   .venv/bin/python -m pip install -r requirements.txt
   ```

2. Start the game through Proton and enter the agility training mode.
3. Run `bash agility/START_LINUX.sh` from the repository root, then return focus
   to the game and keep the mouse pointer inside the game window. Press `F8` to
   start or pause automation; `F9` exits the helper.

The Linux backend works on X11 and on Wayland desktops (the game itself runs
under XWayland). It presses W/A/S/D and reads F8/F9 through X11/XTest. No screen
capture is involved. `linux_platform.py` is that X11 backend; `wine_frida.py`
prepares the Wine process so the Linux build of Frida can attach without
terminating the game. If attaching is denied, allow it until the next reboot
with `sudo sysctl kernel.yama.ptrace_scope=0`.
