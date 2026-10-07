# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import math
from pathlib import Path

import numpy as np
import pytest
from mhfu.live import observe
from mhfu_port import build, fk, manifest, motion, travel
from mhfu_port.cli import main
from mhfu_port.model import ANIMATION, SKELETON
from mhp_formats import fu
from mhp_formats.anim import Channel, Clip, Keyframe, Track
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Bone, Skeleton

PORTS = Path(__file__).parents[3] / "ports"
LOC = (0x040, 0x080, 0x100)
ROT = (0x008, 0x010, 0x020)
UNIT = 16
"""Raw location units in one world unit."""
QUARTER = 4096
"""Raw rotation units in a quarter turn."""


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


def rot(axis: int, *keys: tuple[int, float]) -> Channel:
    """`keys` in degrees."""
    return Channel(ROT[axis], [Keyframe(round(v / 90 * QUARTER), f) for f, v in keys])


def dash(base: list[Channel], top: list[Channel], turn: float = 0.0) -> Clip:
    """A donor's clip: travel on joint 0 (`base`), the hip on the root (`top`), and a body
    that turns `turn` degrees."""
    body = Track([rot(1, (0, 0), (40, turn))]) if turn else Track([rot(0, (0, 5), (40, 5))])
    return Clip([Track(base), Track(top), body, Track()])


HIP = loc(1, (0, 200), (40, 220))


def pack(*clips: Clip) -> fu.Anim:
    return fu.Anim([list(clips), []])


def pac(tmp_path, anim: fu.Anim):
    path = tmp_path / "port.bin"
    entries = [b""] * 4
    entries[SKELETON], entries[ANIMATION] = rig().to_bytes(), anim.to_bytes()
    path.write_bytes(Pac(entries).to_bytes())
    return path


def test_root():
    assert travel.root(rig()) == 1


def test_travel_on_joint0_is_drawn():
    (t,) = travel.of(pack(dash([loc(2, (0, 0), (40, 1000))], [HIP], 20.0)), rig())
    assert (t.entry, t.frames, t.carried, t.drawn) == (0, 40, (0.0, 0.0), (0.0, 1000.0))
    assert t.turn == pytest.approx(20 / 360 * travel.TURN, abs=3)  # raw keys round
    assert t.seconds() == pytest.approx(40 / 2 / 30)


def test_carry_moves_travel_to_root_and_height_to_joint0():
    clip = dash([loc(2, (0, 0), (40, 1000)), loc(1, (0, 165), (40, 165))], [HIP, loc(0, (0, -30))])
    after = travel.carry(pack(clip), rig())
    (t,) = travel.of(after, rig())
    assert t.carried == (0.0, 1000.0) and t.drawn == (0.0, 0.0)
    c = after.streams[0][0]
    assert c is not None
    _, at = fk.Curves(c, fk.Rig.from_skeleton(rig())).at(np.array([0.0, 40.0]))
    assert at[:, 0].tolist() == [[-30.0, 365.0, 0.0], [-30.0, 385.0, 0.0]]


def test_carry_takes_the_turn_out():
    clip = dash([loc(2, (0, 0), (40, 600)), loc(0, (0, 20), (13, -25), (40, 10))], [HIP], 60.0)
    s = rig()
    after = travel.carry(pack(clip), s)
    (t,) = travel.of(after, s)
    assert abs(t.turn) <= 2  # the clip no longer turns the body
    (turn,) = travel.turns(after, s).values()
    assert turn.data == turn.keys[-1] == pytest.approx(60 / 360 * travel.TURN, abs=3)
    assert t.carried == pytest.approx((-10.0, 600.0), abs=0.2)  # the source's, as YAW turns


def test_carry_takes_a_facing_off_the_start_out_too():
    """A body that starts facing aside starts and ends the carried clip facing YAW."""
    s = rig()
    clip = Clip([Track(), Track([HIP]), Track([rot(1, (0, -120), (40, 10))]), Track()])
    after = travel.carry(pack(clip), s)
    (turn,) = travel.turns(after, s).values()
    assert turn.keys[0] == pytest.approx(-120 / 360 * travel.TURN, abs=3)
    assert turn.total == pytest.approx(130 / 360 * travel.TURN, abs=3)
    c = after.streams[0][0]
    assert c is not None
    facing = travel.body_turn(fk.Rig.from_skeleton(s), c, s, np.arange(41.0))
    assert np.abs(facing).max() < math.radians(0.5)


