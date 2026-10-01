# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import importlib.util
import struct
from pathlib import Path
from typing import Any

import pytest
from mhfu import addresses as a
from mhfu import files
from mhfu_port.manifest import Attack, Hitbox, HitzoneState, Hurtbox, ManifestError
from mhfu_studio.monster import runtime as RT

FRAMEWORK = Path(__file__).parents[4] / "framework"
ROW = [100, 75, 65, 40, 0, 15, 5, 30, 20, 110]
TABLES = RT.AttackTables(0x100, 0x200, 56, 107, {0: 5, 2: 10})


def hurt(m):
    m.hurtboxes = [
        Hurtbox(2, 600.0, 1, 0, offset=[0.0, 55.0, 0.0], label="one big sphere"),
        Hurtbox(6, 65.0, 4, 5, "capsule", [35.0, 0.0, 0.0], [330.0, 0.0, 0.0], 0x101),
    ]
    m.hitzones = [HitzoneState("normal", [list(ROW)] + [[0] * 10 for _ in range(6)])]
    return m


def hits(m):
    m.hitboxes = [
        Hitbox(12, 170.0, 2, offset=[0.0, 20.0, 0.0], label="head"),
        Hitbox(125, 0.0, 2, "capsule", to=[0.0, 0.0, 0.0]),
        Hitbox(44, 100.0, 2, "capsule", to=[0.0, 0.0, -175.0], label="tail tip"),
    ]
    m.attacks = [Attack(6, power=40, label="softer"), Attack(31, volume=5)]
    return m


def test_rows():
    h = Hurtbox(2, 600.0, 1, 3, offset=[0.0, 55.0, 0.0])
    assert (
        RT.volume_row(h) == ["2", "0", "3", "1", "0x0", "600.0", "0.0", "55.0", "0.0"] + ["0.0"] * 3
    )
    cap = Hurtbox(6, 65.0, 4, 5, "capsule", [35.0, 0.0, 0.0], [330.0, 0.0, 0.0], 0x101)
    assert RT.volume_row(cap)[1] == "1" and RT.volume_row(cap)[4] == "0x101"
    assert RT.volume_row(cap)[9:] == ["330.0", "0.0", "0.0"]
    assert RT.volume_row(Hurtbox(1, 1.0, to=[9.0, 9.0, 9.0]))[9:] == ["0.0"] * 3
    row = RT.attack_volume_row(Hitbox(44, 100.0, 2, "capsule", [1.0, 2.0, 3.0], [0, 0, -175.0]))
    assert row[:5] == ["44", "1", "0", "0", "0x0"] and row[-1] == "-175.0"


def test_module(make):
    m = hurt(make())
    text = RT.lua_hit_module(m, capacity=42, source="ports/t.toml")
    assert 'local port = require("mhfu_port")' in text and 'port.mod("t_hit"' in text
    assert 'P.hit("t", {' in text and "species = 75," in text and "ports/t.toml" in text
    assert "{ 2, 0, 0, 1, 0x0, 600.0, 0.0, 55.0, 0.0, 0.0, 0.0, 0.0 },  -- one big sphere" in text
    assert "{ 6, 1, 5, 4, 0x101, 65.0, 35.0, 0.0, 0.0, 330.0, 0.0, 0.0 }," in text
    assert "{ 100, 75, 65, 40, 0, 15, 5, 30, 20, 110 }," in text
    assert text.count("        { ") == 7 and "capacity: 42 record(s)." in text
    assert "1 MORE than fit" in RT.lua_hit_module(m, capacity=1)


def test_attack_module(make):
    m = hits(make())
    text = RT.lua_hit_module(m, attacks=TABLES)
    assert "attack_tables = { volumes = 0x00000100, records = 0x00000200, n_sets = 56, " in text
    assert "[2] = { cap = 10, volumes = {" in text and "-- marker" in text
    assert "{ 12, 0, 0, 0, 0x0, 170.0, 0.0, 20.0, 0.0, 0.0, 0.0, 0.0 },  -- head" in text
    assert "{ id = 6, power = 40, element = nil, volume = nil },  -- softer" in text
    assert "{ id = 31, power = nil, element = nil, volume = 5 }," in text
    small = RT.AttackTables(1, 2, 56, 107, {2: 2})
    assert "[2] = { cap = 2, volumes = {  -- 1 MORE than fit" in RT.lua_hit_module(m, attacks=small)
    assert RT.sets_of(m) == {2: m.hitboxes}


