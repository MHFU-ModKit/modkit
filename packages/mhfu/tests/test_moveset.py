# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct

from mhfu import addresses as a
from mhfu import files
from mhfu.em.moveset import Moveset, Walker, known
from mhfu.memory import Image
from mhfu.mips import Code, Gpr
from modkit_testing import mips as asm

BASE = 0x0010_0000


def va(i):
    return BASE + 4 * i


def code(*words):
    return Code(Image(struct.pack(f"<{len(words)}I", *words), BASE), range(BASE, va(len(words))))


SPECIES_FORK = code(
    asm.lbu("v0", a.ENTITY.SPECIES, "a0"),
    asm.li("v1", 81),
    asm.bne("v0", "v1", va(5), va(2)),
    asm.li("a1", 46),  # the delay slot runs either way
    asm.li("a1", 54),
    asm.jal(a.ACTION_EXECUTOR),  # 5
    asm.NOP,
    asm.RET,
    asm.NOP,
)


def test_fork():
    w = Walker(SPECIES_FORK, {a.ACTION_EXECUTOR: "anim"})
    assert sorted(s.args[0] for s in w.run(BASE)) == [46, 54]
    fixed = w.run(BASE, facts=known({a.ENTITY.SPECIES: 75}))
    assert [s.args[0] for s in fixed] == [46]
    guards = {s.args[0]: s.guards for s in w.run(BASE)}
    assert guards == {46: ("species!=81",), 54: ("species==81",)}


def test_counted_loop_falls_through():
    loop = code(
        asm.li("s0", 0),
        asm.addiu("s0", "s0", 1),  # 1
        asm.li("v1", 3),
        asm.bne("s0", "v1", va(1), va(3)),
        asm.NOP,
        asm.li("a1", 46),
        asm.jal(a.ACTION_EXECUTOR),
        asm.NOP,
        asm.RET,
        asm.NOP,
    )
    assert [s.args[0] for s in Walker(loop, {a.ACTION_EXECUTOR: "anim"}).run(BASE)] == [46]


def test_tail_call_through_vtable():
    helper = code(
        asm.lbu("v0", a.ENTITY.PHASE, "a0"),
        asm.li("v1", 3),
        asm.bne("v0", "v1", va(10), va(2)),
        asm.NOP,
        asm.lw("t9", 0, "a0"),
        asm.li("a1", 0),
        asm.li("a2", 6),
        asm.lw("t9", 0x88, "t9"),
        asm.jr("t9"),
        asm.li("a3", 1),  # 9 the delay slot sets the last argument
        asm.RET,
        asm.NOP,
    )
    (site,) = Walker(helper, {}, methods={0x88: "enter"}).run(BASE)
    assert (site.site, site.kind, site.args, site.guards) == (
        va(8),
        "enter",
        (0, 6, 1),
        ("phase==3",),
    )


def test_facts():
    twice = code(
        asm.lbu("v0", 0x280, "a0"),
        asm.bne("v0", "zero", va(4), va(1)),
        asm.NOP,
        asm.li("a1", 1),
        asm.lbu("v1", 0x280, "a0"),  # 4 the same cell again: its outcome is known on each path
        asm.bne("v1", "zero", va(8), va(5)),
        asm.NOP,
        asm.li("a1", 2),
        asm.jal(a.ACTION_EXECUTOR),  # 8
        asm.NOP,
        asm.RET,
        asm.NOP,
    )
    w = Walker(twice, {a.ACTION_EXECUTOR: "anim"}, guards=False)
    assert {s.args[0] for s in w.run(BASE)} == {2, None}  # never 1: both tests agree
    stored = code(asm.sb("zero", 0x280, "a0"), *(twice.at(va(i)).getRaw() for i in range(12)))
    w = Walker(stored, {a.ACTION_EXECUTOR: "anim"}, guards=False)
    assert [s.args[0] for s in w.run(BASE)] == [2]


def test_tail_call_and_probe():
    thunk = code(
        asm.j(va(4)),  # a tail call: the walk goes on at the same depth
        asm.li("a1", 7),
        asm.jal(va(4)),  # never runs; makes va(4) a function
        asm.NOP,
        asm.sb("a1", 0x280, "a0"),  # 4
        asm.jal(a.ACTION_EXECUTOR),
        asm.NOP,
        asm.RET,
        asm.NOP,
    )
    w = Walker(thunk, {a.ACTION_EXECUTOR: "anim"}, depth=0, probes={va(4): Gpr.a1})
    found = {(s.kind, s.args[0], s.via) for s in w.run(BASE)}
    assert found == {("probe", 7, (va(4),)), ("anim", 7, (va(4),))}


