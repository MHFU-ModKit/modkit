# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct
from collections.abc import Iterator
from pathlib import Path

import pytest
from mhp_formats import FormatError
from mhp_formats.pac import Pac
from mhp_formats.psp.swizzle import swizzle
from mhp_formats.tmh import MAGIC, Clut, Rgba, Tmh, TmhImage, quantize

BPP = (16, 16, 16, 32, 4, 8, 16, 32, 4, 8, 8)

# one raw colour per direct format and the RGBA8 the GE widens it to
ANCHORS = {
    0: [(0x0000, (0, 0, 0, 255)), (0xFFFF, (255, 255, 255, 255)), (0x0821, (8, 4, 8, 255)),
        (0x8410, (132, 130, 132, 255)), (0x0003, (24, 0, 0, 255))],
    1: [(0x8000, (0, 0, 0, 255)), (0x7FFF, (255, 255, 255, 0)), (0x0003, (24, 0, 0, 0)),
        (0x0220, (0, 140, 0, 0)), (0xC210, (132, 132, 132, 255))],
    2: [(0x1234, (68, 51, 34, 17)), (0xF00F, (255, 0, 0, 255)), (0x0FF0, (0, 255, 255, 0))],
    3: [(0x04030201, (1, 2, 3, 4)), (0xFF00FF00, (0, 255, 0, 255))],
}  # fmt: skip


