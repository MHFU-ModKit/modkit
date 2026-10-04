# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Everything the overlay and the log say about a species' behaviour pairs, joined into one
document per species (schema `mhfu.species_intel/1`), and its typed reader (`SpeciesIntel`).

Each pair field says where it comes from: `static` (read from the overlay's code, the same in
every run), `measured` (a census sample) or `absent`. A census is attached only to the species
it was taken from; without one every pair's `measured` is null, which means unknown, not zero.

Effects and attacks join a pair through its handler: a spawn site is credited to the pair when
its function is the handler or one it reaches by calls within EFFECT_CALL_DEPTH. Sites no
handler reaches (em75's species-byte switch) are listed once, as `unattributed_effects`.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from .. import addresses as a
from .. import files
from .. import hitbox as hb
from .. import hitzone as hz
from ..files import Extracted
from ..memory import Space
from ..mips import Code
from ..overlay import Overlay
from ..structs import ACTION_INPUT_BASE
from . import attacks as atk
from . import census as cs
from . import chain as ch
from . import effects as fx
from . import moveset as mv
from . import phases

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
    """The overlay's hit-volume sets and the species' hitzone grid, static; a grid write changes
    the damage in game, a hit-volume edit is unverified in game."""
    mem = Space([game_task, ovl])
    sets = hz.find_sets(ovl)
    own = hz.own_set(mem, ovl, species)
    if own is not None and own.va not in {s.va for s in sets}:
        sets.append(own)  # the row names it; the shape search can miss it
    owners = hz.species_sets(mem, ovl)
    out: Doc = {
        "present": True,
        "source": "mhfu.hitzone",
        "note": "static: bytes in the ISO. A grid write changes the damage in game; a "
        "hit-volume edit is unverified in game.",
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
        "note": "static: bytes in the ISO. Overwriting a set in place works on a native "
        "Tigrex; writing it from the generated Lua module is unverified in game.",
        "setter": hex32(a.ATTACK_TABLE_SETTER),
        "spawner": None if sp is None else hex32(sp.fn),
        "spawner_sites": 0 if sp is None else len(sp.sites),
        "spawner_literal_sites": 0 if sp is None else sp.literal,
        "join": join,
        "join_provenance": (
            "measured: traced in the running game from the handler to the HP write"
            if join == "measured"
            else "inferred: the overlay's own game_task node constructor, its literal ids "
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
        "forced_skipped": c.forced,
        "observed_pairs": len(c.dwell),
        "attributed_by": "--census-species (the log does not record which species it watched)",
    }


# --- the behaviour pairs ---


def _main(m: mv.Main) -> Doc:
    out: Doc = {"main": m.main, "dispatcher": hex32(m.dispatcher)}
    if m.switch is None:
        return out | {
            "sub_states": None,
            "enumerated": False,
            "note": "no sub_state jump table — this main state's actions are not enumerable "
            "offline",
        }
    return out | {
        "sub_states": len(m.switch.targets),
        "enumerated": True,
        "first_sub": m.switch.first,
        "note": "",
    }


def handled(ms: mv.Moveset, pair: mv.Pair, h: int, credit: Credit) -> Doc:
    """What the code says about a pair whose case calls handler `h`."""
    anim = ms.animations(pair)
    g = phases.gates(ms.code, h)
    ends = g.ends_on
    seeds = list(phases.budget_seeds(ms.code, h)) if ends == "budget" else []
    ids, sites, computed = credit.attacks_of(h)
    return {
        "main": pair.main,
        "sub": pair.sub,
        "handler": hex32(h),
        "a1": list(anim.ids),
        "a1_computed": anim.computed,
        "ends_on": ends,
        "event_frames": list(g.reached),
        "windows": len(g.crosses),
        "window_frames": list(g.crosses),
        "clip_done_reads": g.clip_done,
        "budget_reads": g.budget,
        "budget": {
            "gated": ends == "budget",
            "phase0_seeds": seeds,
            "post_hook_owns": (not seeds) if ends == "budget" else None,
        },
        "effects": credit.effects_of(h),
        "attack_ids": ids,
        "attack_sites": sites,
        "attack_sites_computed": computed,
    }


def edge(e: ch.Edge) -> Doc:
    return {
        "site": hex32(e.site),
        "kind": e.kind,
        "main": e.main,
        "id": e.id,
        "mode": e.mode,
        "via": [hex32(v) for v in e.via],
        "guards": list(e.guards),
        "to": [list(p) for p in e.to],
        "computed": e.computed,
        "alts": [list(x) for x in e.alts],
    }


def hubs(chain: ch.Chain, least: int = 8) -> list[list[int]]:
    """The pairs at least `least` handlers can hand off to, most first: where the brain thinks
    again. Counted per handler, since one handler can serve many subs."""
    targets: dict[int, set[ch.Pair]] = defaultdict(set)
    for link in chain.pairs.values():
        for e in link.next:
            targets[link.handler].update(e.to)
    count: Counter[ch.Pair] = Counter()
    for found in targets.values():
        count.update(found)
    return [list(k) for k, n in sorted(count.items(), key=lambda kv: (-kv[1], kv[0])) if n >= least]


def _chain(chain: ch.Chain) -> Doc:
    return {
        "source": "mhfu.em.chain",
        "enter_action": hex32(chain.enter.function),
        "species_byte": chain.species,
        "note": "a handler ends an action by calling enter-action (vt+0x88) with a literal "
        "(main, id); the per-main translator turns the id into the pair AND provisions the "
        "handler (the charge's run budget +0x76C is set there, not by act_set). `next` is "
        "that call, read statically, with the guards on the path; `prev` is its inverse. "
        "Pairs with no `next` never end themselves.",
        "hubs": hubs(chain),
        "brain": {hex32(fn): [edge(e) for e in es] for fn, es in chain.brain.items()},
        "translators": {
            str(m): {str(i): None if to is None else [list(p) for p in to] for i, to in ids.items()}
            for m, ids in chain.enter.table().items()
        },
    }


# --- the document ---


@dataclass
class Game:
    """What every species' document reads besides its own overlay."""

    game: Extracted

    @cached_property
    def task(self) -> Overlay:
        return self.game.overlay(files.GAME_TASK)

    @cached_property
    def bias(self) -> dict[int, int]:
        return fx.bias(Code(self.task, self.task.text))


def build(game: Game, species: int, measured: Measured | None = None, reason: str = "") -> Doc:
    """The species' document; `measured` must come from a log of this species."""
    ovl = game.game.em(species)
    ms = mv.Moveset(ovl)
    code = ms.code
    credit = Credit.of(code)
    chain = ch.Chain(ms)
    prev = chain.predecessors()

    static = {
        k: handled(ms, p, p.handler, credit)
        for k, p in sorted(ms.pairs.items())
        if p.handler is not None
    }
    keys = set(ms.pairs) | (set(measured.census.dwell) if measured else set())
    pairs = []
    for key in sorted(keys):
        main, sub = key
        rec: Doc
        prov: dict[str, str] = {}
        if key in static:
            rec = dict(static[key])
            prov = dict.fromkeys(
                ("handler", "a1", "ends_on", "event_frames", "window_frames", "effects", "budget"),
                STATIC,
            )
            rec["next"] = [edge(e) for e in chain.pairs[key].next]
            rec["prev"] = [list(p) for p in prev.get(key, [])]
            prov |= {"next": STATIC, "prev": STATIC}
        elif key in ms.pairs:
            rec = {"main": main, "sub": sub, "handler": None}
            rec["note"] = (
                "the dispatcher's case for this pair runs inline and calls no handler — "
                "nothing offline can say what it does"
            )
            prov["handler"] = STATIC
        else:
            rec = {"main": main, "sub": sub, "handler": None}
            rec["note"] = (
                "not in the overlay's (main,sub) jump tables — the engine reached it by a "
                "path this extractor cannot see"
            )
        fields = ("entered", "dwell_ticks", "move_per_tick")
        if measured is None:
            rec["measured"] = None
            prov |= dict.fromkeys(fields, ABSENT)
        else:
            rec["measured"] = cs.measured(measured.census, key)
            prov |= dict.fromkeys(fields, MEASURED)
            if rec["measured"]["a1"]:
                prov["measured_a1"] = MEASURED
        rec["provenance"] = prov
        pairs.append(rec)

    return {
        "schema": SCHEMA,
        "host_species": species,
        "generated_by": "mhfu intel",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "overlay": {
            "name": ovl.name,
            "file": game.game.path(files.em_overlay(species)).name,
            "sha1": hashlib.sha1(ovl.file).hexdigest(),
            "load": hex32(ovl.load),
            "text": [hex32(ovl.text.start), hex32(ovl.text.stop)],
            "region": f"MHFU {a.REGION.upper()} ({a.GAME_ID})",
            "effect_id_bias": game.bias.get(species, 0),
        },
        "action_tick": hex32(ms.tick_entry),
        "main_states": [_main(m) for _, m in sorted(ms.mains.items())],
        "static": {
            "present": True,
            "source": "overlay disassembly",
            "tools": ["mhfu.em.moveset", "mhfu.em.phases", "mhfu.em.effects"],
            "note": "a property of the ISO: the same for every run and every player. Never a "
            "measurement.",
            "effect_call_depth": EFFECT_CALL_DEPTH,
            "effect_sites": len(credit.effects),
            "effect_sites_computed": sum(s.id is None for s in credit.effects),
        },
        "census": census(measured, reason),
        "pairs": pairs,
        "chain": _chain(chain),
        "unattributed_effects": credit.unattributed(),
        "parts": parts(game.task, ovl, species, game.game.path(files.GAME_TASK).name),
        "attacks": attacks(ovl, code, species, credit),
    }


def summarise(doc: Doc) -> str:
    pairs = doc["pairs"]
    handled = [p for p in pairs if p.get("handler")]
    budget = [p for p in handled if p["budget"]["gated"]]
    owned = [p for p in budget if p["budget"]["post_hook_owns"]]
    ends = Counter(p["ends_on"] for p in handled)
    chained = [p for p in handled if p["next"]]
    resolved = [p for p in chained if any(e["to"] for e in p["next"])]
    unattributed = doc["unattributed_effects"]
    hub_list = " ".join(f"({m},{s})" for m, s in doc["chain"]["hubs"]) or "-"
    out = [
        f"{doc['overlay']['name']}  species {doc['host_species']}  {len(pairs)} pair(s), "
        f"{len(handled)} with a handler",
        "  ends on: " + ", ".join(f"{k}={n}" for k, n in sorted(ends.items())),
        f"  hands off: {len(chained)} pair(s), {len(resolved)} to a resolved pair; hubs {hub_list}",
        f"  {sum(bool(p['effects']) for p in handled)} pair(s) carry effects "
        f"({sum(len(u['sites']) for u in unattributed)} site(s) unattributed in "
        f"{len(unattributed)} function(s))",
        f"  {len(budget)} budget-gated, {len(owned)} of them ownable by a slot-32 post-hook",
    ]
    pt = doc["parts"]
    hurt = [s for s in pt["sets"] if s["kind"] == hz.HURTBOX]
    grid = pt["grid"]
    states = f"{len(grid['states'])} state(s)" if grid["present"] else f"ABSENT ({grid['reason']})"
    out.append(
        f"  parts: {len(hurt)} hurtbox set(s), {sum(s['count'] for s in hurt)} sphere(s); "
        f"grid {states}"
    )
    at = doc["attacks"]
    if at["present"]:
        prim = next((t for t in at["tables"] if t["primary"]), None)
        out.append(
            f"  attacks: {len(at['tables'])} table(s); moveset "
            f"{'-' if prim is None else prim['n_records']} records / "
            f"{'-' if prim is None else prim['n_sets']} set(s); spawner {at['spawner'] or '-'} "
            f"({at['join']}), {sum(bool(p.get('attack_ids')) for p in pairs)} pair(s) name "
            "an attack"
        )
    else:
        out.append(f"  attacks: ABSENT ({at['reason']})")
    c = doc["census"]
    out.append(
        f"  census: {c['transitions']} transitions ({c['forced_skipped']} forced, skipped), "
        f"{c['observed_pairs']} pair(s) observed"
        if c["present"]
        else f"  census: ABSENT ({c['reason']})"
    )
    return "\n".join(out)


# --- reading a document ---

MIN_DWELL_TICKS = 3.0
"""2 Hz ticks a pair must hold, when the engine picks it, before it is worth scripting."""

BIND_OK = "OK"
BIND_NO_HANDLER = "NO_HANDLER"
BIND_NEVER_ENTERED = "NEVER_ENTERED"
BIND_SHORT_DWELL = "SHORT_DWELL"
BIND_UNMEASURED = "UNMEASURED"
BIND_UNKNOWN_PAIR = "UNKNOWN_PAIR"

Pair = ch.Pair


def _va(v: Any) -> int | None:
    """An address or hex field as written ("0x1C", or an int); None for null or ""."""
    if v is None or v == "":
        return None
    return int(v, 0) if isinstance(v, str) else int(v)


def _opt_int(v: Any) -> int | None:
    return None if v is None else int(v)


def _frames(v: Iterable[Any]) -> tuple[float | None, ...]:
    return tuple(None if f is None else float(f) for f in v)


@dataclass(frozen=True)
class EffectRecipe:
    """One literal effect spawn: the host's `bone`, and `frame` only for the framed primitive."""

    id: int
    bone: int | None = None
    frame: int | None = None
    site: int | None = None
    via: str = ""
    fn: int | None = None

    def __str__(self) -> str:
        s = f"{self.id}@b{'?' if self.bone is None else self.bone}"
        return s if self.frame is None else f"{s}@f{self.frame}"

    @classmethod
    def read(cls, d: Doc) -> EffectRecipe:
        return cls(
            int(d["id"]),
            _opt_int(d.get("bone")),
            _opt_int(d.get("frame")),
            _va(d.get("site")),
            str(d.get("via", "")),
            _va(d.get("fn")),
        )


@dataclass(frozen=True)
class Budget:
    """The ACTION_BUDGET frame countdown a handler may end on."""

    gated: bool = False
    phase0_seeds: tuple[int, ...] = ()
    """Literals the handler's phase-0 block re-seeds it with."""
    post_hook_owns: bool | None = None
    """True when a slot-32 post-hook can own it (phase 0 only consumes it)."""

    @classmethod
    def read(cls, d: Doc | None) -> Budget:
        d = d or {}
        return cls(
            bool(d.get("gated")),
            tuple(int(x) for x in d.get("phase0_seeds", [])),
            d.get("post_hook_owns"),
        )


_CELLS = {
    0x280: "reaction pending",
    a.ENTITY.ACTION_BUDGET: "frame budget",
    0x637: "run budget armed",
    a.ENTITY.SECTION: "section",
    a.ENTITY.PHASE + 2: "phase3",
    a.ENTITY.SPECIES: "species",
    0xBE: "clip busy",
    a.ENTITY.CLIP_FLAGS: "clip playing",
}
"""Entity cells a guard tests, named where pinned; the rest stay `+0xNNN`."""
_FLAG_OFF = {
    "reaction pending": "no reaction pending",
    "run budget armed": "run budget unarmed",
    "clip busy": "clip done",
    "clip playing": "clip ended",
}
_GUARD = re.compile(r"^(!?)\+0x([0-9A-Fa-f]+)(==|!=|<=|>=|<|>)(-?\d+)$")


def describe_guard(g: str) -> str:
    """A guard as a person reads it: `+0x280!=0` is `reaction pending`; unpinned cells come
    back unchanged. `budget spent` is the run budget, not the frame budget."""
    if g in ("budget spent", "!budget spent"):
        return "run budget spent" if g[0] != "!" else "run budget left"
    m = _GUARD.match(g)
    if not m:
        return g
    off, op, val = int(m[2], 16), m[3], int(m[4])
    if off == a.ENTITY.ANIM_INPUT and op in ("==", "!=") and 0 <= val - ACTION_INPUT_BASE < 200:
        return f"{'not ' if op == '!=' else ''}playing a1 {val - ACTION_INPUT_BASE}"
    if off == a.ENTITY.ACTION_BUDGET and val == 0 and op in ("<=", ">"):
        return "frame budget spent" if op == "<=" else "frame budget left"
    name = _CELLS.get(off)
    if name is None:
        return g
    if name in _FLAG_OFF:
        if (op, val) in (("==", 0), ("!=", 1)):
            return _FLAG_OFF[name]
        if (op, val) in (("!=", 0), ("==", 1)):
            return name
    return f"{name}{op}{val}"


@dataclass(frozen=True)
class Handoff:
    """One way a handler ends its action: the pair(s) it enters and the guards on the path.

    `to` holds two pairs where the translator remaps the id under a flag; `mode` is act_set's
    fourth argument; `alts` are the other guard sets that reach the same call."""

    to: tuple[Pair, ...]
    guards: tuple[str, ...] = ()
    mode: int | None = None
    site: int | None = None
    via: tuple[int, ...] = ()
    alts: tuple[tuple[str, ...], ...] = ()

    @property
    def reason(self) -> str:
        """The guards other than the phase, pinned cells named."""
        return " & ".join(describe_guard(g) for g in self.guards if not g.startswith("phase"))

    @property
    def raw_reason(self) -> str:
        return " & ".join(g for g in self.guards if not g.startswith("phase"))

    def describe(self) -> str:
        return " & ".join(describe_guard(g) for g in self.guards)

    @property
    def phase(self) -> int | None:
        for g in self.guards:
            if g.startswith("phase=="):
                try:
                    return int(g[7:])
                except ValueError:
                    return None
        return None

    def __str__(self) -> str:
        target = "/".join(f"({m},{s})" for m, s in self.to) or "(computed)"
        return target + (f"  [{self.describe()}]" if self.guards else "")

    @classmethod
    def read(cls, d: Doc) -> Handoff:
        return cls(
            tuple((int(m), int(s)) for m, s in d.get("to", [])),
            tuple(str(g) for g in d.get("guards", [])),
            _opt_int(d.get("mode")),
            _va(d.get("site")),
            tuple(v for v in (_va(x) for x in d.get("via", [])) if v is not None),
            tuple(tuple(str(g) for g in alt) for alt in d.get("alts", [])),
        )


@dataclass(frozen=True)
class PairIntel:
    """One (main, sub) pair. `entered` None means unmeasured, 0 measured never entered: only
    the second may refuse a bind. `a1` is the census's when it saw any, else the static one."""

    main: int
    sub: int
    entered: int | None = None
    dwell_ticks: float = 0.0
    a1: tuple[int, ...] = ()
    note: str = ""
    """What the census said, else the static note."""
    handler: int | None = None
    """None: the dispatcher's case runs inline."""
    a1_static: tuple[int, ...] = ()
    a1_computed: bool = False
    ends_on: str = ""
    event_frames: tuple[float | None, ...] = ()
    """Cursor frames the handler tests; None where the literal came from data."""
    window_frames: tuple[float | None, ...] = ()
    """The windowed test's literals, one edge per site; negative ones are kept."""
    windows: int = 0
    budget: Budget = Budget()
    effects: tuple[EffectRecipe, ...] = ()
    attack_ids: tuple[int, ...] = ()
    """Attack ids the handler passes the spawner, before any species id offset."""
    attack_sites: int = 0
    attack_sites_computed: int = 0
    next: tuple[Handoff, ...] | None = None
    """None: the document predates the chain join; empty: the handler never ends itself."""
    prev: tuple[Pair, ...] = ()
    static_note: str = ""
    a1_measured: tuple[int, ...] = ()
    move_per_tick: float | None = None
    move_samples: int = 0
    provenance: Mapping[str, str] = field(default_factory=dict)

    @property
    def successors(self) -> list[Pair]:
        """Every pair this one hands to, first seen first."""
        out: list[Pair] = []
        for e in self.next or ():
            out += [t for t in e.to if t not in out]
        return out

    @property
    def ends_itself(self) -> bool | None:
        return None if self.next is None else bool(self.next)

    @property
    def measured(self) -> bool:
        return self.entered is not None

    @property
    def never_entered(self) -> bool:
        return self.entered == 0

    @property
    def a1_provenance(self) -> str:
        """Where `a1` came from (not `provenance["a1"]`, which is always the static list)."""
        if self.a1_measured:
            return MEASURED
        return STATIC if self.a1_static else ABSENT

    @property
    def ends_on_clip(self) -> bool:
        return self.ends_on in ("clip", "clip+cursor")

    @property
    def fixed_event_frames(self) -> list[float]:
        return [f for f in self.event_frames if f is not None]

    @property
    def fixed_window_frames(self) -> list[float]:
        return sorted({f for f in self.window_frames if f is not None})

    @property
    def tested_frames(self) -> list[float]:
        """Every clip frame the handler names, of either kind."""
        return sorted(set(self.fixed_event_frames) | set(self.fixed_window_frames))

    def __str__(self) -> str:
        bits = [f"({self.main},{self.sub})", f"0x{self.handler:08X}" if self.handler else "inline"]
        if self.a1:
            bits.append("a1=" + ",".join(map(str, self.a1)))
        if self.ends_on:
            bits.append("ends:" + self.ends_on)
        if self.tested_frames:
            bits.append("f@" + ",".join(f"{f:g}" for f in self.tested_frames))
        if self.effects:
            bits.append("fx " + " ".join(map(str, self.effects)))
        bits.append(f"entered={'?' if self.entered is None else self.entered}")
        return "  ".join(bits)

    @classmethod
    def read(cls, d: Doc) -> PairIntel:
        meas = d.get("measured") or {}
        a1_static = tuple(int(x) for x in d.get("a1", []))
        a1_meas = tuple(int(x) for x in meas.get("a1", []))
        nxt = d.get("next")
        return cls(
            main=int(d["main"]),
            sub=int(d["sub"]),
            entered=int(meas.get("entered", 0)) if meas else None,
            dwell_ticks=float(meas.get("dwell_ticks", 0.0)),
            a1=a1_meas or a1_static,
            note=str(meas.get("note", "") or d.get("note", "")),
            handler=_va(d.get("handler")),
            a1_static=a1_static,
            a1_computed=bool(d.get("a1_computed")),
            ends_on=str(d.get("ends_on", "")),
            event_frames=_frames(d.get("event_frames", [])),
            window_frames=_frames(d.get("window_frames", [])),
            windows=int(d.get("windows", 0)),
            budget=Budget.read(d.get("budget")),
            effects=tuple(EffectRecipe.read(e) for e in d.get("effects", [])),
            attack_ids=tuple(int(x) for x in d.get("attack_ids", [])),
            attack_sites=int(d.get("attack_sites", 0)),
            attack_sites_computed=int(d.get("attack_sites_computed", 0)),
            next=None if nxt is None else tuple(Handoff.read(e) for e in nxt),
            prev=tuple((int(m), int(s)) for m, s in d.get("prev", [])),
            static_note=str(d.get("note", "")),
            a1_measured=a1_meas,
            move_per_tick=meas.get("move_per_tick"),
            move_samples=int(meas.get("move_samples", 0)),
            provenance=dict(d.get("provenance", {})),
        )


@dataclass(frozen=True)
class Bind:
    """May a move be bound to a pair; `unverified` when that rests on absent evidence."""

    ok: bool
    code: str
    reason: str
    overridden: bool = False
    unverified: bool = False

    def __bool__(self) -> bool:
        return self.ok


Vec = tuple[float, float, float]


def _vec(v: Iterable[Any]) -> Vec:
    x, y, z = (float(c) for c in v)
    return (x, y, z)


@dataclass(frozen=True)
class HitSphere:
    """One volume record. `part` (the damage accumulator) and `hitzone_row` (the grid row) are
    different fields; an attack volume carries neither."""

    bone: int
    part: int
    hitzone_row: int
    radius: float
    shape: str = "sphere"
    a: Vec = (0.0, 0.0, 0.0)
    b: Vec | None = None
    flags: int = 0

    @property
    def is_capsule(self) -> bool:
        return self.shape == "capsule"

    @property
    def is_marker(self) -> bool:
        return self.bone in hz.MARKER_BONES

    @classmethod
    def read(cls, d: Doc) -> HitSphere:
        b = d.get("b")
        return cls(
            int(d["bone"]),
            int(d.get("part", 0)),
            int(d.get("hitzone_row", 0)),
            float(d.get("radius", 0.0)),
            str(d.get("shape", "sphere")),
            _vec(d.get("a", (0, 0, 0))),
            None if b is None else _vec(b),
            _va(d.get("flags")) or 0,
        )


@dataclass(frozen=True)
class HitboxSet:
    """One sentinel-delimited run of volumes in the overlay; `species` are the ids whose row
    points at it."""

    va: int
    kind: str
    spheres: tuple[HitSphere, ...] = ()
    species: tuple[int, ...] = ()

    @property
    def parts(self) -> list[int]:
        return sorted({s.part for s in self.spheres})

    @property
    def bones(self) -> list[int]:
        return sorted({s.bone for s in self.spheres})

    @classmethod
    def read(cls, d: Doc) -> HitboxSet:
        return cls(
            _va(d.get("va")) or 0,
            str(d.get("kind", "")),
            tuple(HitSphere.read(x) for x in d.get("spheres", [])),
            tuple(int(x) for x in d.get("species", [])),
        )


@dataclass(frozen=True)
class GridState:
    """One hitzone grid: rows of `hz.COLUMNS` percentages."""

    va: int
    rows: tuple[tuple[int, ...], ...]

    def value(self, row: int, column: str) -> int:
        return self.rows[row][hz.COLUMNS.index(column)]


@dataclass(frozen=True)
class PartIntel:
    """Where the host can be hit: the overlay's volume sets and the species grid. The grid is
    shared species data; `grid_note` says so."""

    present: bool = False
    sets: tuple[HitboxSet, ...] = ()
    states: tuple[GridState, ...] = ()
    columns: tuple[str, ...] = hz.COLUMNS
    column_provenance: Mapping[str, str] = field(default_factory=dict)
    species_row: int | None = None
    state_table: int | None = None
    grid_reason: str = ""
    grid_note: str = ""
    unclassified_runs: int = 0
    active_set_va: int | None = None
    """The set this species walks."""
    sphere_table_field: int | None = None
    """The u32 holding the pointer to it."""

    @property
    def has_grid(self) -> bool:
        return bool(self.states)

    @property
    def n_states(self) -> int:
        return len(self.states)

    @property
    def hurtboxes(self) -> list[HitboxSet]:
        return [s for s in self.sets if s.kind == hz.HURTBOX]

    @property
    def active(self) -> HitboxSet | None:
        """The one set a weapon resolves against for this species."""
        return next((s for s in self.sets if s.va == self.active_set_va), None)

    @property
    def capacity(self) -> int | None:
        """Records that fit in place: the active set's count."""
        st = self.active
        return None if st is None else len(st.spheres)

    def spheres(self) -> list[HitSphere]:
        """The active set's volumes; every hurtbox set's when the document names none."""
        st = self.active
        return list(st.spheres) if st is not None else self.all_spheres()

    def all_spheres(self) -> list[HitSphere]:
        return [s for st in self.hurtboxes for s in st.spheres]

    def parts(self) -> list[int]:
        return sorted({s.part for s in self.spheres()})

    def bones_of_part(self, part: int) -> list[int]:
        return sorted({s.bone for s in self.spheres() if s.part == part})

    def rows_of_part(self, part: int) -> list[int]:
        """A part's volumes may use several rows; all of them, never an average."""
        return sorted({s.hitzone_row for s in self.spheres() if s.part == part})

    def inferred_columns(self) -> list[str]:
        """Columns whose name is an inference, not read out of the game."""
        return [c for c in self.columns if self.column_provenance.get(c, "").startswith("inferred")]

    @classmethod
    def read(cls, d: Doc | None) -> PartIntel:
        if not d or not d.get("present"):
            return cls(grid_reason=(d or {}).get("reason", "no parts block"))
        g = d.get("grid") or {}
        return cls(
            present=True,
            sets=tuple(HitboxSet.read(x) for x in d.get("sets", [])),
            states=tuple(
                GridState(_va(s.get("va")) or 0, tuple(tuple(r) for r in s.get("rows", [])))
                for s in g.get("states", [])
            ),
            columns=tuple(g.get("columns") or hz.COLUMNS),
            column_provenance=dict(g.get("column_provenance") or {}),
            species_row=_va(g.get("species_row")),
            state_table=_va(g.get("state_table")),
            grid_reason="" if g.get("present") else str(g.get("reason", "")),
            grid_note=str(g.get("note", "")),
            unclassified_runs=int(d.get("unclassified_runs", 0)),
            active_set_va=_va(d.get("active_set")),
            sphere_table_field=_va(d.get("sphere_table_field")),
        )


@dataclass(frozen=True)
class AttackSet:
    """One attack volume set: `index` is what a record's volume field names; it is written in
    place, so `capacity` is its record count."""

    index: int
    va: int
    spheres: tuple[HitSphere, ...] = ()
    rigged: bool = True
    """Some record is on a real joint; else it hangs on the node's own position."""

    @property
    def capacity(self) -> int:
        return len(self.spheres)

    @property
    def bones(self) -> list[int]:
        return sorted({s.bone for s in self.spheres if s.bone not in hz.MARKER_BONES})

    def describe(self) -> str:
        return ", ".join(
            f"bone{s.bone}{'/cap' if s.is_capsule else ''} r={s.radius:g}"
            + (f" @{tuple(round(v) for v in s.a)}" if any(s.a) else "")
            for s in self.spheres
        )

    @classmethod
    def read(cls, d: Doc) -> AttackSet:
        sp = tuple(HitSphere.read(x) for x in d.get("spheres", []))
        rigged = d.get("rigged", any(s.bone not in hz.MARKER_BONES for s in sp))
        return cls(int(d.get("index", 0)), _va(d.get("va")) or 0, sp, bool(rigged))


@dataclass(frozen=True)
class AttackRecord:
    """One attack record; `power`, `element` and `volume` are the measured levers."""

    id: int
    va: int
    power: int
    element: int
    volume: int
    kind: int = 0
    flags: int = 0
    angle: int = 0
    tag: int = 0
    u16_0c: int = 0
    value_14: int = 0
    raw: bytes = b""

    @property
    def is_blank(self) -> bool:
        """Record 0 is all zero in every overlay."""
        return not any(self.raw)

    def describe(self) -> str:
        return f"power {self.power}, element 0x{self.element:02X}, set {self.volume}"

    @classmethod
    def read(cls, d: Doc) -> AttackRecord:
        return cls(
            int(d["id"]),
            _va(d.get("va")) or 0,
            int(d.get("power", 0)),
            _va(d.get("element")) or 0,
            int(d.get("volume", 0)),
            int(d.get("kind", 0)),
            _va(d.get("flags")) or 0,
            int(d.get("angle", 0)),
            _va(d.get("tag")) or 0,
            int(d.get("u16_0c", 0)),
            int(d.get("value_14", 0)),
            bytes.fromhex(d["raw"]) if d.get("raw") else b"",
        )


@dataclass(frozen=True)
class AttackTable:
    """One (records, volume sets) pair the overlay registers; `primary` is the one handler
    literals index."""

    handle_va: int
    records_va: int
    volume_table_va: int | None
    primary: bool = False
    rigged: bool = True
    sets: tuple[AttackSet, ...] = ()
    attacks: tuple[AttackRecord, ...] = ()

    def set(self, index: int) -> AttackSet | None:
        return next((s for s in self.sets if s.index == index), None)

    def attack(self, id: int) -> AttackRecord | None:
        """By record id, not list position."""
        return next((r for r in self.attacks if r.id == id), None)

    def volume_for(self, id: int) -> AttackSet | None:
        r = self.attack(id)
        return None if r is None else self.set(r.volume)

    def attacks_using(self, set_index: int) -> list[AttackRecord]:
        return [r for r in self.attacks if not r.is_blank and r.volume == set_index]

    @classmethod
    def read(cls, d: Doc) -> AttackTable:
        return cls(
            _va(d.get("handle")) or 0,
            _va(d.get("records")) or 0,
            _va(d.get("volume_table")),
            bool(d.get("primary")),
            bool(d.get("rigged", True)),
            tuple(AttackSet.read(x) for x in d.get("sets", [])),
            tuple(AttackRecord.read(x) for x in d.get("attacks", [])),
        )


@dataclass(frozen=True)
class AttackIntel:
    """Where the host hits: its attack tables and the spawner its handlers call. `join` says
    whether the spawner-to-table join was measured (em75) or inferred."""

    present: bool = False
    reason: str = ""
    spawner: int | None = None
    join: str = ""
    join_provenance: str = ""
    id_offsets: Mapping[int, int] = field(default_factory=dict)
    """Entity species -> what it adds to a handler literal to get the record id."""
    field_provenance: Mapping[str, str] = field(default_factory=dict)
    tables: tuple[AttackTable, ...] = ()
    attack_sites: int = 0
    attack_sites_uncredited: int = 0
    note: str = ""

    @property
    def primary(self) -> AttackTable | None:
        return next((t for t in self.tables if t.primary), self.tables[0] if self.tables else None)

    @property
    def sets(self) -> list[AttackSet]:
        t = self.primary
        return [] if t is None else list(t.sets)

    @property
    def attacks(self) -> list[AttackRecord]:
        t = self.primary
        return [] if t is None else [r for r in t.attacks if not r.is_blank]

    def set(self, index: int) -> AttackSet | None:
        t = self.primary
        return None if t is None else t.set(index)

    def attack(self, id: int) -> AttackRecord | None:
        t = self.primary
        return None if t is None else t.attack(id)

    def capacity(self, set_index: int) -> int | None:
        st = self.set(set_index)
        return None if st is None else st.capacity

    def id_offset(self, entity_species: int) -> int | None:
        """None for a species the overlay was not read for: never assume 0."""
        return self.id_offsets.get(entity_species)

    def records_for(
        self, literal_ids: Iterable[int], entity_species: int | None = None
    ) -> list[AttackRecord]:
        """The records handler literals reach for an entity of `entity_species` (default: the
        overlay's own, offset 0)."""
        off = 0 if entity_species is None else self.id_offset(entity_species)
        if off is None:
            return []
        found = (self.attack(i + off) for i in literal_ids)
        return [r for r in found if r is not None and not r.is_blank]

    def sets_for(self, literal_ids: Iterable[int], entity_species: int | None = None) -> list[int]:
        return sorted({r.volume for r in self.records_for(literal_ids, entity_species)})

    def attacks_using(self, set_index: int) -> list[AttackRecord]:
        t = self.primary
        return [] if t is None else t.attacks_using(set_index)

    @classmethod
    def read(cls, d: Doc | None) -> AttackIntel:
        if not d or not d.get("present"):
            d = d or {}
            return cls(
                reason=str(d.get("reason", "no attacks block")), spawner=_va(d.get("spawner"))
            )
        return cls(
            present=True,
            spawner=_va(d.get("spawner")),
            join=str(d.get("join", "")),
            join_provenance=str(d.get("join_provenance", "")),
            id_offsets={int(k): int(v) for k, v in (d.get("id_offsets") or {}).items()},
            field_provenance=dict(d.get("field_provenance") or {}),
            tables=tuple(AttackTable.read(t) for t in d.get("tables", [])),
            attack_sites=int(d.get("attack_sites", 0)),
            attack_sites_uncredited=int(d.get("attack_sites_uncredited", 0)),
            note=str(d.get("note", "")),
        )


class SpeciesIntel:
    """A species document, typed: `pair(main, sub)` and the joins over pairs, parts and
    attacks. `doc` is the document itself (`summarise(si.doc)`)."""

    def __init__(self, doc: Doc, source: str = "") -> None:
        self.doc = doc
        self.source = source
        self.host_species = int(doc.get("host_species", -1))
        self._pairs = {(p.main, p.sub): p for p in map(PairIntel.read, doc.get("pairs", []))}
        census = doc.get("census") or {}
        self.has_census = bool(census.get("present"))
        self.census_reason = str(census.get("reason", ""))
        self.census_transitions = int(census.get("transitions", 0))
        self.has_static = bool((doc.get("static") or {}).get("present"))
        self.main_states: list[Doc] = list(doc.get("main_states") or [])
        self.enumerated_mains = {int(m["main"]) for m in self.main_states if m.get("enumerated")}
        self.unattributed_effects: list[Doc] = list(doc.get("unattributed_effects") or [])
        self.overlay: Doc = dict(doc.get("overlay") or {})
        self.chain: Doc = dict(doc.get("chain") or {})
        self.parts = PartIntel.read(doc.get("parts"))
        self.attacks = AttackIntel.read(doc.get("attacks"))

    @classmethod
    def load(cls, path: str | Path) -> SpeciesIntel:
        p = Path(path)
        return cls(json.loads(p.read_text(encoding="utf-8")), str(p))

    def pair(self, main: int, sub: int) -> PairIntel | None:
        return self._pairs.get((main, sub))

    def __len__(self) -> int:
        return len(self._pairs)

    def __iter__(self) -> Iterator[PairIntel]:
        return iter(p for _, p in sorted(self._pairs.items()))

    def pairs_with_effects(self) -> list[PairIntel]:
        return [p for p in self if p.effects]

    def budget_gated(self) -> list[PairIntel]:
        return [p for p in self if p.budget.gated]

    def framed_effects(self) -> list[EffectRecipe]:
        """Every framed spawn, by frame: all unattributed, so the species' vocabulary rather
        than any one pair's."""
        out = [
            EffectRecipe.read(s)
            for u in self.unattributed_effects
            for s in u.get("sites", [])
            if s.get("frame") is not None
        ]
        return sorted(out, key=lambda e: (e.frame or 0, e.id))

    def bindable(self, main: int, sub: int, override: bool = False) -> Bind:
        """Refuses only a pair the census measured as never entered (forced, it survives one
        tick) and, from static intel, a sub the main's dispatcher does not have."""
        p = self.pair(main, sub)
        if p is None:
            if self.has_static and main in self.enumerated_mains:
                n = next((m.get("sub_states") for m in self.main_states if m["main"] == main), None)
                return Bind(
                    False,
                    BIND_NO_HANDLER,
                    f"main {main} dispatches {n} sub_state(s) and {sub} is not one of them — "
                    "act_set would land on nothing.",
                )
            return Bind(
                True,
                BIND_UNKNOWN_PAIR,
                f"nothing is known about ({main},{sub}): it is neither in the overlay's jump "
                "tables nor in the census.",
                unverified=True,
            )
        if p.handler is None:
            return Bind(
                True,
                BIND_NO_HANDLER,
                f"the dispatcher's case for ({main},{sub}) runs inline and calls no handler, so "
                "nothing offline can say what it does.",
                unverified=True,
            )
        if p.never_entered:
            why = (
                f"the census says the engine enters ({main},{sub}) ZERO times. Forced, it "
                "survives exactly one tick — 411 of 411 did."
            )
            return Bind(override, BIND_NEVER_ENTERED, why, overridden=override)
        if not p.measured:
            extra = f" {self.census_reason}" if self.census_reason else ""
            return Bind(
                True,
                BIND_UNMEASURED,
                f"no census covers ({main},{sub}), so whether the engine ever enters it is "
                f"UNKNOWN — not zero.{extra}",
                unverified=True,
            )
        if p.dwell_ticks and p.dwell_ticks < MIN_DWELL_TICKS:
            return Bind(
                True,
                BIND_SHORT_DWELL,
                f"({main},{sub}) holds for only {p.dwell_ticks:.1f} ticks "
                f"({p.dwell_ticks / 2:.1f} s at 2 Hz) even when the ENGINE picks it.",
                unverified=True,
            )
        return Bind(
            True,
            BIND_OK,
            f"the engine entered ({main},{sub}) {p.entered} time(s) and held it "
            f"{p.dwell_ticks:.1f} tick(s).",
        )

    # the chain

    @property
    def has_chain(self) -> bool:
        return bool(self.chain) and any(p.next is not None for p in self)

    @property
    def hubs(self) -> list[Pair]:
        """Where most hand-offs land: the brain picks again there."""
        return [(int(m), int(s)) for m, s in self.chain.get("hubs", [])]

    def successors(self, main: int, sub: int) -> list[Handoff]:
        p = self.pair(main, sub)
        return list(p.next or ()) if p is not None else []

    def predecessors(self, main: int, sub: int) -> list[PairIntel]:
        p = self.pair(main, sub)
        if p is None:
            return []
        return [q for q in (self.pair(*k) for k in p.prev) if q is not None]

    def chain_from(self, main: int, sub: int, depth: int = 6) -> list[PairIntel]:
        """The pairs reachable by hand-offs, breadth first; hubs are terminals."""
        start = self.pair(main, sub)
        if start is None:
            return []
        hubs = set(self.hubs)
        seen = {(main, sub)}
        order, frontier = [start], [start]
        for _ in range(depth):
            nxt = []
            for p in frontier:
                if (p.main, p.sub) in hubs and p is not start:
                    continue
                for t in p.successors:
                    q = self.pair(*t)
                    if t in seen or q is None:
                        continue
                    seen.add(t)
                    order.append(q)
                    nxt.append(q)
            frontier = nxt
            if not frontier:
                break
        return order

    def entries(self) -> list[PairIntel]:
        """Handled pairs nothing hands to: the brain's entry points."""
        return [p for p in self if p.handler is not None and not p.prev and p.next is not None]

    def pairs_hitting_with(
        self, set_index: int, entity_species: int | None = None
    ) -> list[PairIntel]:
        """Every pair whose handler spawns an attack that hits with volume set `set_index`."""
        at = self.attacks
        if not at.present:
            return []
        return [
            p
            for p in self
            if p.attack_ids and set_index in at.sets_for(p.attack_ids, entity_species)
        ]


@dataclass(frozen=True)
class HostSummary:
    """One overlay at the size a host chooser needs."""

    species: int
    pairs: int
    handled: int
    timed: int
    """Pairs whose handler tests fixed clip frames."""
    budget: int
    effects: int
    opaque_mains: int
    """Mains with no sub_state jump table: not enumerable, which is not absent."""

    @property
    def free_timing(self) -> int:
        return self.pairs - self.timed

    @classmethod
    def of(cls, si: SpeciesIntel) -> HostSummary:
        return cls(
            si.host_species,
            len(si),
            sum(p.handler is not None for p in si),
            sum(bool(p.tested_frames) for p in si),
            len(si.budget_gated()),
            len(si.pairs_with_effects()),
            sum(not m.get("enumerated") for m in si.main_states),
        )
