# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct
from dataclasses import replace
from pathlib import Path

import pytest
from mhp_formats import FormatError
from mhp_formats.fu.stage import (
    BASE,
    GRID_AT,
    GRID_POINTER,
    TERM,
    TRI_SIZE,
    TRIS_POINTER,
    Collision,
    Environment,
    Hits,
    PlaneCheck,
    Stage,
    Tri,
    TriFlags,
    build_hits,
    cell_map,
    cells_for_tri,
    fit_grid,
)
from mhp_formats.pac import Pac


def _floor() -> list[Tri]:
    """Two quads of floor over x 100..1400, z 100..900, plus one wall."""
    out = []
    for x0 in (100.0, 750.0):
        a, b = (x0, 10.0, 100.0), (x0 + 650.0, 10.0, 100.0)
        c, d = (x0 + 650.0, 10.0, 900.0), (x0, 10.0, 900.0)
        out += [Tri.from_verts(a, d, c, TriFlags(1, 3, 1)), Tri.from_verts(a, c, b)]
    wall = Tri.from_verts((1200.0, 0.0, 300.0), (1200.0, 800.0, 300.0), (1200.0, 0.0, 700.0))
    return [*out, replace(wall, flags=TriFlags(0, 10, 0x0103))]


def _stage() -> Stage:
    tris = _floor()
    return Stage(
        terrain=b"pmo\0" + bytes(28),
        textures=b".TMH" + bytes(12),
        props=b"",
        environment=Environment(2, [0xAA, 0xCC, 0xFF, 0xFF], [-1000.0, 60000.0], bytes(156)),
        params=b"\x02\x01" + bytes(30),
        collision=Collision([build_hits(tris[4:]), build_hits(tris[:4])]),
        extra=[bytes(48)],
        tail=b"\x7f" * 5,
    )


def test_round_trip():
    stage = _stage()
    data = stage.to_bytes()
    assert Stage.sniff(data)
    assert Stage.from_bytes(data) == stage
    assert Pac.from_bytes(data).entries[5] == stage.collision.to_bytes()
    assert data.endswith(b"\x7f" * 5)


def test_offsets():
    stage = _stage()
    data = stage.to_bytes()
    table = stage.table()
    assert table == [struct.unpack_from("<2I", data, 4 + 8 * k) for k in range(7)]
    assert table[2] == (0, 0)
    coll, _ = table[5]
    for (at, size), hits in zip(stage.collision.table(), stage.collision.chunks, strict=True):
        chunk = data[coll + at : coll + at + size]
        assert chunk == hits.to_bytes()
        assert struct.unpack_from("<I", chunk, GRID_POINTER)[0] == GRID_AT - BASE
        assert struct.unpack_from("<I", chunk, TRIS_POINTER)[0] == hits.tri_offset - BASE
        assert struct.unpack_from(f"<{len(hits.cells)}I", chunk, GRID_AT) == tuple(hits.heads())
        last = hits.tri_offset + TRI_SIZE * (len(hits.tris) - 1)
        assert chunk[last:] == hits.tris[-1].to_bytes()


def test_placeholder():
    data = (6).to_bytes(4, "little") + bytes(48) + b"disc slack"
    assert Stage.sniff(data)
    stage = Stage.from_bytes(data)
    assert stage.collision is None and stage.environment is None and not stage.terrain
    assert stage.verify() == PlaneCheck(0, 0) and stage.verify().frac == 0.0
    assert stage.to_bytes() == data


@pytest.mark.parametrize(
    "data",
    [
        b"",
        bytes(64),
        (3).to_bytes(4, "little") + bytes(24),
        Pac([b"pmo\0", b"a", b"b", b"c", b"d", b"no collision"]).to_bytes(),
        Pac([b"xyz\0", b"a", b"b", b"c", b"d", Collision().to_bytes()]).to_bytes(),
    ],
)
def test_sniff_rejects(data):
    assert not Stage.sniff(data)


def test_too_few_entries():
    with pytest.raises(FormatError):
        Stage.from_bytes(Pac([b"pmo\0", b"a", b"b"]).to_bytes())


def test_environment():
    env = Environment(2, [1, 2, 3, 4], [0.0, 8000.0], b"\x01\x02")
    data = env.to_bytes()
    assert data[:6] == b"\x02\x00\x01\x02\x03\x04" and len(data) == 16
    assert Environment.sniff(data) and Environment.from_bytes(data) == env
    assert not Environment.sniff(data[:13])
    with pytest.raises(FormatError):
        Environment.from_bytes(data[:13])


def test_hits_layout():
    hits = build_hits(_floor())
    data = hits.to_bytes()
    size, cx, cz, nx, nz, ox, oz, grid, tris = struct.unpack_from("<I4I2i2I", data, 4)
    assert data[:4] == b"HITS" and size == len(data)
    assert (cx, cz, nx, nz, ox, oz, grid) == (501, 501, 3, 2, 0, 0, 0x20)
    assert tris == len(data) - 8 - 56 * 5
    heads = struct.unpack_from("<6I", data, 0x28)
    assert heads[0] == 0x20 + 4 * 6 and list(heads) == hits.heads()
    assert hits.tri_offset == 8 + tris
    first = struct.unpack_from(f"<{len(hits.cells[0]) + 1}I", data, 8 + heads[0])
    assert list(first) == [t * 56 for t in hits.cells[0]] + [TERM]
    assert Hits.from_bytes(data) == hits


def test_hits_empty():
    assert Hits.from_bytes(Hits().to_bytes()) == Hits()
    assert build_hits([]).grid == (1, 1)


