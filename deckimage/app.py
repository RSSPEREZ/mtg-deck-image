"""Interface gráfica (Tkinter) e modo linha de comando."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .build import filename_from_title, run
from .decklist import parse_lines


def open_file(path: Path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def asset(name: str) -> Path:
    """Arquivo em deckimage/assets, tanto rodando do código quanto do executável do PyInstaller."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "deckimage" / "assets" / name


def gui():
    from .gui import gui as run_gui
    run_gui(open_file, asset)


def cli(argv: list[str]):
    p = argparse.ArgumentParser(prog="deckimage", description="Gera a imagem de uma lista MTG; o arquivo leva o nome do título.")
    p.add_argument("--main", type=Path, help="arquivo .txt do main deck (pode conter 'Sideboard')")
    p.add_argument("--side", type=Path, help="arquivo .txt do sideboard")
    p.add_argument("--title", required=True, help="título do deck; também vira o nome do arquivo (Mono Green Elves -> mono_green_elves.jpg)")
    p.add_argument("--cols", type=int, default=8)
    p.add_argument("--format", default="premodern", help="filtro de legalidade (padrão premodern; 'none' desliga)")
    p.add_argument("--out", type=Path, help="padrão: ~/Downloads/<titulo>.jpg")
    a = p.parse_args(argv)
    try:
        filename_from_title(a.title)
    except ValueError as e:
        p.error(f"--title: {e}")
    if not a.main and not a.side:
        p.error("informe --main e/ou --side (ou rode sem argumentos para abrir a janela)")
    main, side, bad = parse_lines(a.main.read_text(encoding="utf-8")) if a.main else ([], [], [])
    if a.side:
        s2, _, bad2 = parse_lines(a.side.read_text(encoding="utf-8"))
        side += s2
        bad += bad2
    for b in bad:
        print(f"Linha ignorada: {b}")
    fmt = None if a.format.lower() in ("none", "") else a.format.lower()
    res = run(main, side, title=a.title, cols=a.cols, fmt=fmt, out_path=a.out,
              progress=lambda i, n, _name: print(f"\r  imagens {i}/{n}", end="", flush=True))
    print(f"\nSalvo em {res.path}")
    if res.corrected:
        print("Corrigidas por aproximação:", "; ".join(f"{i.name} -> {i.detail}" for i in res.corrected))
    if res.illegal:
        print(f"Removidas (fora do {fmt}):", "; ".join(f"{i.qty}x {i.name} ({i.detail})" for i in res.illegal))
    if res.missing:
        print("Não encontradas:", ", ".join(i.name for i in res.missing))
    return 1 if (res.missing or res.illegal) else 0


def main():
    if len(sys.argv) > 1:
        sys.exit(cli(sys.argv[1:]))
    gui()


if __name__ == "__main__":
    main()
