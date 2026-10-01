# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct
from pathlib import Path

import pytest
from mhp_formats import FormatError
from mhp_formats.fu.stage import Stage
from mhp_formats.pac import Pac
from mhp_formats.pmo import Block, BoneSlot, Group, Material, Mesh, Pmo
from mhp_formats.psp.ge import Command, Op, Prim
from mhp_formats.psp.vtype import (
    BITS8,
    BITS16,
    COLOR_5650,
    FLOAT,
    VertexType,
    Vertices,
    quantize_vertices,
)

SKINNED = VertexType(texture=BITS16, normal=BITS8, position=BITS16, weight=BITS8, weight_count=2)
STAGE = VertexType(texture=BITS16, color=COLOR_5650, position=BITS16)
QUAD = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 1.0)]
QUAD_TRIS = [(0, 1, 2), (2, 1, 3)]


def _quad(dx: float = 0.0) -> Block:
    vertices = quantize_vertices(
        SKINNED,
        [(x + dx, y, z) for x, y, z in QUAD],
        (2.0, 2.0, 2.0),
        normals=[(0.0, 1.0, 0.0)] * 4,
        uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)],
        weights=[(1.0, 0.0), (0.5, 0.5), (0.0, 1.0), (1.0, 0.0)],
    )
    return Block.build(vertices, QUAD_TRIS)


def _model() -> Pmo:
    return Pmo(
        meshes=[
            Mesh(
                [
                    Group(_quad(), 0, [BoneSlot(0, 3), BoneSlot(1, 4)]),
                    Group(_quad(0.5), 1, [BoneSlot(1, 5)]),
                ],
                materials=[1, 0],
            ),
            Mesh([Group(_quad(-1.0))], [1], uv_scale=(1.5, 1.0), lighting=6, blend=0xDF000032),
        ],
        materials=[Material(texture=0), Material(texture=1, reserved=0x1234)],
        scale=(2.0, 2.0, 2.0),
        clip=2.0,
        tail=b"\x7f" * 3,
    )


def _grid(n: int = 4) -> Block:
    """A stage-like block: an n x n vertex grid drawn one strip per row, then a loose triangle."""
    positions = [(float(x), 0.0, float(z)) for z in range(n) for x in range(n)]
    vertices = quantize_vertices(
        STAGE,
        positions,
        (8.0, 8.0, 8.0),
        uvs=[(0.25, 0.25)] * len(positions),
        colors=[(255, 255, 255, 255)] * len(positions),
    )
    vt = VertexType(texture=BITS16, color=COLOR_5650, position=BITS16, index=BITS8)
    vertices.vtype = vt
    commands = [Command(Op.ORIGIN), Command(Op.BASE), Command(Op.IADDR), Command(Op.VADDR)]
    commands += [Command(Op.VTYPE, vt.to_word()), Command(Op.FFACE)]
    indices: list[int] = []
    for z in range(n - 1):
        row = [i for x in range(n) for i in (z * n + x, (z + 1) * n + x)]
        commands.append(Command.prim(Prim.TRIANGLE_STRIP, len(row)))
        indices += row
    commands += [Command(Op.FFACE, 1), Command.prim(Prim.TRIANGLES, 3)]
    indices += [0, 1, n]
    commands += [Command(Op.OFFSETADDR), Command(Op.RET)]
    return Block(commands, vertices, indices)


def test_round_trip():
    pmo = _model()
    data = pmo.to_bytes()
    assert Pmo.sniff(data)
    assert Pmo.from_bytes(data) == pmo
    size, meshes, groups, remaps = (struct.unpack_from("<I", data, at)[0] for at in (8, 32, 36, 40))
    assert size == len(data) - 3
    assert (meshes, groups) == (0x40, 0x70)
    assert data[remaps : remaps + 3] == bytes([1, 0, 1])


def test_one_mesh():
    """One 0x18 mesh record padded to 16 is what read as a 0x20 record."""
    pmo = Pmo([Mesh([Group(_quad())], [0])], [Material()])
    data = pmo.to_bytes()
    assert struct.unpack_from("<2I", data, 32) == (0x40, 0x60)
    assert Pmo.from_bytes(data) == pmo


