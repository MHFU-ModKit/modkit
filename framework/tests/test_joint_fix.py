"""The joint-fix stub, run from the point the joint builder jumps to it."""

import ctypes
from collections.abc import Callable
from typing import Any

import pytest
from mhfu import addresses
from mhfu.mips import decode
from unicorn import UC_HOOK_MEM_WRITE

MAGIC = 0xC0000000
RESUME = int(addresses.JOINT_BUILDER_RESUME)
STUB = 0x1000
SKEL_WORD = 0x8FF0  # a low half >= 0x8000 tests the lui carry
PASSED = 0x10000  # the skeleton the engine passes
RELOCATED = 0x20000  # the relocated PAC's skeleton
BONES = 0x20  # bone array = skeleton + a fixed offset
SP = 0x3F000


@pytest.fixture(scope="module")
def stub(host_lib: Callable[..., ctypes.CDLL]) -> list[int]:
    lib = host_lib("src/core/joint_fix.cpp")
    out = (ctypes.c_uint32 * 16)()
    n = lib.mhfu_joint_fix_emit(out, 16, ctypes.c_uint32(SKEL_WORD))
    assert n > 0
    return list(out[:n])


def _run(mips: Any, words: list[int], passed_magic: int, skel: int) -> tuple[int, int]:
    """Runs the stub with a2 = PASSED to the resume point; returns (s2, s4)."""
    m = mips()
    m.uc.mem_map(RESUME & ~0xFFF, 0x1000)
    m.write(STUB, words)
    m.write(SKEL_WORD, [skel])
    m.write(PASSED, [passed_magic])
    m.write(RELOCATED, [MAGIC])
    for reg, value in (("a2", PASSED), ("a1", PASSED + BONES), ("s4", PASSED + BONES)):
        m.set_reg(reg, value)
    m.set_reg("sp", SP)
    stores: list[int] = []
    m.uc.hook_add(UC_HOOK_MEM_WRITE, lambda _uc, _a, addr, *_: stores.append(addr))
    m.run(STUB, RESUME, count=64)
    assert not stores and m.reg("sp") == SP, "the stub is frame-free"
    return m.reg("s2"), m.reg("s4")


def test_valid_skeleton_passes_through(mips: Any, stub: list[int]) -> None:
    assert _run(mips, stub, MAGIC, RELOCATED) == (PASSED, PASSED + BONES)


def test_bad_skeleton_is_rebased(mips: Any, stub: list[int]) -> None:
    assert _run(mips, stub, 0x12345678, RELOCATED) == (RELOCATED, RELOCATED + BONES)


def test_no_skeleton_passes_through(mips: Any, stub: list[int]) -> None:
    assert _run(mips, stub, 0x12345678, 0) == (PASSED, PASSED + BONES)


def test_branchless(stub: list[int]) -> None:
    ins = [decode(w, STUB + 4 * i) for i, w in enumerate(stub)]
    assert not any(i.isBranch() for i in ins)
    assert ins[-2].isJumpWithAddress() and ins[-2].getInstrIndexAsVram() == RESUME
