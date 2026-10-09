# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import math
from pathlib import Path

import pytest
from mhfu_port import continuity, manifest
from mhfu_port.data import Data
from mhp_formats.anim import Channel, Clip, Keyframe, Track, quantize
from mhp_formats.skeleton import Bone, Skeleton

PORTS = Path(__file__).parents[3] / "ports"
ROT = (0x008, 0x010, 0x020)


def skeleton() -> Skeleton:
    """Joint 0 and the root 1 carry the travel; the body 2 and a limb 3 turn."""
    return Skeleton(
        [
            Bone(parent=-1, child=1),
            Bone(parent=0, child=2),
            Bone(parent=1, child=3, position=(0.0, 0.0, 50.0)),
            Bone(parent=2, position=(0.0, 30.0, 100.0)),
        ]
    )


def turn(axis: int, degrees: float) -> Channel:
    return Channel(ROT[axis], [Keyframe(quantize("rot", math.radians(degrees)), 0)])


def held(channels: list[Channel], frames: int) -> Track:
    """Each channel at its first key's value from frame 0 to `frames`."""
    return Track(
        [Channel(c.bit, [c.keyframes[0], c.keyframes[0]._replace(frame=frames)]) for c in channels]
    )


def clip(body: list[Channel], limb: list[Channel], frames: int = 10) -> Clip:
    """A clip whose joints 2 and 3 hold the given channels."""
    return Clip([Track(), Track(), held(body, frames), held(limb, frames)])


def moveset(**poses: tuple[float, float]) -> dict[int, Clip]:
    """Clip i holds the body at x degrees and the limb at y degrees about x."""
    return {int(k[1:]): clip([turn(0, b)], [turn(0, ll)]) for k, (b, ll) in poses.items()}


def test_a_clip_fits_itself_best_and_a_rotated_one_worse():
    e = continuity.ends(moveset(c1=(0, 0), c2=(0, 0), c3=(0, 90), c4=(90, 90)), skeleton())
    got = dict(continuity.fits_after(e, 1))
    assert got[2] == pytest.approx(0.0) and got[3] == pytest.approx(45.0)
    assert got[4] == pytest.approx(90.0), "both joints turn 90 degrees"
    assert [i for i, _ in continuity.fits_after(e, 1)] == [2, 3, 4]
    assert continuity.fits_after(e, 1, top=1) == [(2, pytest.approx(0.0, abs=1e-6))]


def test_equal_scores_go_nearest_first():
    e = continuity.ends(moveset(c1=(0, 0), c9=(0, 0), c3=(0, 0), c2=(0, 0)), skeleton())
    assert [i for i, _ in continuity.fits_after(e, 1)] == [2, 3, 9]


def test_the_body_heading_is_not_a_difference():
    still = clip([turn(0, 30)], [turn(0, 10)])
    faced = clip([turn(0, 30), turn(1, 70)], [turn(0, 10)])  # the same pose, turned about y
    e = continuity.ends({1: still, 2: faced}, skeleton())
    assert dict(continuity.fits_after(e, 1))[2] < 1.0


def test_the_end_poses_are_the_clips_ends():
    start = Track([Channel(ROT[0], [Keyframe(0, 0), Keyframe(quantize("rot", math.pi / 2), 8)])])
    swing = Clip([Track(), Track(), start, Track()])
    rest = clip([turn(0, 0)], [])
    e = continuity.ends({1: swing, 2: rest, 3: clip([turn(0, 90)], [])}, skeleton())
    after_swing, before_swing = dict(continuity.fits_after(e, 1)), dict(continuity.fits_after(e, 2))
    assert after_swing[3] == pytest.approx(0.0, abs=0.1), "the swing ends where 3 begins"
    assert before_swing[1] == pytest.approx(0.0), "and begins where the rest ends"


def test_words():
    assert [continuity.word(x) for x in (0.0, 10.0, 15.0, 20.0, 31.0)] == [
        "good",
        "good",
        "fair",
        "fair",
        "poor",
    ]


def test_neighbours():
    ids = [101, 103, 104, 200, 201, 230, 231, 232, 5]
    assert continuity.neighbours(231, ids) == [230, 232, 201, 200, 104, 103, 101, 5]
    assert continuity.neighbours(103, [103, 101, 104, 5]) == [104, 101, 5]


# ---- the Zinogre's donor (MHP3RD_DATA) ----


@pytest.fixture(scope="module")
def zinogre(data: Data) -> continuity.Ends:
    return continuity.donor_ends(manifest.load(PORTS / "zinogre.toml"), data)


def rank(e: continuity.Ends, a: int, b: int) -> int:
    return [i for i, _ in continuity.fits_after(e, a)].index(b) + 1


@pytest.mark.parametrize("a, b", [(218, 219), (219, 220), (221, 222), (222, 223)])
def test_the_backflip_parts_chain(zinogre, a, b):
    assert rank(zinogre, a, b) <= 3


def test_the_flinch_roll_parts_chain_crossed(zinogre):
    """The donor's part 2s are crossed with its part 1s: 103 (_left_) ends where 108 (_right_)
    begins, 4 degrees off, and 107 is 42 off."""
    names = {103: "flinch_roll_left_part_1", 108: "flinch_roll_right_part_2"}
    assert rank(zinogre, 103, 108) <= 3 and rank(zinogre, 104, 107) <= 3, names
    assert dict(continuity.fits_after(zinogre, 103))[108] < continuity.GOOD


def test_the_whole_moveset_ranks_fast(zinogre):
    import time

    t = time.perf_counter()
    for a in zinogre.ids:
        continuity.fits_after(zinogre, a)
    assert time.perf_counter() - t < 1.0
