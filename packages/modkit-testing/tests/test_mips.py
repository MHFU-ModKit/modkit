import pytest
from modkit_testing import mips as asm

rabbitizer = pytest.importorskip("rabbitizer")
Id, Gpr = rabbitizer.InstrId, rabbitizer.RegGprO32
VA = 0x0010_0000


def decode(word, op, va=VA, **regs):
    """The instruction, checked for `op`, no stray bits, and `regs` (rs, rt, rd)."""
    ins = rabbitizer.Instruction(word, va, category=rabbitizer.InstrCategory.R4000ALLEGREX)
    assert ins.isValid() and ins.uniqueId == op
    assert {r: getattr(ins, r) for r in regs} == regs
    return ins


@pytest.mark.parametrize(
    ("word", "op", "regs", "imm"),
    [
        (asm.lui("a0", 0x1234), Id.cpu_lui, {"rt": Gpr.a0}, 0x1234),
        (asm.addiu("a1", "sp", -2), Id.cpu_addiu, {"rs": Gpr.sp, "rt": Gpr.a1}, -2),
        (asm.li("v1", 81), Id.cpu_addiu, {"rs": Gpr.zero, "rt": Gpr.v1}, 81),
        (asm.ori("v0", "a0", 0x8234), Id.cpu_ori, {"rs": Gpr.a0, "rt": Gpr.v0}, 0x8234),
        (asm.sltiu("at", "v0", 3), Id.cpu_sltiu, {"rs": Gpr.v0, "rt": Gpr.at}, 3),
        (asm.lw("t9", 0x88, "a0"), Id.cpu_lw, {"rs": Gpr.a0, "rt": Gpr.t9}, 0x88),
        (asm.lbu("v0", 0x298, "a0"), Id.cpu_lbu, {"rs": Gpr.a0, "rt": Gpr.v0}, 0x298),
        (asm.sw("v0", -4, "sp"), Id.cpu_sw, {"rs": Gpr.sp, "rt": Gpr.v0}, -4),
        (asm.sb("a1", 0x280, "s0"), Id.cpu_sb, {"rs": Gpr.s0, "rt": Gpr.a1}, 0x280),
    ],
)
def test_immediate(word, op, regs, imm):
    assert decode(word, op, **regs).getProcessedImmediate() == imm


@pytest.mark.parametrize(
    ("encode", "op"),
    [
        (asm.beq, Id.cpu_beq),
        (asm.bne, Id.cpu_bne),
        (lambda *a: asm.beq(*a, likely=True), Id.cpu_beql),
        (lambda *a: asm.bne(*a, likely=True), Id.cpu_bnel),
    ],
)
@pytest.mark.parametrize("target", [VA + 0x40, VA - 0x40])
def test_branch(encode, op, target):
    at = VA + 8
    ins = decode(encode("a0", "v1", target, at), op, at, rs=Gpr.a0, rt=Gpr.v1)
    assert ins.getBranchVramGeneric() == target


@pytest.mark.parametrize(("encode", "op"), [(asm.j, Id.cpu_j), (asm.jal, Id.cpu_jal)])
def test_jump(encode, op):
    assert decode(encode(VA + 0x40), op).getInstrIndexAsVram() == VA + 0x40


@pytest.mark.parametrize(
    ("word", "op", "regs"),
    [
        (asm.addu("v1", "v0", "a1"), Id.cpu_addu, {"rd": Gpr.v1, "rs": Gpr.v0, "rt": Gpr.a1}),
        (asm.or_("a0", "s0", "v0"), Id.cpu_or, {"rd": Gpr.a0, "rs": Gpr.s0, "rt": Gpr.v0}),
        (asm.jr("t9"), Id.cpu_jr, {"rs": Gpr.t9}),
        (asm.RET, Id.cpu_jr, {"rs": Gpr.ra}),
        (asm.jalr("t9"), Id.cpu_jalr, {"rd": Gpr.ra, "rs": Gpr.t9}),
    ],
)
def test_register(word, op, regs):
    decode(word, op, **regs)


def test_shift_cop1_nop():
    assert decode(asm.sll("v1", "v0", 2), Id.cpu_sll, rd=Gpr.v1, rt=Gpr.v0).sa == 2
    assert decode(asm.mtc1("at", 12), Id.cpu_mtc1, rt=Gpr.at).fs.value == 12
    decode(asm.NOP, Id.cpu_nop)


@pytest.mark.parametrize("value", [0x0012_0040, 0x0010_8010, 0x0011_FFF0])
def test_hi_lo(value):
    upper = decode(asm.lui("a0", asm.hi(value)), Id.cpu_lui).getProcessedImmediate()
    lower = decode(asm.addiu("a0", "a0", asm.lo(value)), Id.cpu_addiu).getProcessedImmediate()
    assert (upper << 16) + lower == value


def test_register_numbers():
    assert asm.addiu(3, 2, 1) == asm.addiu("v1", "v0", 1)
    assert [asm.gpr(r) for r in ("zero", "t9", "sp", "ra")] == [0, 25, 29, 31]
    for bad in ("x0", 32):
        with pytest.raises(ValueError):
            asm.gpr(bad)
