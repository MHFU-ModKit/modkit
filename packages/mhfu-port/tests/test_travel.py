# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import numpy as np
import pytest
from mhfu_port import fk, travel
from mhfu_port.cli import main
from mhfu_port.model import ANIMATION, SKELETON
from mhp_formats import fu
from mhp_formats.anim import Channel, Clip, Keyframe, Track
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Bone, Skeleton

LOC = (0x040, 0x080, 0x100)
ROT_Y = 0x010
UNIT = 16
"""Raw location units in one world unit."""


def rig() -> Skeleton:
    """Joint 0, the root, then a body and a head off it."""
    return Skeleton(
        [
            Bone(parent=-1, child=1),
            Bone(parent=0, child=2),
            Bone(parent=1, child=3, position=(0.0, 0.0, 50.0)),
            Bone(parent=2, position=(0.0, 30.0, 100.0)),
        ]
    )


def loc(axis: int, *keys: tuple[int, float], ease: int = 0) -> Channel:
    return Channel(LOC[axis], [Keyframe(round(v * UNIT), f, ease, -ease) for f, v in keys])


def dash(base: list[Channel], top: list[Channel]) -> Clip:
    body = Track([Channel(ROT_Y, [Keyframe(0, 0), Keyframe(900, 40)])])
    return Clip([Track(base), Track(top), body, Track()])


def pack(*clips: Clip) -> fu.Anim:
    return fu.Anim([list(clips), []])


def test_root():
    assert travel.root(rig()) == 1


def test_travel_on_joint0_is_drawn():
    anim = pack(dash([loc(2, (0, 0), (40, 1000))], [loc(1, (0, 200), (40, 220))]))
    (t,) = travel.of(anim, rig())
    assert (t.entry, t.frames, t.carried, t.drawn) == (0, 40, (0.0, 0.0), (0.0, 1000.0))
    assert t.seconds() == pytest.approx(40 / 2 / 30)


def test_carry_moves_travel_to_root():
    anim = pack(dash([loc(2, (0, 0), (40, 1000))], [loc(0, (0, -30), (40, -20))]))
    (t,) = travel.of(travel.carry(anim, rig()), rig())
    assert t.carried == (0.0, 1000.0) and t.drawn == (10.0, 0.0)


def test_carry_keeps_pose():
    base = [loc(0, (0, 5), (40, 9)), loc(1, (0, 165), (40, 165)), loc(2, (3, 0), (37, 800), ease=4)]
    top = [loc(1, (0, 226), (9, 210), (20, 290), (33, 200), ease=-6), loc(2, (0, 0), (40, 3))]
    clip = dash(base, top)
    skeleton = rig()
    after = travel.carry(pack(clip), skeleton).streams[0][0]
    assert after is not None
    r = fk.Rig.from_skeleton(skeleton)
    frames = np.linspace(0, 40, 161)
    before_w = r.world(*fk.Curves(clip, r).at(frames))[:, 2:, :3, 3]
    after_w = r.world(*fk.Curves(after, r).at(frames))[:, 2:, :3, 3]
    assert np.abs(before_w - after_w).max() < 0.1


def test_carry_refuses_turning_joint0():
    turning = Track([Channel(ROT_Y, [Keyframe(0, 0), Keyframe(100, 10)])])
    with pytest.raises(ValueError, match="rotates"):
        travel.carry(pack(Clip([turning, Track(), Track(), Track()])), rig())


def test_carry_keeps_shared_clips_shared():
    clip = dash([loc(2, (0, 0), (40, 100))], [])
    after = travel.carry(pack(clip, clip), rig())
    assert after.streams[0][0] is after.streams[0][1]


def test_cli(tmp_path, capsys):
    anim = pack(dash([loc(2, (0, 0), (40, 1000))], []))
    path = tmp_path / "port.bin"
    entries = [b""] * 4
    entries[SKELETON], entries[ANIMATION] = rig().to_bytes(), anim.to_bytes()
    path.write_bytes(Pac(entries).to_bytes())
    assert main(["travel", str(path), "--scale", "0.5"]) == 0
    out = capsys.readouterr().out
    assert "    0" in out and "500" in out and "joint 0" in out
    assert main(["travel", str(path), "--carry"]) == 0
    assert "joint 0" not in capsys.readouterr().out