def _raw(mode: int, values: list[int]) -> bytes:
    """Pack pixel values at a mode's bit depth, low nibble first."""
    if BPP[mode] == 4:
        values = values + [0] * (len(values) % 2)
        return bytes(a | b << 4 for a, b in zip(values[0::2], values[1::2], strict=True))
    return b"".join(v.to_bytes(BPP[mode] // 8, "little") for v in values)


def _stored(mode: int, width: int, height: int, values: list[int]) -> bytes:
    """Pixel values as a bank stores them: swizzled when the rows are whole blocks."""
    linear = _raw(mode, values)
    pitch = width * BPP[mode] // 8
    if width * BPP[mode] % 128:
        return linear
    return swizzle(linear, pitch)


def _palette(clut_mode: int, n: int) -> tuple[Clut, list[Rgba]]:
    """n distinct colours in a CLUT format, and what each decodes to."""
    if clut_mode == 3:
        rgba = [(k & 255, k * 7 & 255, k * 13 & 255, 255 - k) for k in range(n)]
        return Clut(3, b"".join(bytes(c) for c in rgba)), rgba
    raw = [k * 0x9E37 & 0xFFFF for k in range(n)]
    clut = Clut(clut_mode, _raw(clut_mode, raw))
    return clut, clut.colours()


def _pattern(width: int, height: int, n: int) -> list[int]:
    return [(p * 5 + p // width) % n for p in range(width * height)]


def test_layout():
    img = TmhImage(4, 32, 8, bytes(range(128)), Clut(3, bytes(64)), reserved=7)
    data = Tmh([img], reserved=9, tail=b"xy").to_bytes()
    assert data[:16] == MAGIC + struct.pack("<2I", 1, 9)
    assert struct.unpack_from("<4I", data, 16) == (32 + 128 + 16 + 64, 7, 1, 1)
    assert struct.unpack_from("<3I2H", data, 32) == (144, 1, 4, 32, 8)
    assert data[48:176] == bytes(range(128))
    assert struct.unpack_from("<4I", data, 176) == (80, 2, 3, 16)
    assert data.endswith(bytes(64) + b"xy")
    assert Tmh.from_bytes(data) == Tmh([img], 9, b"xy")


def test_round_trip():
    bank = Tmh(
        [
            TmhImage(3, 4, 8, bytes(range(128))),
            TmhImage(5, 16, 8, bytes(128), Clut(1, bytes(512))),
            TmhImage(8, 4, 4, bytes(8)),
            TmhImage(4, 0, 0, b"", Clut(0, b"")),
        ]
    )
    data = bank.to_bytes()
    assert Tmh.sniff(data)
    assert Tmh.from_bytes(data) == bank
    assert Tmh.from_bytes(Tmh().to_bytes()) == Tmh()


def _valid() -> bytearray:
    return bytearray(Tmh([TmhImage(4, 32, 8, bytes(128), Clut(1, bytes(32)))]).to_bytes())


@pytest.mark.parametrize(
    ("at", "value"),
    [
        (0, 0x30484D54),  # magic
        (8, 2),  # count past the end
        (16, 999),  # image size
        (24, 2),  # image header +0x08
        (28, 2),  # CLUT flag
        (36, 2),  # pixel block tag
        (40, 11),  # pixel format
        (180, 3),  # CLUT block tag
        (184, 4),  # CLUT format
        (188, 17),  # CLUT entries
    ],
)
def test_rejects(at, value):
    data = _valid()
    struct.pack_into("<I", data, at, value)
    with pytest.raises(FormatError):
        Tmh.from_bytes(bytes(data))


def test_rejects_short():
    assert not Tmh.sniff(b".TMH0.1")
    with pytest.raises(FormatError):
        Tmh.from_bytes(bytes(_valid()[:-1]))
    with pytest.raises(FormatError):
        Tmh.from_bytes(bytes(_valid()[:100]))
    with pytest.raises(FormatError):
        Tmh([TmhImage(12, 1, 1, b"")]).to_bytes()


@pytest.mark.parametrize("mode", [0, 1, 2, 3])
@pytest.mark.parametrize(("width", "height", "swizzled"), [(None, 16, True), (3, 5, False)])
def test_decode_direct(mode, width, height, swizzled):
    width = width or 256 // BPP[mode]  # two blocks across
    anchors = ANCHORS[mode]
    picks = _pattern(width, height, len(anchors))
    img = TmhImage(
        mode, width, height, _stored(mode, width, height, [anchors[k][0] for k in picks])
    )
    assert img.swizzled == swizzled
    assert img.decode() == b"".join(bytes(anchors[k][1]) for k in picks)
    assert img.encode(img.decode()) == img


@pytest.mark.parametrize("mode", [4, 5, 6, 7])
@pytest.mark.parametrize("clut_mode", [0, 1, 2, 3])
@pytest.mark.parametrize(("width", "height", "swizzled"), [(None, 16, True), (5, 3, False)])
def test_decode_indexed(mode, clut_mode, width, height, swizzled):
    width = width or 256 // BPP[mode]
    n = 16 if mode == 4 else 64
    clut, colours = _palette(clut_mode, n)
    indices = _pattern(width, height, n)
    img = TmhImage(mode, width, height, _stored(mode, width, height, indices), clut)
    assert img.swizzled == swizzled
    assert img.decode() == b"".join(bytes(colours[i]) for i in indices)
    assert img.palette_colours() == colours
    assert img.encode(img.decode(), colours=colours) == img


def test_palettes():
    clut, colours = _palette(3, 48)
    img = TmhImage(4, 32, 8, _stored(4, 32, 8, _pattern(32, 8, 16)), clut)
    assert img.palette_colours(2) == colours[32:]
    assert img.decode(1) == b"".join(bytes(colours[16 + i]) for i in _pattern(32, 8, 16))
    with pytest.raises(ValueError):
        img.decode(3)
    with pytest.raises(ValueError):
        img.palette_colours(-1)


def test_short_band():
    # 12 rows: the second band stores its left block and stops, the GE reads on past the data
    width, height = 64, 12
    clut, colours = _palette(1, 16)
    whole = _pattern(width, 16, 15)
    pixels = swizzle(_raw(4, [i + 1 for i in whole]), 32)[: 32 * height]
    img = TmhImage(4, width, height, pixels, clut)
    got = img.decode()
    for y in range(height):
        for x in range(width):
            want = 0 if y >= 8 and x >= 32 else whole[y * width + x] + 1
            assert got[4 * (y * width + x) : 4 * (y * width + x + 1)] == bytes(colours[want])
    assert img.encode(got, colours=colours) == img


def test_linear_padding():
    img = TmhImage(4, 5, 3, _raw(4, _pattern(5, 3, 16)) + b"\xaa" * 8, _palette(0, 16)[0])
    out = img.encode(bytes(4 * 15))
    assert len(out.pixels) == len(img.pixels)
    assert out.pixels.endswith(b"\xaa" * 8)


def _dxt1_block(lines: bytes, c1: int, c2: int) -> bytes:
    return lines + struct.pack("<2H", c1, c2)


def test_decode_dxt1():
    lines = bytes((0b11100100, 0x00, 0xFF, 0b00011011))
    four = _dxt1_block(lines, 0xF800, 0x001F)  # c1 > c2: two thirds between the ends
    three = _dxt1_block(lines, 0x07E0, 0xFFFF)  # c1 <= c2: a midpoint and transparent black
    a = [(248, 0, 0, 255), (0, 0, 248, 255), (165, 0, 82, 255), (82, 0, 165, 255)]
    b = [(0, 252, 0, 255), (248, 252, 248, 255), (124, 252, 124, 255), (0, 0, 0, 0)]
    rows = [[0, 1, 2, 3], [0, 0, 0, 0], [3, 3, 3, 3], [3, 2, 1, 0]]
    want = [[a[k] for k in r] + [b[k] for k in r] for r in rows]
    img = TmhImage(8, 8, 4, four + three)
    assert not img.swizzled
    assert img.decode() == b"".join(bytes(c) for row in want for c in row)
    cropped = TmhImage(8, 6, 3, four + three)
    assert cropped.decode() == b"".join(bytes(c) for row in want[:3] for c in row[:6])


@pytest.mark.parametrize("mode", [9, 10])
def test_dxt3_dxt5_not_decoded(mode):
    with pytest.raises(NotImplementedError):
        TmhImage(mode, 4, 4, bytes(16)).decode()


def test_decode_rejects():
    with pytest.raises(FormatError):
        TmhImage(5, 16, 8, bytes(127), Clut(3, bytes(1024))).decode()
    with pytest.raises(FormatError):
        TmhImage(5, 16, 8, b"\x10" * 128, Clut(3, bytes(64))).decode()
    with pytest.raises(FormatError):
        TmhImage(4, 32, 8, bytes(128)).decode()
    with pytest.raises(FormatError):
        TmhImage(11, 4, 4, bytes(64)).decode()


def test_encode_quantises():
    width, height = 64, 8
    rgba = b"".join(bytes((x * 4, y * 32, (x + y) & 255, 255)) for y in range(8) for x in range(64))
    clut, colours = _palette(3, 32)
    img = TmhImage(4, width, height, bytes(256), clut)
    out = img.encode(rgba, palette=1)
    assert len(out.pixels) == len(img.pixels) and len(out.clut.data) == len(clut.data)
    assert out.palette_colours(0) == colours[:16]
    got = out.decode(1)
    assert len({got[i : i + 4] for i in range(0, len(got), 4)}) <= 16
    assert len(Tmh([out]).to_bytes()) == len(Tmh([img]).to_bytes())


def test_encode_rejects():
    img = TmhImage(4, 32, 8, bytes(128), _palette(3, 16)[0])
    with pytest.raises(ValueError):
        img.encode(bytes(4 * 255))
    with pytest.raises(ValueError):
        img.encode(bytes(4 * 256), palette=1)
    with pytest.raises(ValueError):
        img.encode(bytes(4 * 256), colours=[(0, 0, 0, 0)] * 17)
    with pytest.raises(ValueError):
        TmhImage(8, 4, 4, bytes(8)).encode(bytes(64))


def test_quantize():
    rgba = b"".join(bytes((x * 8, y * 8, x ^ y, 255)) for y in range(32) for x in range(32))
    for n in (16, 256):
        indices, palette = quantize(rgba, n)
        assert len(palette) == n and len(indices) == 32 * 32 and max(indices) < n
    # few enough colours are kept exactly, in order
    indices, palette = quantize(bytes((9, 9, 9, 9, 1, 2, 3, 4)) * 3, 4)
    assert palette == [(1, 2, 3, 4), (9, 9, 9, 9), (0, 0, 0, 0), (0, 0, 0, 0)]
    assert indices == [1, 0] * 3
    # given colours, each pixel maps to the nearest
    red = [(0, 0, 0, 0), (255, 0, 0, 255)]
    indices, palette = quantize(bytes((250, 0, 0, 255, 5, 5, 5, 0)), 4, red)
    assert indices == [1, 0] and palette == red + [(0, 0, 0, 0)] * 2


def _banks(data_dir: Path) -> Iterator[tuple[str, bytes]]:
    for path in sorted(data_dir.glob("file_*")):
        data = path.read_bytes()
        if path.suffix == ".tmh":
            assert Tmh.sniff(data), path.name
        yield from _find(path.name, data)


def _find(name: str, data: bytes) -> Iterator[tuple[str, bytes]]:
    if Tmh.sniff(data):
        yield name, data
    elif Pac.sniff(data):
        for i, entry in enumerate(Pac.from_bytes(data).entries):
            yield from _find(f"{name}/{i}", entry)
    elif (at := data.find(MAGIC)) > 0:  # a bank closing some other structure
        yield f"{name}@{at}", data[at:]


def _check(data_dir: Path) -> tuple[int, int]:
    banks = images = 0
    for name, data in _banks(data_dir):
        bank = Tmh.from_bytes(data)
        assert bank.to_bytes() == data, name
        for i, img in enumerate(bank.images):
            rgba = img.decode()
            assert len(rgba) == 4 * img.width * img.height, (name, i)
            # re-encoding onto its own palette gives the shipped bytes back, unless the palette
            # holds a colour twice and the choice between the two is the image's own
            if img.mode in (4, 5):
                colours = img.palette_colours()
                if len(set(colours)) == len(colours):
                    assert img.encode(rgba, colours=colours) == img, (name, i)
        banks += 1
        images += len(bank.images)
    return banks, images


def test_round_trip_mhfu(mhfu_data):
    banks, images = _check(mhfu_data)
    assert banks > 4000 and images > 10000


def test_round_trip_mhp3rd(mhp3rd_data):
    banks, images = _check(mhp3rd_data)
    assert banks > 2000 and images > 9000
