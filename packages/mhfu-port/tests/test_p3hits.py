# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace

import pytest
from mhfu.files import Extracted
from mhfu.memory import Image
from mhfu.mips import Code
from mhfu.overlay import Overlay
from mhfu_port import p3hits, p3motion, p3moveset
from mhfu_port import p3moveset as p3
from mhfu_port.manifest import Source
from mhfu_port.p3hits import Hit, Hits, Length, Record, Walk, window
from modkit_testing import mips as asm

BASE = 0x0010_0000
LEAVE = next(iter(p3.END))
UNKNOWN = 0x0020_0000  # a call the walk knows nothing about
STACK = asm.addiu("sp", "sp", -0x10)


class Asm:
    """Words with labels; a branch names its label."""

    def __init__(self) -> None:
        self.words: list[int | Callable[[dict[str, int], int], int]] = []
        self.labels: dict[str, int] = {}

    def label(self, name: str) -> None:
        self.labels[name] = BASE + 4 * len(self.words)

    def __call__(self, *items: int | Callable[[dict[str, int], int], int]) -> None:
        self.words += items

    def branch(self, kind: str, rs: str, rt: str, label: str) -> None:
        fn = {"beq": asm.beq, "bne": asm.bne}[kind]
        self.words += [lambda labels, at: fn(rs, rt, labels[label], at), asm.NOP]

    def jump(self, label: str) -> None:
        self.words += [lambda labels, at: asm.j(labels[label]), asm.NOP]

    def call(self, target: int) -> None:
        self.words += [asm.jal(target), asm.NOP]

    def float_branch(self, kind: str, label: str) -> None:
        """A branch on the float compare just made."""
        fn = {"t": asm.bc1t, "f": asm.bc1f}[kind]
        self.words += [lambda labels, at: fn(labels[label], at), asm.NOP]

    def load_float(self, freg: int, label: str) -> None:
        """`freg` from the float at `label`, by the pool's lui and lwc1 pair."""
        self.words += [
            lambda labels, at: asm.lui("v0", asm.hi(labels[label])),
            lambda labels, at: asm.lwc1(freg, asm.lo(labels[label]), "v0"),
        ]

    def code(self) -> Code:
        done = [
            w(self.labels, BASE + 4 * i) if callable(w) else w for i, w in enumerate(self.words)
        ]
        blob = struct.pack(f"<{len(done)}I", *done)
        return Code(Image(blob, BASE), range(BASE, BASE + len(blob)))


def spawn(a: Asm, slot: int, record: int) -> None:
    a(asm.addu("a0", "s0", "zero"), asm.li("a1", slot), asm.li("a2", record), asm.li("a3", 0))
    a.call(p3.ATTACK)


def play(a: Asm, clip: int) -> None:
    a(asm.addu("a0", "s0", "zero"), asm.li("a1", clip), asm.li("a2", 4), asm.li("a3", 0))
    a.call(p3.EXECUTOR)


def gate(a: Asm, mode: int, lo: int, hi: int = 0) -> None:
    """A cursor gate on frames lo and hi, whose answer is left in v0."""
    a(asm.addu("a0", "s0", "zero"), asm.li("a1", mode))
    a(asm.lui("at", lo), asm.mtc1("at", 12), asm.lui("at", hi), asm.mtc1("at", 13))
    a.call(p3hits.CURSOR_GATES[0])


F0, F40, F60 = 0, 0x4220, 0x4270
"""The high halves of 0.0, 40.0 and 60.0 as floats."""


