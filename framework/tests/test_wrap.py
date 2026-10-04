# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The wrapper builder (wrap.cpp): wrappers emitted at a 32-bit base and run in unicorn."""

import ctypes
from collections.abc import Callable
from typing import Any

import pytest

Mips = Any  # the conftest's Mips class, handed in by the mips fixture

# mhfu_regs_t, in field order
REGS = (
    "at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 t8 t9 s0 s1 s2 s3 s4 s5 s6 s7 hi lo ra sp pc"
).split()
# what a C helper or callee may change
SCRATCH = "at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 t8 t9".split()
KEPT = SCRATCH + "s0 s1 s2 s3 s4 s5 s6 s7 hi lo".split()
N_FPRS = 20
FPRS = [f"f{i}" for i in range(N_FPRS)]

COMMON, WRAPPER = 0x10000, 0x11000
PRE, POST, CALLEE = 0x12000, 0x12400, 0x12800
SITE, END = 0x13000, 0x13800
SETUP = 0x14000
# register dumps and the FPU's start values, below 0x8000 so code addresses them off $zero
DUMP_CALL, DUMP_RESUME, INIT = 0x1000, 0x1200, 0x1400
STACK = 0x3F0000
CALLER_RA = 0x00ABC000


class Wrap(ctypes.Structure):
    _fields_ = [
        ("pre", ctypes.c_void_p),
        ("call", ctypes.c_uint32),
        ("post", ctypes.c_void_p),
        ("pc", ctypes.c_uint32),
        ("tail", ctypes.c_uint32 * 2),
        ("n_tail", ctypes.c_int),
        ("resume", ctypes.c_uint32),
    ]


@pytest.fixture(scope="module")
def lib(host_lib: Callable[..., ctypes.CDLL]) -> ctypes.CDLL:
    lib = host_lib("src/core/wrap.cpp")
    words = ctypes.POINTER(ctypes.c_uint32)
    lib.mhfu_wrap_emit_common.argtypes = [words, ctypes.c_int, ctypes.c_uint32]
    lib.mhfu_wrap_emit.argtypes = [
        words,
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(Wrap),
    ]
    return lib


def emit(lib: ctypes.CDLL, base: int, w: Wrap, cap: int = 64) -> list[int]:
    out = (ctypes.c_uint32 * cap)()
    n = lib.mhfu_wrap_emit(out, cap, base, COMMON, ctypes.byref(w))
    return list(out[:n])


def emit_common(lib: ctypes.CDLL) -> list[int]:
    out = (ctypes.c_uint32 * 256)()
    n = lib.mhfu_wrap_emit_common(out, 256, COMMON)
    assert n
    return list(out[:n])


def start_values() -> dict[str, int]:
    v = {r: 0x10000000 + 0x010101 * i for i, r in enumerate(KEPT, 1)}
    v |= {f: 0x3F800000 + 0x1111 * i for i, f in enumerate(FPRS, 1)}
    v["fcsr"] = 0x00800002  # condition bit set, round toward +inf
    return v


def probe(M: Mips, area: int) -> list[int]:
    """Stores every register at area (only k0 changes): 32 GPRs, hi, lo, f0-f19, FCR31."""
    words = [M.sw(r, area + 4 * r, "zero") for r in range(1, 32) if r != M.GPR["k0"]]
    words += [M.mfhi("k0"), M.sw("k0", area + 0x80, "zero")]
    words += [M.mflo("k0"), M.sw("k0", area + 0x84, "zero")]
    words += [M.swc1(f, area + 0x88 + 4 * f, "zero") for f in range(N_FPRS)]
    words += [M.cfc1("k0", 31), M.sw("k0", area + 0x88 + 4 * N_FPRS, "zero")]
    return words


def dumped(m: Any, area: int) -> dict[str, int]:
    words = m.read(area, 0x22 + N_FPRS + 1)
    v = {name: words[i] for name, i in m.GPR.items()}
    v |= {"hi": words[0x20], "lo": words[0x21], "fcsr": words[0x22 + N_FPRS]}
    v |= {f: words[0x22 + i] for i, f in enumerate(FPRS)}
    return v


def clobber(M: Mips) -> list[int]:
    """What a C function may do: every scratch register, HI/LO, f0-f19 and FCR31 change."""
    words: list[int] = []
    for r in SCRATCH:
        words += M.li(r, 0xDEAD0000 | M.GPR[r])
    words += [M.mthi("t0"), M.mtlo("t1")]
    words += [M.mtc1("t2", f) for f in range(N_FPRS)]
    words += [M.ctc1("zero", 31)]
    return words


