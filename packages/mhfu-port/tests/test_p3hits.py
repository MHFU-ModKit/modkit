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


def _r(funct: int, rd: str, rs: str, rt: str) -> int:
    return asm.gpr(rs) << 21 | asm.gpr(rt) << 16 | asm.gpr(rd) << 11 | funct


def subu(rd: str, rs: str, rt: str) -> int:
    return _r(0x23, rd, rs, rt)


def negu(rd: str, rt: str) -> int:
    return _r(0x23, rd, "zero", rt)


def max_(rd: str, rs: str, rt: str) -> int:
    return _r(0x2C, rd, rs, rt)


def seh(rd: str, rt: str) -> int:
    return 0x1F << 26 | asm.gpr(rt) << 16 | asm.gpr(rd) << 11 | 0x18 << 6 | 0x20


def sra(rd: str, rt: str, sa: int) -> int:
    return asm.gpr(rt) << 16 | asm.gpr(rd) << 11 | sa << 6 | 0x03


def slti(rt: str, rs: str, imm: int) -> int:
    return 0x0A << 26 | asm.gpr(rs) << 21 | asm.gpr(rt) << 16 | imm & 0xFFFF


SIDES = {61: Length(100.0), 62: Length(100.0)}
"""Two clips of 100 frames: fifty AI frames."""
EIGHT = {61: Length(16.0), 62: Length(16.0)}
"""Two clips of 16 frames: eight AI frames."""


def wait_for_the_clip(a: Asm) -> None:
    """Until the clip ends, then the pair's end."""
    a(asm.addu("a0", "s0", "zero"))
    a.call(p3hits.CLIP_ENDED)
    a.branch("beq", "v0", "zero", "done")
    a.call(LEAVE)


def sides(a: Asm) -> None:
    """Clip 61 where the side rule says left, else 62."""
    a(asm.addu("a0", "s0", "zero"))
    a.call(p3hits.SIDE)
    a.branch("beq", "v0", "zero", "right")
    play(a, 61)
    a.jump("played")
    a.label("right")
    play(a, 62)
    a.label("played")


def bearing_off(a: Asm) -> None:
    """The angle of the target off the yaw, a half turn either way, in v1, and its size in v0."""
    a(asm.addu("a0", "zero", "zero"), asm.addu("a1", "zero", "zero"))
    a.call(p3hits.ANGLE)
    a(asm.lw("v1", 0x74, "s0"), subu("v1", "v0", "v1"), seh("v1", "v1"))
    a(negu("v0", "v1"), max_("v0", "v1", "v0"), seh("v0", "v0"))


def test_a_handler_that_asks_for_no_bearing_has_one_arm_and_its_plain_plan():
    w = walk(species(wait_for_the_clip, lambda a: play(a, 61)))
    (arm,) = w.arms(pair())
    assert arm.arcs == ((0, 255),) and arm.plan == w.plan(pair()) and not arm.plan.reads


def test_the_side_rule_gives_a_clip_to_each_half_turn():
    left, right = walk(species(wait_for_the_clip, sides), SIDES).arms(pair())
    assert (left.arcs, right.arcs) == (((0, 127),), ((128, 255),))
    assert [s.clip for s in left.plan.steps] == [61] and [s.clip for s in right.plan.steps] == [62]
    assert (left.plan.bearing, right.plan.bearing) == (63 * 256 + 1, 191 * 256 + 1 - 0x10000)
    assert left.plan.reads and right.plan.complete


def test_a_compare_on_the_angle_splits_where_it_is_made_to_a_256th_of_a_turn():
    def quarter(a: Asm) -> None:
        bearing_off(a)
        a(slti("v1", "v0", 0x4000))
        a.branch("beq", "v1", "zero", "far")
        play(a, 61)
        a.jump("played")
        a.label("far")
        play(a, 62)
        a.label("played")

    near, far = walk(species(wait_for_the_clip, quarter), SIDES).arms(pair())
    assert near.arcs == ((0, 63), (192, 255)) and far.arcs == ((64, 191),)
    assert [s.clip for s in near.plan.steps] == [61] and [s.clip for s in far.plan.steps] == [62]


