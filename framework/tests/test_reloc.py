# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The C overlay relocator (src/core/ovl_reloc.cpp) against mhfu.reloc, byte for byte."""

import ctypes
import random
import struct
from pathlib import Path

import pytest
from mhfu import files, reloc
from mhfu.files import Extracted
from mhfu.overlay import TEXT, Overlay
from modkit_testing import mips as asm

LOAD = 0x0012_0180
CODE = LOAD + TEXT + 0x40
BELOW = LOAD - 0x5080  # the em overlays' neighbour: game_sub's bss ends at their load address
DELTAS = (0x10000, -0x30000, 0xB00000)


class Stats(ctypes.Structure):
    """mhfu_ovl_reloc_stats_t"""

    _fields_ = [
        ("ok", ctypes.c_int),
        ("n_jump", ctypes.c_uint32),
        ("n_hi", ctypes.c_uint32),
        ("n_data", ctypes.c_uint32),
        ("new_base", ctypes.c_uint32),
    ]


@pytest.fixture(scope="module")
def relocate(host_lib):
    """relocate(image, delta) -> (moved bytes or None, Stats), from the C."""
    fn = host_lib("src/core/ovl_reloc.cpp").mhfu_ovl_relocate
    fn.restype = Stats
    fn.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.c_int32]

    def run(image: bytes, delta: int) -> tuple[bytes | None, Stats]:
        buf = ctypes.create_string_buffer(image, len(image))
        st = fn(buf, len(image), delta)
        return (buf.raw[: len(image)] if st.ok else None), st

    return run


@pytest.fixture(scope="module")
def game(mhfu_data: Path) -> Extracted:
    return Extracted.find(mhfu_data)


def python(ov: Overlay, delta: int) -> bytes | None:
    try:
        return reloc.relocate(ov, delta)
    except ValueError:
        return None


def build(words: list[int], data: list[int], bss: int, ctors=(0, 0), padding=0) -> bytes:
    text = bytes(0x40) + struct.pack(f"<{len(words)}I", *words)
    raw = struct.pack(f"<{len(data)}I", *data)
    header = struct.pack("<4sIIIIIII", b"MWo3", 1, LOAD, len(text), len(raw), bss, *ctors)
    return header.ljust(TEXT, b"\0") + text + raw + bytes(padding)


def rules() -> tuple[bytes, int]:
    """One reference of each kind the relocator must get right, and the footprint's end."""
    data_at = CODE + 4 * 16
    stop = data_at + 16 + 0x20  # data, then bss
    words = [
        asm.lui("a0", asm.hi(BELOW)),  # 0 below the load address: stays
        asm.lw("a1", asm.lo(BELOW), "a0"),
        asm.lui("v0", asm.hi(stop - 4)),  # 2 the bss's last word: moves
        asm.sw("zero", asm.lo(stop - 4), "v0"),
        asm.lui("v1", asm.hi(stop + 0x100)),  # 4 past the bss, in the file's padding: stays
        asm.lw("a2", asm.lo(stop + 0x100), "v1"),
        asm.beq("a1", "zero", CODE + 4 * 10, CODE + 4 * 6, likely=True),
        asm.lui("t0", asm.hi(data_at)),  # 7 only on the taken path: pairs at the target
        asm.jal(CODE + 4 * 14),  # 8 into the image: moves
        asm.NOP,
        asm.lw("a3", asm.lo(data_at), "t0"),  # 10
        asm.j(LOAD - 0x100),  # 11 out of it: stays
        asm.NOP,
        asm.NOP,
        asm.RET,  # 14
        asm.NOP,
    ]
    data = [data_at + 8, BELOW, stop, CODE]
    return build(words, data, 0x20, ctors=(data_at + 16, data_at + 16), padding=0x680), stop


def test_rules(relocate):
    image, stop = rules()
    ov, d = Overlay(image), 0x20000
    moved, st = relocate(image, d)
    assert moved == python(ov, d)
    before, after = (struct.unpack_from("<16I", b, TEXT + 0x40) for b in (image, moved))
    assert [i for i in range(16) if before[i] != after[i]] == [2, 7, 8]
    assert after[2] == before[2] + (d >> 16) and after[7] == before[7] + (d >> 16)
    assert after[8] == asm.jal(CODE + 4 * 14 + d)
    assert (st.n_jump, st.n_hi, st.n_data, st.new_base) == (1, 2, 2, LOAD + d)
    there = Overlay(moved)
    assert there.ctors == range(ov.ctors.start + d, ov.ctors.stop + d)
    assert there.bss.stop == stop + d


