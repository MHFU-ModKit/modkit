# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What an MHP3rd big monster's pair does to the hunter: its hit block and, per pair, the clips it
plays, the attack nodes it spawns with their frames, and how it turns.

    ms = Moveset(extracted.overlay(slot.file))
    hits = Hits.read(ms.ovl, slot.addr)         # the species' attack records and volume sets
    plan = Walk(ms, hits, lengths).plan(ms.pairs[7, 13])
    plan.steps[0].clip, plan.steps[0].hits      # 61, the nodes with their active frames

A record (28 bytes) says when a node is active: START, a clip frame, and LEN, in clip frames,
against the clip cursor when a handler spawns it; its volume set says where. A handler is a phase
machine on one entity byte that plays clips, spawns, kills and turns behind tests of the clip
cursor (`CURSOR_GATES`) and of the clip's end. `Walk` runs it an AI frame at a time on a clip
cursor it keeps, answering those tests itself and reading what `Walker` records; where the code
branches on something it cannot know (a bearing, a counter, a wait) the plan is the path, of those
tried, that spawns the most attacks and plays the fewest clips.

A walk can also carry entity cells from one AI frame to the next (`track`, read off the stores it
meets, a counter or a gauge a handler keeps), start them at known values (`facts`), and answer the
float tests of the monster's HP ratio against a constant (`hp`); `Walk.settle` runs a pair so and
returns the cells where it ends.

It keeps a `p3motion.Motion` too, the cells a handler moves the monster by (position, velocity,
acceleration, the floor), which the float loads, stores and helpers of the code act on, so the
tests of the clip cursor and of the height come out as they do in the game. Each step carries what
that motion came to in its own frame: `travel`, `lift`, `lunge` and `spreads`.

