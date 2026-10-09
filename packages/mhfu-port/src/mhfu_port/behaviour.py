# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A port's behaviour graph: blocks on a canvas, wired into paths, compiled to the rule table.

A path starts at an event or state block no other block feeds, or at a block a move lists in
`during`, walks `next` and plays the moves a reached block lists in `play`. One path is one
`Rule`, in canvas order: higher is checked first. `KINDS` is the schema of a block; the loader,
the compiler and the studio all read it.

This module sits under `manifest`: the names the manifest and the graph share are defined here.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from mhfu import addresses, hitzone

if TYPE_CHECKING:
    from .manifest import Manifest


class ManifestError(ValueError):
    """A manifest that does not load: bad TOML, an unknown key, a wrong type or a bad value."""


def need(ok: bool, where: str, why: str) -> None:
    if not ok:
        raise ManifestError(f"{where}: {why}")


def toml(key: str) -> dict[str, str]:
    """Field metadata: the TOML key the field is stored under; empty for none."""
    return {"toml": key}


Event = str
EVENTS: tuple[Event, ...] = addresses.MONSTER_EVENT_KIND.names
"""A monster event's name, as the framework takes it in a rule's `on`."""
PART_EVENTS: tuple[Event, ...] = ("flinch", "part_broken")
"""The events a rule's `part` narrows."""
MAIN_STATES = range(8)
"""A big monster's behaviour main states."""
PARTS = range(hitzone.PART_MASK + 1)
SEAM_RULES: int = addresses.EM_CFG.RULES.count or 0
"""Rules the framework's native brain seam holds."""
UNLIMITED_DIST = 1.0e9

Role = Literal["event", "state", "condition", "modifier"]
ParamType = Literal["int", "float", "choice", "part", "mains"]


@dataclass(frozen=True)
class Param:
    """One value a block takes, written flat beside its `kind`."""

    name: str
    type: ParamType
    """`part` is a body part index; `mains` a non-empty list of main states."""
    title: str
    default: object = None
    """None: required unless `optional`."""
    optional: bool = False
    """May be left out: None, unlimited or any."""
    lo: float | None = None
    hi: float | None = None
    choices: tuple[str, ...] = ()
    tip: str = ""


@dataclass(frozen=True)
class Kind:
    """One kind of block."""

    name: str
    role: Role
    title: str
    params: tuple[Param, ...] = ()
    tip: str = ""


_ON = "on_"
_EVENT_WORDS: dict[Event, tuple[str, str]] = {
    "noticed": ("On notice", "The monster notices the hunter"),
    "combat_entered": ("On combat start", "The monster enters combat"),
    "combat_left": ("On combat end", "The monster leaves combat"),
    "flinch": ("On flinch", "The monster flinches; its move replaces the base monster's flinch"),
    "part_broken": ("On part break", "A part of the monster breaks"),
    "tail_cut": ("On tail cut", "The monster's tail is cut off"),
}
_PART = Param(
    "part", "part", "Of part", optional=True, tip="Only a flinch or break of this part; else any"
)


def _event(name: Event) -> Kind:
    words = name.replace("_", " ")
    title, tip = _EVENT_WORDS.get(name, (f"On {words}", f"The monster event {words}"))
    return Kind(_ON + name, "event", title, (_PART,) if name in PART_EVENTS else (), tip)


