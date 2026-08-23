# Swords & Souls Archery Auto

An automatic helper for the archery training mode in **Swords & Souls
Neverseen**.

## Features

- reads the position and type of every active target from the game's memory;
- handles coins (`TargetCoin`) in the same way as all other targets;
- uses each target's exact in-game star position, including moving, rotating,
  rolling, and extending targets;
- starts and releases the bow through the normal game input path;
- assigns a target as soon as `Arrow.Reset` creates a real arrow;
- continuously updates `Arrow.crosshairPosition` to follow moving targets;
- keeps the cursor and bow at a safe central point while memory guidance aims
  the arrow, preventing clicks on pause and other edge controls;
- supports piercing arrows and redirects them toward targets farther along the
  depth axis;
- completes `starHit → GetScored(true) → Sleep` for every active target when a
  real arrow is released, guaranteeing its star or reward regardless of
  distance, movement, or physical trajectory;
- only operates while the game window is in the foreground.

## Installation and launch

1. Install 64-bit Python for Windows.
2. Open a command prompt in this folder and run once:

   ```powershell
   pip install -r requirements.txt
   ```

3. Start the game and enter the archery training mode.
4. Double-click `START.bat`, then return to the game window.

The console should display:

```text
Swords & Souls archery helper v9 is ready.
```

## Controls

- `F8` — pause or resume automation;
- `F9` — stop the helper and close its window.

## Files

- `autoarcher.py` — bow input and automation logic;
- `archery_memory.py` — target tracking and in-memory scoring logic;
- `START.bat` — launcher;
- `requirements.txt` — required Python packages.

The helper does not modify the game's files. It attaches to the running game
process and works only while the script is running. Rewards and progress
granted by the game may still be written to the normal save file. A future game
update may require the memory reader to be adjusted.
