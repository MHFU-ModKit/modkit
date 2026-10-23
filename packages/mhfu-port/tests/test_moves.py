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
        'stamp = { entry = 46, slot = 2, clip = "stamp", attacks = { { 56, 6, 80 } }, '
        'carrier = { 0, 2 }, steer = { walls = false, curve = "000020004000" }, after = "dash" },',
        'dash = { entry = 20, slot = 0, clip = "dash", carrier = { 0, 2 }, length = 40, '
        'host_attacks = true, steer = { walls = true, dir = 180.0, turn = "hunter" }, '
        'after = "lunge" },',
        "spin = { entry = 9, slot = 1, carrier = { 0, 1 }, "
        'steer = { walls = true, turn = "fixed", total = 90.0, frames = 50 } },',
        "rules = {",
        '{ play = "stamp", from_main = { 0 }, min_frames = 30, dist = { 0.0, 700.0 }, '
        "cooldown = 300 },",
        '{ play = "lunge", from = "dash", receding = true, count = 2 },',
    ]


SPAN = OWN.replace('after = "dash"\n', 'after = "dash"\nstart = 24\nstop = 86\n', 1)
"""The stamp playing its clip from frame 24 to 86, its attack at 56 to 80 inside."""


def test_start_and_stop_reach_the_module():
    text = moves.lua(_m(SPAN), LAYOUT, {6})
    assert "carrier = { 0, 2 }, start = 24, stop = 86, steer = { walls = false," in text
    assert " start = " not in moves.lua(_m(), LAYOUT, {6})


def test_a_derived_moves_span_is_checked_here():
    """Derived moves never go through the manifest's load: `problems` is what refuses them."""
    m = _m(SPAN)
    mv = m.moves["stamp"]
    assert moves.problems(m, LAYOUT, {6}) == []
    mv.start, mv.stop = 90, 80
    found = moves.problems(m, LAYOUT, {6})
    assert found == [
        "moves.stamp: stop 80 is not after start 90",
        "moves.stamp.attack[0]: frame 56 is before start 90",
    ]
    mv.start, mv.stop = 300, 0
    assert moves.problems(m, LAYOUT, {6}) == [
        "moves.stamp: start 300 is past the clip's 228 frames",
        "moves.stamp.attack[0]: frame 56 is before start 300",
    ]
    with pytest.raises(ManifestError, match="start 300 is past"):
        moves.check(m, LAYOUT, {6})


def test_steer_window():
    hunter = OWN.replace(
        'turn = "hunter"\ndir = 180.0', 'turn = "hunter"\nrate = 546\nwindow = [10, 40]'
    )
    text = moves.lua(_m(hunter), LAYOUT, {6})
    assert 'steer = { walls = true, turn = "hunter", rate = 546, window = { 10, 40 } }' in text
    assert "window" not in moves.lua(_m(), LAYOUT, {6})


JUMP = (
    OWN
    + """
[moves.jump]
anim = 9
carrier = [0, 1]
travel = [[0, 0], [-2.5, 40.25], [3, 100]]
[moves.jump.lunge]
window = [0, 32]
near = 400
span = 1800
base = -700
range = 1800
[moves.jump.steer]
turn = "fixed"
angle = 90.0
window = [10, 40]
"""
)


def test_travel_lunge_and_fixed_window():
    text = moves.lua(_m(JUMP), LAYOUT, {6})
    assert (
        'steer = { walls = true, turn = "fixed", total = 90.0, window = { 10, 40 }, '
        'travel = "00000000fff600a1000c0190", lunge = { window = { 0, 32 }, near = 400.0, '
        "span = 1800.0, base = -700.0, range = 1800.0 } } }," in text
    )
    plain = moves.lua(_m(), LAYOUT, {6})
    assert " travel = " not in plain and "lunge = { window" not in plain
    assert "total = 90.0, frames = 50 }" in plain  # a fixed turn without a window: its frames


def test_travel_the_move_player_can_carry_is_not_refused():
    m = _m(JUMP)
    assert moves.problems(m, LAYOUT, {6}) == []
    m.moves["jump"].travel = [(0.0, 0.0)] * 51  # frames 0, 2 .. 100: the clip's last
    assert moves.problems(m, LAYOUT, {6}) == []


def _refused(**fields: object) -> list[str]:
    m = _m(JUMP)
    for k, v in fields.items():
        setattr(m.moves["jump"], k, v)
    return moves.problems(m, LAYOUT, {6})


