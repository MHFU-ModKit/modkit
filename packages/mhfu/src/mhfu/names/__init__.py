# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Names for MHFU EU's code from the MHP2G decomp: the decomp names functions of the JP release
(ULJM-05500), and matching JP's code to EU's module by module carries each name over.

    table = build(Extracted.find(), Extracted.find(env="MHP2G_DATA"), Decomp.find())
    write(table)                       # to symbols.names_path(), which symbols.name() reads

The table is derived from both games and is never committed. Besides `names` it holds
`functions` (every matched EU function and its JP address, named or not: the JP address finds
its assembly in the decomp), `data` (named data the matched code refers to) and `coverage`.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rabbitizer import InstrId

from .. import files, symbols
from ..eboot import Eboot
from ..files import EM_SPECIES, Extracted
from ..mips import instructions
from ..overlay import Overlay
from .decomp import SHARED, Decomp, reach
from .demangle import demangle
from .match import MIN_RATIO, Match, Side, align, similarity

MODULES = (*SHARED, *(f"em{s:02d}" for s in EM_SPECIES))


class Module:
    """One module in both releases, and which JP function is which EU one."""

    def __init__(self, name: str, jp: Eboot | Overlay, eu: Eboot | Overlay) -> None:
        self.name, self.jp_image, self.eu_image = name, jp, eu
        self.jp, self.eu = Side(jp, jp.text), Side(eu, eu.text)
        self.matches = align(self.jp, self.eu)
        self.taken = {m.eu for m in self.matches.values()}

    def add(self, match: Match) -> None:
        self.matches[match.jp] = match
        self.taken.add(match.eu)

    def to_eu(self, va: int) -> tuple[int, Match] | None:
        """The EU address of JP code address `va`: a function start, or any instruction of a
        function matched to the same code."""
        f = self.jp.at(va)
        m = None if f is None else self.matches.get(f.start)
        if f is None or m is None:
            return None
        if va == f.start or self.same(m):
            return m.eu + va - f.start, m
        return None

    def same(self, m: Match) -> bool:
        return self.jp.starts[m.jp].words == self.eu.starts[m.eu].words


def build(
    eu: Extracted,
    jp: Extracted,
    decomp: Decomp,
    progress: Callable[[str], object] = lambda step: None,
) -> dict[str, Any]:
    """The names table, as JSON-ready data."""
    jp_code, eu_code = jp.code(files.JP), eu.code(files.EU)
    for name in MODULES:
        img = jp_code[name]
        decomp.check(name, img.file if isinstance(img, Overlay) else jp.boot.read_bytes())
    modules = {}
    for name in MODULES:
        progress(f"matching {name}")
        modules[name] = Module(name, jp_code[name], eu_code[name])
    calls = _propagate(modules)
    code = decomp.names({n: m.jp.text for n, m in modules.items()})
    names: dict[str, Any] = {}
    coverage: dict[str, Any] = {}
    for name, mod in modules.items():
        mapped = 0
        for va, symbol in sorted(code[name].items()):
            hit = mod.to_eu(va)
            if hit is None:
                continue
            mapped += 1
            eu_va, m = hit
            names[_hex(eu_va)] = {
                "name": demangle(symbol),
                "symbol": symbol,
                "how": m.how,
                "jp": _hex(va),
                "module": name,
            }
        hows = Counter(m.how for m in mod.matches.values())
        coverage[name] = {
            "jp_functions": len(mod.jp.functions),
            "eu_functions": len(mod.eu.functions),
            "matched": dict(sorted(hows.items())),
            "named": len(code[name]),
            "named_mapped": mapped,
        }
    coverage["calls"] = calls
    functions = {
        _hex(m.eu): {"jp": _hex(m.jp), "module": name, "how": m.how, "ratio": m.ratio}
        for name, mod in modules.items()
        for m in sorted(mod.matches.values(), key=lambda m: m.eu)
    }
    return {
        "source": f"tclamb/mhp2g-decomp {decomp.commit}: ULJM-05500 code matched to ULES-01213",
        "names": names,
        "functions": functions,
        "data": _data(modules, decomp),
        "coverage": coverage,
    }


def write(table: dict[str, Any], path: Path | None = None) -> Path:
    path = path or symbols.names_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(table, indent=1) + "\n", encoding="utf-8")
    return path


