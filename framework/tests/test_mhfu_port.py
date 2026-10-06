# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""mhfu_port as a library, against the declared API."""

from pathlib import Path
from typing import Any

import pytest
from lupa.lua54 import LuaError

LIB = Path(__file__).parents[1] / "lua" / "lib" / "mhfu_port.lua"

# re-runs the library in place, as lua_host does when a required library changes
RELOAD = f"""
local f = assert(loadfile({str(LIB)!r}))
package.loaded.mhfu_port = f("mhfu_port") or package.loaded.mhfu_port
"""


def test_require(lua: Any) -> None:
    assert lua.eval('require("mhfu_port") == package.loaded.mhfu_port')
    assert lua.eval('type(mhfu_tick) == "function" and mhfu.port == nil')


def test_mod_replaces(lua: Any) -> None:
    lua.execute(
        """
        local port = require("mhfu_port")
        runs = { a = 0, b = 0 }
        port.mod("m", function(P) runs.a = runs.a + 1; P.define{ name = "x", species = 75 } end)
        first = runs.a
        port.mod("m", function(P) runs.b = runs.b + 1; P.define{ name = "x", species = 75 } end)
        """
        + RELOAD
    )
    assert lua.eval("first") == 1
    assert (lua.eval("runs.a"), lua.eval("runs.b")) == (1, 2)


def test_hot_reload(lua: Any) -> None:
    lua.execute(
        """
        port = require("mhfu_port")
        port.mod("m", function(P) P.define{ name = "x", species = 75 } end)
        before = port.ports.x
        """
        + RELOAD
    )
    assert lua.eval('require("mhfu_port") == port')
    assert lua.eval("port.ports.x ~= nil and port.ports.x ~= before")


def test_log_survives_reload(lua: Any) -> None:
    lua.execute('port = require("mhfu_port"); old_log = port.log' + RELOAD)
    lua.execute('old_log("from a handler of the first load"); mhfu_tick()')
    assert "from a handler of the first load" in lua.eval("mhfu.logs").values()


def test_play_fallback(lua: Any) -> None:
    lua.execute(
        """
        port = require("mhfu_port")
        mhfu.em_installed = function() return false end
        mhfu.entities_of_type = function() return { 0x100000 } end
        port.mod("m", function(P)
          P.define{ name = "x", species = 75, clips = { run = 61 },
                    moves = { charge = { main = 2, sub = 8, clip = "run" } } }
            :brain(function(s) if not s.move then s.port:play("charge") end end)
        end)
        mhfu.calls = {}
        mhfu_tick()
        function wrote(off, v)
          for _, c in ipairs(mhfu.calls) do
            if c[1] == "write_u8" and c[2] == 0x100000 + off and c[3] == v then return true end
          end
          return false
        end
        """
    )
    assert lua.eval(
        "wrote(mhfu.addr.ENTITY.MAIN_STATE, 2) and wrote(mhfu.addr.ENTITY.SUB_STATE, 8)"
    )
    assert lua.eval("port.ports.x.clip") == 61


def test_layout_module(lua: Any, tmp_path: Path) -> None:
    """P.define takes its clips from the module mhfu-port generates, unless given its own."""
    from mhfu_port import layout, manifest

    m = manifest.loads(
        '[port]\nname = "z"\nhost_species = 75\npac = "z.bin"\n[source]\nmodel = 5339\n'
        "[clips.howl]\nslot = 2\n"
    )
    (tmp_path / layout.module_name(m)).write_text(layout.lua(m, layout.Layout({2: 2, 100: 205})))
    lua.execute(f"package.path = package.path .. ';' .. {str(tmp_path / '?.lua')!r}")
    lua.execute(
        """
        mhfu.entities_of_type = function() return {} end
        local port = require("mhfu_port")
        z = port.define{ name = "z", species = 75 }
        x = port.define{ name = "x", species = 75, clips = { a = 1 } }
        y = port.define{ name = "y", species = 75 }
        port.define{ name = "y", species = 75 }
        mhfu_tick()
        """
    )
    assert dict(lua.eval("z.clips").items()) == {"howl": 2, "clip_100": 100}
    assert dict(lua.eval("x.clips").items()) == {"a": 1}
    assert dict(lua.eval("y.clips").items()) == {}
    missing = [line for line in lua.eval("mhfu.logs").values() if "no clip layout" in line]
    assert len(missing) == 1 and "y_clips.lua" in missing[0]


