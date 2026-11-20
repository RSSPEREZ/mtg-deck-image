"""Janela (Tkinter/ttk) com tema escuro feito à mão.

Layout: cabeçalho; à esquerda título/colunas/formato/tamanho da imagem e as listas (main e side) com contadores;
à direita o painel Resultado (prévia, resumo, botões e log); embaixo progresso, pasta de saída e o
botão principal. Toda a parte pesada roda numa thread e conversa com a janela por uma fila.
"""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, ttk

from PIL import Image, ImageTk

from .build import DECK_RULES, DEFAULT_SIZE, FORMATS, SIZES, Cancelled, Issue, Result, downloads_dir, filename_from_title, run
from .decklist import count_cards, parse_lines

# ---------- paleta (mesma família de cores da imagem gerada) ----------
BG, PANEL, FIELD, LINE, HOVER = "#131316", "#1c1c21", "#25252c", "#3a3a44", "#30303a"
FG, MUTED, GOLD, GOLD_HI, INK = "#ececec", "#a0a0ad", "#e6b43c", "#f2c55a", "#17171a"
RED, GREEN = "#ef6f6f", "#6cc48a"
TAGS = {  # marcação das linhas nas listas depois da geração
    "fix": ("#4a3b14", "#ffd877"),
    "bad": ("#4a1f1f", "#ff9a9a"),
    "nf": ("#33333d", "#c9c9d6"),
}

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
NO_FILTER = "(sem filtro)"
AUTO = "Auto"
SIZE_LABELS = [lbl for lbl, _ in SIZES]
SIZE_BY_LABEL = dict(SIZES)
PREVIEW_W, PREVIEW_H = 350, 180
NARROW = 960  # abaixo dessa largura o painel Resultado desce para baixo das listas


def _pick_font(root: tk.Misc, *names: str) -> str:
    fams = set(tkfont.families(root))
    return next((n for n in names if n in fams), names[-1])


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _names(issues: list[Issue], limit: int = 4, show_detail: bool = False) -> str:
    parts = []
    for i in issues[:limit]:
        if show_detail:
            parts.append(f"{i.name} → {i.detail}")
        else:
            parts.append(i.name + (f" ({i.qty} cópias)" if i.qty > 1 else ""))
    more = len(issues) - limit
    return ", ".join(parts) + (f" e mais {more}" if more > 0 else "")


