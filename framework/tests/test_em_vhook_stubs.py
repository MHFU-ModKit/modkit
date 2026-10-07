# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""em_vhook's stubs, assembled on the host and checked word by word, and the slot-29 and slot-30
ones run in unicorn: a stub bug shows up in the game as a spin, not a crash."""

import ctypes
from collections.abc import Callable
from contextlib import suppress
from typing import Any

import pytest
from mhfu import addresses
from mhfu.mips import decode
from rabbitizer import Instruction
from unicorn import UcError

BASE = int(addresses.USER_RAM) + 0x100000  # where the words are decoded
CFG = BASE + 0x4000
RET = BASE + 0x3000
ORIG_AI = int(addresses.USER_RAM) + 0x200000
ORIG_ACT = ORIG_AI + 0x100
SP, T7 = 29, 15

SHIM = """
#include "em_vhook_stubs.h"
extern "C" {
int ai(uint32_t *o, uint32_t cfg, uint32_t orig, uint32_t ret, int *ov)
{ return emv_build_ai_stub(o, STUB_AI_INSNS, cfg, orig, ret, ov); }
int act(uint32_t *o, uint32_t cfg, uint32_t orig, int *ov)
{ return emv_build_act_stub(o, STUB_ACT_INSNS, cfg, orig, ov); }
int cfg_size(void) { return CFG_SIZE; }
int cfg_step(void) { return CFG_STEP_FN; }
int evt(uint32_t *o, uint32_t cfg, uint32_t orig, uint32_t ret, int *ov)
{ return emv_build_events_stub(o, STUB_EVT_INSNS, cfg, orig, ret, ov); }
int cfg_mute(void) { return CFG_MUTE_ENT; }
int cfg_muted(void) { return CFG_MUTED; }
int act_frame(void) { return ACT_FRAME; }
int cfg_react(void) { return CFG_REACT_ENT; }
int cfg_sub(void) { return CFG_SUB_BASE; }
}
"""


class Stubs:
    def __init__(self, lib: ctypes.CDLL, ai: list[int], act: list[int]) -> None:
        self.lib, self.ai, self.act = lib, ai, act
        self.cfg_size, self.act_frame = lib.cfg_size(), lib.act_frame()


@pytest.fixture(scope="module")
def stubs(host_lib: Callable[..., ctypes.CDLL], tmp_path_factory: pytest.TempPathFactory) -> Stubs:
    shim = tmp_path_factory.mktemp("shim") / "shim.cpp"
    shim.write_text(SHIM)
    lib = host_lib(str(shim))  # an absolute path overrides the framework/ prefix
    out, ov = (ctypes.c_uint32 * 1024)(), ctypes.c_int(0)
    n_ai = lib.ai(out, CFG, ORIG_AI, RET, ctypes.byref(ov))
    ai = list(out[:n_ai])
    n_act = lib.act(out, CFG, ORIG_ACT, ctypes.byref(ov))
    assert not ov.value, "a stub outgrew its slot"
    return Stubs(lib, ai, list(out[:n_act]))


def _ins(words: list[int]) -> list[Instruction]:
    return [decode(w, BASE + 4 * i) for i, w in enumerate(words)]


def _sp_uses(words: list[int]) -> list[int]:
    """Words naming $sp as a GPR: FPU arithmetic and J-type have none; lwc1/swc1 only a base."""
    uses = []
    for w in words:
        op = w >> 26
        if op in (0x02, 0x03) or (op == 0x11 and (w >> 21) & 31 not in (0, 4)):
            continue
        regs = {(w >> 21) & 31} if op in (0x31, 0x39) else {(w >> 21) & 31, (w >> 16) & 31}
        if op == 0:
            regs.add((w >> 11) & 31)
        if SP in regs:
            uses.append(w)
    return uses


def _imm(w: int) -> int:
    return ctypes.c_int16(w & 0xFFFF).value


def test_branchless(stubs: Stubs) -> None:
    for words in (stubs.ai, stubs.act):
        assert not any(i.isBranch() for i in _ins(words))


def test_ai_stub_is_frame_free_and_ends_in_one_jr(stubs: Stubs) -> None:
    assert _sp_uses(stubs.ai) == []
    ins = _ins(stubs.ai)
    assert not any(i.isJumpWithAddress() for i in ins)
    assert ins[-2].isJump() and not ins[-2].isJrRa() and ins[-1].isNop()
    for k, i in enumerate(ins):
        if i.isJump():  # the calls' jalr and the tail's jr
            assert ins[k + 1].isNop()


# the slot-29 stub run in unicorn, at addresses inside the machine
STUB, RUN_CFG, RUN_RET, RUN_ORIG = 0x10000, 0x14000, 0x13000, 0x20000
STEP, SEEN, ENTITY, CALLER = 0x30000, 0x7F00, 0x40000, 0x50000  # SEEN: a 16-bit offset


def _run_ai(stubs: Stubs, mips: Any, step: int | None) -> tuple[Any, int]:
    """Runs the stub as the engine calls the AI step; returns the machine and where it went."""
    out, ov = (ctypes.c_uint32 * 1024)(), ctypes.c_int(0)
    n = stubs.lib.ai(out, RUN_CFG, RUN_ORIG, RUN_RET, ctypes.byref(ov))
    m = mips()
    player = int(addresses.PLAYER_ENTITY) & ~0xFFF
    m.uc.mem_map(player, 0x1000)
    m.write(STUB, list(out[:n]))
    m.write(RUN_RET, [mips.jr("ra"), mips.NOP])
    m.write(RUN_CFG, [0] * (stubs.cfg_size // 4))
    if step is not None:  # v0 = step; remember a0
        m.write(STEP, [mips.sw("a0", SEEN, "zero"), mips.jr("ra"), mips.addiu("v0", "zero", step)])
        m.write(RUN_CFG + stubs.lib.cfg_step(), [STEP])
    for reg, v in (("a0", ENTITY), ("a1", 0x11), ("a2", 0x22), ("a3", 0x33), ("ra", CALLER)):
        m.set_reg(reg, v)
    m.set_reg("sp", 0x3F000)
    went: list[int] = []
    for stop in (RUN_ORIG, CALLER):
        m.at(stop, lambda mm, s=stop: (went.append(s), mm.uc.emu_stop()))
    with suppress(UcError):
        m.uc.emu_start(STUB, 0xFFFFFFF0, count=5000)
    assert len(went) == 1
    return m, went[0]


def test_ai_stub_without_a_step_tail_calls_the_original(stubs: Stubs, mips: Any) -> None:
    m, went = _run_ai(stubs, mips, None)
    assert went == RUN_ORIG
    assert [m.reg(r) for r in ("a0", "a1", "a2", "a3", "ra")] == [ENTITY, 0x11, 0x22, 0x33, CALLER]


def test_ai_stub_step_that_declines_runs_the_original(stubs: Stubs, mips: Any) -> None:
    m, went = _run_ai(stubs, mips, 0)
    assert went == RUN_ORIG and m.read(SEEN) == [ENTITY] and m.reg("a0") == ENTITY


def test_ai_stub_step_that_takes_the_frame_returns(stubs: Stubs, mips: Any) -> None:
    m, went = _run_ai(stubs, mips, 1)
    assert went == CALLER and m.read(SEEN) == [ENTITY] and m.reg("v0") == 0


def test_act_stub_uses_only_its_frame(stubs: Stubs) -> None:
    uses = _sp_uses(stubs.act)
    adjusts = sorted(_imm(w) for w in uses if w >> 26 == 0x09)  # addiu sp, sp, imm
    assert adjusts == [-stubs.act_frame, stubs.act_frame]
    assert all(0 <= _imm(w) < stubs.act_frame for w in uses if w >> 26 in (0x23, 0x2B))
    ins = _ins(stubs.act)
    calls = [i.getInstrIndexAsVram() for i in ins if i.isJumpWithAddress()]
    assert calls == [ORIG_ACT] and ins[-2].isJrRa()


def test_config_offsets_in_block(stubs: Stubs) -> None:
    for words in (stubs.ai, stubs.act):
        for w, i in zip(words, _ins(words), strict=True):
            if (i.doesLoad() or i.doesStore()) and (w >> 21) & 31 == T7:
                assert 0 <= _imm(w) < stubs.cfg_size


def _run_events(stubs: Stubs, mips: Any, muted: int) -> tuple[Any, int]:
    out, ov = (ctypes.c_uint32 * 64)(), ctypes.c_int(0)
    n = stubs.lib.evt(out, RUN_CFG, RUN_ORIG, RUN_RET, ctypes.byref(ov))
    assert not ov.value
    words = list(out[:n])
    assert not any(i.isBranch() for i in _ins(words)) and _sp_uses(words) == []
    m = mips()
    m.write(STUB, words)
    m.write(RUN_RET, [mips.jr("ra"), mips.NOP])
    m.write(RUN_CFG, [0] * (stubs.cfg_size // 4))
    m.write(RUN_CFG + stubs.lib.cfg_mute(), [muted])
    m.set_reg("a0", ENTITY)
    m.set_reg("ra", CALLER)
    went: list[int] = []
    for stop in (RUN_ORIG, CALLER):
        m.at(stop, lambda mm, s=stop: (went.append(s), mm.uc.emu_stop()))
    with suppress(UcError):
        m.uc.emu_start(STUB, 0xFFFFFFF0, count=200)
    assert len(went) == 1
    return m, went[0]


def test_events_stub_skips_the_muted_entity(stubs: Stubs, mips: Any) -> None:
    m, went = _run_events(stubs, mips, ENTITY)
    assert went == CALLER and m.read(RUN_CFG + stubs.lib.cfg_muted()) == [1]


def test_events_stub_runs_any_other(stubs: Stubs, mips: Any) -> None:
    for muted in (0, ENTITY + 0x800):
        m, went = _run_events(stubs, mips, muted)
        assert went == RUN_ORIG and m.reg("a0") == ENTITY
        assert m.read(RUN_CFG + stubs.lib.cfg_muted()) == [0]


def _run_act(
    stubs: Stubs, mips: Any, sub: int, gate: int, table: bool = False
) -> tuple[Any, list[int]]:
    """Runs the slot-32 stub on an enter-action (ENTITY, 4, sub, 2) with the reaction entry armed
    for ENTITY's flinch; returns the machine and the (main, sub) the original got."""
    out, ov = (ctypes.c_uint32 * 1024)(), ctypes.c_int(0)
    n = stubs.lib.act(out, RUN_CFG, RUN_ORIG, ctypes.byref(ov))
    m = mips()
    m.write(STUB, list(out[:n]))
    m.write(RUN_ORIG, [mips.jr("ra"), mips.NOP])
    m.write(RUN_CFG, [0] * (stubs.cfg_size // 4))
    r = RUN_CFG + stubs.lib.cfg_react()
    gate_off = int(addresses.ENTITY.FLINCH_MASK)
    m.write(r, [ENTITY, 0b100100011, 4 | (0 << 8) | (2 << 16), gate_off])  # ent, mask, main/to
    m.uc.mem_write(ENTITY + gate_off, bytes([gate]))
    if table:  # a standing substitution of main 4, any sub, to (1, 3)
        m.write(RUN_CFG + stubs.lib.cfg_sub(), [0x0301FE10, 0xFFFFFFFF])
    got: list[int] = []
    m.at(RUN_ORIG, lambda mm: got.extend([mm.reg("a1"), mm.reg("a2")]))
    for reg, v in (("a0", ENTITY), ("a1", 4), ("a2", sub), ("a3", 2), ("ra", CALLER)):
        m.set_reg(reg, v)
    m.set_reg("sp", 0x3F000)
    m.run(STUB, CALLER, count=2000)
    return m, got


def test_act_stub_replaces_a_flinch(stubs: Stubs, mips: Any) -> None:
    m, got = _run_act(stubs, mips, 1, 1)
    hits, last = m.read(RUN_CFG + stubs.lib.cfg_react() + 0x10, 2)
    assert got == [0, 2] and (hits, last) == (1, 0x20401)


def test_act_stub_leaves_the_rest(stubs: Stubs, mips: Any) -> None:
    for sub, gate, table, want in (
        (1, 0, False, [4, 1]),
        (4, 1, False, [4, 4]),
        (1, 1, True, [1, 3]),
    ):
        m, got = _run_act(stubs, mips, sub, gate, table)
        assert got == want and m.read(RUN_CFG + stubs.lib.cfg_react() + 0x10) == [0]