def test_api_version(lua: Any) -> None:
    lua.execute("mhfu.api_version = 0")
    with pytest.raises(LuaError, match=r"api_version >= 1"):
        lua.execute('require("mhfu_port")')


# a byte map under the stubs, for tests that read back what the library writes
def memory(lua: Any) -> None:
    from mhfu.addresses import RAM

    lua.execute(
        f"""
        mem = {{}}
        local function rd(x) return mem[x] or 0 end
        local function wr(x, b) mem[x] = b & 0xFF end
        mhfu.read_u8, mhfu.write_u8 = rd, wr
        mhfu.read_u16 = function(x) return rd(x) | rd(x + 1) << 8 end
        mhfu.write_u16 = function(x, v) wr(x, v); wr(x + 1, v >> 8) end
        mhfu.read_u32 = function(x) return mhfu.read_u16(x) | mhfu.read_u16(x + 2) << 16 end
        mhfu.write_u32 = function(x, v) mhfu.write_u16(x, v); mhfu.write_u16(x + 2, v >> 16) end
        mhfu.read_f32 = function(x)
          return (string.unpack("<f", string.pack("<I4", mhfu.read_u32(x))))
        end
        mhfu.write_f32 = function(x, v)
          mhfu.write_u32(x, (string.unpack("<I4", string.pack("<f", v))))
        end
        mhfu.mem_valid = function(x) return x >= {RAM.start} and x < {RAM.stop} end
        mhfu.get_screen_state = function() return 17 end
        mhfu.get_area_index = function() return 99 end
        mhfu.entity_alive = function(e) return e ~= 0 end
        mhfu.entities_of_type = function() return {{}} end
        ENT = mhfu.addr.NPC_HEAP
        local E = mhfu.addr.ENTITY
        local MAIN, SUB = ENT + E.MAIN_STATE, ENT + E.SUB_STATE
        function pair() return mhfu.read_u8(MAIN), mhfu.read_u8(SUB) end
        function set_pair(m, s) mhfu.write_u8(MAIN, m); mhfu.write_u8(SUB, s) end
        function set_phase(p) mhfu.write_u8(ENT + E.PHASE, p) end
        function count(pat, from)
          local n = 0
          for i = from or 1, #mhfu.logs do if mhfu.logs[i]:find(pat) then n = n + 1 end end
          return n
        end
        function ticks(n) for _ = 1, n do mhfu_tick() end end
        """
    )


