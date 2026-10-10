# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

import pytest
from mhfu_port import behaviour, build, layout, manifest, moves, travel
from mhfu_port.manifest import ManifestError
from mhfu_port.rig import Tip

PORTS = Path(__file__).parents[3] / "ports"

HEAD = """
schema = 1
[port]
name = "z"
host_species = 75
pac = "z.bin"
[source]
model = 5339
[clips.stamp]
source = 200
[clips.dash]
slot = 20
[clips.stop]
slot = 21
[moves.lunge]
main = 1
sub = 4
clip = "dash"
hold_max = 12
claim = 1
"""
OWN = """
[moves.stamp]
clip = "stamp"
after = "dash"
[[moves.stamp.attack]]
id = 6
frame = 56
end = 80
[moves.stamp.steer]
walls = false
[moves.dash]
clip = "dash"
length = 40
host_attacks = true
after = "lunge"
[moves.dash.steer]
turn = "hunter"
dir = 180.0
[moves.spin]
anim = 9
carrier = [0, 1]
[moves.spin.steer]
turn = "fixed"
angle = 90.0
frames = 50
[[rule]]
play = "stamp"
from_main = [0]
min_frames = 30
dist = [0.0, 700.0]
cooldown = 300
[[rule]]
play = "lunge"
from = "dash"
receding = true
count = 2
"""
TURN = travel.Turn(4, (0, 0x2000, 0x4000), 0x4000, None)
LAYOUT = layout.Layout(
    {20: 20, 21: 21, 46: 200, 9: 9}, turns={46: TURN}, frames={46: 228, 20: 82, 21: 66, 9: 100}
)


def _m(extra: str = OWN) -> manifest.Manifest:
    return manifest.loads(HEAD + extra)


def _body(text: str) -> list[str]:
    lines = [line.strip() for line in moves.BUILD.sub("", text).splitlines()]
    return [line for line in lines if line not in ("return {", "},", "}", "") and line[:2] != "--"]


def test_build():
    text = moves.lua(_m(), LAYOUT, {6})
    b = moves.build_of(text)
    assert 0 < b < 1 << 31 and text.startswith("--") and "return {\n  build = 0x" in text
    assert moves.build_of(moves.lua(_m(), LAYOUT, {6})) == b
    assert moves.build_of(moves.lua(_m(OWN.replace("40", "41")), LAYOUT, {6})) != b
    assert moves.build_of("return {}") == 0


def test_lua():
    text = moves.lua(_m(), LAYOUT, {6})
    assert moves.module_name(_m()) == "z_moves.lua" and "GENERATED" in text
    assert _body(text) == [
        "moves = {",
        'lunge = { main = 1, sub = 4, clip = "dash", hold_max = 12, claim = { main = { 1 } } },',
        'stamp = { entry = 46, clip = "stamp", attacks = { { 56, 6, 80 } }, carrier = { 0, 2 }, '
        'steer = { walls = false, curve = "000020004000" }, after = "dash" },',
        'dash = { entry = 20, clip = "dash", carrier = { 0, 2 }, length = 40, host_attacks = true, '
        'steer = { walls = true, dir = 180.0, turn = "hunter" }, after = "lunge" },',
        "spin = { entry = 9, carrier = { 0, 1 }, "
        'steer = { walls = true, turn = "fixed", total = 90.0, frames = 50 } },',
        "rules = {",
        '{ play = "stamp", from_main = { 0 }, min_frames = 30, dist = { 0.0, 700.0 }, '
        "cooldown = 300 },",
        '{ play = "lunge", from = "dash", receding = true, count = 2 },',
    ]


def test_tip():
    tip = Tip(((46, 42), (47, 42), (48, 43)), (0.0, 0.0, -705.0))
    body = _body(moves.lua(_m(), LAYOUT, {6}, tip))
    assert body[-1] == "tip = { { 46, 42 }, { 47, 42 }, { 48, 43 } },"
    assert not any(line.startswith("tip") for line in _body(moves.lua(_m(), LAYOUT, {6})))


def test_graph_makes_the_same_module():
    m = _m()
    again = manifest.loads(manifest.dumps(m))
    assert again.behaviour.blocks and moves.lua(again, LAYOUT, {6}) == moves.lua(m, LAYOUT, {6})


def test_rules_follow_the_canvas():
    m = _m()
    assert [r.play for r in behaviour.compile(m)] == ["stamp", "lunge"]
    m.behaviour.blocks["b1"].at = (0.0, 9999.0)
    text = moves.lua(m, LAYOUT, {6})
    assert _body(text)[-2].startswith('{ play = "lunge"')
    assert _body(text)[-1].startswith('{ play = "stamp"')


