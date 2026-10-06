# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct

from mhfu import addresses as a
from mhfu.live import observe as ob
from ppsspp_debug import Hit


def f32(v: float) -> int:
    return int(struct.unpack("<I", struct.pack("<f", v))[0])


def frame_hit(usec: int, z: float, clip: float, entry: int = 20, yaw: int = 0x4000) -> Hit:
    """A hit logged in `frame_format`: pair (1,2), x 100, y 5, walls 1, class 2, speed 2."""
    stuck = 1 << 8 * (a.ENTITY.STUCK_WALL & 3)
    words = [usec, 1 | 2 << 8, f32(100), f32(5), f32(z), yaw, 1, stuck, f32(clip), f32(2.0)]
    words += [entry + 1000, f32(0.9)]
    return Hit(
        "exec", a.MONSTER_AI_STEP_CALL, None, False, message=" ".join(f"{w:x}" for w in words)
    )


def test_parse_frame():
    f = ob.parse_frame(frame_hit(1_500_000, 7.5, 12.0), 1_000_000)
    assert f is not None
    assert (f.t, f.main, f.sub, f.x, f.z, f.yaw) == (0.5, 1, 2, 100.0, 7.5, 0x4000)
    assert (f.walls, f.stuck, f.clip, f.speed, f.entry) == (1, 1, 12.0, 2.0, 20)
    assert f.scale == ob._f32(f32(0.9))
    assert ob.parse_frame(Hit("exec", 0, None, False, message="nope"), 0) is None


def test_legs_cut_on_entry_and_restart(tmp_path):
    hits = [frame_hit(t, z, c, e) for t, z, c, e in [(0, 0, 0, 20), (1, 30, 2, 20), (2, 70, 4, 20)]]
    hits += [frame_hit(3, 70, 0, 20), frame_hit(4, 70, 2, 21)]
    frames = [f for h in hits if (f := ob.parse_frame(h, 0)) is not None]
    legs = ob.legs(frames)
    assert [(leg.entry, len(leg.frames)) for leg in legs] == [(20, 3), (20, 1), (21, 1)]
    assert legs[0].path == 70.0 and legs[0].turn == 0
    path = tmp_path / "f.csv"
    ob.write_frames(frames, path)
    assert ob.read_frames(path) == frames
    assert "class 2" in ob.legs_report(legs)