def test_float_constants():
    thing = a.ACTION_EXECUTOR
    words = [
        asm.lui("v0", asm.hi(BASE + 0x40)),
        asm.lwc1(1, asm.lo(BASE + 0x40), "v0"),
        asm.lwc1(2, asm.lo(BASE + 0x44), "v0"),
        asm.mul_s(0, 1, 2),
        asm.lwc1(3, asm.lo(BASE + 0x48), "v0"),
        asm.add_s(0, 0, 3),
        asm.trunc_w_s(0, 0),
        asm.mfc1("a1", 0),
        asm.jal(thing),
        asm.NOP,
        asm.RET,
        asm.NOP,
    ]
    pad = [0] * (16 - len(words))
    data = [struct.unpack("<I", struct.pack("<f", x))[0] for x in (1.5, 8192.0, 0.5)]
    mem = Image(struct.pack(f"<{16 + 3}I", *words, *pad, *data), BASE)
    c = Code(mem, range(BASE, va(len(words))))
    (site,) = Walker(c, {thing: "anim"}).run(BASE)
    assert site.args[0] == 12288


def test_float_returned_by_a_call():
    step, thing = 0x0020_0000, a.ACTION_EXECUTOR
    words = [
        asm.jal(step),
        asm.NOP,
        asm.mul_s(0, 0, 0),  # $f0 squared
        asm.trunc_w_s(0, 0),
        asm.mfc1("a1", 0),
        asm.jal(thing),
        asm.NOP,
        asm.RET,
        asm.NOP,
    ]
    c = code(*words)
    assert [s.args[0] for s in Walker(c, {thing: "anim"}, returns={step: 3.0}).run(BASE)] == [9]
    assert [s.args[0] for s in Walker(c, {thing: "anim"}).run(BASE)] == [None]


def test_calls_of_one_function_have_their_own_guards():
    gate, thing = 0x0020_0000, a.ACTION_EXECUTOR
    twice = code(
        asm.jal(gate),
        asm.NOP,
        asm.bne("v0", "zero", va(5), va(3)),
        asm.NOP,
        asm.li("a1", 1),
        asm.jal(gate),  # 5
        asm.NOP,
        asm.bne("v0", "zero", va(10), va(8)),
        asm.NOP,
        asm.li("a1", 2),
        asm.jal(thing),  # 10
        asm.NOP,
        asm.RET,
        asm.NOP,
    )
    w = Walker(twice, {thing: "anim"}, names={gate: "gate@{site:x}"})
    found = {s.guards for s in w.run(BASE)}
    assert (f"gate@{va(0):x}", f"!gate@{va(5):x}") in found


def test_decide_answers_a_call_and_a_branch():
    gate, thing = 0x0020_0000, a.ACTION_EXECUTOR
    c = code(
        asm.jal(gate),
        asm.NOP,
        asm.beq("v0", "zero", va(6), va(2)),  # the gate said no: skip the first play
        asm.NOP,
        asm.li("a1", 1),
        asm.jal(thing),
        asm.NOP,
        asm.lbu("v1", 0x280, "a0"),  # 7 a cell no fact names
        asm.bne("v1", "zero", va(12), va(8)),
        asm.NOP,
        asm.li("a1", 2),
        asm.jal(thing),
        asm.NOP,  # 12
        asm.RET,
        asm.NOP,
    )
    w = Walker(c, {thing: "anim"}, guards=False)
    both = {s.args[0] for s in w.run(BASE)}
    assert both == {1, 2}

    def decide(yes: int, taken: int):
        def answer(pc, target, args):
            return {va(0): yes, va(8): taken}.get(pc) if target in (gate, None) else None

        return answer

    assert [s.args[0] for s in w.run(BASE, decide=decide(1, 1))] == [1]
    assert [s.args[0] for s in w.run(BASE, decide=decide(0, 0))] == [2]
    assert {s.args[0] for s in w.run(BASE)} == both  # a decided run leaves nothing behind


def test_game(game):
    for species in files.EM_SPECIES:
        ms = Moveset(game.em(species))
        assert ms.tick is not None and len(ms.mains) == 8
        assert ms.pairs and all(
            p.handler is None or p.handler in ms.code.text for p in ms.pairs.values()
        )
    ms = Moveset(game.em(75))
    assert len(ms.pairs) == 242 and ms.tick_entry == ms.code.function(ms.tick.jr).start
    assert ms.animations(ms.pairs[0, 5]).ids == (46, 54)
    assert ms.animations(ms.pairs[0, 8]).ids == (24,)  # the case passes a1 = 0
    assert ms.pairs[0, 9].args == {"a1": 1}