CHAIN = """
local HOOK
mhfu.on_bigmonster_action = function(fn) HOOK = fn end
local P = require("mhfu_port")
local zin = P.define{
  name = "zinogre", species = 75, replace = {77},
  clips = { lunge_forward = 6, stop_walk_forward = 5, other = 7 },
  moves = {
    lunge      = { main = 1, sub = 4, clip = "lunge_forward", after = "lunge_stop", hold_max = 8 },
    lunge_stop = { main = 1, sub = 3, clip = "stop_walk_forward" },
    held       = { main = 2, sub = 9, clip = "other" },
  },
}
zin.ent = ENT
assert(HOOK, "the executor hook was not registered")
ticks(1)

-- a re-entry of the running pair is refused, once in the log; forced it goes through
assert(zin:play("lunge"))
local m, s = pair()
assert(m == 1 and s == 4 and zin.move == "lunge")
ticks(3)
assert(not zin:play("lunge") and not zin:play("lunge"), "re-entry refused")
ticks(1)
assert(count("refused: %(1,4%) is already running %(ours%)") == 1 and count("refused") == 1)
assert(zin:play("lunge", 0, { force = true }) and zin.move == "lunge")

-- the engine leaves the pair: `after` is walked
set_pair(0, 3)
ticks(1)
m, s = pair()
assert(m == 1 and s == 3 and zin.move == "lunge_stop" and zin.last_move == "lunge")
assert(count("'lunge' ended %-> after='lunge_stop'") == 1 and zin.clip == 5)

-- hold_max ends a standing pair into `after`
set_pair(0, 1)
ticks(3)
assert(zin.move == nil and zin:play("lunge"))
ticks(9)
m, s = pair()
assert(m == 1 and s == 3, "hold_max walked to lunge_stop")
ticks(1)
assert(count("held %d+ ticks %(hold_max 8%)") == 1)
assert(count("'lunge' hold_max %-> after='lunge_stop'") == 1)

-- a move with neither, its phase never moving, is PARKED once and kept
set_pair(0, 1)
ticks(2)
assert(zin:play("held"))
set_phase(3)
ticks(25)
assert(count("move 'held' PARKED: %(2,9%) phase 3 unchanged") == 1 and count("PARKED") == 1)
assert(zin.move == "held")

-- a declared pair the engine enters itself is painted once per entry
zin:release()
set_pair(1, 4)
assert(HOOK({ entity = ENT, action_id = 17 }) == 6)
assert(HOOK({ entity = ENT, action_id = 17 }) == nil, "latch 1")
ticks(1)
assert(count("engine entered 'lunge' itself") == 1)
set_pair(0, 3); ticks(1); set_pair(1, 4)
assert(HOOK({ entity = ENT, action_id = 17 }) == 6, "painted again on the next entry")
set_pair(0, 4)
assert(HOOK({ entity = ENT, action_id = 51 }) == nil and zin.move == nil)

-- an `after` the engine already reached is adopted, not re-written
set_pair(0, 1)
ticks(2)
assert(zin:play("lunge"))
set_pair(1, 3)
ticks(2)
assert(zin.move == "lunge_stop" and count("the engine is already in %(1,3%), adopted") == 1)
"""


def test_move_chain(lua: Any) -> None:
    memory(lua)
    lua.execute(CHAIN)