def test_refused(make):
    with pytest.raises(ManifestError, match="no hurtbox"):
        RT.lua_hit_module(make())
    m = hits(make())
    with pytest.raises(ManifestError, match="attack table addresses are unknown"):
        RT.lua_hit_module(m)
    m.hitboxes.append(Hitbox(1, 1.0, 56))
    with pytest.raises(ManifestError, match="0..55"):
        RT.lua_hit_module(m, attacks=TABLES)
    m.hitboxes.pop()
    m.attacks.append(Attack(107, power=1))
    with pytest.raises(ManifestError, match="107 record"):
        RT.lua_hit_module(m, attacks=TABLES)


def test_content_id(make):
    m = hits(hurt(make()))
    ids = [RT.content_id(m)]
    m.hurtboxes[0].radius = 2.0
    ids.append(RT.content_id(m))
    m.attacks[0] = Attack(6, power=41)
    ids.append(RT.content_id(m))
    m.hitboxes[0] = Hitbox(13, 170.0, 2)
    ids.append(RT.content_id(m))
    assert len(set(ids)) == 4 and all(len(i) == 8 for i in ids)


def test_export_and_deploy(make, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    m = hurt(make())
    path = RT.export(m)
    assert path == Path("t_hit.lua") and path.read_text().startswith("-- t_hit.lua, GENERATED")
    lib = tmp_path / "mhfu_port.lua"
    lib.write_text("-- the library\n")
    mods = tmp_path / "mods"
    with pytest.raises(FileNotFoundError):
        RT.deploy(path, mods, lib)
    (mods / "lib").mkdir(parents=True)
    (mods / "lib" / "mhfu_port.lua").write_text("-- older\n")
    (mods / "mhfu_port.lua").write_text("-- where it ran as a mod\n")
    dep = RT.deploy(path, mods, lib)
    assert (mods / "t_hit.lua").read_text() == path.read_text() and "was stale" in dep.describe()
    assert (mods / "lib" / "mhfu_port.lua").read_text() == "-- the library\n"
    assert not (mods / "mhfu_port.lua").exists()
    assert RT.deploy(path, mods, lib).library is None


def test_library():
    assert RT.library() == FRAMEWORK / "lua" / "lib" / RT.LIBRARY


def test_host_tables(species):
    si = species(
        [],
        attacks={
            "present": True,
            "tables": [
                {
                    "handle": "0x1",
                    "records": "0x200",
                    "volume_table": "0x100",
                    "primary": True,
                    "sets": [{"index": 0, "va": "0x9", "spheres": [{"bone": 1, "radius": 1}]}],
                    "attacks": [],
                }
            ],
        },
    )
    t = RT.host_attack_tables(si)
    assert (t.volumes_va, t.records_va, t.n_sets, t.capacities) == (0x100, 0x200, 1, {0: 1})
    assert RT.host_attack_tables(None) is None and RT.host_capacity(si) is None


# the generated module under mhfu_port.lua, over the real em75 bytes


@pytest.fixture
def lua() -> Any:
    """A Lua runtime like lua_host's, `mhfu` stubbed from its declaration, its memory a byte
    map `mem` that tests seed with real bytes."""
    lua54 = pytest.importorskip("lupa.lua54")
    spec = importlib.util.spec_from_file_location("lua_api", FRAMEWORK / "tools" / "lua_api.py")
    assert spec and spec.loader
    lua_api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lua_api)
    api = lua_api.parse((FRAMEWORK / "lua" / "meta" / "mhfu.d.lua").read_text(encoding="utf-8"))
    rt = lua54.LuaRuntime()
    rt.execute(f"package.path = {str(FRAMEWORK / 'lua' / 'lib' / '?.lua')!r}")
    rt.eval(
        """function(funcs, consts, addr, lo, hi)
          local m = { logs = {}, addr = addr }
          for _, name in ipairs(funcs) do m[name] = function() return 0 end end
          for name, value in pairs(consts) do m[name] = value end
          mem = {}
          local function rd(x) return mem[x] or 0 end
          local function wr(x, b) mem[x] = b & 0xFF end
          m.read_u8 = rd
          m.read_u16 = function(x) return rd(x) | rd(x + 1) << 8 end
          m.read_u32 = function(x) return m.read_u16(x) | m.read_u16(x + 2) << 16 end
          m.write_u8 = wr
          m.write_u16 = function(x, v) wr(x, v); wr(x + 1, v >> 8) end
          m.write_u32 = function(x, v) m.write_u16(x, v); m.write_u16(x + 2, v >> 16) end
          m.read_f32 = function(x)
            return (string.unpack("<f", string.char(rd(x), rd(x+1), rd(x+2), rd(x+3))))
          end
          m.write_f32 = function(x, v)
            local s = string.pack("<f", v)
            for i = 1, 4 do wr(x + i - 1, s:byte(i)) end
          end
          m.mem_valid = function(x) return x >= lo and x < hi end
          m.log = function(s) m.logs[#m.logs + 1] = s end
          m.get_screen_state = function() return 17 end
          m.entity_alive = function(e) return e ~= 0 end
          m.entities_of_type = function() return {} end
          mhfu = m
        end"""
    )(
        rt.table_from(list(api.funcs)),
        rt.table_from({k.name: k.value for k in api.consts}),
        rt.execute(a.render_lua(a.table())),
        a.RAM.start,
        a.RAM.stop,
    )
    return rt


