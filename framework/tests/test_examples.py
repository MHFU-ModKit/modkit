# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The example mods in lupa, against the declared API."""

from pathlib import Path
from typing import Any

import pytest
from mhfu import addresses as a

LUA = Path(__file__).parents[1] / "lua"
TIGREX, GIADROME, POPO = 0x4B, 0x4D, 0x46
ENT = a.RAM.start + 0x100000
QUEST = a.RAM.start + 0x200000
SPIN = 43


def run(lua: Any, name: str) -> Any:
    """Runs examples/<name>.lua; returns the fake `mhfu`."""
    lua.execute((LUA / "examples" / f"{name}.lua").read_text(encoding="utf-8"))
    return lua.globals().mhfu


def handler(m: Any, event: str) -> Any:
    return next(c[2] for c in m.calls.values() if c[1] == event)


def calls(m: Any, name: str) -> list[tuple[Any, ...]]:
    return [tuple(c.values())[1:] for c in m.calls.values() if c[1] == name]


def action(lua: Any, species: int = TIGREX) -> Any:
    return lua.table_from({"entity": ENT, "type": species, "action_id": 5})


def test_action_override_pulses(lua: Any) -> None:
    m = run(lua, "action_override")
    on_action = handler(m, "on_bigmonster_action")
    ctx = action(lua)
    clock = [90000]
    m.get_quest_timer = lambda: clock[0]
    assert on_action(ctx) == SPIN
    clock[0] -= 299
    assert on_action(ctx) is None
    clock[0] -= 1
    assert on_action(ctx) == SPIN
    assert "[action_override] a1 5 -> 43 (spin)" in m.logs.values()


def test_action_override_leaves_the_rest(lua: Any) -> None:
    m = run(lua, "action_override")
    on_action = handler(m, "on_bigmonster_action")
    assert on_action(action(lua, GIADROME)) is None
    m.entity_section = lambda _ent: 1  # not the player's section
    assert on_action(action(lua)) is None


def test_action_override_clears_the_freeze_gate(lua: Any) -> None:
    m = run(lua, "action_override")
    m.read_u32 = lambda _at: 0x10107
    handler(m, "on_bigmonster_action")(action(lua))
    assert calls(m, "write_u32") == [(ENT + a.ENTITY.FREEZE_GATE, 0x7)]


@pytest.mark.parametrize(
    ("monsters", "done"),
    [({TIGREX}, ["add"]), ({GIADROME}, ["replace", "add"]), ({POPO}, [])],
)
def test_second_monster_adds_a_tigrex(lua: Any, monsters: set[int], done: list[str]) -> None:
    m = run(lua, "second_monster")
    quest, did = set(monsters), []

    def replace(_q: int, old: int, new: int) -> bool:
        quest.discard(old)
        quest.add(new)
        did.append("replace")
        return True

    def add(_q: int, mon: int) -> bool:
        did.append("add")
        return mon == TIGREX

    m.quest_has = lambda _q, mon: mon in quest
    m.quest_replace_monster, m.quest_add_monster = replace, add
    handler(m, "on_quest_targets_building")(QUEST)
    assert did == done
    assert ("[second_monster] added a second Tigrex" in m.logs.values()) == bool(done)


def test_second_monster_logs_tigrex_spawns(lua: Any) -> None:
    m = run(lua, "second_monster")
    spawn = handler(m, "on_bigmonster_spawn")
    spawn(ENT, POPO, 1, 100)
    spawn(ENT, TIGREX, 2, 3200)
    assert list(m.logs.values())[-1] == "[second_monster] Tigrex in slot 2, hp 3200"


FAKE_PORT = """
local port = {}
function port.mod(name, fn)
  port.name = name
  fn({
    log = function(fmt, ...) mhfu.logs[#mhfu.logs + 1] = string.format(fmt, ...) end,
    define = function(spec)
      port.spec = spec
      return { brain = function(self, f) port.brain = f; return self end }
    end,
  })
end
package.loaded.mhfu_port = port
"""


def test_ported_brute_reports_section_changes(lua: Any) -> None:
    lua.execute(FAKE_PORT)
    m = run(lua, "ported_brute")
    port = lua.eval("package.loaded.mhfu_port")
    assert (port.name, port.spec.name, port.spec.fid) == ("ported_brute", "brute_tigrex", 6186)
    for state in (
        {"same_section": False, "dist": 9000},
        {"same_section": True, "dist": 2000.4},
        {"same_section": True, "dist": 1500},
        {"same_section": False, "dist": 4000},
    ):
        port.brain(lua.table_from(state))
    assert list(m.logs.values()) == [
        "[ported_brute] in your section, 2000 units away",
        "[ported_brute] left your section",
    ]


@pytest.mark.skipif(
    not (LUA / "lib" / "mhfu_port.lua").exists(), reason="lib/mhfu_port.lua not written yet"
)
def test_ported_brute_loads_on_mhfu_port(lua: Any) -> None:
    m = run(lua, "ported_brute")
    fid, pac, orig = calls(m, "inject_relocate")[0]
    assert fid == 6186
    assert pac.endswith("/brute_tigrex.bin") and orig.endswith("/file_06185.bin.orig")


def test_ported_zinogre_dashes_at_a_far_hunter(lua: Any, tmp_path: Path) -> None:
    (tmp_path / "zinogre_moves.lua").write_text(
        "return { moves = { dash = { entry = 20, carrier = { 0, 2 }, steer = { walls = true },"
        ' after = "dash_stop" }, dash_stop = { entry = 21 } }, rules = {} }\n'
    )
    lua.execute(f"package.path = package.path .. ';' .. {str(tmp_path / '?.lua')!r}")
    m = run(lua, "ported_zinogre")
    zin = lua.eval('require("mhfu_port").ports.zinogre')
    m.em_installed = lambda: True
    zin.ent, zin._native_armed = ENT, True
    state = {"native": True, "dist": 2000.0, "px": 1.0, "pz": 2.0, "tick": 100}
    zin._brain(lua.table_from({**state, "dist": 900.0}))
    zin._brain(lua.table_from(state))
    zin._brain(lua.table_from({**state, "tick": 110}))  # within the gap
    assert calls(m, "em_play") == [(ENT, 0, False)]
