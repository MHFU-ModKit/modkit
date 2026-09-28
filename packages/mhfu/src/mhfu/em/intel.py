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

import hashlib
import time
from collections import Counter, defaultdict
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
        f"  census: {c['transitions']} transitions, {c['observed_pairs']} pair(s) observed"
        if c["present"]
        else f"  census: ABSENT ({c['reason']})"
    )
    return "\n".join(out)