class Mem:
    def __init__(self, lua: Any) -> None:
        self.mem = lua.globals().mem

    def load(self, at: int, blob: bytes) -> None:
        for i, b in enumerate(blob):
            self.mem[at + i] = b

    def read(self, at: int, n: int) -> bytes:
        return bytes(self.mem[at + i] or 0 for i in range(n))

    def u8(self, at: int) -> int:
        return self.read(at, 1)[0]

    def u16(self, at: int) -> int:
        return int.from_bytes(self.read(at, 2), "little")

    def f32(self, at: int) -> float:
        return float(struct.unpack("<f", self.read(at, 4))[0])


HV = a.HIT_VOLUME
REC = HV.step
AY, BZ = HV.OFFSET_A + 4, HV.OFFSET_B + 8


def run(lua: Any, text: str) -> list[str]:
    lua.execute(text)
    lua.execute(
        'P = require("mhfu_port"); port = P.define{ name = "t", species = 75, replace = {77} }'
    )
    lua.globals().port.ent = a.NPC_HEAP
    lua.execute("mhfu_tick(); mhfu_tick()")
    return list(lua.eval("mhfu.logs").values())


def applied(logs: list[str]) -> list[str]:
    return [line for line in logs if "HIT TABLES APPLIED" in line]


def test_hurtbox_writer(lua, make, games, em75):
    pt = em75.parts
    set0, states = pt.active_set_va, pt.state_table
    ovl, task = games.fu.em(75), games.fu.overlay(files.GAME_TASK)
    mem = Mem(lua)
    original = ovl.read(set0, 43 * REC)
    mem.load(set0, original)
    first = pt.states[0].va
    mem.load(first, task.read(first, states + 8 - first))
    row = a.SPECIES_TABLE + 75 * a.SPECIES.stride
    lua.eval("mhfu.write_u32")(row + a.SPECIES.HURTBOX_SET, set0)
    lua.eval("mhfu.write_u32")(row + a.SPECIES.HITZONE_STATES, states)
    m = make()
    m.hurtboxes = [Hurtbox(2, 582.0, 1, 2, offset=[0.0, -30.0, 30.0])]
    grid = [list(ROW)] + [[0] * 10 for _ in range(6)]
    grid[0][1:4] = [255] * 3
    m.hitzones = [
        HitzoneState("normal", grid),
        HitzoneState("enraged", [[0] * 10] * 6 + [[255] * 10]),
    ]
    logs = run(lua, RT.lua_hit_module(m, capacity=pt.capacity))
    assert len(applied(logs)) == 1, logs
    assert mem.u16(set0) == 2 and mem.u16(set0 + HV.HITZONE_ROW) == 2
    assert mem.u16(set0 + HV.PART) == 1 and mem.f32(set0 + AY) == -30.0
    assert mem.f32(set0 + HV.RADIUS) == pytest.approx(582.0)
    assert mem.u16(set0 + REC) == 0xFFFF and mem.read(set0 + REC + HV.RADIUS, 4) == bytes(4)
    assert mem.read(set0 + 2 * REC, REC) == original[2 * REC : 3 * REC]
    second = pt.states[1].va
    assert mem.u8(first + 1) == 255 and mem.u8(first) == 100 and mem.u8(second + 63) == 255
    assert mem.u8(second + 70) == task.read(second + 70, 1)[0], "the pad is not the grid's"
    assert lua.eval("port._hit_cap") == pt.capacity
    lua.eval("mhfu.write_f32")(set0 + HV.RADIUS, 97.0)
    lua.execute("mhfu_tick()")
    assert mem.f32(set0 + HV.RADIUS) == pytest.approx(582.0)


