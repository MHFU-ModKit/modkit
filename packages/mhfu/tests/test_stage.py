import struct

import pytest
from mhfu import addresses as a
from mhfu import files, hitbox, mips
from mhfu import stage as S
from mhfu.memory import Image
from mhfu.overlay import TEXT, Overlay

LOAD = 0x0010_0000
JR_RA = 0x03E0_0008


def lui(reg: int, hi: int) -> int:
    return 0x3C00_0000 | reg << 16 | hi


def addiu(rt: int, rs: int, imm: int) -> int:
    return 0x2400_0000 | rs << 21 | rt << 16 | imm & 0xFFFF


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
    hi, lo = (p >> 16) + (1 if p & 0x8000 else 0), p & 0xFFFF
    text = words(lui(2, hi), JR_RA, addiu(2, 2, lo), 0, 0, 0, 0, 0)
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


def test_constant_getter_sign_extends():
    text = words(0, lui(2, 0x11), JR_RA, addiu(2, 2, 0x8010), lui(2, 0x11), JR_RA, addiu(3, 2, 1))
    ovl = overlay(text, b"")
    assert list(S.constant_getters(ovl, ovl.text)) == [0x10_8010]


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
    so = S.StageOverlay.parse(overlay(words(JR_RA, 0), bytes(16)))
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


def ram(size: int = 0x2000) -> Image:
    return Image(bytes(size), a.USER_RAM)


def test_resident_files_and_map_manager():
    mem = Image(bytes(0x1000), a.RESOURCE_TABLE)
    base = a.RESOURCE_TABLE + 0x100
    mem.write_u32(a.RESOURCE_TABLE, base)
    mem.write(base, struct.pack("<HHII", 2, files.engine_id(5905), 0x1234, 0))
    mem.write(base + 12, struct.pack("<HHII", 0, S.FREE_FILE, 0, 0))
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
    assert list(hitbox.calls_to(lobby, lobby.text, a.STAGE_VARIANT_FILE))
    eboot = game.eboot()
    picker = mips.instructions(eboot, a.STAGE_VARIANT_FILE, a.STAGE_VARIANT_FILE + 0x100)
    first = files.engine_id(files.stage_variant_pac(0))
    assert any(i.isIType() and i.getProcessedImmediate() == first for i in picker)