def outbound(em: Overlay) -> Counter[int]:
    """Call sites by target of each jal from an em overlay out of its own code."""
    calls = (
        i for i in instructions(em, em.text.start, em.text.stop) if i.uniqueId == InstrId.cpu_jal
    )
    return Counter(t for t in (i.getInstrIndexAsVram() for i in calls) if t not in em.text)


def _hex(va: int) -> str:
    return f"0x{va:08X}"


def _owner(modules: dict[str, Module], caller: str, va: int, side: str) -> Module | None:
    """The module whose code `va` is, seen from code in `caller`."""
    for name in reach(caller):
        mod = modules[name]
        if va in (mod.jp if side == "jp" else mod.eu).text:
            return mod
    return None


def _propagate(modules: dict[str, Module]) -> dict[str, int]:
    """Match the callees of matched same-code pairs, call site by call site, until nothing
    changes; then count how many call sites of same-code pairs the matches agree with."""
    changed = True
    while changed:
        changed = False
        for mod in modules.values():
            for m in list(mod.matches.values()):
                for tj, te in _callees(mod, m):
                    jm = _owner(modules, mod.name, tj, "jp")
                    em = _owner(modules, mod.name, te, "eu")
                    if jm is None or jm is not em or tj in jm.matches or te in jm.taken:
                        continue
                    f, g = jm.jp.starts.get(tj), jm.eu.starts.get(te)
                    if f is None or g is None:
                        continue
                    # a site can call another function in EU (SJIS and UTF-8 variants)
                    if (r := similarity(f, g)) > MIN_RATIO:
                        jm.add(Match(tj, te, "call", round(r, 3)))
                        changed = True
    stats: Counter[str] = Counter()
    for mod in modules.values():
        for m in mod.matches.values():
            for tj, te in _callees(mod, m):
                jm = _owner(modules, mod.name, tj, "jp")
                hit = None if jm is None else jm.to_eu(tj)
                stats["unmatched" if hit is None else "agree" if hit[0] == te else "disagree"] += 1
    return dict(sorted(stats.items()))


def _callees(mod: Module, m: Match) -> list[tuple[int, int]]:
    """(JP target, EU target) at each call site of a same-code pair."""
    if not mod.same(m):
        return []
    f, g = mod.jp.starts[m.jp], mod.eu.starts[m.eu]
    return [(tj, te) for (_, tj), (_, te) in zip(mod.jp.calls(f), mod.eu.calls(g), strict=True)]


def _data(modules: dict[str, Module], decomp: Decomp) -> dict[str, Any]:
    """Named data at the address each JP reference has in EU, from the lui/lo pairs of
    same-code pairs; `votes` is how many references agree, of `refs`."""
    spans = {n: _span(modules, n) for n in modules}
    seen: dict[tuple[str, int], Counter[int]] = defaultdict(Counter)
    for mod in modules.values():
        for m in mod.matches.values():
            if not mod.same(m):
                continue
            pj = mod.jp.pairs(mod.jp.starts[m.jp])
            pe = mod.eu.pairs(mod.eu.starts[m.eu])
            for key, vj in pj.items():
                owner = next((n for n in reach(mod.name) if vj in spans[n]), None)
                if key in pe and owner is not None:
                    seen[owner, vj][pe[key]] += 1
    named = decomp.names(spans)
    out = {}
    for (owner, vj), votes in sorted(seen.items()):
        symbol = named[owner].get(vj)
        if symbol is None or vj in modules[owner].jp.text:
            continue
        ve, n = votes.most_common(1)[0]
        out[_hex(ve)] = {
            "name": demangle(symbol),
            "symbol": symbol,
            "jp": _hex(vj),
            "module": owner,
            "votes": n,
            "refs": sum(votes.values()),
        }
    return out


def _span(modules: dict[str, Module], name: str) -> range:
    """Everything a module owns in JP memory: an overlay its image and bss; BOOT.BIN all below
    the overlays (its bss runs far past its image)."""
    img = modules[name].jp_image
    if isinstance(img, Overlay):
        return range(img.load, img.bss.stop)
    first = min(m.jp_image.base for m in modules.values() if isinstance(m.jp_image, Overlay))
    return range(img.base, first)
