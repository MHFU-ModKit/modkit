# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A port's manifest, `ports/<name>.toml`: the donor's files, the host, the build settings and
the port's own clips, moves, behaviour graph, volumes, attacks and effects.

A key left out takes its default, and `dumps` leaves out every value equal to its default. The
files carry no comments: an editor changes the dataclasses and writes the whole file with
`save`, which refuses a manifest that would not load back. A schema 1 file, whose behaviour is a
`[[rule]]` list, loads as the graph `behaviour.migrate` makes of it.
"""

from __future__ import annotations

import dataclasses
import functools
import tomllib
import types
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeVar

import tomli_w
from mhfu import addresses, files, hitzone, inject

from . import behaviour
from .behaviour import Behaviour, Rule, migrate, validate
from .records import ANIM, GEO

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

SCHEMA = 2
LEGACY_SCHEMA = 1
"""Read as well: its behaviour is a `[[rule]]` list."""

Skin = Literal["auto", "transfer", "source"]
SKINS: tuple[Skin, ...] = typing.get_args(Skin)
Shape = Literal["sphere", "capsule"]
SHAPES: tuple[Shape, ...] = typing.get_args(Shape)
Turn = Literal["clip", "still", "hunter", "away", "fixed"]
TURNS: tuple[Turn, ...] = typing.get_args(Turn)

# shared with the behaviour graph, which sits under this module and defines them
ManifestError = behaviour.ManifestError
Event = behaviour.Event
EVENTS = behaviour.EVENTS
PART_EVENTS = behaviour.PART_EVENTS
MAIN_STATES = behaviour.MAIN_STATES
PARTS = behaviour.PARTS
SEAM_RULES = behaviour.SEAM_RULES
UNLIMITED_DIST = behaviour.UNLIMITED_DIST
_need = behaviour.need
_toml = behaviour.toml

MOVE_ATTACKS: int = addresses.MOVE.ATTACKS.count or 0
"""Attacks the move player holds per move."""
OWN_MOVES: int = addresses.EM_MOVES.MOVES.count or 0
"""Own moves the framework holds per port."""
ROWS = range(hitzone.MAX_ROW + 1)
BYTE = range(0x100)
JOINER, NODE_CAPSULE, NODE_SPHERE = hitzone.MARKER_BONES
"""Volume bones that are coordinate spaces, not joints; see `mhfu.hitbox`."""


@dataclass
class Port:
    """The port and the MHFU monster it rides."""

    name: str
    host_species: int
    """The species whose overlay runs the port and whose model PAC it replaces."""
    pac: str
    """File name of the built model PAC."""
    replace: list[int] = field(default_factory=list)
    """Quest monster ids swapped for `host_species` when a quest builds its targets."""

    @property
    def host_frame(self) -> int:
        """Extracted file id of the host's model PAC."""
        return files.monster_pac(self.host_species)

    @property
    def fid(self) -> int:
        """The file id the engine asks for when it loads `host_frame`."""
        return files.engine_id(self.host_frame)

    @property
    def orig(self) -> str:
        """Name of the pristine `host_frame` in the inject directory."""
        return inject.orig_filename(self.host_frame)


@dataclass
class Source:
    """The donor: its game, species and files."""

    model: int
    """Model, skeleton and textures."""
    game: str = "mhp3rd"
    em_id: int | None = None
    """Donor species, for a donor the builder's tables do not know; picks the anim
    record-to-bone map. Negative builds unmapped."""
    geo_override: int | None = field(default=None, metadata=_toml("geo"))
    anim_override: int | None = field(default=None, metadata=_toml("anim"))

    @property
    def geo(self) -> int:
        """The geometry file: `model + GEO` unless overridden."""
        return self.model + GEO if self.geo_override is None else self.geo_override

    @property
    def anim(self) -> int:
        """The moveset file: `model + ANIM` unless overridden."""
        return self.model + ANIM if self.anim_override is None else self.anim_override


