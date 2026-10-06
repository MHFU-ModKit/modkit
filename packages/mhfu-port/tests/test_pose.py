# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

import pytest
from mhfu.files import monster_pac
from mhfu_port import build, manifest, mesh, motion, pose, verify
from mhfu_port.cli import main
from mhfu_port.mesh import Part, Skinned
from mhp_formats import Channel, Clip, Keyframe, Pac, Track, fu
from mhp_formats.anim import quantize
from mhp_formats.skeleton import Bone, Skeleton

PORTS = Path(__file__).parents[3] / "ports"
TIGREX = 75
ROT_Z, LOC_Y = 0x020, 0x080
FRAMES = 8
QUARTER = 4096
"""A raw rotation of 90 degrees."""
# the donor: a root, a hip and a leg hanging 100 units down; the port puts a pad above them and
# a joint the animation does not reach below them
DONOR = Skeleton([Bone(), Bone(parent=0, position=(0.0, 100.0, 0.0))])
DONOR.bones.append(Bone(parent=1, position=(0.0, -100.0, 0.0)))
PORT = Skeleton([Bone(), Bone(parent=0), Bone(parent=1, position=(0.0, 100.0, 0.0))], [0, 4])
PORT.bones += [
    Bone(parent=2, position=(0.0, -100.0, 0.0)),
    Bone(parent=3, position=(0.0, -50.0, 0.0)),
]
RECORD_OF = {0: 0, 1: 1}
"""Donor bone -> track: the leg carries no record."""


def swing(turn=QUARTER, lift=0.0):
    """The root raised by `lift`, the hip turning `turn` about z."""
    root = Track([Channel(LOC_Y, [Keyframe(quantize("loc", lift), 0)])])
    hip = Track([Channel(ROT_Z, [Keyframe(0, 0), Keyframe(turn, FRAMES)])])
    return Clip([root, hip])


def ported(*clips):
    """The port's clips: donor tracks one joint down, the pad and the leg resting."""
    rest = motion.rest(FRAMES)
    anim = fu.Anim([[None if c is None else Clip([rest, *c.tracks, rest]) for c in clips]])
    positions = [(0.0, 100.0, 0.0), (0.0, 0.0, 0.0), (0.0, 50.0, 0.0), (5.0, -50.0, 0.0)]
    part = Part(positions, [], [(0.0, 0.0)] * 4, [], [(0, 1, 2), (1, 2, 3)], [], 0)
    skinned = Skinned(part, [[(2, 1.0)], [(3, 1.0)], [(3, 1.0)], [(4, 1.0)]])
    model = mesh.build([skinned], (256.0, 256.0, 256.0))
    return verify.Port(Pac([PORT.to_bytes(), model.to_bytes(), b"", anim.to_bytes()]).to_bytes())


def test_compare():
    r = pose.compare(ported(None, swing(), swing()), DONOR, {1: swing(), 2: swing()}, RECORD_OF)
    assert (r.matched, r.pad, r.compared, r.ok) == (3, 1, 3, True)
    assert [(s.slot, s.frame, s.worst) for s in r.slots] == [(1, FRAMES, 0.0), (2, FRAMES, 0.0)]


def test_lift_is_not_a_difference():
    r = pose.compare(ported(None, swing(lift=40.0)), DONOR, {1: swing()}, RECORD_OF)
    (s,) = r.slots
    assert s.lift == pytest.approx(40.0) and s.worst == pytest.approx(0.0, abs=1e-9) and r.ok


def test_differs():
    r = pose.compare(ported(None, swing(turn=2048)), DONOR, {1: swing()}, RECORD_OF)
    assert not r.ok and r.worst > pose.TOLERANCE


def test_absent_and_partial():
    port = ported(None, swing(), swing())
    port.anim.streams[0][2] = Clip([])  # a slot the host fills in some other part only
    r = pose.compare(port, DONOR, {1: swing(), 2: swing(), 3: swing()}, RECORD_OF)
    assert (r.absent, r.partial) == ([3], [(2, 0, 2)])


def test_refuses_another_rig():
    stranger = Skeleton([Bone(), Bone(parent=0, position=(7.0, 7.0, 7.0))])
    stranger.bones += [Bone(parent=1, position=(3.0, 0.0, 0.0)), Bone(parent=1)]
    with pytest.raises(ValueError, match="1 of 4 donor bones"):
        pose.compare(ported(None, swing()), stranger, {1: swing()}, RECORD_OF)


def test_rest_floor():
    f = pose.rest_floor(ported(None, swing(lift=30.0)))
    # the lower vertex rides a joint the animation does not reach, and does not count
    assert (f.vertex, f.joint, f.height) == (1, 3, pytest.approx(30.0, abs=0.01))


def test_host_floor(data):
    """The control: MHFU's rest pose stands on the origin."""
    f = pose.rest_floor(verify.Port(data.fu.read(monster_pac(TIGREX))))
    assert abs(f.height) < 5.0


@pytest.mark.parametrize("name", ["brute_tigrex", "zinogre"])
def test_ports_play_their_donor(data, tmp_path, name):
    m = manifest.load(PORTS / f"{name}.toml")
    d = build.donor(m, data)
    out = build.build(m, data)
    built = out.pac
    clips = {e: d.clips[cid] for e, cid in out.layout.entries.items()}
    r = pose.compare(verify.Port(built), d.skeleton, clips, build.record_map(d, m.build))
    assert r.ok and {round(s.lift, 1) for s in r.slots} == {m.build.ground_lift}
    assert len(r.slots) == len(d.clips) and not r.absent and not r.partial
    path = tmp_path / "port.bin"
    path.write_bytes(built)
    games = ["--data", str(data.fu.root), "--p3rd-data", str(data.p3rd.root)]
    assert main(["pose", str(path), "--manifest", str(PORTS / f"{name}.toml"), *games]) == 0
