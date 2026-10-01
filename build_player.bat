@echo off
rem Builds DuneLektorPlayer.exe exactly like the released mod (PyInstaller onedir, no console).
rem Requires Python 3.12 x64 on PATH.
python -m venv .venv || exit /b 1
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt || exit /b 1
python -m PyInstaller --noconfirm --clean --onedir --noconsole --name DuneLektorPlayer ^
  --exclude-module scipy --exclude-module tkinter ^
  --collect-all soxr --collect-submodules audiotsm ^
  player\player.py || exit /b 1
echo.
echo Done: dist\DuneLektorPlayer\DuneLektorPlayer.exe  (copy the whole folder to Mods\DuneLektorPL\Player)
