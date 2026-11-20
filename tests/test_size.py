"""Testes de tamanho de saída: parse_size, plan_layout, compose e validação da CLI. Sem rede."""
from __future__ import annotations

import itertools

import pytest
from PIL import Image, ImageDraw

from deckimage import app, build
from deckimage.build import CARD_H, CARD_W, DEFAULT_SIZE, SIZES, Resolved, compose, parse_size, plan_layout
from deckimage.decklist import Entry

FIXED = [s for _, s in SIZES if s is not None]
DECKS = {"1": (1, 0), "15+5": (15, 5), "60+15": (60, 15), "100": (100, 0)}


def boxes(lay):
    out = []
    if lay.title_box:
        out.append(("title", lay.title_box))
    out += [("head", b) for b in lay.head_boxes]
    for sec in lay.card_boxes:
        out += [("card", b) for b in sec]
    for sec in lay.label_boxes:
        out += [("label", b) for b in sec]
    out += [("foot", b) for _, b in lay.footer_lines]
    return out


def overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def items(n):
    return [Resolved(Entry(qty=1, name=f"Carta {i}"), None) for i in range(n)]


# ---------- parse_size ----------
def test_sizes_tem_8_opcoes_e_original():
    assert len(SIZES) == 8
    assert sum(1 for _, s in SIZES if s is None) == 1
    assert DEFAULT_SIZE == (1920, 1080)


@pytest.mark.parametrize("txt,esperado", [
    ("1920x1080", (1920, 1080)), ("1920×1080", (1920, 1080)), (" 1080X1350 ", (1080, 1350)),
    ("1920 x 1080", (1920, 1080)), ("original", None), ("Original", None),
    ("480x480", (480, 480)), ("7680x7680", (7680, 7680)),
])
def test_parse_size_valido(txt, esperado):
    assert parse_size(txt) == esperado


@pytest.mark.parametrize("txt", ["abc", "", "1920", "100x100", "479x1000", "1000x479", "7681x1000",
                                 "1000x7681", "-1920x1080", "1920x", "x1080", "1920x1080x2", "1.5x1000"])
def test_parse_size_invalido(txt):
    with pytest.raises(ValueError):
        parse_size(txt)


# ---------- plan_layout (CA2/3/5/6/7/8) ----------
@pytest.mark.parametrize("size", FIXED)
@pytest.mark.parametrize("deck", DECKS)
@pytest.mark.parametrize("cols", [None, 10])
def test_layout_tamanho_exato_dentro_do_canvas_sem_sobreposicao(size, deck, cols):
    lay = plan_layout(*DECKS[deck], size, cols)
    assert (lay.width, lay.height) == size
    for nome, b in boxes(lay):
        assert 0 <= b[0] <= b[2] <= size[0] and 0 <= b[1] <= b[3] <= size[1], (nome, b)
    for (na, a), (nb, b) in itertools.combinations(boxes(lay), 2):
        assert not overlap(a, b), (na, a, nb, b)
    # carta: nunca ampliada e proporção mantida
    assert 1 <= lay.card_w <= CARD_W and 1 <= lay.card_h <= CARD_H
    assert abs(lay.card_w / lay.card_h - CARD_W / CARD_H) < 0.02 or lay.card_w < 30
    for sec in lay.card_boxes:
        for b in sec:
            assert (b[2] - b[0], b[3] - b[1]) == (lay.card_w, lay.card_h)


@pytest.mark.parametrize("size", FIXED)
@pytest.mark.parametrize("deck", DECKS)
def test_layout_numero_de_caixas(size, deck):
    n_main, n_side = DECKS[deck]
    lay = plan_layout(n_main, n_side, size, None)
    assert [len(s) for s in lay.card_boxes] == [n_main] + ([n_side] if n_side else [])
    assert [len(s) for s in lay.label_boxes] == [len(s) for s in lay.card_boxes]
    assert len(lay.head_boxes) == len(lay.card_boxes)


def test_layout_sem_titulo():
    lay = plan_layout(10, 0, (1920, 1080), None, has_title=False)
    assert lay.title_box is None


@pytest.mark.parametrize("size", FIXED)
def test_cols_manual_respeitada(size):
    lay = plan_layout(20, 5, size, 10)
    assert lay.cols == 10
    assert (lay.width, lay.height) == size
    # nenhuma linha do main tem mais de 10 cartas
    xs_por_linha = {}
    for b in lay.card_boxes[0]:
        xs_por_linha.setdefault(b[1], []).append(b)
    assert max(len(v) for v in xs_por_linha.values()) == 10


