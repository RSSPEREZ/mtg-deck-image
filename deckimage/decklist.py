"""Parser de listas de deck (formato texto usado por MTGO, Arena, Moxfield etc.)."""
from __future__ import annotations

import re
from dataclasses import dataclass

# "4 Lightning Bolt", "4x Lightning Bolt", "4 Lightning Bolt (M10) 146", "Lightning Bolt"
_LINE = re.compile(
    r"^\s*(?:(?P<qty>\d+)\s*x?\s+)?"
    r"(?P<name>.+?)"
    r"(?:\s+\((?P<set>[A-Za-z0-9]{2,6})\)(?:\s+(?P<num>[A-Za-z0-9\-★]+))?)?"
    r"\s*(?:\*F\*)?\s*$"
)
_SIDE_HEADER = re.compile(r"^\s*(sideboard|side|sb)\s*:?\s*$", re.I)
_SB_PREFIX = re.compile(r"^\s*SB:\s*", re.I)
_IGNORED_HEADER = re.compile(r"^\s*(deck|main ?deck|maindeck|commander|companion)\s*:?\s*$", re.I)


@dataclass
class Entry:
    qty: int
    name: str
    set_code: str | None = None
    collector_number: str | None = None

    def identifier(self) -> dict:
        if self.set_code and self.collector_number:
            return {"set": self.set_code.lower(), "collector_number": self.collector_number}
        if self.set_code:
            return {"name": self.name, "set": self.set_code.lower()}
        # /cards/collection não acha "Fire // Ice" pelo nome completo, só pela 1ª metade
        return {"name": self.name.split(" // ")[0]}


def parse_lines(text: str) -> tuple[list[Entry], list[Entry], list[str]]:
    """Devolve (main, side, linhas_invalidas).

    Uma linha "Sideboard" (ou prefixo "SB:") dentro do texto manda o resto para o side,
    então dá para colar a lista inteira num campo só.
    """
    main: list[Entry] = []
    side: list[Entry] = []
    bad: list[str] = []
    target = main
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("//", "#")):
            continue
        if _SIDE_HEADER.match(line):
            target = side
            continue
        if _IGNORED_HEADER.match(line):
            continue
        dest = target
        if _SB_PREFIX.match(line):
            line = _SB_PREFIX.sub("", line)
            dest = side
        m = _LINE.match(line)
        if not m or not m.group("name"):
            bad.append(raw)
            continue
        qty = int(m.group("qty") or 1)
        if qty <= 0:
            bad.append(raw)
            continue
        dest.append(Entry(qty, m.group("name").strip(), m.group("set"), m.group("num")))
    return _merge(main), _merge(side), bad


def _merge(entries: list[Entry]) -> list[Entry]:
    """Soma linhas repetidas da mesma carta, mantendo a ordem da primeira aparição."""
    out: dict[tuple, Entry] = {}
    for e in entries:
        key = (e.name.lower(), (e.set_code or "").lower(), e.collector_number or "")
        if key in out:
            out[key].qty += e.qty
        else:
            out[key] = Entry(e.qty, e.name, e.set_code, e.collector_number)
    return list(out.values())
