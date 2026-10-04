# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Move an MWo3 overlay to another address, so overlays built for one load address (all 17 em
overlays share one) can be resident side by side.

An overlay is copied to its fixed address with no relocation table, so its references are found
by analysis: j/jal targets, lui/lo pairs and data words that point into its own image, from
`load` to the end of its bss, plus the header's load address and ctor list. Below an em
overlay's load address lies game_sub's bss, not the overlay's: references there stay.

    moved = relocate(game.em(75), 0x100000)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .mips import Code, decode
from .overlay import Overlay

ALIGN = 0x10000
"""A delta must be a multiple of this: every %lo then stays as it is and only the luis change."""

HEADER_ADDRESSES = (0x08, 0x18, 0x1C)
"""Header words holding addresses: the load address and the ctor list's start and end."""

_TARGET = 0x03FF_FFFF  # the instruction-index field of j and jal
_IMMEDIATE = 0xFFFF


@dataclass(frozen=True)
class Sites:
    """What a move rewrites, by address."""

    jumps: tuple[int, ...]
    his: tuple[int, ...]
    words: tuple[int, ...]


def footprint(ov: Overlay) -> range:
    """The overlay's own memory: its image and bss."""
    return range(ov.load, ov.bss.stop)


def sites(ov: Overlay, code: Code | None = None) -> Sites:
    """Every reference into the footprint; raises ValueError if a lui serves one inside and one
    outside, which no single delta can keep both right."""
    code = code or Code(ov, ov.text)
    foot = footprint(ov)
    jumps = [i.vram for i in code if i.isJumpWithAddress() and i.getInstrIndexAsVram() in foot]
    inside = {p.hi for p in code.pairs if p.value in foot}
    torn = sorted(p.hi for p in code.pairs if p.hi in inside and p.value not in foot)
    if torn:
        raise ValueError(f"{ov.name}: lui at 0x{torn[0]:08X} forms addresses in and out of it")
    words = [a for a in range(ov.initialised.start, ov.initialised.stop, 4) if ov.u32(a) in foot]
    return Sites(tuple(jumps), tuple(sorted(inside)), tuple(words))


def relocate(ov: Overlay, delta: int, where: Sites | None = None) -> bytes:
    """The overlay file moved by `delta` bytes, its disc padding kept."""
    if delta % ALIGN:
        raise ValueError(f"delta 0x{delta:X} is not a multiple of 0x{ALIGN:X}")
    where = where or sites(ov)
    out = bytearray(ov.file)

    def put(va: int, word: int) -> None:
        struct.pack_into("<I", out, va - ov.base, word & 0xFFFF_FFFF)

    for a in where.jumps:
        put(a, _with_target(ov.u32(a), a, decode(ov.u32(a), a).getInstrIndexAsVram() + delta))
    for a in where.his:
        hi = decode(ov.u32(a), a).getProcessedImmediate()
        put(a, _with_immediate(ov.u32(a), a, hi + (delta >> 16)))
    for a in where.words:
        put(a, ov.u32(a) + delta)
    foot = footprint(ov)
    for offset in HEADER_ADDRESSES:
        value = ov.u32(ov.base + offset)
        if value in foot or value == foot.stop:  # an end may sit at the very end
            put(ov.base + offset, value + delta)
    return bytes(out)


# rabbitizer only decodes, so the two field writes are checked by decoding them back
def _with_target(word: int, va: int, target: int) -> int:
    new = word & ~_TARGET | (target >> 2) & _TARGET
    if decode(new, va).getInstrIndexAsVram() != target:
        raise ValueError(f"0x{va:08X}: 0x{target:08X} is out of the jump's reach")
    return new


def _with_immediate(word: int, va: int, value: int) -> int:
    new = word & ~_IMMEDIATE | value & _IMMEDIATE
    if decode(new, va).getProcessedImmediate() != value & _IMMEDIATE:
        raise ValueError(f"0x{va:08X}: 0x{value:X} does not fit the immediate")
    return new
