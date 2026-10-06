# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import importlib.util
import struct
from pathlib import Path
from typing import Any

import pytest
from mhfu import addresses as a
from mhfu import files, hitzone
from mhfu.hitzone import HitVolume
from mhfu.memory import Image, Space
from mhfu_port.manifest import Attack, Hitbox, HitzoneState, Hurtbox, ManifestError
from mhfu_studio.monster import push
from mhfu_studio.monster import runtime as RT

FRAMEWORK = Path(__file__).parents[4] / "framework"
ROW = [100, 75, 65, 40, 0, 15, 5, 30, 20, 110]
SETS = {i: (0x7000 + 0x100 * i, 10 if i == 2 else 5) for i in range(56)}
TABLES = RT.AttackTables(0x5000, 0x6000, 107, 0x7800, SETS)
HOST = RT.Host(75, 0x1000, 3, 0x3100, (0x3000, 0x3048), TABLES)
HV = a.HIT_VOLUME
REC = HV.step


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


def test_records():
    v = HitVolume(Image(RT.volume(Hurtbox(2, 600.0, 1, 3, offset=[0.0, 55.0, 0.0])), 0))
    assert (v.bone, v.shape, v.row, v.part, v.radius, v.offset) == (2, 0, 3, 1, 600.0, (0, 55, 0))
    cap = Hurtbox(6, 65.0, 4, 5, "capsule", [35.0, 0.0, 0.0], [330.0, 0.0, 0.0], 0x101)
    v = HitVolume(Image(RT.volume(cap), 0))
    assert (v.shape, v.flags, v.far) == (1, 0x101, (330.0, 0.0, 0.0))
    assert HitVolume(Image(RT.volume(Hurtbox(1, 1.0, to=[9.0] * 3)), 0)).far == (0, 0, 0)
    v = HitVolume(Image(RT.attack_volume(Hitbox(44, 1.0, 2, "capsule", [1.0] * 3, [0, 0, -5])), 0))
    assert (v.bone, v.shape, v.row, v.part, v.far) == (44, 1, 0, 0, (0.0, 0.0, -5.0))


def test_plan(make):
    m = hits(hurt(make()))
    p = RT.plan(m, HOST)
    whats = [w.what for w in p.writes]
    assert whats == [
        "hurtboxes",
        "grid state 0",
        "attack set 2",
        "attack 6 power",
        "attack 31 volume",
    ]
    hb, grid, s2, power, vol = p.writes
    assert hb.at == 0x1000 and len(hb.data) == 3 * REC and hb.data[-REC:] == hitzone.SENTINEL
    field = a.SPECIES_TABLE + 75 * a.SPECIES.stride
    assert hb.guards == (
        (field + a.SPECIES.HURTBOX_SET, (0x1000).to_bytes(4, "little")),
        (0x1000 + 3 * REC, b"\xff\xff"),
    )
    assert grid.at == 0x3000 and grid.data == bytes(ROW) + bytes(60)
    assert grid.guards[1] == (0x3100, (0x3000).to_bytes(4, "little"))
    assert s2.at == 0x7200 and len(s2.data) == 4 * REC
    assert s2.guards == ((0x7808, (0x7200).to_bytes(4, "little")), (0x7200 + 10 * REC, b"\xff\xff"))
    rec = a.ATTACK_RECORD
    assert (power.at, power.data) == (0x6000 + 6 * rec.step + rec.POWER, bytes([40]))
    assert (vol.at, vol.data) == (0x6000 + 31 * rec.step + rec.VOLUME_SET, bytes([5]))
    assert power.guards == ((0x5000, (0x6000).to_bytes(4, "little")),)
    assert p.notes == ("grid: 1 state(s), host em75 has 2: 1 written",)


def test_plan_leaves_out(make):
    m = hurt(make())
    m.hurtboxes *= 2
    p = RT.plan(m, HOST)
    assert len(p.writes[0].data) == 4 * REC, "three fit, then the sentinel"
    assert "hurtboxes: 4 volume(s), the host's set holds 3" in p.notes[0]


def test_module(make):
    m = hits(hurt(make()))
    text = RT.lua_hit_module(m, HOST, source="ports/t.toml")
    assert 'local port = require("mhfu_port")' in text and 'port.mod("t_hit"' in text
    assert 'P.hit("t", {' in text and "species = 75," in text and "ports/t.toml" in text
    assert 'what = "hurtboxes", at = 0x00001000, guard = {' in text
    assert '{ 0x00001078, "FFFF" }' in text
    assert '"02000000 00000100 00000000 00001644 00000000 00005C42 00000000 00000000' in text
    assert "-- one big sphere" in text and "-- head" in text and "-- softer" in text
    assert text.count('what = "') == 5
    assert 'notes = { "grid: 1 state(s), host em75 has 2: 1 written" },' in text


