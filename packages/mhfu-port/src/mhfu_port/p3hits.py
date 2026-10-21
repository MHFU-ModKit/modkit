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
branches on something it cannot know (a bearing, a counter) it takes the branch falling through,
then each in turn, and the plan is the path that plays the fewest clips.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, NamedTuple

from mhfu.em.moveset import Decide, Site, Walker, known
from mhfu.files import Extracted
from mhfu.mips import Code, Fpr, Gpr
from mhfu.overlay import Overlay
from rabbitizer import InstrId

from . import motion
from . import p3moveset as p3
from .brain.p3 import Species
from .p3moveset import Moveset, Pair, Pairs

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
HOOK = 0x90
"""The species vtable slot called every frame: the attacks a clip spawns on its own."""
KILL = 0x09C94AFC  # noaddr: (entity, slot) ends the attack node in that slot
CURSOR_GATES = (0x09C9DF94, 0x09C9E000)  # noaddr: (entity, mode, $f12, $f13) tests the clip cursor
CLIP_ENDED = 0x09CA6730  # noaddr: (entity) 1 once the playing clip has run out
CLIP_ID = 0x09CA699C  # noaddr: (entity) the clip playing now
CURSOR_STEP = 0x09C9DF10  # noaddr: () $f0 the clip cursor's step this frame
TURN = 0x09CA12F4  # noaddr: (entity, rate) turns toward the target by at most rate YAW units
MOTION = (
    0x09CA20EC,  # noaddr: velocity and acceleration
    0x09CA1F34,  # noaddr: velocity in world axes
    0x09CA19E0,  # noaddr: velocity to zero
    0x0889F098,  # noaddr: a curve table read at the clip cursor
)
"""Calls that move the monster by code rather than by the clip."""
STEP_FRAMES = 2.0
"""Clip frames the cursor advances in an AI frame at normal speed."""
MAX_TICKS = 3000
MAX_RUNS = 32
"""Bounds on one simulation, and on the runs of a pair with their branches going the other way."""
FALL, TAKEN, LOOPS, EXITS = 0, 1, 2, 3
"""How a branch the walk cannot decide goes: falls through, is taken, is taken until the clip ends
and then falls through (a wait), or falls through until the clip ends and then is taken."""
BRANCHES = {
    TAKEN: "is taken",
    LOOPS: "is taken until the clip ends",
    EXITS: "is taken once the clip ends",
}
ARGS = (Gpr.a1, Gpr.a2, Gpr.a3, Gpr.t0, Fpr.fa0, Fpr.fa0f)
MODE, F12, F13 = 0, 4, 5
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
    **dict.fromkeys(MOTION, "motion"),
}
"""What the walk records, by call target; a kind that needs its target carries it after a colon."""
SPAWNERS = {p3.ATTACK: "spawn", p3.ATTACK0: "spawn0", KILL: "kill"}


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
        sets = []
        at = sets_at
        while at in ovl.initialised and (va := ovl.u32(at)) in ovl.initialised:
            sets.append(_volumes(ovl, va))
            at += 4
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


@dataclass(frozen=True)
class Note:
    """What a plan cannot say."""

    code: Literal["branch", "motion", "start", "unresolved", "limit", "record"]
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


