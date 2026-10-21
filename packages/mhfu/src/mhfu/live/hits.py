# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A hit census of one big monster in the running game: per (main, sub) pair, how often the
monster entered it, the attack nodes it spawned there, the hits on the hunter and the damage,
with the monster's distance to the hunter at each spawn.

    t = target(monster.vtable, Extracted.find())      # the breakpoints for this species
    c = run(s, monster, t, seconds=60, keep=Keeper(s, monster, summon))
    print(report(c))

Four log-only breakpoints in one trace, so the game keeps running:
- SET_AI_STATE, the one writer of the pair: entries and dwell;
- the species' attack spawner `(unused, entity, id)`: spawns, the metric of a move that attacks;
- ATTACK_TABLE_SETTER `(node, entity, id, table)`, called for every node of every species,
  also the ones another spawner builds (a shock wave, a projectile): which node a hit came from;
- APPLY_HP_DELTA `(entity, delta)` on the hunter: hits, a negative delta.
Each line also logs the monster's pair, its part-0 input and both positions. Never arm another
debugger connection meanwhile: PPSSPP logs to the newest one only.

A zero is not a result until its breakpoint is known to fire, so a census counts the lines each
breakpoint logged (`fired`) and `Census.warnings` names every one that logged nothing.
"""

from __future__ import annotations

import math
import statistics
import struct
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ppsspp_debug import Hit

from .. import addresses as a
from .. import hitbox
from ..em import attacks
from ..em.abi import Engine
from ..entries import ACTION_INPUT_BASE
from ..files import Extracted
from ..structs import BigMonster
from . import observe
from .session import Session

Pair = tuple[int, int]
STATE, SPAWN, NODE, HP = "state", "spawn", "node", "hp"
KINDS = (STATE, SPAWN, NODE, HP)
REACH = 800.0
"""Units from the monster to the hunter within which a spawn counts as in reach; a charge reaches
645."""
NODE_WINDOW = 3.0
"""Seconds after an attack node is built in which a hit on the hunter is credited to its pair."""
TRIP = 15
"""A drop under this with no node of the monster's before it is the hunter walked into it."""
BRING = 450.0
"""Units from the hunter a `Keeper`'s summon puts the monster."""
LOOK = 1.0
"""Emulated seconds between the keeper's looks."""
CHECK = 0.25
"""Wall seconds between looks at the clock while the log is busy; an idle log looks every DRAIN."""
WALL_FACTOR, WALL_SLACK = 4.0, 60.0
"""A window ends after this many wall seconds per emulated one, plus the slack: a game stuck on
a menu logs nothing and its clock does not move."""
WRAP, DRAIN = observe.WRAP, observe.DRAIN
FIELDS = 12
"""Words of a log line (`log_format`)."""


class NoSpawner(LookupError):
    """The species' attack spawner cannot be found, so its spawns cannot be counted."""


@dataclass(frozen=True)
class Target:
    """What a census arms for one species of monster."""

    spawner: int
    """The species' attack spawner."""
    species: int | None = None
    """The overlay's species (em number), None where only the address list was read."""
    tags: Mapping[int, str] = field(default_factory=dict)
    """Attack table handle -> a node id's suffix: none for the overlay's own moveset, `@tN` for
    its other tables, anything else shows as `@` and the handle."""

    @property
    def kinds(self) -> dict[int, str]:
        """Breakpoint address -> the kind of line it logs."""
        return {
            int(a.SET_AI_STATE): STATE,
            self.spawner: SPAWN,
            int(a.ATTACK_TABLE_SETTER): NODE,
            int(a.APPLY_HP_DELTA): HP,
        }

    def tag(self, handle: int) -> str:
        return self.tags.get(handle, f"@{handle:08X}")


