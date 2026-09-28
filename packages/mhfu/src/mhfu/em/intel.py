"""Everything the overlay and the log say about a species' behaviour pairs, joined into one
document per species (schema `mhfu.species_intel/1`, which the monster editor reads).

Each pair field says where it comes from: `static` (read from the overlay's code, the same in
every run), `measured` (a census sample) or `absent`. A census is attached only to the species
it was taken from; without one every pair's `measured` is null, which means unknown, not zero.

Effects and attacks join a pair through its handler: a spawn site is credited to the pair when
its function is the handler or one it reaches by calls within EFFECT_CALL_DEPTH. Sites no
handler reaches (em75's species-byte switch) are listed once, as `unattributed_effects`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from .. import addresses as a
from .. import hitbox as hb
from .. import hitzone as hz
from ..memory import Space
from ..mips import Code
from ..overlay import Overlay
from . import attacks as atk
from . import census as cs
from . import effects as fx

SCHEMA = "mhfu.species_intel/1"
EFFECT_CALL_DEPTH = 2
"""Calls followed from a handler when crediting spawn sites; most handlers are thin."""
STATIC, MEASURED, ABSENT = "static", "measured", "absent"
DIGITS = 4
"""Decimals kept of a float read from the ISO."""

Doc = dict[str, Any]


def hex32(va: int | None) -> str | None:
    return None if va is None else f"0x{va:08X}"


# --- crediting spawn sites to handlers ---


def call_graph(code: Code) -> dict[int, set[int]]:
    """Function -> the overlay functions it calls or tail-calls."""
    out: dict[int, set[int]] = defaultdict(set)
    for c in code.calls:
        if c.target is not None and c.target in code.text:
            out[code.function(c.site).start].add(c.target)
    return out


def reachable(graph: dict[int, set[int]], fn: int, depth: int) -> set[int]:
    seen, frontier = {fn}, [fn]
    for _ in range(depth):
        frontier = [c for f in frontier for c in graph.get(f, ()) if c not in seen]
        seen.update(frontier)
    return seen


def effect(s: fx.Spawn, fn: bool = True) -> Doc:
    out: Doc = {"id": s.id, "bone": s.bone, "frame": s.frame, "site": hex32(s.site), "via": s.via}
    if fn:
        out["fn"] = hex32(s.fn)
    return out


@dataclass
class Credit:
    """An overlay's effect and attack spawn sites, credited to handlers as they are asked for."""

    code: Code
    effects: list[fx.Spawn]
    attacks: list[atk.Site]
    credited: set[int] = field(default_factory=set)
    """Effect sites some handler reaches."""
    attacks_credited: set[int] = field(default_factory=set)

    @classmethod
    def of(cls, code: Code) -> Credit:
        sp = atk.spawner(code)
        return cls(code, fx.spawns(code), [] if sp is None else sp.sites)

    @cached_property
    def graph(self) -> dict[int, set[int]]:
        return call_graph(self.code)

    def _reach(self, handler: int) -> set[int]:
        return reachable(self.graph, handler, EFFECT_CALL_DEPTH)

    def effects_of(self, handler: int) -> list[Doc]:
        """The literal effects the handler can spawn, by id, bone and frame."""
        fns = self._reach(handler)
        out = []
        for s in self.effects:
            if s.fn in fns and s.id is not None:
                self.credited.add(s.site)
                out.append(effect(s))
        return sorted(out, key=lambda e: (e["id"], _key(e["bone"]), _key(e["frame"])))

    def attacks_of(self, handler: int) -> tuple[list[int], int, int]:
        """(literal attack ids, sites, sites with a computed id) the handler can reach."""
        fns = self._reach(handler)
        ids, sites, computed = set(), 0, 0
        for s in self.attacks:
            if s.fn in fns:
                sites += 1
                self.attacks_credited.add(s.site)
                if s.id is None:
                    computed += 1
                else:
                    ids.add(s.id)
        return sorted(ids), sites, computed

    def unattributed(self) -> list[Doc]:
        """Literal effect sites no handler asked for so far, by function."""
        by_fn: dict[int, list[fx.Spawn]] = defaultdict(list)
        for s in self.effects:
            if s.id is not None and s.site not in self.credited:
                by_fn[s.fn].append(s)
        return [
            {"fn": hex32(fn), "sites": [effect(s, fn=False) for s in sites]}
            for fn, sites in sorted(by_fn.items())
        ]


