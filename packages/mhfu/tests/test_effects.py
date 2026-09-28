import struct

from mhfu import addresses as a
from mhfu import files
from mhfu.em import effects
from mhfu.memory import Image
from mhfu.mips import Code

BASE = 0x0010_0000
R = {n: i for i, n in enumerate("zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7".split())}
R |= {"s0": 16, "v1": 3, "sp": 29, "ra": 31}


def imm(op, rt, rs, value):
    return op << 26 | R[rs] << 21 | R[rt] << 16 | value & 0xFFFF


def addiu(rt, rs, v):
    return imm(0x09, rt, rs, v)


def addu(rd, rs, rt):
    return R[rs] << 21 | R[rt] << 16 | R[rd] << 11 | 0x21


def lw(rt, off, rs):
    return imm(0x23, rt, rs, off)


def beq(rs, rt, to, at, likely=False):
    return imm(0x14 if likely else 0x04, rt, rs, (to - at - 4) >> 2)


def jal(target):
    return 3 << 26 | (target >> 2) & 0x03FF_FFFF


NOP, RET = 0, R["ra"] << 21 | 0x08
PROLOGUE = addiu("sp", "sp", -0x10)


def build(*words, base=BASE):
    blob = struct.pack(f"<{len(words)}I", *words)
    return Code(Image(blob, base), range(base, base + 4 * len(words)))


def va(i, base=BASE):
    return base + 4 * i


def test_spawns():
    code = build(
        PROLOGUE,  # 0 a handler
        addiu("a1", "zero", 12),
        addu("a2", "a1", "zero"),
        jal(va(20)),  # 3 through the wrapper: id 12 at bone 12
        NOP,
        addiu("a1", "zero", 90),  # frame
        addiu("a3", "zero", 60),
        jal(a.EFFECT_SPAWN_FRAMED),  # 7
        addu("t0", "zero", "zero"),  # bone 0, in the delay slot
        lw("a1", 0, "s0"),
        jal(a.EFFECT_SPAWN_AT),  # 10 a computed id
        addiu("a2", "zero", 3),
        RET,
        NOP,
        *[NOP] * 6,
        PROLOGUE,  # 20 the wrapper
        jal(a.EFFECT_SPAWN),
        NOP,
        RET,
        NOP,
    )
    assert effects.wrappers(code) == [va(20)]
    local, framed, at, inner = effects.spawns(code)
    assert (local.via, local.id, local.bone, local.fn) == ("local", 12, 12, va(0))
    assert (framed.via, framed.id, framed.bone, framed.frame) == ("framed", 60, 0, 90)
    assert (at.via, at.id, at.bone, at.frame) == ("positional", None, 3, None)
    assert (inner.via, inner.fn) == ("biased", va(20))
    census = effects.census({7: [local, framed, at], 75: [local]})
    assert census == {12: {7: 1, 75: 1}, 60: {7: 1}}


def test_long_caller_is_no_wrapper():
    code = build(PROLOGUE, jal(a.EFFECT_SPAWN), *[NOP] * effects.WRAPPER_SIZE, RET, NOP)
    assert effects.wrappers(code) == []


def test_bias():
    def at(i):
        return va(i, a.EFFECT_SPAWN)

    code = build(
        PROLOGUE,
        addu("s0", "a1", "zero"),
        addiu("v0", "zero", 80),
        beq("v1", "v0", at(8), at(3), likely=True),  # 3
        addiu("s0", "s0", 30),
        addiu("v0", "zero", 13),
        beq("v1", "v0", at(9), at(6)),  # 6
        NOP,
        NOP,
        addiu("s0", "s0", 100),  # 9
        RET,
        NOP,
        base=a.EFFECT_SPAWN,
    )
    assert effects.bias(code) == {80: 30, 13: 100}


def test_game(game):
    task = game.overlay(files.GAME_TASK)
    bias = effects.bias(Code(task, task.text))
    assert bias and set(bias.values()) <= {30, 100} and not set(bias) & set(files.EM_SPECIES)
    tigrex = game.em(75)
    code = Code(tigrex, tigrex.text)
    sites = effects.spawns(code)
    assert len(effects.wrappers(code)) == 1
    assert sum(s.id is not None for s in sites) > 0.9 * len(sites)
    assert {s.via for s in sites} >= {"local", "framed"}