def test_curve_is_the_turns_module_entry():
    """The keys an own move turns by are the turns module's for its entry."""
    turns = layout.turns_lua(_m(), LAYOUT)
    assert f'[46] = "{moves.curve(_m(), "stamp", LAYOUT)}"' in turns
    assert moves.curve(_m(), "dash", LAYOUT) is None  # turns toward the hunter instead


def test_hub():
    assert moves.hub(75) == (0, 2) and moves.hub(1) is None
    m = manifest.loads(HEAD.replace("host_species = 75", "host_species = 1") + OWN)
    with pytest.raises(ManifestError, match="moves.stamp: no carrier, and none is known"):
        moves.check(m, LAYOUT)
    assert moves.carrier(m, "spin") == (0, 1)


@pytest.mark.parametrize(
    ("extra", "lay", "known", "why"),
    [
        (
            OWN,
            layout.Layout({20: 20, 9: 9}),
            None,
            "moves.stamp: clip 'stamp' has no executor entry",
        ),
        (OWN, LAYOUT, {7}, r"moves.stamp.attack\[0\]: host species 75 has no attack record 6"),
        (
            OWN.replace("frame = 56\nend = 80", "frame = 228\nend = 240"),
            LAYOUT,
            None,
            "frame 228 is past the clip's 228",
        ),
        (
            OWN,
            layout.Layout(LAYOUT.entries, turns={46: travel.Turn(600, (0,) * 301, 0, None)}),
            None,
            "the clip's turn has 301 keys, a move holds 256",
        ),
        (
            OWN
            + "[[rule]]\non = 'flinch'\nplay = 'stamp'\n[[rule]]\non = 'flinch'\nplay = 'spin'\n",
            LAYOUT,
            None,
            "ride 2 carriers",
        ),
    ],
)
def test_check(extra, lay, known, why):
    with pytest.raises(ManifestError, match=why):
        moves.lua(_m(extra), lay, known)


def test_pool():
    lay = layout.Layout(LAYOUT.entries, turns={46: travel.Turn(500, (0,) * 251, 0, None)})
    many = "".join(f"[moves.s{i}]\nclip = 'stamp'\n" for i in range(9))
    with pytest.raises(ManifestError, match="need 2259 keys, the framework holds 2048"):
        moves.check(_m(many), lay)


def test_manifest_limits():
    n = manifest.OWN_MOVES + 1
    many = "".join(f"[moves.o{i}]\nanim = {i}\n" for i in range(n))
    with pytest.raises(ManifestError, match=f"{n} own moves, the framework"):
        _m(many)


def test_mode_is_a_pairs():
    m = _m(OWN + "[[rule]]\nplay = 'spin'\nfrom_main = [1]\nmode = 1\n")
    with pytest.raises(ManifestError, match="mode is a pair's"):
        behaviour.compile(m)
    assert "mode is a pair's" in moves.problems(m, LAYOUT, {6})[0]


def test_zinogre(data):
    m = manifest.load(PORTS / "zinogre.toml")
    b = build.build(m, data)
    text = moves.lua(m, b.layout, moves.records(data.fu, 75), b.tip)
    assert "  tip = { { 46, 42 }, { 47, 42 }, { 48, 43 }, { 49, 44 }, { 50, 45 } },\n" in text
    own = {n: moves.entry(m, n, b.layout) for n, mv in m.moves.items() if mv.own}
    assert own == {
        "stamp": 46,
        "dash": 20,
        "dash_stop": 21,
        "turn_left_90": 9,
        "flinch_head": 101,
        "break_howl": 112,
        "notice_howl": 2,
        "topple_left": 103,
        "topple_left_2": 118,
        "topple_left_3": 108,
        "topple_right": 104,
        "topple_right_2": 117,
        "topple_right_3": 107,
        "charge_up": 18,
    }
    assert b.layout.frames[46] == 228 and 6 in moves.records(data.fu, 75)
    assert f'curve = "{b.layout.turns[46].lua()}"' in text
    stamp = 'stamp = { entry = 46, clip = "stamp_right_claw", attacks = { { 56, 6, 80 } }'
    assert stamp + ", carrier = { 0, 2 }" in text
    assert 'after = "dash_stop"' in text and 'turn = "fixed", total = 90.0, frames = 50' in text
    assert '{ play = "stamp", from = "dash", min_frames = 10' in text
    assert '{ play = "flinch_head", on = "part_broken", part = 0' in text
    assert '{ play = "topple_left", on = "part_broken", part = 4' in text
    assert '{ no_play = true, on = "flinch", effects = { { "var_add", 0, 1 } }' in text
    assert 'conds = { { "var_at_least", 1, 1 }, { "var_at_least", 0, 2 } }, ' in text
    assert 'effects = { { "var_set", 0, 0 }, { "enrage", 0, 0 } }' in text and "signal = 0" in text
    assert "  sever_below = 50,\n  tip = " in text and "natural_rage" not in text
    assert '{ no_play = true, on = "combat_entered", effects = { { "var_set", 1, 1 } }' in text
    assert 'conds = { { "var_at_least", 1, 1 }, { "hp_below", 0, 30 } }' in text
    assert (
        "  vars = { flinches = 0, in_combat = 1 },\n  signals = { roar = 0 },\n  sever_below"
        in text
    )
    assert '{ play = "notice_howl", on = "noticed", force = true' in text
    assert "length = 78, steer = { walls = false" in text and 'after = "topple_left_2"' in text


