# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The move player's step (move.cpp) on the host, one AI frame per call, against a host engine
(move_host.cpp) that lands pairs, installs clips and replaces reactions as em_vhook does; each
frame then advances the clip."""

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
REACTION = 10
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
        ("host_attacks", ctypes.c_uint8),
        ("force", ctypes.c_uint8),
        ("_pad", ctypes.c_uint8 * 2),
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

    def react(self, entry: int = 59) -> None:
        mv = Move(entry=entry, carrier_main=0, carrier_sub=2, back_main=NO_PAIR)
        assert self.lib.mhfu_move_react(0, ENT, ctypes.byref(mv), None) == 1


@pytest.fixture(scope="module")
def lib(host_lib: Callable[..., ctypes.CDLL]) -> ctypes.CDLL:
    lib = host_lib(
        "src/core/move.cpp",
        "src/core/steer.cpp",
        "src/core/monster_events.cpp",
        "tests/move_host.cpp",
    )
    lib.host_mem.restype = ctypes.c_void_p
    lib.host_frame.restype = ctypes.c_uint32
    lib.host_frame.argtypes = [ctypes.c_uint32]
    lib.host_set.argtypes = [ctypes.c_int, ctypes.c_float]
    lib.mhfu_move_state.restype = ctypes.c_void_p
    lib.mhfu_move_play.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    lib.mhfu_move_steer.argtypes = [ctypes.c_void_p]
    lib.mhfu_steer_init_spec.argtypes = [ctypes.c_void_p]
    lib.mhfu_move_react.argtypes = [ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
    lib.host_react_enter.argtypes = [ctypes.c_uint32] * 3
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
    assert g.calls() == [("E", ENT, 0, 1, 0), ("M", ENT, 0, 0, 0)] and g.pair == (0, 1)
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
    assert g.lib.host_has_step()  # it stays, for the monster events


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


def test_host_events_are_muted_while_the_move_plays(g: Game) -> None:
    g.play(46)
    while g.state("STATE", "B")[0] != 3:
        g.frame()
    mutes = [c[1] for c in g.calls() if c[0] == "M"]
    assert mutes == [ENT, 0]


def test_host_attacks_keeps_them(g: Game) -> None:
    g.play(46, host_attacks=1)
    for _ in range(5):
        g.frame()
    g.pair = (4, 1)
    g.frame()
    assert [c[1] for c in g.calls() if c[0] == "M"] == [0]  # only the end's clear


WALL, STUCK = 8, 9


class Spec(ctypes.Structure):
    _fields_ = [
        ("turn", ctypes.c_uint8),
        ("walls", ctypes.c_uint8),
        ("rate", ctypes.c_uint16),
        ("total", ctypes.c_int32),
        ("frames", ctypes.c_uint16),
        ("dir", ctypes.c_uint16),
        ("key_count", ctypes.c_uint16),
        ("stuck_main", ctypes.c_uint8),
        ("stuck_sub", ctypes.c_uint8),
        ("stuck_mode", ctypes.c_uint8),
        ("_pad", ctypes.c_uint8 * 3),
        ("keys", ctypes.c_uint16 * 256),
    ]


def steer(g: Game, keys: tuple[int, ...] = (), **kw: int) -> None:
    spec = Spec()
    g.lib.mhfu_steer_init_spec(ctypes.byref(spec))
    for k, v in kw.items():
        setattr(spec, k, v)
    spec.key_count = len(keys)
    for i, v in enumerate(keys):
        spec.keys[i] = v & 0xFFFF
    g.lib.mhfu_move_steer(ctypes.byref(spec))


def yaw(g: Game) -> int:
    return int(g.peek(a.ENTITY.YAW, "H")[0])


def test_steer_spec_layout() -> None:
    assert ctypes.sizeof(Spec) == a.STEER_SPEC.size
    assert Spec.keys.offset == a.STEER_SPEC.KEYS and Spec.key_count.offset == a.STEER_SPEC.KEY_COUNT


def test_yaw_follows_the_clip_turn(g: Game) -> None:
    """YAW reads its first-frame value plus the curve at the cursor, a loop's turn kept."""
    g.poke(a.ENTITY.YAW, "H", 0x1000)
    steer(g, tuple(100 * k for k in range(int(CLIP_END) // 2 + 1)))
    g.play(46)
    g.frame()
    g.frame()  # the dispatch
    g.frame()  # first playing frame: the cursor reads 2, YAW stays
    assert yaw(g) == 0x1000
    for _ in range(10):
        g.frame()
    assert yaw(g) == 0x1000 + 10 * 100
    st = int(g.lib.mhfu_move_state()) + a.MOVE_STATE.STEER
    assert struct.unpack_from("<H", ctypes.string_at(st + a.STEER_STATE.YAW0, 2)) == (0x1000,)


def test_fixed_turn_spreads_its_total(g: Game) -> None:
    g.poke(a.ENTITY.YAW, "H", 0xF000)
    steer(g, turn=3, total=0x2000, frames=20)
    g.play(46)
    for _ in range(2 + 25):
        g.frame()
    assert yaw(g) == 0x1000


def test_a_wall_ahead_ends_the_move(g: Game) -> None:
    steer(g, walls=1)
    g.play(46)
    for _ in range(6):
        g.frame()
    g.poke(a.ENTITY.WALL_SECTORS, "I", 0x40000000)
    g.frame()
    g.frame()
    assert g.state("END", "B") == (WALL,) and g.state("STATE", "B") == (DONE,)


def test_a_class_2_wall_enters_the_stuck_pair_and_quiets_the_script(g: Game) -> None:
    steer(g, walls=1)
    g.play(46)
    for _ in range(6):
        g.frame()
    g.poke(a.ENTITY.WALL_SECTORS, "I", 0x00000001)
    g.poke(a.ENTITY.STUCK_WALL, "B", 1)
    g.poke(a.ENTITY.SCRIPT_WAKE, "B", 1)
    g.calls()
    g.frame()
    assert ("E", ENT, 0, 6, 1) in g.calls() and g.pair == (0, 6)
    assert g.state("END", "B") == (STUCK,) and g.peek(a.ENTITY.SCRIPT_WAKE, "B") == (0,)


def test_walls_off_or_beside_do_not_end_it(g: Game) -> None:
    steer(g, walls=1)
    g.play(46)
    for _ in range(6):
        g.frame()
    g.poke(a.ENTITY.WALL_SECTORS, "I", 0x00000100)  # beside, not ahead
    for _ in range(4):
        g.frame()
    assert g.state("STATE", "B") == (2,)


def notice(g: Game, combat: int) -> None:
    g.poke(a.ENTITY.AWARE, "B", 1)
    g.poke(a.ENTITY.COMBAT_MODE, "B", combat)


def test_a_move_waits_for_the_notice(g: Game) -> None:
    notice(g, 0)
    g.play(46)
    for _ in range(5):
        g.frame()
    assert g.calls() == [] and g.state("WAITED") == (5,)
    notice(g, 1)
    g.frame()
    assert g.calls()[0] == ("E", ENT, 0, 1, 0)


def test_a_move_waits_for_the_cut_forced_or_not(g: Game) -> None:
    g.lib.host_cut(1)
    g.play(46, force=1)
    for _ in range(5):
        g.frame()
    assert g.calls() == [] and g.state("WAITED") == (0,)
    g.lib.host_cut(0)
    g.frame()
    assert g.calls()[0] == ("E", ENT, 0, 1, 0)


def test_a_forced_move_does_not_wait(g: Game) -> None:
    notice(g, 0)
    g.poke(a.ENTITY.SCRIPT_WAKE, "B", 1)
    g.play(46, force=1)
    g.frame()
    assert g.calls()[0] == ("E", ENT, 0, 1, 0)
    assert g.peek(a.ENTITY.SCRIPT_WAKE, "B") == (0,)  # the notice's pair would cut the carrier


def test_the_script_keeps_its_wake_in_combat(g: Game) -> None:
    notice(g, 1)
    g.poke(a.ENTITY.SCRIPT_WAKE, "B", 1)
    g.play(46)
    g.frame()
    assert g.peek(a.ENTITY.SCRIPT_WAKE, "B") == (1,)


def test_the_wait_has_an_end(g: Game) -> None:
    notice(g, 0)
    g.play(46)
    for _ in range(450):  # MHFU_MOVE_WAIT
        g.frame()
    assert g.calls() == []
    g.frame()
    assert g.calls()[0] == ("E", ENT, 0, 1, 0)


def flinch(g: Game, sub: int = 1, mask: int = 1) -> None:
    """The engine's flinch: REACTION_CHECK set FLINCH_MASK, then its reaction's enter-action ran
    and the host step ran the entered pair's phase 0."""
    g.poke(a.ENTITY.FLINCH_MASK, "B", mask)
    g.lib.host_react_enter(ENT, 4, sub)
    g.poke(a.ENTITY.PHASE, "B", 1)


def test_a_replaced_flinch_plays_the_move_next_frame(g: Game) -> None:
    g.react(59)
    g.play(46)
    for _ in range(5):
        g.frame()
    g.calls()
    flinch(g)
    assert g.pair == (0, 2)
    g.frame()
    assert g.calls() == [
        ("E", ENT, 0, 2, 2),
        ("M", 0, 0, 0, 0),
        ("M", ENT, 0, 0, 0),
        ("X", ENT, 59, 0, 0),
    ]
    assert g.state("END", "B") == (0,) and g.state("STATE", "B") == (2,)
    assert g.state("REACTIONS") == (1,) and g.state("REACT_PAIR") == (0x20401,)
    assert g.state("REACT_PARTS", "B") == (1,)


def test_a_reaction_ends_the_move_it_replaces(g: Game) -> None:
    g.react(59)
    g.play(46)
    for _ in range(5):
        g.frame()
    flinch(g)
    g.poke(a.ENTITY.PHASE, "B", 0)  # the carrier's phase 0 still to run
    g.frame()
    assert g.state("STATE", "B") == (1,) and g.state("STARTED") == (2,)
    g.frame()
    assert ("X", ENT, 59, 0, 0) in g.calls() and g.state("STATE", "B") == (2,)


def test_a_flinch_frame_is_needed(g: Game) -> None:
    g.react(59)
    flinch(g, mask=0)
    assert g.pair == (4, 1)
    flinch(g, sub=4)  # the tail cut keeps its own reaction
    assert g.pair == (4, 4)


def test_react_off(g: Game) -> None:
    g.react(59)
    assert g.lib.mhfu_move_react(0, ENT, None, None) == 1
    flinch(g)
    assert g.pair == (4, 1)
