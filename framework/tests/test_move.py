# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The move player's step (move.cpp) on the host, one AI frame per call, against a host engine
(move_host.cpp) that lands pairs and installs clips; each frame then advances the clip."""

import ctypes
import struct
from collections.abc import Callable

import pytest
from mhfu import addresses as a

BASE = 0x09000000  # move_host.cpp's memory; noaddr
ENT = BASE + 0x1000
CLIP_END = 228.0
SPEED = 2.0
NO_PAIR = 0xFF
CLIP, BACK, PAIR, STOPPED, REPLACED, REFUSED, LOST = range(1, 8)
DONE = 4
NEVER = 0xFFFF_FFFF
NODE6 = BASE + 0x3000 + 0x600  # move_host.cpp's node for attack 6


class Move(ctypes.Structure):
    _fields_ = [
        ("entry", ctypes.c_uint16),
        ("length", ctypes.c_uint16),
        ("carrier_main", ctypes.c_uint8),
        ("carrier_sub", ctypes.c_uint8),
        ("back_main", ctypes.c_uint8),
        ("back_sub", ctypes.c_uint8),
        ("back_mode", ctypes.c_uint8),
        ("skip", ctypes.c_uint8),
        ("part", ctypes.c_uint8),
        ("attack_count", ctypes.c_uint8),
        ("attacks", ctypes.c_uint16 * 16),
        ("spawner", ctypes.c_uint32),
    ]


class Game:
    def __init__(self, lib: ctypes.CDLL) -> None:
        self.lib = lib
        self.mem = (ctypes.c_uint8 * 0x4000).from_address(lib.host_mem())

    def poke(self, off: int, fmt: str, *values: float) -> None:
        struct.pack_into("<" + fmt, self.mem, ENT - BASE + off, *values)

    def peek(self, off: int, fmt: str) -> tuple[float, ...]:
        return struct.unpack_from("<" + fmt, self.mem, ENT - BASE + off)

    @property
    def pair(self) -> tuple[int, int]:
        m, s = self.peek(a.ENTITY.MAIN_STATE, "BB")
        return int(m), int(s)

    @pair.setter
    def pair(self, ms: tuple[int, int]) -> None:
        self.poke(a.ENTITY.MAIN_STATE, "BB", *ms)

    def block(self, part: int) -> int:
        assert a.CLIP_BLOCK.size
        return int(a.ENTITY.CLIP_BLOCKS) + part * a.CLIP_BLOCK.size

    def frame(self) -> int:
        """One AI frame: the step, then the clip advances and stops at its end."""
        skip = int(self.lib.host_frame(ENT))
        for k in range(3):
            b = self.block(k)
            (phase,) = self.peek(b + a.CLIP_BLOCK.PHASE, "f")
            (end,) = self.peek(b + a.CLIP_BLOCK.END, "f")
            if self.peek(b + a.CLIP_BLOCK.FLAGS, "H")[0] & 1:
                phase = min(phase + SPEED, end)
                self.poke(b + a.CLIP_BLOCK.PHASE, "f", phase)
                if phase >= end:
                    self.poke(b + a.CLIP_BLOCK.FLAGS, "H", 0)
        return skip

    def calls(self) -> list[tuple[str, int, int, int, int]]:
        out = (ctypes.c_uint32 * (64 * 5))()
        n = self.lib.host_calls(out)
        return [(chr(out[5 * i]), *out[5 * i + 1 : 5 * i + 5]) for i in range(n)]

    def state(self, field: str, fmt: str = "I") -> tuple[float, ...]:
        base = int(self.lib.mhfu_move_state())
        return struct.unpack_from(
            "<" + fmt, ctypes.string_at(base + getattr(a.MOVE_STATE, field), 32)
        )

    def play(self, entry: int = 46, **kw: int) -> None:
        mv = Move(entry=entry, carrier_main=0, carrier_sub=1, back_main=NO_PAIR)
        for k, v in kw.items():
            if k == "attacks":
                continue
            setattr(mv, k, v)
        for i, (frame, id_, *end) in enumerate(kw.get("attacks", ())):  # type: ignore[attr-defined]
            mv.attacks[4 * i], mv.attacks[4 * i + 1] = frame, id_
            mv.attacks[4 * i + 2] = end[0] if end else 0
            mv.attack_count = i + 1
        assert self.lib.mhfu_move_play(ENT, ctypes.byref(mv)) == 1


@pytest.fixture(scope="module")
def lib(host_lib: Callable[..., ctypes.CDLL]) -> ctypes.CDLL:
    lib = host_lib("src/core/move.cpp", "tests/move_host.cpp")
    lib.host_mem.restype = ctypes.c_void_p
    lib.host_frame.restype = ctypes.c_uint32
    lib.host_frame.argtypes = [ctypes.c_uint32]
    lib.host_set.argtypes = [ctypes.c_int, ctypes.c_float]
    lib.mhfu_move_state.restype = ctypes.c_void_p
    lib.mhfu_move_play.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    return lib


@pytest.fixture
def g(lib: ctypes.CDLL) -> Game:
    lib.mhfu_move_init()
    lib.host_set(1, ctypes.c_float(CLIP_END))
    game = Game(lib)
    ctypes.memset(ctypes.addressof(game.mem), 0, 0x4000)
    game.pair = (1, 3)
    return game


def test_enters_the_carrier_then_plays_the_entry(g: Game) -> None:
    g.play(46)
    g.frame()
    assert g.calls() == [("E", ENT, 0, 1, 0)] and g.pair == (0, 1)
    g.frame()
    assert g.calls() == [("X", ENT, 46, 0, 0)]
    assert g.state("STATE", "B") == (2,)


def test_clip_end_hands_off_and_reads_the_successor(g: Game) -> None:
    g.play(46)
    frames = 0
    while g.state("STATE", "B")[0] != 3:
        g.frame()
        frames += 1
    assert frames == 2 + int(CLIP_END / SPEED)
    g.pair = (2, 2)  # what the carrier's handler picked
    g.frame()
    assert g.state("STATE", "B") == (DONE,) and g.state("END", "B") == (CLIP,)
    assert g.state("END_PAIR", "H") == (0x0202,)
    assert g.state("PEAK", "fff") == (CLIP_END,) * 3
    assert not g.lib.host_has_step()


def test_attack_spawns_when_the_cursor_crosses_its_frame(g: Game) -> None:
    g.play(46, attacks=[(56, 6)])  # type: ignore[arg-type]
    for _ in range(40):
        g.frame()
    spawns = [c for c in g.calls() if c[0] == "S"]
    assert spawns == [("S", int(a.TIGREX_ATTACK_SPAWN), ENT, 6, 0)]
    assert g.state("SPAWN_FRAME") == (28,) and g.state("SPAWN_CURSOR", "f") == (56.0,)
    assert g.state("SPAWN_NODE") == (NODE6,)
    assert g.state("ENDED_FRAME") == (NEVER,)  # no end frame: the node's own life


def test_a_reaction_ends_the_move(g: Game) -> None:
    g.play(46)
    for _ in range(10):
        g.frame()
    g.pair = (4, 1)
    g.frame()
    assert g.state("END", "B") == (PAIR,) and g.state("END_PAIR", "H") == (0x0401,)
    assert not g.lib.host_has_step()


def test_skip_takes_the_frame_except_on_a_hit(g: Game) -> None:
    g.play(46, skip=1)
    assert [g.frame() for _ in range(4)] == [0, 0, 1, 1]
    g.poke(a.ENTITY.HIT_PENDING, "B", 1)
    assert g.frame() == 0
    g.poke(a.ENTITY.HIT_PENDING, "B", 0)
    assert g.frame() == 1


def test_length_enters_the_back_pair(g: Game) -> None:
    g.play(46, length=10, back_main=0, back_sub=2, back_mode=0)
    for _ in range(12):
        g.frame()
    assert ("E", ENT, 0, 2, 0) in g.calls()
    assert g.state("END", "B") == (BACK,) and g.state("END_FRAME") == (10,)


def test_carrier_that_does_not_land_is_refused(g: Game) -> None:
    g.lib.host_set(0, ctypes.c_float(CLIP_END))
    g.play(46)
    g.frame()
    assert g.state("END", "B") == (REFUSED,) and g.state("END_PAIR", "H") == (0x0103,)


def test_a_lost_clip_ends_the_move(g: Game) -> None:
    g.play(46)
    for _ in range(5):
        g.frame()
    g.poke(g.block(0) + a.CLIP_BLOCK.NODE, "I", 0x5001)
    g.frame()
    assert g.state("END", "B") == (LOST,)


def test_stop_and_replace(g: Game) -> None:
    g.play(46)
    for _ in range(5):
        g.frame()
    g.play(53)
    g.frame()
    assert g.state("END", "B") == (0,) and g.state("STARTED") == (2,)
    g.lib.mhfu_move_stop()
    g.frame()
    assert g.state("END", "B") == (STOPPED,)
    assert not g.lib.host_has_step()


def test_window_ends_the_node_at_its_end_frame(g: Game) -> None:
    g.play(46, attacks=[(56, 6, 80)])  # type: ignore[arg-type]
    for _ in range(60):
        g.frame()
    ends = [c for c in g.calls() if c[0] == "K"]
    assert ends == [("K", NODE6, 0x1234, 0, 0)]
    assert g.state("ENDED_FRAME") == (40,) and g.state("ENDED_STATE", "B") == (2,)


def test_window_still_open_ends_with_the_move(g: Game) -> None:
    g.play(46, attacks=[(56, 6, 200)])  # type: ignore[arg-type]
    for _ in range(40):
        g.frame()
    g.pair = (4, 1)
    g.frame()
    assert [c[0] for c in g.calls()].count("K") == 1
    assert g.state("ENDED_FRAME") == (39,)


def test_node_that_ended_itself_is_left_alone(g: Game) -> None:
    g.play(46, attacks=[(56, 6, 80)])  # type: ignore[arg-type]
    for _ in range(35):
        g.frame()
    g.mem[NODE6 - BASE + a.ATTACK_NODE.STATE] = 0
    for _ in range(10):
        g.frame()
    assert not [c for c in g.calls() if c[0] == "K"]
    assert g.state("ENDED_STATE", "B") == (0,)


def test_node_no_longer_ours_is_left_alone(g: Game) -> None:
    g.play(46, attacks=[(56, 6, 80)])  # type: ignore[arg-type]
    for _ in range(35):
        g.frame()
    struct.pack_into("<I", g.mem, NODE6 - BASE, 0x5678)  # freed: the base class's vtable
    for _ in range(10):
        g.frame()
    assert not [c for c in g.calls() if c[0] == "K"]
    assert g.state("ENDED_STATE", "B") == (0xFF,)
