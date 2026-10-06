# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import math
from collections import Counter

import numpy as np
import pytest
from mhfu import files
from mhfu.entries import PART_STREAMS
from mhfu.files import Extracted
from mhfu_port import fk
from mhp_formats.anim import Channel, Clip, Keyframe, Track
from mhp_formats.fu import Anim
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Bone, Skeleton

ROT_X, ROT_Z, LOC_Y = 0x008, 0x020, 0x080
QUARTER = 4096
"""A raw rotation of 90 degrees."""


def clip(*tracks: dict[int, list[tuple[int, ...]]]) -> Clip:
    """One track per dict: channel bit -> [(frame, raw value[, ease_in, ease_out])]."""
    return Clip(
        [
            Track(
                [
                    Channel(bit, [Keyframe(v, f, *eases) for f, v, *eases in keys])
                    for bit, keys in t.items()
                ]
            )
            for t in tracks
        ]
    )


def smooth(s: float) -> float:
    """The spline between two keys without eases, as a fraction of the way."""
    return 3 * s**2 - 2 * s**3


def rig(parents: list[int], local: list[tuple[float, float, float]] | None = None) -> fk.Rig:
    return fk.Rig(parents, local or [(10.0 * (i + 1), 2.0, -3.0) for i in range(len(parents))])


def test_bind_world():
    local = [(0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 2.0), (5.0, 0.0, 0.0)]
    assert fk.bind_world([-1, 0, 1, 3], local) == [
        (0.0, 1.0, 0.0),
        (1.0, 1.0, 0.0),
        (1.0, 1.0, 2.0),
        (5.0, 0.0, 0.0),
    ]


def test_cycle_becomes_root():
    assert list(fk.effective_parents([-1, 2, 1, 7])) == [-1, 2, -1, -1]
    r = rig([-1, 2, 1])
    assert np.array_equal(r.bind_joints, fk.bind_world([-1, 2, 1], r.bind_local.tolist()))


def test_euler_order():
    r = fk.euler_xyz([0.3, -0.7, 1.1])
    rx, ry, rz = (fk.euler_xyz(a) for a in ([0.3, 0, 0], [0, -0.7, 0], [0, 0, 1.1]))
    assert np.allclose(r, rz @ ry @ rx)


def test_channel_holds_past_its_last_key():
    c = fk.Curves(
        clip({}, {ROT_X: [(0, 0), (10, QUARTER)]}, {ROT_X: [(0, 0), (30, 2048)]}), rig([-1, 0, 1])
    )
    rot, _ = c.at(25)
    assert rot[1, 0] == pytest.approx(math.pi / 2)
    assert rot[2, 0] == pytest.approx(smooth(25 / 30) * math.pi / 4)
    assert c.last_frame == 30
    assert c.driven == (1, 2)
    assert list(c.keys()) == [0, 10, 30]


def test_undriven_axis_keeps_bind():
    r = rig([-1, 0], [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)])
    _, loc = fk.Curves(clip({}, {LOC_Y: [(0, 160), (10, 320)]}), r).at(0)
    assert loc.tolist() == [[1.0, 2.0, 3.0], [4.0, 10.0, 6.0]]


def test_fractional_and_batched_frames():
    c = fk.Curves(clip({ROT_Z: [(0, 0), (4, QUARTER)]}), rig([-1]))
    rot, loc = c.at([[1.0, 2.5]])
    assert rot.shape == loc.shape == (1, 2, 1, 3)
    assert rot[0, :, 0, 2] == pytest.approx(
        [smooth(0.25) * math.pi / 2, smooth(0.625) * math.pi / 2]
    )


def test_eases_are_slopes():
    """The engine's spline: the left key's ease_out and the right key's ease_in are its slopes
    in raw units per frame; at the chord's slope it is a straight line."""
    r = rig([-1], [(0.0, 0.0, 0.0)])
    straight = fk.Curves(clip({LOC_Y: [(0, 0, 0, 160), (10, 1600, 160, 0)]}), r)
    assert straight.at(2.5)[1][0, 1] == pytest.approx(25.0)
    eased = fk.Curves(clip({LOC_Y: [(0, 0, 0, 320), (10, 1600, 0, 0)]}), r)
    step = 1e-6
    slope = (eased.at(step)[1][0, 1] - eased.at(0.0)[1][0, 1]) / step
    assert slope == pytest.approx(320 / 16, rel=1e-4)
    assert eased.at(10.0)[1][0, 1] == pytest.approx(100.0)


