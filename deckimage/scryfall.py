"""Cliente Scryfall que respeita as regras da API.

- User-Agent identificando o app + Accept em toda requisição;
- no mínimo 100 ms entre QUALQUER requisição (API e imagens), ou seja, < 10 req/s;
- HTTP 429 -> espera e tenta de novo, com backoff;
- busca em lote via /cards/collection (75 cartas por chamada) em vez de 1 chamada por carta;
- imagens ficam em cache local, então gerar a mesma lista de novo não baixa nada.
"""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__

API = "https://api.scryfall.com"
USER_AGENT = f"DeckImageMTG/{__version__}"
MIN_INTERVAL = 0.1  # 100 ms
COLLECTION_MAX = 75


def _ssl_context() -> ssl.SSLContext:
    """Usa os certificados do certifi (vão dentro do executável), para não depender
    de onde cada distro Linux guarda os seus."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


SSL_CTX = _ssl_context()


class ScryfallError(Exception):
    pass


def cache_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    d = base / "deckimage-mtg" / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Client:
    def __init__(self, log=print):
        self._lock = threading.Lock()
        self._last = 0.0
        self.log = log
        self.requests_made = 0

    # ---------- baixo nível ----------
    def _wait_turn(self):
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < MIN_INTERVAL:
                time.sleep(MIN_INTERVAL - delta)
            self._last = time.monotonic()

    def _request(self, url: str, *, data: bytes | None = None, accept="application/json") -> bytes:
        headers = {"User-Agent": USER_AGENT, "Accept": accept}
        if data is not None:
            headers["Content-Type"] = "application/json"
        delay = 1.0
        for attempt in range(5):
            self._wait_turn()
            self.requests_made += 1
            req = urllib.request.Request(url, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    wait = float(e.headers.get("Retry-After") or delay)
                    self.log(f"Scryfall respondeu {e.code}; aguardando {wait:.0f}s e tentando de novo…")
                    time.sleep(wait)
                    delay *= 2
                    continue
                body = e.read()
                try:
                    details = json.loads(body).get("details", "")
                except Exception:
                    details = body[:200].decode(errors="replace")
                raise ScryfallError(f"HTTP {e.code}: {details}") from None
            except urllib.error.URLError as e:
                if attempt == 4:
                    raise ScryfallError(f"Falha de rede: {e.reason}") from None
                time.sleep(delay)
                delay *= 2
        raise ScryfallError("Muitas tentativas sem sucesso (rate limit).")

    def _json(self, url: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        return json.loads(self._request(url, data=data))

    # ---------- cartas ----------
    def collection(self, identifiers: list[dict]) -> tuple[list[dict], list[dict]]:
        """Busca em lote. Devolve (cartas_encontradas, identificadores_nao_encontrados)."""
        found, missing = [], []
        for i in range(0, len(identifiers), COLLECTION_MAX):
            chunk = identifiers[i : i + COLLECTION_MAX]
            res = self._json(f"{API}/cards/collection", {"identifiers": chunk})
            found.extend(res.get("data", []))
            missing.extend(res.get("not_found", []))
        return found, missing

    def oldest_printings(self, names: list[str], fmt: str | None = None, extra: str = "",
                         batch: int = 20) -> list[dict]:
        """Busca em lote a impressão mais antiga (em papel) de cada nome exato.
        `extra` acrescenta filtros à busca (ex.: faixa de coleções)."""
        out: list[dict] = []
        for i in range(0, len(names), batch):
            terms = " or ".join('!"' + n.replace('"', '') + '"' for n in names[i : i + batch])
            legal = f" legal:{fmt}" if fmt else ""
            url = f"{API}/cards/search?" + urllib.parse.urlencode(
                {"q": f"({terms}){legal}{extra} game:paper -is:promo prefer:oldest", "unique": "cards"})
            while url:
                try:
                    res = self._json(url)
                except ScryfallError as e:
                    if "HTTP 404" not in str(e):  # 404 = nenhuma carta no filtro, não é erro
                        self.log(f"  aviso: busca de edição falhou ({e}); mantendo a padrão")
                    break
                out.extend(res.get("data", []))
                url = res.get("next_page") if res.get("has_more") else None
        return out

    def named_fuzzy(self, name: str, set_code: str | None = None) -> dict | None:
        params = {"fuzzy": name}
        if set_code:
            params["set"] = set_code
        try:
            return self._json(f"{API}/cards/named?" + urllib.parse.urlencode(params))
        except ScryfallError as e:
            self.log(f"  não encontrada: {name} ({e})")
            return None

    # ---------- imagens ----------
    def image(self, url: str) -> Path:
        """Baixa (ou reaproveita do cache) a imagem original, sem modificá-la."""
        ext = Path(urllib.parse.urlparse(url).path).suffix or ".jpg"
        path = cache_dir() / (hashlib.sha1(url.encode()).hexdigest() + ext)
        if path.exists() and path.stat().st_size > 0:
            return path
        content = self._request(url, accept="*/*")
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(content)
        tmp.replace(path)
        return path


def front_image_url(card: dict, size: str = "normal") -> str | None:
    """Cartas normais têm image_uris na raiz; dupla-face guardam em card_faces."""
    if "image_uris" in card:
        return card["image_uris"].get(size)
    faces = card.get("card_faces") or []
    if faces and "image_uris" in faces[0]:
        return faces[0]["image_uris"].get(size)
    return None
