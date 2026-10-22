# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Which of a species row's script variables a modder can use, and who else keeps each one.

    s = vars.survey(prog, game.em(75))     # {1: Var(1, ENGINE, ...), 2: Var(2, FREE, ...), ...}
    vars.free(s)                           # [2]

`set_var`, `reset_var`, `if_var` and `if_var3_zero` name a variable, 1 to 9 (`catalogue.VARS`).
Variables 1 to 4 are bytes of the monster (`BYTE`); 5 to 9 call species code and keep nothing
(`CODE`). Of the bytes, the engine owns what `ENGINE` says it does, for every species; a mere
zeroing is a reset, not ownership. A variable is, in order: ENGINE, SPECIES (the species overlay
stores to its byte or calls a helper that does, `HELPERS`), SCRIPT (the script names it), else
FREE: safe to keep a value in.

The species scan reads the overlay's code only: stores (`sb`, `sh`, `sw`) at exactly a variable's
offset, through any base register, and `jal` to a helper. The engine's own code has other structs
with a field at the same offset, so it is never scanned. Without an overlay the survey sees the
script and the engine only.
"""

from __future__ import annotations

import zlib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from rabbitizer import InstrId

from ... import addresses as a
from ... import mips
from ...addresses import Address
from ...overlay import Overlay
from . import catalogue as cat
from . import ir
from .opcodes import Op

__all__ = [
    "BYTE",
    "CODE",
    "ENGINE",
    "HELPERS",
    "MEANING",
    "Fact",
    "Helper",
    "Kind",
    "Sites",
    "Var",
    "free",
    "scan",
    "survey",
    "var_of",
]


class Kind(StrEnum):
    FREE = "free"
    SCRIPT = "script"
    ENGINE = "engine"
    SPECIES = "species"
    CODE = "code"


MEANING: Mapping[Kind, str] = {
    Kind.FREE: "free: nothing keeps a value in it, safe to use",
    Kind.SCRIPT: "script: this script keeps a value in it, safe only as the script does",
    Kind.ENGINE: "engine: the engine writes it every frame, not safe",
    Kind.SPECIES: "species: the species' own code stores to it, not safe",
    Kind.CODE: "code: species code answers it and no byte keeps a value, not safe",
}
"""What each kind says to a modder, for every place that shows one."""

BYTE: Mapping[int, int] = {
    1: a.ENTITY.SCRIPT_VAR_1,
    2: a.ENTITY.SCRIPT_VAR_2,
    3: a.ENTITY.SCRIPT_VAR_3,
    4: a.ENTITY.SCRIPT_VAR_4,
}
"""The variables that are a byte of the entity, with its offset."""
CODE: Mapping[int, tuple[int, int]] = {
    5: (0x94, 0x98),
    6: (0x9C, 0xA0),
    7: (0xA4, 0xA8),
    8: (0xCC, 0xD0),
    9: (0xD4, 0xD8),
}
"""The species vtable slots `set_var` and `if_var` call for the variables without a byte."""


@dataclass(frozen=True)
class Fact:
    """One thing the engine does to a variable."""

    text: str
    owns: bool = False
    """The engine keeps the variable itself; false for a mere reset."""
    op: str | None = None
    """An AI-script op that has this effect, counted in the script."""


_STEP = f"{a.MONSTER_STEP.name} (every species)"
_SPAWN = Fact(f"{a.MONSTER_SPAWN_INIT.name} zeroes it at spawn")
ENGINE: Mapping[int, tuple[Fact, ...]] = {
    1: (Fact(f"{_STEP} counts it down every frame while +0x4C4 is 0", owns=True), _SPAWN),
    2: (
        _SPAWN,
        Fact(f"{a.MAIN6_ENTRY_CLEAR.name} zeroes it on entering main 6"),
        Fact(f"{a.CLEAR_COUNTERS.name} zeroes it", op="clear_counters"),
    ),
    3: (
        Fact(
            f"{_STEP} zeroes it every frame while {a.ENTITY.COMBAT_MODE.name} is 1,"
            " else counts it down",
            owns=True,
        ),
        _SPAWN,
    ),
}
"""What the engine does to each variable (checked statically over every species vtable)."""


@dataclass(frozen=True)
class Helper:
    """A game_task function that writes a variable when a species calls it."""

    var: int
    fn: Address
    text: str


HELPERS = (
    Helper(1, a.SET_SCRIPT_VAR_1, "sets it to 2"),
    Helper(2, a.GAUGE_A_LATCH, "sets it to 1 when gauge A runs out"),
)
"""The shared writers; an overlay that calls one owns the variable's value in that species."""