def test_travel_past_the_clip_is_refused():
    found = _refused(travel=[(0.0, 0.0)] * 52)
    assert found == ["moves.jump.travel: its 52 samples reach frame 102, past the clip's 100"]
    found = _refused(travel=[(0.0, 0.0)] * 40, start=24)
    assert "moves.jump.travel: its 40 samples reach frame 102, past the clip's 100" in found


def test_travel_the_move_player_cannot_hold_is_refused():
    n = moves.SAMPLES + 1
    big = _refused(travel=[(0.0, 0.0)] * n)
    assert f"moves.jump.travel: {n} travel samples, a move holds {moves.SAMPLES}" in big
    assert "a travel sample holds" in _refused(travel=[(0.0, 9000.0)])[0]


def test_a_lunge_outside_its_moves_span_is_refused():
    lunge = _m(JUMP).moves["jump"].lunge
    assert lunge is not None
    for window, why in [((0, 101), "(0, 101) is outside [0, 100]"), ((5, 5), "not 0 <= lo < hi")]:
        lunge.window = window
        assert why in _refused(lunge=lunge)[0]
    lunge.window = (0, 32)
    assert "(0, 32) is outside [0, 30]" in _refused(lunge=lunge, stop=30)[0]
    assert "(0, 32) is outside [24, 100]" in _refused(lunge=lunge, start=24)[0]
    for field, value in (("span", 0.0), ("near", -1.0), ("base", float("inf"))):
        lunge = _m(JUMP).moves["jump"].lunge
        assert lunge is not None
        setattr(lunge, field, value)
        assert "span above 0" in _refused(lunge=lunge)[0]


def test_a_fixed_window_outside_its_moves_span_is_refused():
    m = _m(JUMP)
    mv = m.moves["jump"]
    mv.stop = 30
    assert moves.problems(m, LAYOUT, {6}) == [
        "moves.jump.lunge: window (0, 32) is outside [0, 30]",
        "moves.jump.steer: fixed window (10, 40) is outside [0, 30]",
    ]
    mv.stop, mv.start = 0, 12
    assert "fixed window (10, 40) is outside [12, 100]" in moves.problems(m, LAYOUT, {6})[1]
    mv.start = 0
    mv.steer.window = (10, 101)
    assert "(10, 101) is outside [0, 100]" in moves.problems(m, LAYOUT, {6})[0]


def test_hunter_windows_are_not_held_to_the_span():
    """Only a fixed turn's window must lie inside [start, stop]: the hunter turn's is as before."""
    m = _m(JUMP)
    mv = m.moves["jump"]
    mv.steer.turn, mv.steer.window, mv.steer.angle, mv.stop = "hunter", (10, 400), None, 30
    assert moves.problems(m, LAYOUT, {6}) == ["moves.jump.lunge: window (0, 32) is outside [0, 30]"]


def test_pools_of_samples_and_lunges():
    lay = layout.Layout({}, frames={9: 400})
    k = moves.SAMPLE_POOL // moves.SAMPLES + 1
    path = [[0, 0]] * moves.SAMPLES
    many = "".join(f"[moves.t{i}]\nanim = 9\ncarrier = [0, 1]\ntravel = {path}\n" for i in range(k))
    assert moves.problems(_m(many), lay, {6}) == [
        f"moves: the own moves' travel needs {moves.SAMPLES * k} samples, the framework holds "
        f"{moves.SAMPLE_POOL}"
    ]
    n = moves.LUNGES + 1
    lunging = "".join(
        f"[moves.l{i}]\nanim = 9\ncarrier = [0, 1]\n[moves.l{i}.lunge]\nwindow = [0, 9]\n"
        "near = 1\nspan = 1\nbase = 0\nrange = 1\n"
        for i in range(n)
    )
    assert moves.problems(_m(lunging), lay, {6}) == [
        f"moves: {n} lunges, the framework holds {moves.LUNGES}"
    ]


def test_tip():
    tip = Tip(((46, 42), (47, 42), (48, 43)), (0.0, 0.0, -705.0))
    body = _body(moves.lua(_m(), LAYOUT, {6}, tip))
    assert body[-1] == "tip = { { 46, 42 }, { 47, 42 }, { 48, 43 } },"
    assert not any(line.startswith("tip") for line in _body(moves.lua(_m(), LAYOUT, {6})))


