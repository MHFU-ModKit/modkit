"""Host builds of framework sources, for tests that call them through ctypes."""

import ctypes
import shutil
import struct
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from unicorn import (
    UC_ARCH_MIPS,
    UC_HOOK_CODE,
    UC_MODE_LITTLE_ENDIAN,
    UC_MODE_MIPS32,
    Uc,
    mips_const,
)

FRAMEWORK = Path(__file__).parents[1]


@pytest.fixture(scope="session")
def host_lib(tmp_path_factory: pytest.TempPathFactory) -> Callable[..., ctypes.CDLL]:
    """build(*sources) compiles framework sources (paths relative to framework/) into one
    shared library; the address header is rendered fresh, and MHFU_HOST is defined."""
    cc = shutil.which("c++")
    if cc is None:
        pytest.skip("no host C++ compiler")
    gen = tmp_path_factory.mktemp("gen")
    subprocess.run(
        [sys.executable, "-m", "mhfu.addresses", "c", "-o", str(gen / "addresses.gen.h")],
        check=True,
    )

    def build(*sources: str) -> ctypes.CDLL:
        out = tmp_path_factory.mktemp("host") / "lib.so"
        includes = [FRAMEWORK / "include", FRAMEWORK / "src" / "core", gen]
        subprocess.run(
            [cc, "-std=gnu++17", "-shared", "-fPIC", "-O1", "-Wall", "-DMHFU_HOST"]
            + [f"-I{d}" for d in includes]
            + [str(FRAMEWORK / s) for s in sources]
            + ["-o", str(out)],
            check=True,
        )
        return ctypes.CDLL(str(out))

    return build


_GPRS = "zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 s0 s1 s2 s3 s4 s5 s6 s7 t8 t9"
_GPRS += " k0 k1 gp sp fp ra"
Reg = int | str


def _r(reg: Reg) -> int:
    return reg if isinstance(reg, int) else Mips.GPR[reg]


class Mips:
    """A little-endian MIPS32 machine with an FPU over [0, size), for running generated words.
    Registers are names ("a0") or numbers; the encoders are static methods."""

    GPR = {name: i for i, name in enumerate(_GPRS.split())}

    def __init__(self, size: int = 0x400000) -> None:
        self.uc = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        self.uc.mem_map(0, size)
        self.steps = 0
        self.uc.hook_add(UC_HOOK_CODE, self._step)

    def _step(self, _uc: Uc, _addr: int, _size: int, _data: object) -> None:
        self.steps += 1

    def write(self, addr: int, words: Sequence[int]) -> None:
        self.uc.mem_write(addr, struct.pack(f"<{len(words)}I", *words))

    def read(self, addr: int, n: int = 1) -> list[int]:
        return list(struct.unpack(f"<{n}I", self.uc.mem_read(addr, 4 * n)))

    def reg(self, reg: Reg) -> int:
        uid = {"hi": mips_const.UC_MIPS_REG_HI, "lo": mips_const.UC_MIPS_REG_LO}.get(str(reg))
        if uid is None:
            uid = getattr(mips_const, f"UC_MIPS_REG_{_r(reg)}")
        return int(self.uc.reg_read(uid))

    def set_reg(self, reg: Reg, value: int) -> None:
        uid = {"hi": mips_const.UC_MIPS_REG_HI, "lo": mips_const.UC_MIPS_REG_LO}.get(str(reg))
        if uid is None:
            uid = getattr(mips_const, f"UC_MIPS_REG_{_r(reg)}")
        self.uc.reg_write(uid, value & 0xFFFFFFFF)

    def at(self, addr: int, fn: Callable[["Mips"], None]) -> None:
        """Calls fn(self) each time execution reaches addr, before that instruction runs."""
        self.uc.hook_add(UC_HOOK_CODE, lambda _uc, _a, _s, _d: fn(self), begin=addr, end=addr)

    def run(self, start: int, until: int, count: int = 100_000) -> None:
        """Runs from start until execution reaches until (which does not run); raises when
        that takes count instructions. Unicorn's pc after a stop is not reliable."""
        self.steps = 0
        self.uc.emu_start(start, until, count=count)
        if self.steps >= count:
            raise AssertionError(f"{until:#x} not reached in {count} instructions")

    @staticmethod
    def _i(op: int, rs: int, rt: int, imm: int) -> int:
        return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)

    @staticmethod
    def lui(rt: Reg, imm: int) -> int:
        return Mips._i(0x0F, 0, _r(rt), imm)

    @staticmethod
    def ori(rt: Reg, rs: Reg, imm: int) -> int:
        return Mips._i(0x0D, _r(rs), _r(rt), imm)

    @staticmethod
    def addiu(rt: Reg, rs: Reg, imm: int) -> int:
        return Mips._i(0x09, _r(rs), _r(rt), imm)

    @staticmethod
    def lw(rt: Reg, off: int, base: Reg) -> int:
        return Mips._i(0x23, _r(base), _r(rt), off)

    @staticmethod
    def sw(rt: Reg, off: int, base: Reg) -> int:
        return Mips._i(0x2B, _r(base), _r(rt), off)

    @staticmethod
    def lwc1(ft: int, off: int, base: Reg) -> int:
        return Mips._i(0x31, _r(base), ft, off)

    @staticmethod
    def swc1(ft: int, off: int, base: Reg) -> int:
        return Mips._i(0x39, _r(base), ft, off)

    @staticmethod
    def li(rt: Reg, value: int) -> list[int]:
        return [Mips.lui(rt, value >> 16), Mips.ori(rt, rt, value)]

    @staticmethod
    def j(target: int) -> int:
        return (0x02 << 26) | ((target >> 2) & 0x03FFFFFF)

    @staticmethod
    def jal(target: int) -> int:
        return (0x03 << 26) | ((target >> 2) & 0x03FFFFFF)

    @staticmethod
    def jr(rs: Reg) -> int:
        return (_r(rs) << 21) | 0x08

    NOP = 0

    @staticmethod
    def mfhi(rd: Reg) -> int:
        return (_r(rd) << 11) | 0x10

    @staticmethod
    def mflo(rd: Reg) -> int:
        return (_r(rd) << 11) | 0x12

    @staticmethod
    def mthi(rs: Reg) -> int:
        return (_r(rs) << 21) | 0x11

    @staticmethod
    def mtlo(rs: Reg) -> int:
        return (_r(rs) << 21) | 0x13

    @staticmethod
    def mtc1(rt: Reg, fs: int) -> int:
        return (0x11 << 26) | (0x04 << 21) | (_r(rt) << 16) | (fs << 11)

    @staticmethod
    def cfc1(rt: Reg, fs: int) -> int:
        return (0x11 << 26) | (0x02 << 21) | (_r(rt) << 16) | (fs << 11)

    @staticmethod
    def ctc1(rt: Reg, fs: int) -> int:
        return (0x11 << 26) | (0x06 << 21) | (_r(rt) << 16) | (fs << 11)


@pytest.fixture
def mips() -> type[Mips]:
    """The Mips class: Mips() is a fresh machine, Mips.jal(...) and friends encode words."""
    return Mips