def test_views():
    pmo = _model()
    assert len(pmo.groups()) == 3
    assert pmo.positions(1)[3] == (1.5, 0.0, 1.0)
    assert pmo.scale_of(2) == (2.0, 2.0, 2.0)
    assert pmo.triangles(0) == QUAD_TRIS
    assert [pmo.material(g) for g in range(3)] == [pmo.materials[i] for i in (1, 0, 1)]
    assert [pmo.palette(g) for g in range(3)] == [[3, 4], [3, 5], [3, 5]]
    assert pmo.influences(0)[1] == [(3, 0.5), (4, 0.5)]
    pmo.meshes[0].groups[0].material = 7
    pmo.meshes[1].materials = [9]
    assert pmo.material(0) is None and pmo.material(2) is None
    with pytest.raises(IndexError):
        pmo.mesh_of(3)


def test_rigid_influences():
    block = _grid()
    pmo = Pmo([Mesh([Group(block)], [0])], [Material()])
    assert pmo.influences(0) == [[]] * 16
    pmo.meshes[0].groups[0].bones = [BoneSlot(0, 7)]
    assert pmo.influences(0)[5] == [(7, 1.0)]


def test_shared_block():
    block = _quad()
    pmo = Pmo([Mesh([Group(block), Group(block, 1)], [0, 0])], [Material()])
    data = pmo.to_bytes()
    assert len(data) == len(Pmo([Mesh([Group(block)], [0])], [Material()]).to_bytes()) + 0x10
    back = Pmo.from_bytes(data)
    assert back == pmo
    assert back.groups()[0].block is back.groups()[1].block


def test_build_index_width():
    vt = VertexType(position=FLOAT)
    rows = [(float(i), 0.0, 0.0) for i in range(257)]
    assert Block.build(quantize_vertices(vt, rows[:256]), [(0, 1, 255)]).vertices.vtype.index == 1
    wide = Block.build(quantize_vertices(vt, rows), [(0, 1, 256)])
    assert wide.vertices.vtype.index == 2
    assert Pmo([Mesh([Group(wide)], [0])], [Material()]).to_bytes()
    with pytest.raises(ValueError):
        Block.build(quantize_vertices(vt, rows[:3]), [(0, 1, 3)])
    with pytest.raises(ValueError):
        Block.build(Vertices(vt, position=[(0.0, 0.0, 0.0)] * 0x10001), [])


def test_quantize_vertices():
    v = quantize_vertices(STAGE, [(1.0, -9.0, 0.5)], (2.0, 2.0, 2.0), uvs=[(0.5, 2.0)],
                          colors=[(8, 4, 8, 255)])  # fmt: skip
    assert v.position == [(16384, -32768, 8192)]
    assert v.texture == [(16384, 65535)]
    assert v.color == [1 | 1 << 5 | 1 << 11]
    f = quantize_vertices(VertexType(position=FLOAT), [(1.0, 2.0, 3.0)], (2.0, 4.0, 1.0))
    assert f.positions((2.0, 4.0, 1.0)) == [(1.0, 2.0, 3.0)]
    for kwargs in ({}, {"uvs": [(0.0, 0.0)], "colors": [(0, 0, 0, 0)], "normals": [(0, 0, 1)]}):
        with pytest.raises(ValueError):
            quantize_vertices(STAGE, [(0.0, 0.0, 0.0)], **kwargs)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        quantize_vertices(STAGE, [(0.0, 0.0, 0.0)], uvs=[], colors=[(0, 0, 0, 0)])
    with pytest.raises(ValueError):
        quantize_vertices(SKINNED, QUAD[:1], normals=[(0, 0, 1)], uvs=[(0, 0)], weights=[(1,)])
    with pytest.raises(ValueError):
        quantize_vertices(VertexType(position=BITS16, morph_count=2), QUAD)


def test_clear():
    pmo = _model()
    pmo.meshes[0].groups[0].block.clear(keep_layout=True)
    block = pmo.meshes[0].groups[1].block
    block.clear()
    assert pmo.triangles(0) == [] and pmo.triangles(1) == []
    assert len(pmo.groups()[0].block.vertices) == 4 and len(block.vertices) == 0
    assert Op.NOP in [c.op for c in pmo.groups()[0].block.commands]
    back = Pmo.from_bytes(pmo.to_bytes())
    assert back.triangles(0) == [] and len(back.groups()[0].block.vertices) == 0  # undrawn
    assert back.groups()[1] == pmo.groups()[1]


