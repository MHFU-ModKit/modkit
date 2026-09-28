# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct
from pathlib import Path

import pytest
from mhp_formats import FormatError
from mhp_formats.pac import Pac
from mhp_formats.skeleton import BONE_TAG, FU_MAGIC, P3RD_MAGIC, Bone, Skeleton


def _bones(name=None):
    return [
        Bone(-1, 1, -1, (1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), name=name),
        Bone(0, -1, 2, (1.0, 0.5, 1.0), (0.25, 0.0, -1.5), (0.0, 12.0, 3.5), stream=1, name=name),
        Bone(0, -1, -1, position=(-4.0, 0.0, 0.0), kind=2, link=1, stream=2, name=name),
        Bone(kind=3, name=name),
    ]


def test_fu_layout():
    skel = Skeleton(_bones(), [0, 3])
    data = skel.to_bytes()
    assert struct.unpack_from("<8I", data) == (FU_MAGIC, 5, len(data), 0, 2, 0x14, 0, 3)
    bone = data[0x20 + 0x10C : 0x20 + 2 * 0x10C]
    assert struct.unpack_from("<3I4i", bone) == (BONE_TAG | 1, 1, 0x10C, 1, 0, -1, 2)
    assert struct.unpack_from("<4f", bone, 0x1C) == (1.0, 0.5, 1.0, 1.0)
    assert struct.unpack_from("<4f", bone, 0x3C) == (0.0, 12.0, 3.5, 1.0)
    assert struct.unpack_from("<iI", bone, 0x4C) == (-1, 1)
    assert not any(bone[0x54:])
    assert struct.unpack_from("<Ii", data, 0x20 + 2 * 0x10C + 0x48) == (0x3F800000, 1)
    assert len(data) == 0x20 + 4 * 0x10C
    assert Skeleton.from_bytes(data) == skel
    assert skel.roots() == [0, 3]


def test_p3rd_layout():
    skel = Skeleton(_bones(b"HD-00\0\xdd\x02"), [0], magic=P3RD_MAGIC)
    data = skel.to_bytes()
    assert struct.unpack_from("<7I", data) == (P3RD_MAGIC, 5, len(data), 0, 1, 0x10, 0)
    assert struct.unpack_from("<3I", data, 0x1C + 0x5C) == (BONE_TAG | 1, 1, 0x5C)
    assert data[0x1C + 0x54 : 0x1C + 0x5C] == b"HD-00\0\xdd\x02"
    assert len(data) == 0x1C + 4 * 0x5C
    assert Skeleton.from_bytes(data) == skel


def test_p3rd_layout_fu_magic():
    skel = Skeleton(_bones(b"LL-01\0\0\0"), [0, 45, 7])
    data = skel.to_bytes()
    assert struct.unpack_from("<I", data)[0] == FU_MAGIC
    assert struct.unpack_from("<I", data, 0x24 + 8)[0] == 0x5C
    assert Skeleton.from_bytes(data) == skel


def test_params_count():
    skel = Skeleton([Bone()], [0, 9], params_count=3, tail=b"\x5d" * 12)
    data = skel.to_bytes()
    assert struct.unpack_from("<3I", data, 12) == (0, 3, 0x14)
    assert data.endswith(b"\x5d" * 12)
    assert Skeleton.from_bytes(data) == skel


def test_empty():
    skel = Skeleton()
    assert Skeleton.from_bytes(skel.to_bytes()) == skel


def _data():
    return bytearray(Skeleton(_bones(), [0]).to_bytes())


def _poke(fmt, offset, *values):
    data = _data()
    struct.pack_into(fmt, data, offset, *values)
    return bytes(data)


@pytest.mark.parametrize(
    "data",
    [
        b"",
        _poke("<I", 0, 0x40000000),  # magic
        _poke("<I", 12, 1),  # the parameter section's tag
        _poke("<I", 8, 0x10000),  # size past the data
        _poke("<I", 4, 0),  # no parameter section
    ],
)
def test_not_a_skeleton(data):
    assert not Skeleton.sniff(data)
    with pytest.raises(FormatError):
        Skeleton.from_bytes(data)


@pytest.mark.parametrize(
    "data",
    [
        _poke("<I", 0x1C, 0x20000001),  # bone tag
        _poke("<I", 0x1C + 4, 2),  # bone count word
        _poke("<I", 0x1C + 0xC, 1),  # index
        _poke("<I", 0x1C + 8, 0x100),  # a section size neither game uses
        _poke("<f", 0x1C + 0x28, 0.0),  # w
        _poke("<B", 0x1C + 0x60, 1),  # MHFU's section does not end in zeros
        _poke("<I", 20, 0x16),  # parameter section size
        _poke("<I", 8, 0x1C + 3 * 0x10C),  # header size short of the bones
        _poke("<I", 4, 9),  # more bones than the file holds
        bytes(_data()[:0x100]),  # truncated
    ],
)
def test_rejects(data):
    with pytest.raises(FormatError):
        Skeleton.from_bytes(data)


@pytest.mark.parametrize("bone", [Bone(name=b"short"), Bone(kind=0x10000)])
def test_unwritable(bone):
    with pytest.raises(ValueError):
        Skeleton([bone]).to_bytes()


def _walk(name, data):
    yield name, data
    if Pac.sniff(data):
        for i, entry in enumerate(Pac.from_bytes(data).entries):
            yield from _walk(f"{name}[{i}]", entry)


def _round_trip(data_dir: Path) -> int:
    checked = 0
    for path in sorted(data_dir.glob("file_*")):
        for name, data in _walk(path.name, path.read_bytes()):
            if Skeleton.sniff(data):
                assert Skeleton.from_bytes(data).to_bytes() == data, name
                checked += 1
    return checked


def test_round_trip_mhfu(mhfu_data):
    assert _round_trip(mhfu_data) >= 4030


def test_round_trip_mhp3rd(mhp3rd_data):
    assert _round_trip(mhp3rd_data) >= 2140


def test_tigrex(mhfu_data):
    pac = Pac.from_bytes(min(mhfu_data.glob("file_06185.*")).read_bytes())
    skel = Skeleton.from_bytes(pac.entries[0])
    assert (skel.magic, len(skel.bones), skel.params) == (FU_MAGIC, 48, [0, 45])
    # each animation stream drives one run of bones
    assert [b.stream for b in skel.bones] == [0] * 31 + [1] * 9 + [2] * 5 + [3] * 3
    assert skel.roots() == [0, 45]
    assert {b.name for b in skel.bones} == {None}


def test_p3rd_names(mhp3rd_data):
    pac = Pac.from_bytes(min(mhp3rd_data.glob("file_05248.*")).read_bytes())
    skel = Skeleton.from_bytes(pac.entries[0])
    assert (skel.magic, len(skel.bones), skel.params) == (P3RD_MAGIC, 46, [0, 43])
    assert all(b.name is not None and len(b.name) == 8 for b in skel.bones)
