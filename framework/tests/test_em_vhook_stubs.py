"""em_vhook's two stubs, assembled on the host and checked word by word: a stub bug shows up
in the game as a spin, not a crash."""

import ctypes
from collections.abc import Callable

import pytest
from mhfu import addresses
from mhfu.mips import decode
from rabbitizer import Instruction

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
int act_frame(void) { return ACT_FRAME; }
}
"""


class Stubs:
    def __init__(self, ai: list[int], act: list[int], cfg_size: int, act_frame: int) -> None:
        self.ai, self.act, self.cfg_size, self.act_frame = ai, act, cfg_size, act_frame


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
    return Stubs(ai, list(out[:n_act]), lib.cfg_size(), lib.act_frame())


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


def test_ai_stub_is_frame_free_and_tail_calls(stubs: Stubs) -> None:
    assert _sp_uses(stubs.ai) == []
    ins = _ins(stubs.ai)
    jumps = [i.getInstrIndexAsVram() for i in ins if i.isJumpWithAddress()]
    assert jumps == [ORIG_AI] and ins[-2].isJumpWithAddress() and ins[-1].isNop()
    for k, i in enumerate(ins):
        if i.isJump() and not i.isJumpWithAddress():  # the conditional call's jalr
            assert ins[k + 1].isNop()


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
