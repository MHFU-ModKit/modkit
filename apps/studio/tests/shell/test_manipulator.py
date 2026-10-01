# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

import numpy as np
import pytest
from mhfu_studio.shell.camera import OrbitCamera
from mhfu_studio.shell.input import Button, Pointer
from mhfu_studio.shell.manipulator import (
    ARM_PX,
    VIEW_RING,
    Handle,
    Manipulation,
    Manipulator,
    Operation,
    along,
    angle,
    axes,
    hit,
    ray_plane,
    rotation,
    snapped,
    world_per_px,
)
from mhfu_studio.shell.overlay import Ink, Recorder
from mhfu_studio.shell.workspace import Gesture

SIZE = (800, 600)
CAM = OrbitCamera()  # three-quarter view from distance 10


def arm(at: Sequence[float] = (0.0, 0.0, 0.0), cam: OrbitCamera = CAM) -> float:
    return ARM_PX * world_per_px(cam, np.asarray(at, dtype=np.float64), SIZE[1])


ARM = arm()
X, Y, Z = np.eye(3)
I4 = np.eye(4)


def px(p: Sequence[float], cam: OrbitCamera = CAM) -> tuple[float, float]:
    x, y, _ = cam.project(np.asarray(p, dtype=np.float64), SIZE)[0]
    return float(x), float(y)


def ev(kind: str, at: tuple[float, float], button: Button = Button.NONE) -> Pointer:
    held = Button.LEFT if kind in ("press", "move") and button != Button.NONE else Button.NONE
    return Pointer(kind, at[0], at[1], SIZE, button=button, buttons=held)


def pose(at: Sequence[float] = (0.0, 0.0, 0.0), linear: np.ndarray | None = None) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = at
    if linear is not None:
        m[:3, :3] = linear
    return m


def drag(
    g: Manipulator,
    op: Operation,
    a: tuple[float, float],
    b: tuple[float, float],
    m: np.ndarray = I4,
    cam: OrbitCamera = CAM,
    rec: Recorder | None = None,
    **kw: object,
) -> list[Manipulation]:
    """Press at `a`, move to `b` (painting into `rec`), release."""
    out = [g.pointer(ev("press", a, Button.LEFT), cam, m, op, **kw)]
    out.append(g.pointer(ev("move", b, Button.LEFT), cam, out[-1].matrix, op, **kw))
    if rec is not None:
        g.paint(rec, cam, SIZE, out[-1].matrix, op)
    out.append(g.pointer(ev("release", b, Button.LEFT), cam, out[-1].matrix, op, **kw))
    return out


# ---- math ---- #


def test_along() -> None:
    assert along(np.zeros(3), X, np.array([5.0, 10.0, 0.0]), -Y) == pytest.approx(5.0)
    assert along(np.zeros(3), X, Y, X) is None


def test_ray_plane() -> None:
    assert np.allclose(ray_plane(np.array([1.0, 5.0, 2.0]), -Y, np.zeros(3), Y), [1, 0, 2])
    assert ray_plane(Y, X, np.zeros(3), Y) is None
    assert ray_plane(Y, Y, np.zeros(3), Y) is None


def test_angle() -> None:
    assert angle(X, Y, Z) == pytest.approx(90.0)
    assert angle(Y, X, Z) == pytest.approx(-90.0)
    assert snapped(37.0, 15.0) == 30.0 and snapped(38.0, 15.0) == 45.0
    assert snapped(37.0, None) == 37.0


def test_axes_drop_scale() -> None:
    r = rotation(Y, 30.0)
    assert np.allclose(axes(pose(linear=r @ np.diag([2.0, 3.0, 4.0])), True), r.T)
    assert np.array_equal(axes(pose(linear=r), False), np.eye(3))


# ---- hit-testing ---- #


