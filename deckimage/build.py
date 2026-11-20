"""Orquestra: lista -> Scryfall -> imagens -> <titulo>.jpg."""
from __future__ import annotations

import os
import re
import subprocess
import unicodedata
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .decklist import Entry
from .scryfall import Client, front_image_url

CARD_W, CARD_H = 488, 680  # tamanho "normal" do Scryfall, usado sem redimensionar
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


def downloads_dir() -> Path:
    if not sys.platform.startswith("win"):
        try:
            out = subprocess.run(["xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True, timeout=5)
            p = Path(out.stdout.strip())
            if out.returncode == 0 and p.is_dir() and p != Path.home():
                return p
        except (OSError, subprocess.SubprocessError):
            pass
    d = Path.home() / "Downloads"
    d.mkdir(exist_ok=True)
    return d


# ---------- resolução ----------
def resolve(client: Client, entries: list[Entry], fmt: str | None = None) -> list[Resolved]:
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
        out.append(Resolved(e, card))
    _use_oldest_printing(client, out, fmt)
    return _merge_same_card(out)


def _oracle(card: dict) -> str | None:
    return card.get("oracle_id") or (card.get("card_faces") or [{}])[0].get("oracle_id")


# Coleções de onde vêm as imagens: Fourth Edition (1995) até Scourge (2003), só core e expansões
# (fica de fora Alpha/Beta/Revised, Portal, Unglued, decks de caixa etc.).
PRINT_ERA = " date>=4ed date<=scg (st:core or st:expansion)"


def _use_oldest_printing(client: Client, items: list[Resolved], fmt: str | None = None):
    """Sem edição na lista -> impressão mais antiga (evita Marvel, Star Trek etc.).
    No Premodern, só entre Fourth Edition e Scourge; se não houver, cai para a mais antiga em geral."""
    todo = [r for r in items if r.card and not r.entry.set_code]
    if not todo:
        return
    names = list(dict.fromkeys(r.card["name"] for r in todo))
    if fmt != "premodern":  # a faixa de coleções só vale para Premodern
        chosen = {_oracle(c): c for c in client.oldest_printings(names, fmt)}
        for r in todo:
            r.card = chosen.get(_oracle(r.card), r.card)
        return
    chosen = {_oracle(c): c for c in client.oldest_printings(names, fmt, PRINT_ERA)}
    rest = list(dict.fromkeys(r.card["name"] for r in todo if _oracle(r.card) not in chosen))
    if rest:
        client.log(f"  sem impressão entre Fourth Edition e Scourge: {', '.join(rest)} — usando a mais antiga")
        for c in client.oldest_printings(rest, fmt):
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
        else:
            merged[key] = r
            out.append(r)
    return out


def fetch_images(client: Client, items: list[Resolved], progress=None):
    total = len(items)
    for i, r in enumerate(items, 1):
        if r.card:
            url = front_image_url(r.card)
            if url:
                r.image = client.image(url)
        if progress:
            progress(i, total)


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


def _section_height(n: int, cols: int) -> int:
    rows = max(1, -(-n // cols))
    return 90 + rows * (CARD_H + LABEL_H) + (rows - 1) * GAP


_MASK: Image.Image | None = None


def _corner_mask() -> Image.Image:
    """Máscara com cantos arredondados (raio ~4,7% da largura, como na carta física), suavizada."""
    global _MASK
    if _MASK is None:
        k = 4  # desenha em 4x e reduz, para a borda sair sem serrilhado
        big = Image.new("L", (CARD_W * k, CARD_H * k), 0)
        ImageDraw.Draw(big).rounded_rectangle((0, 0, CARD_W * k - 1, CARD_H * k - 1), radius=23 * k, fill=255)
        _MASK = big.resize((CARD_W, CARD_H), Image.LANCZOS)
    return _MASK


def _missing_card(name: str) -> Image.Image:
    img = Image.new("RGB", (CARD_W, CARD_H), (60, 40, 40))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4, 4, CARD_W - 5, CARD_H - 5), 22, outline=(200, 90, 90), width=4)
    d.multiline_text((CARD_W // 2, CARD_H // 2), "Não encontrada\n\n" + name, font=_font(28, True),
                     fill=FG, anchor="mm", align="center")
    return img


def compose(main: list[Resolved], side: list[Resolved], out_path: Path,
            title: str = "", cols: int = 8) -> Path:
    sections = [(f"Main deck ({sum(r.entry.qty for r in main)})", main)]
    if side:
        sections.append((f"Sideboard ({sum(r.entry.qty for r in side)})", side))

    width = MARGIN * 2 + cols * CARD_W + (cols - 1) * GAP
    title_h = 110 if title else 0
    footer_h = 90
    height = MARGIN + title_h + sum(_section_height(len(s), cols) + GAP for _, s in sections) + footer_h

    canvas = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(canvas)
    f_title, f_head, f_qty, f_foot = _font(72, True), _font(52, True), _font(44, True), _font(24)

    y = MARGIN
    if title:
        d.text((MARGIN, y), title, font=f_title, fill=FG)
        y += title_h

    for head, items in sections:
        d.text((MARGIN, y), head, font=f_head, fill=ACCENT)
        y += 90
        for i, r in enumerate(items):
            col, row = i % cols, i // cols
            x = MARGIN + col * (CARD_W + GAP)
            cy = y + row * (CARD_H + LABEL_H + GAP)
            if r.image:
                with Image.open(r.image) as im:
                    card_img = im.convert("RGB")
                if card_img.size != (CARD_W, CARD_H):  # só acontece se o Scryfall mudar o tamanho
                    card_img = card_img.resize((CARD_W, CARD_H), Image.LANCZOS)
            else:
                card_img = _missing_card(r.entry.name)
            canvas.paste(card_img, (x, cy), _corner_mask())  # cantos arredondados, sem o branco do JPG
            # quantidade fica ABAIXO da carta para não cobrir nada da imagem (inclusive o copyright)
            d.text((x + CARD_W // 2, cy + CARD_H + LABEL_H // 2), f"{r.entry.qty}x",
                   font=f_qty, fill=FG, anchor="mm")
        y += _section_height(len(items), cols) - 90 + GAP

    d.text((MARGIN, height - footer_h + 30), DISCLAIMER, font=f_foot, fill=MUTED)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "JPEG", quality=90, optimize=True)
    return out_path


FORMATS = ["premodern", "oldschool", "vintage", "legacy", "pauper", "modern", "pioneer",
           "standard", "commander", "penny", "predh"]


def is_legal(card: dict, fmt: str) -> bool:
    return card.get("legalities", {}).get(fmt) in ("legal", "restricted")


def _drop_illegal(items: list[Resolved], fmt: str | None, log) -> tuple[list[Resolved], list[str]]:
    if not fmt:
        return items, []
    keep, illegal = [], []
    for r in items:
        if r.card and not is_legal(r.card, fmt):
            status = r.card.get("legalities", {}).get(fmt, "not_legal")
            illegal.append(f"{r.entry.qty}x {r.card['name']} ({'banida' if status == 'banned' else 'não legal'})")
            log(f"  fora do formato {fmt}: {r.card['name']} ({status}) — removida da imagem")
        else:
            keep.append(r)
    return keep, illegal


def run(main_entries: list[Entry], side_entries: list[Entry], *, title: str, cols=8, fmt: str | None = "premodern",
        out_path: Path | None = None, log=print, progress=None) -> tuple[Path, list[str], list[str]]:
    """Devolve (caminho, nao_encontradas, fora_do_formato). fmt=None desliga o filtro.
    O título é obrigatório e dá nome ao arquivo (ver filename_from_title), salvo se out_path for passado."""
    title = title.strip()
    if not title:
        raise ValueError("Informe o título do deck.")
    out = out_path or downloads_dir() / filename_from_title(title)
    client = Client(log=log)
    log(f"Consultando Scryfall ({len(main_entries) + len(side_entries)} cartas distintas)"
        + (f", formato {fmt}…" if fmt else "…"))
    main, ill_main = _drop_illegal(resolve(client, main_entries, fmt), fmt, log)
    side, ill_side = _drop_illegal(resolve(client, side_entries, fmt), fmt, log)
    all_items = main + side
    log("Baixando imagens (com cache local)…")
    fetch_images(client, all_items, progress)
    not_found = [r.entry.name for r in all_items if r.card is None]
    compose(main, side, out, title=title, cols=cols)
    log(f"Requisições feitas ao Scryfall: {client.requests_made}")
    return out, not_found, ill_main + ill_side