KINDS: dict[str, Kind] = {
    k.name: k
    for k in (
        *map(_event, EVENTS),
        Kind(
            "host_state",
            "state",
            "Base monster in state",
            (
                Param(
                    "mains",
                    "mains",
                    "Main states",
                    tip="Any of these main states of the base monster",
                ),
            ),
            "Fires while the base monster is in one of these main states",
        ),
        Kind(
            "played_for",
            "condition",
            "Played for at least",
            (
                Param(
                    "frames",
                    "int",
                    "Frames",
                    lo=1,
                    tip="AI frames the move or main state must have stood",
                ),
            ),
            "The move it follows, or the main state, has stood this long",
        ),
        Kind(
            "distance",
            "condition",
            "Hunter distance",
            (
                Param("lo", "float", "Nearest", 0.0, lo=0, tip="The nearest the hunter may be"),
                Param(
                    "hi",
                    "float",
                    "Farthest",
                    optional=True,
                    lo=0,
                    tip="The farthest the hunter may be; else any distance",
                ),
            ),
            "The hunter is this far from the monster, in world units",
        ),
        Kind(
            "hunter_moving",
            "condition",
            "Hunter moving",
            (
                Param(
                    "way",
                    "choice",
                    "Way",
                    choices=("away", "closer"),
                    tip="Away from the monster, or closer to it",
                ),
            ),
            "The hunter is moving away from the monster or closer to it",
        ),
        Kind(
            "cooldown",
            "modifier",
            "Then wait",
            (Param("frames", "int", "Frames", lo=1, tip="AI frames before it may fire again"),),
            "After it fired, the path waits before it may fire again",
        ),
        Kind(
            "limit",
            "modifier",
            "At most",
            (Param("times", "int", "Times", lo=1, tip="How often it may fire"),),
            "The path fires at most this often; without it, every time",
        ),
        Kind(
            "force",
            "modifier",
            "Right away",
            (),
            'An own move asked during the monster\'s notice waits for combat, so the "!" and'
            " the howl play out; this plays it at once",
        ),
        Kind(
            "mode",
            "modifier",
            "Enter in mode",
            (
                Param(
                    "mode",
                    "int",
                    "Mode",
                    lo=0,
                    hi=0xFF,
                    tip="The mode a pair move's action is entered with; an own move has none",
                ),
            ),
            "The mode the base monster's action is entered with",
        ),
    )
}
"""The schema of a block, by kind name."""


@dataclass
class Block:
    """A node on the canvas. `params` sit flat beside `kind` in the TOML."""

    kind: str
    at: tuple[float, float]
    """Canvas x, y."""
    params: dict[str, Any] = field(default_factory=dict, metadata={"flat": True})
    next: list[str] = field(default_factory=list)
    """Block ids fed next."""
    play: list[str] = field(default_factory=list)
    """Moves played when a path gets here."""
    label: str = ""


@dataclass
class MoveNode:
    """A move on the canvas."""

    at: tuple[float, float]
    during: list[str] = field(default_factory=list)
    """Block ids fed while the move plays."""


@dataclass
class Behaviour:
    """The graph: blocks by id and the moves wired into it."""

    blocks: dict[str, Block] = field(default_factory=dict)
    moves: dict[str, MoveNode] = field(default_factory=dict)


@dataclass
class Rule:
    """A trigger the native seam evaluates every frame: when the live pair is `from_move`'s or in
    `from_main` and has stood `min_frames` (or own move `from_move` has played that long), the
    hunter is within `dist` and receding or closing as asked, play `play`, a pair or an own move;
    then wait `cooldown` frames, at most `count` times (None: unlimited). A pair rule waits while
    an own move plays.

    With `on`, the rule fires on that monster event instead (`part`: only the flinch or break of
    that part), under the same distance, cooldown and count; `on = "flinch"` plays its move in
    place of the host's flinch. An own move asked while the monster's notice runs waits for
    combat (else it cuts off the "!" and the roar) unless `force`."""

    play: str
    on: Event | None = None
    part: int | None = None
    from_move: str | None = field(default=None, metadata=toml("from"))
    from_main: list[int] = field(default_factory=list)
    min_frames: int = 0
    dist: tuple[float, float] = (0.0, UNLIMITED_DIST)
    receding: bool = False
    closing: bool = False
    mode: int = 0
    cooldown: int = 0
    count: int | None = None
    force: bool = False
    label: str = ""


@dataclass(frozen=True)
class Path:
    """A walk through the graph that plays a move."""

    blocks: tuple[str, ...]
    """Block ids in order; never empty."""
    play: str
    during: str | None
    """The move whose while-playing port feeds `blocks[0]`; None: `blocks[0]` is an event or
    state block with no input."""


# the schema in use

ID = re.compile(r"[a-z][a-z0-9_]*")
"""A block id."""


def event_of(kind: str) -> Event | None:
    """The monster event an event block fires on; None for any other kind."""
    return kind.removeprefix(_ON) if KINDS[kind].role == "event" else None


def new_id(b: Behaviour) -> str:
    """The lowest free block id, `b1`, `b2`, ..."""
    return next(f"b{i}" for i in itertools.count(1) if f"b{i}" not in b.blocks)