def _corrupt(data: bytes, off: int, word: int) -> bytes:
    out = bytearray(data)
    struct.pack_into("<I", out, off, word)
    return bytes(out)


def _malformed() -> list[bytes]:
    data = build_hits(_floor()).to_bytes()
    tri_at = 8 + struct.unpack_from("<I", data, 0x24)[0]
    lists_at = 0x28 + 4 * 6
    gap = data[:tri_at] + bytes(56) + data[tri_at:]
    return [
        b"HIT",
        b"NOPE" + data[4:],
        _corrupt(data, 4, len(data) + 1),
        _corrupt(data, 0x20, 0x24),
        _corrupt(data, 0x24, len(data)),
        _corrupt(data, 0x28, 0x20 + 4 * 6 + 4),
        _corrupt(data, tri_at - 4, 0),
        _corrupt(data, lists_at, 57),
        _corrupt(data, lists_at, 56 * 5),
        _corrupt(_corrupt(gap, 0x24, tri_at - 8 + 56), 4, len(gap)),
        _corrupt(data, 4, len(data) + 4) + bytes(4),
    ]


@pytest.mark.parametrize("data", _malformed())
def test_hits_rejects(data):
    with pytest.raises(FormatError):
        Hits.from_bytes(data)


def test_hits_unbuildable():
    hits = build_hits(_floor())
    with pytest.raises(ValueError):
        replace(hits, cells=hits.cells[1:]).to_bytes()
    with pytest.raises(ValueError):
        replace(hits, cells=[[5], *hits.cells[1:]]).to_bytes()


def test_tri():
    t = Tri.from_verts((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
    assert t.normal == (0.0, 1.0, 0.0) and t.plane_d == 0.0 and not t.vertical()
    s = Tri.from_verts((0.1, 0.2, 0.3), (1.1, 0.2, 0.3), (0.1, 1.2, 0.3))
    assert s.v0 == struct.unpack("<3f", struct.pack("<3f", 0.1, 0.2, 0.3))
    assert _floor()[-1].vertical()
    with pytest.raises(ValueError):
        Tri.from_verts((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), (2.0, 2.0, 2.0))


def test_tri_flags():
    flags = TriFlags.from_word(0x0103_0A02)
    assert flags == TriFlags(2, 10, 0x0103) and flags.word == 0x0103_0A02


def test_verify():
    hits = build_hits(_floor())
    assert hits.verify() == PlaneCheck(5, 0)
    hits.tris[0] = replace(hits.tris[0], plane_d=500.0)
    hits.tris[1] = replace(hits.tris[1], normal=(0.0, 2.0, 0.0))
    assert hits.verify() == PlaneCheck(3, 2)
    assert Collision([hits, build_hits(_floor())]).verify() == PlaneCheck(8, 2)


def test_grid_check():
    hits = build_hits(_floor())
    assert hits.grid_check() == (sum(map(len, hits.cells)), 0, 0, 0, 0)
    wall = 4
    hits.cells = [[t for t in run if t != wall] for run in hits.cells]
    hits.cells[0].append(3)
    check = hits.grid_check()
    assert (check.missing, check.missing_vertical, check.extra, check.unlisted) == (2, 2, 1, 1)


def test_cells_for_tri():
    tri = Tri.from_verts((600.0, 0.0, 1100.0), (600.0, 0.0, 1200.0), (700.0, 0.0, 1100.0))
    assert cells_for_tri(tri, (4, 4), (501, 501)) == [1 * 4 + 2]
    assert cells_for_tri(tri, (4, 4), (501, 501), origin=(200, 200)) == [0 * 4 + 1]
    assert len(cells_for_tri(tri, (4, 4), (501, 501), pad=150.0)) == 4
    sliver = Tri.from_verts((0.0, 0.0, 990.0), (990.0, 0.0, 0.0), (1000.0, 0.0, 10.0))
    assert 1 * 2 + 1 not in cells_for_tri(sliver, (2, 2), (501, 501))
    assert cell_map([tri, sliver], (4, 4), (501, 501))[6] == {0}


def test_cell_of():
    hits = build_hits(_floor())
    assert hits.cell_of(1300.0, 600.0) == 2 * 2 + 1
    assert hits.cell_of(-1.0, 0.0) is None and hits.cell_of(0.0, 1100.0) is None


def test_fit_grid():
    tris = _floor()
    assert fit_grid(tris) == (3, 2)
    assert fit_grid(tris, cell=(100, 100), cap=10) == (10, 10)
    assert fit_grid(tris, origin=(600, 0)) == (2, 2)


def test_collision_sniff():
    data = Collision([build_hits(_floor())]).to_bytes()
    assert Collision.sniff(data) and Collision.from_bytes(data).to_bytes() == data
    assert not Collision.sniff(Pac([b"HITX"]).to_bytes()) and not Collision.sniff(b"")


def test_round_trip_mhfu(mhfu_data: Path):
    stages = placeholders = passing = 0
    for path in sorted(mhfu_data.glob("file_*")):
        data = path.read_bytes()
        if not Stage.sniff(data):
            continue
        stages += 1
        stage = Stage.from_bytes(data)
        assert stage.to_bytes() == data, path.name
        if stage.collision is None:
            placeholders += 1
            continue
        nested = Pac.from_bytes(data).entries[5]
        assert stage.collision.to_bytes() == nested, path.name
        for hits, raw in zip(stage.collision.chunks, Pac.from_bytes(nested).entries, strict=True):
            assert hits.to_bytes() == raw, path.name
        passing += stage.verify().frac > 0.99
    assert (stages, placeholders, passing) == (282, 20, 245)
