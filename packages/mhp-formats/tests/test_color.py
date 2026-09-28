# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhp_formats.psp.color import Color, from_rgba, pack, to_rgba, unpack


@pytest.mark.parametrize(
    ("fmt", "value", "rgba"),
    [
        (Color.BGR5650, 0xFFFF, (255, 255, 255, 255)),
        (Color.BGR5650, 0x001F, (255, 0, 0, 255)),
        (Color.BGR5650, 0x07E0, (0, 255, 0, 255)),
        (Color.BGR5650, 0x0821, (8, 4, 8, 255)),
        (Color.ABGR5551, 0x8000, (0, 0, 0, 255)),
        (Color.ABGR5551, 0x7C00, (0, 0, 255, 0)),
        (Color.ABGR4444, 0x1234, (0x44, 0x33, 0x22, 0x11)),
        (Color.ABGR8888, 0x11223344, (0x44, 0x33, 0x22, 0x11)),
    ],
)
def test_color(fmt, value, rgba):
    assert unpack(value, fmt) == rgba
    assert pack(rgba, fmt) == value


@pytest.mark.parametrize("fmt", [Color.BGR5650, Color.ABGR5551, Color.ABGR4444])
def test_round_trip(fmt):
    for value in range(1 << 16):
        assert pack(unpack(value, fmt), fmt) == value


def test_rounds():
    assert pack((4, 0, 0, 255), Color.BGR5650) == 0
    assert pack((5, 0, 0, 255), Color.BGR5650) == 1


@pytest.mark.parametrize("fmt", list(Color))
def test_bulk(fmt):
    rgba = bytes([255, 0, 0, 255, 0, 255, 0, 255])
    assert to_rgba(from_rgba(rgba, fmt), fmt) == rgba