def target(vtable: int, game: Extracted | None = None) -> Target:
    """The breakpoints for the monster class at `vtable`. em75's spawner is an address of the
    list; any other species' is the one its overlay calls like a moveset (`mhfu attacks`), which
    needs the extracted game. Raises `NoSpawner` for a species with no attack spawner."""
    if game is None:
        if vtable != int(a.TIGREX_VTABLE):
            raise NoSpawner(
                f"the monster's class {vtable:#x} is not em75's, whose spawner is known "
                "offline: the others' are read from the game, which is not extracted here "
                "(pass --data or set MHFU_DATA)"
            )
        return Target(int(a.TIGREX_ATTACK_SPAWN), 75)
    engine = Engine.load(game)
    species = next((s for s, o in engine.owners.items() if o.vtable.va == vtable), None)
    if species is None:
        raise NoSpawner(f"no species overlay owns the entity class at {vtable:#x}")
    spawner = attacks.spawner(engine.codes[species])
    if spawner is None:
        raise NoSpawner(f"em{species:02d} has no attack spawner: it spawns no attack nodes")
    return Target(spawner.fn, species, table_tags(game, species))


def table_tags(game: Extracted, species: int) -> dict[int, str]:
    """The overlay's attack tables by handle: its moveset's (the primary one) ``, the rest `@tN`."""
    tables = hitbox.tables(game.em(species))
    primary = hitbox.primary_table(tables)
    return {t.handle: "" if t is primary else f"@t{k}" for k, t in enumerate(tables)}


# --- the log ---


def log_format(entity: int) -> str:
    """What each breakpoint logs: `observe.log_format`'s clock, $ra, pair and a0..a3, then the
    monster's part-0 input and the x and z of the monster and of the hunter."""

    def word(address: int, size: int) -> str:
        return f"{{[{address:#x},{size}]}}"

    monster = entity + int(a.ENTITY.POSITION)
    hunter = int(a.PLAYER_ENTITY) + int(a.ENTITY.POSITION)
    here = [
        word(entity + int(a.ENTITY.ANIM_INPUT), 2),
        word(monster, 4),
        word(monster + 8, 4),
        word(hunter, 4),
        word(hunter + 8, 4),
    ]
    return " ".join([observe.log_format(entity), *here])


def _f32(word: int) -> float:
    return float(struct.unpack("<f", struct.pack("<I", word))[0])


def _s32(word: int) -> int:
    return word - WRAP if word & 0x8000_0000 else word


@dataclass(frozen=True)
class Event:
    """One line of the log."""

    kind: str
    t: float
    """Emulated seconds since the window began."""
    pair: Pair
    """The monster's pair when it logged: for a pair change, the one it left."""
    entry: int | None
    """The executor entry its part 0 played; None below ACTION_INPUT_BASE."""
    dist: float
    """From the monster to the hunter in x and z."""
    to: Pair | None = None
    """STATE: the pair entered."""
    id: int | None = None
    """SPAWN, NODE: the attack id."""
    table: int | None = None
    """NODE: the attack table's handle."""
    delta: int | None = None
    """HP: the signed change of the hunter's HP."""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "k": self.kind,
            "t": round(self.t, 3),
            "pair": list(self.pair),
            "entry": self.entry,
            "dist": round(self.dist, 1),
        }
        if self.to is not None:
            out["to"] = list(self.to)
        for name in ("id", "table", "delta"):
            if (value := getattr(self, name)) is not None:
                out[name] = value
        return out


def parse(hit: Hit, start: int, entity: int, kinds: Mapping[int, str]) -> Event | None:
    """A hit logged in `log_format`; None for any line that is not about the monster or the
    hunter (the monster's own HP changes, say) or not in the format."""
    try:
        words = [int(f, 16) for f in (hit.message or "").split()]
    except ValueError:
        return None
    kind = kinds.get(hit.pc or hit.address)
    if kind is None or len(words) != FIELDS:
        return None
    usec, _ra, pair, a0, a1, a2, a3, anim, mx, mz, px, pz = words
    common: dict[str, Any] = {
        "t": ((usec - start) % WRAP) / 1e6,
        "pair": (pair & 0xFF, pair >> 8),
        "entry": anim - ACTION_INPUT_BASE if anim >= ACTION_INPUT_BASE else None,
        "dist": math.hypot(_f32(mx) - _f32(px), _f32(mz) - _f32(pz)),
    }
    if kind == STATE and a0 == entity:
        return Event(kind, to=(a1 & 0xFF, a2 & 0xFF), **common)
    if kind == SPAWN and a1 == entity:
        return Event(kind, id=a2, **common)
    if kind == NODE and a1 == entity:
        return Event(kind, id=a2, table=a3, **common)
    if kind == HP and a0 == int(a.PLAYER_ENTITY):
        return Event(kind, delta=_s32(a1), **common)
    return None