def engine_pose(clip: Clip, frames: np.ndarray, s: Skeleton | None = None) -> np.ndarray:
    """World joint positions as the engine draws them: no root translation."""
    s = s or rig()
    r = fk.Rig.from_skeleton(s)
    rot_, loc_ = fk.Curves(clip, r).at(frames)
    loc_[:, travel.root(s)] = 0.0
    out: np.ndarray = r.world(rot_, loc_)[..., :3, 3]
    return out


def turned_by(pose: np.ndarray, turn: travel.Turn | None, frames: np.ndarray) -> np.ndarray:
    """`pose` as YAW turned by `turn` draws it."""
    th = turn.at(frames) / travel.TURN * math.tau if turn else np.zeros(len(frames))
    c, si = np.cos(th)[:, None], np.sin(th)[:, None]
    x, y, z = pose[..., 0], pose[..., 1], pose[..., 2]
    out: np.ndarray = np.stack([x * c + z * si, y, -x * si + z * c], axis=-1)
    return out


def test_carry_keeps_pose():
    """Source == carried turned by the curve, within 0.2 units at the keys and 1 between."""
    base = [loc(0, (0, 5), (40, 9)), loc(1, (0, 165), (40, 165)), loc(2, (3, 0), (37, 800), ease=4)]
    top = [loc(0, (0, -20), (9, 10), (20, -30), (40, 0)), loc(1, (0, 226), (9, 210), (40, 200))]
    clip = dash(base, top, -75.0)
    s = rig()
    source = travel.carry(pack(clip), s)  # the travel half alone: the turn below
    swapped = fu.Anim([[travel._carried(clip, 0, 1)], []])
    (turn,) = travel.turns(source, s).values()
    for frames, tol in ((travel._grid(40), 0.2), (np.arange(0, 40.25, 0.5), 1.0)):
        want = engine_pose(swapped.streams[0][0], frames)
        got = turned_by(engine_pose(source.streams[0][0], frames), turn, frames)
        assert np.abs(got - want).max() < tol


@pytest.mark.parametrize(("name", "between"), [("zinogre", 10.0), ("brute_tigrex", 0.2)])
def test_port_carries_its_turns_exactly(data, monkeypatch, name, between):
    """Every clip: the source pose == the carried one turned by its curve, within 0.2 units on
    the keys (the engine's cursor at speed 2), `between` off them (a tumble's sudden twist);
    the travel within 0.2 on the keys; a clip that does not turn is left as it was."""
    m = manifest.load(PORTS / f"{name}.toml")
    d, h = build.donor(m, data), build.host(m, data)
    rec = build.record_map(d, m.build)
    bind = build.binding(m.build, d, h, build.animated(m.build, rec, len(d.skeleton.bones)))
    with monkeypatch.context() as mp:
        mp.setattr(travel, "carry", lambda anim, _: anim)
        raw = build.animation(d, h, bind, rec, build.layout(m, d, h), m.build.ground_lift)
    s = bind.rig.skeleton
    r = travel.root(s)
    part = s.bones[r].stream
    joints = fk.part_joints([b.stream for b in s.bones])[part]
    streams = range(2 * part, 2 * part + 2)
    swapped = travel._each(
        raw, streams, lambda c: travel._carried(c, joints.index(0), joints.index(r))
    )
    carried = travel.carry(raw, s)
    turns = travel.turns(carried, s)
    frig = fk.Rig.from_skeleton(s)
    for e in motion.filled(raw):
        a, b = fk.rig_clip(swapped, e, s), fk.rig_clip(carried, e, s)
        assert a is not None and b is not None
        n = motion.frames(a)
        if e not in turns:
            si, slot = fk.entry_slot(part, e)
            assert carried.streams[si][slot] == swapped.streams[si][slot]
            continue
        for frames, tol in ((travel._grid(n), 0.2), (np.arange(0, n + 0.25, 0.5), between)):
            want = engine_pose(a, frames, s)
            got = turned_by(engine_pose(b, frames, s), turns[e], frames)
            assert np.abs(got - want).max() < tol, (e, tol)
        g = travel._grid(n)
        th = turns[e].at(g)[1:] / travel.TURN * math.tau
        src = fk.Curves(a, frig).at(g)[1][:, r][:, [0, 2]]
        step = np.diff(fk.Curves(b, frig).at(g)[1][:, r][:, [0, 2]], axis=0)
        moved = np.cumsum(
            np.stack(
                [
                    step[:, 0] * np.cos(th) + step[:, 1] * np.sin(th),
                    -step[:, 0] * np.sin(th) + step[:, 1] * np.cos(th),
                ],
                1,
            ),
            0,
        )
        assert np.abs(moved - (src[1:] - src[0])).max() < 0.2, e


