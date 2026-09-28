# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct
from pathlib import Path

import pytest
from mhp_formats import FormatError
from mhp_formats import pmo as fu
from mhp_formats.p3rd.pmo import Mesh, Pmo
from mhp_formats.pac import Pac
from mhp_formats.pmo import Block, BoneSlot, Group, Material
from mhp_formats.psp.ge import Command, Op, Prim
from mhp_formats.psp.vtype import BITS8, BITS16, VertexType, Vertices, quantize_vertices

VT = VertexType(texture=BITS16, normal=BITS8, position=BITS16, weight=BITS8, weight_count=3)
QUAD = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 1.0)]


def _quad(scale: float) -> Block:
    vertices = quantize_vertices(
        VT,
        [(x * scale, y, z * scale) for x, y, z in QUAD],
        (4.0, 4.0, 4.0),
        normals=[(0.0, 1.0, 0.0)] * 4,
        uvs=[(0.0, 0.0)] * 4,
        weights=[(0.5, 0.25, 0.25)] * 4,
    )
    return Block.build(vertices, [(0, 1, 2), (2, 1, 3)])


def _strips() -> Block:
    """MHP3rd's unindexed shape: one strip per VADDR, each from its own run of the buffer."""
    vt = VertexType(position=BITS16)
    commands = [Command(Op.ORIGIN), Command(Op.BASE), Command(Op.VADDR)]
    commands += [Command(Op.VTYPE, vt.to_word()), Command(Op.FFACE)]
    commands += [Command.prim(Prim.TRIANGLE_STRIP, 4), Command(Op.VADDR, 4), Command(Op.FFACE, 1)]
    commands += [Command.prim(Prim.TRIANGLE_STRIP, 3), Command(Op.OFFSETADDR), Command(Op.RET)]
    positions = [(i, 0, i % 2) for i in range(7)]
    return Block(commands, Vertices(vt, position=positions))  # type: ignore[arg-type]


def _model(**kwargs: object) -> Pmo:
    second = Mesh([Group(_strips(), 1), Group(_quad(0.5))], [1, 2], lighting=0, blend=0)
    second.scale, second.uv_offset = (2.0, 2.0, 2.0), (0.25, 0.0)
    first = Mesh([Group(_quad(1.0), 0, [BoneSlot(0, 1), BoneSlot(1, 2), BoneSlot(2, 3)])], [0])
    first.scale = (4.0, 4.0, 4.0)
    kwargs.setdefault("remap_table", False)
    materials = [Material(texture=i) for i in range(3)]
    return Pmo([first, second], materials, (4.0, 4.0, 4.0), 3.5, **kwargs)  # type: ignore[arg-type]


def test_round_trip():
    pmo = _model()
    data = pmo.to_bytes()
    assert Pmo.sniff(data) and not fu.Pmo.sniff(data) and not Pmo.needs_geometry(data)
    assert Pmo.from_bytes(data) == pmo
    groups, remaps = struct.unpack_from("<2I", data, 36)
    assert (groups, remaps) == (0x40 + 0x60, 0)
    list_offset, vertex_offset = struct.unpack_from("<2I", data, groups + 4)
    assert vertex_offset - list_offset == 4 * 9  # aligned to 4, not 16


def test_remap_table():
    pmo = _model(remap_table=True)
    pmo.meshes[1].materials = [2, 0]
    data = pmo.to_bytes()
    assert struct.unpack_from("<I", data, 40)[0] == 0x40 + 0x60 + 0x30
    assert Pmo.from_bytes(data) == pmo
    assert (pmo.material(1), pmo.material(2)) == (pmo.materials[0], pmo.materials[2])


