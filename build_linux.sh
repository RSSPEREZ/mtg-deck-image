#!/usr/bin/env bash
# Gera dist/DeckImageMTG (executável único para Linux)
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt pyinstaller
.venv/bin/pyinstaller --noconfirm --onefile --windowed --name DeckImageMTG --icon deckimage/assets/icon.ico --add-data "deckimage/assets:deckimage/assets" --hidden-import PIL._tkinter_finder run.py
echo "Pronto: dist/DeckImageMTG"