_STORES = frozenset([InstrId.cpu_sb, InstrId.cpu_sh, InstrId.cpu_sw])
_SCANNED: dict[tuple[int, int, int], Sites] = {}


@dataclass(frozen=True)
class Sites:
    """What an overlay's code does to the variables."""

    stores: Mapping[int, int]
    """Stores at a variable's offset, by variable."""
    calls: Mapping[int, int]
    """`jal`s to each helper, by its address."""


@dataclass(frozen=True)
class Var:
    number: int
    kind: Kind
    uses: int
    """Script nodes that name it."""
    why: tuple[str, ...]
    """Who else writes it, one short line each."""

    @property
    def reasons(self) -> tuple[str, ...]:
        """What to tell a modder of the kind: how much the script names it, then `why`."""
        n = self.uses
        if n == 0:
            named = "no script node names it"
        else:
            named = "1 script node names it" if n == 1 else f"{n} script nodes name it"
        return (named, *self.why)


def var_of(op: Op, args: Mapping[str, ir.Value]) -> int | None:
    """The script variable 1 to 9 a node of `op` with `args` sets or tests; None for none."""
    if op.name == "if_var3_zero":
        return 3
    v = next((args.get(i.name) for i in cat.entry(op).operands if i.fmt is cat.Fmt.VAR), None)
    return v if isinstance(v, int) and 1 <= v <= cat.VARS else None


def scan(overlay: Overlay) -> Sites:
    """The overlay's stores to a variable's byte and calls of a helper; kept per overlay content,
    as the scan decodes all of its code."""
    key = (overlay.load, len(overlay.file), zlib.crc32(overlay.file))
    if key not in _SCANNED:
        at = {offset: v for v, offset in BYTE.items()}
        fns = {h.fn for h in HELPERS}
        stores: Counter[int] = Counter()
        calls: Counter[int] = Counter()
        for ins in mips.instructions(overlay, overlay.text.start, overlay.text.stop):
            if ins.uniqueId in _STORES:
                if (v := at.get(ins.getProcessedImmediate())) is not None:
                    stores[v] += 1
            elif ins.uniqueId == InstrId.cpu_jal and ins.getInstrIndexAsVram() in fns:
                calls[ins.getInstrIndexAsVram()] += 1
        _SCANNED[key] = Sites(dict(stores), dict(calls))
    return _SCANNED[key]


def survey(p: ir.Program, overlay: Overlay | None = None) -> dict[int, Var]:
    """Every variable, 1 to `catalogue.VARS`, as `p` and the engine use it and, with `overlay`
    (the species overlay that serves the row), as its code does."""
    ops = {f.op for facts in ENGINE.values() for f in facts if f.op}
    named: Counter[int] = Counter()
    counted: Counter[str] = Counter()
    for n in p.nodes():
        if isinstance(n, ir.Insn | ir.Block):
            if (v := var_of(n.op, n.args)) is not None:
                named[v] += 1
            if n.op.name in ops:
                counted[n.op.name] += 1
    sites = None if overlay is None else scan(overlay)
    return {v: _var(v, named[v], sites, counted) for v in range(1, cat.VARS + 1)}


def _var(v: int, uses: int, sites: Sites | None, counted: Mapping[str, int]) -> Var:
    if v not in BYTE:
        set_, test = CODE[v]
        return Var(
            v, Kind.CODE, uses, (f"no byte: set_var calls vtable +0x{set_:X}, if_var +0x{test:X}",)
        )
    facts = ENGINE.get(v, ())
    why = [f.text + _in_script(counted[f.op]) if f.op else f.text for f in facts]
    stores = 0 if sites is None else sites.stores.get(v, 0)
    called = [] if sites is None else [(h, sites.calls.get(h.fn, 0)) for h in HELPERS if h.var == v]
    if stores:
        why.append(
            f"{stores} store{'' if stores == 1 else 's'} in the species overlay hit its byte"
        )
    for h, n in called:
        if n:
            plural = "" if n == 1 else "s"
            why.append(f"{n} call{plural} to {h.fn.name} in the species overlay: it {h.text}")
    if any(f.owns for f in facts):
        kind = Kind.ENGINE
    elif stores or any(n for _, n in called):
        kind = Kind.SPECIES
    else:
        kind = Kind.SCRIPT if uses else Kind.FREE
    if sites is None and kind in (Kind.FREE, Kind.SCRIPT):
        why.append("the species overlay is not scanned")
    return Var(v, kind, uses, tuple(why))


def _in_script(n: int) -> str:
    return f" ({n} in this script)" if n else ""


def free(s: Mapping[int, Var]) -> list[int]:
    """The variables a script may keep a value in, ascending."""
    return sorted(v.number for v in s.values() if v.kind is Kind.FREE)
