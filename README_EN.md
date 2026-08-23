# Swords & Souls Neverseen — Auto Training Scripts

[Русская версия](README.md)

A collection of five Python automation helpers for **Swords & Souls
Neverseen** on Windows.

| Training mode | Folder | Launcher |
| --- | --- | --- |
| Sword | [`sword`](sword) | `sword/START_FIXED.bat` |
| Archery | [`archery`](archery) | `archery/START.bat` |
| Magic | [`magic`](magic) | `magic/START.bat` |
| Shield | [`shield`](shield) | `shield/START.bat` |
| Agility | [`agility`](agility) | `agility/START.bat` |

The helpers read the running game's state through Frida and send normal keyboard
or mouse input. They do not modify the game files.

## Video demonstration

[▶ Watch or download the video (MP4, 98.34 MB)](https://github.com/ibadullaevrahil67-maker/swords-souls-neverseen-auto-scripts/releases/download/v1.0.0/swords-souls-auto-scripts-demo.mp4)

The video demonstrates dependency installation and all five automation scripts.

## Requirements

- 64-bit Windows 10 or Windows 11;
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

Open PowerShell or Command Prompt in any location and run:

```powershell
python -m pip install "frida>=17.2,<18" "mss>=10.0" "numpy>=2.0" "opencv-python>=4.10"
```

## Run

1. Start the game and enter the desired training mode.
2. Open the desired training folder and run the BAT file inside it.
3. Return to the game window.

Shared controls:

- `F8` — pause or resume automation;
- `F9` — stop the helper.

Each folder contains a dedicated `README_EN.md` with mode-specific details.

## Important

- The scripts depend on the current Unity/Mono game internals. A game update may
  require the memory readers to be adjusted.
- Use at your own risk and follow the rules applicable to the game and Steam.
