# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

import pytest
from mhfu import addresses
from mhfu_port import behaviour as B
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

BODY = """
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
"""

RULES = """
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

GRAPH = """
[behaviour.blocks.b1]
kind = "played_for"
at = [0, 0]
frames = 15
next = ["b2"]

[behaviour.blocks.b2]
kind = "distance"
at = [220, 0]
lo = 250
hi = 1000
next = ["b3"]

[behaviour.blocks.b3]
kind = "hunter_moving"
at = [440, 0]
way = "away"
next = ["b4"]

[behaviour.blocks.b4]
kind = "cooldown"
at = [660, 0]
frames = 30
next = ["b5"]

[behaviour.blocks.b5]
kind = "limit"
at = [880, 0]
times = 2
play = ["stop"]

[behaviour.blocks.b6]
kind = "host_state"
at = [0, 120]
mains = [1]
play = ["stop"]
label = "any run"

[behaviour.moves.run]
at = [0, 0]
during = ["b1"]
"""

FULL = "schema = 2\n" + BODY + GRAPH
V1 = "schema = 1\n" + BODY + RULES


def _with(extra: str) -> str:
    return MINIMAL + extra


def _v1(extra: str) -> str:
    return "schema = 1\n" + MINIMAL + extra


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


def blk(i: str, kind: str, extra: str = "") -> str:
    return f"[behaviour.blocks.{i}]\nkind = '{kind}'\nat = [0, 0]\n{extra}"


def test_minimal():
    m = M.loads(MINIMAL)
    assert m.build == M.Build()
    assert (m.source.game, m.source.geo, m.source.anim) == ("mhp3rd", 101, 102)
    assert M.dumps(m).startswith("schema = 2\n") and "behaviour" not in M.dumps(m)


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
    assert B.compile(m)[0].dist == (250.0, 1000.0)
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
        blk("b1", "cooldown", "frames = '1'\n"),
        blk("b1", "force", "next = 'b2'\n"),
        blk("b1", "force").replace("[0, 0]", "[0, 0, 0]"),
        "[behaviour.moves.m]\nat = [0, 0]\nduring = 'b1'\n",
    ],
)
def test_bad_type(extra):
    with pytest.raises(ManifestError, match="expected"):
        M.loads(_with(MOVE + extra))


def test_bad_v1_type():
    with pytest.raises(ManifestError, match=r"rule\[0\].dist: expected 2 values"):
        M.loads(_v1("[[rule]]\nfrom_main = [1]\nplay = 'm'\ndist = [1]\n"))


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
        M.loads("schema = 3\n" + MINIMAL)
    with pytest.raises(ManifestError, match="behaviour: is for schema 2"):
        M.loads(_v1(FULL[FULL.index("[behaviour") :]))
    with pytest.raises(ManifestError, match="unknown key.*rule"):
        M.loads(_with(RULES))


def test_bad_toml_names_path():
    with pytest.raises(ManifestError, match="p.toml"):
        M.loads("[port", "p.toml")


def test_block_params_are_flat():
    m = M.loads(FULL)
    text = M.dumps(m)
    assert '[behaviour.blocks.b2]\nkind = "distance"' in text and "params" not in text
    assert m.behaviour.blocks["b2"].params == {"lo": 250, "hi": 1000}
    assert m.behaviour.blocks["b2"].at == (220.0, 0.0)
    assert M.loads(text) == m and "[[rule]]" not in text


def test_v1_loads_as_the_graph():
    old = M.loads(V1)
    assert B.compile(old) == B.compile(M.loads(FULL.replace('label = "any run"\n', "")))
    back = M.dumps(old)
    assert back.startswith("schema = 2\n") and "[[rule]]" not in back
    assert B.compile(M.loads(back)) == B.compile(old)


def test_own_move():
    m = M.loads(_with(STAMP))
    o = m.moves["o"]
    assert o.own and o.pair is None and m.moves == M.loads(M.dumps(m)).moves
    assert o.attacks == [M.AttackWindow(6, 56, 80)] and o.steer.angle == -90.0
    assert not M.loads(_with(MOVE)).moves["m"].own
    assert "steer" not in M.dumps(M.loads(_with(OWN)))


CUT = (
    "[clips.a]\nslot = 65\nsource = 7\nstart = 0\nframes = 2\n"
    "[clips.b]\nslot = 83\nsource = 7\nstart = 2\nframes = 9\n"
)


def test_cuts_share_a_source():
    m = M.loads(_with(CUT))
    assert [(c.id, c.cut) for c in m.clips.values()] == [(7, (0, 2)), (7, (2, 9))]
    assert M.loads(M.dumps(m)) == m and M.Clip(3, frames=5).cut is None


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
        ("[clips.a]\nslot = 1\nstart = 0\n", "start needs slot and frames"),
        ("[clips.a]\nsource = 1\nstart = 0\nframes = 2\n", "start needs slot and frames"),
        ("[clips.a]\nslot = 1\nstart = -1\nframes = 2\n", "start is 0 or more"),
        ("[clips.a]\nslot = 1\nstart = 0\nframes = 0\n", "frames is at least 1"),
        (CUT + "[clips.c]\nsource = 7\n", "clip 7 is placed by clips.b too"),
        ("[clips.c]\nsource = 7\n" + CUT, "clip 7 is placed by clips.c too"),
        ("[parts.p]\nindex = 8\n", "not a part"),
        ("[[hurtbox]]\nbone = 1\nradius = 1.0\npart = 8\n", "not a part"),
        ("[[hurtbox]]\nbone = 1\nradius = 1.0\nhitzone_row = 7\n", "not a row"),
        ("[[hurtbox]]\nbone = 1\nradius = 1.0\noffset = [0, 0]\n", "x, y, z"),
        ("[[hitbox]]\nbone = 1\nradius = 1.0\nset = -1\n", "set"),
        ("[[hitzone]]\nstate = 's'\nrows = [[0]]\n", "rows"),
        ("[[attack]]\nid = 1\npower = 256\n", "byte"),
        ("[[attack]]\nid = 1\n[[attack]]\nid = 1\n", "twice"),
        ("[[effect]]\nmove = 'm'\nframe = 1\nid = 1\nbone = 1\n", "not in moves"),
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
    ],
)
def test_invalid(extra, why):
    with pytest.raises(ManifestError, match=why):
        M.loads(_with(extra))


@pytest.mark.parametrize(
    ("extra", "why"),
    [
        (blk("b1", "nope"), "kind 'nope' is not one of"),
        (blk("B1", "force"), "id is a lowercase letter"),
        (blk("a-b", "force"), "id is a lowercase letter"),
        ("[behaviour.blocks.b1]\nkind = 'force'\n", "b1.at: missing"),
        (blk("b1", "force", "frames = 3\n"), "unknown param.*frames"),
        (blk("b1", "on_noticed", "part = 0\n"), "unknown param.*part"),
        (blk("b1", "cooldown"), "b1.frames: missing"),
        (blk("b1", "cooldown", "frames = 0\n"), "0 is under 1"),
        (blk("b1", "cooldown", "frames = true\n"), "expected an integer"),
        (blk("b1", "cooldown", "frames = 1.5\n"), "expected an integer"),
        (blk("b1", "mode", "mode = 256\n"), "256 is over 255"),
        (blk("b1", "distance", "lo = -1.0\n"), "-1.0 is under 0"),
        (blk("b1", "distance", "hi = 'far'\n"), "expected a number"),
        (blk("b1", "hunter_moving"), "b1.way: missing"),
        (blk("b1", "hunter_moving", "way = 'up'\n"), "expected one of 'away', 'closer'"),
        (blk("b1", "on_flinch", "part = 8\n"), "8 is not a part"),
        (blk("b1", "host_state"), "b1.mains: missing"),
        (blk("b1", "host_state", "mains = [8]\n"), "not a list of main states"),
        (blk("b1", "host_state", "mains = []\n"), "not a list of main states"),
        (blk("b1", "host_state", "mains = 1\n"), "not a list of main states"),
        (blk("b1", "force", "next = ['b2']\n"), "'b2' is not in blocks"),
        (blk("b1", "force", "play = ['nope']\n"), "'nope' is not in moves"),
        (blk("b1", "force", "play = ['m', 'm']\n"), "names one twice"),
        (blk("b1", "force", "next = ['b1']\n"), "behaviour.blocks.b1: next leads back"),
        (
            blk("b1", "force", "next = ['b2']\n") + blk("b2", "force", "next = ['b1']\n"),
            "next leads back",
        ),
        ("[behaviour.moves.nope]\nat = [0, 0]\n", "behaviour.moves.nope: is not in moves"),
        ("[behaviour.moves.m]\n", "behaviour.moves.m.at: missing"),
        ("[behaviour.moves.m]\nat = [0, 0]\nduring = ['b9']\n", "'b9' is not in blocks"),
    ],
)
def test_invalid_block(extra, why):
    with pytest.raises(ManifestError, match=why):
        M.loads(_with(MOVE + extra))


@pytest.mark.parametrize(
    ("extra", "why"),
    [
        ("[[rule]]\nplay = 'm'\n", r"rule\[0\]: needs from, from_main or on"),
        ("[[rule]]\nfrom = 'm'\nplay = 'n'\n", r"rule\[0\]: from 'm' alone has no block"),
        ("[[rule]]\nfrom_main = [1]\nplay = 'n'\n", "'n' is not in moves"),
        ("[[rule]]\nfrom_main = [9]\nplay = 'm'\n", "not a list of main states"),
        (RULE + "count = 0\n", "0 is under 1"),
        (RULE + "receding = true\nclosing = true\n", "cannot be receding and closing"),
        ("[[rule]]\non = 'roared'\nplay = 'm'\n", "on is one of"),
        ("[[rule]]\non = 'noticed'\npart = 0\nplay = 'm'\n", "part goes with"),
        ("[[rule]]\non = 'flinch'\npart = 8\nplay = 'm'\n", "8 is not a part"),
        ("[[rule]]\nfrom_main = [1]\n", "play: missing"),
        ("[[rule]]\nfrom_main = [1]\nplay = 'm'\ntypo = 1\n", "unknown key"),
    ],
)
def test_invalid_v1(extra, why):
    with pytest.raises(ManifestError, match=why):
        M.loads(_v1(MOVE + extra))


def test_at_is_two_numbers():
    m = M.loads(_with(MOVE + blk("b1", "force")))
    m.behaviour.blocks["b1"].at = (1.0,)  # type: ignore[assignment]
    with pytest.raises(ManifestError, match="b1.at: expected 2 numbers"):
        B.validate(m)
    with pytest.raises(ManifestError, match="b1.at: expected 2 values"):
        M.check(m)


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
    assert M.SEAM_RULES == addresses.EM_CFG.RULES.count == B.SEAM_RULES >= 8


def test_part_events():
    assert set(M.PART_EVENTS) < set(M.EVENTS)
    for event in M.EVENTS:
        text = _with(OWN + blk("b1", f"on_{event}", "part = 1\nplay = ['o']\n"))
        if event in M.PART_EVENTS:
            M.loads(text)
        else:
            with pytest.raises(ManifestError, match="unknown param"):
                M.loads(text)