def _params(b: Block) -> dict[str, Any]:
    """`b`'s params with its kind's defaults filled in."""
    out = {p.name: p.default for p in KINDS[b.kind].params if p.default is not None}
    return out | b.params


# validation


def _int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _number(v: object) -> float | None:
    return v if isinstance(v, int | float) and not isinstance(v, bool) else None


def _check_value(p: Param, v: object, w: str) -> None:
    n = _number(v)
    if p.type == "int":
        need(_int(v), w, f"expected an integer, got {v!r}")
    elif p.type == "float":
        need(n is not None, w, f"expected a number, got {v!r}")
    elif p.type == "choice":
        want = ", ".join(map(repr, p.choices))
        need(v in p.choices, w, f"expected one of {want}, got {v!r}")
    elif p.type == "part":
        need(_int(v) and v in PARTS, w, f"{v!r} is not a part")
    else:
        ok = isinstance(v, list) and bool(v) and all(_int(k) and k in MAIN_STATES for k in v)
        need(ok, w, f"{v!r} is not a list of main states, 0..{MAIN_STATES[-1]}")
    if n is not None:
        need(p.lo is None or n >= p.lo, w, f"{n} is under {p.lo}")
        need(p.hi is None or n <= p.hi, w, f"{n} is over {p.hi}")


def _check_params(kind: Kind, params: dict[str, Any], w: str) -> None:
    known = {p.name for p in kind.params}
    unknown = [k for k in params if k not in known]
    need(not unknown, w, f"unknown param(s) {', '.join(unknown)} for kind {kind.name}")
    for p in kind.params:
        if p.name in params:
            _check_value(p, params[p.name], f"{w}.{p.name}")
        else:
            need(p.optional or p.default is not None, f"{w}.{p.name}", "missing")


def _check_at(at: object, w: str) -> None:
    ok = isinstance(at, tuple | list) and len(at) == 2 and all(_number(v) is not None for v in at)
    need(ok, w, f"expected 2 numbers, got {at!r}")


def _check_names(names: list[str], known: Collection[str], w: str, what: str) -> None:
    for n in names:
        need(n in known, w, f"{n!r} is not in {what}")
    need(len(set(names)) == len(names), w, "names one twice")


def _cycle(blocks: dict[str, Block]) -> str | None:
    """A block on a cycle through `next`, else None."""
    state: dict[str, bool] = {}  # True: on the walk; False: done

    def visit(i: str) -> str | None:
        if i in state:
            return i if state[i] else None
        state[i] = True
        found = next((c for n in blocks[i].next if (c := visit(n)) is not None), None)
        state[i] = False
        return found

    return next((c for i in blocks if (c := visit(i)) is not None), None)


def validate(m: Manifest) -> None:
    """The graph's own rules: known kinds, params that match them, names that exist, no cycle.
    Paths are checked by `compile`."""
    b = m.behaviour
    for i, blk in b.blocks.items():
        w = f"behaviour.blocks.{i}"
        need(ID.fullmatch(i) is not None, w, "id is a lowercase letter, then letters, digits, _")
        need(blk.kind in KINDS, w, f"kind {blk.kind!r} is not one of " + ", ".join(KINDS))
        _check_params(KINDS[blk.kind], blk.params, w)
        _check_at(blk.at, w + ".at")
        _check_names(blk.next, b.blocks, w + ".next", "blocks")
        _check_names(blk.play, m.moves, w + ".play", "moves")
    for name, node in b.moves.items():
        w = f"behaviour.moves.{name}"
        need(name in m.moves, w, "is not in moves")
        _check_at(node.at, w + ".at")
        _check_names(node.during, b.blocks, w + ".during", "blocks")
    cyclic = _cycle(b.blocks)
    need(cyclic is None, f"behaviour.blocks.{cyclic}", "next leads back to the block")


# paths


