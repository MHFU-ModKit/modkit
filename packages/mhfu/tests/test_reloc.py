import struct

import pytest
from mhfu import files, reloc
from mhfu.mips import Code
from mhfu.overlay import TEXT, Overlay
from modkit_testing import mips as asm

LOAD = 0x0012_0180
CODE = LOAD + TEXT + 0x40
OUTSIDE = 0x0011_0000


def overlay():
    data_at = CODE + 4 * 16
    ptr, ext = data_at + 8, OUTSIDE + 0x760
    words = [
        asm.lui("a0", asm.hi(ptr)),
        asm.jal(CODE + 4 * 14),  # 1 into the image
        asm.addiu("a0", "a0", asm.lo(ptr)),
        asm.lui("v0", asm.hi(ext)),
        asm.jal(OUTSIDE),  # 4 out of it
        asm.lw("a1", asm.lo(ext), "v0"),  # below load: another overlay's bss
        asm.beq("a0", "zero", CODE + 4 * 10, CODE + 4 * 6, likely=True),
        asm.lui("v1", asm.hi(ptr)),  # 7 only on the taken path
        asm.RET,
        asm.NOP,
        asm.lw("a1", asm.lo(ptr), "v1"),  # 10 pairs with the lui in the branch-likely slot
        asm.RET,
        asm.NOP,
        asm.NOP,
        asm.RET,  # 14
        asm.NOP,
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