# --- the accounting ---


@dataclass
class PairRow:
    entries: int = 0
    dwell_s: float = 0.0
    spawns: int = 0
    spawn_in_reach: int = 0
    spawn_ids: Counter[int] = field(default_factory=Counter)
    nodes: int = 0
    node_in_reach: int = 0
    node_ids: Counter[str] = field(default_factory=Counter)
    """Attack ids with their table's tag: `6`, `2@t1`."""
    hits: int = 0
    hits_in_reach: int = 0
    damage: int = 0
    deltas: list[int] = field(default_factory=list)

    @property
    def ids(self) -> Counter[str]:
        """What the pair spawned: its nodes' ids, else its spawner's."""
        return self.node_ids or Counter({str(k): v for k, v in self.spawn_ids.items()})

    def to_json(self) -> dict[str, Any]:
        return {
            "entries": self.entries,
            "dwell_s": round(self.dwell_s, 1),
            "spawns": self.spawns,
            "spawn_in_reach": self.spawn_in_reach,
            "spawn_ids": {str(k): v for k, v in sorted(self.spawn_ids.items())},
            "nodes": self.nodes,
            "node_in_reach": self.node_in_reach,
            "node_ids": dict(sorted(self.node_ids.items())),
            "hits": self.hits,
            "hits_in_reach": self.hits_in_reach,
            "damage": self.damage,
            "hit_deltas": sorted(self.deltas),
        }


@dataclass
class Census:
    seconds: float
    reach: float
    pairs: dict[Pair, PairRow] = field(default_factory=dict)
    fired: Counter[str] = field(default_factory=Counter)
    """Lines each breakpoint logged, whatever they said."""
    trips: Counter[Pair] = field(default_factory=Counter)
    """Hits under TRIP not credited to a node: the hunter was walked into."""
    loose: list[Event] = field(default_factory=list)
    """Hits credited to the current pair for want of a node of the monster's before them."""
    ended: str = "time"
    """`time`, or why a window ended early: `dead`, `left` (the hunter left the area), `slow`."""
    skipped: int = 0
    """Lines not about the monster or the hunter, or not in the format."""
    dropped: int = 0
    """Lines lost because the log was read too slowly."""
    events: list[Event] = field(default_factory=list)

    def total(self, name: str) -> int:
        return sum(getattr(r, name) for r in self.pairs.values())

    @property
    def warnings(self) -> list[str]:
        """Each breakpoint that logged nothing, with what else says whether it is dead."""
        said = {
            STATE: "no pair change",
            SPAWN: "no attack spawned",
            NODE: "no attack node built",
            HP: "no HP change of the hunter",
        }
        out = []
        for kind in KINDS:
            if not self.fired[kind]:
                out.append(
                    f"the {kind} breakpoint logged nothing: {said[kind]} in {self.seconds:.0f} s, "
                    "or it did not take"
                )
        if self.fired[NODE] and not self.fired[SPAWN]:
            out.append(
                "nodes were built but the spawner logged nothing: another spawner's, or dead"
            )
        if self.fired[SPAWN] and not self.fired[NODE]:
            out.append(
                "the spawner logged but no node was built: all out of section, or the setter's"
            )
        reach = self.total("spawn_in_reach")
        if reach and not self.fired[HP]:
            out.append(f"{reach} spawn(s) in reach and not one HP change: misses, or HP is dead")
        return out

    def to_json(self) -> dict[str, Any]:
        return {
            "seconds": round(self.seconds, 1),
            "reach": self.reach,
            "ended": self.ended,
            "fired": {k: self.fired[k] for k in KINDS},
            "skipped": self.skipped,
            "dropped": self.dropped,
            "warnings": self.warnings,
            "pairs": {f"{p[0]},{p[1]}": r.to_json() for p, r in sorted(self.pairs.items())},
            "trips": {f"{p[0]},{p[1]}": n for p, n in sorted(self.trips.items())},
            "loose_hits": [e.to_json() for e in self.loose],
            "events": [e.to_json() for e in self.events],
        }