@pytest.mark.parametrize(
    ("op", "at", "want"),
    [
        ("translate", 0.7 * ARM * X, Handle("axis", 0)),
        ("translate", 0.7 * ARM * Y, Handle("axis", 1)),
        ("translate", 0.7 * ARM * Z, Handle("axis", 2)),
        ("translate", 0.375 * ARM * (Y + Z), Handle("plane", 0)),
        ("translate", 0.375 * ARM * (Z + X), Handle("plane", 1)),
        ("translate", 0.375 * ARM * (X + Y), Handle("plane", 2)),
        ("translate", np.zeros(3), Handle("view")),
        ("rotate", ARM * np.array([0.0, 0.6, 0.8]), Handle("axis", 0)),
        ("rotate", ARM * np.array([0.8, 0.0, 0.6]), Handle("axis", 1)),
        ("rotate", ARM * np.array([0.6, 0.8, 0.0]), Handle("axis", 2)),
        ("scale", 0.7 * ARM * X, Handle("axis", 0)),
        ("scale", ARM * Z, Handle("axis", 2)),
        ("scale", np.zeros(3), Handle("view")),
    ],
)
def test_hit(op: Operation, at: np.ndarray, want: Handle) -> None:
    assert hit(CAM, SIZE, I4, op, *px(at)) == want


def test_hit_view_ring() -> None:
    cx, cy = px((0.0, 0.0, 0.0))
    assert hit(CAM, SIZE, I4, "rotate", cx + VIEW_RING * ARM_PX, cy) == Handle("view")


@pytest.mark.parametrize("op", ["translate", "rotate", "scale"])
def test_miss(op: Operation) -> None:
    assert hit(CAM, SIZE, I4, op, 5.0, 5.0) is None


def test_press_off_handles() -> None:
    g = Manipulator()
    got = g.pointer(ev("press", (5.0, 5.0), Button.LEFT), CAM, I4, "translate")
    assert got.consumed == Gesture.NONE and not got.using and not got.over and not g.hot


def test_behind_camera() -> None:
    m = pose(CAM.eye - CAM.forward * 5.0)
    g = Manipulator()
    for x in range(0, SIZE[0], 40):
        for y in range(0, SIZE[1], 40):
            assert hit(CAM, SIZE, m, "translate", x, y) is None
    rec = Recorder()
    g.paint(rec, CAM, SIZE, m, "rotate")
    assert rec.calls == []


# ---- drags ---- #


def test_drag_x_snaps() -> None:
    g, rec = Manipulator(), Recorder()
    a, b = px(0.5 * ARM * X), px((0.5 * ARM + 2.3) * X)
    press, move, release = drag(g, "translate", a, b, rec=rec, snap=0.5)
    assert rec.texts() == ["X +2.5"]
    assert press.using and press.consumed == Gesture.ALL and np.array_equal(press.matrix, I4)
    assert move.using and move.consumed == Gesture.ALL
    assert release.done and not release.using
    assert np.allclose(release.matrix, pose((2.5, 0.0, 0.0)), atol=1e-9)
    assert not g.pointer(ev("move", (5.0, 5.0)), CAM, release.matrix, "translate").using


def test_plane_keeps_third() -> None:
    start = pose((0.0, 0.5, 0.0))
    a = start[:3, 3] + 0.375 * arm(start[:3, 3]) * (Z + X)
    *_, got = drag(Manipulator(), "translate", px(a), px(a + [3.0, 0.0, -2.0]), start)
    assert got.matrix[1, 3] == 0.5
    assert np.allclose(got.matrix[[0, 2], 3], [3.0, -2.0], atol=1e-6)


def test_view_plane_move() -> None:
    right = CAM.view[0, :3]
    *_, got = drag(Manipulator(), "translate", px((0.0, 0.0, 0.0)), px(right))
    assert np.allclose(got.matrix[:3, 3], right, atol=1e-6)


def test_rotate_snaps_to_90() -> None:
    pivot = np.array([0.4, 0.5, -0.3])
    start = pose(pivot)
    t = np.radians([53.13, 53.13 + 88.0])
    ring = [arm(pivot) * np.array([np.cos(a), np.sin(a), 0.0]) for a in t]
    rec = Recorder()
    a, b = px(pivot + ring[0]), px(pivot + ring[1])
    *_, got = drag(Manipulator(), "rotate", a, b, start, rec=rec, snap=15.0)
    assert rec.texts() == ["Z +90.0\N{DEGREE SIGN}"]
    assert np.allclose(got.matrix[:3, :3], [[0, -1, 0], [1, 0, 0], [0, 0, 1]], atol=1e-12)
    assert np.allclose(got.matrix[:3, 3], pivot)


def test_rotate_past_half_turn() -> None:
    g, rec = Manipulator(), Recorder()
    a = np.radians(53.13 + np.arange(0.0, 271.0, 30.0))
    pts = [px(ARM * np.array([np.cos(t), np.sin(t), 0.0])) for t in a]
    g.pointer(ev("press", pts[0], Button.LEFT), CAM, I4, "rotate")
    for p in pts[1:]:
        got = g.pointer(ev("move", p, Button.LEFT), CAM, I4, "rotate")
    g.paint(rec, CAM, SIZE, got.matrix, "rotate")
    assert rec.texts() == ["Z +270.0\N{DEGREE SIGN}"]


