# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct
from pathlib import Path

import pytest
from mhfu.memory import Image
from mhfu_port import p3motion
from mhfu_port.p3motion import (
    ACCELERATION,
    VELOCITY,
    Curve,
    Lunge,
    Motion,
    Spread,
    X,
    Y,
    Z,
)

BASE = 0x0010_0000
KEYS = BASE + 0x100


def f(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def image(*curves: list[tuple[float, int, float, float]]) -> Image:
    """Curves back to back, each a header and its keys: the first at BASE."""
    blob = bytearray(0x400)
    at, table = 0, 0x100
    for keys in curves:
        struct.pack_into("<HhI", blob, at, len(keys), 0, BASE + table)
        for k in keys:
            struct.pack_into("<fiff", blob, table, *k)
            table += 16
        at += 8
    return Image(bytes(blob), BASE)


def test_a_curve_is_exact_on_its_keys_and_held_outside_them():
    c = Curve.read(image([(5.0, 10, 0.0, 0.0), (9.0, 20, 0.0, 0.0), (1.0, 40, 0.0, 0.0)]), BASE)
    assert [c.at(t) for t in (0, 10, 20, 40)] == [5.0, 5.0, 9.0, 1.0]
    assert c.at(-3) == 5.0 and c.at(99) == 1.0


def test_a_curve_between_keys_is_the_hermite_spline():
    flat = Curve.read(image([(0.0, 0, 0.0, 0.0), (8.0, 8, 0.0, 0.0)]), BASE)
    assert flat.at(4) == pytest.approx(4.0)
    assert flat.at(2) == pytest.approx(8 * (3 * 0.25**2 - 2 * 0.25**3))  # smoothstep
    line = Curve.read(image([(0.0, 0, 1.0, 1.0), (8.0, 8, 1.0, 1.0)]), BASE)
    assert [line.at(t) for t in (1, 2.5, 7)] == pytest.approx([1.0, 2.5, 7.0])
    swing = Curve.read(image([(0.0, 0, 0.0, 2.0), (0.0, 4, -2.0, 0.0)]), BASE)
    assert swing.at(2) == pytest.approx(2.0) and swing.at(1) > 0 and swing.at(3) > 0  # an arch


def test_a_curve_changes_over_the_last_step():
    c = Curve.read(image([(0.0, 0, 1.0, 1.0), (100.0, 100, 1.0, 1.0)]), BASE)
    assert c.delta(10, 2) == pytest.approx(2.0)
    assert c.delta(1, 2) == pytest.approx(1.0)  # from the start at the earliest
    assert c.delta(0, 2) == 0.0


def test_a_curve_that_claims_too_many_keys_is_refused():
    blob = bytearray(0x400)
    struct.pack_into("<HhI", blob, 0, p3motion.MAX_KEYS + 1, 0, KEYS)
    with pytest.raises(ValueError, match="claims"):
        Curve.read(Image(bytes(blob), BASE), BASE)


def motion() -> Motion:
    return Motion(Image(bytes(8), BASE), rest=0.0)


def at(m: Motion, off: int) -> float:
    return m.cells[off]


def test_the_clip_speed_cell_starts_at_one():
    assert at(motion(), p3motion.SPEED) == 1.0


def test_velocity_moves_and_then_accelerates():
    m = motion()
    m.cells.update({VELOCITY + 4: 10.0, ACCELERATION + 4: -3.0})
    m.call(p3motion.VEL_ACC, ())
    assert (at(m, Y), at(m, VELOCITY + 4)) == (10.0, 7.0)
    m.call(p3motion.VEL_ACC, ())
    assert (at(m, Y), at(m, VELOCITY + 4)) == (17.0, 4.0)
    m.call(p3motion.VEL_APPLY, ())
    assert at(m, Y) == 21.0


def test_the_yaw_turns_a_move_toward_its_left():
    m = motion()
    m.cells[VELOCITY + 8] = 100.0  # forward
    m.yaw = 0x4000  # a quarter turn the way YAW grows: toward +x
    m.call(p3motion.VEL_APPLY, ())
    assert (at(m, X), at(m, Z)) == pytest.approx((100.0, 0.0))
    m.call(p3motion.VEL_WORLD, ())  # unturned
    assert (at(m, X), at(m, Z)) == pytest.approx((100.0, 100.0))


def test_a_handler_turns_its_velocity_into_world_axes():
    m = motion()
    m.cells[VELOCITY + 8] = 5.0
    m.yaw = 0x8000
    m.call(p3motion.ROT_Y, ())
    assert (at(m, VELOCITY), at(m, VELOCITY + 8)) == pytest.approx((0.0, -5.0))


def test_a_lift_is_the_next_steps_height():
    m = motion()
    m.begin(0.0, 70.0, 2.0)
    m.call(p3motion.VEL_APPLY, ())
    m.call(p3motion.LIFT, (None,) * 4 + (f(25.0),))
    assert at(m, Y) == 25.0 and m.before_hand_over().y == 0.0
    m.begin(2.0, 70.0, 2.0)
    assert m.before_hand_over().y == 25.0


def test_zeroing_clears_velocity_and_acceleration():
    m = motion()
    m.cells.update({VELOCITY: 1.0, ACCELERATION + 8: 2.0})
    m.call(p3motion.VEL_ZERO, ())
    assert (at(m, VELOCITY), at(m, ACCELERATION + 8)) == (0.0, 2.0)
    m.call(p3motion.VEL_ACC_ZERO, ())
    assert at(m, ACCELERATION + 8) == 0.0


def spread_args(total: int, lo: float, hi: float) -> tuple[int | None, ...]:
    return (total & 0xFFFFFFFF, None, None, None, f(lo), f(hi))


def test_a_spread_turns_by_its_total_across_the_window_and_is_noted_once():
    m = motion()
    args = spread_args(-1200, 4.0, 16.0)  # 100 units a clip frame
    for cursor in range(0, 20, 2):
        m.begin(float(cursor), 70.0, 2.0)
        m.call(p3motion.YAW_SPREAD, args)
    assert m.yaw == -1200
    assert m.events == [Spread(-1200, (4.0, 16.0))]
    assert Spread(-16384, (0, 1)).degrees == -90.0


def test_a_spread_outside_its_window_turns_nothing():
    m = motion()
    m.begin(30.0, 70.0, 2.0)
    m.call(p3motion.YAW_SPREAD, spread_args(1000, 4.0, 16.0))
    assert m.yaw == 0


def test_a_curve_walk_moves_by_the_curve_in_the_yaw_it_began_in():
    img = image(
        [(0.0, 0, 1.0, 1.0), (100.0, 100, 1.0, 1.0)],
        [(0.0, 0, 3.0, 3.0), (300.0, 100, 3.0, 3.0)],
    )
    m = Motion(img, rest=0.0)
    m.yaw = 0x4000
    m.call(p3motion.CURVE_MOVE_INIT, ())
    m.yaw = 0  # the curve walk keeps the yaw it began at
    m.begin(10.0, 70.0, 2.0)
    m.call(p3motion.CURVE_MOVE, (BASE, 0, BASE + 8, 0, None, None, 0))
    assert at(m, VELOCITY + 4) == 0.0
    # x 2 and z 6 turned a quarter toward +x
    assert (at(m, X), at(m, Z)) == pytest.approx((6.0, -2.0))


def test_a_curve_delta_call_answers_the_curves_step():
    img = image([(0.0, 0, 1.0, 1.0), (100.0, 100, 1.0, 1.0)])
    m = Motion(img, rest=0.0)
    m.begin(10.0, 70.0, 2.0)
    assert m.call(p3motion.CURVE_DELTA, (BASE, None, None, None, f(10.0))) == pytest.approx(2.0)


def lunge_args(lo: float, hi: float, near: float, far: float, v0: float, v1: float):
    return (None, None, None, None, f(lo), f(hi), None, None, f(near), f(far), f(v0), f(v1))


def test_a_lunge_is_taken_down_as_its_constants():
    m = motion()
    m.call(p3motion.LUNGE, lunge_args(0, 32, 400, 2200, -700, 1100))
    assert m.events == [Lunge((0.0, 32.0), 400.0, 1800.0, -700.0, 1800.0)]


def test_what_the_model_cannot_follow_is_said():
    m = motion()
    m.call(p3motion.LUNGE, lunge_args(0, 32, 400, 2200, -700, 1100)[:11] + (None,))
    assert "lunge" in m.lost
    n = motion()
    del n.cells[VELOCITY]
    n.call(p3motion.VEL_APPLY, ())
    assert "0xB0" in n.lost
    k = motion()
    k.call(p3motion.CURVE_DELTA, (BASE + 0x300, None, None, None, f(1.0)))
    assert "curve" in k.lost


def test_clip_reads_answer_the_cursor_and_the_clips_end():
    m = motion()
    m.begin(12.0, 70.0, 2.0)
    assert [m.call(t, ()) for t in (p3motion.CURSOR, p3motion.CLIP_END, p3motion.SCALE)] == [
        12.0,
        70.0,
        1.0,
    ]
    m.begin(None, None, 2.0)
    assert m.call(p3motion.CURSOR, ()) is None


def test_the_zinogres_curves_give_the_numbers_the_donor_walks(mhp3rd_data: Path):
    """The somersault's three curves, read from the overlay, at the cursors the research took
    them at (units a frame at a step of 2): the first rows of its table."""
    from mhfu.files import Extracted
    from mhfu_port.brain.p3 import Species

    ex = Extracted.find(mhp3rd_data)
    found = Species.read(ex).slots[40][0]
    ovl = ex.overlay(found.file)
    x, y, z = (Curve.read(ovl, a) for a in (0x09DD9850, 0x09DD96E8, 0x09DD9580))  # noaddr: curves
    got = {t: (x.delta(t, 2), y.delta(t, 2), z.delta(t, 2)) for t in (2.0, 30.0, 38.0)}
    assert got[2.0] == pytest.approx((-7.6, 14.7, 27.1), abs=0.1)
    assert got[30.0] == pytest.approx((10.9, -31.2, 128.3), abs=0.1)
    assert got[38.0] == pytest.approx((16.3, -187.8, 31.8), abs=0.1)