def _key(v: int | None) -> int:
    return -1 if v is None else v


# --- where he can be hit ---


def _round(v: tuple[float, ...]) -> list[float]:
    return [round(c, DIGITS) for c in v]


def _volume(v: hz.HitVolume, hurtbox: bool) -> Doc:
    """A hurtbox record, or an attack volume's (whose row and part only matter to a round
    trip, and whose bone may be a marker)."""
    out: Doc = {"bone": v.bone, "shape": "capsule" if v.capsule else "sphere"}
    if hurtbox:
        out |= {"hitzone_row": v.row, "part": v.part & hz.PART_MASK}
    out |= {"radius": round(v.radius, DIGITS), "a": _round(v.offset)}
    if v.capsule:
        out["b"] = _round(v.far)
    if v.flags:
        out["flags"] = f"0x{v.flags:X}"
    if not hurtbox and (v.row or v.part):
        out["row_part"] = [v.row, v.part]
    return out


def parts(game_task: Overlay, ovl: Overlay, species: int, task_file: str) -> Doc:
    """The overlay's hit-volume sets and the species' hitzone grid; static, and never yet
    changed in a game and seen to work."""
    mem = Space([game_task, ovl])
    sets = hz.find_sets(ovl)
    own = hz.own_set(mem, ovl, species)
    if own is not None and own.va not in {s.va for s in sets}:
        sets.append(own)  # the row names it; the shape search can miss it
    owners = hz.species_sets(mem, ovl)
    out: Doc = {
        "present": True,
        "source": "mhfu.hitzone",
        "note": "static: bytes in the ISO. NOT validated in game — no cold boot has ever "
        "changed either table and confirmed the effect.",
        "sets": [
            {
                "va": hex32(s.va),
                "kind": s.kind,
                "count": len(s.volumes),
                "bones": s.bones,
                "parts": s.parts,
                "rows": s.rows,
                "species": [n for va, n in sorted(owners.items()) if va == s.va],
                "spheres": [_volume(v, hurtbox=True) for v in s.volumes],
            }
            for s in sorted(sets, key=lambda s: s.va)
            if s.kind != hz.UNKNOWN
        ],
        "unclassified_runs": sum(s.kind == hz.UNKNOWN for s in sets),
        "active_set": None if own is None else hex32(own.va),
        "active_capacity": None if own is None else len(own.volumes),
        "sphere_table_field": hex32(
            a.SPECIES_TABLE + species * a.SPECIES.stride + a.SPECIES.HURTBOX_SET
        ),
    }
    grid = hz.species_hitzones(mem, species)
    if grid is None:
        out["grid"] = {
            "present": False,
            "reason": f"species {species} has no hitzone state table at "
            f"row+0x{a.SPECIES.HITZONE_STATES:X}",
        }
        return out
    out["grid"] = {
        "present": True,
        "file": task_file,
        "species_row": hex32(grid.row),
        "state_table": hex32(grid.states_va),
        "columns": list(hz.COLUMNS),
        "column_provenance": dict(hz.COLUMN_PROVENANCE),
        "element_bits": {k: f"0x{v:X}" for k, v in hz.ELEMENT_BITS.items()},
        "states": [{"va": hex32(g.base), "rows": [list(r) for r in g.rows]} for g in grid.states],
        "note": "the grid is SHARED: it lives in species data, so a port riding this host "
        "inherits it and editing it changes the native monster too.",
    }
    return out


# --- where he hits you ---

RECORD_FIELDS = {
    "power": a.ATTACK_RECORD.POWER,
    "volume": a.ATTACK_RECORD.VOLUME_SET,
    "element": a.ATTACK_RECORD.ELEMENT,
    "u16_0c": a.ATTACK_RECORD.HALF_0C,
    "unknown_01": a.ATTACK_RECORD.UNKNOWN_01,
    "kind": a.ATTACK_RECORD.KIND,
    "flags": a.ATTACK_RECORD.FAMILY,
    "angle": a.ATTACK_RECORD.ANGLE,
    "tag": a.ATTACK_RECORD.TAG,
    "value_14": a.ATTACK_RECORD.VALUE_14,
}
"""The attack record's JSON keys and the fields they hold; a field's doc says how it is known."""


