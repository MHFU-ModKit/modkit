import struct

import pytest
from mhfu import files, reloc
from mhfu.mips import Code
from mhfu.overlay import TEXT, Overlay

LOAD = 0x0012_0180
CODE = LOAD + TEXT + 0x40
OUTSIDE = 0x0011_0000
R = {"zero": 0, "v0": 2, "v1": 3, "a0": 4, "a1": 5, "sp": 29, "ra": 31}


def imm(op, rt, rs, value):
    return op << 26 | R[rs] << 21 | R[rt] << 16 | value & 0xFFFF


def lui(rt, value):
    return imm(0x0F, rt, "zero", value >> 16)


def addiu(rt, rs, value):
    return imm(0x09, rt, rs, value)


def lw(rt, value, rs):
    return imm(0x23, rt, rs, value)


def beql(rs, rt, to, at):
    return imm(0x14, rt, rs, (to - at - 4) >> 2)


def jal(target):
    return 3 << 26 | (target >> 2) & 0x03FF_FFFF


RET = 0x03E0_0008


def half(value):
    """%hi, %lo of value, the low half sign-extended."""
    lo = (value & 0xFFFF) - (0x10000 if value & 0x8000 else 0)
    return value - lo, lo


def overlay():
    data_at = CODE + 4 * 16
    ptr_hi, ptr_lo = half(data_at + 8)
    ext_hi, ext_lo = half(OUTSIDE + 0x760)
    words = [
        lui("a0", ptr_hi),
        jal(CODE + 4 * 14),  # 1 into the image
        addiu("a0", "a0", ptr_lo),
        lui("v0", ext_hi),
        jal(OUTSIDE),  # 4 out of it
        lw("a1", ext_lo, "v0"),  # below load: another overlay's bss
        beql("a0", "zero", CODE + 4 * 10, CODE + 4 * 6),
        lui("v1", ptr_hi),  # 7 only on the taken path
        RET,
        0,
        lw("a1", ptr_lo, "v1"),  # 10 pairs with the lui in the branch-likely slot
        RET,
        0,
        0,
        RET,  # 14
        0,
    ]
    text = bytes(0x40) + struct.pack(f"<{len(words)}I", *words)
    data = struct.pack("<4I", data_at + 12, OUTSIDE, 7, CODE)  # the last word: a ctor list
    ctors = (data_at + 12, data_at + 16)
    header = struct.pack("<4sIIIIIII", b"MWo3", 1, LOAD, len(text), len(data), 0x10, *ctors)
    return Overlay(header.ljust(TEXT, b"\0") + text + data, "em99.ovl")


def test_sites():
    ov = overlay()
    s = reloc.sites(ov)
    assert s.jumps == (CODE + 4,)
    assert s.his == (CODE, CODE + 4 * 7)
    assert s.words == (CODE + 4 * 16, CODE + 4 * 19)


def test_relocate():
    ov, d = overlay(), 0x20000
    moved = Overlay(reloc.relocate(ov, d))
    moves_by(ov, moved, d)
    calls = [i.getInstrIndexAsVram() for i in Code(moved, moved.text) if i.isJumpWithAddress()]
    assert calls == [CODE + 4 * 14 + d, OUTSIDE]
    assert moved.load == LOAD + d
    assert moved.ctors == range(ov.ctors.start + d, ov.ctors.stop + d)


def test_round_trip():
    ov = overlay()
    assert reloc.relocate(ov, 0) == ov.file
    there = Overlay(reloc.relocate(ov, -0x30000))
    assert reloc.relocate(there, 0x30000) == ov.file
    with pytest.raises(ValueError):
        reloc.relocate(ov, 0x8000)


def moves_by(ov, moved, delta):
    """Every reference into the footprint moved by delta, nothing else changed."""
    foot = reloc.footprint(ov)
    before, after = Code(ov, ov.text), Code(moved, moved.text)
    for p, q in zip(before.pairs, after.pairs, strict=True):
        assert (p.hi + delta, p.lo + delta) == (q.hi, q.lo)
        assert q.value == (p.value + delta if p.value in foot else p.value)
    for a in range(ov.initialised.start, ov.initialised.stop, 4):
        w = ov.u32(a)
        assert moved.u32(a + delta) == (w + delta if w in foot else w)


@pytest.mark.parametrize("species", files.EM_SPECIES[::4])
def test_em_overlays(game, species):
    ov = game.em(species)
    moved = Overlay(reloc.relocate(ov, 0x100000))
    moves_by(ov, moved, 0x100000)
    assert reloc.relocate(moved, -0x100000) == ov.file