@dataclass
class _Run:
    """One simulation: the clip cursor, the phase and what the handler did."""

    vector: dict[int, int]
    """How each branch the walk cannot decide goes: FALL, TAKEN, LOOPS or EXITS."""
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
    ) -> None:
        self.ms, self.hits, self.lengths, self.step = ms, hits, lengths, step
        stores = {
            i.vram: i.rt
            for i in ms.code
            if i.uniqueId == InstrId.cpu_sb and i.getProcessedImmediate() == PHASE
        }
        self._walker = Walker(
            ms.code,
            WATCH,
            depth=3,
            guards=False,
            args=ARGS,
            probes=stores,
            returns={CURSOR_STEP: step},
            max_paths=512,
        )
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
    def of(cls, extracted: Extracted, monster: int, anim: bytes, slot: int = 0) -> Walk:
        """The walk of MHP3rd monster `monster`'s overlay in `slot`, with the clips of its
        animation file `anim` (the lengths its handlers' tests are answered from)."""
        found = Species.read(extracted).slots[monster]
        if slot >= len(found):
            raise ValueError(f"monster {monster} has {len(found)} slots, not {slot + 1}")
        ms = Moveset(extracted.overlay(found[slot].file))
        clips = motion.moveset(anim)
        lengths = {i: Length(motion.frames(c), bool(c.loop)) for i, c in clips.items()}
        return cls(ms, Hits.read(ms.ovl, found[slot].addr), lengths)

    def _hook_function(self) -> int | None:
        vt = self.ms.vtable
        if vt is None:
            return None
        fn = self.ms.ovl.u32(vt + HOOK)
        return fn if fn in self.ms.code.text else None

    # --- the plan ---

    def plan(self, pair: Pair) -> Plan:
        """The default path through `pair`'s handler: with every branch the walk cannot decide
        falling through, and with each of them taken in turn, the outcome that spawns the most
        attacks, of those the one that plays the fewest clips, the first found among equals."""
        at = (pair.main, pair.sub)
        if pair.handler is None or self.ms.tick_entry is None:
            return Plan(at, (), (), False, (Note("unresolved", "it has no handler of its own"),))
        first = self._run(pair, {})
        runs = [first]
        for how in (TAKEN, LOOPS, EXITS):
            for pc in first.trail:
                if len(runs) < MAX_RUNS:
                    runs.append(self._run(pair, {**first.vector, pc: how}))
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
        if len(runs) >= MAX_RUNS:
            notes.append(Note("limit", f"only {MAX_RUNS - 1} runs of its branches were tried"))
        return Plan(at, tuple(best.done), best.leaves, best.left, tuple(notes), tuple(others))

    def _run(self, pair: Pair, vector: dict[int, int]) -> _Run:
        tick = self.ms.tick_entry
        assert tick is not None
        run = _Run(dict(vector))
        decide = self._decider(run)
        at = self._dispatched(pair)
        while run.ticks < MAX_TICKS:
            run.ended = run.played = False
            run.crossed, run.windows, run.asked = [], [], []
            facts = known({PHASE: run.phase, p3.MAIN: at[0], p3.SUB: at[1]})
            sites = self._walker.run(tick, None, facts, decide)
            if self._hook is not None and run.clip is not None:
                sites += self._hook_walker.run(self._hook, None, None, decide)
            phase = run.phase
            self._apply(run, pair, sites)
            if run.finished:
                break
            if run.current is None and not sites:
                run.notes.append(Note("unresolved", "the handler plays no clip"))
                break
            self._advance(run, 1)
            if run.phase == phase and not run.played and _neutral(sites):
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
        def decide(pc: int, target: int | None, args: tuple[int | None, ...]) -> int | None:
            if target is None:
                return self._branch(run, pc)
            if target in CURSOR_GATES:
                return self._gate(run, args)
            if target == CLIP_ENDED:
                return self._ask(run, _Query(None))
            if target == CLIP_ID:
                return run.clip if run.clip is not None else 0
            return None

        return decide

    def _branch(self, run: _Run, pc: int) -> int:
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

    def _apply(self, run: _Run, pair: Pair, sites: Sequence[Site]) -> None:
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
            elif kind == "enter":
                a = s.args
                self._leave(run, ((a[0], a[1]),) if a[0] is not None and a[1] is not None else ())

    def _play(self, run: _Run, pair: Pair, s: Site) -> None:
        clip = s.args[0]
        if clip is None:  # a byte of the constant table the handler copies: one per phase
            ids = [i for i in self.ms.table_ids(s.site, pair.entry) or [] if i]
            clip = ids[min(run.phase, len(ids) - 1)] if ids else None
        if clip is None:
            run.notes.append(Note("unresolved", f"the clip played at 0x{s.site:08X} is computed"))
            run.finished = True
            return
        start = s.args[2] or 0
        if start:
            run.notes.append(Note("start", f"clip {clip} starts at frame {start}"))
        self._start(run, clip, s.args[1], start)

    def _play_first(self, run: _Run, ids: Sequence[int] | None, s: Site) -> None:
        """A helper that plays one of several clips it is given a record of: the first."""
        if not ids:
            run.notes.append(Note("unresolved", f"the clips played at 0x{s.site:08X} are computed"))
            run.finished = True
            return
        if len(ids) > 1:
            run.notes.append(Note("record", f"clip {ids[0]} of {', '.join(map(str, ids))}"))
        self._start(run, ids[0], None, 0)

    def _start(self, run: _Run, clip: int, blend: int | None, start: int) -> None:
        if clip not in self.lengths:
            run.notes.append(Note("unresolved", f"the donor has no clip {clip}"))
            run.finished = True
            return
        self._close_step(run, "clip" if run.ended else "cursor")
        carried = {}
        for slot, node in run.nodes.items():
            if not node.hit.flags & FLAG_OUTLIVES or (node.left is not None and node.left <= 0):
                continue
            end = None if node.left is None else start + node.left
            hit = Hit(slot, node.hit.record, node.hit.flags, float(start), end, True)
            carried[slot] = _Node(hit, node.left)
        run.nodes = carried
        step = _Step(clip, blend, float(start))
        step.hits.extend(n.hit for n in carried.values())
        run.steps.append(step)
        run.clip, run.cursor, run.played = clip, float(start), True

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
        length = self.lengths.get(step.clip)
        run.cursor += n * self.step
        for node in run.nodes.values():
            if node.left is not None:
                node.left -= n * self.step
        if length is not None and length.loop and run.cursor >= length.frames:
            run.cursor %= length.frames

    def _close_step(self, run: _Run, how: How) -> None:
        step = run.current
        if step is None:
            return
        length = self.lengths.get(step.clip)
        end = None if length is None else length.frames
        hits = [
            _ended(h, min(h.end, end)) if end is not None and h.end is not None else h
            for h in step.hits
        ]
        run.done.append(
            Step(step.clip, step.blend, step.start, how, step.ticks + 1, tuple(hits), _turns(step))
        )
        run.steps.pop()

    def _close(self, run: _Run) -> None:
        how: How = "clip" if run.ended else "pair" if run.left else "cursor"
        self._close_step(run, how)


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