def census(
    events: Sequence[Event],
    seconds: float,
    reach: float = REACH,
    tag: Callable[[int], str] = lambda handle: f"@{handle:08X}",
) -> Census:
    """The accounting of a window of `seconds`. A pair's dwell runs from one change to the next
    (the first from the window's start); a hit is credited to the pair that built the monster's
    last node within NODE_WINDOW, else to the pair it was in."""
    out = Census(seconds, reach, events=list(events))
    out.fired.update(e.kind for e in events)

    def row(p: Pair) -> PairRow:
        return out.pairs.setdefault(p, PairRow())

    held: Pair | None = None
    since = 0.0
    node: Event | None = None
    for ev in events:
        if ev.kind == STATE and ev.to is not None:
            held = ev.pair if held is None else held
            row(held).dwell_s += ev.t - since
            held, since = ev.to, ev.t
            row(ev.to).entries += 1
        elif ev.kind == SPAWN and ev.id is not None:
            r = row(ev.pair)
            r.spawns += 1
            r.spawn_ids[ev.id] += 1
            r.spawn_in_reach += ev.dist <= reach
        elif ev.kind == NODE and ev.id is not None and ev.table is not None:
            r = row(ev.pair)
            r.nodes += 1
            r.node_ids[f"{ev.id}{tag(ev.table)}"] += 1
            r.node_in_reach += ev.dist <= reach
            node = ev
        elif ev.kind == HP and ev.delta is not None and ev.delta < 0:
            owner = ev.pair
            if node is not None and ev.t - node.t <= NODE_WINDOW:
                owner = node.pair
            elif -ev.delta < TRIP:
                out.trips[owner] += 1
            else:
                out.loose.append(ev)
            r = row(owner)
            r.hits += 1
            r.hits_in_reach += ev.dist <= reach
            r.damage -= ev.delta
            r.deltas.append(ev.delta)
    if held is not None:
        row(held).dwell_s += seconds - since
    return out


# --- the window ---


def run(
    s: Session,
    monster: BigMonster,
    aim: Target,
    seconds: float,
    *,
    reach: float = REACH,
    keep: Callable[[], str] | None = None,
) -> Census:
    """Trace `monster` for `seconds` of emulated time, then `census` it. `keep` is called every
    LOOK emulated seconds and returns `ok` or the reason to end the window early."""
    client, entity = s.client, monster.base
    kinds = aim.kinds
    condition = f"a0 == {entity:#x} || a1 == {entity:#x} || a0 == {int(a.PLAYER_ENTITY):#x}"
    start = client.evaluate("usec")

    def now() -> float:
        return ((client.evaluate("usec") - start) % WRAP) / 1e6

    events: list[Event] = []
    skipped = 0
    ended, used, next_look = "time", 0.0, LOOK
    wall = checked = time.monotonic()
    with client.trace(kinds, condition=condition, log_format=log_format(entity)) as stream:
        missing = set(kinds) - {b.address for b in client.breakpoints()}
        if missing:
            where = ", ".join(f"{x:#x}" for x in sorted(missing))
            raise RuntimeError(f"breakpoints not armed: {where}")
        while True:
            try:
                hit: Hit | None = stream.next(DRAIN)
            except TimeoutError:
                hit = None
            if hit is not None:
                ev = parse(hit, start, entity, kinds)
                if ev is None:
                    skipped += 1
                else:
                    events.append(ev)
                if time.monotonic() - checked < CHECK:
                    continue
            checked = time.monotonic()
            used = now()
            if used >= seconds:
                break
            if keep is not None and used >= next_look:
                next_look = used + LOOK
                if (why := keep()) != "ok":
                    ended = why
                    break
            if time.monotonic() - wall > WALL_FACTOR * seconds + WALL_SLACK:
                ended = "slow"
                break
        dropped = stream.dropped
    used = min(now(), seconds)
    result = census([e for e in events if e.t <= used], used, reach, aim.tag)
    result.ended, result.skipped, result.dropped = ended, skipped, dropped
    return result