def test_rotate_edge_on() -> None:
    cam = OrbitCamera(yaw=0.0, pitch=0.0)
    cx, cy = px((0.0, 0.0, 0.0), cam)
    a = px(0.5 * arm(cam=cam) * X, cam)
    assert hit(cam, SIZE, I4, "rotate", *a) == Handle("axis", 1)
    *_, got = drag(Manipulator(), "rotate", a, (cx, cy - (a[0] - cx)), cam=cam, snap=15.0)
    assert np.allclose(got.matrix[:3, :3], rotation(Y, 90.0))


def test_scale_axis() -> None:
    rec = Recorder()
    *_, got = drag(
        Manipulator(), "scale", px(0.5 * ARM * X), px(0.81 * ARM * X), rec=rec, snap=0.25
    )
    assert rec.texts() == ["X \N{MULTIPLICATION SIGN}1.50"]
    assert np.allclose(got.matrix, pose(linear=np.diag([1.5, 1.0, 1.0])))


def test_uniform_scale() -> None:
    start = pose((1.0, 0.0, 0.0))
    c = px(start[:3, 3])
    *_, got = drag(Manipulator(), "scale", c, (c[0] + 100.0, c[1] + 30.0), start)
    assert np.allclose(got.matrix, pose((1.0, 0.0, 0.0), 2.0 * np.eye(3)))
    *_, got = drag(Manipulator(), "scale", c, (c[0] + 60.0, c[1]), start, snap=0.25)
    assert np.allclose(got.matrix[:3, :3], 1.5 * np.eye(3))


def test_local_axes() -> None:
    start = pose(linear=rotation(Y, 90.0))
    local_x = rotation(Y, 90.0) @ X
    assert np.allclose(local_x, -Z)
    a, b = px(0.5 * ARM * local_x), px((0.5 * ARM + 1.0) * local_x)
    *_, got = drag(Manipulator(), "translate", a, b, start, local=True)
    assert np.allclose(got.matrix[:3, 3], -Z, atol=1e-6)
    a, b = px(0.5 * ARM * X), px((0.5 * ARM + 1.0) * X)
    *_, got = drag(Manipulator(), "translate", a, b, start)
    assert np.allclose(got.matrix[:3, 3], X, atol=1e-6)


def test_cancel() -> None:
    g = Manipulator()
    start = pose((1.0, 2.0, 3.0))
    a = px(start[:3, 3] + 0.5 * arm(start[:3, 3]) * X)
    g.pointer(ev("press", a, Button.LEFT), CAM, start, "translate")
    g.pointer(ev("move", (a[0] + 40, a[1]), Button.LEFT), CAM, start, "translate")
    back = g.cancel()
    assert back is not None and np.array_equal(back, start) and g.cancel() is None
    got = g.pointer(ev("release", (a[0] + 40, a[1]), Button.LEFT), CAM, start, "translate")
    assert not got.done and not got.using and got.consumed == Gesture.NONE


# ---- paint ---- #


@pytest.mark.parametrize(
    ("op", "kinds"),
    [
        ("translate", {"line": 3, "polygon": 6, "circle": 1}),
        ("rotate", {"polyline": 6, "circle": 1}),
        ("scale", {"line": 3, "rect": 4}),
    ],
)
def test_paint_shapes(op: Operation, kinds: dict[str, int]) -> None:
    rec = Recorder()
    Manipulator().paint(rec, CAM, SIZE, I4, op)
    assert Counter(rec.kinds()) == kinds
    assert not any(Ink.HOT in c for c in rec.calls)


@pytest.mark.parametrize(
    ("op", "at", "hot"),
    [("translate", 0.7 * ARM * X, 2), ("rotate", ARM * np.array([0.6, 0.8, 0.0]), 2)],
)
def test_paint_hot(op: Operation, at: np.ndarray, hot: int) -> None:
    g = Manipulator()
    assert g.pointer(ev("move", px(at)), CAM, I4, op).over and g.hot
    rec = Recorder()
    g.paint(rec, CAM, SIZE, I4, op)
    assert sum(Ink.HOT in c for c in rec.calls) == hot