@dataclass
class Build:
    """How the port is built."""

    source_skeleton: bool = False
    """Ship the donor's own rig instead of retargeting onto the host's."""
    skin: Skin = "auto"
    ground_lift: float = 0.0
    """Raises the donor onto MHFU's floor, in engine units; measure it, never guess."""
    animated: int | None = None
    """Overrides the animated joint count derived from the moveset."""
    bone_offset: int | None = None
    """Overrides the donor species' measured anim record-to-bone offset."""
    skip_bones: list[int] | None = None
    """Overrides the donor species' skipped bones."""
    drop_joints: list[int] | None = None
    nb: int = 3


@dataclass
class Clip:
    """One clip of the port's vocabulary, the donor's MHP3rd clip `source` (`stream * 100 +
    slot`). Without `slot` it is a name only and `layout` packs the clip; with one it is pinned
    in that executor entry, the a1 that plays it (`source` None: the clip numbered like it).

    `frames` and `loop` fingerprint the clip in a build; `labelled_build` names the build the
    label was written against. With `start`, the entry holds only source frames `start` to
    `start + frames`, so one source can fill several entries.
    """

    slot: int | None = None
    source: int | None = field(default=None, kw_only=True)
    start: int | None = field(default=None, kw_only=True)
    frames: int | None = None
    loop: bool | None = None
    impact_frame: int | None = None
    turn: float | None = None
    """Degrees YAW turns while the clip plays, positive the way YAW grows: toward the
    monster's +x, its left. None: the turn its own body makes (`mhfu_port.travel`)."""
    label: str = ""
    labelled_build: str | None = None

    @property
    def cut(self) -> tuple[int, int] | None:
        """`(start, frames)` of the source the entry holds; None: all of it."""
        if self.start is None or self.frames is None:
            return None
        return self.start, self.frames

    @property
    def id(self) -> int:
        """The MHP3rd clip id."""
        if self.source is not None:
            return self.source
        if self.slot is None:
            raise ManifestError("a clip needs a source or a slot")
        return self.slot


@dataclass
class Claim:
    """The host enter-actions a move takes over: any main in `mains`, every sub or only `sub`."""

    mains: list[int] = field(metadata=_toml("main"))
    sub: int | None = None

    def __post_init__(self) -> None:
        self.mains = sorted(set(self.mains))

    @property
    def mask(self) -> int:
        return sum(1 << m for m in self.mains)


@dataclass
class AttackWindow:
    """Attack record `id`, spawned when the clip reaches `frame` and ended at `end` (None: it
    lives as its record says)."""

    id: int
    frame: int
    end: int | None = None
    label: str = ""


@dataclass
class Steer:
    """How an own move turns, and whether a wall ends it."""

    turn: Turn = "clip"
    """`clip` follows the clip's turn, `hunter` and `away` turn at `rate`, `fixed` turns
    `angle` degrees over `frames` AI frames, `still` not at all."""
    rate: int | None = None
    """YAW units an AI frame for `hunter` and `away`; None: the host charge's."""
    angle: float | None = None
    frames: int | None = None
    walls: bool = True
    dir: float = 0.0
    """Degrees of the travel against the facing, which walls count as ahead (180: backwards)."""


@dataclass
class Move:
    """A host behaviour pair `(main, sub)` and the clip painted while it runs, or, without a
    pair, an own move the framework's move player plays.

    On a pair, the pair owns the hitbox, damage and effects; `clip` (a name in `clips`) or
    `anim` (a raw a1) only the animation. `after` is the move handed to when this one ends or
    has stood `hold_max` ticks; `claim` substitutes it for the host brain's own picks.

    An own move plays its clip on every body part, riding `carrier` (None: the host's hub),
    spawns `attacks` at its clip frames and turns as `steer` says; the host's attacks for the
    clip's entry are muted unless `host_attacks`.
    """

    main: int | None = None
    sub: int | None = None
    clip: str | None = None
    anim: int | None = None
    latch: int = 1
    """Executor dispatches one forced move covers."""
    min_gap: int = 2
    """Ticks before the same move may be issued again."""
    allow_unentered: bool = False
    """Bind the pair even though the census never saw the engine enter it."""
    after: str | None = None
    hold_max: int | None = None
    claim: Claim | None = None
    attacks: list[AttackWindow] = field(default_factory=list, metadata=_toml("attack"))
    steer: Steer = field(default_factory=Steer)
    length: int | None = None
    """AI frames from the clip's dispatch; None: until the clip ends."""
    carrier: tuple[int, int] | None = None
    host_attacks: bool = False
    label: str = ""

    @property
    def own(self) -> bool:
        """Played by the move player, not on a host pair."""
        return self.main is None

    @property
    def pair(self) -> tuple[int, int] | None:
        """The host pair; None for an own move."""
        return None if self.main is None or self.sub is None else (self.main, self.sub)