def test_fatigue():
    table = {
        "max": 1000,
        "starts": [400, 600],
        "tired": 2250,
        "refill": 500,
        "low_hp": 15,
        "calm": True,
        "pairs": [[3, 3, 5]],
        "own": {"stamp": 15},
    }
    text = moves.lua(_m(), LAYOUT, {6}, None, table)
    assert _body(text)[-1] == (
        "fatigue = { max = 1000, starts = { 400, 600 }, tired = 2250, refill = 500, low_hp = 15, "
        "calm = true, pairs = { { 3, 3, 5 } }, own = { stamp = 15 } },"
    )
    plain = moves.lua(_m(), LAYOUT, {6})
    assert not any(line.startswith("fatigue") for line in _body(plain))
    assert moves.build_of(text) != moves.build_of(plain)


def test_charge():
    table = {
        "ups": {"stamp": [25, 35, 40, 55]},
        "transform": "dash",
        "chip_gauge": 5,
        "chip_durability": 4,
        "cooldown_up": 420,
        "cooldown_out": 900,
        "discharge": [[4, 16], [4, 30]],
    }
    text = moves.lua(_m(), LAYOUT, {6}, None, None, table)
    assert _body(text)[-1] == (
        'charge = { ups = { stamp = { 25, 35, 40, 55 } }, transform = "dash", chip_gauge = 5, '
        "chip_durability = 4, cooldown_up = 420, cooldown_out = 900, "
        "discharge = { { 4, 16 }, { 4, 30 } } },"
    )
    plain = moves.lua(_m(), LAYOUT, {6})
    assert not any(line.startswith("charge") for line in _body(plain))
    assert moves.build_of(text) != moves.build_of(plain)
    both = moves.lua(_m(), LAYOUT, {6}, None, {"max": 1000}, table)
    assert [line.split(" = ")[0] for line in _body(both)[-2:]] == ["fatigue", "charge"]


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


def test_slots_are_the_sorted_names():
    m = _m()
    assert moves.slots(m) == {"dash": 0, "spin": 1, "stamp": 2}
    m.moves["alpha"] = manifest.Move(anim=1)
    assert moves.slots(m) == {"alpha": 0, "dash": 1, "spin": 2, "stamp": 3}  # a pair move has none


def test_slots_stop_at_the_registry():
    m = _m()
    for i in range(moves.SLOTS):
        m.moves[f"o{i:02}"] = manifest.Move(anim=i)
    assert len(moves.slots(m)) == moves.SLOTS and max(moves.slots(m).values()) == moves.SLOTS - 1
    assert (
        f"{moves.SLOTS + 3} own moves, the framework holds {moves.SLOTS}"
        in moves.problems(m, LAYOUT, {6})[0]
    )


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
            OWN.replace("anim = 9\n", "anim = 9\nstart = 100\n"),
            LAYOUT,
            None,
            "spin: start 100 is past",
        ),
        (SPAN.replace("stop = 86", "stop = 229"), LAYOUT, None, "stop 229 is past the clip's 228"),
        (SPAN.replace("stop = 86", "stop = 24"), LAYOUT, None, "stop 24 is not after start 24"),
        (
            SPAN.replace("start = 24", "start = 60"),
            LAYOUT,
            None,
            r"attack\[0\]: frame 56 is before",
        ),
        (SPAN.replace("stop = 86", "stop = 70"), LAYOUT, None, "lasts to 80, past stop 70"),
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
    n = moves.POOL // 251 + 1
    many = "".join(f"[moves.s{i}]\nclip = 'stamp'\n" for i in range(n))
    with pytest.raises(
        ManifestError, match=f"need {251 * n} keys, the framework holds {moves.POOL}"
    ):
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
    at = moves.slots(m)
    assert sorted(at.values()) == list(range(len(own))) and at["stamp"] == 6
    stamp = 'stamp = { entry = 46, slot = 6, clip = "stamp_right_claw", attacks = { { 56, 6, 80 } }'
    assert stamp + ", carrier = { 0, 2 }" in text
    assert 'turn = "fixed", total = 90.0, frames = 50' in text and "claim" not in text
    dash = next(line for line in text.splitlines() if line.strip().startswith("dash = {"))
    assert "after" not in dash, "the brain's pairs end the dash themselves"
    assert '{ play = "flinch_head", on = "part_broken", part = 0' in text
    assert '{ play = "topple_left", on = "part_broken", part = 4' in text
    assert "signal = 0" in text and "enrage" not in text and "var_add" not in text
    assert "  sever_below = 50,\n  tip = " in text and "natural_rage" not in text
    assert '{ no_play = true, on = "combat_entered", effects = { { "var_set", 0, 1 } }' in text
    assert "  vars = { in_combat = 0 },\n  signals = { roar = 0 },\n  sever_below" in text
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