def test_carry_is_idempotent():
    anim = pack(dash([loc(2, (0, 0), (40, 1000))], [HIP, loc(0, (0, -30), (40, 5))], 45.0))
    once = travel.carry(anim, rig())
    assert travel.carry(once, rig()).to_bytes() == once.to_bytes()


def test_carry_keeps_a_rooted_still_clip():
    clip = dash([], [loc(2, (0, 0), (40, 1000))])  # a native's: travel on the root, no turn
    assert travel.carry(pack(clip), rig()).streams[0][0] is clip


def test_carry_refuses_turning_joint0():
    turning = Track([rot(1, (0, 0), (100, 1))])
    with pytest.raises(ValueError, match="rotates"):
        travel.carry(pack(Clip([turning, Track([HIP]), Track(), Track()])), rig())


def test_carry_keeps_shared_clips_shared():
    clip = dash([loc(2, (0, 0), (40, 100))], [HIP], 30.0)
    after = travel.carry(pack(clip, clip), rig())
    assert after.streams[0][0] is after.streams[0][1]


def test_authored_turn():
    s = rig()
    still = travel.carry(pack(dash([], [loc(2, (0, 0), (40, 300))])), s)
    assert travel.turns(still, s) == {}
    (eased,) = travel.turns(still, s, {0: -90.0}).values()
    quarter = travel.TURN // 4
    assert (eased.data, eased.keys[0], eased.keys[-1], eased.keys[10]) == (
        0,
        0,
        -quarter,
        -quarter // 2,
    )
    turned = travel.carry(pack(dash([], [HIP], 40.0)), s)
    (more,) = travel.turns(turned, s, {0: 90.0}).values()
    assert more.data == pytest.approx(40 / 360 * travel.TURN, abs=3) and more.keys[-1] == quarter
    assert eased.at(travel._grid(40)).tolist() == list(eased.keys)
    assert eased.lua()[:8] == f"0000{eased.keys[1] & 0xFFFF:04x}" and len(eased.lua()) == 4 * 21


def test_cli(tmp_path, capsys):
    path = pac(tmp_path, pack(dash([loc(2, (0, 0), (40, 1000))], [HIP])))
    assert main(["travel", str(path), "--scale", "0.5"]) == 0
    out = capsys.readouterr().out
    assert "    0" in out and "500" in out and "joint 0" in out
    assert main(["travel", str(path), "--carry"]) == 0
    assert "joint 0" not in capsys.readouterr().out


def test_against_a_game_trace(tmp_path, capsys):
    anim = pack(dash([], [loc(2, (0, 0), (40, 1000))]))
    path = pac(tmp_path, anim)
    root_z = travel.path(anim, rig(), 0, [c - 2 for c in range(2, 22, 2)])[:, 1]
    frames = [
        observe.Frame(i / 30, 1, 4, 0.0, 0.0, 0.5 * z, 0, 0, 0, c, 2.0, 0, 0.5)
        for i, (c, z) in enumerate(zip(range(2, 22, 2), root_z, strict=True))
    ]
    csv = tmp_path / "game.csv"
    observe.write_frames(frames, csv)
    assert main(["travel", str(path), "--against", str(csv)]) == 0
    assert " 1.000 " in capsys.readouterr().out
