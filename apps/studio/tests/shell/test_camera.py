# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import numpy as np
import pytest
from mhfu_studio.shell.camera import (
    Bounds,
    Lens,
    Limit,
    OrbitCamera,
    gl_bytes,
    look_at,
    perspective,
)

BOX = Bounds(np.array([0.0, 0.0, 0.0]), np.array([1000.0, 300.0, 1000.0]))
MAPLIKE = Lens(
    views={"iso": (45.0, 35.0)},
    view="iso",
    fov=45.0,
    distance=10_000.0,
    max_pitch=89.5,
    near=Limit(distance=0.002, floor=1.0),
    far=Limit(scale=20.0, distance=60.0, floor=1000.0),
    closest=Limit(floor=1.0),
)


def test_frame_fits_the_box() -> None:
    cam = OrbitCamera().frame(BOX)
    corners = np.array([[x, y, z] for x in (0, 1000) for y in (0, 300) for z in (0, 1000)])
    px = cam.project(corners, (800, 500))
    assert (px[:, 0] >= 0).all() and (px[:, 0] <= 800).all()
    assert (px[:, 1] >= 0).all() and (px[:, 1] <= 500).all()
    assert np.allclose(cam.target, BOX.center)


def test_ray_through_a_projected_point() -> None:
    cam = OrbitCamera(MAPLIKE).frame(BOX)
    p = np.array([400.0, 100.0, 600.0])
    x, y, depth = cam.project(p, (800, 500))[0]
    assert 0 <= depth <= 1
    o, d = cam.ray(x, y, (800, 500))
    assert np.linalg.norm(o + d * np.dot(p - o, d) - p) < 1.0


def test_lens_limits() -> None:
    cam = OrbitCamera(MAPLIKE)
    cam.distance = 100.0
    assert cam.near == 1.0 and cam.far == max(6000.0, 20.0 * 10_000.0)
    cam.dolly(1000)
    assert cam.distance == 1.0
    cam.fly_to((1.0, 2.0, 3.0), distance=0.0)
    assert cam.distance == 1.0 and np.allclose(cam.target, (1, 2, 3))
    turntable = OrbitCamera().frame(Bounds.of([(0, 0, 0), (2, 2, 2)]))
    turntable.dolly(10_000)
    assert turntable.distance == pytest.approx(turntable.scale * 1e-3)


def test_angles() -> None:
    cam = OrbitCamera(MAPLIKE)
    assert (cam.yaw, cam.pitch) == (45.0, 35.0)
    cam.pitch = 120.0
    assert cam.pitch == 89.5
    cam.orbit(-1000.0, 0.0)
    assert 0 <= cam.yaw < 360
    with pytest.raises(KeyError, match="iso"):
        cam.look("front")
    cam.look("iso")
    assert np.allclose(cam.forward, -cam.direction)


def test_matrices() -> None:
    m = look_at((0.0, 0.0, 5.0), (0.0, 0.0, 0.0))
    assert np.allclose(m @ [0, 0, 0, 1], [0, 0, -5, 1])
    assert np.isfinite(look_at((0.0, 5.0, 0.0), (0.0, 0.0, 0.0))).all()
    with pytest.raises(ValueError):
        look_at((1.0, 1.0, 1.0), (1.0, 1.0, 1.0))
    with pytest.raises(ValueError):
        perspective(40.0, 1.0, 10.0, 1.0)
    p = np.arange(16, dtype=np.float64).reshape(4, 4)
    assert np.frombuffer(gl_bytes(p), "f4")[1] == 4.0


def test_bounds() -> None:
    assert Bounds.of(np.zeros((0, 3))).radius == 0.0
    u = Bounds.union(None, Bounds.of([(0, 0, 0)]), Bounds.of([(2, 0, 0)]))
    assert u.radius == 1.0 and np.allclose(u.center, (1, 0, 0))