class Rig:
    """A caller at SITE, the wrapper at WRAPPER, and fake pre, callee and post helpers."""

    def __init__(self, M: Mips, lib: ctypes.CDLL, w: Wrap, site: list[int]) -> None:
        self.m = m = M()
        self.seen: dict[str, dict[str, int]] = {}
        self.edits: dict[str, dict[str, int]] = {}
        m.write(COMMON, emit_common(lib))
        self.words = emit(lib, WRAPPER, w)
        assert self.words
        m.write(WRAPPER, self.words)
        ret = [M.jr("ra"), M.NOP]
        for name, addr in (("pre", PRE), ("post", POST)):
            m.write(addr, clobber(M) + ret)
            m.at(addr, self._helper(name))
        results = M.li("v0", 0x600D0000) + M.li("v1", 0x600D0001)
        results += M.li("k0", 0x40490FDB) + [M.mtc1("k0", 0)]
        m.write(CALLEE, probe(M, DUMP_CALL) + clobber(M) + results + ret)
        caller = site + probe(M, DUMP_RESUME)
        m.write(SITE, caller + [M.NOP])
        self.end = SITE + 4 * len(caller)

        self.start = start_values()
        m.write(INIT, [self.start[f] for f in FPRS] + [self.start["fcsr"]])
        setup = [M.lwc1(f, INIT + 4 * f, "zero") for f in range(N_FPRS)]
        setup += [M.lw("k0", INIT + 4 * N_FPRS, "zero"), M.ctc1("k0", 31)]
        m.write(SETUP, setup)
        m.run(SETUP, SETUP + 4 * len(setup))
        for r in KEPT:
            m.set_reg(r, self.start[r])
        m.set_reg("sp", STACK)
        m.set_reg("ra", CALLER_RA)

    def _helper(self, name: str) -> Callable[[Any], None]:
        def hook(m: Any) -> None:
            regs = m.reg("a0")
            self.seen[name] = dict(zip(REGS, m.read(regs, len(REGS)), strict=True))
            for field, value in self.edits.get(name, {}).items():
                m.write(regs + 4 * REGS.index(field), [value])

        return hook

    def run(self) -> dict[str, int]:
        self.m.run(SITE, self.end)
        return dumped(self.m, DUMP_RESUME)


def wrap(**kw: Any) -> Wrap:
    w = Wrap()
    for k, v in kw.items():
        if k == "tail":
            w.tail[: len(v)] = v
            w.n_tail = len(v)
        else:
            setattr(w, k, v)
    return w


@pytest.fixture
def call_rig(mips: Mips, lib: ctypes.CDLL) -> Rig:
    w = wrap(pre=PRE, call=CALLEE, post=POST, pc=SITE)
    return Rig(mips, lib, w, [mips.jal(WRAPPER), mips.NOP])


@pytest.fixture
def detour_rig(mips: Mips, lib: ctypes.CDLL) -> Rig:
    tail = [mips.sw("s0", 0, "sp"), mips.addiu("v1", "v1", 1)]
    w = wrap(pre=PRE, pc=SITE, tail=tail, resume=SITE + 8)
    return Rig(mips, lib, w, [mips.j(WRAPPER), mips.NOP])


def test_pre_sees_the_callers_registers(call_rig: Rig) -> None:
    call_rig.run()
    seen = call_rig.seen["pre"]
    assert {r: seen[r] for r in KEPT} == {r: call_rig.start[r] for r in KEPT}
    assert (seen["sp"], seen["pc"], seen["ra"]) == (STACK, SITE, SITE + 8)


def test_call_gets_every_register_and_the_edit(call_rig: Rig) -> None:
    call_rig.edits["pre"] = {"a1": 0x0A1ED17}
    call_rig.run()
    at_call = dumped(call_rig.m, DUMP_CALL)
    want = call_rig.start | {"a1": 0x0A1ED17}
    names = KEPT + FPRS + ["fcsr"]
    assert {r: at_call[r] for r in names} == {r: want[r] for r in names}
    assert at_call["sp"] % 16 == 0 and at_call["sp"] < STACK


def test_call_results_reach_post_and_the_caller(call_rig: Rig) -> None:
    call_rig.edits["pre"] = {"a1": 0x0A1ED17}
    call_rig.edits["post"] = {"v0": 0x0E0E0E0E}
    back = call_rig.run()
    post = call_rig.seen["post"]
    assert (post["v0"], post["v1"], post["a1"]) == (0x600D0000, 0x600D0001, 0x0A1ED17)
    want = call_rig.start | {"v0": 0x0E0E0E0E, "v1": 0x600D0001, "a1": 0x0A1ED17}
    want |= {"f0": 0x40490FDB}
    names = KEPT + FPRS + ["fcsr"]
    assert {r: back[r] for r in names} == {r: want[r] for r in names}
    assert (back["sp"], back["ra"]) == (STACK, SITE + 8)


def test_detour_runs_the_tail_and_resumes(detour_rig: Rig) -> None:
    detour_rig.edits["pre"] = {"a1": 0x0A1ED17}
    back = detour_rig.run()
    seen = detour_rig.seen["pre"]
    assert (seen["sp"], seen["pc"], seen["ra"]) == (STACK, SITE, CALLER_RA)
    want = detour_rig.start | {"a1": 0x0A1ED17, "v1": detour_rig.start["v1"] + 1}
    names = KEPT + FPRS + ["fcsr"]
    assert {r: back[r] for r in names} == {r: want[r] for r in names}
    assert (back["sp"], back["ra"]) == (STACK, CALLER_RA)
    # the tail's store used the caller's sp
    assert detour_rig.m.read(STACK)[0] == detour_rig.start["s0"]


def test_no_conditional_branches(call_rig: Rig, detour_rig: Rig, lib: ctypes.CDLL) -> None:
    for words in (call_rig.words, detour_rig.words, emit_common(lib)):
        for word in words:
            op = word >> 26
            assert op not in (0x01, 0x04, 0x05, 0x06, 0x07, 0x14, 0x15, 0x16, 0x17)
            assert not (op == 0x11 and (word >> 21) & 0x1F == 0x08)
    assert len(detour_rig.words) < 30 and len(call_rig.words) < 30


def test_refuses_what_does_not_fit(lib: ctypes.CDLL) -> None:
    w = wrap(pre=PRE, call=CALLEE, post=POST, pc=SITE)
    n = len(emit(lib, WRAPPER, w))
    assert emit(lib, WRAPPER, w, cap=n - 1) == []
    # j keeps the delay slot's top 4 bits: a target in another 256 MB region is out of reach
    assert emit(lib, WRAPPER, wrap(pre=PRE, pc=SITE, resume=0x10000000 + SITE)) == []
