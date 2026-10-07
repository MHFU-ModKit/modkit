# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path
from typing import Any

import pytest
from mhfu import addresses as a
from mhfu.em.intel import SpeciesIntel
from mhfu_port import manifest
from mhfu_port.manifest import Claim, Manifest, Move
from mhfu_studio.monster import actions as A
from mhfu_studio.monster import clips as C
from mhfu_studio.monster.document import PortDocument

FRAMEWORK = Path(__file__).parents[4] / "framework"
#: slot 1 the original's own clip, 2 an idle copy
COV = C.Coverage(
    {1: C.SlotCoverage(1, C.CARRIED, 10, True), 2: C.SlotCoverage(2, C.FILLER, 6, False)},
    has_source=True,
)


@pytest.fixture
def intel(intel75: SpeciesIntel, make_pair: Any) -> SpeciesIntel:
    """The synthetic em75 and three more: one an idle copy plays, one whose anim is picked
    live, one the census saw never entered."""
    more = [
        make_pair(2, 9, a1=[2], prev=[[0, 3]]),
        make_pair(4, 1, a1=[], a1_computed=True, prev=[[0, 3]]),
        make_pair(4, 2, a1=[1], prev=[[0, 3]], measured={"entered": 0}),
    ]
    return SpeciesIntel(intel75.doc | {"pairs": [*intel75.doc["pairs"], *more]})


def rows(m: Manifest, intel: SpeciesIntel) -> list[A.ActionRow]:
    return A.rows(m, intel, COV, intel.attacks, 75)


def test_rows(intel: SpeciesIntel, port_doc: PortDocument) -> None:
    rs = rows(port_doc.manifest, intel)
    assert [(r.group, r.pair, r.move) for r in rs] == [
        (A.MOVE, (1, 4), "charge"),
        (A.ATTACK, (3, 9), None),
        (A.ENTERED, (0, 1), None),
        (A.ENTERED, (0, 3), None),
        (A.ENTERED, (0, 6), None),
        (A.ENTERED, (2, 9), None),
        (A.ENTERED, (4, 1), None),
    ], "(1,3) is entered by nothing known, (4,2) never"
    assert rs[1].alike == ((3, 10),) and rs[2].alike == ((0, 2),)
    assert rs[0].cells() == ["charge (1,4)", "walk", "group 2", "clip too short"]
    assert rs[1].cells() == ["(3,9) +1", "anim 14 · missing", "group 3", "–"]
    assert rs[5].cells()[1] == "anim 2 · idle copy" and rs[6].cells()[1] == "picked while it runs"
    assert "Same code and anim as (3,10)" in rs[1].tip() and "Your move charge" in rs[0].tip()


def test_plays_now(make: Any, intel: SpeciesIntel) -> None:
    m = make(
        """
[clips.bite]
slot = 2
impact_frame = 3

[moves.bite]
main = 3
sub = 9
clip = "bite"

[moves.rush]
main = 1
sub = 4
anim = 1
claim = 1
"""
    )
    bite = A.plays_now(m, intel.pair(3, 9), 3, 9, None, COV)
    assert (bite.slot, bite.name, bite.move, bite.impact, bite.frames) == (2, "bite", "bite", 3, 6)
    via = A.plays_now(m, intel.pair(1, 3), 1, 3, None, COV)
    assert via.claimed and via.text() == "anim 1 (via rush) · own clip"
    keeps = A.plays_now(m, intel.pair(0, 6), 0, 6, None, C.Coverage())
    assert keeps.kind is None and keeps.text() == "anim 14", "no build to look the anim up in"
    assert A.plays_now(m, None, 9, 9, None, COV).text() == "keeps the last clip"
    assert A.bound_move(m, 3, 9) == "bite" and A.claimer(m, 1, 30) == "rush"