def paths(m: Manifest) -> list[Path]:
    """Every complete path, in priority order."""
    b = m.behaviour
    fed = {n for blk in b.blocks.values() for n in blk.next}
    listed = {n for node in b.moves.values() for n in node.during}
    sources: list[tuple[str, str | None]] = [
        (i, None)
        for i, blk in b.blocks.items()
        if KINDS[blk.kind].role in ("event", "state") and i not in fed | listed
    ]
    sources += [(i, name) for name, node in b.moves.items() for i in node.during]
    out: list[Path] = []

    def walk(trail: tuple[str, ...], during: str | None) -> None:
        blk = b.blocks[trail[-1]]
        out.extend(Path(trail, mv, during) for mv in blk.play)
        for n in blk.next:
            if n not in trail:  # a cycle is `validate`'s to refuse
                walk((*trail, n), during)

    for i, during in sources:
        walk((i,), during)
    return sorted(out, key=lambda p: _priority(m, p))


def _priority(m: Manifest, p: Path) -> tuple[Any, ...]:
    """Higher on the canvas first: each block's `(y, x)` in order, then the played move's."""
    b = m.behaviour

    def row(at: tuple[float, float]) -> tuple[float, float]:
        return at[1], at[0]

    node = b.moves.get(p.play)
    move = row(node.at) if node is not None else (math.inf, math.inf)
    return tuple(row(b.blocks[i].at) for i in p.blocks), move, p.blocks, p.play, p.during or ""


def loose(m: Manifest) -> list[str]:
    """Block ids on no complete path: an unfinished wiring."""
    used = {i for p in paths(m) for i in p.blocks}
    return [i for i in m.behaviour.blocks if i not in used]


# compiling


def _dist(p: dict[str, Any]) -> dict[str, Any]:
    hi = UNLIMITED_DIST if p.get("hi") is None else float(p["hi"])
    return {"dist": (float(p["lo"]), hi)}


_APPLY: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "host_state": lambda p: {"from_main": list(p["mains"])},
    "played_for": lambda p: {"min_frames": p["frames"]},
    "distance": _dist,
    "hunter_moving": lambda p: {"receding": p["way"] == "away", "closing": p["way"] == "closer"},
    "cooldown": lambda p: {"cooldown": p["frames"]},
    "limit": lambda p: {"count": p["times"]},
    "force": lambda p: {"force": True},
    "mode": lambda p: {"mode": p["mode"]},
}
"""A block kind's fields of the `Rule`; an event block sets `on` and `part` itself."""


def _rule(m: Manifest, p: Path) -> Rule:
    w = "behaviour path " + " > ".join(p.blocks) + f" plays {p.play}"
    blocks = [m.behaviour.blocks[i] for i in p.blocks]
    kinds = [blk.kind for blk in blocks]
    for k in kinds:
        need(kinds.count(k) == 1, w, f"has two {KINDS[k].title!r} blocks")
    events = [blk for blk in blocks if KINDS[blk.kind].role == "event"]
    need(len(events) <= 1, w, "has more than one event block")
    kw: dict[str, Any] = {"play": p.play, "from_move": p.during}
    for blk in blocks:
        if (event := event_of(blk.kind)) is not None:
            kw |= {"on": event, "part": blk.params.get("part")}
        else:
            kw |= _APPLY[blk.kind](_params(blk))
    if labels := [blk.label for blk in blocks if blk.label]:
        kw["label"] = "; ".join(labels)
    r = Rule(**kw)
    lo, hi = r.dist
    need(0 <= lo < hi, w, "dist needs 0 <= lo < hi")
    need(r.from_move != r.play, w, "from and play are the same move")
    if m.moves[r.play].own:
        need(r.mode == 0, w, "mode is a pair's: an own move enters its carrier")
    else:
        need(r.on != "flinch", w, "on = flinch plays an own move, in place of the host's reaction")
    return r


def compile(m: Manifest) -> list[Rule]:
    """One rule per path, in priority order; `ManifestError` for a bad path."""
    rules = [_rule(m, p) for p in paths(m)]
    need(len(rules) <= SEAM_RULES, "behaviour", f"{len(rules)} paths, the seam holds {SEAM_RULES}")
    return rules


# editing


def uses(m: Manifest, move: str) -> list[str]:
    """Where the graph names `move`, for a refusal."""
    out = [f"block {i} plays it" for i, blk in m.behaviour.blocks.items() if move in blk.play]
    node = m.behaviour.moves.get(move)
    return out + ([f"while {move} plays"] if node is not None and node.during else [])