def _record(r: hb.Attack) -> Doc:
    return {
        "id": r.index,
        "va": hex32(r.base),
        "power": r.power,
        "element": f"0x{r.element:02X}",
        "volume": r.volume_set,
        "kind": r.kind,
        "flags": f"0x{r.family:02X}",
        "angle": r.angle,
        "tag": f"0x{r.tag:02X}",
        "u16_0c": r.half_0c,
        "value_14": r.value_14,
        "raw": r.raw.hex(),
    }


def _table(t: hb.AttackTable, primary: bool) -> Doc:
    return {
        "handle": hex32(t.handle),
        "records": hex32(t.records),
        "n_records": len(t.attacks),
        "volume_table": hex32(t.volume_table),
        "n_sets": len(t.volumes),
        "primary": primary,
        "rigged": t.rigged,
        "sets": [
            {
                "index": i,
                "va": hex32(v.va),
                "count": len(v.volumes),
                "bones": [b for b in v.bones if b not in hz.MARKER_BONES],
                "rigged": v.rigged,
                "spheres": [_volume(x, hurtbox=False) for x in v.volumes],
            }
            for i, v in enumerate(t.volumes)
        ],
        "attacks": [_record(r) for r in t.attacks],
    }


def attacks(ovl: Overlay, code: Code, species: int, credit: Credit) -> Doc:
    """The species' attack tables and the spawner its handlers call. The em75 join was walked
    live; the others are the spawner's ids fitting the biggest table (`attacks.fit`)."""
    tables = hb.tables(ovl)
    primary = hb.primary_table(tables)
    sp = atk.spawner(code)
    join = atk.fit(sp, primary)
    if not tables:
        return {
            "present": False,
            "reason": f"this overlay never calls the table setter "
            f"0x{a.ATTACK_TABLE_SETTER:08X} — no attack table (em1/em33)",
            "spawner": None if sp is None else hex32(sp.fn),
        }
    return {
        "present": True,
        "source": "mhfu.hitbox + mhfu.em.attacks",
        "note": "static: bytes in the ISO. The in-place set overwrite is proven by RAM poke on "
        "a native Tigrex; the generated-Lua runtime path has not been cold-booted yet.",
        "setter": hex32(a.ATTACK_TABLE_SETTER),
        "spawner": None if sp is None else hex32(sp.fn),
        "spawner_sites": 0 if sp is None else len(sp.sites),
        "spawner_literal_sites": 0 if sp is None else sp.literal,
        "join": join,
        "join_provenance": (
            "measured — walked live from the handler to the HP write"
            if join == "measured"
            else "inferred — the overlay's own game_task node constructor, its literal ids "
            "against the biggest table; see mhfu.em.attacks.fit"
        ),
        "extra_spawners": [
            {"fn": hex32(x.fn), "sites": len(x.sites), "ids": x.ids} for x in atk.extras(code, sp)
        ],
        "id_offsets": {
            str(k): v for k, v in sorted(hb.ID_OFFSETS.get(species, {species: 0}).items())
        },
        "field_provenance": {k: f.doc for k, f in RECORD_FIELDS.items()},
        "attack_sites": len(credit.attacks),
        "attack_sites_uncredited": sum(
            s.site not in credit.attacks_credited for s in credit.attacks
        ),
        "tables": [_table(t, t is primary) for t in tables],
    }


# --- what the log measured ---


@dataclass(frozen=True)
class Measured:
    """A census and the log it came from."""

    census: cs.Census
    log: Path
    since: int = 0


def census(measured: Measured | None, reason: str) -> Doc:
    out: Doc = {
        "present": measured is not None,
        "source": "mhfu.em.census",
        "note": "the ONLY source that knows whether a pair is usable: 411 of 411 forced moves "
        "into never-entered pairs survived exactly one tick.",
    }
    if measured is None:
        return out | {
            "reason": reason or "no census supplied",
            "how": "deploy the observe-only probe, cold-boot into a quest with this species, "
            "then re-run with --log <framework.log>",
            "consequence": "every pair's `measured` block is null and `entered` is UNKNOWN — "
            "not zero. A consumer must not treat an absent measurement as a never-entered pair.",
        }
    c, stat = measured.census, measured.log.stat()
    return out | {
        "log": str(measured.log),
        "since": measured.since,
        "log_bytes": stat.st_size,
        "log_mtime": int(stat.st_mtime),
        "transitions": c.transitions,
        "observed_pairs": len(c.dwell),
        "attributed_by": "--census-species (the log does not record which species it watched)",
    }
