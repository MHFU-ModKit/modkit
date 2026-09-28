import struct

import pytest
from mhfu.memory import Image, Space, Unmapped


def test_typed_reads():
    img = Image(struct.pack("<IhfB", 0xDEADBEEF, -2, 1.5, 7) + b"hi\0x", 0x1000)
    assert img.u32(0x1000) == 0xDEADBEEF
    assert img.s16(0x1004) == -2
    assert img.f32(0x1006) == 1.5
    assert img.u8(0x100A) == 7
    assert img.cstr(0x100B, 4) == "hi"


def test_writes_stay_in_the_copy():
    source = bytes(8)
    img = Image(source, 0x1000)
    img.write_u32(0x1004, 5)
    assert img.u32(0x1004) == 5
    assert source == bytes(8)


def test_unmapped():
    img = Image(bytes(8), 0x1000)
    with pytest.raises(Unmapped):
        img.u32(0x1006)
    with pytest.raises(Unmapped):
        img.u8(0xFFF)


def test_space():
    space = Space([Image(b"\1\0\0\0", 0x2000), Image(b"\2\0\0\0", 0x1000)])
    assert (space.u32(0x1000), space.u32(0x2000)) == (2, 1)
    assert 0x1800 not in space
    with pytest.raises(Unmapped):
        space.u8(0x1800)
    with pytest.raises(ValueError):
        Space([Image(bytes(8), 0x1000), Image(bytes(8), 0x1004)])
