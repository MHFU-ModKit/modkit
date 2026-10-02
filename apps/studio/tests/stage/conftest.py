# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A synthetic stage, small enough to reason about, for the data-free tests."""

from pathlib import Path

import pytest
from mhfu import files
from mhfu.files import Extracted
from mhfu_studio.stage.file import StageFile
from mhp_formats.fu.stage import Collision, Environment, Stage, Tri, TriFlags, build_hits
from mhp_formats.pmo import Block, Group, Material, Mesh, Pmo
from mhp_formats.psp.ge import Command, Op, Prim
from mhp_formats.psp.vtype import BITS8, BITS16, COLOR_5650, VertexType, quantize_vertices
from mhp_formats.tmh import Clut, Tmh, TmhImage

SCALE = (4000.0, 4000.0, 4000.0)
STEP = 100.0
"""Grid spacing of the synthetic meshes, in world units."""


def grid_block(n: int, at: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> Block:
    """An n x n vertex grid, one strip per row, then one loose triangle under FFACE 1."""
    ox, oy, oz = at
    positions = [(ox + STEP * x, oy, oz + STEP * z) for z in range(n) for x in range(n)]
    vertices = quantize_vertices(
        VertexType(texture=BITS16, color=COLOR_5650, position=BITS16),
        positions,
        SCALE,
        uvs=[(x / n, z / n) for z in range(n) for x in range(n)],
        colors=[(200, 180, 160, 255)] * len(positions),
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


def cards_block() -> Block:
    """Three strips of four vertices each, sharing none: what positions-only packing fills."""
    corners = [(0.0, 0.0), (100.0, 0.0), (0.0, 100.0), (100.0, 100.0)]
    positions = [(1000.0 + 200 * k + x, 20.0, z) for k in range(3) for x, z in corners]
    vertices = quantize_vertices(
        VertexType(texture=BITS16, color=COLOR_5650, position=BITS16),
        positions,
        SCALE,
        uvs=[(0.5, 0.5)] * len(positions),
        colors=[(90, 90, 90, 255)] * len(positions),
    )
    vt = VertexType(texture=BITS16, color=COLOR_5650, position=BITS16, index=BITS8)
    vertices.vtype = vt
    commands = [Command(Op.ORIGIN), Command(Op.BASE), Command(Op.IADDR), Command(Op.VADDR)]
    commands += [Command(Op.VTYPE, vt.to_word()), Command(Op.FFACE)]
    commands += [Command.prim(Prim.TRIANGLE_STRIP, 4)] * 3
    commands += [Command(Op.OFFSETADDR), Command(Op.RET)]
    return Block(commands, vertices, list(range(12)))


def terrain() -> Pmo:
    groups = [
        Group(grid_block(6), 0),
        Group(grid_block(4, (2000.0, 50.0, 2000.0)), 1),
        Group(cards_block(), 0),
    ]
    return Pmo(
        [Mesh(groups, [0, 1])],
        [Material(texture=0), Material(color=0xFF8080FF, texture=1)],
        SCALE,
        clip=SCALE[0],
    )


def props() -> Pmo:
    return Pmo([Mesh([Group(grid_block(3, (500.0, 300.0, 500.0)))], [0])], [Material()], SCALE)


def bank(shade: int = 0) -> Tmh:
    """Two indexed images: 8 bpp 16 x 8 over 256 colours, 4 bpp 32 x 8 over 16."""
    pal256 = b"".join(
        bytes(((k + shade) % 256, k, 255 - k, 255 if k % 4 else 0)) for k in range(256)
    )
    pal16 = b"".join(bytes((16 * k, (16 * k + shade) % 256, 0, 255)) for k in range(16))
    return Tmh(
        [
            TmhImage(5, 16, 8, bytes(k % 64 for k in range(128)), Clut(3, pal256)),
            TmhImage(4, 32, 8, bytes((k * 7) % 256 for k in range(128)), Clut(3, pal16)),
        ]
    )


def floor_tris() -> list[Tri]:
    """Floor quads over x 0..1500, z 0..1000 at y 0."""
    out = []
    for x0 in (0.0, 750.0):
        a, b = (x0, 0.0, 0.0), (x0 + 750.0, 0.0, 0.0)
        c, d = (x0 + 750.0, 0.0, 1000.0), (x0, 0.0, 1000.0)
        out += [
            Tri.from_verts(a, d, c, TriFlags(1, 3, 1)),
            Tri.from_verts(a, c, b, TriFlags(1, 3, 1)),
        ]
    return out


def wall_tris() -> list[Tri]:
    a, b, c, d = (
        (1200.0, 0.0, 300.0),
        (1200.0, 800.0, 300.0),
        (1200.0, 800.0, 700.0),
        (1200.0, 0.0, 700.0),
    )
    return [Tri.from_verts(a, b, c), Tri.from_verts(a, c, d)]


def stage_bytes(shade: int = 0) -> bytes:
    return Stage(
        terrain=terrain().to_bytes(),
        textures=bank(shade).to_bytes(),
        props=props().to_bytes(),
        environment=Environment(2, [0xAA, 0xCC, 0xFF, 0xFF], [-1000.0, 60000.0], bytes(156)),
        params=b"\x02\x01" + bytes(30),
        collision=Collision([build_hits(wall_tris(), (3, 2)), build_hits(floor_tris(), (3, 2))]),
        tail=b"\x7f" * 5,
    ).to_bytes()


@pytest.fixture(autouse=True)
def _state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A push's undo goes to the test's folder, never the user's."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.fixture
def game(tmp_path: Path) -> Extracted:
    """An extraction holding st001 and st002 (whose bank differs in colour)."""
    data = tmp_path / "game" / "data_files"
    data.mkdir(parents=True)
    for n, shade in ((1, 0), (2, 40)):
        (data / f"file_{files.stage_pac(n):05d}.bin").write_bytes(stage_bytes(shade))
    return Extracted(tmp_path / "game")


@pytest.fixture
def st(game: Extracted) -> StageFile:
    return StageFile.read(game, 1)


@pytest.fixture
def bare() -> StageFile:
    """st001 without props, as five retail stages ship."""
    stage = Stage.from_bytes(stage_bytes())
    stage.props = b""
    return StageFile(1, stage.to_bytes())


@pytest.fixture
def assets(tmp_path: Path) -> Path:
    """An edit list's folder: a quad, a closed tetrahedron, two loose triangles, a slab."""
    d = tmp_path / "doc"
    d.mkdir()
    (d / "quad.obj").write_text(
        "v 100 500 100\nv 300 500 100\nv 300 500 300\nv 100 500 300\n"
        "vt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nf 1/1 2/2 3/3 4/4\n"
    )
    (d / "tetra.obj").write_text(
        "v 300 700 300\nv 100 500 100\nv 500 500 100\nv 300 500 500\n"
        "f 1 2 3\nf 1 3 4\nf 1 4 2\nf 2 4 3\n"
    )
    (d / "pair.obj").write_text(
        "v 0 900 0\nv 0 900 100\nv 100 900 0\nv 500 900 500\nv 500 900 600\nv 600 900 500\n"
        "f 1 2 3\nf 4 5 6\n"
    )
    (d / "slab.obj").write_text(
        "v 100 50 100\nv 600 50 100\nv 600 50 600\nv 100 50 600\nf 1 3 2\nf 1 4 3\n"
    )
    return d