def test_auto_maximiza_carta_20_5_full_hd():
    auto = plan_layout(20, 5, (1920, 1080), None)
    for c in range(1, 26):
        assert auto.card_w >= plan_layout(20, 5, (1920, 1080), c).card_w, c
    assert auto.cols == plan_layout(20, 5, (1920, 1080), auto.cols).cols


@pytest.mark.parametrize("size", FIXED)
@pytest.mark.parametrize("deck", ["15+5", "60+15"])
# Auto pode usar main e sideboard lado a lado (carta >= a melhor grade empilhada).
def test_auto_maximiza_carta_varios(size, deck):
    n_main, n_side = DECKS[deck]
    auto = plan_layout(n_main, n_side, size, None)
    melhor = max(plan_layout(n_main, n_side, size, c).card_w for c in range(1, 31))
    assert auto.card_w >= melhor


def test_carta_pequena_gera_aviso():
    lay = plan_layout(60, 15, (1280, 720), None)
    assert lay.card_w < build.SMALL_CARD_W
    assert lay.warnings


def test_carta_grande_sem_aviso():
    assert not plan_layout(15, 5, (3840, 2160), None).warnings


def test_poucas_cartas_em_4k_nao_ampliam():
    lay = plan_layout(1, 0, (3840, 2160), None)
    assert (lay.card_w, lay.card_h) == (CARD_W, CARD_H)


def test_original_8_colunas_4168_de_largura():
    lay = plan_layout(60, 15, None, 8)
    assert lay.width == 4168
    assert (lay.card_w, lay.card_h) == (CARD_W, CARD_H)
    assert lay.cols == 8


def test_original_cols_none_usa_8():
    lay = plan_layout(60, 15, None, None)
    assert lay.cols == 8 and lay.width == 4168


def test_original_goblins_dimensoes_v120():
    # Goblins do exemplo (T1): 4168 x 2732
    lay = plan_layout(28, 15, None, 8)
    assert lay.width == 4168


# ---------- compose (CA2/CA7/CA8) com cartas falsas ----------
@pytest.mark.parametrize("size", FIXED)
@pytest.mark.parametrize("deck", ["1", "60+15"])
def test_compose_dimensoes_exatas(tmp_path, size, deck):
    n_main, n_side = DECKS[deck]
    log = []
    out = compose(items(n_main), items(n_side), tmp_path / "x.jpg", "Titulo", size=size, log=log.append)
    with Image.open(out) as im:
        assert im.size == size
    lay = plan_layout(n_main, n_side, size, None)
    assert bool(log) == bool(lay.warnings)


def test_compose_100_cartas_um_tamanho(tmp_path):
    out = compose(items(100), [], tmp_path / "c.jpg", "Commander", size=(1080, 1920), log=lambda _m: None)
    with Image.open(out) as im:
        assert im.size == (1080, 1920)


def test_compose_original(tmp_path):
    out = compose(items(9), items(2), tmp_path / "o.jpg", "T", cols=8, size=None, log=lambda _m: None)
    with Image.open(out) as im:
        assert im.size[0] == 4168


def test_compose_titulo_enorme_nao_quebra(tmp_path):
    out = compose(items(3), [], tmp_path / "t.jpg", "Titulo " * 60, size=(1080, 1080), log=lambda _m: None)
    with Image.open(out) as im:
        assert im.size == (1080, 1080)


def _carta_com_canto_branco(path):
    im = Image.new("RGB", (CARD_W, CARD_H), "white")
    ImageDraw.Draw(im).rounded_rectangle((0, 0, CARD_W - 1, CARD_H - 1), 23, fill=(20, 60, 160))
    im.save(path, "JPEG", quality=95)