def test_clear_prims():
    block = _grid()
    before = block.triangles()
    assert block.clear_prims([0, 3]) == 2
    after = block.triangles()
    assert after[6:18] == before[6:18]
    assert all(len(set(t)) == 1 for t in after[:6] + after[18:])
    with pytest.raises(ValueError):
        block.clear_prims([4])
    quads = _quads()
    assert quads.clear_prims([0, 4]) == 1


def test_spans():
    assert _quads().spans() == [(0, 0), (0, 4), (4, 4), (8, 6), (14, 2)]
    with pytest.raises(ValueError):
        Block([], Vertices(VertexType(position=BITS16))).spans()


def test_budget():
    block = _grid()
    assert block.budget() == (4, 27, 19, 16)
    assert block.budget([1]) == (1, 8, 6, 0)
    assert block.budget([0, 3]).vertices == 4
    assert _quads().budget() == (5, 16, 6, 14)
    with pytest.raises(ValueError):
        Block([], Vertices(VertexType(position=BITS16))).budget()


def test_pack():
    block = _grid()
    tris = QUAD_TRIS + [(4, 5, 6)]
    positions = [*QUAD, (4.0, 4.0, 4.0), (5.0, 4.0, 4.0), (4.0, 4.0, 5.0)]
    packed = block.pack(
        positions, tris, (8.0, 8.0, 8.0), uvs=[(0.5, 0.5)] * 7, color=(0, 0, 0, 255)
    )
    assert packed == (4, 4, 3, 0, 0, 7, 9, False)
    drawn = {t for t in block.triangles() if len(set(t)) == 3}
    got = {tuple(tuple(block.vertices.positions((8.0, 8.0, 8.0))[i]) for i in t) for t in drawn}
    assert got == {tuple(positions[i] for i in t) for t in tris}
    assert block.vertices.texture[block.indices[0]] == (16384, 16384)
    assert block.vertices.color[block.indices[0]] == 0


def _quads() -> Block:
    """Quads sharing no vertex: an empty strip, two strips, a list, a 2-index strip."""
    block = Block.build(_grid().vertices, [])
    block.vertices.position = block.vertices.position[:14]
    block.vertices.texture = block.vertices.texture[:14]
    block.vertices.color = block.vertices.color[:14]
    strip = Command.prim(Prim.TRIANGLE_STRIP, 4)
    block.commands[6:6] = [Command.prim(Prim.TRIANGLE_STRIP, 0), strip, strip]
    block.commands[9:9] = [Command(Op.FFACE, 1), Command.prim(Prim.TRIANGLES, 6)]
    block.commands[11:11] = [Command.prim(Prim.TRIANGLE_STRIP, 2)]
    block.indices = [*range(8), 8, 9, 10, 10, 9, 11, 12, 13]
    return block


def test_pack_positions_only():
    block = _quads()
    packed = block.pack(QUAD, QUAD_TRIS, (8.0, 8.0, 8.0), [0, 1, 2, 4], reindex=False)
    assert packed == (4, 2, 2, 0, 0, 0, 8, False)
    assert block.indices == _quads().indices
    positions = block.vertices.positions((8.0, 8.0, 8.0))
    assert positions[:4] == QUAD and positions[4:8] == [QUAD[0]] * 4
    assert positions[8:] == _quads().vertices.positions((8.0, 8.0, 8.0))[8:]


def test_pack_list():
    block = _quads()
    packed = block.pack(QUAD, QUAD_TRIS, (8.0, 8.0, 8.0), [3])
    assert (packed.used, packed.triangles, packed.vertices_used) == (1, 2, 4)
    positions = block.vertices.positions((8.0, 8.0, 8.0))
    drawn = [tuple(positions[i] for i in t) for t in block.triangles()[4:6]]
    assert drawn == [tuple(QUAD[i] for i in t) for t in QUAD_TRIS]


