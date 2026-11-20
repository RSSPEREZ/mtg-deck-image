"""Orquestra: lista -> Scryfall -> imagens -> <titulo>.jpg."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .decklist import Entry
from .scryfall import Client, front_image_url

CARD_W, CARD_H = 488, 680  # tamanho "normal" do Scryfall; é o máximo (a carta só é reduzida)
GAP = 24
LABEL_H = 64
MARGIN = 48
BG = (24, 24, 28)
FG = (235, 235, 235)
MUTED = (150, 150, 160)
ACCENT = (230, 180, 60)

DISCLAIMER = (
    "Projeto de fã não oficial. Magic: The Gathering e as imagens das cartas são © Wizards of the Coast. "
    "Dados e imagens via Scryfall. Não aprovado nem endossado pela Wizards."
)


_WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def filename_from_title(title: str) -> str:
    """'Mono Green Elves' -> 'mono_green_elves.jpg'. Tira acentos; o que não é letra/número vira '_'."""
    ascii_ = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_.lower()).strip("_")[:100].rstrip("_")
    if not slug:
        raise ValueError("O título precisa ter pelo menos uma letra ou número.")
    if slug in _WIN_RESERVED:
        slug += "_deck"
    return slug + ".jpg"


@dataclass
class Resolved:
    entry: Entry
    card: dict | None
    image: Path | None = None
    typed_name: str | None = None  # nome como estava na lista, quando a carta veio por aproximação


@dataclass
class Issue:
    """Carta com algo a avisar: corrigida por aproximação, fora do formato ou não encontrada."""
    name: str          # nome na lista
    detail: str        # ex.: "Goblin Matron", "banida", "não legal"
    qty: int
    lines: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class Result:
    path: Path
    corrected: list[Issue]
    illegal: list[Issue]
    missing: list[Issue]
    images: int        # imagens distintas na composição
    cached: int        # quantas já estavam no cache local
    seconds: float
    size: tuple[int, int]
    main_count: int
    side_count: int


class Cancelled(Exception):
    """Geração interrompida pelo usuário."""


def _check(cancel: threading.Event | None):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def system_env() -> dict[str, str]:
    """Ambiente para chamar programas do sistema (xdg-open, xdg-user-dir...).

    O executável do PyInstaller aponta LD_LIBRARY_PATH para as bibliotecas
    empacotadas (libstdc++, libssl do Ubuntu 22.04). Programas do sistema que
    herdam isso, como o kde-open no SteamOS, quebram por versão de símbolo.
    Restaura o valor original (LD_LIBRARY_PATH_ORIG) ou remove a variável.
    """
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        for var in ("LD_LIBRARY_PATH",):
            orig = env.pop(var + "_ORIG", None)
            if orig is not None:
                env[var] = orig
            else:
                env.pop(var, None)
    return env


def downloads_dir() -> Path:
    if not sys.platform.startswith("win"):
        try:
            out = subprocess.run(
                ["xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True, timeout=5, env=system_env()
            )
            p = Path(out.stdout.strip())
            if out.returncode == 0 and p.is_dir() and p != Path.home():
                return p
        except (OSError, subprocess.SubprocessError):
            pass
    d = Path.home() / "Downloads"
    d.mkdir(exist_ok=True)
    return d


# ---------- resolução ----------
def resolve(client: Client, entries: list[Entry], fmt: str | None = None,
            corrected: list[Issue] | None = None) -> list[Resolved]:
    """`corrected`, se passado, recebe as cartas achadas por aproximação (antes de juntar repetidas)."""
    if not entries:
        return []
    found, _ = client.collection([e.identifier() for e in entries])

    by_name: dict[str, dict] = {}
    by_print: dict[tuple, dict] = {}
    for c in found:
        names = [c["name"]] + [f["name"] for f in c.get("card_faces", [])]
        for n in names:
            by_name.setdefault(n.lower(), c)
            by_name.setdefault((n.lower(), c["set"]), c)
        by_print[(c["set"], c["collector_number"].lower())] = c

    out = []
    for e in entries:
        card = None
        if e.set_code and e.collector_number:
            card = by_print.get((e.set_code.lower(), e.collector_number.lower()))
        elif e.set_code:
            card = by_name.get((e.name.lower(), e.set_code.lower()))
        else:
            card = by_name.get(e.name.lower()) or by_name.get(e.name.split(" // ")[0].lower())
        if card is None:  # nome com erro de digitação, acento etc. -> busca aproximada
            client.log(f"  buscando por aproximação: {e.name}")
            card = client.named_fuzzy(e.name, e.set_code)
            if card:
                client.log(f"    -> {card['name']}")
                out.append(Resolved(e, card, typed_name=e.name))
                continue
        out.append(Resolved(e, card))
    _use_oldest_printing(client, out, fmt)
    if corrected is not None:
        corrected += [Issue(r.typed_name, r.card["name"], r.entry.qty, list(r.entry.lines))
                      for r in out if r.typed_name and r.card]
    return _merge_same_card(out)


def _oracle(card: dict) -> str | None:
    return card.get("oracle_id") or (card.get("card_faces") or [{}])[0].get("oracle_id")


# Primeira escolha de imagem: a impressão mais antiga com borda preta (a branca contrasta demais com o
# fundo escuro). Fica de fora memorabilia (Collectors' Edition, 30th Anniversary) e coleções de humor.
BLACK_BORDER = " border:black -st:memorabilia -st:funny"


def _use_oldest_printing(client: Client, items: list[Resolved], fmt: str | None = None):
    """Sem edição na lista -> impressão mais antiga com borda preta; se a carta só existir com borda
    branca (ou prateada), a mais antiga de todas. Evita Marvel, Star Trek etc."""
    todo = [r for r in items if r.card and not r.entry.set_code]
    if not todo:
        return
    names = list(dict.fromkeys(r.card["name"] for r in todo))
    chosen = {_oracle(c): c for c in client.oldest_printings(names, fmt, BLACK_BORDER)}
    rest = list(dict.fromkeys(r.card["name"] for r in todo if _oracle(r.card) not in chosen))
    if rest:
        fallback = client.oldest_printings(rest, fmt)
        if fallback:
            client.log(f"  sem impressão de borda preta: {', '.join(c['name'] for c in fallback)} — usando a mais antiga")
        for c in fallback:
            chosen.setdefault(_oracle(c), c)
    for r in todo:
        r.card = chosen.get(_oracle(r.card), r.card)


def _merge_same_card(items: list[Resolved]) -> list[Resolved]:
    """'4 Lightning Bolt' + '1 Lightnig Bolt' (corrigida por aproximação) viram 5x."""
    merged: dict[str, Resolved] = {}
    out: list[Resolved] = []
    for r in items:
        if r.card is None:
            out.append(r)
            continue
        key = r.card["id"]
        if key in merged:
            merged[key].entry.qty += r.entry.qty
            merged[key].entry.lines += r.entry.lines
        else:
            merged[key] = r
            out.append(r)
    return out


def fetch_images(client: Client, items: list[Resolved], progress=None, cancel: threading.Event | None = None):
    """progress(i, total, nome_da_carta) é chamado depois de cada imagem."""
    total = len(items)
    for i, r in enumerate(items, 1):
        _check(cancel)
        if r.card:
            url = front_image_url(r.card)
            if url:
                r.image = client.image(url)
        if progress:
            progress(i, total, r.card["name"] if r.card else r.entry.name)


# ---------- layout ----------
# Rótulo (como aparece na janela) -> (largura, altura); None = "Original" (largura pelas colunas, altura livre).
SIZES: list[tuple[str, tuple[int, int] | None]] = [
    ("1920 × 1080 — Full HD (padrão)", (1920, 1080)),
    ("1280 × 720 — HD", (1280, 720)),
    ("2560 × 1440 — QHD", (2560, 1440)),
    ("3840 × 2160 — 4K", (3840, 2160)),
    ("1080 × 1080 — Instagram quadrado", (1080, 1080)),
    ("1080 × 1350 — Instagram retrato (4:5)", (1080, 1350)),
    ("1080 × 1920 — Stories / vertical (9:16)", (1080, 1920)),
    ("Original — largura pelas colunas, altura livre", None),
]
DEFAULT_SIZE = (1920, 1080)
MIN_SIDE, MAX_SIDE = 480, 7680
ORIGINAL_COLS = 8
SMALL_CARD_W = 110  # abaixo disso a carta fica pequena demais para ler: só avisa


def parse_size(s: str) -> tuple[int, int] | None:
    """'1920x1080' / '1920×1080' / 'original' -> (W, H) ou None. ValueError se inválido."""
    t = s.strip().lower().replace("×", "x")
    if t == "original":
        return None
    m = re.fullmatch(r"(\d+)\s*x\s*(\d+)", t)
    if not m:
        raise ValueError(f"tamanho inválido: '{s}' (use LARGURAxALTURA, ex.: 1920x1080, ou 'original')")
    w, h = int(m.group(1)), int(m.group(2))
    if not (MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE):
        raise ValueError(f"tamanho fora do limite: {w}x{h} (cada lado entre {MIN_SIDE} e {MAX_SIDE} px)")
    return w, h


Box = tuple[int, int, int, int]  # x0, y0, x1, y1


@dataclass
class Layout:
    width: int
    height: int
    cols: int
    card_w: int
    card_h: int
    gap: int
    margin: int
    label_h: int
    title_font: int
    head_font: int
    qty_font: int
    foot_font: int
    title_box: Box | None
    head_boxes: list[Box]               # um por seção (main, depois side)
    card_boxes: list[list[Box]]         # por seção, uma caixa por carta
    label_boxes: list[list[Box]]        # caixa do rótulo de quantidade, abaixo de cada carta
    footer_lines: list[tuple[str, Box]]
    warnings: list[str] = field(default_factory=list)


def _estimate(text: str, size: int) -> float:
    return len(text) * size * 0.6  # conservador para DejaVu/Arial; compose passa a medida real


def _wrap(text: str, size: int, max_w: float, measure) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if cur and measure(trial, size) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur] if cur else lines


def plan_layout(n_main: int, n_side: int, size: tuple[int, int] | None = DEFAULT_SIZE,
                cols: int | None = None, has_title: bool = True, measure=None) -> Layout:
    """Calcula todas as posições (função pura: sem Pillow nem rede). `size=None` é o modo Original
    (medidas da v1.2.0, cols None -> 8). Com tamanho fixo, a imagem sai exatamente W x H e a carta é a
    maior possível (até 488x680) que cabe; cols None escolhe o nº de colunas que a maximiza."""
    measure = measure or _estimate
    counts = [n_main] + ([n_side] if n_side else [])
    fixed = size is not None
    k = min(size) / 1080 if fixed else 1.0
    margin = max(8, round(48 * k))
    gap = max(4, round(24 * k))
    title_font = max(18, round(72 * k))
    title_h = max(24, round(110 * k)) if has_title else 0
    head_font = max(16, round(52 * k))
    head_h = max(22, round(90 * k))
    foot_font = max(14, round(24 * k))
    line_h = round(foot_font * 1.25)
    pad = round(30 * k)
    warnings: list[str] = []
    split = None

    def label_of(cw: int) -> tuple[int, int]:  # (fonte da quantidade, altura do rótulo)
        f = max(14, round(0.09 * cw))
        return f, max(20, round(f * 1.45))

    def rows_of(c: int) -> list[int]:
        return [max(1, -(-n // c)) for n in counts]

    def content_h(cw: int, c: int) -> int:
        ch = round(cw * CARD_H / CARD_W)
        lh = label_of(cw)[1]
        return margin + title_h + sum(head_h + r * (ch + lh) + (r - 1) * gap + gap for r in rows_of(c))

    if fixed:
        W, H = size
        foot_lines = _wrap(DISCLAIMER, foot_font, W - 2 * margin, measure)
        foot_h = 2 * pad + len(foot_lines) * line_h
        avail_w = W - 2 * margin

        def best_cw(c: int) -> int:
            cap = min(CARD_W, (avail_w - (c - 1) * gap) // c)
            if cap < 1:
                return 0
            if content_h(1, c) + foot_h > H:
                return 1
            lo, hi = 1, cap
            while lo < hi:  # maior cw que cabe na altura
                mid = (lo + hi + 1) // 2
                if content_h(mid, c) + foot_h <= H:
                    lo = mid
                else:
                    hi = mid - 1
            return lo

        if cols is None:
            cands = range(1, max(1, max(counts)) + 1)
            cols_n = max(cands, key=lambda c: (best_cw(c), -c))
        else:
            cols_n = max(1, cols)
        cw = max(1, best_cw(cols_n))
        if content_h(cw, cols_n) + foot_h > H:
            warnings.append("conteúdo não cabe no tamanho escolhido; use um tamanho maior ou menos cartas")
        if cols is None and n_side:  # main e sideboard lado a lado, se isso render carta maior
            sep = 3 * gap

            def split_cw(cm: int, cs: int) -> int:
                cap = min(CARD_W, (avail_w - sep - (cm + cs - 2) * gap) // (cm + cs))
                if cap < 1:
                    return 0
                r = max(-(-n_main // cm), -(-n_side // cs))

                def h(w: int) -> int:
                    return (margin + title_h + head_h + r * (round(w * CARD_H / CARD_W) + label_of(w)[1])
                            + (r - 1) * gap + gap + foot_h)
                lo, hi = 1, cap
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if h(mid) <= H:
                        lo = mid
                    else:
                        hi = mid - 1
                if h(lo) > H:
                    return 0
                # os cabeçalhos "Main deck (N)" e "Sideboard (N)" precisam caber na largura da própria grade
                need = 1.2 * measure("Sideboard (999)", head_font)
                m = min(cm, cs)
                return lo if m * lo + (m - 1) * gap >= need else 0

            best = max(((split_cw(cm, cs), -(cm + cs), cm, cs)
                        for cm in range(1, n_main + 1) for cs in range(1, n_side + 1)), default=None)
            if best and best[0] > cw:
                cw, split = best[0], (best[2], best[3])
                warnings.clear()
    else:
        cols_n = cols or ORIGINAL_COLS
        cw = CARD_W
        W = 2 * margin + cols_n * cw + (cols_n - 1) * gap
        foot_lines = _wrap(DISCLAIMER, foot_font, W - 2 * margin, measure)
        foot_h = 2 * pad + len(foot_lines) * line_h
        H = content_h(cw, cols_n) + foot_h

    ch = round(cw * CARD_H / CARD_W)
    qty_font, lh = label_of(cw)
    text_x1 = W - margin
    row_h = ch + lh

    def grid_w(c: int) -> int:
        return c * cw + (c - 1) * gap

    if split:
        cm, cs = split
        cols_n = cm
        block_w = grid_w(cm) + grid_w(cs) + 3 * gap
        x0 = max(margin, (W - block_w) // 2)
        r = max(-(-n_main // cm), -(-n_side // cs))
        total = margin + title_h + head_h + r * row_h + (r - 1) * gap + gap
        sects = [(n_main, cm, x0), (n_side, cs, x0 + grid_w(cm) + 3 * gap)]
    else:
        x0 = max(margin, (W - grid_w(cols_n)) // 2)
        total = content_h(cw, cols_n)
        sects = [(n, cols_n, x0) for n in counts]
    off = max(0, (H - foot_h - total) // 2) if fixed else 0  # centraliza o bloco na vertical

    y = margin + off
    title_box = None
    if has_title:
        title_box = (x0, y, max(x0 + 1, text_x1), y + title_h)
        y += title_h
    head_boxes, card_boxes, label_boxes = [], [], []
    for n, c, sx in sects:
        head_w = grid_w(c) if split else text_x1 - sx
        head_boxes.append((sx, y, max(sx + 1, sx + head_w), y + head_h))
        cb, lb = [], []
        for i in range(max(n, 0)):
            cx = sx + (i % c) * (cw + gap)
            cy = y + head_h + (i // c) * (row_h + gap)
            cb.append((cx, cy, cx + cw, cy + ch))
            lb.append((cx, cy + ch, cx + cw, cy + ch + lh))
        card_boxes.append(cb)
        label_boxes.append(lb)
        if not split:
            rr = max(1, -(-n // c))
            y += head_h + rr * row_h + (rr - 1) * gap + gap
    foot_top = H - foot_h
    footer_lines = [(t, (margin, foot_top + pad + i * line_h, text_x1, foot_top + pad + (i + 1) * line_h))
                    for i, t in enumerate(foot_lines)]
    if cw < SMALL_CARD_W:
        warnings.append(f"cartas pequenas ({cw}x{ch} px); use um tamanho maior")
    return Layout(W, H, cols_n, cw, ch, gap, margin, lh, title_font, head_font, qty_font, foot_font,
                  title_box, head_boxes, card_boxes, label_boxes, footer_lines, warnings)


# ---------- composição ----------
def _font(size: int, bold=False):
    names = (
        ["DejaVuSans-Bold.ttf", "arialbd.ttf", "LiberationSans-Bold.ttf"]
        if bold
        else ["DejaVuSans.ttf", "arial.ttf", "LiberationSans-Regular.ttf"]
    )
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except OSError:
            continue
    for d in ("/usr/share/fonts/TTF", "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/ttf-dejavu"):
        try:
            return ImageFont.truetype(os.path.join(d, names[0]), size)
        except OSError:
            continue
    return ImageFont.load_default(size)


BASE_RADIUS = 23  # raio do canto da carta moderna (~4,7% da largura)
_MASKS: dict[tuple, Image.Image] = {}


def _corner_radius(img: Image.Image) -> int:
    """Mede o canto do JPG pela diagonal: o fundo claro vai até ~0,29·r. A Alpha tem cantos bem mais
    redondos (r≈34) que as outras edições (r≈20); com o raio fixo sobrava branco nas quinas."""
    g = img.convert("L")
    w, h = g.size
    clear = 0
    for fx, fy in ((lambda i: i, lambda i: i), (lambda i: w - 1 - i, lambda i: i),
                   (lambda i: i, lambda i: h - 1 - i), (lambda i: w - 1 - i, lambda i: h - 1 - i)):
        n = 0  # os 4 cantos: o scan nem sempre é simétrico, vale o maior
        while n < min(80, w, h) and g.getpixel((fx(n), fy(n))) >= 128:
            n += 1
        clear = max(clear, n)
    return max(BASE_RADIUS, round(clear / 0.29) + 3)


def _corner_mask(radius: float = BASE_RADIUS, size: tuple[int, int] = (CARD_W, CARD_H)) -> Image.Image:
    """Máscara com cantos arredondados, suavizada (desenhada em 4x e reduzida). Cache por (raio, tamanho)."""
    key = (round(radius, 1), size)
    if key not in _MASKS:
        k = 4
        w, h = size
        big = Image.new("L", (w * k, h * k), 0)
        ImageDraw.Draw(big).rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=radius * k, fill=255)
        _MASKS[key] = big.resize(size, Image.LANCZOS)
    return _MASKS[key]


def _missing_card(name: str) -> Image.Image:
    img = Image.new("RGB", (CARD_W, CARD_H), (60, 40, 40))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, CARD_W - 5, CARD_H - 5), 22, outline=(200, 90, 90), width=4)
    d.multiline_text((CARD_W // 2, CARD_H // 2), "Não encontrada\n\n" + name, font=_font(28, True),
                     fill=FG, anchor="mm", align="center")
    return img


def _fit_title(d: ImageDraw.ImageDraw, title: str, size: int, max_w: int) -> tuple[str, ImageFont.FreeTypeFont]:
    """Reduz a fonte até ~60% e, se ainda não couber, corta com '…' (P7)."""
    floor = max(12, round(size * 0.6))
    for s in range(size, floor - 1, -1):
        f = _font(s, True)
        if d.textlength(title, font=f) <= max_w:
            return title, f
    f = _font(floor, True)
    t = title
    while len(t) > 1 and d.textlength(t + "…", font=f) > max_w:
        t = t[:-1]
    return t.rstrip() + "…", f


def compose(main: list[Resolved], side: list[Resolved], out_path: Path,
            title: str = "", cols: int | None = None, size: tuple[int, int] | None = DEFAULT_SIZE,
            log=print) -> Path:
    sections = [(f"Main deck ({sum(r.entry.qty for r in main)})", main)]
    if side:
        sections.append((f"Sideboard ({sum(r.entry.qty for r in side)})", side))

    def measure(text: str, sz: int) -> float:
        return _font(sz).getlength(text)

    lay = plan_layout(len(main), len(side), size, cols, has_title=bool(title), measure=measure)
    for w in lay.warnings:
        log(f"  aviso: {w}")
    canvas = Image.new("RGB", (lay.width, lay.height), BG)
    d = ImageDraw.Draw(canvas)
    f_head, f_qty, f_foot = _font(lay.head_font, True), _font(lay.qty_font, True), _font(lay.foot_font)

    if title and lay.title_box:
        x0, y0, x1, y1 = lay.title_box
        text, f_title = _fit_title(d, title, lay.title_font, x1 - x0)
        d.text((x0, y0), text, font=f_title, fill=FG)

    scale = lay.card_w / CARD_W
    for (head, items), hb, cbs, lbs in zip(sections, lay.head_boxes, lay.card_boxes, lay.label_boxes):
        d.text((hb[0], hb[1]), head, font=f_head, fill=ACCENT)
        for r, cb, lb in zip(items, cbs, lbs):
            if r.image:
                with Image.open(r.image) as im:
                    card_img = im.convert("RGB")
                if card_img.size != (CARD_W, CARD_H):  # só acontece se o Scryfall mudar o tamanho
                    card_img = card_img.resize((CARD_W, CARD_H), Image.LANCZOS)
                radius = _corner_radius(card_img)
            else:
                card_img = _missing_card(r.entry.name)
                radius = BASE_RADIUS
            size_px = (lay.card_w, lay.card_h)
            if size_px != (CARD_W, CARD_H):  # só reduz, proporcional; nada é desenhado por cima
                card_img = card_img.resize(size_px, Image.LANCZOS)
                radius = radius * scale + 1  # +1 px: a suavização do LANCZOS não deixa quina clara
            canvas.paste(card_img, (cb[0], cb[1]), _corner_mask(radius, size_px))
            # quantidade fica ABAIXO da carta para não cobrir nada da imagem (inclusive o copyright)
            d.text(((lb[0] + lb[2]) // 2, (lb[1] + lb[3]) // 2), f"{r.entry.qty}x",
                   font=f_qty, fill=FG, anchor="mm")

    for text, (x0, y0, _x1, _y1) in lay.footer_lines:
        d.text((x0, y0), text, font=f_foot, fill=MUTED)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "JPEG", quality=90, optimize=True)
    return out_path


FORMATS = ["premodern", "oldschool", "vintage", "legacy", "pauper", "modern", "pioneer",
           "standard", "commander", "penny", "predh"]


def is_legal(card: dict, fmt: str) -> bool:
    return card.get("legalities", {}).get(fmt) in ("legal", "restricted")


def _drop_illegal(items: list[Resolved], fmt: str | None, log) -> tuple[list[Resolved], list[Issue]]:
    if not fmt:
        return items, []
    keep, illegal = [], []
    for r in items:
        if r.card and not is_legal(r.card, fmt):
            status = r.card.get("legalities", {}).get(fmt, "not_legal")
            illegal.append(Issue(r.card["name"], "banida" if status == "banned" else "não legal",
                                 r.entry.qty, list(r.entry.lines)))
            log(f"  fora do formato {fmt}: {r.card['name']} ({status}) — removida da imagem")
        else:
            keep.append(r)
    return keep, illegal


# Formatos com mínimo de cartas no main e máximo no side, para os avisos da janela.
DECK_RULES = {f: (60, 15) for f in ("premodern", "oldschool", "vintage", "legacy", "pauper", "modern",
                                    "pioneer", "standard", "penny")}
DECK_RULES.update({"commander": (100, 0), "predh": (100, 0)})


def run(main_entries: list[Entry], side_entries: list[Entry], *, title: str, cols: int | None = None,
        size: tuple[int, int] | None = DEFAULT_SIZE, fmt: str | None = "premodern",
        out_path: Path | None = None, log=print, progress=None, cancel: threading.Event | None = None) -> Result:
    """Gera a imagem e devolve um Result. fmt=None desliga o filtro.
    O título é obrigatório e dá nome ao arquivo (ver filename_from_title), salvo se out_path for passado.
    Com `cancel` setado, levanta Cancelled no próximo ponto de checagem (nada é gravado)."""
    t0 = time.monotonic()
    title = title.strip()
    if not title:
        raise ValueError("Informe o título do deck.")
    out = out_path or downloads_dir() / filename_from_title(title)
    client = Client(log=log)
    log(f"Consultando Scryfall ({len(main_entries) + len(side_entries)} cartas distintas)"
        + (f", formato {fmt}…" if fmt else "…"))
    corrected: list[Issue] = []
    main, ill_main = _drop_illegal(resolve(client, main_entries, fmt, corrected), fmt, log)
    _check(cancel)
    side, ill_side = _drop_illegal(resolve(client, side_entries, fmt, corrected), fmt, log)
    _check(cancel)
    all_items = main + side
    log("Baixando imagens (com cache local)…")
    fetch_images(client, all_items, progress, cancel)
    _check(cancel)
    missing = [Issue(r.entry.name, "não encontrada", r.entry.qty, list(r.entry.lines))
               for r in all_items if r.card is None]
    compose(main, side, out, title=title, cols=cols, size=size, log=log)
    log(f"Requisições feitas ao Scryfall: {client.requests_made}")
    with Image.open(out) as im:
        size = im.size
    return Result(out, corrected, ill_main + ill_side, missing, sum(1 for r in all_items if r.image), client.cache_hits,
                  time.monotonic() - t0, size, sum(r.entry.qty for r in main), sum(r.entry.qty for r in side))
