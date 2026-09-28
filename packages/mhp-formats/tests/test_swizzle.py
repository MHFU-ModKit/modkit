# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhp_formats.psp.swizzle import swizzle, unswizzle


def test_layout():
    # two blocks across, one band: each block is 8 lines of 16 bytes, left block first
    pitch = 32
    linear = bytes(range(256))
    blocks = swizzle(linear, pitch)
    rows = [linear[y * pitch : (y + 1) * pitch] for y in range(8)]
    assert blocks == b"".join(r[:16] for r in rows) + b"".join(r[16:] for r in rows)


@pytest.mark.parametrize(("pitch", "bands"), [(16, 1), (16, 3), (48, 2), (512, 4)])
def test_inverse(pitch, bands):
    data = bytes((i * 37 + i // 251) & 0xFF for i in range(pitch * 8 * bands))
    assert unswizzle(swizzle(data, pitch), pitch) == data
    assert swizzle(unswizzle(data, pitch), pitch) == data
    assert swizzle(data, pitch) != data or pitch == 16


def test_one_block_across_is_linear():
    data = bytes(range(128)) * 2
    assert swizzle(data, 16) == data


@pytest.mark.parametrize(("size", "pitch"), [(256, 24), (256, 0), (200, 16), (128, 32)])
def test_rejects(size, pitch):
    with pytest.raises(ValueError):
        swizzle(bytes(size), pitch)
    with pytest.raises(ValueError):
        unswizzle(bytes(size), pitch)