A handler that reads the bearing of its target (`ANGLE`, the side rule `SIDE`) has the walk answer
it for a bearing `D` (a `plan` with `bearing`): the target stands D YAW units to the left of the
monster's facing. The walk then also carries every entity cell the handler stores from one AI
frame to the next, so its phases, flags and yaw come out as they do in the game, and records the
yaw the handler's code adds (`Step.code`). `arms` walks such a handler at 45 degree steps of D,
finds each change of plan to 1/256 of a turn (the script's arc resolution) and gives the arcs
that play one plan alike: `Arm`.
"""

from __future__ import annotations

import bisect
import math
import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, NamedTuple

import rabbitizer
from mhfu.em.moveset import Cell, Decide, Site, Walker, known
from mhfu.files import Extracted
from mhfu.memory import Unmapped
from mhfu.mips import Code, Register
from mhfu.overlay import Overlay
from rabbitizer import InstrId, Instruction

from . import motion, p3motion
from . import p3moveset as p3
from .brain.p3 import Species
from .brain.splice import GLOBAL_540
from .p3motion import Lunge, Motion, Pose, Spread
from .p3moveset import Moveset, Pair, Pairs
from .travel import KEY_STEP

DESCRIPTOR_HITS = 0xA4
"""A species descriptor's pointer to its hit block."""
HIT_SETS, HIT_RECORDS = 0x5C, 0x60
"""The hit block's pointers to the table of volume sets and to the record array, which ends where
that table starts."""
RECORD = struct.Struct("<hhhB")
"""A record's START, LEN, POWER and KIND."""
RECORD_SIZE = 0x1C
RECORD_TAG, RECORD_SET = 0x09, 0x11
VOLUME = struct.Struct("<HhHxxI4f3f")
"""A volume: bone, second bone, shape, flags, radius, then its offset and far end (x, y, z)."""
VOLUME_SIZE = 0x28
END_OF_SET = 0xFFFF
MAX_SET = 32
SHAPES = ("sphere", "capsule", "capsule2", "box")

PHASE = 0x5D
"""The entity byte a handler's phase machine switches on."""
STORES: dict[rabbitizer.Enum, int] = {
    InstrId.cpu_sb: 1,
    InstrId.cpu_sh: 2,
    InstrId.cpu_sw: 4,
}
"""The stores a tracked cell is probed at, and their widths."""
COMPARES = {
    InstrId.cpu_c_lt_s: lambda x, y: x < y,
    InstrId.cpu_c_le_s: lambda x, y: x <= y,
    InstrId.cpu_c_eq_s: lambda x, y: x == y,
}
"""The float compares `Walk.hp` answers, as the test of their two operands."""
SCAN = 16
"""Instructions back from a float compare the constant it tests is searched for."""
HOOK = 0x90
"""The species vtable slot called every frame: the attacks a clip spawns on its own."""
KILL = 0x09C94AFC  # noaddr: (entity, slot) ends the attack node in that slot
CURSOR_GATES = (0x09C9DF94, 0x09C9E000)  # noaddr: (entity, mode, $f12, $f13) tests the clip cursor
CLIP_ENDED = 0x09CA6730  # noaddr: (entity) 1 once the playing clip has run out
CLIP_ID = 0x09CA699C  # noaddr: (entity) the clip playing now
CURSOR_STEP = 0x09C9DF10  # noaddr: () $f0 the clip cursor's step this frame
TURN = 0x09CA12F4  # noaddr: (entity, rate) turns toward the target by at most rate YAW units
MOTION = (*sorted(p3motion.TARGETS), 0x0889F098)  # noaddr: a curve table read at the cursor
"""Calls that move the monster by code rather than by the clip."""
GLOBALS = 0x09DA9600  # noaddr: a pointer to the global object a handler tests a word of
GLOBAL_WORD = 0x540
"""That word's offset: the handlers compare it with `GLOBAL_540` as the script does."""
ANGLE = 0x08876E84  # noaddr: (from, to) the bearing of a point from another, in YAW units
SIDE = 0x09DB41B8  # noaddr: (entity) 1 when the target's bearing minus the yaw is 0 to a half turn
CARRY = 0x09DB426C  # noaddr: (entity, main, sub, blend) hands over to a pair, the blend carried
BLEND_IN = 0x09CA67D8  # noaddr: (entity) 1 while the playing clip is blending in
YAW = (0x74, 4)
"""The entity's yaw, as a cell."""
HALF = 0x8000
"""YAW units in half a turn; bit 15 of a bearing minus the yaw is the side."""
BUCKETS = 256
"""The script's arc resolution: a turn in 256ths. Bucket `k` is judged at bearing `256 k + 1`."""
SAMPLE = 32
"""Buckets between the bearings a handler is first walked at: 45 degrees."""
MIN_CODE = 16
"""YAW units a code turn comes to under which it is left out."""
STEP_FRAMES = 2.0
"""Clip frames the cursor advances in an AI frame at normal speed."""
MIN_TRAVEL, MIN_LIFT = 0.5, 1.0
"""World units under which a step's travel, or its height over rest, is not kept."""
MAX_TICKS = 3000
MAX_STEPS = 24
MAX_RUNS = 32
MAX_MOVED = 400
"""Bounds on one simulation (AI frames, clips played), on the runs of a pair with their
branches going the other way, and on the AI frames of a run that move the monster by code, which
cannot be skipped over."""
FALL, TAKEN, LOOPS, EXITS = 0, 1, 2, 3
"""How a branch the walk cannot decide goes: falls through, is taken, is taken until the clip ends
and then falls through (a wait), or falls through until the clip ends and then is taken."""
BRANCHES = {
    TAKEN: "is taken",
    LOOPS: "is taken until the clip ends",
    EXITS: "is taken once the clip ends",
}
ARGS = p3motion.ARGS
MODE, F12, F13 = p3motion.A1, p3motion.F12, p3motion.F13
"""Where a cursor gate's mode and its two frames sit in `ARGS`."""
WINDOWS = frozenset([3, 4, 5, 7])
"""The cursor gate modes that test a window of frames; the others test one frame."""
NEUTRAL = frozenset(["turn", "motion"])
"""What a tick can do without changing what the next does."""

FLAG_OUTLIVES, FLAG_REFRESH, FLAG_OPENS, FLAG_JOINS = 1, 2, 8, 16
"""A node's spawn flags: it outlives a clip change; its life is refreshed until it is killed; it
opens a hit group; it joins one (a hit lands once between the nodes of a group)."""

WATCH = {
    **dict.fromkeys(p3.PLAYERS, "play"),
    **{t: f"record:{t:x}" for t in p3.RECORDS},
    **dict.fromkeys(p3.REACTIONS, "react"),
    **dict.fromkeys(p3.ENTER, "enter"),
    **{t: f"end:{t:x}" for t in p3.END},
    p3.ATTACK: "spawn",
    p3.ATTACK0: "spawn0",
    KILL: "kill",
    TURN: "turn",
    SIDE: "side",
    CARRY: "carry",
    **dict.fromkeys(MOTION, "motion"),
}
"""What the walk records, by call target; a kind that needs its target carries it after a colon."""
SPAWNERS = {p3.ATTACK: "spawn", p3.ATTACK0: "spawn0", KILL: "kill"}
HANDLED = frozenset([*p3motion.TARGETS, p3motion.CURSOR, p3motion.CLIP_END, p3motion.SCALE])
"""The calls whose effect or answer the walk's motion supplies."""
STARTERS = frozenset(["play", "record", "react"])
"""The kinds of call that start a clip."""


class Record(NamedTuple):
    """One attack record of a species."""

    id: int
    start: int
    """The clip frame it turns active (against the cursor at spawn); 0 or less: at once."""
    length: int
    """How many clip frames it stays active."""
    power: int
    kind: int
    tag: int
    volumes: int
    """Its volume set's index."""


class Volume(NamedTuple):
    """One sphere, capsule or box of a volume set, on its bone's axes."""

    bone: int
    bone2: int
    shape: str
    flags: int
    radius: float
    offset: tuple[float, float, float]
    to: tuple[float, float, float]


@dataclass(frozen=True)
class Hits:
    """A species' attack records and the volume sets they index."""

    records: tuple[Record, ...]
    sets: tuple[tuple[Volume, ...], ...]

    @classmethod
    def read(cls, ovl: Overlay, descriptor: int) -> Hits:
        """The hit block of the species whose descriptor is at `descriptor`; ValueError where it
        has none or the block is not laid out as expected."""
        hits = ovl.u32(descriptor + DESCRIPTOR_HITS)
        if hits not in ovl.initialised:
            raise ValueError(f"no hit block at descriptor 0x{descriptor:08X}")
        sets_at, records_at = ovl.u32(hits + HIT_SETS), ovl.u32(hits + HIT_RECORDS)
        if not (records_at in ovl.initialised and records_at <= sets_at):
            raise ValueError(f"the hit block at 0x{hits:08X} has no record array")
        records = []
        for i in range((sets_at - records_at) // RECORD_SIZE):
            raw = ovl.read(records_at + i * RECORD_SIZE, RECORD_SIZE)
            start, length, power, kind = RECORD.unpack_from(raw)
            records.append(Record(i, start, length, power, kind, raw[RECORD_TAG], raw[RECORD_SET]))
        needed = max((r.volumes for r in records), default=-1) + 1
        sets: list[tuple[Volume, ...]] = []
        for at in range(sets_at, sets_at + 4 * needed, 4):
            if at not in ovl.initialised or (va := ovl.u32(at)) not in ovl.initialised:
                raise ValueError(f"the hit block at 0x{hits:08X} lacks volume set {len(sets)}")
            sets.append(_volumes(ovl, va))
        return cls(tuple(records), tuple(sets))

    def volumes(self, record: int) -> tuple[Volume, ...]:
        """The volumes the record's node walks; none for a record or set the block lacks."""
        if not 0 <= record < len(self.records):
            return ()
        i = self.records[record].volumes
        return self.sets[i] if i < len(self.sets) else ()


def _volumes(ovl: Overlay, va: int) -> tuple[Volume, ...]:
    out = []
    for _ in range(MAX_SET):
        if ovl.u16(va) == END_OF_SET:
            break
        bone, bone2, shape, flags, radius, *r = VOLUME.unpack(ovl.read(va, VOLUME.size))
        name = SHAPES[shape] if shape < len(SHAPES) else str(shape)
        out.append(Volume(bone, bone2, name, flags, radius, (r[0], r[1], r[2]), (r[3], r[4], r[5])))
        va += VOLUME_SIZE
    return tuple(out)


def window(record: Record, cursor: float, flags: int = 0) -> tuple[float, float | None] | None:
    """The clip frames a node spawned at `cursor` is active in: `(from, to)`, `to` None while it
    is refreshed until killed; None where it is over before it starts."""
    if record.start > 0:
        over = record.start + record.length
        if over <= cursor:
            return None
        start, end = max(float(record.start), cursor), float(over)
    else:
        start, end = cursor, cursor + record.length
    return start, None if flags & FLAG_REFRESH else end


class Length(NamedTuple):
    """A clip as the walk needs it."""

    frames: float
    loop: bool = False


How = Literal["clip", "cursor", "pair"]
"""What ended a step: its clip ran out, the cursor reached a frame the handler tests, or the pair
handed over."""


@dataclass(frozen=True)
class Hit:
    """An attack node on one clip of a step."""

    slot: int
    record: int
    flags: int
    frame: float
    """The clip frame it turns active; for a node carried over from the clip before: 0."""
    end: float | None
    """The frame it ends; None: with the step."""
    carried: bool = False
    """It was spawned on an earlier clip and outlives it."""


@dataclass(frozen=True)
class Turn:
    """A stretch of AI frames the handler turned the monster toward its target."""

    rate: int | None
    """YAW units an AI frame; None where the walk could not work it out."""
    window: tuple[float, float]
    """The cursor frames it turned in: the window of the gate that let it through, else the
    cursor at the first of those AI frames and at the end of the last."""


@dataclass(frozen=True)
class Step:
    """One clip a pair plays."""

    clip: int
    blend: int | None
    start: float
    """The frame it starts from."""
    ended: How
    ticks: int
    """AI frames it played, from the first after the play to the one that ended it."""
    hits: tuple[Hit, ...]
    turns: tuple[Turn, ...]
    stop: float | None = None
    """The clip frame the cursor test that ended it was crossed at; None when its clip or the
    pair ended it, or no test of the cursor changed its answer on the tick."""
    travel: tuple[tuple[float, float], ...] = ()
    """The monster's travel by code, cumulative (x, z) at the clip frames `start`, `start + 2`,
    ... in its frame at the first playing frame; the first is (0, 0). Empty: none."""
    lift: tuple[float, ...] = ()
    """Its height over rest at each whole clip frame from `start` to the step's last, one more
    than the frames it covers; empty where it stays within `MIN_LIFT` of rest."""
    lunge: Lunge | None = None
    spreads: tuple[Spread, ...] = ()
    """The turns of a fixed total it makes."""
    code: tuple[int, ...] = ()
    """The yaw its code adds, cumulative YAW units at the clip frames `start`, `start + 2`, ...
    (the first is 0): walked at a bearing only. Empty: none."""
    aim: bool = False
    """`code` is the curve for an `aim` turn: its total is the angle its arm ends at, and the
    handler turns exactly the bearing."""
    hand: int | None = None
    """The blend, in AI frames, the pair hands over with, when this is its last step and the
    handler says it."""


@dataclass(frozen=True)
class Note:
    """What a plan cannot say."""

    code: Literal["branch", "motion", "unresolved", "limit", "record"]
    text: str


@dataclass(frozen=True)
class Plan:
    """A pair's default path through its handler."""

    pair: Pairs
    steps: tuple[Step, ...]
    leaves: tuple[Pairs, ...]
    """The pairs it hands over to, its end helpers' rest pairs included."""
    complete: bool
    """The handler ended, rather than the walk giving up."""
    notes: tuple[Note, ...] = ()
    alternatives: tuple[tuple[int, ...], ...] = ()
    """The clips of the other paths its branches could take."""
    reads: bool = False
    """A run asked for the bearing of the target (`ANGLE`, `SIDE`)."""
    bearing: int | None = None
    """The bearing it was walked at; None: not at one."""


class Arm(NamedTuple):
    """The bearings a handler plays one plan at."""

    arcs: tuple[tuple[int, int], ...]
    """Runs of buckets (lo, hi), 256ths of a turn to the left of the facing, both ends in."""
    plan: Plan
    """The plan, walked at `plan.bearing`."""


@dataclass(frozen=True)
class Settled:
    """Where a handler run for `Walk.settle` ended."""

    cells: Mapping[Cell, int]
    """The tracked cells."""
    left: bool
    """The handler handed over: it ended, rather than the walk giving up."""
    ticks: int
    """AI frames it ran."""
    undecided: tuple[int, ...]
    """The branches it met and could not decide, which fell through."""


class _Query(NamedTuple):
    """A test of the clip cursor or of the clip's end the handler made, to ask again later."""

    mode: int | None
    """The cursor gate's mode; None for the clip's end."""
    lo: float = 0.0
    hi: float = 0.0


@dataclass
class _Node:
    """A live attack node: the hit it makes and the clip frames it has left."""

    hit: Hit
    left: float | None
    """None while refreshed until killed."""


@dataclass
class _Step:
    clip: int
    blend: int | None
    start: float
    ticks: int = 0
    hits: list[Hit] = field(default_factory=list)
    turns: list[tuple[int | None, float, float, tuple[float, float] | None]] = field(
        default_factory=list
    )
    origin: Pose = Pose(0.0, 0.0, 0.0)
    """Where the monster was when it began."""
    elapsed: float = 0.0
    """Clip frames it has played, a loop's wraps counted."""
    trace: dict[float, Pose] = field(default_factory=dict)
    """The position after each AI frame by `elapsed`, x and z from `origin`, y over rest."""
    lunge: Lunge | None = None
    spreads: list[Spread] = field(default_factory=list)
    yaw: int | None = None
    """The yaw cell as the last AI frame left it; None: not followed."""
    turned: int = 0
    """The YAW units the code added since the step began."""
    yaws: dict[float, int] = field(default_factory=dict)
    """`turned` after each AI frame by `elapsed`."""


@dataclass
class _Run:
    """One simulation: the clip cursor, the phase and what the handler did."""

    vector: dict[int, int]
    """How each branch the walk cannot decide goes: FALL, TAKEN, LOOPS or EXITS."""
    kin: Motion
    """The kinematic cells of the run."""
    trail: list[int] = field(default_factory=list)
    """Those branches, in the order the run met them."""
    steps: list[_Step] = field(default_factory=list)
    done: list[Step] = field(default_factory=list)
    nodes: dict[int, _Node] = field(default_factory=dict)
    phase: int = 0
    clip: int | None = None
    cursor: float = 0.0
    ticks: int = 0
    leaves: tuple[Pairs, ...] = ()
    finished: bool = False
    """The run stopped, for whatever reason."""
    left: bool = False
    """The handler handed over: the run reached the end of the pair."""
    notes: list[Note] = field(default_factory=list)
    asked: list[tuple[_Query, bool]] = field(default_factory=list)
    """This tick's tests and their answers."""
    ended: bool = False
    """The clip-ended test answered yes this tick."""
    crossed: list[float] = field(default_factory=list)
    """The frames of the one-frame cursor gates that answered yes this tick."""
    windows: list[tuple[float, float]] = field(default_factory=list)
    """The windows of the cursor gates that answered yes this tick."""
    played: bool = False
    """A clip started this tick."""
    cells: dict[Cell, int] = field(default_factory=dict)
    """The tracked entity cells, as the handler's stores left them."""
    hp: float | None = None
    """The HP ratio the float tests of it are answered for."""
    moved: bool = False
    moving: int = 0
    """The AI frames so far that were."""
    """A helper acted on, or a read of, the motion this tick: it cannot be skipped."""
    plays: int = 0
    """The clip starts this tick's code has made so far."""
    marks: dict[int, Pose] = field(default_factory=dict)
    """Where the monster was at each clip start of this tick, by call site."""
    events: list[tuple[int, Lunge | Spread]] = field(default_factory=list)
    """What the helpers asked for this tick, with the clip starts that came before it."""
    bearing: int | None = None
    """Where the target is, YAW units to the left of the facing the run began with; None: not
    known."""
    read: bool = False
    """The handler asked for the bearing."""
    blend_in: int = 0
    """AI frames of the playing clip's blend that are left."""
    hand: int | None = None
    """The blend the pair handed over with."""

    @property
    def current(self) -> _Step | None:
        return self.steps[-1] if self.steps else None


class Walk:
    """The pairs of one MHP3rd overlay, read by running their handlers."""

    def __init__(
        self,
        ms: Moveset,
        hits: Hits,
        lengths: Mapping[int, Length],
        step: float = STEP_FRAMES,
        *,
        track: Sequence[Cell] = (),
        facts: Mapping[Cell, int] | None = None,
        rest: float = p3motion.REST,
    ) -> None:
        """`track` are entity cells (offset, width) whose value the walk carries from one AI frame
        to the next, read off the stores it meets; `facts` the values cells hold when a run starts,
        a tracked one's start or a constant; `rest` the height over the floor the monster stands
        at, which the landing tests of a jump are answered from."""
        self.ms, self.hits, self.lengths, self.step = ms, hits, lengths, step
        self.facts, self.rest = dict(facts or {}), rest
        stores = {
            i.vram: i.rt
            for i in ms.code
            if i.uniqueId == InstrId.cpu_sb and i.getProcessedImmediate() == PHASE
        }
        self._cells: dict[int, Cell] = {}
        for i in ms.code:
            width = STORES.get(i.uniqueId)
            if width is not None and (i.getProcessedImmediate(), width) in track:
                self._cells[i.vram], stores[i.vram] = (i.getProcessedImmediate(), width), i.rt
        self._walker = Walker(
            ms.code,
            WATCH,
            depth=3,
            guards=False,
            args=ARGS,
            pointers={GLOBALS: {GLOBAL_WORD: GLOBAL_540}},
            probes=stores,
            returns={CURSOR_STEP: step},
            max_paths=512,
        )
        self._armed: dict[int | None, list[tuple[tuple[object, ...], tuple[Arm, ...]]]] = {}
        self._hook = self._hook_function()
        self._hook_walker = Walker(
            ms.code,
            SPAWNERS,
            follow=_wrappers(ms.code),
            depth=1,
            guards=False,
            args=ARGS,
            max_paths=512,
        )

    @classmethod
    def of(
        cls, extracted: Extracted, monster: int, anim: bytes, slot: int = 0, **options: Any
    ) -> Walk:
        """The walk of MHP3rd monster `monster`'s overlay in `slot`, with the clips of its
        animation file `anim` (the lengths its handlers' tests are answered from); `options` are
        `__init__`'s `track` and `facts`."""
        found = Species.read(extracted).slots.get(monster, ())
        if slot >= len(found):
            raise ValueError(f"monster {monster} has {len(found)} slots, not {slot + 1}")
        ms = Moveset(extracted.overlay(found[slot].file))
        clips = motion.moveset(anim)
        lengths = {i: Length(motion.frames(c), bool(c.loop)) for i, c in clips.items()}
        return cls(ms, Hits.read(ms.ovl, found[slot].addr), lengths, **options)

    def _hook_function(self) -> int | None:
        vt = self.ms.vtable
        if vt is None:
            return None
        fn = self.ms.ovl.u32(vt + HOOK)
        return fn if fn in self.ms.code.text else None

    # --- the plan ---

    def plan(self, pair: Pair, bearing: int | None = None) -> Plan:
        """The default path through `pair`'s handler. Every branch the walk cannot decide falls
        through; then each is tried alone another way (`BRANCHES`). The outcome that spawns the
        most attacks wins, then the one that plays the fewest clips, then the first tried.

        With a `bearing` (YAW units, the target to the left of the facing) the handler's reads of
        the target's bearing are answered, and what it stores carried from frame to frame."""
        at = (pair.main, pair.sub)
        if pair.handler is None or self.ms.tick_entry is None:
            return Plan(at, (), (), False, (Note("unresolved", "it has no handler of its own"),))
        first = self._run(pair, {}, bearing=bearing)
        runs = [first]
        for how in BRANCHES:
            for pc in first.trail:
                if len(runs) < MAX_RUNS:
                    runs.append(self._run(pair, {**first.vector, pc: how}, bearing=bearing))
        best = min(runs, key=lambda r: (not r.left, -_spawns(r), len(r.done)))
        chain = tuple(s.clip for s in best.done)
        others: list[tuple[int, ...]] = []
        for r in runs:
            other = tuple(s.clip for s in r.done)
            if r.left and other != chain and other not in others:
                others.append(other)
        notes = list(best.notes)
        notes += [
            Note("branch", f"the branch at 0x{pc:08X} {BRANCHES[how]}")
            for pc, how in best.vector.items()
            if how != FALL
        ]
        if len(first.trail) * len(BRANCHES) >= MAX_RUNS:
            notes.append(Note("limit", f"only {len(runs) - 1} runs of its branches were tried"))
        reads = any(r.read for r in runs)
        return Plan(
            at,
            tuple(best.done),
            best.leaves,
            best.left,
            tuple(notes),
            tuple(others),
            reads,
            bearing,
        )

    def settle(self, pair: Pair, hp: float | None = None) -> Settled:
        """`pair`'s handler run to its end on the default path, with the float tests of the HP
        ratio against a constant answered for `hp` (the branches it cannot decide fall through):
        the tracked cells where it stopped."""
        run = self._run(pair, {}, hp)
        return Settled(dict(run.cells), run.left, run.ticks, tuple(run.trail))

    # --- the arms ---

    def arms(self, pair: Pair, base: Plan | None = None) -> tuple[Arm, ...]:
        """The plans `pair`'s handler plays by the target's bearing: where it asks for it, the
        handler is walked with the target at every 45 degrees, each change of plan found to a
        bucket (1/256 of a turn, `BUCKETS`) by bisection, and the runs of buckets that play one
        plan are an arm. Plans are compared by their clips, where they start and stop, and where
        they hand over to. A handler that plays one plan everywhere has one arm, its plan walked
        with no bearing (`base`, if the caller has it). An arm's plan is walked at the middle of
        its widest run; where it turns by code, a second bearing in the run tells a turn that goes
        by the bearing (the curve scaled to it) from one that does not.

        Pairs of one handler that differ in what they hand over to alone (the wrappers of a turn,
        each leading to another pair) walk alike at every bearing sampled: they share the arms."""
        base = base if base is not None else self.plan(pair)
        whole = ((0, BUCKETS - 1),)
        if not base.reads or pair.handler is None or self.ms.tick_entry is None:
            return (Arm(whole, base),)
        runs: dict[int, _Run] = {}

        def probe(k: int) -> _Run:
            if k not in runs:
                runs[k] = self._run(pair, {}, bearing=_bearing_of(k))
            return runs[k]

        def sig(k: int) -> tuple[object, ...]:
            return _signature(probe(k))

        samples = [*range(0, BUCKETS, SAMPLE), BUCKETS - 1]
        seen = tuple((tuple(probe(k).done), probe(k).left) for k in samples)
        if len({sig(k) for k in samples}) == 1:
            return (Arm(whole, base),)
        for earlier, held in self._armed.get(pair.handler, []):
            if earlier == seen:
                return tuple(_rehanded(arm, pair, probe, samples) for arm in held)
        edges = [0]
        for a, b in zip(samples, samples[1:], strict=False):
            if sig(a) == sig(b):
                continue
            lo, hi = a, b
            while hi - lo > 1:
                mid = (lo + hi) // 2
                lo, hi = (mid, hi) if sig(mid) == sig(lo) else (lo, mid)
            edges.append(hi)
        spans = [(lo, hi - 1) for lo, hi in zip(edges, [*edges[1:], BUCKETS], strict=True)]
        groups: dict[tuple[object, ...], list[tuple[int, int]]] = {}
        for span in spans:
            groups.setdefault(sig(span[0]), []).append(span)
        planned: dict[tuple[object, ...], Arm] = {}
        for arcs in groups.values():
            lo, hi = max(arcs, key=lambda a: a[1] - a[0])
            at = (lo + hi) // 2
            alt = lo if at - lo >= hi - at else hi
            plan = self.plan(pair, _bearing_of(at))
            aimed = _aimed(plan, (lo, hi), at, alt, probe(alt))
            same = planned.get(_plan_signature(aimed))
            joined = (*(same.arcs if same else ()), *arcs)
            planned[_plan_signature(aimed)] = Arm(
                tuple(sorted(joined)), same.plan if same else aimed
            )
        out = tuple(planned.values())
        if len(out) == 1:
            out = (Arm(whole, base),)
        self._armed.setdefault(pair.handler, []).append((seen, out))
        return out

    def _run(
        self,
        pair: Pair,
        vector: dict[int, int],
        hp: float | None = None,
        bearing: int | None = None,
    ) -> _Run:
        tick = self.ms.tick_entry
        assert tick is not None
        run = _Run(
            dict(vector),
            Motion(self.ms.ovl, self.rest),
            cells=dict(self.facts),
            hp=hp,
            bearing=bearing,
        )
        if bearing is not None:
            run.cells[YAW] = 0
        decide = self._decider(run)
        at = self._dispatched(pair)
        while run.ticks < MAX_TICKS:
            run.ended = run.played = run.moved = False
            run.crossed, run.windows, run.asked = [], [], []
            run.plays, run.marks, run.events = 0, {}, []
            self._begin(run)
            facts = {
                **known({PHASE: run.phase, p3.MAIN: at[0], p3.SUB: at[1]}),
                **run.cells,
            }
            sites = self._walker.run(
                tick, None, facts, decide, run.kin.cells, persist=bearing is not None
            )
            if self._hook is not None and run.clip is not None:
                sites += self._hook_walker.run(self._hook, None, None, decide)
            phase = run.phase
            self._apply(run, pair, sites)
            self._sample(run)
            if run.finished:
                break
            run.moving += run.moved
            if run.moving > MAX_MOVED:
                run.notes.append(Note("limit", f"the handler moved the monster {MAX_MOVED} frames"))
                break
            if run.current is None and not sites:
                run.notes.append(Note("unresolved", "the handler plays no clip"))
                break
            self._advance(run, 1)
            if run.phase == phase and not run.played and not run.moved and _neutral(sites):
                self._skip(run, sites)
        else:
            run.notes.append(Note("limit", f"the handler ran {MAX_TICKS} frames without ending"))
        self._close(run)
        return run

    def _dispatched(self, pair: Pair) -> Pairs:
        """The pair the tick dispatches `pair` as: the one the species translator rewrites it to."""
        to = self.ms.translator.get((pair.main, pair.sub), frozenset())
        return next(iter(to)) if len(to) == 1 else (pair.main, pair.sub)

    # --- the world the walk answers from ---

    def _decider(self, run: _Run) -> Decide:
        def decide(pc: int, target: int | None, args: tuple[int | None, ...]) -> int | float | None:
            if target is None:
                return self._branch(run, pc)
            if target not in HANDLED and not self.ms.code.at(pc).doesLink():
                return None  # a tail call: only the motion is told of those
            if target in CURSOR_GATES:
                return self._gate(run, args)
            if target in (ANGLE, SIDE):
                return self._bearing(run, target)
            if target == BLEND_IN:
                run.moved = run.moved or run.blend_in > 0  # the answer changes: no skipping
                return int(run.blend_in > 0)
            if target == CLIP_ENDED:
                return self._ask(run, _Query(None))
            if target == CLIP_ID:
                return run.clip if run.clip is not None else 0
            if target in HANDLED:
                return self._act(run, target, args)
            kind = WATCH.get(target, "").partition(":")[0]
            if kind in STARTERS:
                run.marks[pc] = run.kin.before_hand_over()
                run.plays += 1
                if kind == "play":
                    run.kin.turn_over()
            return None

        return decide

    def _bearing(self, run: _Run, target: int) -> int | None:
        """The bearing of the target (`ANGLE`: the world's, as it stood when the run began) or its
        side of the yaw (`SIDE`), for the bearing the run is at; None where it is at none."""
        run.read = True
        if run.bearing is None:
            return None
        if target == ANGLE:
            return run.bearing & 0xFFFF
        return int(not (run.bearing - run.cells.get(YAW, 0)) & HALF)

    def _act(self, run: _Run, target: int, args: tuple[int | None, ...]) -> float | None:
        """A helper the motion acts for, or a read of the clip it answers."""
        run.moved = True
        out = run.kin.call(target, args)
        run.events += [(run.plays, e) for e in run.kin.events]
        run.kin.events.clear()
        return out

    def _begin(self, run: _Run) -> None:
        """The motion's view of the clip for the AI frame about to run."""
        length = self.lengths.get(run.clip) if run.clip is not None else None
        cursor = run.cursor if length is not None else None
        run.kin.begin(cursor, None if length is None else float(length.frames), self.step)

    def _sample(self, run: _Run) -> None:
        """Where the AI frame left the monster, for the step it ended in."""
        step = run.current
        if step is not None:
            step.trace[step.elapsed] = _relative(step, run.kin.now(), self.rest)
            if (yaw := run.cells.get(YAW)) is not None:
                if step.yaw is not None:
                    step.turned += _signed16(yaw - step.yaw)
                step.yaw = yaw
                step.yaws[step.elapsed] = step.turned

    def _branch(self, run: _Run, pc: int) -> int:
        if run.hp is not None and (said := self._test_hp(pc, run.hp)) is not None:
            return said
        if pc not in run.vector:
            run.vector[pc] = FALL
            run.trail.append(pc)
        how = run.vector[pc]
        if how in (LOOPS, EXITS):
            over = bool(self._ask(run, _Query(None)))
            return int(over == (how == EXITS))
        return int(how == TAKEN)

    def _gate(self, run: _Run, args: tuple[int | None, ...]) -> int | None:
        mode, f12, f13 = args[MODE], args[F12], args[F13]
        if mode is None or f12 is None or mode > max(WINDOWS):
            return None
        return self._ask(run, _Query(mode, _float(f12), _float(f13) if f13 is not None else 0.0))

    def _test_hp(self, pc: int, hp: float) -> int | None:
        """The outcome of the float-compare branch at `pc` when one operand is a constant loaded
        from the species' data and the other, the HP ratio, is `hp`; None for any other branch."""
        code = self.ms.code
        branch = code.at(pc)
        if branch.uniqueId not in (InstrId.cpu_bc1t, InstrId.cpu_bc1f):
            return None
        back = [code.at(a) for a in range(pc - 4, pc - 4 * SCAN, -4) if a in code.text]
        compare = next((i for i in back if i.uniqueId in COMPARES), None)
        if compare is None:
            return None
        value = {r: self._constant(code, compare, r) for r in (compare.fs, compare.ft)}
        known_ = {r: v for r, v in value.items() if v is not None}
        if len(known_) != 1:
            return None
        x, y = (known_.get(r, hp) for r in (compare.fs, compare.ft))
        holds = COMPARES[compare.uniqueId](x, y)
        return int(holds == (branch.uniqueId == InstrId.cpu_bc1t))

    def _constant(self, code: Code, before: Instruction, reg: Register) -> float | None:
        """The float `reg` holds at `before` when the nearest `lwc1` into it before it loads a
        constant off a base the code knows."""
        for a in range(before.vram - 4, before.vram - 4 * SCAN, -4):
            if a not in code.text:
                return None
            ins = code.at(a)
            if ins.uniqueId == InstrId.cpu_lwc1 and ins.ft == reg:
                base = code.constant(a, ins.rs)
                if base is None:
                    return None
                try:
                    return _float(code.mem.u32((base + ins.getProcessedImmediate()) & 0xFFFFFFFF))
                except Unmapped:
                    return None
            if ins.modifiesFd() and ins.fd == reg or ins.modifiesFt() and ins.ft == reg:
                return None
        return None

    def _ask(self, run: _Run, q: _Query) -> int | None:
        yes = self._answer(q, run.clip, run.cursor)
        if yes is None:
            return None
        run.asked.append((q, yes))
        if q.mode is None:
            run.ended = run.ended or yes
        elif yes and q.mode in WINDOWS:
            run.windows.append((q.lo, q.hi))
        elif yes:
            run.crossed.append(q.lo)
        return int(yes)

    def _answer(self, q: _Query, clip: int | None, cursor: float) -> bool | None:
        """The test's answer with `clip` at `cursor`; None where the clip is unknown."""
        length = self.lengths.get(clip) if clip is not None else None
        if length is None:
            return None
        end, nxt = length.frames, cursor + self.step
        if q.mode is None:
            return nxt >= end
        nxt = min(nxt, end)
        lo, hi = q.lo, q.hi
        return (
            cursor < lo <= nxt,
            nxt >= lo,
            nxt < lo,
            lo <= nxt <= hi,
            lo < nxt <= hi,
            lo <= nxt < hi,
            cursor >= lo,
            lo <= cursor <= hi,
        )[q.mode]

    def _skip(self, run: _Run, sites: Sequence[Site]) -> None:
        """Past the AI frames that would ask the same and so do the same as the one just run."""
        length = self.lengths.get(run.clip) if run.clip is not None else None
        if length is None or run.current is None:
            return
        n, cursor = 0, run.cursor
        while run.ticks + n < MAX_TICKS and all(
            self._answer(q, run.clip, cursor) == yes for q, yes in run.asked
        ):
            n += 1
            cursor += self.step
            if length.loop and cursor >= length.frames:
                cursor -= length.frames
        if n:
            for s in sites:
                if s.kind == "turn":
                    one = run.windows[0] if len(set(run.windows)) == 1 else None
                    last = run.cursor + (n - 1) * self.step
                    run.current.turns.append((s.args[0], last, last + self.step, one))
            self._advance(run, n)

    # --- what the handler did in a tick ---

    def _take(self, run: _Run, after: bool) -> None:
        """What the helpers asked for, before the tick's first clip start or after it."""
        step = run.current
        for plays, event in run.events:
            if step is None or (plays > 0) != after:
                continue
            if isinstance(event, Lunge):
                step.lunge = step.lunge or event
            elif event not in step.spreads:
                step.spreads.append(event)

    def _apply(self, run: _Run, pair: Pair, sites: Sequence[Site]) -> None:
        self._take(run, False)
        self._apply_sites(run, pair, sites)
        self._take(run, True)

    def _apply_sites(self, run: _Run, pair: Pair, sites: Sequence[Site]) -> None:
        for s in sites:
            if run.finished:
                return
            kind, _, target = s.kind.partition(":")
            if kind == "play":
                self._play(run, pair, s)
            elif kind == "record":
                helper = p3.RECORDS[int(target, 16)]
                self._play_first(run, self.ms.descriptors(s.args[helper.arg], helper.offsets), s)
            elif kind == "react":
                self._play_first(run, None if s.args[0] is None else self.ms.steps(s.args[0]), s)
            elif kind in ("spawn", "spawn0"):
                self._spawn(run, s, kind == "spawn")
            elif kind == "kill":
                self._kill(run, s.args[0])
            elif kind == "store":
                off, width, value = s.args
                assert off is not None and width is not None
                for cell in [c for c in run.cells if c[0] < off + width and off < c[0] + c[1]]:
                    del run.cells[cell]
                if value is not None:
                    run.cells[off, width] = value
            elif kind == "probe" and s.site in self._cells:
                cell = self._cells[s.site]
                if s.args[0] is not None:
                    run.cells[cell] = s.args[0] & ((1 << 8 * cell[1]) - 1)
            elif kind == "probe":
                run.phase = s.args[0] if s.args[0] is not None else run.phase
            elif kind == "turn" and run.current is not None:
                one = run.windows[0] if len(set(run.windows)) == 1 else None
                run.current.turns.append((s.args[0], run.cursor, run.cursor + self.step, one))
            elif kind == "motion":
                if not any(n.code == "motion" for n in run.notes):
                    run.notes.append(Note("motion", "the handler moves the monster by code"))
            elif kind == "end":
                self._leave(run, p3.END[int(target, 16)])
            elif kind in ("enter", "carry"):
                a = s.args
                self._leave(run, ((a[0], a[1]),) if a[0] is not None and a[1] is not None else ())
                if kind == "carry":
                    run.hand = a[2]

    def _play(self, run: _Run, pair: Pair, s: Site) -> None:
        clip = s.args[0]
        if clip is None:  # a byte of the constant table the handler copies: one per phase
            ids = [i for i in self.ms.table_ids(s.site, pair.entry) or [] if i]
            clip = ids[min(run.phase, len(ids) - 1)] if ids else None
        if clip is None:
            run.notes.append(Note("unresolved", f"the clip played at 0x{s.site:08X} is computed"))
            run.finished = True
            return
        self._start(run, clip, s.args[1], s.args[2] or 0, s.site)

    def _play_first(self, run: _Run, ids: Sequence[int] | None, s: Site) -> None:
        """A helper that plays one of several clips it is given a record of: the first."""
        if not ids:
            run.notes.append(Note("unresolved", f"the clips played at 0x{s.site:08X} are computed"))
            run.finished = True
            return
        if ids[0] == run.clip:  # such a helper is called every frame and keeps its clip playing
            return
        if len(ids) > 1:
            run.notes.append(Note("record", f"clip {ids[0]} of {', '.join(map(str, ids))}"))
        self._start(run, ids[0], None, 0, s.site)

    def _start(self, run: _Run, clip: int, blend: int | None, start: int, site: int) -> None:
        if clip not in self.lengths:
            run.notes.append(Note("unresolved", f"the donor has no clip {clip}"))
            run.finished = True
            return
        if len(run.done) >= MAX_STEPS:
            run.notes.append(Note("limit", f"the handler played {MAX_STEPS} clips without ending"))
            run.finished = True
            return
        where = run.marks.get(site, run.kin.before_hand_over())
        if run.current is not None:
            run.current.trace[run.current.elapsed] = _relative(run.current, where, self.rest)
        self._close_step(run, "clip" if run.ended else "cursor", self._crossed(run))
        carried = {}
        for slot, node in run.nodes.items():
            if not node.hit.flags & FLAG_OUTLIVES or (node.left is not None and node.left <= 0):
                continue
            end = None if node.left is None else start + node.left
            hit = Hit(slot, node.hit.record, node.hit.flags, float(start), end, True)
            carried[slot] = _Node(hit, node.left)
        run.nodes = carried
        step = _Step(clip, blend, float(start), origin=where, yaw=run.cells.get(YAW))
        step.hits.extend(n.hit for n in carried.values())
        run.steps.append(step)
        run.clip, run.cursor, run.played = clip, float(start), True
        run.blend_in = blend or 0

    def _at(self, run: _Run) -> float:
        """The clip frame an event of this tick happens at: the frame of the crossing that let it
        through, else the cursor."""
        return max(run.crossed) if run.crossed and not run.played else run.cursor

    def _spawn(self, run: _Run, s: Site, with_flags: bool) -> None:
        slot, record = s.args[0], s.args[1]
        flags = (s.args[2] or 0) if with_flags else 0
        step = run.current
        if slot is None or record is None or step is None:
            run.notes.append(
                Note("unresolved", f"the attack spawned at 0x{s.site:08X} is computed")
            )
            return
        if not 0 <= record < len(self.hits.records):
            run.notes.append(Note("unresolved", f"attack record {record} is not in the hit block"))
            return
        self._kill(run, slot)
        at = self._at(run)
        when = window(self.hits.records[record], at, flags)
        if when is None:
            return
        hit = Hit(slot, record, flags, when[0], when[1])
        step.hits.append(hit)
        run.nodes[slot] = _Node(hit, None if when[1] is None else when[1] - at)

    def _kill(self, run: _Run, slot: int | None) -> None:
        node = run.nodes.pop(slot, None) if slot is not None else None
        step = run.current
        if node is None or step is None:
            return
        at, end = self._at(run), node.hit.end
        step.hits[step.hits.index(node.hit)] = _ended(node.hit, at if end is None else min(end, at))

    def _leave(self, run: _Run, to: tuple[Pairs, ...]) -> None:
        run.leaves = to
        run.finished = run.left = True

    def _advance(self, run: _Run, n: int) -> None:
        """`n` AI frames on: the cursor, the nodes' lives and the step's count."""
        run.ticks += n
        step = run.current
        if step is None or run.played:
            return
        step.ticks += n
        step.elapsed += n * self.step
        run.blend_in = max(0, run.blend_in - n)
        length = self.lengths.get(step.clip)
        run.cursor += n * self.step
        for node in run.nodes.values():
            if node.left is not None:
                node.left -= n * self.step
        if length is not None and length.loop and run.cursor >= length.frames:
            run.cursor %= length.frames

    def _crossed(self, run: _Run) -> float | None:
        """The clip frame the step ends at: of the last cursor test this tick whose answer changed
        since the tick before, its `lo`, or its `hi` for a window the cursor left. None on a
        step's first tick, which has no tick before."""
        step, clip = run.current, run.clip
        length = self.lengths.get(clip) if clip is not None else None
        if step is None or step.ticks < 1 or run.ended or clip is None or length is None:
            return None
        before = run.cursor - self.step
        if length.loop:
            before %= length.frames
        for q, yes in reversed(run.asked):
            was = None if q.mode is None else self._answer(q, clip, before)
            if was is not None and was != yes:
                return q.hi if was and q.mode in WINDOWS else q.lo
        return None

    def _close_step(
        self, run: _Run, how: How, stop: float | None = None, hand: int | None = None
    ) -> None:
        step = run.current
        if step is None:
            return
        length = self.lengths.get(step.clip)
        end = None if length is None else length.frames
        hits = [
            _ended(h, min(h.end, end)) if end is not None and h.end is not None else h
            for h in step.hits
        ]
        ticks = step.ticks + 1
        run.done.append(
            Step(
                step.clip,
                step.blend,
                step.start,
                how,
                ticks,
                tuple(hits),
                _turns(step),
                stop,
                _path(step.trace),
                _height(step.trace, round(ticks * self.step)),
                step.lunge,
                tuple(step.spreads),
                _code(step.yaws),
                False,
                hand,
            )
        )
        run.steps.pop()

    def _close(self, run: _Run) -> None:
        how: How = "clip" if run.ended else "pair" if run.left else "cursor"
        self._close_step(run, how, hand=run.hand if run.left else None)
        if run.kin.lost:
            run.notes.append(Note("motion", f"its motion could not be followed: {run.kin.lost}"))


def _spawns(run: _Run) -> int:
    return sum(not h.carried for s in run.done for h in s.hits)


def _neutral(sites: Sequence[Site]) -> bool:
    return all(s.kind in NEUTRAL for s in sites)


def _ended(hit: Hit, end: float) -> Hit:
    return Hit(hit.slot, hit.record, hit.flags, hit.frame, end, hit.carried)


def _float(bits: int) -> float:
    return float(struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0])


def _turns(step: _Step) -> tuple[Turn, ...]:
    """The turns by rate: the window of the one cursor gate that let every tick through, else
    the span of the ticks."""
    by_rate: dict[int | None, list[tuple[float, float, tuple[float, float] | None]]] = {}
    for rate, a, b, gate in step.turns:
        by_rate.setdefault(rate, []).append((a, b, gate))
    out = []
    for rate, found in by_rate.items():
        gates = {g for _, _, g in found}
        one = gates.pop() if len(gates) == 1 else None
        out.append(Turn(rate, one or (min(a for a, _, _ in found), max(b for _, b, _ in found))))
    return tuple(out)


def _bearing_of(bucket: int) -> int:
    """The bearing, YAW units to the left of the facing, a bucket is judged at: one unit past its
    lower edge, so that a compare at a whole fraction of a turn judges it by the bearings above."""
    d = bucket * 256 + 1
    return d - 0x10000 if d >= HALF else d


def _signature(run: _Run) -> tuple[object, ...]:
    """What makes two runs one plan: the clips, from where to where, and what they hand over to."""
    return tuple((s.clip, s.start, s.stop) for s in run.done), run.leaves, run.left


def _plan_signature(plan: Plan) -> tuple[object, ...]:
    """What makes two plans one arm: their clips, from where to where, and where they lead."""
    return tuple((s.clip, s.start, s.stop) for s in plan.steps), plan.leaves, plan.complete


def _rehanded(arm: Arm, pair: Pair, probe: Callable[[int], _Run], samples: Sequence[int]) -> Arm:
    """`arm` of another pair of the handler, with what `pair` hands over to at a bearing in it."""
    k = next(k for k in samples if any(lo <= k <= hi for lo, hi in arm.arcs))
    plan = replace(arm.plan, pair=(pair.main, pair.sub), leaves=probe(k).leaves)
    return Arm(arm.arcs, plan)


def _outer(span: tuple[int, int]) -> int:
    """The largest angle, in YAW units, a bearing of the buckets `span` is off the facing."""
    lo, hi = span
    if hi < BUCKETS // 2:
        return (hi + 1) * 256
    return (BUCKETS - lo) * 256 if lo >= BUCKETS // 2 else HALF


def _near(t: int, d: int) -> bool:
    """`t` is `d` YAW units, within what rounding along a curve costs."""
    return abs(t - d) <= 64 + abs(d) // 32


def _aimed(plan: Plan, span: tuple[int, int], at: int, alt: int, other: _Run) -> Plan:
    """`plan`, walked at bucket `at`, with each step's turns told by the bearing, from a run
    (`other`) at bucket `alt` of the same arm:

    - a turn by code that comes to the bearing itself at both is one that goes by the bearing:
      `Step.code` is the curve scaled to the angle `span` ends at, and `aim` is set; one that comes
      to the same at both is a curve that stands as it is; any other is dropped, with a note;
    - a fixed turn (`spreads`) the other run does not make too depends on the bearing: it is
      dropped, with a note, for the plan to carry the same turn wherever the target is."""
    d1, d2 = _bearing_of(at), _bearing_of(alt)
    steps, notes = [], list(plan.notes)
    for i, step in enumerate(plan.steps):
        twin = other.done[i] if i < len(other.done) and other.done[i].clip == step.clip else None
        if twin is None or abs(d1 - d2) < 512:
            steps.append(step)
            continue
        if gone := [sp for sp in step.spreads if sp not in twin.spreads]:
            step = replace(step, spreads=tuple(sp for sp in step.spreads if sp not in gone))
            text = (
                f"clip {step.clip}: a turn of {gone[0].degrees:.0f} degrees depends on the bearing"
            )
            notes.append(Note("motion", text))
        if step.code:
            t1, t2 = step.code[-1], twin.code[-1] if twin.code else 0
            if _near(t1, t2):
                pass
            elif _near(t1, d1) and _near(t2, d2):
                size = min(_outer(span), 0x7FFF) * (1 if t1 > 0 else -1)
                code = tuple(max(-0x7FFF, min(0x7FFF, round(c * size / t1))) for c in step.code)
                step = replace(step, code=code, aim=True)
            else:
                step = replace(step, code=())
                text = f"clip {step.clip}: a turn by code depends on the bearing, not as an aim"
                notes.append(Note("motion", text))
        steps.append(step)
    return replace(plan, steps=tuple(steps), notes=tuple(notes))


def _signed16(x: int) -> int:
    """The low 16 bits of `x` as a signed YAW difference."""
    x &= 0xFFFF
    return x - 0x10000 if x >= HALF else x


def _interp(keys: Sequence[float], series: Mapping[float, int], frame: float) -> float:
    """The series at clip frame `frame`: straight lines between its points (`keys`, in order),
    held past them."""
    if frame <= keys[0]:
        return series[keys[0]]
    if frame >= keys[-1]:
        return series[keys[-1]]
    hi = bisect.bisect_right(keys, frame)
    a, b = keys[hi - 1], keys[hi]
    return series[a] + (frame - a) / (b - a) * (series[b] - series[a])


def _code(turns: Mapping[float, int]) -> tuple[int, ...]:
    """The code turn: the cumulative yaw every KEY_STEP clip frames from the step's start, the
    first 0, each held within a signed 16 bits; empty where it comes to nothing."""
    if not turns:
        return ()
    keys = sorted(turns)
    n = math.ceil(keys[-1] / KEY_STEP - 1e-9)
    out = [0] + [round(_interp(keys, turns, KEY_STEP * k)) for k in range(1, n + 1)]
    out = [max(-0x7FFF, min(0x7FFF, v)) for v in out]
    return tuple(out) if max(abs(v) for v in out) >= MIN_CODE else ()


def _relative(step: _Step, at: Pose, rest: float) -> Pose:
    """`at` as a step's trace holds it: x and z from where the step began, y over `rest`."""
    return Pose(at.x - step.origin.x, at.y - rest, at.z - step.origin.z)


def _along(keys: Sequence[float], trace: Mapping[float, Pose], frame: float, axis: int) -> float:
    """The trace's `axis` at clip frame `frame`: straight lines between its points (`keys`, in
    order), held past them."""
    if frame <= keys[0]:
        return trace[keys[0]][axis]
    if frame >= keys[-1]:
        return trace[keys[-1]][axis]
    hi = bisect.bisect_right(keys, frame)
    a, b = keys[hi - 1], keys[hi]
    return trace[a][axis] + (frame - a) / (b - a) * (trace[b][axis] - trace[a][axis])


def _path(trace: Mapping[float, Pose]) -> tuple[tuple[float, float], ...]:
    """The trace's (x, z) every KEY_STEP clip frames from the step's start. The first is the
    origin, as the move player's first frame moves nothing: what the frames at the start did
    shows from the second."""
    if not trace:
        return ()
    keys = sorted(trace)
    n = math.ceil(keys[-1] / KEY_STEP - 1e-9)
    out = [(0.0, 0.0)] + [
        (
            round(_along(keys, trace, KEY_STEP * k, 0), 2),
            round(_along(keys, trace, KEY_STEP * k, 2), 2),
        )
        for k in range(1, n + 1)
    ]
    return tuple(out) if max(max(abs(x), abs(z)) for x, z in out) >= MIN_TRAVEL else ()


def _height(trace: Mapping[float, Pose], frames: int) -> tuple[float, ...]:
    """The trace's height over rest at each whole clip frame from 0 to `frames`; empty where it
    stays put (a monster that landed low and lies there has the engine's floor to stand on)."""
    if not trace:
        return ()
    heights = [p.y for p in trace.values()]
    if max(heights) - min(heights) < MIN_LIFT:
        return ()
    keys = sorted(trace)
    return tuple(round(_along(keys, trace, f, 1), 2) for f in range(frames + 1))


def _wrappers(code: Code) -> frozenset[int]:
    """The functions that test the clip cursor and then tail-call an attack spawner: the form a
    species' clip hook spawns through."""
    calls: dict[int, list[int | None]] = {}
    for c in code.calls:
        calls.setdefault(code.function(c.site).start, []).append(c.target)
    return frozenset(
        fn
        for fn, targets in calls.items()
        if targets[0] in CURSOR_GATES and targets[-1] in (p3.ATTACK, p3.ATTACK0)
    )
