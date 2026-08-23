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