NATIVE = """
local SEAM = { installed = false, subs = {}, rules = {}, req = nil, req_done = 0, land = true,
               land_as = nil, ring = {} }
function mhfu.em_installed() return SEAM.installed end
function mhfu.em_substitute(slot, mask, sub, tm, ts, count)
  SEAM.subs[slot] = { mask = mask, sub = sub, to_main = tm, to_sub = ts, count = count }
  return true
end
function mhfu.em_rule(slot, r) SEAM.rules[slot] = r; return true end
function mhfu.em_clear() SEAM.subs, SEAM.rules = {}, {} end
function mhfu.em_request(m, s, mode)
  SEAM.req = { m, s, mode }
  SEAM.req_done = SEAM.req_done + 1
  if SEAM.land then
    local lm, ls = SEAM.land_as and SEAM.land_as[1] or m, SEAM.land_as and SEAM.land_as[2] or s
    set_pair(lm, ls)
    set_phase(0)
    SEAM.result = { lm, ls }
  else
    SEAM.result = { pair() }
  end
  table.insert(SEAM.ring, { main = m, sub = s, mode = mode, subst = 0 })
  return true
end
function mhfu.em_status()
  local ring = {}
  for i = math.max(1, #SEAM.ring - 7), #SEAM.ring do ring[#ring + 1] = SEAM.ring[i] end
  local r = SEAM.result or { 0, 0 }
  return { installed = SEAM.installed, req_done = SEAM.req_done, req_main = r[1], req_sub = r[2],
           ring = ring, sub_hits = 0, sub_landed = 0, brain_fires = 0 }
end
local HOOK
mhfu.on_bigmonster_action = function(fn) HOOK = fn end

local P = require("mhfu_port")
local function define()
  local z = P.define{
    name = "zinogre", species = 75, replace = {77},
    clips = { lunge_forward = 6, dash_forward_stop = 21 },
    moves = {
      lunge      = { main = 1, sub = 4, clip = "lunge_forward", after = "lunge_stop",
                     hold_max = 8, claim = { main = 1 } },
      lunge_stop = { main = 0, sub = 3, clip = "dash_forward_stop" },
    },
  }
  z:rule{ from = "lunge", min_frames = 15, dist = { 250, 1e9 }, receding = true,
          play = "lunge_stop", cooldown = 30 }
  return z
end
local zin = define()
zin.ent = ENT

-- the seam down: play() writes the cells, nothing is armed, `after` walks by hand
ticks(1)
assert(zin:play("lunge"))
local m, s = pair()
assert(m == 1 and s == 4 and SEAM.req == nil and next(SEAM.subs) == nil)
set_pair(0, 1); ticks(1)
m, s = pair(); assert(m == 0 and s == 3 and zin.move == "lunge_stop")
set_pair(0, 1); ticks(2)
assert(zin.move == nil)

-- the seam up: the next tick arms claims and rules, the unused slots cleared
SEAM.installed = true
ticks(1)
assert(count("native seam live") == 1)
local c = SEAM.subs[0]
assert(c and c.mask == 0x02 and c.sub == mhfu.EM_ANY and c.to_main == 1 and c.to_sub == 4
       and c.count == mhfu.EM_UNLIMITED)
for k = 1, 3 do assert(SEAM.subs[k] and SEAM.subs[k].count == 0 and SEAM.rules[k] == nil) end
local r = SEAM.rules[0]
assert(r and r.from_mask == 0x02 and r.from_sub == 4 and r.to_main == 0 and r.to_sub == 3
       and r.min_frames == 15 and r.receding == true and r.closing == false
       and r.dist_lo == 250 and r.cooldown == 30)
assert(count("claim: host enter%-actions with main in 0x02 %-> 'lunge'") == 1)
assert(count("rule 1: main 0x02 sub 4 >=15 frames d%[250,inf%) receding %-> 'lunge_stop'") == 1)

-- play() requests through the seam and is confirmed by the cells; `after` is adopted
local t2 = #mhfu.logs
assert(zin:play("lunge"))
assert(SEAM.req[1] == 1 and SEAM.req[2] == 4 and SEAM.req[3] == 0 and zin.clip == 6)
ticks(1)
assert(count("'lunge' entered natively %(1,4%), provisioned") == 1 and zin._req == nil)
set_pair(0, 3)
ticks(1)
assert(count("move 'lunge' %(1,4%) ended after", t2) == 1)
assert(zin.move == "lunge_stop" and count("adopted", t2) == 1 and SEAM.req[1] == 1)
set_pair(0, 1); ticks(2)

-- the translator's other main for the id is tracked, not read as ended
SEAM.land_as = { 2, 4 }
assert(zin:play("lunge"))
ticks(1)
assert(count("entered natively as %(2,4%) — the translator's main for id 4") == 1)
ticks(3)
assert(zin.move == "lunge" and zin._entered[1] == 2)
set_pair(0, 3); ticks(1)
assert(count("move 'lunge' %(2,4%) ended after") == 1)
SEAM.land_as = nil
set_pair(0, 1); ticks(3)
assert(zin.move == nil)

-- landed and over within the tick: not a decline, `after` adopted where he stands
local t3 = #mhfu.logs
assert(zin:play("lunge"))
set_pair(0, 3)
ticks(1)
assert(count("'lunge' entered natively %(1,4%) and was over within the tick "
             .. "%(now %(0,3%)%)", t3) == 1)
assert(count("was requested and issued but", t3) == 0)
assert(zin.move == "lunge_stop" and count("adopted", t3) == 1)
set_pair(0, 1); ticks(3)

-- a declined request is reported with the ring and walks no `after`
SEAM.land = false
local t4 = #mhfu.logs
assert(zin:play("lunge"))
ticks(2)
assert(count("was requested and issued but the cells read %(0,1%)", t4) == 1)
assert(count("last enter%-actions: .*%(1,4,m0%)", t4) == 1)
assert(zin.move == nil and zin.clip == nil and count("after='lunge_stop'", t4) == 0)
SEAM.land = true

-- {raw=true} writes the cells with the seam live
local reqs = SEAM.req_done
assert(zin:play("lunge", 0, { raw = true }))
m, s = pair()
assert(m == 1 and s == 4 and SEAM.req_done == reqs)
ticks(1)
assert(zin.move == "lunge")
set_pair(0, 1); ticks(2)

-- a redefine re-arms; the seam-live line is said once
SEAM.subs, SEAM.rules = {}, {}
zin = define(); zin.ent = ENT
ticks(1)
assert(SEAM.subs[0] and SEAM.subs[0].to_sub == 4 and SEAM.rules[0])
assert(count("native seam live") == 1)
set_pair(1, 4)
assert(HOOK({ entity = ENT, action_id = 17 }) == 6, "a claimed pair the engine enters is painted")
"""


