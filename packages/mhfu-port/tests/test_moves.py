# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

import pytest
from mhfu_port import build, layout, manifest, moves, travel
from mhfu_port.manifest import ManifestError

PORTS = Path(__file__).parents[3] / "ports"

HEAD = """
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
    lines = [line.strip() for line in text.splitlines() if not line.startswith("--")]
    return [line for line in lines if line not in ("return {", "},", "}", "")]


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


def test_curve_is_the_clips_module_turn():
    """The keys an own move turns by are the clips module's `_turns` for its entry."""
    clips = layout.lua(_m(), LAYOUT)
    assert f'[46] = "{moves.curve(_m(), "stamp", LAYOUT)}"' in clips
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
        (OWN + "[[rule]]\non = 'flinch'\nplay = 'stamp'\n", LAYOUT, None, "`on` does not reach"),
        (OWN.replace("[moves.stamp]\n", "[moves.stamp]\neager = true\n"), LAYOUT, None, "`eager`"),
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


@pytest.mark.parametrize(
    ("extra", "why"),
    [
        ("".join(f"[moves.o{i}]\nanim = {i}\n" for i in range(17)), "17 own moves, the framework"),
        (OWN + "[[rule]]\nplay = 'spin'\nfrom_main = [1]\nmode = 1\n", "mode is a pair's"),
    ],
)
def test_manifest_limits(extra, why):
    with pytest.raises(ManifestError, match=why):
        _m(extra)


def test_zinogre(data):
    m = manifest.load(PORTS / "zinogre.toml")
    b = build.build(m, data)
    text = moves.lua(m, b.layout, moves.records(data.fu, 75))
    own = {n: moves.entry(m, n, b.layout) for n, mv in m.moves.items() if mv.own}
    assert own == {"stamp": 46, "dash": 20, "dash_stop": 21, "turn_left_90": 9}
    assert b.layout.frames[46] == 228 and 6 in moves.records(data.fu, 75)
    assert f'curve = "{b.layout.turns[46].lua()}"' in text
    stamp = 'stamp = { entry = 46, clip = "stamp_right_claw", attacks = { { 56, 6, 80 } }'
    assert stamp + ", carrier = { 0, 2 }" in text
    assert 'after = "dash_stop"' in text and 'turn = "fixed", total = 90.0, frames = 50' in text
    assert '{ play = "stamp", from = "dash", min_frames = 10' in text


def test_lua_key():
    assert [moves.lua_key(k) for k in ("stamp", "end", "a b", "repeat_")] == [
        "stamp",
        '["end"]',
        '["a b"]',
        "repeat_",
    ]