def test_timing_in_the_row(make: Any, intel: SpeciesIntel) -> None:
    m = make(
        '[clips.walk]\nslot = 1\nimpact_frame = 7\n[moves.c]\nmain = 1\nsub = 4\nclip = "walk"'
    )
    r = rows(m, intel)[0]
    assert r.timing.text == "clip too short" and r.timing.level == "error"
    m.moves["c"].sub = 3
    assert rows(m, intel)[0].timing.text == "–"


# the moves as Lua

LUA = """
[clips.lunge_forward]
slot = 6

[clips.stop]
slot = 21

[moves.lunge]
main = 1
sub = 4
clip = "lunge_forward"
after = "halt"
hold_max = 12
latch = 2
min_gap = 4
label = "not in the Lua"
claim = { main = [0, 1], sub = 7 }

[moves.halt]
main = 0
sub = 3
clip = "stop"
claim = 3

[moves.raw-anim]
main = 2
sub = 1
anim = 40
"""


def lua_fields(mv: Move) -> tuple[object, ...]:
    """What `mhfu_port.lua` reads of a move."""
    c = None if mv.claim is None else (tuple(mv.claim.mains), mv.claim.sub)
    return mv.main, mv.sub, mv.clip, mv.anim, mv.latch, mv.after, mv.hold_max, c


def define(snippet: str) -> Any:
    """`snippet` through the framework's own `P.define`, `mhfu` stubbed."""
    lua54 = pytest.importorskip("lupa.lua54")
    rt = lua54.LuaRuntime()
    rt.execute(f"package.path = {str(FRAMEWORK / 'lua' / 'lib' / '?.lua')!r}")
    rt.eval(
        """function(addr)
          mhfu = setmetatable({ api_version = 1, addr = addr },
                              { __index = function() return function() return 0 end end })
        end"""
    )(rt.execute(a.render_lua(a.table())))
    rt.execute(f'port = require("mhfu_port").define{{ name = "t", species = 75,\n{snippet}\n}}')
    return rt.globals().port


def back(port: Any) -> tuple[dict[str, int], dict[str, tuple[object, ...]]]:
    out = {}
    for name, t in port.moves.items():
        c = t.claim
        claim = None
        if c is not None:
            mains = c.main if isinstance(c.main, int) else list(c.main.values())
            claim = Claim(mains if isinstance(mains, list) else [mains], c.sub)
        mv = Move(t.main, t.sub, t.clip, t.anim, t.latch or 1, after=t.after, hold_max=t.hold_max)
        mv.claim = claim
        out[name] = lua_fields(mv)
    return dict(port.clips.items()), out


def test_lua_round_trips(make: Any) -> None:
    m = make(LUA)
    text = A.lua_moves(m)
    assert 'halt = { main = 0, sub = 3, clip = "stop", claim = { main = { 3 } } },' in text
    assert "claim = { main = { 0, 1 }, sub = 7 }" in text and "label" not in text
    clips, moves = back(define(text))
    assert clips == {} and "clips =" not in text, "the clips come from the layout module"
    assert moves == {n: lua_fields(mv) for n, mv in m.moves.items() if not mv.own}
    assert A.lua_moves(make("")) == ""


@pytest.mark.xfail(strict=True, reason="mhfu_port.moves.lua_key writes a Lua keyword bare")
def test_lua_keyword_names(make: Any) -> None:
    text = A.lua_moves(make(LUA.replace('"halt"', '"end"').replace("moves.halt", "moves.end")))
    assert '["end"] = ' in text
    assert "end" in back(define(text))[1]


def test_lua_of_the_zinogre(ports: Path) -> None:
    m = manifest.load(ports / "zinogre.toml")
    text = A.lua_moves(m)
    assert text.startswith("-- from zinogre.toml") and "are in zinogre_clips.lua" in text
    clips, moves = back(define(text))
    assert clips == {}
    assert moves == {n: lua_fields(mv) for n, mv in m.moves.items() if not mv.own}
