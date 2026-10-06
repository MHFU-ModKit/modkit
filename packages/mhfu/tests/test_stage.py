# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct

import pytest
from mhfu import addresses as a
from mhfu import files, mips
from mhfu import stage as S
from mhfu.memory import Image, Space
from mhfu.overlay import TEXT, Overlay
from modkit_testing import mips as asm

LOAD = 0x0010_0000


def words(*ws: int) -> bytes:
    return struct.pack(f"<{len(ws)}I", *ws)


def overlay(text: bytes, data: bytes, bss: int = 0, tail: bytes = b"") -> Overlay:
    header = struct.pack("<4sIIIIIII", b"MWo3", 1, LOAD, len(text), len(data), bss, 0, 0)
    return Overlay((header + b"stage001.ovl").ljust(TEXT, b"\0") + text + data + tail)


def stage_overlay(p_off: int = 0x40) -> tuple[Overlay, int]:
    """Text: one constant getter returning the parameter object at data + p_off."""
    text_size = 0x20
    data_start = LOAD + TEXT + text_size
    p = data_start + p_off
    text = words(asm.lui("v0", asm.hi(p)), asm.RET, asm.addiu("v0", "v0", asm.lo(p)), *[0] * 5)
    data = bytearray(0x200)
    exits, spheres, table = 0x100, 0x180, 0x1E0
    fields = a.STAGE_PARAMS
    struct.pack_into("<I", data, p_off + fields.SURFACES, data_start + table)
    struct.pack_into("<I", data, p_off + fields.SPHERES, data_start + spheres)
    struct.pack_into("<10H", data, p_off + fields.SLOTS, 1, 2, 3, *[S.NO_SLOT] * 7)
    struct.pack_into("<I", data, p_off + fields.EXITS, data_start + exits)
    struct.pack_into("<BBB", data, p_off + fields.ENV, 0, 2, 1)
    exit_rec = struct.pack("<HH3f2f12x3fH2x", 99, 0, 1, 2, 3, 500, 1000, 4, 5, 6, 0x4000)
    data[exits : exits + 2 * len(exit_rec)] = exit_rec * 2
    data[spheres : spheres + 0x18] = struct.pack("<HH3ffI", 0, 7, 1, 2, 3, 50, 9)
    data[table : table + 8] = words(0x1, 0x80)
    return overlay(text, bytes(data), bss=0x40, tail=b"\xff" * 0x40), p


def test_stage_files():
    assert files.stage_pac(1) == files.STAGE_PACS.start
    assert files.stage_variant_pac(15) == files.STAGE_PACS.stop - 1
    assert len(files.STAGE_PACS) == 282 and len(files.STAGES) == 267
    with pytest.raises(ValueError):
        files.stage_pac(0)
    with pytest.raises(ValueError):
        files.stage_variant_pac(16)
    assert [S.variant_name(v) for v in (0, 5, 15)] == ["st046_1a", "st046_2b", "st046_4d"]


def test_loaded_zeroes_bss_not_slack():
    ovl, _ = stage_overlay()
    mem = ovl
    assert mem.end == ovl.bss.stop
    assert mem.read(ovl.bss.start, len(ovl.bss)) == bytes(len(ovl.bss))


def test_returned_constants_sign_extend():
    text = words(
        asm.NOP,
        asm.lui("v0", 0x11),
        asm.RET,
        asm.addiu("v0", "v0", 0x8010),
        asm.lui("v0", 0x11),
        asm.RET,
        asm.addiu("v1", "v0", 1),
    )
    ovl = overlay(text, b"")
    code = mips.Code(ovl, ovl.text)
    returned = [code.constant(i.vram, mips.Gpr.v0) for i in code if i.isReturn()]
    assert returned == [0x10_8010, 0x11_0000]


def test_stage_overlay():
    ovl, p = stage_overlay()
    so = S.StageOverlay.parse(ovl)
    assert so.params is not None and so.params.base == p
    (e, e2) = so.exits()
    assert (e.target, e.trigger, e.radius, e.height, e.dest, e.yaw) == (
        99,
        (1.0, 2.0, 3.0),
        500.0,
        1000.0,
        (4.0, 5.0, 6.0),
        0x4000,
    )
    (s,) = so.spheres()
    assert (s.id, s.position, s.radius, s.tail) == (7, (1.0, 2.0, 3.0), 50.0, 9)
    # the table runs into bss up to the overlay's end, not into the file's slack
    assert so.surface_table() == [0x1, 0x80, 0, 0, 0, 0, 0, 0]
    assert so.surface_table(2) == [0x1, 0x80]


def test_no_parameter_object():
    so = S.StageOverlay.parse(overlay(words(asm.RET, asm.NOP), bytes(16)))
    assert so.params is None and so.exits() == [] and so.surface_table() is None