@pytest.mark.parametrize("size", [(1920, 1080), (1080, 1350), (None)])
def test_cantos_sem_quina_branca(tmp_path, size):
    img = tmp_path / "c.jpg"
    _carta_com_canto_branco(img)
    r = [Resolved(Entry(qty=2, name="Teste"), {}, image=img) for _ in range(4)]
    out = compose(r, [], tmp_path / "o.jpg", "T", cols=None if size else 8, size=size, log=lambda _m: None)
    lay = plan_layout(4, 0, size, None if size else 8)
    with Image.open(out) as im:
        px = im.convert("RGB")
        for x0, y0, x1, y1 in lay.card_boxes[0]:
            for cx, cy in [(x0, y0), (x1 - 1, y0), (x0, y1 - 1), (x1 - 1, y1 - 1)]:
                p = px.getpixel((cx, cy))
                assert min(p) < 200, (size, (cx, cy), p)  # branco puro vazaria aqui
        # o interior da carta é azul
        x0, y0, x1, y1 = lay.card_boxes[0][0]
        p = px.getpixel(((x0 + x1) // 2, (y0 + y1) // 2))
        assert p[2] > 120 and p[0] < 80


def test_teste_de_canto_detecta_quina_branca(tmp_path):
    """Sanidade: sem máscara de canto, o pixel da quina seria branco (o teste acima falharia)."""
    img = tmp_path / "c.jpg"
    _carta_com_canto_branco(img)
    with Image.open(img) as im:
        assert min(im.getpixel((0, 0))) > 230


# ---------- CLI (CA9) ----------
BASE = ["--title", "T", "--main", "x.txt"]


@pytest.mark.parametrize("args", [
    ["--size", "abc"], ["--size", "100x100"], ["--size", "9000x1000"], ["--size", "1000x9000"],
    ["--size", ""], ["--cols", "0"], ["--cols", "x"], ["--cols", "41"], ["--cols", "-3"],
])
def test_cli_argumentos_invalidos(args, capsys):
    with pytest.raises(SystemExit) as e:
        app.cli(BASE + args)
    assert e.value.code == 2
    assert "error" in capsys.readouterr().err.lower()


def test_cli_sem_main_nem_side(capsys):
    with pytest.raises(SystemExit) as e:
        app.cli(["--title", "T"])
    assert e.value.code == 2


def test_cli_sem_titulo():
    with pytest.raises(SystemExit):
        app.cli(["--main", "x.txt"])


def test_cli_repassa_size_e_cols(tmp_path, monkeypatch):
    deck = tmp_path / "d.txt"
    deck.write_text("4 Goblin Lackey\n", encoding="utf-8")
    capt = {}

    class R:
        path, corrected, illegal, missing = tmp_path / "o.jpg", [], [], []

    def fake_run(main, side, **kw):
        capt.update(kw)
        return R

    monkeypatch.setattr(app, "run", fake_run)
    for extra, esp_size, esp_cols in [
        ([], DEFAULT_SIZE, None),
        (["--size", "1080x1350"], (1080, 1350), None),
        (["--size", "1920×1080", "--cols", "auto"], (1920, 1080), None),
        (["--size", "original", "--cols", "8"], None, 8),
        (["--size", "2000x1000", "--cols", "10"], (2000, 1000), 10),
    ]:
        assert app.cli(["--title", "T", "--main", str(deck)] + extra) == 0
        assert capt["size"] == esp_size and capt["cols"] == esp_cols, extra


# ---------- Auto: main e sideboard lado a lado / centralização (T5) ----------
def lado_a_lado(lay):
    return len(lay.head_boxes) == 2 and lay.head_boxes[0][0] != lay.head_boxes[1][0]


def test_lado_a_lado_ocorre_em_3_2_full_hd_e_carta_maior_que_empilhado():
    auto = plan_layout(3, 2, (1920, 1080), None)
    assert lado_a_lado(auto)
    assert auto.head_boxes[0][1] == auto.head_boxes[1][1]  # mesmo topo
    empilhado = max(plan_layout(3, 2, (1920, 1080), c).card_w for c in range(1, 31))
    assert auto.card_w > empilhado


def test_lado_a_lado_side_fica_a_direita_sem_sobrepor_o_main():
    lay = plan_layout(3, 2, (1920, 1080), None)
    max_x_main = max(b[2] for b in lay.card_boxes[0])
    min_x_side = min(b[0] for b in lay.card_boxes[1])
    assert min_x_side >= max_x_main + lay.gap


@pytest.mark.parametrize("size", FIXED)
@pytest.mark.parametrize("deck", ["3+2", "15+5", "60+15"])
def test_auto_conteudo_centralizado_na_horizontal(size, deck):
    n_main, n_side = map(int, deck.split("+"))
    lay = plan_layout(n_main, n_side, size, None)
    cartas = [b for sec in lay.card_boxes for b in sec]
    esq = min(b[0] for b in cartas)
    dir_ = lay.width - max(b[2] for b in cartas)
    assert abs(esq - dir_) <= 1 + lay.gap  # tolerância de arredondamento


def test_lado_a_lado_so_no_auto_manual_e_original_ficam_empilhados():
    manual = plan_layout(3, 2, (1920, 1080), 3)
    assert not lado_a_lado(manual)
    assert min(b[1] for b in manual.card_boxes[1]) > max(b[3] for b in manual.card_boxes[0])
    orig = plan_layout(3, 2, None, None)
    assert not lado_a_lado(orig)


def test_lado_a_lado_compose_tamanho_exato(tmp_path):
    out = compose(items(3), items(2), tmp_path / "x.jpg", "Titulo", size=(1920, 1080), log=lambda *_: None)
    assert Image.open(out).size == (1920, 1080)
