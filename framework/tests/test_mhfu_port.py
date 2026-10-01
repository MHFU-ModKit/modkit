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


def test_api_version(lua: Any) -> None:
    lua.execute("mhfu.api_version = 0")
    with pytest.raises(LuaError, match=r"api_version >= 1"):
        lua.execute('require("mhfu_port")')