def test_lua_key():
    assert [moves.lua_key(k) for k in ("stamp", "end", "a b", "repeat_")] == [
        "stamp",
        '["end"]',
        '["a b"]',
        "repeat_",
    ]


def test_events_and_force_reach_the_module():
    extra = OWN + "[[rule]]\non = 'flinch'\npart = 0\nplay = 'stamp'\ncount = 3\n"
    extra += "[[rule]]\non = 'noticed'\nplay = 'stamp'\nforce = true\n"
    body = _body(moves.lua(_m(extra), LAYOUT))
    assert body[-2:] == [
        '{ play = "stamp", on = "flinch", part = 0, count = 3 },',
        '{ play = "stamp", on = "noticed", force = true },',
    ]


def _board() -> manifest.Manifest:
    def blk(kind, y, nxt=(), play=(), **params):
        return behaviour.Block(kind, (0.0, y), params, list(nxt), list(play))

    m = _m()
    m.behaviour = behaviour.Behaviour(
        {
            "b1": blk("on_flinch", 0, ["b2"]),
            "b2": blk("counter_add", 0, counter="hits", by=2),
            "b3": blk("idle", 100, ["b4"]),
            "b4": blk("counter_is", 100, ["b5"], counter="hits", value=3),
            "b5": blk("flag_set", 100, play=["stamp"], flag="end"),
            "b6": blk("on_signal", 200, ["b7"], name="roar"),
            "b7": blk("hunter_side", 200, ["b8"], sides=["left", "behind"]),
            "b8": blk("monster_hp", 200, play=["dash"], lo=10, hi=60),
        }
    )
    return m


def test_the_board_reaches_the_module():
    body = _body(moves.lua(_board(), LAYOUT, {6}))
    assert body[-6:] == [
        "rules = {",
        '{ no_play = true, on = "flinch", effects = { { "var_add", 1, 2 } } },',
        '{ play = "stamp", from_main = { 0 }, conds = { { "var_at_least", 1, 3 } }, '
        'effects = { { "var_set", 0, 1 } } },',
        '{ play = "dash", signal = 0, conds = { { "side", 10, 0 }, { "hp_at_least", 0, 10 }, '
        '{ "hp_below", 0, 60 } } },',
        'vars = { ["end"] = 0, hits = 1 },',
        "signals = { roar = 0 },",
    ]


def test_the_gate_and_natural_rage_reach_the_module():
    m = _board()
    m.parts["tail"] = manifest.Part(3, severable=True, sever_below=50)
    m.behaviour.natural_rage = False
    body = _body(moves.lua(m, LAYOUT, {6}))
    assert body[-2:] == ["sever_below = 50,", "natural_rage = false,"]
    assert moves.sever_below(m) == 50 and moves.sever_below(_m()) is None


def test_the_defaults_leave_both_out():
    m = _m()
    m.parts["tail"] = manifest.Part(3, severable=True)
    body = _body(moves.lua(m, LAYOUT, {6}))
    assert not any(line.startswith(("sever_below", "natural_rage")) for line in body)


def test_a_module_without_the_board_has_no_tables():
    body = _body(moves.lua(_m(), LAYOUT, {6}))
    assert not any(line.startswith(("vars", "signals")) for line in body)
    assert not any("conds" in line or "effects" in line or "no_play" in line for line in body)


def test_the_board_is_checked_with_the_rest():
    m = _board()
    for i in range(behaviour.BOARD_SIGNALS):
        name = f"s{i}"
        m.behaviour.blocks[name] = behaviour.Block(
            "on_signal", (0.0, 400.0 + i), {"name": name}, [], ["dash"]
        )
    n = behaviour.BOARD_SIGNALS + 1
    assert moves.problems(m, LAYOUT, {6}) == [f"behaviour: {n} signals, the board holds 16"]


def test_problems_lists_every_one():
    bad = OWN.replace("frame = 56\nend = 80", "frame = 228\nend = 240")
    found = moves.problems(_m(bad), layout.Layout({20: 20, 9: 9}), {7})
    assert found == ["moves.stamp: clip 'stamp' has no executor entry in this build"]
    found = moves.problems(_m(bad), LAYOUT, {7})
    assert [p.split(":")[0] for p in found] == ["moves.stamp.attack[0]"] * 2
    assert moves.problems(_m(), LAYOUT, {6}) == []
