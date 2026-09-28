import struct

import pytest
from mhfu import files
from mhfu.memory import Image, Unmapped
from mhfu.mips import Code, Fpr, Gpr, decode, is_prologue, writes

BASE = 0x0010_0000
REGS = "zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 s0 s1 s2 s3 s4 s5 s6 s7 t8 t9".split()
R = {name: i for i, name in enumerate([*REGS, "k0", "k1", "gp", "sp", "fp", "ra"])}


# a few encoders, enough to write test code
def imm(op, rt, rs, value):
    return op << 26 | R[rs] << 21 | R[rt] << 16 | value & 0xFFFF


def reg(fn, rd="zero", rs="zero", rt="zero", sa=0):
    return R[rs] << 21 | R[rt] << 16 | R[rd] << 11 | sa << 6 | fn


def lui(rt, v):
    return imm(0x0F, rt, "zero", v)


def addiu(rt, rs, v):
    return imm(0x09, rt, rs, v)


def ori(rt, rs, v):
    return imm(0x0D, rt, rs, v)


def lw(rt, off, rs):
    return imm(0x23, rt, rs, off)


def lbu(rt, off, rs):
    return imm(0x24, rt, rs, off)


def sltiu(rt, rs, v):
    return imm(0x0B, rt, rs, v)


def beq(rs, rt, to, at):
    return imm(0x04, rt, rs, (to - at - 4) >> 2)


def jal(target):
    return 3 << 26 | (target >> 2) & 0x03FF_FFFF


def j(target):
    return 2 << 26 | (target >> 2) & 0x03FF_FFFF


def addu(rd, rs, rt):
    return reg(0x21, rd, rs, rt)


def sll(rd, rt, sa):
    return reg(0x00, rd, rt=rt, sa=sa)


def jr(rs):
    return reg(0x08, rs=rs)


def jalr(rs):
    return reg(0x09, "ra", rs)


def mtc1(rt, fs):
    return 0x11 << 26 | 4 << 21 | R[rt] << 16 | fs << 11


NOP = 0
PROLOGUE = addiu("sp", "sp", -0x10)
RET = jr("ra")


def build(*words, data=b""):
    blob = struct.pack(f"<{len(words)}I", *words) + data
    return Code(Image(blob, BASE), range(BASE, BASE + 4 * len(words)))


def va(i):
    return BASE + 4 * i


def test_decode_fields():
    ins = decode(addiu("a1", "zero", -2), BASE)
    assert ins.rt == Gpr.a1 and ins.getProcessedImmediate() == -2
    assert is_prologue(decode(PROLOGUE, BASE))
    assert not is_prologue(decode(addiu("sp", "sp", 0x10), BASE))
    assert writes(decode(jal(BASE), BASE), Gpr.ra)
    assert writes(decode(mtc1("at", 12), BASE), Fpr.fa0)


def test_functions():
    code = build(
        PROLOGUE,  # 0 caller
        jal(va(6)),
        NOP,
        RET,
        addiu("sp", "sp", 0x10),
        NOP,  # 5 padding
        RET,  # 6 leaf, found by the jal
        addiu("v0", "zero", 1),
        lui("v1", 1),  # 8 scheduled ahead of its prologue
        PROLOGUE,
        RET,
        NOP,
    )
    assert code.entries == (va(0), va(6), va(8))
    assert code.function(va(2)) == range(va(0), va(6))
    assert code.function(va(9)) == range(va(8), va(12))
    with pytest.raises(Unmapped):
        code.at(va(12))


def test_second_frame_is_not_a_function():
    code = build(
        PROLOGUE,
        beq("a0", "zero", va(3), va(1)),
        NOP,
        PROLOGUE,  # 3 a branch target: inside the function
        RET,
        NOP,
    )
    assert code.entries == (va(0),)


def test_pairs():
    code = build(
        lui("a0", 0x0012),
        addiu("a0", "a0", -0x10),
        lw("a1", 8, "a0"),  # a0 now holds the address: a struct offset, not a %lo
        lui("t0", 0x0013),
        lw("a2", 0x20, "t0"),  # one lui shared by two loads
        lw("a3", 0x24, "t0"),
        lui("v0", 0x4270),
        ori("v0", "v0", 0x1234),  # a constant
        RET,
        NOP,
    )
    found = {(p.hi, p.lo, p.value) for p in code.pairs}
    assert found == {
        (va(0), va(1), 0x0011_FFF0),
        (va(3), va(4), 0x0013_0020),
        (va(3), va(5), 0x0013_0024),
        (va(6), va(7), 0x4270_1234),
    }


