# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""MIPS encoders for test code: registers by ABI name (`"a0"`) or number, immediates masked to
16 bits, a branch from its own address `at` to `target`."""

GPRS = (
    "zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 "
    "s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra"
).split()

Reg = str | int


def gpr(reg: Reg) -> int:
    """The register's number."""
    if isinstance(reg, str):
        return GPRS.index(reg)
    if not 0 <= reg < 32:
        raise ValueError(f"no register {reg}")
    return reg


def hi(value: int) -> int:
    """%hi: the `lui` half, carrying for the sign of `lo`."""
    return (value + 0x8000) >> 16 & 0xFFFF


def lo(value: int) -> int:
    """%lo: the low half, sign-extended."""
    return (value & 0xFFFF) - (value & 0x8000) * 2


def _i(op: int, rs: Reg, rt: Reg, imm: int) -> int:
    return op << 26 | gpr(rs) << 21 | gpr(rt) << 16 | imm & 0xFFFF


def _r(funct: int, rd: Reg = 0, rs: Reg = 0, rt: Reg = 0, sa: int = 0) -> int:
    return gpr(rs) << 21 | gpr(rt) << 16 | gpr(rd) << 11 | (sa & 0x1F) << 6 | funct


def _jump(op: int, target: int) -> int:
    return op << 26 | target >> 2 & 0x03FF_FFFF


def _branch(op: int, rs: Reg, rt: Reg, target: int, at: int) -> int:
    return _i(op, rs, rt, (target - at - 4) >> 2)


def lui(rt: Reg, imm: int) -> int:
    return _i(0x0F, 0, rt, imm)


def addiu(rt: Reg, rs: Reg, imm: int) -> int:
    return _i(0x09, rs, rt, imm)


def li(rt: Reg, imm: int) -> int:
    """`addiu rt, zero, imm`."""
    return addiu(rt, 0, imm)


def ori(rt: Reg, rs: Reg, imm: int) -> int:
    return _i(0x0D, rs, rt, imm)


def sltiu(rt: Reg, rs: Reg, imm: int) -> int:
    return _i(0x0B, rs, rt, imm)


def lw(rt: Reg, offset: int, base: Reg) -> int:
    return _i(0x23, base, rt, offset)


def lbu(rt: Reg, offset: int, base: Reg) -> int:
    return _i(0x24, base, rt, offset)


def lhu(rt: Reg, offset: int, base: Reg) -> int:
    return _i(0x25, base, rt, offset)


def sw(rt: Reg, offset: int, base: Reg) -> int:
    return _i(0x2B, base, rt, offset)


def sb(rt: Reg, offset: int, base: Reg) -> int:
    return _i(0x28, base, rt, offset)


def beq(rs: Reg, rt: Reg, target: int, at: int, likely: bool = False) -> int:
    return _branch(0x14 if likely else 0x04, rs, rt, target, at)


def bne(rs: Reg, rt: Reg, target: int, at: int, likely: bool = False) -> int:
    return _branch(0x15 if likely else 0x05, rs, rt, target, at)


def j(target: int) -> int:
    return _jump(0x02, target)


def jal(target: int) -> int:
    return _jump(0x03, target)


def jr(rs: Reg) -> int:
    return _r(0x08, rs=rs)


def jalr(rs: Reg) -> int:
    """`jalr rs`, linking in ra."""
    return _r(0x09, "ra", rs)


def addu(rd: Reg, rs: Reg, rt: Reg) -> int:
    return _r(0x21, rd, rs, rt)


def or_(rd: Reg, rs: Reg, rt: Reg) -> int:
    return _r(0x25, rd, rs, rt)


def sll(rd: Reg, rt: Reg, sa: int) -> int:
    return _r(0x00, rd, rt=rt, sa=sa)


def mtc1(rt: Reg, fs: int) -> int:
    """`mtc1 rt, $f<fs>`."""
    return 0x11 << 26 | 0x04 << 21 | gpr(rt) << 16 | (fs & 0x1F) << 11


NOP = 0
RET = jr("ra")