@dataclass
class Hurtbox:
    """A volume where the port is hit, on the rig the port ships.

    `part` is the damage accumulator it feeds, `hitzone_row` the grid row the hit is scaled by;
    `offset` and `to` (a capsule's far end) are bone-relative. `flags` ships verbatim.
    """

    bone: int
    radius: float
    part: int | None = None
    hitzone_row: int | None = None
    shape: Shape = "sphere"
    offset: list[float] | None = None
    to: list[float] | None = None
    flags: int = 0
    label: str = ""

    @property
    def is_capsule(self) -> bool:
        return self.shape == "capsule"

    @property
    def is_marker(self) -> bool:
        return self.bone in hitzone.MARKER_BONES


@dataclass
class Part:
    """A name for one damage accumulator."""

    index: int
    hitzone_row: int | None = None
    """Advisory: the row is per volume and a part may use several."""
    severable: bool = False
    label: str = ""


@dataclass
class HitzoneState:
    """One damage grid: rows of `hitzone.COLUMNS` percentages. The grid is species data the
    port inherits from its host."""

    name: str = field(metadata=_toml("state"))
    rows: list[list[int]] = field(default_factory=list)
    label: str = ""

    def value(self, row: int, column: str) -> int:
        return self.rows[row][hitzone.COLUMNS.index(column)]


@dataclass
class Hitbox:
    """An attack volume on the port's rig, in the host overlay's volume set `set`."""

    bone: int
    radius: float
    set: int
    shape: Shape = "sphere"
    offset: list[float] | None = None
    to: list[float] | None = None
    flags: int = 0
    label: str = ""

    @property
    def is_capsule(self) -> bool:
        return self.shape == "capsule"

    @property
    def is_marker(self) -> bool:
        return self.bone in hitzone.MARKER_BONES

    @property
    def is_node_space(self) -> bool:
        """At the attack node's own position rather than on a joint."""
        return self.bone in (NODE_CAPSULE, NODE_SPHERE)


@dataclass
class Attack:
    """The levers set on attack record `id`; None keeps the host's byte."""

    id: int
    power: int | None = None
    element: int | None = None
    volume: int | None = None
    """The volume set the record's node walks."""
    label: str = ""

    @property
    def is_empty(self) -> bool:
        return self.power is None and self.element is None and self.volume is None


@dataclass
class Effect:
    """MHFU effect `id` spawned at `bone` on `frame` of `move`."""

    move: str
    frame: int
    id: int
    bone: int
    label: str = ""


@dataclass
class Manifest:
    """One port. Clips, moves and parts are keyed by name."""

    port: Port
    source: Source
    build: Build = field(default_factory=Build)
    clips: dict[str, Clip] = field(default_factory=dict)
    moves: dict[str, Move] = field(default_factory=dict)
    parts: dict[str, Part] = field(default_factory=dict)
    hurtboxes: list[Hurtbox] = field(default_factory=list, metadata=_toml("hurtbox"))
    hitzones: list[HitzoneState] = field(default_factory=list, metadata=_toml("hitzone"))
    hitboxes: list[Hitbox] = field(default_factory=list, metadata=_toml("hitbox"))
    attacks: list[Attack] = field(default_factory=list, metadata=_toml("attack"))
    effects: list[Effect] = field(default_factory=list, metadata=_toml("effect"))
    behaviour: Behaviour = field(default_factory=Behaviour)
    path: Path | None = field(default=None, compare=False, repr=False, metadata=_toml(""))
    """Where it was loaded from; not part of its identity."""

    def rename_clip(self, old: str, new: str) -> None:
        """Rename a clip in place, and every move that plays it."""
        if old not in self.clips:
            raise ManifestError(f"no clip {old!r}")
        if new in self.clips:
            raise ManifestError(f"clip {new!r} already exists")
        self.clips = {new if k == old else k: c for k, c in self.clips.items()}
        for mv in self.moves.values():
            if mv.clip == old:
                mv.clip = new