def test_map_table():
    rows = bytearray(S.MAP_ROWS * 8)
    stages = a.MAP_TABLE + len(rows)
    struct.pack_into("<II", rows, 0, 3, stages)
    struct.pack_into("<II", rows, 8, 0, 0)
    struct.pack_into("<II", rows, 16, 2, 0x10)
    mem = Image(bytes(rows) + struct.pack("<3H", 98, 92, 93), a.MAP_TABLE)
    table = S.map_table(mem)
    assert table[:3] == [(98, 92, 93), (), ()] and len(table) == S.MAP_ROWS


def test_exit_fault():
    table = [(98, 99), (139, 140)]
    assert S.exit_fault(98, 99, table) == ""
    assert S.exit_fault(98, 140, table) == "the target shares no map with this stage"
    assert S.exit_fault(98, 0, table) == S.exit_fault(98, 999, table) == "no such stage"


def ram(size: int = 0x2000) -> Image:
    return Image(bytes(size), a.USER_RAM)


def test_resident_files_and_map_manager():
    mem = Image(bytes(0x1000), a.RESOURCE_TABLE)
    base = a.RESOURCE_TABLE + 0x100
    mem.write_u32(a.RESOURCE_TABLE, base)
    mem.write(base, struct.pack("<HHII", 2, files.engine_id(5905), 0x1234, 0))
    mem.write(base + 12, struct.pack("<HHII", 0, S.FREE_FILE, 0, 0))
    mem.write(base + 24, struct.pack("<HHII", 0, files.engine_id(5906), 0x5678, 0))
    (slot,) = S.resident_files(mem).items()
    assert slot[0] == 5905 and slot[1].data == 0x1234
    mgr_mem = Image(bytes(0x400), a.MAP_MANAGER_PTR)
    mgr_mem.write_u32(a.MAP_MANAGER_PTR, a.MAP_MANAGER_PTR + 0x10)
    mgr_mem.write_u16(a.MAP_MANAGER_PTR + 0x10 + a.MAP_MANAGER.STAGE, 107)
    assert S.map_manager(mgr_mem).stage == 107


def spot(x: float, r: float, id: int) -> bytes:
    return struct.pack("<4f4H", x, 100.0, 2000.0, r, id, 6, 3, 2)


def spot_table(groups: int) -> bytes:
    out = b""
    for g in range(groups):
        for k in range(4):
            unused = g == 1 and k == 3
            out += (
                struct.pack("<4f4H", 0, 0, 0, 0, 4 * g + k, 0, 0, 0)
                if unused
                else spot(1000.0 + k, 200.0, 4 * g + k)
            )
        out += spot(S.SPOT_END_X, 0.0, 0)
    return out


def test_find_spots():
    mem = ram()
    mem.write(a.USER_RAM + 0x100, spot(S.SPOT_END_X, 0.0, 0))  # a stray terminator
    mem.write(a.USER_RAM + 0x400, spot_table(3))
    found = S.find_spots(S.snapshot(mem, range(mem.base, mem.end)))
    assert [s.id for s in found] == list(range(12))
    assert found[0].base == a.USER_RAM + 0x400
    assert [s.kind for s in found].count("unused") == 1
    assert (found[5].tool, found[5].uses, found[5].uses_m) == (3, 6, 2)
    assert S.find_spots(S.snapshot(ram(), range(a.USER_RAM, a.USER_RAM + 0x2000))) == []


def spawn(hp: int, entity: int, x: float = 5000.0) -> bytes:
    body = struct.pack("<I3f6H", 0xABCD, x, 50.0, 7000.0, 3, 2, 100, hp, entity, 0)
    return body.ljust(a.SMALL_SPAWN.size or 0, b"\0")


def test_find_spawns():
    mem = ram()
    mem.write(a.USER_RAM + 0x100, spawn(54, 2))
    mem.write(a.USER_RAM + 0x200, spawn(S.NO_HP, S.NO_ENTITY))
    mem.write(a.USER_RAM + 0x300, spawn(54, 2, x=float("nan")))
    mem.write(a.USER_RAM + 0x402, spawn(54, 2))  # misaligned
    found = S.find_spawns(S.snapshot(mem, range(mem.base, mem.end)))
    assert [s.base - a.USER_RAM for s in found] == [0x100, 0x200]
    assert [s.here for s in found] == [True, False]
    assert found[0].hp == 54 and found[0].entity == 2


def test_every_stage_overlay(game):
    exits = 0
    for n in files.STAGES:
        so = S.StageOverlay.read(game, n)
        assert so.name == f"stage{n:03d}.ovl" and so.params is not None
        table = so.surface_table()
        assert table is not None and 0 < len(table) <= S.SURFACE_IDS
        exits += len(so.exits())
        assert all(e.target in files.STAGES for e in so.exits())
    assert exits == 555


def test_map_table_game(game):
    table = S.read_map_table(game)
    assert len(table) == S.MAP_ROWS
    assert 139 in table[0] and table[11][0] == 98
    assert [r for r, stages in enumerate(table) if not stages] == [24, 25]


def test_lobby_loads_the_variants(game):
    """The last 16 stage PACs are st046's: the lobby asks the variant picker for the file."""
    lobby = game.overlay(files.LOBBY_TASK)
    assert mips.Code(lobby, lobby.text).callers(a.STAGE_VARIANT_FILE)
    eboot = game.eboot()
    picker = mips.instructions(eboot, a.STAGE_VARIANT_FILE, a.STAGE_VARIANT_FILE + 0x100)
    first = files.engine_id(files.stage_variant_pac(0))
    assert any(i.isIType() and i.getProcessedImmediate() == first for i in picker)


