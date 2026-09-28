# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The GE's four packed colour formats, which textures and vertices share: red in the low bits,
widened to 8 bits by bit replication as the GE does, and rounded on the way back."""

from __future__ import annotations

import sys
from array import array
from enum import IntEnum

Rgba = tuple[int, int, int, int]


class Color(IntEnum):
    """A texture's pixel format 0-3; a VTYPE colour format is the same value + 4."""

    BGR5650 = 0
    ABGR5551 = 1
    ABGR4444 = 2
    ABGR8888 = 3


_5TO8 = bytes(v << 3 | v >> 2 for v in range(32))
_6TO8 = bytes(v << 2 | v >> 4 for v in range(64))


def unpack(value: int, fmt: Color) -> Rgba:
    if fmt == Color.ABGR8888:
        return value & 0xFF, value >> 8 & 0xFF, value >> 16 & 0xFF, value >> 24 & 0xFF
    if fmt == Color.BGR5650:
        return _5TO8[value & 31], _6TO8[value >> 5 & 63], _5TO8[value >> 11 & 31], 255
    if fmt == Color.ABGR5551:
        r, g, b = _5TO8[value & 31], _5TO8[value >> 5 & 31], _5TO8[value >> 10 & 31]
        return r, g, b, 255 * (value >> 15 & 1)
    return (value & 15) * 17, (value >> 4 & 15) * 17, (value >> 8 & 15) * 17, (value >> 12) * 17


def pack(rgba: Rgba, fmt: Color) -> int:
    r, g, b, a = rgba
    if fmt == Color.ABGR8888:
        return r | g << 8 | b << 16 | a << 24
    if fmt == Color.BGR5650:
        return _q(r, 5) | _q(g, 6) << 5 | _q(b, 5) << 11
    if fmt == Color.ABGR5551:
        return _q(r, 5) | _q(g, 5) << 5 | _q(b, 5) << 10 | (a >= 128) << 15
    return _q(r, 4) | _q(g, 4) << 4 | _q(b, 4) << 8 | _q(a, 4) << 12


def to_rgba(data: bytes, fmt: Color) -> bytes:
    """Packed little-endian colours -> RGBA8."""
    if fmt == Color.ABGR8888:
        return bytes(data)
    return bytes(c for v in _words(data) for c in unpack(v, fmt))


def from_rgba(rgba: bytes, fmt: Color) -> bytes:
    """RGBA8 -> packed little-endian colours."""
    if fmt == Color.ABGR8888:
        return bytes(rgba)
    px = memoryview(rgba).cast("B")
    words = array(
        "H", (pack((r, g, b, a), fmt) for r, g, b, a in zip(*[iter(px)] * 4, strict=True))
    )
    if sys.byteorder == "big":
        words.byteswap()
    return words.tobytes()


def _q(v: int, bits: int) -> int:
    return round(v * ((1 << bits) - 1) / 255)


def _words(data: bytes) -> array[int]:
    words = array("H", data)
    if sys.byteorder == "big":
        words.byteswap()
    return words
