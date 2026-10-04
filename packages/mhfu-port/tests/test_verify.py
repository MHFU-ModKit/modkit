# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import math
from pathlib import Path

import pytest
from mhfu.files import monster_pac
from mhfu_port import build, manifest, mesh, motion, verify
from mhfu_port.cli import main
from mhfu_port.mesh import Part, Skinned
from mhp_formats import Channel, Clip, Keyframe, Pac, fu
from mhp_formats.anim import quantize
from mhp_formats.skeleton import Bone, Skeleton

PORTS = Path(__file__).parents[3] / "ports"
TIGREX = 75
LOC_Y = 0x080
FRAMES = 8
# a root, a pelvis forking into a front leg (with a foot) and a rear leg
PARENTS = [-1, 0, 1, 1, 2]
LOCAL = [
    (0.0, 0.0, 0.0),
    (0.0, 100.0, 0.0),
    (50.0, 0.0, 0.0),
    (-50.0, 0.0, 0.0),
    (0.0, -100.0, 0.0),
]
VERTICES = [(50.0, 100.0, 0.0), (-50.0, 100.0, 0.0), (0.0, 120.0, 0.0), (50.0, 0.0, 0.0)]
JOINT = [2, 3, 1, 4]
TRIANGLES = [(0, 1, 2), (0, 2, 3)]
AT_6 = 3 * 0.75**2 - 2 * 0.75**3
"""How far a lift has risen at frame 6 of FRAMES: the spline between two keys without eases."""


def skeleton(parents=PARENTS, local=LOCAL, streams=None):
    streams = streams or [0] * len(parents)
    bones = [
        Bone(parent=p, position=o, stream=s)
        for p, o, s in zip(parents, local, streams, strict=True)
    ]
    return Skeleton(bones, [0, len(bones)])


def model(joints=JOINT):
    part = Part(VERTICES, [], [(0.0, 0.0)] * 4, [], TRIANGLES, [[(j, 1.0)] for j in joints], 0)
    return mesh.build([Skinned(part, part.influences)], (256.0, 256.0, 256.0))


def lifted(joint, units):
    """A clip raising `joint` by `units` over FRAMES frames; every other joint rests."""
    tracks = [motion.rest(FRAMES) for _ in PARENTS]
    bind = LOCAL[joint][1]
    keys = [Keyframe(quantize("loc", bind), 0), Keyframe(quantize("loc", bind + units), FRAMES)]
    tracks[joint].channels.append(Channel(LOC_Y, keys))
    return Clip(tracks)


def pac(*clips, skel=None, pmo=None):
    anim = fu.Anim([list(clips)])
    entries = [(skel or skeleton()).to_bytes(), (pmo or model()).to_bytes(), b"", anim.to_bytes()]
    return Pac(entries).to_bytes()


def port(*clips, skel=None, pmo=None):
    return verify.Port(pac(*clips, skel=skel, pmo=pmo))


def rest():
    return lifted(0, 0.0)


def checks(p, *donor):
    return {c.name: c for c in verify.audit(p, *donor)}


def test_branches():
    assert verify.branches(PARENTS, 1) == {2: 2, 4: 2, 3: 3}


def test_tear():
    p = port(rest(), lifted(3, 400.0))
    t = verify.tear(p)
    assert t is not None and (t.slot, t.frame, t.joints) == (1, 6, (2, 3))
    assert t.growth == pytest.approx(math.hypot(100, 400 * AT_6) - 100, abs=0.5)
    assert verify.tear(port(rest())).growth == pytest.approx(0.0, abs=1e-6)


def test_worst_and_faces():
    p = port(rest(), lifted(4, -50.0))
    (top, *_) = verify.worst(p)
    assert (top.slot, top.frame, top.joints) == (1, 6, (2, 4))
    assert top.growth == pytest.approx(50 * AT_6, abs=0.1)
    (face, ratio), *more = verify.faces(p, 1, FRAMES)
    assert not more and face.joints == (2, 4) and ratio == pytest.approx(1.5, rel=1e-3)
    with pytest.raises(ValueError, match="slot 5 is empty"):
        verify.faces(p, 5, 0)