PAC_AT = a.USER_RAM + 0xE0_0000  # a heap address for the stage PAC


def tri(*verts: tuple[float, float, float]) -> bytes:
    """A floor triangle with its unit normal and plane, normal y up."""
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = verts
    u, v = (bx - ax, by - ay, bz - az), (cx - ax, cy - ay, cz - az)
    n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    length = sum(c * c for c in n) ** 0.5
    n = tuple(c / length * (-1 if n[1] < 0 else 1) for c in n)
    d = -(n[0] * ax + n[1] * ay + n[2] * az)
    return struct.pack("<I13f", 0, *verts[0], *verts[1], *verts[2], *n, d)


def resident_stage(stage: int, fixed: bool = True) -> Space:
    """A resident stage PAC whose floor chunk is a 2x2 grid of 500-unit cells: cell (0, 0)
    lists a level floor at 100, a ramp y = x / 5 and a wall, every other cell nothing."""
    tris = [
        tri((0, 100, 0), (0, 100, 400), (400, 100, 0)),
        tri((0, 0, 0), (0, 0, 500), (500, 100, 0)),
        tri((0, 0, 0), (0, 500, 0), (0, 0, 500)),
    ]
    coll, chunk = 0x40, 0x60
    grid = chunk + S.HITS_GRID_AT
    lists = grid + 4 * 4
    tri_at = lists + 4 * (len(tris) + 1) + 4 * 3
    size = tri_at - chunk + 56 * len(tris)
    pac = bytearray(chunk + size)
    struct.pack_into("<I", pac, 0, 6)
    struct.pack_into("<II", pac, 4 + 8 * S.COLLISION_ENTRY, coll, chunk - coll + size)
    struct.pack_into("<I4I", pac, coll, 2, 0, 0, chunk - coll, size)
    grid_word = PAC_AT + grid if fixed else S.HITS_GRID_AT - 8
    head = (S.HITS_TAG, size, 500, 500, 2, 2, 0, 0, grid_word, PAC_AT + tri_at)
    pac[chunk:grid] = struct.pack("<4sIIIIIiiII", *head)
    first = [PAC_AT + tri_at + 56 * k for k in range(len(tris))] + [S.LIST_END]
    rest = lists + 4 * len(first)
    struct.pack_into("<4I", pac, grid, PAC_AT + lists, PAC_AT + rest, PAC_AT + rest, PAC_AT + rest)
    struct.pack_into(f"<{len(first)}I", pac, lists, *first)
    struct.pack_into("<I", pac, rest, S.LIST_END)
    pac[tri_at:] = b"".join(tris)
    table = Image(bytes(0x400), a.RESOURCE_TABLE)
    table.write_u32(a.RESOURCE_TABLE, a.RESOURCE_TABLE + 0x10)
    row = struct.pack("<HHII", S.LOADED, files.engine_id(files.stage_pac(stage)), PAC_AT, 0)
    table.write(a.RESOURCE_TABLE + 0x10, row)
    return Space([table, Image(bytes(pac), PAC_AT)])


def test_floor():
    floor = S.Floor.read(resident_stage(98), 98)
    assert floor.heights(100, 100) == pytest.approx([100, 20])
    assert floor.height(100, 100) == pytest.approx(100)
    assert floor.height(450, 20, near=0) == pytest.approx(90)
    assert floor.height(100, 100, near=30) == pytest.approx(20)
    assert floor.heights(600, 100) == [] and floor.height(-1, 100) is None
    assert floor.heights(0, 0) == pytest.approx([100, 0])


def test_floor_not_loaded():
    with pytest.raises(S.NotLoaded, match="not resident"):
        S.Floor.read(resident_stage(98), 99)
    with pytest.raises(S.NotLoaded, match="not fixed up"):
        S.Floor.read(resident_stage(98, fixed=False), 98)


def test_floor_triangles():
    mem = resident_stage(98)
    floor = S.Floor.read(mem, 98, chunk=S.FLOOR_CHUNK)
    tris = list(floor.triangles())
    assert len(tris) == 3
    assert tris[0].v1 == (0, 100, 400) and tris[0].normal[1] == pytest.approx(1)
    assert tris[2].normal[1] == pytest.approx(0) and tris[2].material == 0
    assert tris[1].address == tris[0].address + 56
    with pytest.raises(S.NotLoaded, match="chunk 0"):
        S.Floor.read(mem, 98, chunk=S.WALL_CHUNK)


def test_triangle_flags():
    mem = resident_stage(98)
    floor = S.Floor.read(mem, 98)
    at = next(floor.triangles()).address
    mem.write_u32(at, 0x0101_0A02)
    tri = next(S.Floor.read(mem, 98).triangles())
    assert (tri.surface, tri.material, tri.exclude) == (2, 10, 0x101)
