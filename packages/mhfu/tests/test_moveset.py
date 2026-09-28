import struct

from mhfu import addresses as a
from mhfu import files
from mhfu.em.moveset import Moveset, Walker, known
from mhfu.memory import Image
from mhfu.mips import Code

BASE = 0x0010_0000
R = {n: i for i, n in enumerate("zero at v0 v1 a0 a1 a2 a3 t0 t1".split())} | {"t9": 25, "ra": 31}


def imm(op, rt, rs, v):
    return op << 26 | R[rs] << 21 | R[rt] << 16 | v & 0xFFFF


def li(rt, v):
    return imm(0x09, rt, "zero", v)


def lbu(rt, off, rs="a0"):
    return imm(0x24, rt, rs, off)


def lw(rt, off, rs):
    return imm(0x23, rt, rs, off)


def sb(rt, off, rs="a0"):
    return imm(0x28, rt, rs, off)


def bne(rs, rt, to, at):
    return imm(0x05, rt, rs, (to - at - 4) >> 2)


def jal(target):
    return 3 << 26 | target >> 2 & 0x03FF_FFFF


def jr(rs):
    return R[rs] << 21 | 0x08


NOP = 0


def va(i):
    return BASE + 4 * i


def code(*words):
    return Code(Image(struct.pack(f"<{len(words)}I", *words), BASE), range(BASE, va(len(words))))


SPECIES_FORK = code(
    lbu("v0", a.ENTITY.SPECIES),
    li("v1", 81),
    bne("v0", "v1", va(5), va(2)),
    li("a1", 46),  # the delay slot runs either way
    li("a1", 54),
    jal(a.ACTION_EXECUTOR),  # 5
    NOP,
    jr("ra"),
    NOP,
)


def test_fork():
    w = Walker(SPECIES_FORK, {a.ACTION_EXECUTOR: "anim"})
    assert sorted(s.args[0] for s in w.run(BASE)) == [46, 54]
    fixed = w.run(BASE, facts=known({a.ENTITY.SPECIES: 75}))
    assert [s.args[0] for s in fixed] == [46]
    guards = {s.args[0]: s.guards for s in w.run(BASE)}
    assert guards == {46: ("species!=81",), 54: ("species==81",)}


def test_tail_call_through_vtable():
    helper = code(
        lbu("v0", a.ENTITY.PHASE),
        li("v1", 3),
        bne("v0", "v1", va(10), va(2)),
        NOP,
        lw("t9", 0, "a0"),
        li("a1", 0),
        li("a2", 6),
        lw("t9", 0x88, "t9"),
        jr("t9"),
        li("a3", 1),  # 9 the delay slot sets the last argument
        jr("ra"),
        NOP,
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
        lbu("v0", 0x280),
        bne("v0", "zero", va(4), va(1)),
        NOP,
        li("a1", 1),
        lbu("v1", 0x280),  # 4 the same cell again: its outcome is known on each path
        bne("v1", "zero", va(8), va(5)),
        NOP,
        li("a1", 2),
        jal(a.ACTION_EXECUTOR),  # 8
        NOP,
        jr("ra"),
        NOP,
    )
    w = Walker(twice, {a.ACTION_EXECUTOR: "anim"}, guards=False)
    assert {s.args[0] for s in w.run(BASE)} == {2, None}  # never 1: both tests agree
    stored = code(sb("zero", 0x280), *(twice.at(va(i)).getRaw() for i in range(12)))
    w = Walker(stored, {a.ACTION_EXECUTOR: "anim"}, guards=False)
    assert [s.args[0] for s in w.run(BASE)] == [2]


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