# reading


T = TypeVar("T", bound="DataclassInstance")


def _key(f: dataclasses.Field[Any]) -> str:
    """The field's TOML key; empty for a field that is not stored."""
    key: str = f.metadata.get("toml", f.name)
    return key


@functools.cache
def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def _fail(where: str, want: str, v: object) -> ManifestError:
    return ManifestError(f"{where}: expected {want}, got {v!r}")


def _claim_shorthand(v: object) -> object:
    """`claim = 1` and `main = 1` spell a single main."""
    if isinstance(v, int) and not isinstance(v, bool):
        v = {"main": v}
    if isinstance(v, dict) and isinstance(v.get("main"), int) and not isinstance(v["main"], bool):
        v = {**v, "main": [v["main"]]}
    return v


def _value(tp: Any, v: object, where: str) -> Any:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        (tp,) = [a for a in args if a is not type(None)]
        return _value(tp, v, where)
    if tp is Claim:
        v = _claim_shorthand(v)
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        return _read(tp, v, where)
    if origin is Literal:
        if v not in args:
            raise _fail(where, "one of " + ", ".join(map(repr, args)), v)
        return v
    if origin is list:
        if not isinstance(v, list):
            raise _fail(where, "a list", v)
        return [_value(args[0], x, f"{where}[{i}]") for i, x in enumerate(v)]
    if origin is tuple:
        if not isinstance(v, list) or len(v) != len(args):
            raise _fail(where, f"{len(args)} values", v)
        pairs = enumerate(zip(args, v, strict=True))
        return tuple(_value(a, x, f"{where}[{i}]") for i, (a, x) in pairs)
    if origin is dict:
        if not isinstance(v, dict):
            raise _fail(where, "a table", v)
        return {k: _value(args[1], x, f"{where}.{k}") for k, x in v.items()}
    if tp is float and isinstance(v, int | float) and not isinstance(v, bool):
        return float(v)
    if tp is int and isinstance(v, bool):
        raise _fail(where, "an integer", v)
    if not isinstance(v, tp):
        raise _fail(where, {int: "an integer", float: "a number"}.get(tp, tp.__name__), v)
    return v


def _is_flat(f: dataclasses.Field[Any]) -> bool:
    """Whether the dict field's keys sit flat beside the other fields' (`Block.params`)."""
    return bool(f.metadata.get("flat"))


def _flat(cls: type) -> dataclasses.Field[Any] | None:
    return next((f for f in dataclasses.fields(cls) if _is_flat(f)), None)


def _read(cls: type[T], raw: object, where: str) -> T:
    if not isinstance(raw, dict):
        raise _fail(where, "a table", raw)
    flat = _flat(cls)
    known = {_key(f): f for f in dataclasses.fields(cls) if _key(f) and f is not flat}
    unknown = sorted(set(raw) - set(known))
    if unknown and flat is None:
        raise ManifestError(f"{where}: unknown key(s) {', '.join(unknown)}")
    kwargs = {}
    if flat is not None:
        kwargs[flat.name] = {k: v for k, v in raw.items() if k not in known}
    hints = _hints(cls)
    for key, f in known.items():
        sub = f"{where}.{key}" if where else key
        if key in raw:
            kwargs[f.name] = _value(hints[f.name], raw[key], sub)
        elif f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            raise ManifestError(f"{sub}: missing")
    return cls(**kwargs)


def _vec3(v: list[float] | None, where: str) -> None:
    _need(v is None or len(v) == 3, where, "needs x, y, z")