def test_audit():
    ok = checks(port(rest()))
    assert all(c.ok for c in ok.values()), [c for c in ok.values() if not c.ok]
    assert ok["location channels vs the body fork (report)"].detail.startswith("fork 1")
    torn = checks(port(rest(), lifted(3, 400.0)))
    assert not torn["clips do not tear the mesh across the body fork"].ok
    narrow = checks(port(Clip([motion.rest(FRAMES)] * 4)))
    assert not narrow["each animation stream carries its part's joints"].ok
    split = checks(port(rest(), skel=skeleton(streams=[0, 0, 1, 0, 1])))
    assert not split["the animated joints' streams form contiguous runs"].ok
    late = checks(port(rest(), skel=skeleton(parents=[-1, 0, 4, 1, 2])))
    assert not late["every parent precedes its child"].ok
    still = checks(port(rest(), skel=Skeleton(skeleton().bones, [0, 2])))
    assert not still["little geometry on a joint the animation does not reach"].ok


def test_correspondence():
    donor = Skeleton([Bone(parent=-1), Bone(parent=0, position=(0.0, 5.0, 0.0))])
    donor.bones += [Bone(parent=1, position=(1.0, 0.0, 0.0)), Bone(position=(9.0, 5.0, 0.0))]
    donor.bones.insert(2, Bone(parent=1, position=(-1.0, 0.0, 0.0)))
    # a pad above the root, the two children swapped, the orphan re-parented under bone 1
    parents = [-1, 0, 1, 2, 2, 2]
    local = [(0.0, 0.0, 0.0)] * 2 + [(0.0, 5.0, 0.0), (1.0, 0.0, 0.0), (-1.0, 0.0, 0.0)]
    local.append((9.0, 0.0, 0.0))
    c = verify.correspondence(donor, skeleton(parents, local))
    assert (c.joint_of, c.pad, c.by_position) == ({0: 1, 1: 2, 2: 4, 3: 3, 4: 5}, 1, {4})
    assert c.tree == {0: 1, 1: 2, 2: 4, 3: 3}


def test_audit_donor():
    parts = [Part(VERTICES, [], [], [], TRIANGLES, [[(j, 1.0)] for j in JOINT], 0)]
    good = checks(port(rest()), skeleton(), parts)
    assert all(c.ok for c in good.values()), [c for c in good.values() if not c.ok]
    moved = checks(port(rest(), pmo=model([2, 3, 1, 2])), skeleton(), parts)
    assert moved["every vertex keeps the donor's joints"].detail == "1 differ"
    short = checks(port(rest()), skeleton(), [*parts, parts[0]])
    assert not short["vertex count kept"].ok


def test_commands(tmp_path, capsys):
    torn, flat = tmp_path / "torn.bin", tmp_path / "flat.bin"
    torn.write_bytes(pac(rest(), lifted(3, 400.0)))
    raised = lifted(0, 0.0)
    root = raised.tracks[0].channels[-1]
    root.keyframes[:] = [k._replace(value=quantize("loc", 30.0)) for k in root.keyframes]
    flat.write_bytes(pac(rest(), raised))
    assert main(["verify", str(torn)]) == 1
    assert main(["stretch", str(torn), "--fork"]) == 0
    tear = math.hypot(100, 400 * AT_6) - 100
    assert (
        f"worst tear {tear:.0f} units (slot 1 frame 6, group 0, joints 2-3)"
        in capsys.readouterr().out
    )
    assert main(["stretch", str(torn)]) == main(["stretch", str(torn), "--slot", "1"]) == 0
    assert main(["floor", str(torn), str(flat)]) == 0
    assert "flat.bin stands +30.0 units against torn.bin" in capsys.readouterr().out


def test_host(data):
    p = verify.Port(data.fu.read(monster_pac(TIGREX)))
    assert all(c.ok for c in verify.audit(p))
    assert verify.tear(p) is None


@pytest.mark.parametrize("name", ["brute_tigrex", "zinogre"])
def test_ports(data, tmp_path, name):
    """The issue's bar: both ports, as built, pass verify against their donor."""
    m = manifest.load(PORTS / f"{name}.toml")
    pac = tmp_path / "port.bin"
    pac.write_bytes(build.build(m, data).pac)
    games = ["--data", str(data.fu.root), "--p3rd-data", str(data.p3rd.root)]
    assert main(["verify", str(pac), "--manifest", str(PORTS / f"{name}.toml"), *games]) == 0