def test_views():
    pmo = _model()
    assert pmo.scale_of(0) == (4.0, 4.0, 4.0) and pmo.scale_of(2) == (2.0, 2.0, 2.0)
    assert pmo.positions(2)[3] == (0.25, 0.0, 0.25)
    assert pmo.triangles(1) == [(0, 1, 2), (2, 1, 3), (5, 4, 6)]
    assert pmo.influences(2)[0] == [(1, 0.5), (2, 0.25), (3, 0.25)]
    assert Pmo.from_bytes(pmo.to_bytes()).groups()[1].block.commands[6] == Command(Op.VADDR, 4)
    plain = Pmo([fu.Mesh([Group(_quad(1.0))], [0])], [Material()], scale=(8.0, 8.0, 8.0))
    assert plain.scale_of(0) == (8.0, 8.0, 8.0)


def test_external():
    pmo = _model(external=True, geometry_tail=b"\xaa" * 5)
    data, geometry = pmo.to_bytes(), pmo.geometry_bytes()
    size, offset = struct.unpack_from("<I", data, 8)[0], struct.unpack_from("<I", data, 0x34)[0]
    assert offset == len(data) and size == len(data) + len(geometry) - 5
    assert Pmo.needs_geometry(data)
    assert Pmo.from_bytes(data, geometry) == pmo
    with pytest.raises(FormatError):
        Pmo.from_bytes(data)


def test_inplace():
    pmo = _model()
    original = pmo.to_bytes()
    assert pmo.to_bytes_inplace(original) == (original, [])
    external = _model(external=True)
    with pytest.raises(ValueError):
        external.to_bytes_inplace(external.to_bytes())


def test_unwritable():
    pmo = _model()
    pmo.meshes[1].materials = [2, 1]
    with pytest.raises(ValueError):
        pmo.to_bytes()
    with pytest.raises(ValueError):
        _model().geometry_bytes()
    with pytest.raises(ValueError):
        _model(external=True, tail=b"x").to_bytes()
    with pytest.raises(ValueError):
        Pmo([fu.Mesh([Group(_quad(1.0))], [0])], [Material()]).to_bytes()


def test_rejects():
    data = _model().to_bytes()
    assert not Pmo.needs_geometry(b"pmo\0")
    for blob in (fu.Pmo([fu.Mesh([Group(_quad(1.0))], [0])], [Material()]).to_bytes(), data[:60]):
        with pytest.raises(FormatError):
            Pmo.from_bytes(blob)
    with pytest.raises(FormatError):
        fu.Pmo.from_bytes(data)


def _pmos(data_dir: Path):
    for path in sorted(data_dir.glob("file_*.bin")):
        yield from _walk(path, path.name, path.read_bytes())


def _walk(path: Path, name: str, data: bytes):
    if Pmo.sniff(data):
        number = int(path.name[5:10])
        companion = path.with_name(f"file_{number + 1:05d}.bin")
        yield name, data, companion.read_bytes() if Pmo.needs_geometry(data) else None
    elif Pac.sniff(data):
        for i, entry in enumerate(Pac.from_bytes(data).entries):
            yield from _walk(path, f"{name}[{i}]", entry)


def test_round_trip_mhp3rd(mhp3rd_data):
    counts = {"inline": 0, "external": 0}
    for name, data, geometry in _pmos(mhp3rd_data):
        pmo = Pmo.from_bytes(data, geometry)
        assert pmo.to_bytes() == data, name
        if geometry is not None:
            assert pmo.geometry_bytes() == geometry, name
        counts["external" if geometry else "inline"] += 1
    assert counts == {"inline": 1994, "external": 226}


def test_brute(mhp3rd_data):
    """Its header's material count (24) was once read as the group count."""
    data = Pac.from_bytes((mhp3rd_data / "file_05248.bin").read_bytes()).entries[1]
    pmo = Pmo.from_bytes(data, (mhp3rd_data / "file_05249.bin").read_bytes())
    assert pmo.external and not pmo.remap_table
    assert (len(pmo.meshes), len(pmo.groups()), len(pmo.materials)) == (16, 88, 24)
    bones = {b for g in range(88) for row in pmo.influences(g) for b, w in row if w}
    assert (min(bones), max(bones)) == (1, 45)