def _own_move(mv: Move, w: str) -> None:
    _need(mv.claim is None and mv.hold_max is None, w, "an own move has no claim or hold_max")
    _need(len(mv.attacks) <= MOVE_ATTACKS, w, f"the move player holds {MOVE_ATTACKS} attacks")
    for i, a in enumerate(mv.attacks):
        _need(a.id >= 0 and a.frame >= 0, f"{w}.attack[{i}]", "id and frame are 0 or more")
        _need(a.end is None or a.end > a.frame, f"{w}.attack[{i}]", "end is after frame")
    _need(mv.length is None or mv.length >= 1, w, "length is at least 1 AI frame")
    _need(mv.carrier is None or mv.carrier[0] in MAIN_STATES, w, "carrier main is a main state")
    s = mv.steer
    fixed = s.turn == "fixed"
    _need(fixed == (s.angle is not None), f"{w}.steer", "angle goes with turn = fixed")
    _need(fixed == (s.frames is not None), f"{w}.steer", "frames goes with turn = fixed")
    _need(s.frames is None or 1 <= s.frames <= 0x7FFF, f"{w}.steer", "frames is 1..32767")
    _need(s.angle is None or abs(s.angle) < 360, f"{w}.steer", "angle is under one turn")
    _need(s.rate is None or 1 <= s.rate <= 0xFFFF, f"{w}.steer", "rate is 1..65535")


def _validate(m: Manifest) -> None:
    """The rules the types cannot say: bounds and cross-references."""
    slots: dict[int, str] = {}
    sources: dict[int, str] = {}
    for name, c in m.clips.items():
        w = f"clips.{name}"
        _need(c.slot is not None or c.source is not None, w, "needs a source or a slot")
        if c.slot is not None:
            _need(c.slot not in slots, w, f"slot {c.slot} is clips.{slots.get(c.slot)}")
            slots[c.slot] = name
        cid = c.id
        _need(cid >= 0, w, "source is 0 or more")
        if c.start is not None:
            _need(c.slot is not None and c.frames is not None, w, "start needs slot and frames")
            _need(c.start >= 0, w, "start is 0 or more")
            _need(c.frames is not None and c.frames >= 1, w, "frames is at least 1")
        held = sources.get(cid)
        cuts = held is not None and c.start is not None and m.clips[held].start is not None
        _need(held is None or cuts, w, f"clip {cid} is placed by clips.{held} too")
        sources[cid] = name
    claimed: dict[tuple[int, int | None], str] = {}
    for name, mv in m.moves.items():
        w = f"moves.{name}"
        _need(mv.clip is not None or mv.anim is not None, w, "needs clip or anim")
        _need(mv.clip is None or mv.clip in m.clips, w, f"clip {mv.clip!r} is not in clips")
        _need(mv.after is None or mv.after in m.moves, w, f"after {mv.after!r} is not in moves")
        _need(mv.after != name, w, "after names the move itself")
        _need(mv.hold_max is None or mv.hold_max >= 1, w, "hold_max is at least 1 tick")
        _need((mv.main is None) == (mv.sub is None), w, "needs both main and sub, or neither")
        if mv.own:
            _own_move(mv, w)
        else:
            plain = Move(mv.main, mv.sub)
            _need(
                (mv.attacks, mv.steer, mv.length, mv.carrier, mv.host_attacks)
                == (plain.attacks, plain.steer, plain.length, plain.carrier, plain.host_attacks),
                w,
                "attack, steer, length, carrier and host_attacks are for an own move",
            )
        if mv.claim is not None:
            _need(bool(mv.claim.mains), w, "claim needs a main")
            for k in mv.claim.mains:
                _need(k in MAIN_STATES, w, f"claim main {k} is not a main state")
                key = (k, mv.claim.sub)
                _need(key not in claimed, w, f"claims main {k} as moves.{claimed.get(key)} does")
                claimed[key] = name
    for name, p in m.parts.items():
        _need(p.index in PARTS, f"parts.{name}", f"index {p.index} is not a part")
        _need(p.hitzone_row in (None, *ROWS), f"parts.{name}", "hitzone_row is not a row")
    for i, h in enumerate(m.hurtboxes):
        w = f"hurtbox[{i}]"
        _need(h.part in (None, *PARTS), w, f"part {h.part} is not a part")
        _need(h.hitzone_row in (None, *ROWS), w, f"hitzone_row {h.hitzone_row} is not a row")
        _vec3(h.offset, w + ".offset")
        _vec3(h.to, w + ".to")
    for i, hz in enumerate(m.hitzones):
        w = f"hitzone[{i}]"
        _need(len(hz.rows) == len(ROWS), w, f"needs {len(ROWS)} rows")
        for j, row in enumerate(hz.rows):
            _need(len(row) == hitzone.GRID_COLS, f"{w}.rows[{j}]", ", ".join(hitzone.COLUMNS))
            _need(all(v in BYTE for v in row), f"{w}.rows[{j}]", "a percentage is 0..255")
    for i, hb in enumerate(m.hitboxes):
        _need(hb.set >= 0, f"hitbox[{i}]", "set is 0 or more")
        _vec3(hb.offset, f"hitbox[{i}].offset")
        _vec3(hb.to, f"hitbox[{i}].to")
    ids: set[int] = set()
    for i, a in enumerate(m.attacks):
        w = f"attack[{i}]"
        _need(a.id >= 0, w, "id is 0 or more")
        _need(a.id not in ids, w, f"record {a.id} is declared twice")
        ids.add(a.id)
        for lever in (a.power, a.element, a.volume):
            _need(lever in (None, *BYTE), w, f"{lever} is not a byte")
    for i, e in enumerate(m.effects):
        _need(e.move in m.moves, f"effect[{i}]", f"move {e.move!r} is not in moves")
    own = sum(mv.own for mv in m.moves.values())
    _need(own <= OWN_MOVES, "moves", f"{own} own moves, the framework holds {OWN_MOVES}")
    validate(m)