class Keeper:
    """What the window does to the game, once per look and never per tick: ends it when the
    hunter is dead (HP 0 on three looks) or left the area, and with `engage` brings the monster
    to the hunter by `summon` when it is out of the section, over FAR units away in combat, or
    has been out of combat for IDLE looks."""

    FAR = 1100.0
    IDLE = 20

    def __init__(
        self,
        s: Session,
        monster: BigMonster,
        summon: Callable[[], object],
        *,
        engage: bool = False,
    ) -> None:
        self.s, self.monster, self.summon, self.engage = s, monster, summon, engage
        self.area = s.game.area_index
        self.summons = 0
        self._dead = 0
        self._idle = 0

    def __call__(self) -> str:
        s, m = self.s, self.monster
        if s.game.area_index != self.area:
            return "left"
        self._dead = self._dead + 1 if s.game.player.hp == 0 else 0
        if self._dead >= 3:
            return "dead"
        if not self.engage:
            return "ok"
        combat = s.mem.u8(m.base + int(a.ENTITY.COMBAT_MODE))
        self._idle = 0 if combat else self._idle + 1
        px, _, pz = s.game.player.position
        mx, _, mz = m.position
        far = math.hypot(px - mx, pz - mz) > self.FAR
        if m.section != self.area or (combat and far) or self._idle >= self.IDLE:
            self.summon()
            self.summons += 1
            self._idle = 0
        return "ok"


# --- the report ---


def report(c: Census) -> str:
    """One line per pair: entries, dwell, spawns, hits and damage, each with how many were in
    reach, then the ids; the totals, and every breakpoint that logged nothing."""
    head = (
        f"{'pair':8}{'entries':>8}{'dwell':>8}{'spawns':>8}{'in reach':>9}"
        f"{'hits':>6}{'in reach':>9}{'damage':>8}  ids"
    )
    lines = [head]
    for p, r in sorted(c.pairs.items()):
        ids = " ".join(f"{k}x{n}" for k, n in sorted(r.ids.items()))
        lines.append(
            f"({p[0]},{p[1]})".ljust(8)
            + f"{r.entries:8d}{r.dwell_s:7.1f}s{r.spawns:8d}{r.spawn_in_reach:9d}"
            + f"{r.hits:6d}{r.hits_in_reach:9d}{r.damage:8d}  {ids or '-'}"
        )
    dists = [e.dist for e in c.events if e.kind == SPAWN]
    near = f", median spawn distance {statistics.median(dists):.0f}" if dists else ""
    lines.append(
        f"{c.seconds:.0f} s ({c.ended}), reach {c.reach:g}: {c.total('entries')} entries, "
        f"{c.total('spawns')} spawns ({c.total('spawn_in_reach')} in reach{near}), "
        f"{c.total('hits')} hits for {c.total('damage')}, {sum(c.trips.values())} trip(s)"
    )
    fired = ", ".join(f"{k} {c.fired[k]}" for k in KINDS)
    lines.append(f"lines: {fired}; {c.skipped} skipped, {c.dropped} dropped")
    lines += [f"! {w}" for w in c.warnings]
    return "\n".join(lines)