def test_rejects(relocate):
    image, _ = rules()
    assert relocate(image, 0x8000)[0] is None
    lui = asm.lui("a0", asm.hi(LOAD))  # forms the load address and one below it
    torn = build([lui, asm.addiu("a1", "a0", asm.lo(LOAD)), asm.lw("a2", -0x80, "a0")], [], 0)
    assert python(Overlay(torn), 0x10000) is None
    assert relocate(torn, 0x10000)[0] is None


REGS = ["zero", "at", "v0", "v1", "a0", "a1", "t0", "s0", "gp", "sp", "ra", "t9"]


def program(rng: random.Random, n: int = 300) -> bytes:
    """Random code over what the walk tracks: luis and lo pairs, moves, likely and plain
    branches, calls, returns, prologues; addresses around the footprint's edges."""
    stop = CODE + 4 * n + 128 + 0x100
    marks = [BELOW, LOAD - 4, LOAD, CODE + 8, CODE + 4 * n + 12, stop - 4, stop, stop + 0x680]

    def reg() -> str:
        return rng.choice(REGS)

    def addr() -> int:
        return rng.choice(marks) + rng.choice([0, 0, 4, -8, 0x7FF0])

    def code() -> int:
        return CODE + 4 * rng.randrange(-2, n + 2)

    words = []
    for i in range(n):
        at, r = CODE + 4 * i, rng.random()
        if r < 0.18:
            w = asm.lui(reg(), asm.hi(addr()))
        elif r < 0.30:
            w = asm.addiu(reg(), reg(), asm.lo(addr()))
        elif r < 0.38:
            w = rng.choice([asm.lw, asm.lbu, asm.lhu, asm.sw, asm.sb])(reg(), asm.lo(addr()), reg())
        elif r < 0.42:
            w = asm.ori(reg(), reg(), addr() & 0xFFFF)
        elif r < 0.50:
            w = rng.choice([asm.addu, asm.or_])(reg(), reg(), rng.choice([reg(), "zero"]))
        elif r < 0.62:
            b = rng.choice([asm.beq, asm.bne])
            w = b(reg(), rng.choice([reg(), "zero"]), code(), at, likely=rng.random() < 0.4)
        elif r < 0.66:
            w = asm.beq("zero", "zero", code(), at)
        elif r < 0.70:
            w = asm.RET
        elif r < 0.74:
            w = asm.jal(rng.choice([code(), LOAD - 0x100, addr() & ~3]))
        elif r < 0.77:
            w = asm.j(rng.choice([code(), LOAD - 0x100]))
        elif r < 0.80:
            w = rng.choice([asm.jalr, asm.jr])(reg())
        elif r < 0.84:
            w = asm.addiu("sp", "sp", -0x20)
        elif r < 0.88:
            w = asm.sll(reg(), reg(), 2)
        elif r < 0.91:
            w = asm.mtc1(reg(), 12)
        else:
            w = asm.NOP
        words.append(w)
    data = [rng.choice([addr(), rng.getrandbits(32)]) & 0xFFFF_FFFF for _ in range(32)]
    end = CODE + 4 * n + 128
    ctors = rng.choice([(end - 8, end), (end, end), (0, 0)])
    return build(words, data, 0x100, ctors, padding=rng.choice([0, 0x680]))


def test_random_programs(relocate):
    moved = 0
    for seed in range(200):
        image = program(random.Random(seed))
        for delta in (0x20000, -0x10000):
            expected = python(Overlay(image), delta)
            assert relocate(image, delta)[0] == expected, f"seed {seed} delta {delta:#x}"
            moved += expected is not None
    assert moved > 250  # most programs relocate; the rest are torn in both


def check(relocate, ov: Overlay) -> None:
    where = reloc.sites(ov)
    for delta in DELTAS:
        moved, st = relocate(ov.file, delta)
        assert moved == reloc.relocate(ov, delta, where), f"{ov.name} by {delta:#x}"
        counts = tuple(map(len, (where.jumps, where.his, where.words)))
        assert (st.n_jump, st.n_hi, st.n_data) == counts


@pytest.mark.parametrize("species", files.EM_SPECIES)
def test_em_overlays(relocate, game, species):
    check(relocate, game.em(species))


def test_stage_overlays(relocate, game):
    for stage in files.STAGES:
        check(relocate, game.overlay(files.stage_overlay(stage)))
