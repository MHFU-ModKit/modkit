# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A move's per-frame steering (steer.cpp) on the host."""

import ctypes
import math
import struct
from collections.abc import Callable

import pytest
from mhfu import addresses as a

TURN = 0x10000
GO, WALL, STUCK = 0, 1, 2
STILL, HUNTER, AWAY, FIXED = 0, 1, 2, 3


class Vec3(ctypes.Structure):
    _fields_ = [("x", ctypes.c_float), ("y", ctypes.c_float), ("z", ctypes.c_float)]


class Steer(ctypes.Structure):
    _fields_ = [
        ("turn", ctypes.c_uint8),
        ("walls", ctypes.c_uint8),
        ("rate", ctypes.c_uint16),
        ("total", ctypes.c_int32),
        ("frames", ctypes.c_uint16),
        ("dir", ctypes.c_uint16),
    ]


@pytest.fixture(scope="module")
def lib(host_lib: Callable[..., ctypes.CDLL]) -> ctypes.CDLL:
    lib = host_lib("src/core/steer.cpp")
    u16, u32 = ctypes.c_uint16, ctypes.c_uint32
    lib.mhfu_steer_bearing.restype = u16
    lib.mhfu_steer_bearing.argtypes = [ctypes.c_float, ctypes.c_float]
    lib.mhfu_steer_toward.restype = u16
    lib.mhfu_steer_toward.argtypes = [u16, u16, u16]
    lib.mhfu_steer_share.restype = ctypes.c_int32
    lib.mhfu_steer_share.argtypes = [ctypes.c_int32, u16, u16]
    lib.mhfu_steer_ahead.restype = u32
    lib.mhfu_steer_ahead.argtypes = [u16]
    lib.mhfu_steer_wall.argtypes = [u32, ctypes.c_uint8, u16]
    lib.mhfu_steer_step.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(Vec3),
        ctypes.POINTER(Steer),
        u16,
    ]
    return lib


def test_bearing(lib: ctypes.CDLL) -> None:
    for k in range(360):
        dx, dz = math.sin(math.radians(k + 0.3)) * 500, math.cos(math.radians(k + 0.3)) * 500
        want = round(math.atan2(dx, dz) / math.tau * TURN) % TURN
        got = lib.mhfu_steer_bearing(dx, dz)
        assert abs((got - want + TURN // 2) % TURN - TURN // 2) <= 1, k
    assert lib.mhfu_steer_bearing(0, 0) == 0


def test_toward(lib: ctypes.CDLL) -> None:
    assert lib.mhfu_steer_toward(0x8000, 0x9000, 64) == 0x8040
    assert lib.mhfu_steer_toward(0x8000, 0x7000, 64) == 0x7FC0
    assert lib.mhfu_steer_toward(0x8000, 0x8030, 64) == 0x8030
    assert lib.mhfu_steer_toward(0xFFF0, 0x0100, 64) == 0x0030  # the short way, across 0
    assert lib.mhfu_steer_toward(0x0000, 0x8000, 64) == 0x0040  # a half turn goes positive


def test_share_sums_to_total(lib: ctypes.CDLL) -> None:
    for total, frames in ((0x4000, 41), (-0x2AAA, 7), (5, 30)):
        shares = [lib.mhfu_steer_share(total, frames, f) for f in range(frames)]
        assert sum(shares) == total
        assert max(shares) - min(shares) <= 1
    assert lib.mhfu_steer_share(0x4000, 41, 41) == 0
    assert lib.mhfu_steer_share(TURN, 41, 0) == 0


def test_ahead(lib: ctypes.CDLL) -> None:
    assert lib.mhfu_steer_ahead(0) == 0xC0000003
    assert lib.mhfu_steer_ahead(0x4000) == 0x3C0
    assert lib.mhfu_steer_ahead(0xF800) == 0xE0000001


def test_wall(lib: ctypes.CDLL) -> None:
    assert lib.mhfu_steer_wall(0x80000000, 1, 0) == STUCK  # the Tigrex charge's (0,6)
    assert lib.mhfu_steer_wall(0x00000001, 1, 0) == STUCK
    assert lib.mhfu_steer_wall(0x40000000, 0, 0) == WALL
    assert lib.mhfu_steer_wall(0x00000100, 1, 0) == GO  # beside, not ahead
    assert lib.mhfu_steer_wall(0x00000100, 0, 0x4000) == WALL


def entity(yaw: int, pos: tuple[float, float, float], sectors: int = 0, stuck: int = 0):
    e = ctypes.create_string_buffer(0x800)
    struct.pack_into("<H", e, a.ENTITY.YAW, yaw)
    struct.pack_into("<3f", e, a.ENTITY.POSITION, *pos)
    struct.pack_into("<I", e, a.ENTITY.WALL_SECTORS, sectors)
    e[a.ENTITY.STUCK_WALL] = stuck
    return e


def yaw_of(e) -> int:
    return int(struct.unpack_from("<H", e, a.ENTITY.YAW)[0])


def test_step_turns_toward_the_hunter(lib: ctypes.CDLL) -> None:
    e = entity(0x0000, (0.0, 0.0, 0.0))
    hunter = Vec3(1000.0, 0.0, 0.0)  # bearing 0x4000
    p = Steer(turn=HUNTER, walls=1, rate=64)
    for f in range(10):
        assert lib.mhfu_steer_step(e, hunter, p, f) == GO
    assert yaw_of(e) == 640
    p.turn = AWAY
    assert lib.mhfu_steer_step(e, hunter, p, 0) == GO
    assert yaw_of(e) == 640 - 64


def test_step_fixed_turn(lib: ctypes.CDLL) -> None:
    e = entity(0xF000, (0.0, 0.0, 0.0))
    p = Steer(turn=FIXED, total=0x2000, frames=30)
    for f in range(30):
        assert lib.mhfu_steer_step(e, Vec3(), p, f) == GO
    assert yaw_of(e) == 0x1000


def test_step_stops_at_a_wall_before_turning(lib: ctypes.CDLL) -> None:
    e = entity(0x8000, (0.0, 0.0, 0.0), sectors=0x80000000, stuck=1)
    p = Steer(turn=FIXED, walls=1, total=0x1000, frames=4)
    assert lib.mhfu_steer_step(e, Vec3(), p, 0) == STUCK
    assert yaw_of(e) == 0x8000
    p.walls = 0
    assert lib.mhfu_steer_step(e, Vec3(), p, 0) == GO
    assert yaw_of(e) == 0x8400