def loads(text: str, path: str | Path | None = None) -> Manifest:
    """Parse and validate a manifest; `path` only labels errors and `Manifest.path`."""
    try:
        raw = tomllib.loads(text)
        schema = raw.pop("schema", LEGACY_SCHEMA if "rule" in raw else SCHEMA)
        old = schema == LEGACY_SCHEMA
        _need(old or schema == SCHEMA, "schema", f"{schema!r} is not {LEGACY_SCHEMA} or {SCHEMA}")
        legacy = raw.pop("rule", []) if old else []
        _need(not old or "behaviour" not in raw, "behaviour", f"is for schema {SCHEMA}")
        m = _read(Manifest, raw, "")
        if old:
            rules = _value(list[Rule], legacy, "rule")
            m.behaviour = migrate([typing.cast(dict[str, Any], _plain(r)) for r in rules])
        _validate(m)
    except tomllib.TOMLDecodeError as e:
        raise ManifestError(f"{path or '<string>'}: {e}") from e
    except ManifestError as e:
        raise ManifestError(f"{path}: {e}" if path else str(e)) from None
    m.path = Path(path) if path else None
    return m


def load(path: str | Path) -> Manifest:
    return loads(Path(path).read_text(encoding="utf-8"), path)


# writing


def _is_default(f: dataclasses.Field[Any], v: object) -> bool:
    if f.default is not dataclasses.MISSING:
        return bool(v == f.default)
    if f.default_factory is not dataclasses.MISSING:
        return bool(v == f.default_factory())
    return False


def _plain(v: object) -> object:
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        out: dict[str, Any] = {}
        for f in dataclasses.fields(v):
            x = getattr(v, f.name)
            if _is_flat(f):
                out |= typing.cast(dict[str, Any], _plain(x))
            elif _key(f) and not _is_default(f, x):
                out[_key(f)] = _plain(x)
        return out
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_plain(x) for x in v]
    return v


def dumps(m: Manifest) -> str:
    """The manifest as TOML, defaults left out."""
    body = typing.cast(dict[str, Any], _plain(m))
    return tomli_w.dumps({"schema": SCHEMA, **body})


def check(m: Manifest) -> None:
    """Raise `ManifestError` unless `m` would load back as it is."""
    try:
        back = loads(dumps(m))
    except TypeError as e:
        raise ManifestError(str(e)) from e
    _need(back == m, "manifest", "does not survive a round trip; check its types")


def save(m: Manifest, path: str | Path | None = None) -> Path:
    """Write `m` to `path`, by default where it was loaded from, after `check`."""
    target = Path(path) if path is not None else m.path
    if target is None:
        raise ManifestError("the manifest has no path to save to")
    check(m)
    target.write_text(dumps(m), encoding="utf-8")
    return target


def discover(root: str | Path) -> list[Manifest]:
    """Every `*.toml` in `root`, by file name."""
    return [load(p) for p in sorted(Path(root).glob("*.toml"))]
