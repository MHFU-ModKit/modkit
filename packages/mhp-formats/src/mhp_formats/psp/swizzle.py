# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The GE's texture swizzle: rows cut into blocks of 16 bytes by 8 rows, stored block after block,
left to right and top to bottom. Bytes, not pixels, so it serves every bit depth."""

from array import array

BLOCK_BYTES = 16
BLOCK_ROWS = 8


def swizzle(data: bytes, pitch: int) -> bytes:
    """Linear rows of `pitch` bytes -> blocks. Whole blocks only: the rows must come in eights."""
    return _move(data, pitch, to_blocks=True)


def unswizzle(data: bytes, pitch: int) -> bytes:
    """Blocks -> linear rows of `pitch` bytes; the exact inverse of `swizzle`."""
    return _move(data, pitch, to_blocks=False)


def _move(data: bytes, pitch: int, to_blocks: bool) -> bytes:
    if pitch <= 0 or pitch % BLOCK_BYTES or len(data) % (pitch * BLOCK_ROWS):
        raise ValueError(f"{len(data)} bytes are not whole 16x8 blocks of a {pitch}-byte pitch")
    # 8-byte words, two per block line; one strided slice moves a word column of a whole row
    src = array("Q", data)
    out = array("Q", bytes(len(data)))
    words = pitch // 8
    for y in range(len(src) // words):
        band, line = divmod(y, BLOCK_ROWS)
        block = band * BLOCK_ROWS * words + line * 2
        for k in (0, 1):
            row = slice(y * words + k, (y + 1) * words, 2)
            blocks = slice(block + k, (band + 1) * BLOCK_ROWS * words, 16)
            if to_blocks:
                out[blocks] = src[row]
            else:
                out[row] = src[blocks]
    return out.tobytes()