class App:
    def __init__(self, open_file, asset):
        self.open_file = open_file
        self.root = root = tk.Tk(className="DeckImageMTG")  # WM_CLASS: o atalho .desktop casa a janela por ele
        root.title("Deck Image – MTG")
        root.configure(bg=BG)
        root.geometry("1100x760")
        root.minsize(760, 680)
        self.sans = _pick_font(root, "DejaVu Sans", "Segoe UI", "Helvetica", "TkDefaultFont")
        self.mono = _pick_font(root, "DejaVu Sans Mono", "Consolas", "Courier New", "TkFixedFont")

        self.events: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.running = False
        self.out_dir = downloads_dir()
        self.result: Result | None = None
        self.bad_lines: list[str] = []
        self._preview = None
        self._dl_start = 0.0
        self._narrow: bool | None = None

        try:
            icon = Image.open(asset("icon.png")).convert("RGBA")
            self._icon = ImageTk.PhotoImage(icon.resize((32, 32), Image.LANCZOS))
            self._logo = ImageTk.PhotoImage(icon.resize((36, 36), Image.LANCZOS))
            root.iconphoto(True, self._icon)
        except OSError:
            self._logo = None

        self._style()
        self._build()
        self._bind()
        self.refresh_counts()
        self.update_where()
        self.show_empty()
        self.title_entry.focus_set()
        root.after(100, self.poll)

    # ---------- estilo ----------
    def _style(self):
        s, F = ttk.Style(self.root), self.sans
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, font=(F, 10), bordercolor=LINE, lightcolor=BG,
                    darkcolor=BG, troughcolor=FIELD, focuscolor=GOLD)
        s.configure("TFrame", background=BG)
        s.configure("P.TFrame", background=PANEL)
        s.configure("TLabel", background=BG, foreground=FG)
        s.configure("Muted.TLabel", foreground=MUTED, font=(F, 9))
        s.configure("P.TLabel", background=PANEL)
        s.configure("PM.TLabel", background=PANEL, foreground=MUTED, font=(F, 9))
        s.configure("H.TLabel", background=PANEL, font=(F, 11, "bold"))
        for n in ("TEntry", "TSpinbox", "TCombobox"):
            s.configure(n, fieldbackground=FIELD, foreground=FG, insertcolor=GOLD, bordercolor=LINE,
                        lightcolor=LINE, darkcolor=LINE, padding=(10, 9), arrowcolor=MUTED, background=FIELD,
                        arrowsize=18, selectbackground=GOLD, selectforeground=INK)
            s.map(n, fieldbackground=[("readonly", FIELD), ("disabled", BG)],
                  foreground=[("readonly", FG), ("disabled", MUTED)],
                  selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)],
                  bordercolor=[("focus", GOLD)], lightcolor=[("focus", GOLD)], darkcolor=[("focus", GOLD)])
        s.configure("Err.TEntry", bordercolor=RED, lightcolor=RED, darkcolor=RED)
        s.map("Err.TEntry", bordercolor=[("focus", RED)], lightcolor=[("focus", RED)], darkcolor=[("focus", RED)])
        s.configure("TButton", background=FIELD, foreground=FG, bordercolor=LINE, lightcolor=FIELD,
                    darkcolor=FIELD, padding=(14, 8), focuscolor=GOLD)
        s.map("TButton", background=[("active", HOVER)], foreground=[("disabled", MUTED)])
        s.configure("Link.TButton", background=PANEL, foreground=GOLD, bordercolor=PANEL, lightcolor=PANEL,
                    darkcolor=PANEL, padding=(8, 3), font=(F, 9))
        s.map("Link.TButton", background=[("active", "#2a2a31")], foreground=[("disabled", MUTED)])
        s.configure("Primary.TButton", background=GOLD, foreground=INK, bordercolor=GOLD, lightcolor=GOLD,
                    darkcolor=GOLD, padding=(34, 14), font=(F, 12, "bold"))
        s.map("Primary.TButton", background=[("active", GOLD_HI), ("disabled", "#5a4a22")],
              foreground=[("disabled", "#2a2a2a")])
        s.configure("PrimarySm.TButton", background=GOLD, foreground=INK, bordercolor=GOLD, lightcolor=GOLD,
                    darkcolor=GOLD, padding=(20, 10), font=(F, 11, "bold"))
        s.map("PrimarySm.TButton", background=[("active", GOLD_HI)])
        s.configure("Gold.Horizontal.TProgressbar", background=GOLD, troughcolor=FIELD, bordercolor=FIELD,
                    lightcolor=GOLD, darkcolor=GOLD, thickness=10)
        s.configure("Vertical.TScrollbar", background=FIELD, troughcolor=PANEL, bordercolor=PANEL,
                    lightcolor=FIELD, darkcolor=FIELD, arrowcolor=MUTED, gripcount=0)
        s.map("Vertical.TScrollbar", background=[("active", HOVER)])
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", GOLD)
        self.root.option_add("*TCombobox*Listbox.selectForeground", INK)
        self.root.option_add("*TCombobox*Listbox.font", (F, 12))

    # ---------- montagem ----------
    def _build(self):
        root, F = self.root, self.sans
        hdr = ttk.Frame(root, padding=(20, 14, 20, 6))
        hdr.pack(fill="x")
        if self._logo:
            tk.Label(hdr, image=self._logo, bg=BG).pack(side="left")
        ttk.Label(hdr, text="Deck Image MTG", font=(F, 15, "bold")).pack(side="left", padx=(10, 0))
        ttk.Label(hdr, text="Lista de deck  →  imagem para compartilhar", foreground=MUTED).pack(side="left", padx=14)
        ttk.Label(hdr, text="Imagens e dados via Scryfall", style="Muted.TLabel").pack(side="right")

        foot = ttk.Frame(root, padding=(20, 8, 20, 16))
        foot.pack(side="bottom", fill="x")  # antes do corpo, para ficar sempre visível
        self.body = body = ttk.Frame(root, padding=(20, 6))
        body.pack(fill="both", expand=True)

        # ----- coluna esquerda
        self.left = left = ttk.Frame(body)
        left.columnconfigure(0, weight=1, uniform="l")
        left.columnconfigure(1, weight=1, uniform="l")
        left.rowconfigure(1, weight=1)

        form = ttk.Frame(left)
        form.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        form.columnconfigure(0, weight=1)
        r = ttk.Frame(form)
        r.grid(row=0, column=0, sticky="ew")
        ttk.Label(r, text="Título do deck").pack(side="left")
        ttk.Label(r, text="  obrigatório", style="Muted.TLabel").pack(side="left")
        self.title_var = tk.StringVar()
        self.title_entry = ttk.Entry(form, textvariable=self.title_var, font=(F, 13))
        self.title_entry.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.hint = ttk.Label(form, font=(F, 9), foreground=MUTED)
        self.hint.grid(row=2, column=0, sticky="w", pady=(4, 0))

        c = ttk.Frame(form)
        c.grid(row=0, column=1, rowspan=3, sticky="s", padx=(14, 0), pady=(0, 22))
        ttk.Label(c, text="Colunas").pack(anchor="w")
        self.cols_var = tk.StringVar(value=AUTO)
        self.cols_box = ttk.Spinbox(c, values=[AUTO] + [str(n) for n in range(3, 16)], textvariable=self.cols_var,
                                    width=5, font=(F, 12), wrap=False)
        self.cols_box.pack(pady=(4, 0))
        f = ttk.Frame(form)
        f.grid(row=0, column=2, rowspan=3, sticky="s", padx=(14, 0), pady=(0, 22))
        ttk.Label(f, text="Formato (filtra cartas ilegais)").pack(anchor="w")
        self.fmt_var = tk.StringVar(value="premodern")
        self.fmt_box = ttk.Combobox(f, textvariable=self.fmt_var, values=FORMATS + [NO_FILTER], state="readonly",
                                    width=16, font=(F, 12))
        self.fmt_box.pack(pady=(4, 0))

        z = ttk.Frame(form)
        z.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        z.columnconfigure(1, weight=1)
        ttk.Label(z, text="Tamanho da imagem").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.size_var = tk.StringVar(value=next(l for l, v in SIZES if v == DEFAULT_SIZE))
        self.size_box = ttk.Combobox(z, textvariable=self.size_var, values=SIZE_LABELS, state="readonly",
                                     font=(F, 12))
        self.size_box.grid(row=0, column=1, sticky="ew")

        self.main_txt, self.main_count = self._list_pane(left, 0, "Main deck", EXAMPLE_MAIN)
        self.side_txt, self.side_count = self._list_pane(left, 1, "Sideboard", EXAMPLE_SIDE)
        self.legend = ttk.Frame(left)
        for key, txt in (("fix", "corrigida"), ("bad", "removida (fora do formato)"), ("nf", "não encontrada")):
            bg, fg = TAGS[key]
            tk.Label(self.legend, text=f" {txt} ", bg=bg, fg=fg, font=(F, 9)).pack(side="left", padx=(0, 8))
        ttk.Label(self.legend, text="linhas marcadas na lista", style="Muted.TLabel").pack(side="left")

        # ----- coluna direita: Resultado
        self.right = right = tk.Frame(body, bg=PANEL, highlightthickness=1, highlightbackground=LINE, width=380)
        right.pack_propagate(False)
        rh = ttk.Frame(right, style="P.TFrame", padding=(14, 10, 14, 6))
        rh.pack(fill="x")
        ttk.Label(rh, text="Resultado", style="H.TLabel").pack(side="left")
        self.log_btn = ttk.Button(rh, style="Link.TButton", command=self.toggle_log)
        self.preview = tk.Canvas(right, width=PREVIEW_W, height=PREVIEW_H, bg=FIELD, highlightthickness=0,
                                 cursor="arrow")
        self.preview.bind("<Button-1>", lambda e: self.result and self.open_file(self.result.path))
        self.info = ttk.Frame(right, style="P.TFrame", padding=(14, 0, 14, 12))
        self.log_txt = tk.Text(right, bg=FIELD, fg=MUTED, relief="flat", font=(self.mono, 9), height=8,
                               highlightthickness=0, padx=8, pady=6, wrap="word", state="disabled")
        self.log_view = False  # True: o painel mostra o log no lugar do resumo
        self.log_txt.tag_configure("warn", foreground="#ffd877")
        self.log_txt.tag_configure("bad", foreground="#ff9a9a")

        # ----- rodapé
        pr = ttk.Frame(foot)
        pr.pack(fill="x", pady=(0, 10))
        self.status = ttk.Label(pr, foreground=MUTED)
        self.status.pack(side="left")
        self.counter = ttk.Label(pr, foreground=GOLD, font=(F, 10, "bold"))
        self.counter.pack(side="right")
        self.pbar = ttk.Progressbar(foot, style="Gold.Horizontal.TProgressbar", mode="determinate")
        self.pbar.pack(fill="x", pady=(0, 12))
        act = ttk.Frame(foot)
        act.pack(fill="x")
        act.columnconfigure(0, weight=1, minsize=40)
        w = ttk.Frame(act)
        w.grid(row=0, column=0, sticky="ew")
        w.grid_propagate(False)
        w.configure(height=40)
        ttk.Label(w, text="Salvar em", style="Muted.TLabel").pack(anchor="w")
        self._where_font = tkfont.Font(family=self.mono, size=10)
        self._where_full = ""
        self.where = ttk.Label(w, font=self._where_font)
        self.where.pack(anchor="w", fill="x")
        w.pack_propagate(False)
        w.bind("<Configure>", lambda e: self._fit_where())
        self.dir_btn = ttk.Button(act, text="Alterar pasta…", command=self.choose_dir)
        self.dir_btn.grid(row=0, column=1, padx=14, pady=(8, 0))
        ttk.Label(act, text="Ctrl+Enter", style="Muted.TLabel").grid(row=0, column=2, padx=(0, 14))
        self.go_btn = ttk.Button(act, text="Gerar imagem", style="Primary.TButton", command=self.go_or_cancel)
        self.go_btn.grid(row=0, column=3)

    def _set_where(self, text: str):
        self._where_full = text
        self._fit_where()

    def _fit_where(self):
        """Caminho longo: elipse no meio (mantém a pasta de origem e o nome do arquivo)."""
        text, width = self._where_full, self.where.master.winfo_width() - 4
        f = self._where_font
        if width > 20 and f.measure(text) > width:
            lo, hi = 1, len(text)
            while lo < hi:  # maior nº de caracteres mantidos que cabe
                mid = (lo + hi + 1) // 2
                keep = mid
                cand = text[: keep // 2] + "…" + text[len(text) - (keep - keep // 2):]
                if f.measure(cand) <= width:
                    lo = mid
                else:
                    hi = mid - 1
            text = text[: lo // 2] + "…" + text[len(text) - (lo - lo // 2):]
        self.where.configure(text=text)

    def _list_pane(self, parent, col: int, title: str, text: str):
        F = self.sans
        box = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=LINE)
        box.grid(row=1, column=col, sticky="nsew", padx=(0, 10) if col == 0 else 0)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(1, weight=1)
        h = ttk.Frame(box, style="P.TFrame", padding=(12, 8, 6, 6))
        h.grid(row=0, column=0, columnspan=2, sticky="ew")
        h.columnconfigure(0, weight=1)
        ttk.Label(h, text=title, style="H.TLabel").grid(row=0, column=0, sticky="w")
        sub = ttk.Frame(h, style="P.TFrame")
        sub.grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))
        n = tk.Label(sub, bg=FIELD, fg=FG, font=(F, 9, "bold"))
        n.pack(side="left")
        msg = tk.Label(sub, bg=PANEL, font=(F, 9))
        msg.pack(side="left", padx=8)
        b = ttk.Frame(h, style="P.TFrame")
        b.grid(row=0, column=1, sticky="ne")
        t = tk.Text(box, bg=FIELD, fg=FG, insertbackground=GOLD, relief="flat", font=(self.mono, 11), padx=12,
                    pady=8, selectbackground=GOLD, selectforeground=INK, highlightthickness=0, wrap="none",
                    spacing1=2, spacing3=2, undo=True, width=10, height=8)
        sb = ttk.Scrollbar(box, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.grid(row=1, column=0, sticky="nsew", padx=(1, 0), pady=(0, 1))
        sb.grid(row=1, column=1, sticky="ns", pady=(0, 1))
        t.insert("1.0", text)
        t.edit_reset()
        t.edit_modified(False)
        for key, (bg, fg) in TAGS.items():
            t.tag_configure(key, background=bg, foreground=fg)
        t.tag_configure("bad", overstrike=True)
        t.tag_configure("nf", underline=True)
        ttk.Button(b, text="Colar", style="Link.TButton", width=6, command=lambda: self.paste(t)).pack(side="left")
        ttk.Button(b, text="Limpar", style="Link.TButton", width=6, command=lambda: self.clear(t)).pack(side="left")
        t._buttons = b.winfo_children()  # type: ignore[attr-defined]
        return t, (n, msg)

    def _bind(self):
        self.title_var.trace_add("write", lambda *_: self.update_where())
        self.fmt_var.trace_add("write", lambda *_: self.refresh_counts())
        self.size_var.trace_add("write", lambda *_: self.update_where())
        for t in (self.main_txt, self.side_txt):
            t.bind("<<Modified>>", self._on_modified)
        for seq in ("<Control-Return>", "<Control-KP_Enter>"):
            self.root.bind_all(seq, lambda e: (self.go_or_cancel() if not self.running else None, "break")[1])
        self.root.bind("<Configure>", self._on_resize)

    # ---------- layout responsivo ----------
    def _on_resize(self, event):
        if event.widget is not self.root:
            return
        narrow = event.width < NARROW
        if narrow == self._narrow:
            return
        self._narrow = narrow
        self.left.grid_forget()
        self.right.grid_forget()
        for i in range(2):
            self.body.columnconfigure(i, weight=0, minsize=0)
            self.body.rowconfigure(i, weight=0, minsize=0)
        if narrow:  # Resultado embaixo das listas
            self.body.columnconfigure(0, weight=1)
            self.body.rowconfigure(0, weight=1)
            self.left.grid(row=0, column=0, sticky="nsew")
            self.right.grid(row=1, column=0, sticky="ew", pady=(12, 0))
            self.right.pack_propagate(True)  # altura do conteúdo
        else:
            self.body.columnconfigure(0, weight=1)
            self.body.columnconfigure(1, minsize=380)
            self.body.rowconfigure(0, weight=1)
            self.left.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
            self.right.grid(row=0, column=1, sticky="nsew")
            self.right.pack_propagate(False)
        pw, ph = (200, 112) if narrow else (PREVIEW_W, PREVIEW_H)
        self.preview.configure(width=pw, height=ph)
        if self.result and not self.running:
            self.show_done(self.result)  # refaz a prévia e o resumo no novo tamanho
        elif not self.running:
            self._pack_right()

    def _pack_right(self):
        """Prévia em cima e resumo embaixo; na janela estreita, lado a lado. O log substitui o resumo."""
        for w in (self.preview, self.info, self.log_txt):
            w.pack_forget()
        side = "left" if self._narrow else "top"
        self.preview.pack(side=side, anchor="n", padx=14, pady=(2, 8))
        if self.log_view:
            self.log_txt.pack(side=side, fill="both", expand=True, padx=(0 if self._narrow else 14, 14), pady=(2, 12))
        else:
            self.info.pack(side=side, fill="both", expand=True)

    def toggle_log(self, show: bool | None = None):
        self.log_view = (not self.log_view) if show is None else show
        self.log_btn.configure(text="Ver resumo" if self.log_view else "Ver log")
        self._pack_right()

    # ---------- listas ----------
    def paste(self, t: tk.Text):
        try:
            clip = self.root.clipboard_get()
        except tk.TclError:
            return
        t.edit_separator()
        t.delete("1.0", "end")
        t.insert("1.0", clip)
        t.edit_separator()
        t.focus_set()

    def clear(self, t: tk.Text):
        t.edit_separator()
        t.delete("1.0", "end")
        t.edit_separator()
        t.focus_set()

    def _on_modified(self, event):
        t = event.widget
        if not t.edit_modified():
            return
        t.edit_modified(False)
        for key in TAGS:  # a lista mudou: as marcações da geração anterior não valem mais
            t.tag_remove(key, "1.0", "end")
        self.root.after_idle(self.refresh_counts)

    def parse(self):
        main, extra, bad1 = parse_lines(self.main_txt.get("1.0", "end"), "main")
        side, _, bad2 = parse_lines(self.side_txt.get("1.0", "end"), "side")
        return main, extra + side, bad1 + bad2

    def refresh_counts(self):
        main, side, bad = self.parse()
        nm, ns = count_cards(main), count_cards(side)
        fmt = self.fmt()
        rule = DECK_RULES.get(fmt) if fmt else None
        for (num, msg), n, is_main in ((self.main_count, nm, True), (self.side_count, ns, False)):
            num.configure(text=f" {_plural(n, 'carta', 'cartas')} ")
            if not rule:
                msg.configure(text="")
            elif is_main:
                need = rule[0]
                if fmt in ("commander", "predh"):
                    ok = n == need
                    msg.configure(text=f"✓ {need} cartas" if ok else
                                  (f"faltam {need - n} para {need}" if n < need else f"{n - need} a mais que {need}"),
                                  fg=GREEN if ok else GOLD)
                else:
                    msg.configure(text=f"✓ {need} ou mais" if n >= need else f"faltam {need - n} para {need}",
                                  fg=GREEN if n >= need else GOLD)
            else:
                lim = rule[1]
                if lim == 0:
                    msg.configure(text="sem sideboard neste formato" if n else "", fg=GOLD)
                else:
                    msg.configure(text=f"✓ dentro do limite ({lim})" if n <= lim else f"{n - lim} acima do limite de {lim}",
                                  fg=GREEN if n <= lim else GOLD)
        if not self.running and not self._title_error:
            distinct = len(main) + len(side)
            txt = (f"Pronto para gerar  ·  {_plural(nm + ns, 'carta', 'cartas')}, "
                   f"{_plural(distinct, 'imagem diferente', 'imagens diferentes')}") if distinct else "Cole uma lista para começar."
            if bad:
                txt += f"  ·  {_plural(len(bad), 'linha não entendida', 'linhas não entendidas')}"
            self.status.configure(text=txt, foreground=MUTED)

    _title_error = False

    def size(self) -> tuple[int, int] | None:
        return SIZE_BY_LABEL.get(self.size_var.get(), DEFAULT_SIZE)

    def size_text(self) -> str:
        sz = self.size()
        return f"{sz[0]}×{sz[1]}" if sz else "tamanho original"

    def fmt(self) -> str | None:
        v = self.fmt_var.get()
        return None if v == NO_FILTER else v

    # ---------- título e pasta ----------
    def update_where(self):
        if self._title_error and self.title_var.get().strip():
            self._title_error = False
            self.title_entry.configure(style="TEntry")
            self.refresh_counts()
        try:
            name = filename_from_title(self.title_var.get())
            self.hint.configure(text=f"Arquivo:  {name}  ·  {self.size_text()}", foreground=MUTED)
            self._set_where(str(self.out_dir / name))
        except ValueError:
            if not self._title_error:
                self.hint.configure(text=f"Também vira o nome do arquivo (.jpg)  ·  {self.size_text()}",
                                    foreground=MUTED)
            self._set_where(f"{self.out_dir}{os.sep}  (nome definido pelo título)")

    def choose_dir(self):
        d = filedialog.askdirectory(parent=self.root, initialdir=str(self.out_dir), title="Pasta da imagem")
        if d:
            self.out_dir = Path(d)
            self.update_where()

    def title_error(self, msg: str):
        self._title_error = True
        self.title_entry.configure(style="Err.TEntry")
        self.hint.configure(text=f"⚠  {msg}", foreground=RED)
        self.status.configure(text="Falta o título para gerar.", foreground=RED)
        self.title_entry.focus_set()

    # ---------- painel Resultado ----------
    def _clear_info(self, log_link: bool = False):
        for w in self.info.winfo_children():
            w.destroy()
        if log_link:
            self.log_btn.pack(side="right")
        else:
            self.log_btn.pack_forget()

    def _preview_placeholder(self, msg: str, filled: int = 10):
        c = self.preview
        c.delete("all")
        pw, ph = int(c.cget("width")), int(c.cget("height"))
        c.create_rectangle(8, 8, pw - 8, ph - 8, outline=LINE, dash=(5, 4))
        k = pw / PREVIEW_W
        for rr in range(2):
            for cc in range(5):
                x, y = (34 + cc * 58) * k, (20 + rr * 66) * k
                c.create_rectangle(x, y, x + 44 * k, y + 56 * k, fill="#2d2d36" if rr * 5 + cc < filled else "#202026",
                                   outline="")
        c.create_text(pw // 2, ph - 20, text=msg, fill=MUTED, font=(self.sans, 10))
        c.configure(cursor="arrow")

    def _text(self, parent, text, **kw):
        lbl = tk.Label(parent, text=text, bg=PANEL, fg=kw.pop("fg", MUTED), font=kw.pop("font", (self.sans, 9)),
                       justify="left", anchor="w", wraplength=kw.pop("wrap", 330))
        lbl.pack(anchor="w", **kw)
        return lbl

    def show_empty(self):
        self._clear_info()
        self.log_view = False
        self._pack_right()
        self._preview_placeholder("A imagem aparece aqui")
        ttk.Label(self.info, text="Ainda sem imagem", style="P.TLabel", font=(self.sans, 11, "bold")).pack(anchor="w")
        self._text(self.info, "Cole o main e o sideboard, dê um título e clique em Gerar imagem (atalho: Ctrl+Enter)."
                   "\n\nAo terminar, aqui aparecem a prévia, as cartas removidas ou não encontradas e os botões "
                   "para abrir a imagem e a pasta.", pady=(4, 0))

    def show_running(self):
        self._clear_info()
        self._preview_placeholder("Montando a imagem…", filled=0)
        ttk.Label(self.info, text="Gerando…", style="P.TLabel", font=(self.sans, 12, "bold")).pack(anchor="w")
        self.run_sub = ttk.Label(self.info, text="Consultando o Scryfall", style="PM.TLabel")
        self.run_sub.pack(anchor="w", pady=(0, 6))
        self.log_txt.configure(state="normal")
        self.log_txt.delete("1.0", "end")
        self.log_txt.configure(state="disabled")
        self.log_view = True  # durante a geração o log aparece direto, abaixo do "Gerando…"
        for w in (self.preview, self.info, self.log_txt):
            w.pack_forget()
        side = "left" if self._narrow else "top"
        self.preview.pack(side=side, anchor="n", padx=14, pady=(2, 8))
        self.info.pack(side=side, fill="x")
        self.log_txt.pack(side=side, fill="both", expand=True, padx=(0 if self._narrow else 14, 14), pady=(0, 12))

    def _issue_row(self, dot, head, body):
        fr = ttk.Frame(self.info, style="P.TFrame")
        fr.pack(fill="x", pady=3)
        tk.Label(fr, text="●", fg=dot, bg=PANEL, font=(self.sans, 9)).pack(side="left", anchor="n", pady=2)
        t = ttk.Frame(fr, style="P.TFrame")
        t.pack(side="left", padx=8, fill="x", expand=True)
        ttk.Label(t, text=head, style="P.TLabel", font=(self.sans, 10, "bold")).pack(anchor="w")
        if not self._narrow:  # na janela estreita só o título; os nomes estão marcados na lista
            self._text(t, body, wrap=300)

    def show_done(self, res: Result):
        self._clear_info(log_link=True)
        self.toggle_log(False)
        c = self.preview
        c.delete("all")
        try:
            pw, ph = int(c.cget("width")), int(c.cget("height"))
            with Image.open(res.path) as im:
                im.thumbnail((pw, ph), Image.LANCZOS)
                self._preview = ImageTk.PhotoImage(im)
            c.create_image(pw // 2, ph // 2, image=self._preview)
            c.configure(cursor="hand2")
        except OSError:
            self._preview_placeholder("Sem prévia")
        ttk.Label(self.info, text="✓  Imagem pronta", style="P.TLabel", foreground=GREEN,
                  font=(self.sans, 12, "bold")).pack(anchor="w")
        sub = f"{res.main_count} no main · {res.side_count} no sideboard · {res.size[0]}×{res.size[1]}"
        ttk.Label(self.info, text=sub, style="PM.TLabel").pack(anchor="w", pady=(0, 6))
        fmt = self.fmt()
        n_nf = len(res.missing) + len(self.bad_lines)
        if self._narrow:  # janela estreita: avisos numa linha; os nomes estão marcados na lista
            parts = [(GOLD, f"{len(res.corrected)} corrigida(s)") if res.corrected else None,
                     (RED, f"{len(res.illegal)} removida(s)") if res.illegal else None,
                     (MUTED, f"{n_nf} não encontrada(s)") if n_nf else None]
            line = ttk.Frame(self.info, style="P.TFrame")
            line.pack(anchor="w")
            for part in filter(None, parts):
                tk.Label(line, text="● " + part[1], fg=part[0] if part[0] != MUTED else FG, bg=PANEL,
                         font=(self.sans, 10, "bold")).pack(side="left", padx=(0, 12))
        else:
            if res.corrected:
                self._issue_row(GOLD, f"{_plural(len(res.corrected), 'corrigida', 'corrigidas')} por aproximação",
                                _names(res.corrected, 3, show_detail=True))
            if res.illegal:
                self._issue_row(RED, f"{_plural(len(res.illegal), 'removida', 'removidas')} (fora do {fmt.capitalize()})",
                                _names(res.illegal))
            if res.missing or self.bad_lines:
                n = len(res.missing) + len(self.bad_lines)
                body = _names(res.missing) if res.missing else ""
                if self.bad_lines:
                    body += ("; " if body else "") + f"{_plural(len(self.bad_lines), 'linha', 'linhas')} não entendida(s)"
                self._issue_row(MUTED, f"{_plural(n, 'não encontrada', 'não encontradas')}", body)
        if not (res.corrected or res.illegal or res.missing or self.bad_lines):
            self._text(self.info, "Todas as cartas foram encontradas.", fg=GREEN)
        row = ttk.Frame(self.info, style="P.TFrame")
        row.pack(fill="x", pady=(10, 0))
        row.columnconfigure((0, 1), weight=1)
        open_btn = ttk.Button(row, text="Abrir imagem", style="PrimarySm.TButton",
                              command=lambda: self.open_file(res.path))
        folder_btn = ttk.Button(row, text="Abrir pasta", command=lambda: self.open_file(res.path.parent))
        self.copy_btn = ttk.Button(row, text="Copiar caminho", command=lambda: self.copy_path(res.path))
        if self._narrow:  # os três lado a lado
            row.columnconfigure(2, weight=1)
            for i, b in enumerate((open_btn, folder_btn, self.copy_btn)):
                b.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 4, 0))
        else:
            open_btn.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
            folder_btn.grid(row=1, column=0, sticky="ew", padx=(0, 4))
            self.copy_btn.grid(row=1, column=1, sticky="ew", padx=(4, 0))

    def show_stopped(self, title: str, msg: str, color: str):
        self._clear_info(log_link=True)
        self.toggle_log(False)
        self._preview_placeholder("A imagem aparece aqui")
        ttk.Label(self.info, text=title, style="P.TLabel", foreground=color,
                  font=(self.sans, 12, "bold")).pack(anchor="w")
        self._text(self.info, msg, pady=(2, 6))

    def copy_path(self, path: Path):
        self.root.clipboard_clear()
        self.root.clipboard_append(str(path))
        self.copy_btn.configure(text="✓ Copiado")
        self.root.after(1500, lambda: self.copy_btn.winfo_exists() and self.copy_btn.configure(text="Copiar caminho"))

    def log(self, msg: str):
        tag = "bad" if ("fora do formato" in msg or "não encontrada" in msg) else (
            "warn" if ("->" in msg or "aproximação" in msg or "ignorada" in msg) else "")
        self.log_txt.configure(state="normal")
        self.log_txt.insert("end", msg.strip() + "\n", tag)
        self.log_txt.see("end")
        self.log_txt.configure(state="disabled")

    # ---------- geração ----------
    def set_running(self, running: bool):
        self.running = running
        state = "disabled" if running else "normal"
        for t in (self.main_txt, self.side_txt):
            t.configure(state=state, fg=MUTED if running else FG)
            for b in t._buttons:  # type: ignore[attr-defined]
                b.state(["disabled"] if running else ["!disabled"])
        for w in (self.title_entry, self.cols_box, self.dir_btn):
            w.state(["disabled"] if running else ["!disabled"])
        for cb in (self.fmt_box, self.size_box):
            cb.state(["disabled"] if running else ["!disabled", "readonly"])
        self.go_btn.configure(text="Cancelar" if running else "Gerar imagem")
        self.go_btn.state(["!disabled"])

    def go_or_cancel(self):
        if self.running:
            self.cancel.set()
            self.go_btn.configure(text="Cancelando…")
            self.go_btn.state(["disabled"])
            return
        try:
            name = filename_from_title(self.title_var.get())
        except ValueError:
            self.title_error("Dê um título: ele vira o nome do arquivo.")
            return
        main, side, bad = self.parse()
        if not main and not side:
            self.status.configure(text="A lista está vazia.", foreground=RED)
            self.main_txt.focus_set()
            return
        try:
            cols = max(3, min(15, int(self.cols_var.get())))
        except (tk.TclError, ValueError):
            cols = None  # "Auto" (ou texto inválido): o layout escolhe
        self.cols_var.set(AUTO if cols is None else str(cols))
        for t in (self.main_txt, self.side_txt):
            for key in TAGS:
                t.tag_remove(key, "1.0", "end")
        self.bad_lines = bad
        self.result = None
        self.cancel.clear()
        self.set_running(True)
        self.show_running()
        for b in bad:
            self.log(f"Linha ignorada (não entendi): {b.strip()}")
        self.status.configure(text="Consultando o Scryfall…", foreground=FG)
        self.counter.configure(text="")
        self.pbar.configure(mode="indeterminate")
        self.pbar.start(12)
        self._dl_start = 0.0
        threading.Thread(target=self.worker, args=(main, side, self.title_var.get(), cols, self.fmt(),
                                                   self.out_dir / name, self.size()), daemon=True).start()

    def worker(self, main, side, title, cols, fmt, out, size):
        try:
            res = run(main, side, title=title, cols=cols, size=size, fmt=fmt, out_path=out, cancel=self.cancel,
                      log=lambda m: self.events.put(("log", m)),
                      progress=lambda i, n, name: self.events.put(("progress", (i, n, name))))
            self.events.put(("done", res))
        except Cancelled:
            self.events.put(("cancelled", None))
        except Exception as e:  # noqa: BLE001
            self.events.put(("error", str(e)))

    def mark_lines(self, res: Result):
        widgets = {"main": self.main_txt, "side": self.side_txt}
        for tag, issues in (("fix", res.corrected), ("bad", res.illegal), ("nf", res.missing)):
            for i in issues:
                for src, n in i.lines:
                    widgets[src].tag_add(tag, f"{n}.0", f"{n}.end")
        if self.bad_lines:  # linhas que o parser não entendeu: marca pelo texto
            for t in widgets.values():
                for n, line in enumerate(t.get("1.0", "end-1c").splitlines(), 1):
                    if line in self.bad_lines:
                        t.tag_add("nf", f"{n}.0", f"{n}.end")
        if res.corrected or res.illegal or res.missing or self.bad_lines:
            self.legend.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        else:
            self.legend.grid_forget()

    def finish(self):
        self.pbar.stop()
        self.pbar.configure(mode="determinate")
        self.set_running(False)

    def poll(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "log":
                    self.log(data)
                elif kind == "progress":
                    i, n, name = data
                    if self._dl_start == 0.0:
                        self._dl_start = time.monotonic()
                        self.pbar.stop()
                        self.pbar.configure(mode="determinate", maximum=n)
                    self.pbar.configure(value=i)
                    self.status.configure(text=f"Baixando imagens das cartas…  {name}", foreground=FG)
                    self.counter.configure(text=f"{i}/{n}")
                    eta = (time.monotonic() - self._dl_start) / i * (n - i)
                    self.run_sub.configure(text=f"{i} de {n} imagens" + (f" · ~{eta:.0f} s restantes" if i < n and eta >= 1 else ""))
                    self._preview_placeholder("Montando a imagem…", filled=round(10 * i / n))
                elif kind == "done":
                    res: Result = data
                    self.result = res
                    self.finish()
                    self.pbar.configure(value=self.pbar["maximum"])
                    self.counter.configure(text="")
                    self.status.configure(
                        text=f"Concluído em {res.seconds:.0f} s · {_plural(res.images, 'imagem', 'imagens')} "
                             f"({res.cached} do cache)", foreground=GREEN)
                    self.mark_lines(res)
                    self.show_done(res)
                elif kind == "cancelled":
                    self.finish()
                    self.pbar.configure(value=0)
                    self.counter.configure(text="")
                    self.status.configure(text="Geração cancelada. Nada foi salvo.", foreground=MUTED)
                    self.show_stopped("Cancelada", "Nenhum arquivo foi gravado. As imagens já baixadas ficam no "
                                      "cache e a próxima geração será mais rápida.", MUTED)
                elif kind == "error":
                    self.finish()
                    self.pbar.configure(value=0)
                    self.counter.configure(text="")
                    self.status.configure(text="Não foi possível gerar a imagem.", foreground=RED)
                    self.show_stopped("Erro", data, RED)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


def gui(open_file, asset):
    App(open_file, asset).root.mainloop()
