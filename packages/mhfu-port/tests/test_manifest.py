# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

import pytest
from mhfu import addresses
from mhfu_port import manifest as M
from mhfu_port.manifest import ManifestError

PORTS = Path(__file__).parents[3] / "ports"

MINIMAL = """
[port]
name = "x"
host_species = 75
pac = "x.bin"

[source]
model = 100
"""

FULL = """
schema = 1

[port]
name = "x"
host_species = 75
pac = "x.bin"
replace = [77]

[source]
model = 100
em_id = 40
geo = 7

[build]
source_skeleton = true
skin = "source"
ground_lift = 1.5
animated = 46
skip_bones = []
drop_joints = [3, 4]

[clips.run]
slot = 6
frames = 100
loop = true
impact_frame = 62
turn = -66.5
label = "a \\"quoted\\" label\\nwith a newline"
labelled_build = "x.bin@0"

[clips.stop]
slot = 21

[moves.run]
main = 1
sub = 4
clip = "run"
after = "stop"
hold_max = 12
claim = { main = [1, 0, 1], sub = 7 }

[moves.stop]
main = 0
sub = 3
clip = "stop"
latch = 2

[moves.raw]
main = 2
sub = 0
anim = 17
allow_unentered = true

[parts.head]
index = 1
hitzone_row = 2
severable = true

[[hurtbox]]
bone = 35
radius = 230
part = 0
hitzone_row = 6
shape = "capsule"
offset = [0, 0, 0]
to = [1.0, 2.0, 3.25]
flags = 0x101

[[hitzone]]
state = "normal"
rows = [[100, 255, 0, 0, 0, 0, 0, 0, 0, 0], [0,0,0,0,0,0,0,0,0,0], [0,0,0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0,0,0], [0,0,0,0,0,0,0,0,0,0], [0,0,0,0,0,0,0,0,0,0],
        [0,0,0,0,0,0,0,0,0,1]]

[[hitbox]]
set = 2
bone = 127
radius = 600.0
offset = [-550.0, -50.0, 150.0]

[[attack]]
id = 6
power = 8
element = 0x10

[[effect]]
move = "run"
frame = 4
id = 42
bone = 33

[[rule]]
from = "run"
min_frames = 15
dist = [250, 1000]
receding = true
play = "stop"
cooldown = 30
count = 2

[[rule]]
from_main = [1]
play = "stop"
"""


def _with(extra: str) -> str:
    return MINIMAL + extra


def test_minimal():
    m = M.loads(MINIMAL)
    assert m.build == M.Build()
    assert (m.source.game, m.source.geo, m.source.anim) == ("mhp3rd", 101, 102)
    assert M.dumps(m).startswith("schema = 1\n")


def test_derived():
    port = M.loads(MINIMAL).port
    assert (port.host_frame, port.fid, port.orig) == (6185, 6186, "file_06185.bin.orig")


def test_source_override():
    s = M.loads(FULL).source
    assert (s.geo, s.anim) == (7, 102)
    s.model = 200
    assert (s.geo, s.anim) == (7, 202)


def test_round_trip():
    m = M.loads(FULL)
    assert M.loads(M.dumps(m)) == m
    assert m.clips["run"].label == 'a "quoted" label\nwith a newline'
    assert (m.clips["run"].turn, m.clips["stop"].turn) == (-66.5, None)
    assert m.hurtboxes[0].radius == 230.0 and m.hurtboxes[0].offset == [0.0, 0.0, 0.0]
    assert m.rules[0].dist == (250.0, 1000.0)
    assert m.moves["run"].claim == M.Claim([0, 1], sub=7)


def test_defaults_left_out():
    text = M.dumps(M.loads(_with("[moves.m]\nmain = 0\nsub = 0\nanim = 1\nlatch = 1\n")))
    assert "latch" not in text and "[build]" not in text


def test_claim_shorthand():
    for claim in ("1", "{ main = 1 }", "{ main = [1] }"):
        m = M.loads(_with(f"[moves.m]\nmain = 1\nsub = 4\nanim = 17\nclaim = {claim}\n"))
        assert m.moves["m"].claim == M.Claim([1])
        assert m.moves["m"].claim.mask == 2


@pytest.mark.parametrize(
    "extra",
    [
        "typo = 1\n",
        "[build]\nhops = 1\n",
        "[build]\nreweight_undriven = false\n",
        "[clips.a]\nslot = 1\nlabl = ''\n",
        "[[hurtbox]]\nbone = 1\nradius = 1.0\nsize = 2\n",
    ],
)
def test_unknown_key(extra):
    with pytest.raises(ManifestError, match="unknown key"):
        M.loads(_with(extra))