def switch_code(bias):
    table = va(16)
    words = [
        PROLOGUE,
        lbu("v0", 0x298, "a0"),
        addiu("v0", "v0", -bias),
        sltiu("at", "v0", 3),
        beq("at", "zero", va(13), va(4)),
        NOP,
        lui("a1", table >> 16),
        addiu("a1", "a1", table & 0xFFFF),
        sll("v1", "v0", 2),
        addu("v1", "v1", "a1"),
        lw("v1", 0, "v1"),
        jr("v1"),
        NOP,
        RET,  # 13 default and every case
        addiu("sp", "sp", 0x10),
        NOP,
    ]
    return build(*words, data=struct.pack("<4I", va(13), va(13), va(13), 0xFFFF))


@pytest.mark.parametrize("bias", [0, 2])
def test_switch(bias):
    code = switch_code(bias)
    s = code.switch(va(11))
    assert s is not None
    assert s.table == va(16) and s.targets == (va(13),) * 3 and s.first == bias
    assert s.operand is not None and s.operand.getProcessedImmediate() == 0x298
    assert va(13) in code.labels
    assert all(c.site != va(11) for c in code.calls)


def test_constant_arguments():
    code = build(
        PROLOGUE,
        addiu("a1", "zero", 5),
        lui("a2", 0x0012),
        addiu("a2", "a2", 0x40),
        addu("s0", "a1", "zero"),  # 4 a move
        jal(va(20)),
        addiu("a3", "zero", 7),  # 6 the delay slot runs before the call
        addu("a1", "s0", "zero"),
        jal(va(20)),
        NOP,
        lui("at", 0x4270),
        mtc1("at", 12),
        jal(va(20)),
        NOP,
        RET,
        NOP,
    )
    assert code.constant(va(5), Gpr.a1) == 5
    assert code.constant(va(5), Gpr.a2) == 0x0012_0040
    assert code.constant(va(5), Gpr.a3) == 7
    assert code.source(va(5), Gpr.a3) == code.at(va(6))
    assert code.constant(va(8), Gpr.a1) == 5  # s0 survives the call
    assert code.constant(va(8), Gpr.a2) is None  # a2 does not
    assert code.source(va(8), Gpr.a2) == code.at(va(5))
    assert code.constant(va(12), Fpr.fa0) == 0x4270_0000
    assert code.constant(va(5), Gpr.a0) is None  # from the caller


def test_constant_stops_at_a_label():
    code = build(
        PROLOGUE,
        addiu("a1", "zero", 1),
        beq("a0", "zero", va(5), va(2)),
        NOP,
        addiu("a1", "zero", 2),
        jal(va(8)),  # 5 reached with a1 = 1 or 2
        NOP,
        RET,
        NOP,
    )
    assert code.constant(va(5), Gpr.a1) is None
    assert code.constant(va(4), Gpr.a1) == 1  # past the branch, on its fall-through


def test_calls():
    code = build(
        PROLOGUE,
        lw("t9", 0, "a0"),
        lw("t9", 0x88, "t9"),
        jalr("t9"),
        NOP,
        jal(va(9)),
        NOP,
        j(va(9)),  # a tail call
        NOP,
        RET,  # 9
        NOP,
    )
    virtual, direct, tail = code.calls
    assert (virtual.site, virtual.slot, virtual.target) == (va(3), 0x88, None)
    assert (direct.target, direct.link) == (va(9), True)
    assert (tail.target, tail.link) == (va(9), False)
    assert code.callers(va(9)) == [va(5), va(7)]


def test_game_code(game):
    for species in files.EM_SPECIES:
        ovl = game.em(species)
        code = Code(ovl, ovl.text)
        assert all(ins.isValid() for ins in code)
        assert set(code.entries) >= {
            c.target for c in code.calls if c.link and c.target in ovl.text
        }
        for s in code.switches.values():
            assert s.targets and all(t in code.function(s.jr) for t in s.targets)
