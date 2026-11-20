@echo off
REM Gera dist\DeckImageMTG.exe — rodar no Windows com Python 3.10+ instalado (marcar "tcl/tk" no instalador)
cd /d "%~dp0"
if not exist .venv python -m venv .venv
.venv\Scripts\pip install -q -r requirements.txt pyinstaller || exit /b 1
.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name DeckImageMTG --icon deckimage/assets/icon.ico --add-data "deckimage/assets;deckimage/assets" --hidden-import PIL._tkinter_finder run.py || exit /b 1
echo Pronto: dist\DeckImageMTG.exe