@pytest.mark.parametrize("key", ["host_frame = 6185", "fid = 6186", 'orig = "a"'])
def test_derived_key_refused(key):
    with pytest.raises(ManifestError, match="unknown key"):
        M.loads(MINIMAL.replace('pac = "x.bin"', f'pac = "x.bin"\n{key}'))


@pytest.mark.parametrize(
    "extra",
    [
        "[build]\nnb = true\n",
        "[build]\nnb = 3.0\n",
        "[build]\nground_lift = '1'\n",
        "[build]\nsource_skeleton = 1\n",
        "[build]\nskin = 'guess'\n",
        "[build]\ndrop_joints = 3\n",
        "[clips.a]\nslot = '1'\n",
        "[[hurtbox]]\nbone = 1\nradius = 1.0\nshape = 'cube'\n",
        "[[rule]]\nfrom_main = [1]\nplay = 'm'\ndist = [1]\n",
    ],
)
def test_bad_type(extra):
    with pytest.raises(ManifestError, match="expected"):
        M.loads(_with(extra))


def test_bad_section():
    with pytest.raises(ManifestError, match="clips: expected a table"):
        M.loads("clips = 1\n" + MINIMAL)


def test_missing():
    with pytest.raises(ManifestError, match="port.pac: missing"):
        M.loads(MINIMAL.replace('pac = "x.bin"', ""))
    with pytest.raises(ManifestError, match="clips.a: needs a source or a slot"):
        M.loads(_with("[clips.a]\nframes = 1\n"))


def test_schema():
    with pytest.raises(ManifestError, match="schema"):
        M.loads("schema = 2\n" + MINIMAL)


def test_bad_toml_names_path():
    with pytest.raises(ManifestError, match="p.toml"):
        M.loads("[port", "p.toml")


MOVE = "[moves.m]\nmain = 1\nsub = 4\nanim = 17\n"
RULE = "[[rule]]\nfrom_main = [1]\nplay = 'm'\n"
OWN = "[moves.o]\nanim = 46\n"
STAMP = (
    OWN
    + """carrier = [0, 2]
length = 90
host_attacks = true
[[moves.o.attack]]
id = 6
frame = 56
end = 80
[moves.o.steer]
turn = "fixed"
angle = -90.0
frames = 20
walls = false
"""
)


def test_rule_on_an_event():
    m = M.loads(_with(OWN + "eager = true\n[[rule]]\non = 'flinch'\npart = 0\nplay = 'o'\n"))
    assert (m.rules[0].on, m.rules[0].part, m.moves["o"].eager) == ("flinch", 0, True)
    assert M.loads(M.dumps(m)) == m


def test_own_move():
    m = M.loads(_with(STAMP))
    o = m.moves["o"]
    assert o.own and o.pair is None and m.moves == M.loads(M.dumps(m)).moves
    assert o.attacks == [M.AttackWindow(6, 56, 80)] and o.steer.angle == -90.0
    assert not M.loads(_with(MOVE)).moves["m"].own
    assert "steer" not in M.dumps(M.loads(_with(OWN)))


