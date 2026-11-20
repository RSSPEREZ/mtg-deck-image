@echo off
REM Gera dist\DeckImageMTG.exe — rodar no Windows com Python 3.10+ instalado (marcar "tcl/tk" no instalador)
cd /d "%~dp0"
if not exist .venv python -m venv .venv
.venv\Scripts\pip install -q -r requirements.txt pyinstaller || exit /b 1
.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name DeckImageMTG run.py || exit /b 1
echo Pronto: dist\DeckImageMTG.exe
