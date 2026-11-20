"""Interface gráfica (Tkinter) e modo linha de comando."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .build import DEFAULT_SIZE, MAX_SIDE, MIN_SIDE, filename_from_title, parse_size, run
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


def _size_arg(s: str):
    try:
        return parse_size(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def _cols_arg(s: str):
    if s.strip().lower() == "auto":
        return None
    try:
        n = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"colunas inválidas: '{s}' (use um número de 1 a 40 ou 'auto')")
    if not 1 <= n <= 40:
        raise argparse.ArgumentTypeError(f"colunas fora do limite: {n} (use de 1 a 40 ou 'auto')")
    return n


def cli(argv: list[str]):
    p = argparse.ArgumentParser(prog="deckimage", description="Gera a imagem de uma lista MTG; o arquivo leva o nome do título.")
    p.add_argument("--main", type=Path, help="arquivo .txt do main deck (pode conter 'Sideboard')")
    p.add_argument("--side", type=Path, help="arquivo .txt do sideboard")
    p.add_argument("--title", required=True, help="título do deck; também vira o nome do arquivo (Mono Green Elves -> mono_green_elves.jpg)")
    p.add_argument("--size", type=_size_arg, default=DEFAULT_SIZE, metavar="TAMANHO",
                   help=f"tamanho da imagem: LARGURAxALTURA em pixels ({MIN_SIDE} a {MAX_SIDE} por lado), "
                        "ex.: 1920x1080 (padrão), 1080x1350; ou 'original' (largura pelas colunas, altura livre)")
    p.add_argument("--cols", type=_cols_arg, default=None, metavar="N|auto",
                   help="colunas de cartas: número (1 a 40) ou 'auto' (padrão: a que deixa a carta maior; "
                        "em 'original', 8)")
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
    res = run(main, side, title=a.title, cols=a.cols, size=a.size, fmt=fmt, out_path=a.out,
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
