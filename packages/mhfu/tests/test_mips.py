# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct

import pytest
from mhfu import files
from mhfu.memory import Image, Unmapped
from mhfu.mips import Code, Fpr, Gpr, decode, is_prologue, move_source, writes
from modkit_testing import mips as asm

BASE = 0x0010_0000
PROLOGUE = asm.addiu("sp", "sp", -0x10)


def build(*words, data=b""):
    blob = struct.pack(f"<{len(words)}I", *words) + data
    return Code(Image(blob, BASE), range(BASE, BASE + 4 * len(words)))


def va(i):
    return BASE + 4 * i


def test_decode_fields():
    ins = decode(asm.li("a1", -2), BASE)
    assert ins.rt == Gpr.a1 and ins.getProcessedImmediate() == -2
    assert is_prologue(decode(PROLOGUE, BASE))
    assert not is_prologue(decode(asm.addiu("sp", "sp", 0x10), BASE))
    assert writes(decode(asm.jal(BASE), BASE), Gpr.ra)
    assert writes(decode(asm.mtc1("at", 12), BASE), Fpr.fa0)
    assert move_source(decode(asm.or_("a0", "s0", "zero"), BASE)) == Gpr.s0  # or: the move pseudo
    assert move_source(decode(asm.addu("a0", "zero", "s1"), BASE)) == Gpr.s1


def test_functions():
    code = build(
        PROLOGUE,  # 0 caller
        asm.jal(va(6)),
        asm.NOP,
        asm.RET,
        asm.addiu("sp", "sp", 0x10),
        asm.NOP,  # 5 padding
        asm.RET,  # 6 leaf, found by the jal
        asm.li("v0", 1),
        asm.lui("v1", 1),  # 8 scheduled ahead of its prologue
        PROLOGUE,
        asm.RET,
        asm.NOP,
    )
    assert code.entries == (va(0), va(6), va(8))
    assert code.function(va(2)) == range(va(0), va(6))
    assert code.function(va(9)) == range(va(8), va(12))
    with pytest.raises(Unmapped):
        code.at(va(12))


def test_tail_called_function():
    code = build(
        PROLOGUE,
        asm.li("v0", 1),
        asm.li("v1", 2),  # 2
        asm.j(va(2)),  # into the middle of a run: not a call
        asm.NOP,
        asm.j(va(8)),  # a tail call to the next function
        asm.addiu("sp", "sp", 0x10),
        asm.NOP,  # padding
        asm.RET,  # 8 reached only by the j
        asm.NOP,
    )
    assert code.entries == (va(0), va(8))


def test_second_frame_is_not_a_function():
    code = build(
        PROLOGUE,
        asm.beq("a0", "zero", va(3), va(1)),
        asm.NOP,
        PROLOGUE,  # 3 a branch target: inside the function
        asm.RET,
        asm.NOP,
    )
    assert code.entries == (va(0),)


def test_pairs():
    code = build(
        asm.lui("a0", 0x0012),
        asm.addiu("a0", "a0", -0x10),
        asm.lw("a1", 8, "a0"),  # a0 now holds the address: a struct offset, not a %lo
        asm.lui("t0", 0x0013),
        asm.lw("a2", 0x20, "t0"),  # one lui shared by two loads
        asm.lw("a3", 0x24, "t0"),
        asm.lui("v0", 0x4270),
        asm.ori("v0", "v0", 0x1234),  # a constant
        asm.RET,
        asm.NOP,
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
        asm.lbu("v0", 0x298, "a0"),
        asm.addiu("v0", "v0", -bias),
        asm.sltiu("at", "v0", 3),
        asm.beq("at", "zero", va(13), va(4)),
        asm.NOP,
        asm.lui("a1", asm.hi(table)),
        asm.addiu("a1", "a1", asm.lo(table)),
        asm.sll("v1", "v0", 2),
        asm.addu("v1", "v1", "a1"),
        asm.lw("v1", 0, "v1"),
        asm.jr("v1"),
        asm.NOP,
        asm.RET,  # 13 default and every case
        asm.addiu("sp", "sp", 0x10),
        asm.NOP,
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
    assert code.table(va(10)) == va(16)  # lw v1, 0(table + index)


def test_constant_arguments():
    code = build(
        PROLOGUE,
        asm.li("a1", 5),
        asm.lui("a2", 0x0012),
        asm.addiu("a2", "a2", 0x40),
        asm.addu("s0", "a1", "zero"),  # 4 a move
        asm.jal(va(20)),
        asm.li("a3", 7),  # 6 the delay slot runs before the call
        asm.addu("a1", "s0", "zero"),
        asm.jal(va(20)),
        asm.NOP,
        asm.lui("at", 0x4270),
        asm.mtc1("at", 12),
        asm.jal(va(20)),
        asm.NOP,
        asm.RET,
        asm.NOP,
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
        asm.li("a1", 1),
        asm.beq("a0", "zero", va(5), va(2)),
        asm.NOP,
        asm.li("a1", 2),
        asm.jal(va(8)),  # 5 reached with a1 = 1 or 2
        asm.NOP,
        asm.RET,
        asm.NOP,
    )
    assert code.constant(va(5), Gpr.a1) is None
    assert code.constant(va(4), Gpr.a1) == 1  # past the branch, on its fall-through


def test_virtual_call_after_a_label():
    code = build(
        PROLOGUE,
        asm.beq("a1", "zero", va(4), va(1), likely=True),  # the vptr loads on both paths
        asm.lw("t9", 0, "a0"),
        asm.lw("t9", 0, "a0"),
        asm.lw("t9", 0x28, "t9"),  # 4
        asm.jalr("t9"),
        asm.NOP,
        asm.RET,
        asm.NOP,
    )
    (call,) = code.calls
    assert (call.site, call.slot) == (va(5), 0x28)


def test_calls():
    code = build(
        PROLOGUE,
        asm.lw("t9", 0, "a0"),
        asm.lw("t9", 0x88, "t9"),
        asm.jalr("t9"),
        asm.NOP,
        asm.jal(va(9)),
        asm.NOP,
        asm.j(va(9)),  # a tail call
        asm.NOP,
        asm.RET,  # 9
        asm.NOP,
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