def test_native_seam(lua: Any) -> None:
    memory(lua)
    lua.execute(NATIVE)


HIT_RELOAD = """
local QUEST
mhfu.on_quest_targets_building = function(fn) QUEST = fn end
local P = require("mhfu_port")
local HV = mhfu.addr.HIT_VOLUME
-- a host hurtbox set of 3 records and one attack set of 2, each sentinel-terminated
local SET, VT, ASET = ENT + 0x4000, ENT + 0x5000, ENT + 0x6000
local row = mhfu.addr.SPECIES_TABLE + 75 * mhfu.addr.SPECIES.STRIDE
mhfu.write_u32(row + mhfu.addr.SPECIES.HURTBOX_SET, SET)
mhfu.write_u32(VT, ASET)
for i = 0, 2 do mhfu.write_u16(SET + i * HV.SIZE, 1) end
mhfu.write_u16(SET + 3 * HV.SIZE, 0xFFFF)
for i = 0, 1 do mhfu.write_u16(ASET + i * HV.SIZE, 1) end
mhfu.write_u16(ASET + 2 * HV.SIZE, 0xFFFF)
local zin = P.define{ name = "zinogre", species = 75 }
zin.ent = ENT
-- a hot-reloaded <name>_hit.lua re-runs this; 4 volumes over a cap of 3 are truncated
local function export(id, radius)
  local v = { 2, 0, 0, 1, 0, radius, 0, 0, 0, 0, 0, 0 }
  P.hit("zinogre", { species = 75, id = id, volumes = { v, v, v, v },
                     attack_tables = { volumes = VT, n_sets = 1 },
                     attack_sets = { [0] = { cap = 2, volumes = { v } } } })
end
local function radius() return mhfu.read_f32(SET + HV.RADIUS) end

export("599b6933", 100.0); ticks(2)
assert(count("APPLIED %(first contact%).*id=599b6933") == 1 and radius() == 100.0)
export("73527c5b", 200.0); ticks(2)
assert(count("APPLIED %(new export%).*id=73527c5b") == 1 and radius() == 200.0)
export("73527c5b", 200.0); ticks(2)
assert(count("APPLIED") == 2, "the same id is not re-applied")
export("599b6933", 100.0); ticks(1)
assert(count("APPLIED %(new export%).*id=599b6933") == 1 and radius() == 100.0)
mhfu.write_f32(SET + HV.RADIUS, 5.0); ticks(1)
assert(count("APPLIED %(live table changed under us%)") == 1 and radius() == 100.0)
assert(count("first contact") == 1)

-- a brain-mod reload redefines the port: what is in place stays in place
local before = zin
zin = P.define{ name = "zinogre", species = 75 }
for _, k in ipairs({ "ent", "_hit_id", "_hit_last", "_hit_cap", "_atk_caps" }) do
  assert(zin[k] ~= nil and zin[k] == before[k], k)
end
ticks(2)
assert(count("APPLIED") == 4, "not re-applied after a redefine")

-- the next quest loads the overlay afresh: its first apply is first contact again
QUEST(1)
mhfu.write_f32(SET + HV.RADIUS, 5.0); ticks(1)
assert(count("APPLIED %(first contact%).*id=599b6933") == 2 and radius() == 100.0)
"""


def test_hit_reload(lua: Any) -> None:
    memory(lua)
    lua.execute(HIT_RELOAD)


def test_fits_the_file_buffer() -> None:
    """lua_host reads a script through one buffer and skips a bigger one at boot."""
    import re

    framework = Path(__file__).parents[1]
    src = (framework / "mods" / "lua_host" / "scripts.cpp").read_text(encoding="utf-8")
    m = re.search(r"#define G_FILEBUF_SZ \((\d+) \* 1024\)", src)
    assert m, "G_FILEBUF_SZ not found"
    skipped_from = int(m[1]) * 1024 - 1  # read_script skips a read that fills the buffer
    for path in sorted((framework / "lua").glob("[le]*/*.lua")):
        assert path.stat().st_size < skipped_from, f"{path.name}: lua_host skips it for its size"