def test_pack_runs_out():
    block = _grid()
    positions = [(float(i), 0.0, 0.0) for i in range(30)]
    tris = [(i, i + 1, i + 2) for i in range(0, 27, 3)]
    packed = block.pack(positions, tris, (8.0, 8.0, 8.0), [0])
    assert packed.ran_out and packed.used == 0
    packed = block.pack([(0.0, 0.0, 0.0)], [], (8.0, 8.0, 8.0), [1])
    assert packed.ran_out and packed.used == 0


def _bent(block: Block, before: list) -> int:
    """Drawn triangles with some corners moved and some not: a primitive the pack left as it
    was still drawing a vertex the pack wrote."""
    after = block.vertices.position
    moved = {i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b}
    return sum(0 < len(moved.intersection(t)) < 3 for t in block.triangles() if len(set(t)) == 3)


def test_pack_skips_shared():
    block = _grid()
    before = list(block.vertices.position)
    packed = block.pack(QUAD, [(0, 1, 2)], (8.0, 8.0, 8.0), reindex=False, collapse=False)
    # every strip shares a row with the next: none can take a triangle without bending one
    assert (packed.used, packed.skipped, packed.triangles, packed.left) == (0, 4, 0, 1)
    assert block.vertices.position == before
    packed = block.pack(QUAD, [(0, 1, 2)], (8.0, 8.0, 8.0), reindex=False)
    assert (packed.used, packed.skipped) == (4, 0)  # chosen whole, so they collapse together
    assert all(len({block.vertices.position[i] for i in t}) == 1 for t in block.triangles())
    with pytest.raises(ValueError):
        block.pack(QUAD, QUAD_TRIS, (1.0, 1.0, 1.0), [9])


def _chain() -> Block:
    """A private strip; two strips sharing vertex 7; a triangle list sharing 10 with the last."""
    block = Block.build(_grid().vertices, [])
    for name in ("position", "texture", "color"):
        setattr(block.vertices, name, getattr(block.vertices, name)[:13])
    strip = Command.prim(Prim.TRIANGLE_STRIP, 4)
    block.commands[6:6] = [strip, strip, strip, Command.prim(Prim.TRIANGLES, 3)]
    block.indices = [0, 1, 2, 3, 4, 5, 6, 7, 7, 8, 9, 10, 10, 11, 12]
    return block


@pytest.mark.parametrize("collapse", [True, False])
def test_pack_never_bends(collapse: bool):
    loose = [(0.0, 0.5, 0.0), (1.0, 0.5, 0.0), (0.0, 0.5, 1.0)]
    positions = [
        *loose,
        *((x + 2, y, z) for x, y, z in loose),
        *((x + 4, y, z) for x, y, z in loose),
    ]
    tris = [(0, 1, 2), (3, 4, 5), (6, 7, 8)]
    for prims in ([0, 1, 2], [1, 2], [0, 1, 2, 3], None):
        block = _chain()
        before = list(block.vertices.position)
        packed = block.pack(
            positions, tris, (8.0, 8.0, 8.0), prims, reindex=False, collapse=collapse
        )
        assert _bent(block, before) == 0, prims
        assert block.indices == _chain().indices
    block = _chain()
    packed = block.pack(
        positions, tris, (8.0, 8.0, 8.0), [0, 1, 2], reindex=False, collapse=collapse
    )
    assert (packed.triangles, packed.skipped, packed.left) == (1, 2, 2)
    assert block.vertices.position[4:] == _chain().vertices.position[4:]


def test_pack_no_collapse():
    block = _grid()
    packed = block.pack(QUAD, [(0, 1, 2)], (8.0, 8.0, 8.0), [2, 3], collapse=False)
    assert (packed.used, packed.triangles, packed.left, packed.vertices_used) == (1, 1, 0, 3)
    assert block.indices[:16] + block.indices[24:] == _grid().indices[:16] + [0, 1, 4]