def species(
    phase_one: Callable[[Asm], None],
    phase_zero: Callable[[Asm], None],
    pool: Mapping[str, float] | None = None,
) -> Code:
    """A tick that runs `phase_zero`, then `phase_one` until it returns or ends the pair; `pool`
    are floats the code can load by label."""
    a = Asm()
    a(STACK, asm.sw("s0", 4, "sp"), asm.sw("ra", 8, "sp"), asm.addu("s0", "a0", "zero"))
    a(asm.lbu("v1", p3hits.PHASE, "s0"))
    a.branch("beq", "v1", "zero", "phase0")
    a(asm.li("v0", 1))
    a.branch("beq", "v1", "v0", "phase1")
    a.label("done")
    a(asm.lw("ra", 8, "sp"), asm.lw("s0", 4, "sp"), asm.addiu("sp", "sp", 0x10), asm.RET, asm.NOP)
    a.label("phase0")
    phase_zero(a)
    a(asm.li("v0", 1), asm.sb("v0", p3hits.PHASE, "s0"))
    a.jump("done")
    a.label("phase1")
    phase_one(a)
    a.jump("done")
    for name, value in (pool or {}).items():
        a.label(name)
        a(struct.unpack("<I", struct.pack("<f", value))[0])
    return a.code()


def first_phase(a: Asm) -> None:
    play(a, 61)
    spawn(a, 0, 5)


def straight(a: Asm) -> None:
    """A window turn, an attack at a crossing, and the pair's end with the clip's."""
    gate(a, 3, F0, F40)
    a.branch("beq", "v0", "zero", "no_turn")
    a(asm.li("a1", 100), asm.addu("a0", "s0", "zero"))
    a.call(p3hits.TURN)
    a.label("no_turn")
    gate(a, 0, F60)
    a.branch("beq", "v0", "zero", "no_spawn")
    spawn(a, 1, 6)
    a.label("no_spawn")
    a(asm.addu("a0", "s0", "zero"))
    a.call(p3hits.CLIP_ENDED)
    a.branch("beq", "v0", "zero", "done")
    a.call(LEAVE)


RECORDS = tuple(
    Record(i, start, length, 40, 2, 0, 0)
    for i, (start, length) in enumerate([(0, 0), (0, 5), (0, 5), (0, 5), (0, 5), (20, 30), (0, 10)])
)
HITS = Hits(RECORDS, ())
LENGTHS = {61: Length(100.0)}


def walk(code: Code, lengths=LENGTHS, **options) -> Walk:
    tick = BASE
    ms = SimpleNamespace(
        code=code,
        ovl=None,
        vtable=None,
        tick_entry=tick,
        translator={},
        table_ids=lambda site, entry: None,
    )
    return Walk(ms, HITS, lengths, **options)  # type: ignore[arg-type]


def pair() -> p3.Pair:
    return p3.Pair(0, 0, BASE, (), False, "move", (), (), frozenset(), ())


def test_window():
    r = Record(0, 20, 30, 40, 2, 0, 0)
    assert window(r, 0) == (20, 50) and window(r, 30) == (30, 50) and window(r, 50) is None
    assert window(r, 0, p3hits.FLAG_REFRESH) == (20, None)
    now = Record(0, 0, 10, 40, 2, 0, 0)
    assert window(now, 24.0) == (24.0, 34.0)