def test_a_handler_that_plays_one_clip_at_every_bearing_has_one_arm_and_its_plain_plan():
    def same(a: Asm) -> None:
        bearing_off(a)
        play(a, 61)

    w = walk(species(wait_for_the_clip, same), SIDES)
    (arm,) = w.arms(pair())
    assert arm.arcs == ((0, 255),) and arm.plan == w.plan(pair()) and arm.plan.reads


def adds_to_the_yaw(a: Asm, per_tick: str) -> None:
    """Each tick adds to the yaw: 100 units, or an eighth of the angle of the target off it that
    the first tick kept at 0xF40."""
    if per_tick == "fixed":
        a(asm.li("v0", 100))
    else:
        a(asm.lw("v0", 0xF40, "s0"), sra("v0", "v0", 3))
    a(asm.lw("v1", 0x74, "s0"), asm.addu("v1", "v1", "v0"), asm.sw("v1", 0x74, "s0"))
    wait_for_the_clip(a)


def keeps_the_angle(a: Asm) -> None:
    sides(a)
    bearing_off(a)
    a(asm.sw("v1", 0xF40, "s0"))


def test_a_turn_that_comes_to_the_angle_of_the_target_is_an_aim_turn_on_its_curve():
    left, right = walk(
        species(lambda a: adds_to_the_yaw(a, "eighth"), keeps_the_angle), EIGHT
    ).arms(pair())
    for arm, size in ((left, 0x7FFF), (right, -0x7FFF)):
        (step,) = arm.plan.steps
        assert step.aim and step.code[0] == 0 and step.code[-1] == size
        # the first tick's turn is in the second value, as a step's travel is
        assert list(step.code) == pytest.approx([0, *(k * size / 8 for k in range(2, 9))], abs=2)


def test_a_turn_that_is_the_same_at_every_bearing_is_a_curve_that_stands():
    left, right = walk(species(lambda a: adds_to_the_yaw(a, "fixed"), sides), EIGHT).arms(pair())
    for arm in (left, right):
        (step,) = arm.plan.steps
        assert not step.aim and step.code[-1] == 800


def test_the_blend_a_handler_hands_over_with_is_the_last_step_s():
    def hands_over(a: Asm) -> None:
        a(asm.li("a1", 7), asm.li("a2", 203), asm.li("a3", 6), asm.addu("a0", "s0", "zero"))
        a.call(p3hits.CARRY)

    plan = walk(species(hands_over, sides), SIDES).arms(pair())[0].plan
    assert plan.leaves == ((7, 203),) and plan.complete
    assert [s.hand for s in plan.steps] == [6]


def test_a_clip_blends_in_for_the_frames_it_was_played_with():
    def turns_while_blending(a: Asm) -> None:
        a(asm.addu("a0", "s0", "zero"))
        a.call(p3hits.BLEND_IN)
        a.branch("beq", "v0", "zero", "settled")
        a(asm.li("a1", 100), asm.addu("a0", "s0", "zero"))
        a.call(p3hits.TURN)
        a.label("settled")
        wait_for_the_clip(a)

    plan = walk(species(turns_while_blending, lambda a: play(a, 61))).plan(pair())
    (step,) = plan.steps
    assert step.blend == 4 and step.turns == (p3hits.Turn(100, (0.0, 8.0)),)


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


@pytest.mark.parametrize(
    ("pair_", "clip", "rate"),
    [
        ((7, 12), 203, 546),
        ((7, 86), 213, 728),
        ((7, 89), 247, 1365),
        ((7, 99), 231, 637),
        ((7, 16), 30, 182),
        ((7, 150), 200, 1001),  # the word at +0x540 reads 2: 5.5 degrees, not 4
        ((7, 17), 253, 1001),
    ],
)
def test_zinogre_turn_rates_are_the_donors_at_clip_speed_one(plans, pair_, clip, rate):
    """`trunc(deg * ent[0xAC4] * 65536 / 360 + 0.5)`, every site resolved."""
    assert step_of(plans(*pair_), clip).turns[0].rate == rate


