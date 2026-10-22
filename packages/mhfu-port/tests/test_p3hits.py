# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from mhfu.files import Extracted
from mhfu.memory import Image
from mhfu.mips import Code
from mhfu.overlay import Overlay
from mhfu_port import p3hits, p3moveset
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


def species(phase_one: Callable[[Asm], None], phase_zero: Callable[[Asm], None]) -> Code:
    """A tick that runs `phase_zero`, then `phase_one` until it returns or ends the pair."""
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


def walk(code: Code, lengths=LENGTHS) -> Walk:
    tick = BASE
    ms = SimpleNamespace(
        code=code,
        ovl=None,
        vtable=None,
        tick_entry=tick,
        translator={},
        table_ids=lambda site, entry: None,
    )
    return Walk(ms, HITS, lengths)  # type: ignore[arg-type]


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
