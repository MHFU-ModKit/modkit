# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A synthetic section for the data-free tests, and the shipped stages for the data ones.

st139 (the village's number, so the workspace opens on it): a floor grid textured with an
opaque slot, two 7x7 "crates" in one group (two welded components, alpha-masked slot), a far
backdrop quad, an untextured plane; props hold one more grid. Collision: a floor (one triangle
on a sink surface), two walls, one of them climbable.
"""

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
from mhfu import files
from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.core.scene import ExitRecord, MapScene, Sphere, build, open_stage
from mhfu_studio.stage.file import StageFile
from mhp_formats.fu.stage import Collision, Environment, Stage, Tri, TriFlags, build_hits
from mhp_formats.pmo import Block, Group, Material, Mesh, Pmo
from mhp_formats.psp.ge import Command, Op, Prim
from mhp_formats.psp.vtype import BITS16, COLOR_8888, VertexType, quantize_vertices
from mhp_formats.tmh import Clut, Tmh, TmhImage

STAGE = 139
OTHER = 98
SCALE = (30000.0, 30000.0, 30000.0)
STEP = 250.0
SURFACES = [0, 0x20]
"""Surface id 1 is a sink surface."""
FOG = [0x40, 0x60, 0x80, 0xFF]
Vec3 = tuple[float, float, float]


def grid(n: int, at: Vec3, step: float = STEP) -> list[Vec3]:
    ox, oy, oz = at
    return [(ox + step * x, oy, oz + step * z) for z in range(n) for x in range(n)]


def block(grids: Sequence[tuple[int, Vec3]], shade: int = 200, step: float = STEP) -> Block:
    """Grids of n x n vertices in one block, each row of quads one strip."""
    positions: list[Vec3] = []
    commands = [Command(Op.ORIGIN), Command(Op.BASE), Command(Op.IADDR), Command(Op.VADDR)]
    vt = VertexType(texture=BITS16, color=COLOR_8888, position=BITS16, index=BITS16)
    commands += [Command(Op.VTYPE, vt.to_word()), Command(Op.FFACE)]
    indices: list[int] = []
    uvs: list[tuple[float, float]] = []
    for n, at in grids:
        base = len(positions)
        positions += grid(n, at, step)
        uvs += [(x / (n - 1), z / (n - 1)) for z in range(n) for x in range(n)]
        for z in range(n - 1):
            row = [base + i for x in range(n) for i in (z * n + x, (z + 1) * n + x)]
            commands.append(Command.prim(Prim.TRIANGLE_STRIP, len(row)))
            indices += row
    commands += [Command(Op.OFFSETADDR), Command(Op.RET)]
    colours = [(shade, (shade + 7 * i) % 256, 255 - shade, 255) for i in range(len(positions))]
    plain = VertexType(texture=BITS16, color=COLOR_8888, position=BITS16)
    vertices = quantize_vertices(plain, positions, SCALE, uvs=uvs, colors=colours)
    vertices.vtype = vt
    return Block(commands, vertices, indices)


def terrain() -> Pmo:
    groups = [
        Group(block([(9, (0.0, 0.0, 0.0))]), 0),
        Group(block([(7, (300.0, 100.0, 300.0)), (7, (1300.0, 100.0, 1300.0))], step=50.0), 1),
        Group(block([(2, (-9000.0, -500.0, -9000.0))], shade=120, step=20000.0), 0),
        Group(block([(3, (500.0, 400.0, 1500.0))], shade=90, step=100.0), 2),
    ]
    materials = [Material(texture=0), Material(texture=1), Material(texture=0xFF)]
    return Pmo([Mesh(groups, [0, 1, 2])], materials, SCALE, clip=SCALE[0])


def props() -> Pmo:
    group = Group(block([(3, (1000.0, 200.0, 400.0))], shade=150, step=100.0), 0)
    return Pmo([Mesh([group], [0])], [Material(texture=1)], SCALE)


def bank() -> Tmh:
    """An opaque 8 bpp 16 x 16 and a 4 bpp 32 x 8 whose colour 0 is clear."""
    pal256 = b"".join(bytes((k, 255 - k, (3 * k) % 256, 255)) for k in range(256))
    pal16 = b"".join(bytes((16 * k, 128, 255 - 16 * k, 0 if k == 0 else 255)) for k in range(16))
    return Tmh(
        [
            TmhImage(5, 16, 16, bytes((7 * k) % 256 for k in range(256)), Clut(3, pal256)),
            TmhImage(4, 32, 8, bytes((k * 13) % 256 for k in range(128)), Clut(3, pal16)),
        ]
    )


def floor_tris() -> list[Tri]:
    out = []
    for x0 in (0.0, 1000.0):
        a, b = (x0, 0.0, 0.0), (x0 + 1000.0, 0.0, 0.0)
        c, d = (x0 + 1000.0, 0.0, 2000.0), (x0, 0.0, 2000.0)
        sink = TriFlags(1, 3, 0) if x0 else TriFlags(0, 3, 0)
        out += [Tri.from_verts(a, d, c, sink), Tri.from_verts(a, c, b, TriFlags(0, 3, 0))]
    return out


def wall_tris() -> list[Tri]:
    a, b, c, d = (
        (1500.0, 0.0, 300.0),
        (1500.0, 800.0, 300.0),
        (1500.0, 800.0, 900.0),
        (
            1500.0,
            0.0,
            900.0,
        ),
    )
    return [Tri.from_verts(a, b, c, TriFlags(0, 10, 0)), Tri.from_verts(a, c, d)]


def stage_bytes() -> bytes:
    return Stage(
        terrain=terrain().to_bytes(),
        textures=bank().to_bytes(),
        props=props().to_bytes(),
        environment=Environment(2, FOG, [-1000.0, 60000.0], bytes(156)),
        params=b"\x02\x01" + bytes(30),
        collision=Collision([build_hits(wall_tris(), (5, 5)), build_hits(floor_tris(), (5, 5))]),
    ).to_bytes()


EXITS = [
    ExitRecord(0, OTHER, "st098", 0, (1800.0, 0.0, 1800.0), 200.0, 300.0, (10.0, 0.0, 10.0), 0x4000)
]
SPHERES = [Sphere(24, (600.0, 100.0, 600.0), 150.0)]


@pytest.fixture
def game(tmp_path: Path) -> Extracted:
    """An extraction holding st139 and st098, both the synthetic section."""
    data = tmp_path / "game" / "data_files"
    data.mkdir(parents=True)
    for n in (STAGE, OTHER):
        (data / f"file_{files.stage_pac(n):05d}.bin").write_bytes(stage_bytes())
    return Extracted(tmp_path / "game")


@pytest.fixture
def atlas(game: Extracted) -> Atlas:
    return Atlas(game, [(STAGE, OTHER)])


def synthetic(game: Extracted, number: int = STAGE) -> MapScene:
    return build(StageFile.read(game, number), exits=EXITS, spheres=SPHERES, surface_table=SURFACES)


@pytest.fixture
def scene(game: Extracted) -> MapScene:
    return synthetic(game)


@pytest.fixture
def doc_dir(tmp_path: Path) -> Path:
    d = tmp_path / "doc"
    d.mkdir()
    return d


@pytest.fixture(scope="module")
def shipped(mhfu_data: Path) -> Extracted:
    return Extracted.find(mhfu_data)


@pytest.fixture(scope="module")
def camp(shipped: Extracted) -> MapScene:
    return open_stage(shipped, 98)


@pytest.fixture
def synth() -> SimpleNamespace:
    """The synthetic section's constants and builders."""
    return SimpleNamespace(
        STAGE=STAGE,
        OTHER=OTHER,
        FOG=FOG,
        EXITS=EXITS,
        SPHERES=SPHERES,
        block=block,
        build=synthetic,
    )
