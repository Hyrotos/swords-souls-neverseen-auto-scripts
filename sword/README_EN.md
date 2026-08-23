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

1. Install 64-bit Python for Windows.
2. Open a command prompt in this folder and run once:

   ```powershell
   pip install -r requirements.txt
   ```

3. Start the game and enter the sword training mode.
4. Double-click `START_FIXED.bat`, then return to the game window.

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
