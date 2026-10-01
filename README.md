# Dune Lektor PL - source code

Polish voice-over ("lektor") mod for **Dune: Awakening** (single-player, WPS Dune Launcher).
Nexus Mods: https://www.nexusmods.com/duneawakening/mods/47

This repository contains the full source of the code shipped in the mod. Voice recordings
(`Audio/*.opus`, AI voice based on MG-MAT, used with permission) and the subtitle lookup table
(`Scripts/lines.lua`, generated from the game's Polish subtitles) are not included.

## What the mod does

1. `mod/Scripts/main.lua` - UE4SS Lua script loaded by WPS Dune Launcher. It reads the subtitle /
   dialogue text currently shown in the game, looks it up in `lines.lua` and writes `PLAY <id>`
   into `Player/cmd.txt`. It also starts the player and forwards Mod Settings changes
   (`GLOSNOSC`, `SPEED`, `GRACE`).
2. `player/player.py` -> `DuneLektorPlayer.exe` - a small audio player. It watches `cmd.txt`,
   plays `Audio/<id>.opus` through WASAPI (sounddevice), applies volume / tempo, and exits
   when the game process (`DuneSandbox*Shipping*.exe`) closes.

The player **does not** connect to the network, does not modify game files and does not inject
into the game process. It only reads files from the mod folder and plays audio.

## Building DuneLektorPlayer.exe

Windows 10/11 x64, Python 3.12 x64:

```bat
build_player.bat
```

or manually:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean --onedir --noconsole --name DuneLektorPlayer ^
  --exclude-module scipy --exclude-module tkinter ^
  --collect-all soxr --collect-submodules audiotsm player\player.py
```

Output: `dist\DuneLektorPlayer\` - this folder is shipped as `Mods\DuneLektorPL\Player\`.

Test without the game: `DuneLektorPlayer.exe <mod.ini> --audio <Audio dir> --cmd cmd.txt --log player.log --test`,
then append `PLAY <id>` lines to `cmd.txt`.

## Credits

- Voice: MG-MAT (https://www.youtube.com/@MG-MAT), used with permission
- WPS Dune Launcher / Framework - Wasted Potential Studios; Mod Settings - NckDotCom; UE4SS
- Dune: Awakening (c) Funcom. Unofficial, free, non-commercial fan mod.