def test_refused(make):
    with pytest.raises(ManifestError, match="no hurtbox"):
        RT.plan(make(), HOST)
    m = hits(make())
    with pytest.raises(ManifestError, match="attack table addresses are unknown"):
        RT.plan(m, RT.Host(75))
    m.hitboxes.append(Hitbox(1, 1.0, 56))
    with pytest.raises(ManifestError, match="0..55"):
        RT.plan(m, HOST)
    m.hitboxes.pop()
    m.attacks.append(Attack(107, power=1))
    with pytest.raises(ManifestError, match="107 record"):
        RT.plan(m, HOST)
    with pytest.raises(ManifestError, match="hurtboxes but the set host em75 walks"):
        RT.plan(hurt(make()), RT.Host(75))
    with pytest.raises(ManifestError, match="intel"):
        RT.plan(hurt(make()), None)


def test_content_id(make):
    m = hits(hurt(make()))
    ids = [RT.content_id(m)]
    m.hurtboxes[0].radius = 2.0
    ids.append(RT.content_id(m))
    m.attacks[0] = Attack(6, power=41)
    ids.append(RT.content_id(m))
    m.hitboxes[0] = Hitbox(13, 170.0, 2)
    ids.append(RT.content_id(m))
    m.hitboxes[0].label = "renamed"
    ids.append(RT.content_id(m))
    assert len(set(ids)) == 4 and all(len(i) == 8 for i in ids)


def test_export_and_deploy(make, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    m = hurt(make())
    path = RT.export(m, None, HOST)
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


def test_host(intel75):
    h = RT.host(intel75)
    assert h is not None and (h.species, h.hurtboxes, h.capacity) == (75, 0x1000, 3)
    assert h.grids == (0x3000,) and h.attacks is not None
    t = h.attacks
    assert (t.handle_va, t.records_va, t.volumes_va, t.n_records) == (0x5000, 0x6000, 0x7800, 8)
    assert t.sets[2] == (0x7000, 2) and t.n_sets == 4
    assert RT.host(None) is None


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


def game(games: Any) -> Any:
    """game_task.ovl and em75.ovl at the addresses the game maps them, writable."""
    ovls = (games.fu.overlay(files.GAME_TASK), games.fu.em(75))
    return Space([Image(bytes(o.data), o.base) for o in ovls])


def seed(mem: Mem, space: Any, writes: tuple[RT.Write, ...]) -> None:
    """The game's bytes under every write and guard into the Lua byte map."""
    for w in writes:
        mem.load(w.at, space.read(w.at, len(w.data)))
        for at, b in w.guards:
            mem.load(at, space.read(at, len(b)))


def full(make):
    m = hits(hurt(make()))
    grid = [list(ROW)] + [[0] * 10 for _ in range(6)]
    grid[0][1:4] = [255] * 3
    m.hitzones = [HitzoneState("normal", grid), HitzoneState("enraged", [[255] * 10] * 7)]
    return m


def test_both_routes_write_the_same_bytes(lua, make, games, em75):
    """The stick's module under mhfu_port.lua and the debugger push leave the game alike."""
    m, host = full(make), RT.host(em75)
    p = RT.plan(m, host)
    space = game(games)
    mem = Mem(lua)
    seed(mem, space, p.writes)
    done = push.apply(space, p)
    assert not done.mismatched and not p.notes
    (line,) = applied(run(lua, RT.lua_hit_module(m, host)))
    assert f"{len(p.writes)} write(s), {p.size} byte(s)" in line
    for w in p.writes:
        assert mem.read(w.at, len(w.data)) == space.read(w.at, len(w.data)) == w.data, w.what
    assert space.read(host.hurtboxes + 2 * REC, REC) == hitzone.SENTINEL
    assert space.u16(host.hurtboxes + host.capacity * REC) == hitzone.SENTINEL_BONE
    lua.eval("mhfu.write_f32")(host.hurtboxes + HV.RADIUS, 97.0)
    lua.execute("mhfu_tick()")
    logs = list(lua.eval("mhfu.logs").values())
    assert "live table changed under us" in applied(logs)[-1]
    assert mem.f32(host.hurtboxes + HV.RADIUS) == pytest.approx(600.0)


def test_a_failing_guard_writes_nothing(lua, make, games, em75):
    m, host = full(make), RT.host(em75)
    assert host is not None and host.attacks is not None
    p = RT.plan(m, host)
    space = game(games)
    mem = Mem(lua)
    seed(mem, space, p.writes)
    moved = host.attacks.volumes_va + 4 * 2  # set 2's pointer, as a relocated overlay has it
    elsewhere = (a.RAM.start + 0x2000000).to_bytes(4, "little")
    mem.load(moved, elsewhere)
    space.write(moved, elsewhere)
    before = [space.read(w.at, len(w.data)) for w in p.writes]
    with pytest.raises(push.Refused, match="attack set 2: .* reads 0000000A"):
        push.apply(space, p)
    logs = run(lua, RT.lua_hit_module(m, host))
    assert not applied(logs) and any("NOT written: attack set 2" in x for x in logs), logs
    for w, b in zip(p.writes, before, strict=True):
        assert mem.read(w.at, len(w.data)) == space.read(w.at, len(w.data)) == b, w.what


def test_old_export_is_ignored(lua):
    lua.execute('P = require("mhfu_port"); P.hit("t", { species = 75, volumes = {} }); mhfu_tick()')
    assert any("re-export" in x for x in lua.eval("mhfu.logs").values())
