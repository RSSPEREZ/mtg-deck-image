"""Interface gráfica (Tkinter) e modo linha de comando."""
from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from .build import FORMATS, downloads_dir, run
from .decklist import parse_lines

EXAMPLE_MAIN = """4 Lightning Bolt
4 Delver of Secrets
4 Brainstorm
4 Ponder
2 Counterspell
18 Island
"""
EXAMPLE_SIDE = """2 Pyroblast
3 Red Elemental Blast
"""


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


def gui():
    import tkinter as tk
    from tkinter import messagebox, ttk
    from tkinter.scrolledtext import ScrolledText

    root = tk.Tk()
    root.title("Deck Image – MTG (projeto não oficial)")
    root.geometry("900x720")
    root.minsize(700, 560)

    events: queue.Queue = queue.Queue()

    top = ttk.Frame(root, padding=10)
    top.pack(fill="x")
    ttk.Label(top, text="Título (opcional):").pack(side="left")
    title_var = tk.StringVar()
    ttk.Entry(top, textvariable=title_var, width=40).pack(side="left", padx=6)
    ttk.Label(top, text="Colunas:").pack(side="left", padx=(12, 0))
    cols_var = tk.IntVar(value=8)
    ttk.Spinbox(top, from_=3, to=15, textvariable=cols_var, width=4).pack(side="left", padx=6)
    ttk.Label(top, text="Formato:").pack(side="left", padx=(12, 0))
    fmt_var = tk.StringVar(value="premodern")
    ttk.Combobox(top, textvariable=fmt_var, values=FORMATS + ["(sem filtro)"], state="readonly",
                 width=13).pack(side="left", padx=6)

    lists = ttk.Frame(root, padding=(10, 0))
    lists.pack(fill="both", expand=True)
    lists.columnconfigure(0, weight=3)
    lists.columnconfigure(1, weight=2)
    lists.rowconfigure(1, weight=1)
    ttk.Label(lists, text="Main deck  (ex.: 4 Lightning Bolt)").grid(row=0, column=0, sticky="w")
    ttk.Label(lists, text="Sideboard").grid(row=0, column=1, sticky="w", padx=(10, 0))
    main_txt = ScrolledText(lists, height=20, undo=True, font=("TkFixedFont", 11))
    side_txt = ScrolledText(lists, height=20, undo=True, font=("TkFixedFont", 11))
    main_txt.grid(row=1, column=0, sticky="nsew")
    side_txt.grid(row=1, column=1, sticky="nsew", padx=(10, 0))
    main_txt.insert("1.0", EXAMPLE_MAIN)
    side_txt.insert("1.0", EXAMPLE_SIDE)

    bottom = ttk.Frame(root, padding=10)
    bottom.pack(fill="x")
    btn = ttk.Button(bottom, text="Gerar final.jpg")
    btn.pack(side="left")
    pbar = ttk.Progressbar(bottom, mode="determinate")
    pbar.pack(side="left", fill="x", expand=True, padx=10)
    ttk.Label(bottom, text=f"Salva em: {downloads_dir() / 'final.jpg'}", foreground="gray").pack(side="left")

    log_txt = ScrolledText(root, height=8, state="disabled", font=("TkFixedFont", 10))
    log_txt.pack(fill="both", padx=10, pady=(0, 10))

    def log(msg: str):
        events.put(("log", msg))

    def worker(main_text, side_text, title, cols, fmt):
        try:
            main, side_extra, bad1 = parse_lines(main_text)
            side, _, bad2 = parse_lines(side_text)
            side = side_extra + side  # "Sideboard" colado no campo do main também vale
            for b in bad1 + bad2:
                log(f"Linha ignorada (não entendi): {b}")
            if not main and not side:
                events.put(("error", "A lista está vazia."))
                return
            out, missing, illegal = run(main, side, title=title, cols=cols, fmt=fmt, log=log,
                               progress=lambda i, n: events.put(("progress", (i, n))))
            events.put(("done", (out, missing, illegal, fmt)))
        except Exception as e:  # noqa: BLE001
            events.put(("error", str(e)))

    def start():
        btn.state(["disabled"])
        pbar["value"] = 0
        log_txt.configure(state="normal")
        log_txt.delete("1.0", "end")
        log_txt.configure(state="disabled")
        try:
            cols = max(3, min(15, int(cols_var.get())))
        except (tk.TclError, ValueError):
            cols = 8
        threading.Thread(
            target=worker,
            args=(main_txt.get("1.0", "end"), side_txt.get("1.0", "end"), title_var.get().strip(), cols,
                  None if fmt_var.get().startswith("(") else fmt_var.get()),
            daemon=True,
        ).start()

    btn.configure(command=start)

    def poll():
        try:
            while True:
                kind, data = events.get_nowait()
                if kind == "log":
                    log_txt.configure(state="normal")
                    log_txt.insert("end", data + "\n")
                    log_txt.see("end")
                    log_txt.configure(state="disabled")
                elif kind == "progress":
                    i, n = data
                    pbar["maximum"] = n
                    pbar["value"] = i
                elif kind == "done":
                    out, missing, illegal, fmt = data
                    btn.state(["!disabled"])
                    msg = f"Imagem salva em:\n{out}"
                    if illegal:
                        msg += f"\n\nRemovidas (fora do {fmt}):\n" + "\n".join(illegal)
                    if missing:
                        msg += "\n\nNão encontradas:\n" + "\n".join(missing)
                    log(f"Pronto: {out}")
                    if messagebox.askyesno("Pronto", msg + "\n\nAbrir a imagem?"):
                        open_file(out)
                elif kind == "error":
                    btn.state(["!disabled"])
                    messagebox.showerror("Erro", data)
        except queue.Empty:
            pass
        root.after(100, poll)

    poll()
    root.mainloop()


def cli(argv: list[str]):
    p = argparse.ArgumentParser(prog="deckimage", description="Gera final.jpg com as cartas de uma lista MTG.")
    p.add_argument("--main", type=Path, help="arquivo .txt do main deck (pode conter 'Sideboard')")
    p.add_argument("--side", type=Path, help="arquivo .txt do sideboard")
    p.add_argument("--title", default="")
    p.add_argument("--cols", type=int, default=8)
    p.add_argument("--format", default="premodern", help="filtro de legalidade (padrão premodern; 'none' desliga)")
    p.add_argument("--out", type=Path, help="padrão: ~/Downloads/final.jpg")
    a = p.parse_args(argv)
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
    out, missing, illegal = run(main, side, title=a.title, cols=a.cols, fmt=fmt, out_path=a.out,
                       progress=lambda i, n: print(f"\r  imagens {i}/{n}", end="", flush=True))
    print(f"\nSalvo em {out}")
    if illegal:
        print(f"Removidas (fora do {fmt}):", "; ".join(illegal))
    if missing:
        print("Não encontradas:", ", ".join(missing))
    if missing or illegal:
        return 1
    return 0


def main():
    if len(sys.argv) > 1:
        sys.exit(cli(sys.argv[1:]))
    gui()


if __name__ == "__main__":
    main()