@pytest.mark.parametrize(
    ("extra", "why"),
    [
        ("[moves.m]\nmain = 1\nsub = 4\n", "needs clip or anim"),
        ("[moves.m]\nmain = 1\nsub = 4\nclip = 'nope'\n", "not in clips"),
        (MOVE + "after = 'nope'\n", "not in moves"),
        (MOVE + "after = 'm'\n", "itself"),
        (MOVE + "hold_max = 0\n", "hold_max"),
        (MOVE + "claim = 8\n", "not a main state"),
        (MOVE + "claim = 1\n[moves.n]\nmain = 1\nsub = 5\nanim = 1\nclaim = 1\n", "claims"),
        ("[clips.a]\nslot = 1\n[clips.b]\nslot = 1\n", "slot 1"),
        ("[parts.p]\nindex = 8\n", "not a part"),
        ("[[hurtbox]]\nbone = 1\nradius = 1.0\npart = 8\n", "not a part"),
        ("[[hurtbox]]\nbone = 1\nradius = 1.0\nhitzone_row = 7\n", "not a row"),
        ("[[hurtbox]]\nbone = 1\nradius = 1.0\noffset = [0, 0]\n", "x, y, z"),
        ("[[hitbox]]\nbone = 1\nradius = 1.0\nset = -1\n", "set"),
        ("[[hitzone]]\nstate = 's'\nrows = [[0]]\n", "rows"),
        ("[[attack]]\nid = 1\npower = 256\n", "byte"),
        ("[[attack]]\nid = 1\n[[attack]]\nid = 1\n", "twice"),
        ("[[effect]]\nmove = 'm'\nframe = 1\nid = 1\nbone = 1\n", "not in moves"),
        (MOVE + "[[rule]]\nplay = 'm'\n", "needs from"),
        (MOVE + "[[rule]]\nfrom_main = [1]\nplay = 'n'\n", "not in moves"),
        (MOVE + "[[rule]]\nfrom = 'm'\nplay = 'm'\n", "same move"),
        (MOVE + "[[rule]]\nfrom_main = [9]\nplay = 'm'\n", "main state"),
        (MOVE + RULE + "dist = [5, 5]\n", "lo < hi"),
        (MOVE + RULE + "count = 0\n", "count"),
        (MOVE + RULE + "receding = true\nclosing = true\n", "and closing"),
        (MOVE + RULE * (M.SEAM_RULES + 1), f"holds {M.SEAM_RULES}"),
        ("[moves.m]\nmain = 1\nanim = 1\n", "both main and sub"),
        (MOVE + "length = 9\n", "for an own move"),
        (MOVE + "[[moves.m.attack]]\nid = 6\nframe = 1\n", "for an own move"),
        (OWN + "hold_max = 9\n", "no claim or hold_max"),
        (OWN + "[[moves.o.attack]]\nid = 6\nframe = 1\n" * 5, "holds 4 attacks"),
        (OWN + "[[moves.o.attack]]\nid = 6\nframe = 9\nend = 9\n", "after frame"),
        (OWN + "length = 0\n", "length"),
        (OWN + "carrier = [9, 0]\n", "carrier main"),
        (OWN + "[moves.o.steer]\nturn = 'fixed'\n", "angle goes with"),
        (OWN + "[moves.o.steer]\nangle = 90.0\n", "angle goes with"),
        (OWN + "[moves.o.steer]\nturn = 'spin'\n", "one of"),
        (OWN + "[moves.o.steer]\nturn = 'hunter'\nrate = 0\n", "rate"),
        (OWN + "[[rule]]\non = 'roared'\nplay = 'o'\n", "one of"),
        (OWN + "[[rule]]\non = 'noticed'\npart = 0\nplay = 'o'\n", "part goes with"),
        (OWN + "[[rule]]\non = 'flinch'\npart = 8\nplay = 'o'\n", "not a part"),
        (MOVE + "[[rule]]\non = 'flinch'\nplay = 'm'\n", "flinch plays an own move"),
    ],
)
def test_invalid(extra, why):
    with pytest.raises(ManifestError, match=why):
        M.loads(_with(extra))


def test_rename_clip():
    m = M.loads(FULL)
    m.rename_clip("run", "dash")
    assert list(m.clips) == ["dash", "stop"]
    assert m.moves["run"].clip == "dash"
    M.check(m)
    with pytest.raises(ManifestError):
        m.rename_clip("dash", "stop")


def test_check():
    m = M.loads(FULL)
    m.moves["stop"].clip = "gone"
    with pytest.raises(ManifestError, match="not in clips"):
        M.check(m)
    m = M.loads(FULL)
    m.build.nb = 3.5
    with pytest.raises(ManifestError, match="expected an integer"):
        M.check(m)


def test_save(tmp_path):
    p = tmp_path / "x.toml"
    p.write_text(FULL)
    m = M.load(p)
    assert m.path == p
    m.clips["run"].label = "edited"
    M.save(m)
    assert M.load(p) == m
    m.effects[0].move = "gone"
    with pytest.raises(ManifestError):
        M.save(m)
    assert M.load(p).effects[0].move == "run"
    with pytest.raises(ManifestError, match="no path"):
        M.save(M.loads(MINIMAL))


def test_discover(tmp_path):
    for name in ("b", "a"):
        (tmp_path / f"{name}.toml").write_text(MINIMAL.replace('"x"', f'"{name}"'))
    assert [m.port.name for m in M.discover(tmp_path)] == ["a", "b"]


@pytest.mark.parametrize("name", ["brute_tigrex", "zinogre"])
def test_ports(name):
    path = PORTS / f"{name}.toml"
    m = M.load(path)
    assert m.port.name == name and m.port.pac == f"{name}.bin"
    assert M.dumps(m) == path.read_text(encoding="utf-8")
    assert not m.hitzones


def test_rule_capacity_is_the_frameworks():
    assert M.SEAM_RULES == addresses.EM_CFG.RULES.count and M.SEAM_RULES >= 8
