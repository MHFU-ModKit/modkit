# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A big monster's animation events: what MONSTER_VTABLE.ANIM_EVENTS (slot 30) does for the
executor entry a body part plays, read from the overlay.

The events are keyed by the PLAYING ENTRY (ENTITY.ANIM_INPUT), not by the behaviour pair: whichever
pair plays the entry runs its events, so an attack a handler never names still spawns. Per entry
the function tests the clip cursor against literal frames (CLIP_CURSOR_CROSSES, _REACHED) and
spawns attacks and effects behind them.

    table = events.table(code, fn, species)                 # entry -> Entry
    table[46].attacks                                       # (Spawn(18, 120.0), ...)

A species' slot-30 function is either the table itself or a thin switch on ENTITY.SPECIES to one
(em75's); the entry's branch is found by the `addiu a0, zero, INPUT;
beq v1, a0` link the compiler lays per entry, and walked with the input in v1. A species whose
function has no such links gets an empty table, which says nothing about its events.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from rabbitizer import InstrId

from .. import addresses as a
from ..entries import ACTION_INPUT_BASE
from ..mips import Code, Fpr, Gpr
from . import abi
from . import attacks as atk
from .moveset import Site, Walker, known

SLOT = (int(a.MONSTER_VTABLE.ANIM_EVENTS) - abi.offset(0)) // 4
"""The entity vtable slot of the events."""
CROSSES, REACHED = "crosses", "reached"
"""A cursor test: it stepped over the frame this tick, or is at or past it."""
FRAME_DIGITS = 2
Doc = dict[str, Any]


@dataclass(frozen=True)
class Gate:
    """A cursor test in an entry's branch."""

    kind: str
    frame: float | None
    """None where the frame comes from data, or differs between the paths reaching the test."""


@dataclass(frozen=True)
class Spawn:
    """An attack spawn in an entry's branch."""

    id: int | None
    """The literal attack id, None where the code computes it."""
    frame: float | None
    """The frame of the last cursor test before the spawn in the function; None when there is
    none or its frame is not one literal."""


@dataclass(frozen=True)
class Entry:
    entry: int
    gates: tuple[Gate, ...] = ()
    attacks: tuple[Spawn, ...] = ()

    @property
    def spawn_frames(self) -> list[float]:
        """The distinct frames attacks spawn at."""
        return sorted({s.frame for s in self.attacks if s.frame is not None})


def _frame(bits: int) -> float:
    (value,) = struct.unpack("<f", struct.pack("<I", bits & 0xFFFF_FFFF))
    return round(float(value), FRAME_DIGITS)


def links(code: Code, fn: range) -> dict[int, int]:
    """Entry -> the pc of its `addiu a0, zero, INPUT` in `fn`, where `beq v1, a0` follows."""
    out: dict[int, int] = {}
    pcs = range(fn.start, fn.stop, 4)
    for pc, after in zip(pcs, pcs[1:], strict=False):
        li, test = code.at(pc), code.at(after)
        if (
            li.uniqueId == InstrId.cpu_addiu
            and li.rt == Gpr.a0
            and li.rs == Gpr.zero
            and test.uniqueId in (InstrId.cpu_beq, InstrId.cpu_beql)
            and {test.rs, test.rt} == {Gpr.v1, Gpr.a0}
            and (value := li.getProcessedImmediate()) >= ACTION_INPUT_BASE
        ):
            out[value - ACTION_INPUT_BASE] = pc
    return out


def branches(code: Code, fn: int, species: int) -> dict[int, int]:
    """Entry -> branch start, in the function holding the table: `fn`'s own, else the callee of
    a species switch there that a walk reaches for `species`."""
    body = code.function(fn)
    found = links(code, body)
    if found:
        return found
    callees = {c.target for c in code.calls if c.site in body and c.link and c.target in code.text}
    walker = Walker(code, {t: f"{t:x}" for t in callees if t}, depth=0, guards=False)
    sites = walker.run(fn, facts=known({int(a.ENTITY.SPECIES): species}))
    out: dict[int, int] = {}
    for site in sites:
        out |= links(code, code.function(int(site.kind, 16)))
    return dict(sorted(out.items()))


def table(code: Code, fn: int | None, species: int) -> dict[int, Entry]:
    """Every entry with a branch in the species' event function `fn`; empty for none."""
    if fn is None:
        return {}
    spawner = atk.spawner(code)
    watch = {a.CLIP_CURSOR_CROSSES: CROSSES, a.CLIP_CURSOR_REACHED: REACHED}
    if spawner is not None:
        watch[spawner.fn] = "attack"
    walker = Walker(code, watch, depth=0, guards=False, args=(Fpr.fa0, Gpr.a2))
    facts = known({int(a.ENTITY.SPECIES): species})
    out = {}
    for entry, start in branches(code, fn, species).items():
        sites = walker.run(start, seed={Gpr.v1: entry + ACTION_INPUT_BASE}, facts=facts)
        out[entry] = _entry(entry, sites)
    return out


def _entry(entry: int, sites: list[Site]) -> Entry:
    """The gates and spawns of the sites one entry's walk reached, in address order."""
    by_site: dict[int, tuple[str, set[int | None]]] = {}
    for s in sites:
        value = s.args[1] if s.kind == "attack" else s.args[0]
        by_site.setdefault(s.site, (s.kind, set()))[1].add(value)
    gates: list[Gate] = []
    spawns: list[Spawn] = []
    last: float | None = None
    for site in sorted(by_site):
        kind, values = by_site[site]
        if kind == "attack":
            named = sorted(v for v in values if v is not None)
            ids: list[int | None] = [*named] if named else [None]
            spawns += [Spawn(i, last) for i in ids if Spawn(i, last) not in spawns]
            continue
        frames = sorted({_frame(v) for v in values if v is not None})
        last = frames[0] if len(frames) == 1 else None
        gates.append(Gate(kind, last))
    return Entry(entry, tuple(gates), tuple(spawns))


def document(entries: Mapping[int, Entry], fn: int | None) -> Doc:
    """The table as the species intel document keeps it."""
    return {
        "present": bool(entries),
        "function": None if fn is None else f"0x{fn:08X}",
        "entries": [
            {
                "entry": e.entry,
                "gates": [[g.kind, g.frame] for g in e.gates],
                "attacks": [[s.id, s.frame] for s in e.attacks],
            }
            for e in entries.values()
        ],
    }


def read(doc: Doc | None) -> dict[int, Entry]:
    """`document`'s table; empty for a document without one."""
    out = {}
    for d in (doc or {}).get("entries", []):
        gates = tuple(Gate(str(k), None if f is None else float(f)) for k, f in d["gates"])
        spawns = tuple(
            Spawn(None if i is None else int(i), None if f is None else float(f))
            for i, f in d["attacks"]
        )
        out[int(d["entry"])] = Entry(int(d["entry"]), gates, spawns)
    return out