def test_a_step_without_code_motion_carries_none(plans):
    for s in plans(7, 22).steps[:1]:
        assert (s.travel, s.lift, s.lunge, s.spreads) == ((), (), None, ())


def _d(degrees: float) -> int:
    """Degrees to the left of the facing as the YAW units the walk takes."""
    d = round(degrees / 360 * 0x10000)
    return d - 0x10000 if d >= 0x8000 else d


def arms_of(zinogre, main: int, sub: int) -> tuple[p3hits.Arm, ...]:
    w, ms = zinogre
    return w.arms(ms.pairs[main, sub])


def test_zinogre_turns_in_place_by_the_bearing_of_the_target_with_the_clips_9_8_38_37(zinogre):
    w, ms = zinogre
    pair_ = ms.pairs[2, 26]
    for degrees, clip in [(45, 9), (-45, 8), (150, 38), (-150, 37)]:
        plan = w.plan(pair_, _d(degrees))
        assert [s.clip for s in plan.steps] == [clip], degrees
    arms = arms_of(zinogre, 2, 26)
    assert [a.arcs for a in arms] == [((0, 63),), ((64, 127),), ((128, 191),), ((192, 255),)]
    assert [a.plan.steps[0].clip for a in arms] == [9, 38, 37, 8]


def test_zinogre_turns_in_place_turn_exactly_the_angle_along_the_curves_of_90_and_180(zinogre):
    arms = arms_of(zinogre, 2, 26)
    steps = [a.plan.steps[0] for a in arms]
    assert all(s.aim for s in steps)
    assert [s.code[-1] for s in steps] == [0x4000, 0x7FFF, -0x7FFF, -0x4000]
    assert all(s.turns == () and s.spreads == () for s in steps)


def test_zinogre_wrappers_split_at_85_86_and_170_171_and_hand_over_with_the_blend(zinogre):
    arms = arms_of(zinogre, 7, 23)
    assert [a.arcs for a in arms] == [((0, 85),), ((86, 127),), ((128, 170),), ((171, 255),)]
    assert [(a.plan.steps[0].clip, a.plan.steps[0].hand) for a in arms] == [
        (9, 6),
        (38, 10),
        (37, 10),
        (8, 6),
    ]
    assert all(a.plan.leaves == ((7, 203),) and not a.plan.steps[0].aim for a in arms)
    assert [a.plan.steps[0].ticks * 2 for a in arms] == [52, 62, 62, 52]  # where it hands over


def test_zinogre_wrappers_of_one_handler_share_their_arms(zinogre):
    first, other = arms_of(zinogre, 7, 23), arms_of(zinogre, 7, 38)
    assert [a.arcs for a in first] == [a.arcs for a in other]
    assert [a.plan.steps for a in first] == [a.plan.steps for a in other]
    assert other[0].plan.leaves == ((7, 207),) and first[0].plan.leaves == ((7, 203),)


def test_zinogre_turns_by_the_side_rule_between_two_clips_of_a_fixed_twenty_degrees(zinogre):
    left, right = arms_of(zinogre, 1, 1)
    assert (left.arcs, right.arcs) == (((0, 127),), ((128, 255),))
    assert [s.plan.steps[0].clip for s in (left, right)] == [11, 39]
    assert [s.plan.steps[0].spreads for s in (left, right)] == [
        (p3motion.Spread(0xE39, (0.0, 164.0)),),
        (p3motion.Spread(-0xE39, (0.0, 164.0)),),
    ]
    assert not any(s.plan.steps[0].aim for s in (left, right))


def test_zinogre_handlers_that_ignore_the_bearing_have_one_arm(zinogre):
    for key in [(7, 0), (7, 22), (7, 12)]:
        (arm,) = arms_of(zinogre, *key)
        assert arm.arcs == ((0, 255),) and arm.plan.bearing is None


def test_zinogre_blends_in_for_the_frames_it_plays_a_clip_with(plans):
    for key in [(7, 0), (7, 236)]:
        first = plans(*key).steps[0]
        assert first.blend == 4
        assert [(t.rate, t.window) for t in first.turns] == [(1092, (0, 24)), (455, (26, 38))]
