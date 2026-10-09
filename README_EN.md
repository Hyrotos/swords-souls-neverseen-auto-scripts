# Swords & Souls Neverseen — Auto Training Scripts

[Русская версия](README.md)

A collection of five Python automation helpers for **Swords & Souls
Neverseen**. All modes support Windows and also Linux when the game is running through
Proton under X11/XWayland.

| Training mode | Folder | Launcher |
| --- | --- | --- |
| Sword | [`sword`](sword) | Windows: `START_FIXED.bat`; Linux/Proton: `START_LINUX.sh` |
| Archery | [`archery`](archery) | Windows: `START.bat`; Linux/Proton: `START_LINUX.sh` |
| Magic | [`magic`](magic) | Windows: `START.bat`; Linux/Proton: `START_LINUX.sh` |
| Shield | [`shield`](shield) | Windows: `START.bat`; Linux/Proton: `START_LINUX.sh` |
| Agility | [`agility`](agility) | Windows: `START.bat`; Linux/Proton: `START_LINUX.sh` |

The helpers read the running game's state through Frida and send normal keyboard
or mouse input. They do not modify the game files.

## Video demonstration

[▶ Watch or download the video (MP4, 98.34 MB)](https://github.com/ibadullaevrahil67-maker/swords-souls-neverseen-auto-scripts/releases/download/v1.0.0/swords-souls-auto-scripts-demo.mp4)

The video demonstrates dependency installation and all five automation scripts.

## Requirements

- 64-bit Windows 10 or Windows 11 for all modes, or Linux with Steam + Proton;
- the Steam version of Swords & Souls Neverseen;
- 64-bit Python 3.11 or 3.12;
- the game and helper must run under the same Windows user.

## Install Python

Download 64-bit Python from [python.org](https://www.python.org/downloads/windows/)
and enable **Add Python to PATH** during setup.

Alternatively, use Windows Package Manager:

```powershell
winget install -e --id Python.Python.3.12
```

Open a new terminal after installation and verify it:

```powershell
python --version
```

## Install dependencies

Open PowerShell or Command Prompt in the repository root and run:

```powershell
python -m pip install -r requirements.txt
```

On Linux, install the same requirements in the project virtual
environment; the Linux-only X11 dependency is selected automatically.

## Run

On Windows, start the game, enter the desired training mode, and run that
mode's BAT file. On Linux, start the game through Proton under
X11/XWayland and run that mode's launcher from the repository root, for
example `bash shield/START_LINUX.sh`.

To run all five helpers at once, use `START_ALL.bat` on Windows or
`bash START_ALL_LINUX.sh` on Linux. Each helper only reacts while its own
training mode is open, so the matching automation plays whichever training you
enter. Do not start several single-mode launchers side by side instead: on
Linux, helpers running as separate processes can crash the game when they stop.

Shared controls:

- `F8` — pause or resume automation;
- `F9` — stop the helper.

Each folder contains a dedicated `README_EN.md` with mode-specific details.

## Important

- The scripts depend on the current Unity/Mono game internals. A game update may
  require the memory readers to be adjusted.
- Use at your own risk and follow the rules applicable to the game and Steam.
