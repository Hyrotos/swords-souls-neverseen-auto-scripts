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

1. Install 64-bit Python for Windows.
2. Open a command prompt in this folder and run once:

   ```powershell
   pip install -r requirements.txt
   ```

3. Start the game and enter the shield training mode.
4. Double-click `START.bat`, then return to the game window.

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
- `START.bat` — launcher;
- `requirements.txt` — required Python packages.

The helper does not modify the game's files. It attaches to the running game
process and works only while the script is running. A future game update may
require the memory reader to be adjusted.