def test_spline_ends():
    assert fk.spline(3.0, 3.0, 1.0, 9.0, 7.0, 5.0, -9.0) == pytest.approx(1.0)
    assert fk.spline(7.0, 3.0, 1.0, 9.0, 7.0, 5.0, -9.0) == pytest.approx(5.0)
    assert fk.spline(9.0, 3.0, 1.0, 9.0, 3.0, 5.0, -9.0) == 1.0


def test_track_map():
    c = clip({}, {ROT_X: [(0, QUARTER)]})
    rot, _ = fk.Curves(c, rig([-1, 0, 1]), tracks={2: 1}).at(0)
    assert rot[:, 0] == pytest.approx([0.0, 0.0, math.pi / 2])


def test_world_and_deform():
    r = rig([-1, 0], [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0)])
    world = fk.world_matrices(r, clip({ROT_Z: [(0, QUARTER)]}), 0)
    assert world[1, :3, 3] == pytest.approx([0.0, 10.0, 0.0])
    deform = fk.deform_matrices(r, clip({ROT_Z: [(0, QUARTER)]}), 0)
    skin = fk.Skin([(20.0, 0.0, 0.0), (5.0, 0.0, 0.0)], [[(1, 1.0)], [(0, 0.5), (1, 0.5)]], 2)
    assert skin.apply(deform) == pytest.approx(np.array([[0.0, 20.0, 0.0], [0.0, 5.0, 0.0]]))
    assert list(skin.dominant()) == [1, 0]


def test_bind_deform_is_identity():
    r = rig([-1, 0, 1])
    assert np.allclose(r.deform(r.bind_world), np.eye(4))


def test_unskinned_vertex_stays():
    skin = fk.Skin([(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)], [[(0, 0.0), (1, 0.0)], [(-1, 1.0)]], 2)
    assert list(skin.dominant()) == [-1, -1]
    deform = np.tile(np.eye(4), (2, 1, 1))
    deform[:, :3, 3] = 100.0
    assert np.array_equal(skin.apply(deform), skin.positions)


def test_normals_ignore_translation():
    skin = fk.Skin([(0.0, 0.0, 0.0)], [[(0, 1.0)]], 1)
    deform = np.eye(4)[None].copy()
    deform[0, :3, 3] = 50.0
    deform[0, :3, :3] = fk.euler_xyz([0.0, 0.0, math.pi / 2])
    assert skin.apply_normals(deform, [(2.0, 0.0, 0.0)]) == pytest.approx(
        np.array([[0.0, 1.0, 0.0]])
    )


def test_rig_clip_joins_parts():
    bones = [Bone(parent=i - 1, stream=s) for i, s in enumerate([0, 0, 1, 2])]
    body, head = clip({ROT_X: [(0, 1)]}, {ROT_X: [(0, 2)]}), clip({ROT_X: [(0, 3)]})
    anim = Anim([[body], [None], [head], [None]])
    whole = fk.rig_clip(anim, 0, Skeleton(bones))
    assert whole is not None
    assert [t.channels[0].keyframes[0].value for t in whole.tracks[:3]] == [1, 2, 3]
    assert whole.tracks[3].channels == []
    assert fk.rig_clip(Anim([[None], [None], [None]]), 0, Skeleton(bones)) is None


def test_tigrex_parts(mhfu_data):
    """The game's own split: part k's clips hold one track per joint of `Bone.stream` k."""
    pac = Pac.from_bytes(Extracted.find(mhfu_data).read(files.monster_pac(75)))
    skeleton, pack = Skeleton.from_bytes(pac.entries[0]), Anim.from_bytes(pac.entries[3])
    width = Counter(b.stream for b in skeleton.bones[: skeleton.params[1]])
    for k, n in width.items():
        clips = [c for c in pack.streams[PART_STREAMS * k] if c is not None]
        assert clips and {len(c.tracks) for c in clips} == {n}
    odd = [c for s in pack.streams[1::PART_STREAMS] for c in s if c is not None]
    assert not odd