def test_pack_rejects():
    block = _grid()
    with pytest.raises(ValueError):
        _quad().pack(QUAD, QUAD_TRIS, (1.0, 1.0, 1.0), color=(0, 0, 0, 0))
    unindexed = Block([Command(Op.PRIM, 3)], Vertices(VertexType(texture=BITS16)))
    with pytest.raises(ValueError):
        unindexed.pack(QUAD, QUAD_TRIS, (1.0, 1.0, 1.0))
    block.vertices.vtype = VertexType(color=COLOR_5650, position=BITS16, index=BITS8)
    with pytest.raises(ValueError):
        block.pack(QUAD, QUAD_TRIS, (1.0, 1.0, 1.0), uvs=[(0, 0)] * 4)
    block.vertices.vtype = VertexType(position=BITS16, index=BITS8, through=True)
    with pytest.raises(ValueError):
        block.pack(QUAD, QUAD_TRIS, (1.0, 1.0, 1.0))


def _resident() -> Pmo:
    return Pmo(
        [Mesh([Group(_grid(8)), Group(_quad()), Group(_quad(0.5))], [0, 0, 0])], [Material()]
    )


def test_inplace():
    pmo = _resident()
    original = pmo.to_bytes()
    assert pmo.to_bytes_inplace(original) == (original, [])
    big, quad, grown = pmo.groups()
    big.block.clear()
    quad.block.clear(keep_layout=True)
    data, moved = pmo.to_bytes_inplace(original)
    assert len(data) == len(original) and moved == []
    assert data[: len(data) // 2] != original[: len(data) // 2]
    grown.block = Block.build(_grid(5).vertices, [(i, i + 1, i + 5) for i in range(20)])
    data, moved = pmo.to_bytes_inplace(original)
    back = Pmo.from_bytes(data)
    assert moved == [2] and back.groups()[2].block == grown.block
    assert back.triangles(0) == back.triangles(1) == []


def test_inplace_leaves_old_bytes():
    pmo = _resident()
    original = pmo.to_bytes()
    pmo.groups()[0].block.indices[-3:] = [0, 0, 0]
    data, _ = pmo.to_bytes_inplace(original)
    changed = [i for i, (a, b) in enumerate(zip(data, original, strict=True)) if a != b]
    assert len(changed) == 2


def test_inplace_rejects():
    original = _model().to_bytes()
    pmo = _model()
    pmo.groups()[2].block = Block.build(_grid(8).vertices, [(0, 1, 2)] * 60)
    with pytest.raises(ValueError):
        pmo.to_bytes_inplace(original)
    pmo = _model()
    pmo.materials.append(Material())
    with pytest.raises(ValueError):
        pmo.to_bytes_inplace(original)
    with pytest.raises(ValueError):
        Pmo([Mesh([Group(_quad())], [0])], [Material()]).to_bytes_inplace(original)
    with pytest.raises(ValueError):
        pmo.to_bytes_inplace(b"not a pmo")
    block = _quad()
    shared = Pmo([Mesh([Group(block), Group(block)], [0])], [Material()])
    original = shared.to_bytes()
    shared.groups()[1].block = _quad()
    with pytest.raises(ValueError):
        shared.to_bytes_inplace(original)


def test_unwritable():
    too_many = Pmo([Mesh([Group(_quad())], list(range(256)))], [Material()])
    with pytest.raises(ValueError):
        too_many.to_bytes()
    bad = Pmo([Mesh([Group(_quad(), 300)], [0])], [Material()])
    with pytest.raises(ValueError):
        bad.to_bytes()
    block = _quad()
    block.indices[0] = 300
    with pytest.raises(ValueError):
        Pmo([Mesh([Group(block)], [0])], [Material()]).to_bytes()
    block = _quad()
    block.vertices.vtype = SKINNED
    with pytest.raises(ValueError):
        Pmo([Mesh([Group(block)], [0])], [Material()]).to_bytes()
    block = _quad()
    del block.commands[0]
    with pytest.raises(ValueError):
        Pmo([Mesh([Group(block)], [0])], [Material()]).to_bytes()


def _patched(data: bytes, at: int, fmt: str, *values: int) -> bytes:
    out = bytearray(data)
    struct.pack_into(fmt, out, at, *values)
    return bytes(out)


def test_rejects():
    data = _model().to_bytes()
    groups = struct.unpack_from("<I", data, 36)[0]
    geometry = struct.unpack_from("<I", data, 52)[0]
    lists = [geometry + struct.unpack_from("<I", data, groups + 16 * g + 4)[0] for g in range(3)]
    iaddr, vaddr = (w & 0xFFFFFF for w in struct.unpack_from("<2I", data, lists[0] + 8))
    absolute = _patched(data, lists[0], "<4I", 0, Op.BASE << 24, Op.IADDR << 24 | lists[0] + iaddr,
                        Op.VADDR << 24 | lists[0] + vaddr)  # fmt: skip
    bad = [
        b"",
        data[:4] + b"102\0" + data[8:],
        data[:0x50],
        _patched(data, 8, "<I", len(data) + 16),
        _patched(data, 40, "<I", 0),
        _patched(data, 40, "<I", len(data)),
        _patched(data, 0x40 + 0x18 + 0x12, "<H", 1),
        _patched(data, groups + 16 + 2, "<H", 0),
        _patched(data, lists[0], "<I", 0),
        _patched(data, lists[0], "<I", Op.VTYPE << 24),
        _patched(data, lists[0] + 8, "<I", Op.IADDR << 24 | 1),
        _patched(data, lists[0] + 12, "<I", Op.VADDR << 24 | 0x401),
        _patched(data, groups + 8, "<I", 0x1000),
        _patched(data, 0x1C, "<H", 0xFFFF),
        absolute,
    ]
    assert not Pmo.sniff(bad[0]) and not Pmo.sniff(bad[1])
    for blob in bad:
        with pytest.raises(FormatError):
            Pmo.from_bytes(blob)


def test_unindexed_iaddr():
    vt = VertexType(position=BITS16)
    block = Block(
        [
            Command(Op.ORIGIN),
            Command(Op.VADDR),
            Command(Op.VTYPE, vt.to_word()),
            Command.prim(3, 3),
        ],
        Vertices(vt, position=[(0, 0, 0)] * 3),
    )
    block.commands.append(Command(Op.RET))
    data = Pmo([Mesh([Group(block)], [0])], [Material()]).to_bytes()
    assert Pmo.from_bytes(data).groups()[0].block == block
    block.commands.insert(1, Command(Op.IADDR))
    with pytest.raises(ValueError):
        Pmo([Mesh([Group(block)], [0])], [Material()]).to_bytes()
    geometry = struct.unpack_from("<I", data, 52)[0]
    with pytest.raises(FormatError):
        Pmo.from_bytes(_patched(data, geometry + 12, "<I", Op.IADDR << 24))
    block.indices = [0]
    with pytest.raises(ValueError):
        Pmo([Mesh([Group(block)], [0])], [Material()]).to_bytes()


def _pmos(data_dir: Path):
    for path in sorted(data_dir.glob("file_*.bin")):
        data = path.read_bytes()
        if Stage.sniff(data):
            stage = Stage.from_bytes(data)
            yield from ((f"{path.name} stage", p) for p in (stage.terrain, stage.props) if p)
            continue
        yield from _walk(path.name, data)


def _walk(name: str, data: bytes):
    if Pmo.sniff(data):
        yield name, data
    elif Pac.sniff(data):
        for i, entry in enumerate(Pac.from_bytes(data).entries):
            yield from _walk(f"{name}[{i}]", entry)


def test_round_trip_mhfu(mhfu_data):
    counts = {"stage": 0, "other": 0}
    for name, data in _pmos(mhfu_data):
        assert Pmo.from_bytes(data).to_bytes() == data, name
        counts["stage" if name.endswith("stage") else "other"] += 1
    assert counts == {"stage": 519, "other": 4033}


def test_tigrex(mhfu_data):
    """The native-quest Tigrex: 7 meshes whose group counts cover the whole body."""
    pac = Pac.from_bytes((mhfu_data / "file_06185.bin").read_bytes())
    pmo = Pmo.from_bytes(pac.entries[1])
    assert len(pmo.meshes) == 7 and len(pmo.groups()) == 214
    assert sum(len(g.block.vertices) for g in pmo.groups()) > 4000
    bones = {b for g in range(214) for row in pmo.influences(g) for b, w in row if w}
    assert min(bones) >= 0 and max(bones) < 64
