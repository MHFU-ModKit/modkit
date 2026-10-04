# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Clip <-> F-curves without Blender; these run in normal CI."""

from collections.abc import Callable
from types import ModuleType

import numpy as np
import pytest
from mhfu_port import fk
from mhfu_port.model import Model
from mhp_formats.anim import CHANNEL_BITS, Channel, Clip, Keyframe, Track

BIND = np.array([(0.0, 10.0, 0.0), (0.0, 100.0, 0.0), (0.0, 0.0, 50.0)])
BONES = ["j00", 'j01 "neck"', "j02"]


@pytest.fixture(scope="module")
def curves(module: Callable[[str], ModuleType]) -> ModuleType:
    return module("curves")


def bezier(p0: np.ndarray, p1: np.ndarray, p2: np.ndarray, p3: np.ndarray, s: float) -> np.ndarray:
    out: np.ndarray = (
        (1 - s) ** 3 * p0 + 3 * s * (1 - s) ** 2 * p1 + 3 * s**2 * (1 - s) * p2 + s**3 * p3
    )
    return out


def test_handles_draw_the_engine_spline(curves: ModuleType) -> None:
    keys = [Keyframe(0, 0, 0, 300), Keyframe(2048, 10, -100, 50), Keyframe(-700, 23, 9, 0)]
    step = curves.target(0x008).step
    co, left, right = curves.points(keys, step)
    for k in range(2):
        a, b = keys[k], keys[k + 1]
        for s in np.linspace(0.0, 1.0, 11):
            x, y = bezier(co[k], right[k], left[k + 1], co[k + 1], s)
            want = fk.spline(x, a.frame, a.value, a.ease_out, b.frame, b.value, b.ease_in)
            assert y / step == pytest.approx(float(want), abs=0.01)
            assert x == pytest.approx(a.frame + s * (b.frame - a.frame), abs=1e-4)


def test_keys_come_back(curves: ModuleType) -> None:
    keys = [
        Keyframe(-32768, -5, 32767, -32768),
        Keyframe(16, 0, 1, -1),
        Keyframe(16, 0, 3, 3),
        Keyframe(32767, 520, -27269, 0),
    ]
    for bit in (0x008, 0x080, 0x001, 0x400):
        to = curves.target(bit)
        offset = 37.25 if to.prop == curves.LOCATION else 0.0
        stored = [a.astype(np.float32) for a in curves.points(keys, to.step, offset)]
        assert curves.keyframes(*stored, to.step, offset) == keys
    one = [Keyframe(5, 3, 2, 1)]
    assert curves.keyframes(*curves.points(one, 1 / 16), 1 / 16) == one


def test_linear_segment_takes_the_chord(curves: ModuleType) -> None:
    keys = [Keyframe(0, 0, 0, 900), Keyframe(160, 10, -5, 0), Keyframe(0, 20)]
    co, left, right = curves.points(keys, 1 / 16)
    got = curves.keyframes(co, left, right, 1 / 16, 0.0, ["LINEAR", "BEZIER", "LINEAR"])
    assert got == [Keyframe(0, 0, 0, 16), Keyframe(160, 10, 16, 0), Keyframe(0, 20)]


def test_targets(curves: ModuleType) -> None:
    for bit in [*CHANNEL_BITS, 0x001, 0x002, 0x004]:
        to = curves.target(bit)
        assert curves.bit_of(to.prop, to.index) == bit
        assert to.custom == (bit < 0x008 or bit >= 0x200)
    assert curves.target(0x010) == curves.Target("rotation_euler", 1, np.pi / 8192)
    assert curves.target(0x200).prop == "mhfu_ch_200" and curves.target(0x002).step == 1 / 16
    assert curves.bit_of("scale", 0) is None and curves.bit_of("mhfu_ch_zz", 0) is None
    assert curves.bit_of("mhfu_ch_040", 0) is None


def test_paths(curves: ModuleType) -> None:
    for bone in BONES + ["a\\b"]:
        for prop in ("location", "mhfu_ch_001"):
            assert curves.parse_path(curves.data_path(bone, prop)) == (bone, prop)
    assert curves.data_path('j01 "neck"', "location") == 'pose.bones["j01 \\"neck\\""].location'
    assert curves.parse_path("location") is None


def clip() -> Clip:
    return Clip(
        [
            Track([Channel(0x080, [Keyframe(160, 0), Keyframe(480, 20)])]),
            Track(
                [
                    Channel(0x040, [Keyframe(3, 0, 1, 2)]),
                    Channel(0x008, [Keyframe(0, 0), Keyframe(9, 4), Keyframe(9, 4)]),
                    Channel(0x200, [Keyframe(256, 0)]),
                ]
            ),
            Track(),
            Track([Channel(0x020, [Keyframe(1, 1)])]),
        ],
        1,
        3.5,
    )


def test_clip_round_trip(curves: ModuleType) -> None:
    c = clip()
    found = curves.to_curves(c, BONES, BIND)
    assert [(x.bone, x.prop, x.index) for x in found] == [
        ("j00", "location", 1),
        ('j01 "neck"', "location", 0),
        ('j01 "neck"', "rotation_euler", 0),
        ('j01 "neck"', "mhfu_ch_200", 0),
    ]
    assert found[0].co[:, 1].tolist() == [0.0, 20.0]
    joints = {b: j for j, b in enumerate(BONES)}
    assert curves.to_clip(found, joints, BIND, 1, 3.5, base=c) == c
    fresh = curves.to_clip(found, joints, BIND, 1, 3.5)
    assert [[ch.bit for ch in t.channels] for t in fresh.tracks] == [
        [0x080],
        [0x008, 0x040, 0x200],
        [],
    ]


def test_track_map(curves: ModuleType) -> None:
    """A donor's joints play mapped tracks; a track no joint plays comes from the base."""
    c = clip()
    joint_tracks = {0: 1, 2: 3}
    found = curves.to_curves(c, BONES, BIND, joint_tracks)
    assert {x.bone for x in found} == {"j00", "j02"}
    joints = {b: j for j, b in enumerate(BONES)}
    assert curves.to_clip(found, joints, BIND, 1, 3.5, c, joint_tracks) == c
    dropped = curves.to_clip(found[:-1], joints, BIND, 1, 3.5, c, joint_tracks)
    assert dropped.tracks[3] == Track() and dropped.tracks[0] == c.tracks[0]
    short = Clip(c.tracks[:2], 1, 3.5)
    assert curves.to_clip(found, joints, BIND, 1, 3.5, short, joint_tracks) == c


def test_stored_model(module: Callable[[str], ModuleType], synthetic: bytes) -> None:
    stored = module("stored")
    m = Model.from_bytes(synthetic, "syn")
    props = stored.props(m)
    assert props[stored.GAME] == "mhfu" and stored.GEOMETRY not in props
    again = stored.model(props)
    assert again.anim is not None and m.anim is not None
    assert again.anim.to_bytes() == m.anim.to_bytes() and again.name == "syn"