def rename_move(m: Manifest, old: str, new: str) -> None:
    """Rename `old` in the play lists and the moves table; the manifest's moves are the
    caller's."""
    need(new == old or new not in m.behaviour.moves, f"behaviour.moves.{new}", "already exists")
    for blk in m.behaviour.blocks.values():
        blk.play = [new if n == old else n for n in blk.play]
    m.behaviour.moves = {new if k == old else k: v for k, v in m.behaviour.moves.items()}


def drop_move(m: Manifest, move: str) -> None:
    """Remove `move`'s node and its entries in the play lists."""
    for blk in m.behaviour.blocks.values():
        blk.play = [n for n in blk.play if n != move]
    m.behaviour.moves.pop(move, None)


# schema 1

ROW, COLUMN, LEFT = 120, 220, 260
"""The canvas grid `migrate` lays chains on: a rule a row, a block a column from `LEFT`."""


def _legacy(raw: dict[str, Any], where: str) -> Rule:
    names = {f.metadata.get("toml", f.name): f.name for f in dataclasses.fields(Rule)}
    unknown = sorted(set(raw) - set(names))
    need(not unknown, where, f"unknown key(s) {', '.join(unknown)}")
    need("play" in raw, where, "play: missing")
    kw = {names[k]: v for k, v in raw.items()}
    if "dist" in kw:
        kw["dist"] = tuple(kw["dist"])
    return Rule(**kw)


def _chain(r: Rule, where: str) -> list[tuple[str, dict[str, Any]]]:
    """The blocks a schema 1 rule becomes, in order."""
    need(bool(r.from_move or r.from_main or r.on), where, "needs from, from_main or on")
    need(r.on in (None, *EVENTS), where, "on is one of " + ", ".join(map(repr, EVENTS)))
    need(r.part is None or r.on in PART_EVENTS, where, "part goes with a flinch or break")
    need(not (r.receding and r.closing), where, "cannot be receding and closing")
    lo, hi = r.dist
    chain: list[tuple[str, dict[str, Any]]] = []
    if r.from_main:
        chain.append(("host_state", {"mains": list(r.from_main)}))
    if r.on:
        chain.append((_ON + r.on, {} if r.part is None else {"part": r.part}))
    if r.min_frames:
        chain.append(("played_for", {"frames": r.min_frames}))
    if r.dist != Rule("").dist:
        chain.append(
            ("distance", ({"lo": lo} if lo else {}) | ({"hi": hi} if hi != UNLIMITED_DIST else {}))
        )
    if r.receding or r.closing:
        chain.append(("hunter_moving", {"way": "away" if r.receding else "closer"}))
    if r.mode:
        chain.append(("mode", {"mode": r.mode}))
    if r.force:
        chain.append(("force", {}))
    if r.cooldown:
        chain.append(("cooldown", {"frames": r.cooldown}))
    if r.count is not None:
        chain.append(("limit", {"times": r.count}))
    need(bool(chain), where, f"from {r.from_move!r} alone has no block to carry it")
    return chain


def migrate(rules: list[dict[str, Any]]) -> Behaviour:
    """The graph a schema 1 `[[rule]]` list becomes: one chain a row, in rule order."""
    b = Behaviour()
    from_row: dict[str, int] = {}
    play_row: dict[str, int] = {}
    width = 0
    for row, raw in enumerate(rules):
        where = f"rule[{row}]"
        r = _legacy(raw, where)
        chain = _chain(r, where)
        ids = []
        for col, (kind, params) in enumerate(chain):
            ids.append(new_id(b))
            b.blocks[ids[-1]] = Block(kind, (float(LEFT + col * COLUMN), float(row * ROW)), params)
        for here, there in itertools.pairwise(ids):
            b.blocks[here].next.append(there)
        b.blocks[ids[0]].label = r.label
        b.blocks[ids[-1]].play.append(r.play)
        if r.from_move is not None:
            from_row.setdefault(r.from_move, row)
            b.moves.setdefault(r.from_move, MoveNode((0.0, 0.0))).during.append(ids[0])
        play_row.setdefault(r.play, row)
        b.moves.setdefault(r.play, MoveNode((0.0, 0.0)))
        width = max(width, len(chain))
    for name, node in b.moves.items():
        if name in from_row:
            node.at = (0.0, float(from_row[name] * ROW))
        else:
            node.at = (float(LEFT + width * COLUMN), float(play_row[name] * ROW))
    return b