def test_hit_block():
    load, at = 0x0030_0000, 0x0030_0050
    descriptor, hits, records, sets, volumes = at, at + 0xA8, at + 0x110, at + 0x110 + 2 * 28, 0
    sets_table = sets
    volumes = sets_table + 8
    blob = bytearray(0x400)
    struct.pack_into("<I", blob, descriptor - at + p3hits.DESCRIPTOR_HITS, hits)
    struct.pack_into("<II", blob, hits - at + p3hits.HIT_SETS, sets_table, records)
    struct.pack_into("<hhhB", blob, records - at, 20, 30, 40, 2)
    blob[records - at + p3hits.RECORD_TAG] = 7
    struct.pack_into("<hhhB", blob, records - at + 28, -1, 10, 20, 1)
    blob[records - at + 28 + p3hits.RECORD_SET] = 0
    struct.pack_into("<I", blob, sets_table - at, volumes)
    one = struct.pack("<HhHxxI4f3f", 9, -1, 1, 0, 150.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    blob[volumes - at : volumes - at + len(one)] = one
    struct.pack_into("<H", blob, volumes - at + p3hits.VOLUME_SIZE, 0xFFFF)
    head = struct.pack("<4sIIIIIII", b"MWo3", 1, load, 0x10, len(blob), 0, 0, 0).ljust(0x40, b"\0")
    ovl = Overlay(head + bytes(0x10) + bytes(blob), "em000.ovl")
    got = Hits.read(ovl, descriptor)
    assert got.records[:2] == (
        Record(0, 20, 30, 40, 2, 7, 0),
        Record(1, -1, 10, 20, 1, 0, 0),
    )
    assert got.volumes(0)[0] == p3hits.Volume(9, -1, "capsule", 0, 150.0, (1, 2, 3), (4, 5, 6))
    assert got.volumes(0)[1:] == () and got.volumes(99) == ()
    with pytest.raises(ValueError, match="no hit block"):
        Hits.read(ovl, descriptor + 4)


def test_straight_pair():
    plan = walk(species(straight, first_phase)).plan(pair())
    assert plan.complete and plan.leaves == p3.END[LEAVE] and not plan.alternatives
    (step,) = plan.steps
    assert (step.clip, step.start, step.ended, step.ticks) == (61, 0, "clip", 50)
    assert step.hits == (Hit(0, 5, 0, 20, 50), Hit(1, 6, 0, 60, 70))
    assert [(t.rate, t.window) for t in step.turns] == [(100, (0, 40))]
    assert step.stop is None  # the clip ended it


def test_a_step_stops_at_the_frame_its_gate_crossed():
    def second(a: Asm) -> None:
        gate(a, 0, F60)
        a.branch("beq", "v0", "zero", "done")
        play(a, 62)
        a.call(LEAVE)

    plan = walk(species(second, lambda a: play(a, 61)), {61: Length(100.0), 62: Length(40.0)}).plan(
        pair()
    )
    first, last = plan.steps
    assert (first.clip, first.ended, first.ticks, first.stop) == (61, "cursor", 30, 60.0)
    assert (last.clip, last.ended, last.stop) == (62, "pair", None)


def test_a_step_stops_at_the_end_of_the_window_it_left():
    def second(a: Asm) -> None:
        gate(a, 3, F0, F40)
        a.branch("bne", "v0", "zero", "done")
        play(a, 62)
        a.call(LEAVE)

    plan = walk(species(second, lambda a: play(a, 61)), {61: Length(100.0), 62: Length(40.0)}).plan(
        pair()
    )
    assert [(s.ended, s.ticks, s.stop) for s in plan.steps] == [
        ("cursor", 21, 40.0),
        ("pair", 1, None),
    ]


def test_a_step_ended_on_its_first_tick_has_no_frame_to_stop_on():
    def second(a: Asm) -> None:
        gate(a, 1, F0)  # already true on the first tick: nothing changed since a tick before
        play(a, 62)
        a.call(LEAVE)

    plan = walk(species(second, lambda a: play(a, 61)), {61: Length(100.0), 62: Length(40.0)}).plan(
        pair()
    )
    assert [(s.ended, s.ticks, s.stop) for s in plan.steps] == [
        ("cursor", 1, None),
        ("pair", 1, None),
    ]


def test_a_branch_the_walk_cannot_decide_is_taken_for_the_attack():
    def first(a: Asm) -> None:
        play(a, 61)
        a(asm.lbu("v0", 0x2000, "s0"))
        a.branch("bne", "v0", "zero", "attack")
        a.jump("rest")
        a.label("attack")
        spawn(a, 0, 5)
        a.label("rest")

    def second(a: Asm) -> None:
        a.call(LEAVE)

    plan = walk(species(second, first)).plan(pair())
    (step,) = plan.steps
    assert [h.record for h in step.hits] == [5]
    assert [n.code for n in plan.notes] == ["branch"] and "is taken" in plan.notes[0].text


def test_a_wait_on_something_unknown_lasts_until_the_clip_ends():
    def second(a: Asm) -> None:
        gate(a, 0, F60)
        a.branch("beq", "v0", "zero", "ask")
        spawn(a, 0, 6)
        a.label("ask")
        a.call(UNKNOWN)
        a.branch("beq", "v0", "zero", "done")
        a.call(LEAVE)

    plan = walk(species(second, lambda a: play(a, 61))).plan(pair())
    (step,) = plan.steps
    assert plan.complete and step.ticks == 50 and step.hits == (Hit(0, 6, 0, 60, 70),)
    assert "until the clip ends" in plan.notes[0].text


def test_a_clip_the_donor_lacks_ends_the_walk():
    plan = walk(species(straight, first_phase), {}).plan(pair())
    assert not plan.complete and plan.steps == () and plan.notes[0].code == "unresolved"


COUNTER, GAUGE = 0xF64, 0xFF4


def counts_down(a: Asm) -> None:
    """Until a counter the first phase set runs out: one off it and ten on a gauge a frame."""
    a(asm.lbu("v0", COUNTER, "s0"))
    a.branch("beq", "v0", "zero", "out")
    a(asm.addiu("v0", "v0", -1), asm.sb("v0", COUNTER, "s0"))
    a(asm.lhu("v1", GAUGE, "s0"), asm.addiu("v1", "v1", 10), asm.sh("v1", GAUGE, "s0"))
    a.jump("done")
    a.label("out")
    a.call(LEAVE)


def sets_counter(a: Asm) -> None:
    play(a, 61)
    a(asm.li("v0", 3), asm.sb("v0", COUNTER, "s0"))


def test_tracked_cells_carry_from_frame_to_frame():
    cells = {"track": [(COUNTER, 1), (GAUGE, 2)], "facts": {(GAUGE, 2): 5}}
    got = walk(species(counts_down, sets_counter), **cells).settle(pair())
    assert got.left and got.cells == {(COUNTER, 1): 0, (GAUGE, 2): 35} and got.ticks == 4
    assert not got.undecided


def test_a_branch_on_an_untracked_cell_is_not_decided():
    got = walk(species(counts_down, sets_counter)).settle(pair())
    assert not got.left and got.cells == {} and got.undecided


def test_facts_start_a_run_and_are_not_shared_between_runs():
    w = walk(species(counts_down, sets_counter), track=[(COUNTER, 1)], facts={(GAUGE, 2): 7})
    first, second = w.settle(pair()), w.settle(pair())
    assert first.cells == second.cells == {(COUNTER, 1): 0, (GAUGE, 2): 7}


def over_three_quarters(a: Asm) -> None:
    """Thirty on the gauge above 0.75 of the HP, ten else, then the pair's end."""
    a.load_float(0, "limit")
    a(asm.c_lt_s(0, 20), asm.NOP)
    a.float_branch("t", "big")
    a(asm.li("v1", 10))
    a.jump("add")
    a.label("big")
    a(asm.li("v1", 30))
    a.label("add")
    a(asm.sh("v1", GAUGE, "s0"))
    a.call(LEAVE)


def test_the_hp_ratio_answers_a_float_test_against_a_constant():
    code = species(over_three_quarters, lambda a: play(a, 61), {"limit": 0.75})
    w = walk(code, track=[(GAUGE, 2)])
    assert w.settle(pair(), 0.9).cells == {(GAUGE, 2): 30}
    assert w.settle(pair(), 0.5).cells == {(GAUGE, 2): 10}
    assert w.settle(pair(), 0.75).cells == {(GAUGE, 2): 10}  # c.lt.s: 0.75 < 0.75 does not hold
    assert not w.settle(pair(), 0.9).undecided


def test_without_an_hp_ratio_the_float_test_falls_through():
    code = species(over_three_quarters, lambda a: play(a, 61), {"limit": 0.75})
    got = walk(code, track=[(GAUGE, 2)]).settle(pair())
    assert got.cells == {(GAUGE, 2): 10} and len(got.undecided) == 1


def test_the_false_branch_of_a_float_test():
    def below(a: Asm) -> None:
        a.load_float(0, "limit")
        a(asm.c_le_s(0, 20), asm.NOP)
        a.float_branch("f", "small")
        a(asm.li("v1", 30))
        a.jump("add")
        a.label("small")
        a(asm.li("v1", 10))
        a.label("add")
        a(asm.sh("v1", GAUGE, "s0"))
        a.call(LEAVE)

    w = walk(species(below, lambda a: play(a, 61), {"limit": 0.75}), track=[(GAUGE, 2)])
    assert w.settle(pair(), 0.75).cells == {(GAUGE, 2): 30}  # 0.75 <= 0.75 holds
    assert w.settle(pair(), 0.6).cells == {(GAUGE, 2): 10}


def jump(a: Asm) -> None:
    """Rises 30 a frame while the cursor is under 4, then falls under a pull of 20 until its height
    is 30 or less: the clip 62 plays and the pair ends."""
    a(asm.addu("a0", "s0", "zero"))
    a.call(p3motion.CURSOR)
    a(asm.mov_s(20, 0))
    a.load_float(1, "four")
    a(asm.c_lt_s(20, 1), asm.NOP)
    a.float_branch("f", "fall")
    a.load_float(2, "up")
    a(asm.swc1(2, 0xB4, "s0"), asm.addu("a0", "s0", "zero"))
    a.call(p3motion.VEL_APPLY)
    a.jump("done")
    a.label("fall")
    a.load_float(2, "pull")
    a(asm.swc1(2, 0xC4, "s0"), asm.addu("a0", "s0", "zero"))
    a.call(p3motion.VEL_ACC)
    a(asm.lwc1(3, 0x84, "s0"))
    a.load_float(4, "land")
    a(asm.c_le_s(3, 4), asm.NOP)
    a.float_branch("f", "done")
    play(a, 62)
    a.call(LEAVE)


JUMP = {"four": 4.0, "up": 30.0, "pull": -20.0, "land": 30.0}


def test_a_jump_lasts_until_the_height_the_motion_model_works_out():
    lengths = {61: Length(100.0), 62: Length(40.0)}
    first, second = (
        walk(species(jump, lambda a: play(a, 61), JUMP), lengths, rest=0.0).plan(pair()).steps
    )
    heights = [30, 60, 90, 100, 90, 60, 10]  # after the AI frame at cursor 0, 2, ... 12
    assert (first.clip, first.ticks, first.ended) == (61, 7, "cursor")
    assert first.lift == tuple(pytest.approx(v) for v in jump_lift(heights, 15))
    assert first.travel == () and first.lunge is None and not first.spreads
    assert (second.clip, second.lift) == (62, ())  # the height it lands at is the floor's


def jump_lift(heights: list[int], frames: int) -> list[float]:
    """Straight lines through (0, h0), (2, h1), ... held past the last."""
    out = []
    for f in range(frames):
        k = min(f / 2, len(heights) - 1)
        lo = int(k)
        hi = min(lo + 1, len(heights) - 1)
        out.append(heights[lo] + (k - lo) * (heights[hi] - heights[lo]))
    return out


def test_a_jump_starts_from_the_rest_height_it_is_given():
    lengths = {61: Length(100.0), 62: Length(40.0)}
    w = walk(species(jump, lambda a: play(a, 61), JUMP), lengths, rest=500.0)
    first, _ = w.plan(pair()).steps
    assert first.ticks > 7  # the landing test reads the height over the floor: a longer fall


BRUTE, BRUTE_MODEL = 23, 5248
ZINOGRE, ZINOGRE_MODEL = 40, 5339


def walker(mhp3rd_data: Path, monster: int, model: int) -> tuple[Walk, p3moveset.Moveset]:
    extracted = Extracted.find(mhp3rd_data)
    w = Walk.of(extracted, monster, extracted.read(Source(model=model).anim))
    return w, w.ms


@pytest.fixture(scope="module")
def brute(mhp3rd_data):
    return walker(mhp3rd_data, BRUTE, BRUTE_MODEL)


@pytest.fixture(scope="module")
def zinogre(mhp3rd_data):
    return walker(mhp3rd_data, ZINOGRE, ZINOGRE_MODEL)


def test_brute_hit_block(brute):
    w, _ = brute
    assert len(w.hits.records) == 29 and len(w.hits.sets) == 18
    assert w.hits.records[21].start == 135 and w.hits.records[21].length == 30
    assert [(v.bone, v.radius) for v in w.hits.volumes(28)] == [(0, 900.0)]


def plan_of(brute, main: int, sub: int) -> p3hits.Plan:
    w, ms = brute
    return w.plan(ms.pairs[main, sub])


def test_brute_charge(brute):
    plan = plan_of(brute, 7, 13)
    (step,) = plan.steps
    assert (step.clip, step.ended, step.ticks) == (61, "clip", 191)
    assert [(h.record, h.flags, h.frame, h.end) for h in step.hits] == [
        (28, 8, 138, 148),
        (21, 16, 135, 165),
    ]
    assert [(t.rate, t.window) for t in step.turns] == [(546, (10, 40))]


def test_brute_leap(brute):
    (step,) = plan_of(brute, 7, 15).steps
    assert [(h.record, h.flags, h.frame, h.end) for h in step.hits] == [(22, 0, 75, 100)]
    assert [(t.rate, t.window) for t in step.turns] == [(273, (0, 20))]


def test_brute_rush_takes_the_straight_path(brute):
    plan = plan_of(brute, 7, 14)
    first, second = plan.steps
    assert (first.clip, second.clip) == (59, 60) and plan.alternatives == (
        (65, 59, 60),
        (64, 59, 60),
    )
    assert [(h.record, h.frame, h.end, h.carried) for h in first.hits] == [
        (19, 85, None, False),
        (26, 85, None, False),
    ]
    assert [(h.record, h.frame, h.end, h.carried) for h in second.hits] == [
        (19, 0, 14, True),
        (26, 0, 14, True),
        (27, 96, 106, False),
        (20, 94, 124, False),
    ]
    assert [(t.rate, t.window) for t in first.turns] == [(364, (50, 140))]
    assert {n.code for n in plan.notes} == {"branch", "motion"}


def test_brute_hook_spawns_by_clip(brute):
    (step,) = plan_of(brute, 1, 4).steps  # clip 26: its vt+0x90 hook spawns record 17 at frame 132
    assert (step.clip, step.hits) == (26, (Hit(0, 17, 0, 132, 142),))
    (step,) = plan_of(brute, 1, 1).steps  # clip 25 spawns record 13 twice
    assert [(h.record, h.frame, h.end) for h in step.hits] == [(13, 238, 248), (13, 284, 294)]


def test_brute_pairs_without_attacks(brute):
    for key, clips in {
        (1, 15): [63],
        (2, 20): [33],
        (2, 23): [62],
        (2, 19): [32, 107, 108],
    }.items():
        plan = plan_of(brute, *key)
        assert plan.complete and [s.clip for s in plan.steps] == clips
        assert not any(s.hits for s in plan.steps)


def test_zinogre_walks_its_attack_pairs(zinogre):
    w, ms = zinogre
    pairs = [p for p in ms.pairs.values() if p.attacks][:12]
    plans = [w.plan(p) for p in pairs]
    assert all(p.complete for p in plans)
    spawned = sum(any(h for s in p.steps for h in s.hits) for p in plans)
    assert spawned >= len(plans) * 3 // 4


@pytest.fixture(scope="module")
def plans(zinogre):
    """The Zinogre's plans by pair, each walked once."""
    w, ms = zinogre
    got: dict[tuple[int, int], p3hits.Plan] = {}

    def of(main: int, sub: int) -> p3hits.Plan:
        if (main, sub) not in got:
            got[main, sub] = w.plan(ms.pairs[main, sub])
        return got[main, sub]

    return of


def step_of(plan: p3hits.Plan, clip: int) -> p3hits.Step:
    return next(s for s in plan.steps if s.clip == clip)


def test_zinogre_air_roll_lasts_22_frames_and_flies_as_the_donor_does(plans):
    for pair_ in [(7, 22), (7, 112), (7, 201)]:
        roll = step_of(plans(*pair_), 225)
        assert roll.ticks == 22 and roll.travel[0] == (0.0, 0.0)
        assert roll.travel[-1] == pytest.approx((120, 852), rel=0.01)
        assert max(roll.lift) == pytest.approx(644, rel=0.01) and len(roll.lift) == 45
        assert roll.lunge == p3motion.Lunge((0.0, 32.0), 400.0, 1800.0, -700.0, 1800.0)
        assert not roll.spreads
    landing = step_of(plans(7, 22), 226)
    assert landing.lift == () and landing.travel == ()  # the floor takes it from there


def test_zinogre_stamp_jump_lasts_18_frames_after_a_wind_up_of_16_or_39(plans):
    for pair_, wind_up in [((7, 21), 16), ((7, 86), 39), ((7, 100), 39)]:
        steps = plans(*pair_).steps
        assert steps[0].clip == 213 and steps[0].ticks == wind_up
        hop = step_of(plans(*pair_), 209)
        assert hop.ticks == 18 and hop.lift[-1] == 0.0  # it lands and snaps to rest
        assert hop.travel[-1] == pytest.approx((83, 1401), rel=0.01, abs=1)
        assert max(hop.lift) == pytest.approx(300, rel=0.01)


def test_zinogre_runs_that_end_in_a_slide_travel_and_turn_half_way_round(plans):
    first = step_of(plans(7, 35), 229)
    assert first.travel[-1] == pytest.approx((-10, 1046), rel=0.01, abs=0.5)
    assert first.spreads == (p3motion.Spread(-0x8000, (0.0, 60.0)),)
    second = step_of(plans(7, 67), 230)
    assert second.travel[-1] == pytest.approx((71, 1434), rel=0.01, abs=0.5)
    assert len(first.travel) == 75 and len(second.travel) == 52  # every two clip frames


def test_zinogre_sidesteps_travel_across_their_window_and_turn_with_it(plans):
    for sub, x, total in [(16, -257, -0x8000), (17, 192, 0x8000)]:
        (step,) = plans(2, sub).steps
        assert step.ticks == 103 and step.travel[0] == (0.0, 0.0)
        assert step.travel[40 // 2 - 1] == (0.0, 0.0) and step.travel[-1] == (x, -525.0)
        assert step.spreads == (p3motion.Spread(total, (40.0, 160.0)),)


def test_zinogre_tumbles_turn_by_the_donors_totals(plans):
    assert step_of(plans(7, 66), 236).spreads == (p3motion.Spread(0x2666, (60.0, 98.0)),)
    assert step_of(plans(7, 66), 236).travel[-1] == (0.0, -116.0)
    roll = step_of(plans(7, 87), 248)
    assert roll.spreads == (p3motion.Spread(-0x4CCD, (26.0, 64.0)),)
    assert roll.travel[-1] == pytest.approx((52, 232), abs=0.5)


def test_a_step_without_code_motion_carries_none(plans):
    for s in plans(7, 22).steps[:1]:
        assert (s.travel, s.lift, s.lunge, s.spreads) == ((), (), None, ())