def test_attack_writer(lua, make, games, em75):
    tables = RT.host_attack_tables(em75)
    assert tables is not None
    ovl = games.fu.em(75)
    mem = Mem(lua)
    mem.load(tables.volumes_va, ovl.read(tables.volumes_va, 4 * tables.n_sets))
    sets = {s: int.from_bytes(ovl.read(tables.volumes_va + 4 * s, 4), "little") for s in (0, 2)}
    original = {s: ovl.read(va, (tables.capacities[s] + 1) * REC) for s, va in sets.items()}
    for s, va in sets.items():
        mem.load(va, original[s])
    rec = a.ATTACK_RECORD.step
    records = ovl.read(tables.records_va, tables.n_records * rec)
    mem.load(tables.records_va, records)
    m = hits(make())
    logs = run(lua, RT.lua_hit_module(m, attacks=tables))
    (line,) = applied(logs)
    assert "1 attack set(s)/3 volume(s)" in line and "2 attack record(s)" in line
    s2 = sets[2]
    assert mem.u16(s2) == 12 and mem.read(s2 + 2, 6) == bytes(6)
    assert mem.f32(s2 + HV.RADIUS) == pytest.approx(170.0) and mem.f32(s2 + AY) == 20.0
    assert (mem.u16(s2 + REC), mem.u16(s2 + REC + HV.SHAPE)) == (125, 1)
    assert mem.f32(s2 + 2 * REC + BZ) == -175.0 and mem.u16(s2 + 3 * REC) == 0xFFFF
    assert mem.read(s2 + 4 * REC, REC) == original[2][4 * REC : 5 * REC]
    assert mem.read(sets[0], len(original[0])) == original[0]
    r6, r31 = tables.records_va + 6 * rec, tables.records_va + 31 * rec
    assert mem.u8(r6 + a.ATTACK_RECORD.POWER) == 40
    for at, field in ((r6, a.ATTACK_RECORD.VOLUME_SET), (r31, a.ATTACK_RECORD.POWER)):
        assert mem.u8(at + field) == records[at - tables.records_va + field]
    assert mem.u8(r31 + a.ATTACK_RECORD.VOLUME_SET) == 5
    lua.eval("mhfu.write_f32")(s2 + HV.RADIUS, 97.0)
    lua.execute("mhfu_tick()")
    assert mem.f32(s2 + HV.RADIUS) == pytest.approx(170.0)

    bad = make()
    bad.hitboxes = [Hitbox(1, 50.0, 0)]
    bad.attacks = [Attack(9, power=1)]
    wrong = RT.AttackTables(
        tables.volumes_va, tables.records_va, tables.n_sets, tables.n_records, {0: 99}
    )
    lua.execute(RT.lua_hit_module(bad, attacks=wrong).replace('"t_hit"', '"t_hit_bad"'))
    seen = len(lua.eval("mhfu.logs"))
    lua.execute("mhfu_tick()")
    new = list(lua.eval("mhfu.logs").values())[seen:]
    assert any("NOT written" in line and "expected 99" in line for line in new), new
    assert mem.read(sets[0], len(original[0])) == original[0]
    r9 = tables.records_va + 9 * rec + a.ATTACK_RECORD.POWER
    assert mem.u8(r9) == records[r9 - tables.records_va]
