# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The move player's step (move.cpp) on the host, one AI frame per call, against a host engine
(move_host.cpp) that lands pairs, installs clips and replaces reactions as em_vhook does; each
frame then advances the clip."""

import ctypes
import math
import struct
from collections.abc import Callable

import pytest
from mhfu import addresses as a
from mhfu import steering

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
        ("hold_script", ctypes.c_uint8),
        ("_pad", ctypes.c_uint8),
        ("start", ctypes.c_uint16),
        ("stop", ctypes.c_uint16),
    ]


assert ctypes.sizeof(Move) == a.MOVE.size


class Game:
    def __init__(self, lib: ctypes.CDLL) -> None:
        self.lib = lib
        self.mem = (ctypes.c_uint8 * 0x4000).from_address(lib.host_mem())
        self.hunter = (ctypes.c_uint8 * 0x300).from_address(lib.host_hunter())
        self.speed = SPEED

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
        """One AI frame: the step, then the clip advances at `speed`, held while the blend
        counts down, and stops at its end."""
        skip = int(self.lib.host_frame(ENT))
        for k in range(3):
            b = self.block(k)
            self.poke(b + a.CLIP_BLOCK.SPEED, "f", self.speed)  # MONSTER_STEP writes it every frame
            (phase,) = self.peek(b + a.CLIP_BLOCK.PHASE, "f")
            (end,) = self.peek(b + a.CLIP_BLOCK.END, "f")
            if self.peek(b + a.CLIP_BLOCK.FLAGS, "H")[0] & 1:
                (left,) = self.peek(b + a.CLIP_BLOCK.BLEND, "i")
                if left:
                    self.poke(b + a.CLIP_BLOCK.BLEND, "i", left - 1)
                    if left > 1:
                        continue
                phase = min(phase + self.speed, end)
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

    def steer_state(self, field: str, fmt: str = "f") -> tuple[float, ...]:
        base = int(self.lib.mhfu_move_state()) + a.MOVE_STATE.STEER
        return struct.unpack_from(
            "<" + fmt, ctypes.string_at(base + getattr(a.STEER_STATE, field), 8)
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
    lib.host_hunter.restype = ctypes.c_void_p
    lib.host_frame.restype = ctypes.c_uint32
    lib.host_frame.argtypes = [ctypes.c_uint32]
    lib.host_set.argtypes = [ctypes.c_int, ctypes.c_float]
    lib.host_blend.argtypes = [ctypes.c_int]
    lib.mhfu_move_state.restype = ctypes.c_void_p
    lib.mhfu_move_play.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    lib.mhfu_move_steer.argtypes = [ctypes.c_void_p]
    lib.mhfu_steer_init_spec.argtypes = [ctypes.c_void_p]
    lib.mhfu_move_react.argtypes = [ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
    lib.host_react_enter.argtypes = [ctypes.c_uint32] * 3
    lib.mhfu_move_target_pos.restype = ctypes.c_uint32
    lib.mhfu_move_target_pos.argtypes = [ctypes.c_uint32]
    return lib


@pytest.fixture
def g(lib: ctypes.CDLL) -> Game:
    lib.mhfu_move_init()
    lib.host_set(1, ctypes.c_float(CLIP_END))
    game = Game(lib)
    ctypes.memset(ctypes.addressof(game.mem), 0, 0x4000)
    ctypes.memset(ctypes.addressof(game.hunter), 0, 0x300)
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


@pytest.mark.parametrize("hold", [0, 1])
def test_the_back_pair_clears_the_wake_when_a_move_follows(g: Game, hold: int) -> None:
    """The hub's entry at the end wakes the AI script (here: it stays set); with hold_script the
    move player clears it, so the script cannot pick a pair before the next move starts."""
    g.play(46, length=10, back_main=0, back_sub=2, hold_script=hold)
    for _ in range(20):
        g.poke(a.ENTITY.SCRIPT_WAKE, "B", 1)
        g.frame()
        if ("E", ENT, 0, 2, 0) in g.calls():
            break
    else:
        raise AssertionError("the back pair was never entered")
    assert g.peek(a.ENTITY.SCRIPT_WAKE, "B") == (1 - hold,)


def test_length_enters_the_back_pair(g: Game) -> None:
    g.play(46, length=10, back_main=0, back_sub=2, back_mode=0)
    for _ in range(12):
        g.frame()
    assert ("E", ENT, 0, 2, 0) in g.calls()
    assert g.state("END", "B") == (BACK,) and g.state("END_FRAME") == (10,)


def test_start_is_the_executors_a3(g: Game) -> None:
    g.play(46, start=24)
    g.frame()
    g.frame()
    assert g.calls() == [("E", ENT, 0, 1, 0), ("M", ENT, 0, 0, 0), ("X", ENT, 46, 0, 24)]
    cursors = {g.peek(g.block(k) + a.CLIP_BLOCK.PHASE, "f") for k in range(3)}
    assert cursors == {(24.0 + SPEED,)}  # every part on the dispatch frame's step


SPANS = [(0, 60, 0, 2.0), (24, 86, 4, 2.0), (24, 86, 4, 2.8), (42, 102, 3, 2.8)]


@pytest.mark.parametrize(("start", "stop", "blend", "speed"), SPANS)
def test_stop_ends_the_move_at_a_clip_frame(
    g: Game, start: int, stop: int, blend: int, speed: float
) -> None:
    g.lib.host_blend(blend)
    g.speed = speed
    g.play(46, start=start, stop=stop)
    while g.state("STATE", "B")[0] != DONE:
        g.frame()
    held = blend + math.ceil((stop - start) / speed)  # the AI frame the cursor reaches stop
    assert g.state("END", "B") == (BACK,) and g.state("END_FRAME") == (held,)
    assert g.pair == (0, 1)  # the carrier again: its handler hands back


def test_stop_comes_before_a_longer_length_and_the_clips_end(g: Game) -> None:
    g.play(46, start=0, stop=100, length=200)
    while g.state("STATE", "B")[0] != DONE:
        g.frame()
    assert g.state("END_FRAME") == (50,)
    g.play(46, stop=300)  # past the clip's end
    while g.state("STATE", "B")[0] != 3:
        g.frame()
    assert g.state("END", "B") == (CLIP,)


def test_an_attack_before_start_never_spawns(g: Game) -> None:
    g.lib.host_blend(2)
    g.play(46, start=24, attacks=[(10, 3), (24, 5), (56, 6)])  # type: ignore[arg-type]
    for _ in range(30):
        g.frame()
    assert [c[3] for c in g.calls() if c[0] == "S"] == [5, 6]
    # the one at START on the first playing frame, with the cursor still held there
    assert g.state("SPAWN_FRAME", "III") == (NEVER, 1, 18)
    assert g.state("SPAWN_CURSOR", "fff") == (-1.0, 24.0, 56.0)


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


class Lunge(ctypes.Structure):
    _fields_ = [
        ("lo", ctypes.c_uint16),
        ("hi", ctypes.c_uint16),
        *[(k, ctypes.c_float) for k in ("near", "span", "base", "range")],
    ]


class Sample(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int16), ("z", ctypes.c_int16)]


class Spec(ctypes.Structure):
    _fields_ = [
        ("turn", ctypes.c_uint8),
        ("walls", ctypes.c_uint8),
        ("rate", ctypes.c_uint16),
        ("total", ctypes.c_int32),
        ("frames", ctypes.c_uint16),
        ("dir", ctypes.c_uint16),
        ("win_from", ctypes.c_uint16),
        ("win_to", ctypes.c_uint16),
        ("key_count", ctypes.c_uint16),
        ("stuck_main", ctypes.c_uint8),
        ("stuck_sub", ctypes.c_uint8),
        ("stuck_mode", ctypes.c_uint8),
        ("_pad", ctypes.c_uint8 * 3),
        ("keys", ctypes.c_uint16 * a.STEER_KEYS),
        ("sample_count", ctypes.c_uint16),
        ("lunge", Lunge),
        ("samples", Sample * a.STEER_SAMPLES),
    ]


Point = steering.Point


def spec_of(
    g: Game,
    keys: tuple[int, ...] = (),
    samples: tuple[Point, ...] = (),
    lunge: tuple[int, int, float, float, float, float] | None = None,
    **kw: int,
) -> Spec:
    """A spec: turn `keys`, travel `samples` (world units), a `lunge` as (lo, hi, near, span, base,
    range), and steer fields by name."""
    spec = Spec()
    g.lib.mhfu_steer_init_spec(ctypes.byref(spec))
    for k, v in kw.items():
        setattr(spec, k, v)
    spec.key_count = len(keys)
    for i, v in enumerate(keys):
        spec.keys[i] = v & 0xFFFF
    spec.sample_count = len(samples)
    for i, (x, z) in enumerate(samples):
        spec.samples[i].x, spec.samples[i].z = steering.sample(x), steering.sample(z)
    if lunge:
        spec.lunge = Lunge(*lunge)
    return spec


def steer(
    g: Game,
    keys: tuple[int, ...] = (),
    samples: tuple[Point, ...] = (),
    lunge: tuple[int, int, float, float, float, float] | None = None,
    **kw: int,
) -> None:
    """The spec the next move takes (spec_of's arguments)."""
    g.lib.mhfu_move_steer(ctypes.byref(spec_of(g, keys, samples, lunge, **kw)))


def yaw(g: Game) -> int:
    return int(g.peek(a.ENTITY.YAW, "H")[0])


def test_steer_spec_layout() -> None:
    assert ctypes.sizeof(Spec) == a.STEER_SPEC.size
    assert Spec.keys.offset == a.STEER_SPEC.KEYS and Spec.key_count.offset == a.STEER_SPEC.KEY_COUNT
    assert Spec.win_from.offset == a.STEER_SPEC.STEER + a.STEER_PARAMS.WIN_FROM
    assert Spec.stuck_main.offset == a.STEER_SPEC.STUCK_MAIN
    assert Spec.sample_count.offset == a.STEER_SPEC.SAMPLE_COUNT
    assert Spec.lunge.offset == a.STEER_SPEC.LUNGE and Spec.samples.offset == a.STEER_SPEC.SAMPLES
    assert ctypes.sizeof(Lunge) == a.STEER_LUNGE.size and Lunge.range.offset == a.STEER_LUNGE.RANGE
    assert ctypes.sizeof(Sample) == a.TRAVEL_SAMPLE.size and Sample.z.offset == a.TRAVEL_SAMPLE.Z


def test_yaw_follows_the_clip_turn(g: Game) -> None:
    """YAW reads its first-frame value plus the curve at the cursor, a loop's turn kept."""
    g.poke(a.ENTITY.YAW, "H", 0x1000)
    steer(g, keys=tuple(100 * k for k in range(int(CLIP_END) // 2 + 1)))
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


def test_the_script_is_quieted_on_the_hub_in_combat(g: Game) -> None:
    """The hub's entry after a reaction wakes the script, which would cut the carrier at once."""
    notice(g, 1)
    g.poke(a.ENTITY.SCRIPT_WAKE, "B", 1)
    g.play(46, carrier_sub=2)  # (0, 2)
    g.frame()
    assert g.calls()[0] == ("E", ENT, 0, 2, 0)
    assert g.peek(a.ENTITY.SCRIPT_WAKE, "B") == (0,)


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


CURVE: tuple[Point, ...] = ((0, 0), (-2, 10), (-3.25, 30), (0, 60), (4, 100.5), (6, 140))
"""x, z in the monster's frame at its first playing frame, every STEER_KEY_STEP clip frames."""


def world(yaw0: int, x: float, z: float) -> Point:
    """A vector in the frame of YAW `yaw0` as world axes: z forward, x its left."""
    th = yaw0 / 0x10000 * math.tau
    return x * math.cos(th) + z * math.sin(th), -x * math.sin(th) + z * math.cos(th)


def at(g: Game) -> Point:
    x, _, z = g.peek(a.ENTITY.POSITION, "fff")
    return x, z


def settle(g: Game, frames: int) -> list[tuple[float, Point]]:
    """(the cursor the step saw, the position it left) of each of `frames` AI frames from the
    first playing one; the frames before it leave the position alone."""
    out = []
    before = at(g)
    for _ in range(frames):
        g.frame()
        if int(g.state("FRAMES")[0]) == 0 or g.state("STATE", "B")[0] == 1:
            assert at(g) == before
            continue
        (cursor,) = g.steer_state("CURSOR")
        out.append((cursor, at(g)))
    return out


TRAVELS = [
    (2.0, 0, 1.0, 0, 0),
    (2.8, 0, 1.0, 0, 0),
    (2.0, 3, 1.5, 0x4000, 0),
    (2.8, 4, 0.75, 0xC123, 0),
    (2.0, 2, 1.25, 0x2000, 24),
    (2.8, 5, 1.0, 0x8000, 42),
]


@pytest.mark.parametrize(("speed", "blend", "size", "yaw0", "start"), TRAVELS)
def test_travel_is_the_curves_change_turned_and_scaled(
    g: Game, speed: float, blend: int, size: float, yaw0: int, start: int
) -> None:
    """After every frame the monster is where the curve's change since the move began, turned by
    YAW as it was then and scaled by its size, puts it; the blend's held cursor moves it not."""
    g.lib.host_blend(blend)
    g.speed = speed
    g.poke(a.ENTITY.YAW, "H", yaw0)
    g.poke(a.ENTITY.SIZE_SCALE, "f", size)
    g.poke(a.ENTITY.POSITION, "fff", 100.0, 7.0, -50.0)
    steer(g, samples=CURVE)
    g.play(46, start=start)
    seen = settle(g, 2 + blend + int((CLIP_END - start) / speed))
    assert len(seen) > 8
    for cursor, got in seen:
        x, z = steering.at(CURVE, cursor, start)
        ox, oz = steering.at(CURVE, start, start)
        dx, dz = world(yaw0, x - ox, z - oz)
        assert got == pytest.approx((100.0 + dx * size, -50.0 + dz * size), abs=0.02)
    held = [p for c, p in seen if c == start]
    assert (len(held) > 0) == (blend > 0) and all(p == (100.0, -50.0) for p in held)
    assert seen[-1][1] != (100.0, -50.0)
    assert g.peek(a.ENTITY.POSITION + 4, "f") == (7.0,)  # y is the floor's


def test_travel_turns_by_the_first_frames_yaw_not_the_live_one(g: Game) -> None:
    """The move's own turn changes YAW as it plays; the curve stays in the first frame's."""
    g.poke(a.ENTITY.SIZE_SCALE, "f", 1.0)
    steer(g, samples=((0, 0), (0, 100)), turn=3, total=0x4000, frames=10)
    g.play(46)
    seen = settle(g, 12)
    assert g.peek(a.ENTITY.YAW, "H")[0] == 0x4000
    assert seen[-1][1] == pytest.approx((0.0, 100.0), abs=0.02)  # along world +z, not the turn


def test_travel_keeps_the_resolution(g: Game) -> None:
    g.poke(a.ENTITY.SIZE_SCALE, "f", 1.0)
    steer(g, samples=((0, 0), (0.25, -0.75)))
    g.play(46)
    settle(g, 8)
    assert at(g) == pytest.approx((0.25, -0.75), abs=1e-6)


def test_a_wall_ahead_stops_the_travel_that_frame(g: Game) -> None:
    g.poke(a.ENTITY.SIZE_SCALE, "f", 1.0)
    steer(g, samples=CURVE, walls=1)
    g.play(46)
    seen = settle(g, 8)
    g.poke(a.ENTITY.WALL_SECTORS, "I", 0x40000000)
    g.frame()
    assert at(g) == seen[-1][1] and g.state("END", "B") == (WALL,)


def test_a_loop_carries_the_curve_on(g: Game) -> None:
    """A clip that loops back to its loop start does not undo the travel: the rest of the curve
    is travelled, then the part from the loop start again."""
    g.poke(a.ENTITY.SIZE_SCALE, "f", 1.0)
    steer(g, samples=((0, 0), (0, 10), (0, 20), (0, 30)))
    g.play(46)
    settle(g, 6)
    b = g.block(0)
    g.poke(b + a.CLIP_BLOCK.PHASE, "f", 0.0)  # the engine wrapped the cursor
    g.poke(b + a.CLIP_BLOCK.LOOP_START, "f", 0.0)
    g.frame()
    assert at(g) == pytest.approx((0.0, 30.0), abs=0.02)
    g.frame()
    assert at(g)[1] == pytest.approx(30.0 + 10.0, abs=0.02)  # the cursor is at 2 again


LUNGE = (10, 40, 400.0, 1800.0, -700.0, 1800.0)
"""window (lo, hi], near, span, base, range: MHP3rd's stamp jump."""


def lunge_total(d: float, size: float = 1.0) -> float:
    """The lunge's length at distance d: its four numbers are times the size."""
    lo, hi, near, span, base, rng = LUNGE
    near, span = near * size, span * size
    return 0.0 if d < near else (base + rng * min((d - near) / span, 1)) * size


@pytest.mark.parametrize("d", [150.0, 300.0, 399.0, 400.0, 1300.0, 2200.0, 3500.0])
@pytest.mark.parametrize(
    ("bearing", "yaw0", "size"), [(0.4, 0, 1.0), (-2.5, 0x6000, 2.0), (1.1, 0x2000, 0.5)]
)
def test_lunge_is_the_distances_length_along_the_bearing(
    g: Game, d: float, bearing: float, yaw0: int, size: float
) -> None:
    """Spread over its window by the cursor, along the bearing to the hunter at the first playing
    frame in world axes, whatever YAW is; near, span, base and range times the size."""
    ex, ez = 10.0, -20.0
    ux, uz = math.sin(bearing), math.cos(bearing)
    g.poke(a.ENTITY.POSITION, "fff", ex, 0.0, ez)
    g.poke(a.ENTITY.YAW, "H", yaw0)
    g.poke(a.ENTITY.SIZE_SCALE, "f", size)
    struct.pack_into("<fff", g.hunter, a.ENTITY.POSITION, ex + ux * d, 0.0, ez + uz * d)
    steer(g, lunge=LUNGE)
    g.play(46)
    seen = settle(g, 2 + int(CLIP_END / SPEED))
    total, (lo, hi, *_) = lunge_total(d, size), LUNGE
    for cursor, (x, z) in seen:
        share = min(max((cursor - lo) / (hi - lo), 0.0), 1.0)
        assert (x, z) == pytest.approx((ex + ux * total * share, ez + uz * total * share), abs=0.02)
    assert abs(seen[0][1][0] - ex) + abs(seen[0][1][1] - ez) < 1e-3  # nothing before the window
    assert seen[-1][0] > hi


def test_lunge_takes_the_hunter_where_the_move_began(g: Game) -> None:
    g.poke(a.ENTITY.SIZE_SCALE, "f", 1.0)
    g.poke(a.ENTITY.POSITION, "fff", 0.0, 0.0, 0.0)
    struct.pack_into("<fff", g.hunter, a.ENTITY.POSITION, 0.0, 0.0, 1000.0)
    steer(g, lunge=LUNGE)
    g.play(46)
    settle(g, 3)
    struct.pack_into("<fff", g.hunter, a.ENTITY.POSITION, 1000.0, 0.0, 0.0)  # steps aside
    settle(g, 40)
    total = lunge_total(1000.0)
    assert at(g) == pytest.approx((0.0, total), abs=0.02)


@pytest.mark.parametrize("speed", [2.0, 2.8])
def test_travel_and_lunge_add(g: Game, speed: float) -> None:
    g.speed = speed
    g.poke(a.ENTITY.SIZE_SCALE, "f", 2.0)
    struct.pack_into("<fff", g.hunter, a.ENTITY.POSITION, 0.0, 0.0, 1300.0)
    steer(g, samples=CURVE, lunge=LUNGE)
    g.play(46)
    settle(g, 2 + int(CLIP_END / speed))
    x, z = world(0, *CURVE[-1])
    assert at(g) == pytest.approx((x * 2.0, z * 2.0 + lunge_total(1300.0, 2.0)), abs=0.05)


FIXED_WINDOW = (10, 40)


@pytest.mark.parametrize("speed", [2.0, 2.8])
@pytest.mark.parametrize("total", [0x2000, -0x2A55, 7])
def test_fixed_turn_spreads_across_its_window_by_the_cursor(
    g: Game, speed: float, total: int
) -> None:
    """None before the window, the share of it the cursor has covered inside, all of it after:
    the cumulative rounding makes the sum exact whatever the cursor's step."""
    lo, hi = FIXED_WINDOW
    g.speed = speed
    g.poke(a.ENTITY.YAW, "H", 0x1000)
    steer(g, turn=3, total=total, win_from=lo, win_to=hi)
    g.play(46)
    for _ in range(2 + int(CLIP_END / speed)):
        g.frame()
        if int(g.state("FRAMES")[0]) == 0:
            continue
        (cursor,) = g.steer_state("CURSOR")
        share = min(max((cursor - lo) / (hi - lo), 0.0), 1.0)
        turned = (int(g.peek(a.ENTITY.YAW, "H")[0]) - 0x1000 + 0x8000) % 0x10000 - 0x8000
        assert abs(turned - total * share) <= 1
    assert yaw(g) == (0x1000 + total) % 0x10000


def test_fixed_turn_without_a_window_still_runs_over_its_frames(g: Game) -> None:
    g.poke(a.ENTITY.YAW, "H", 0)
    steer(g, turn=3, total=0x1000, frames=8)
    g.play(46)
    for _ in range(2 + 6):
        g.frame()
    assert 0 < yaw(g) < 0x1000
    for _ in range(10):
        g.frame()
    assert yaw(g) == 0x1000


def test_a_move_without_travel_leaves_the_entity_as_it_was(g: Game) -> None:
    """Samples or a lunge past their counts are never read: the entity's bytes are what a spec
    without them leaves."""

    def run(junk: bool) -> bytes:
        g.lib.host_set(1, ctypes.c_float(CLIP_END))
        ctypes.memset(ctypes.addressof(g.mem), 0, 0x4000)
        g.pair = (1, 3)
        g.poke(a.ENTITY.SIZE_SCALE, "f", 1.5)
        g.poke(a.ENTITY.POSITION, "fff", 5.0, 6.0, 7.0)
        struct.pack_into("<fff", g.hunter, a.ENTITY.POSITION, 300.0, 0.0, 400.0)
        spec = Spec()
        g.lib.mhfu_steer_init_spec(ctypes.byref(spec))
        spec.turn, spec.rate, spec.walls = 1, 64, 1
        spec.key_count = 3
        spec.keys[:3] = (0, 0x400, 0x800)
        if junk:
            for k in range(a.STEER_SAMPLES):
                spec.samples[k].x, spec.samples[k].z = 1000 + k, -2000
            spec.lunge.lo, spec.lunge.near, spec.lunge.span = 1, 1.0, 1.0
            spec.lunge.base = spec.lunge.range = 900.0
        g.lib.mhfu_move_steer(ctypes.byref(spec))
        g.play(46)
        for _ in range(40):
            g.frame()
        return bytes(g.mem[ENT - BASE : ENT - BASE + 0x800])

    assert run(False) == run(True)


def test_the_lunge_halves_at_half_the_size(g: Game) -> None:
    """Far past its span the length is base + range, in world units times the size."""

    def lunged(size: float) -> float:
        g.lib.host_set(1, ctypes.c_float(CLIP_END))
        ctypes.memset(ctypes.addressof(g.mem), 0, 0x4000)
        g.pair = (1, 3)
        g.poke(a.ENTITY.SIZE_SCALE, "f", size)
        struct.pack_into("<fff", g.hunter, a.ENTITY.POSITION, 0.0, 0.0, 9000.0)
        steer(g, lunge=LUNGE)
        g.play(46)
        settle(g, 2 + int(CLIP_END / SPEED))
        return at(g)[1]

    full, half = lunged(1.0), lunged(0.5)
    assert full == pytest.approx(1100.0, abs=0.05) and half == pytest.approx(full / 2, abs=0.05)


# --- the target

TARGET = BASE + 0x2000  # a second entity in move_host.cpp's memory: what the monster aims at
HUNTER_POS = int(a.PLAYER_ENTITY) + int(a.ENTITY.POSITION)
TARGET_POS = TARGET + int(a.ENTITY.POSITION)


def aim_at(g: Game, x: float, z: float) -> None:
    """ENTITY.TARGET points at an entity standing at (x, z)."""
    struct.pack_into("<fff", g.mem, TARGET - BASE + a.ENTITY.POSITION, x, 0.0, z)
    g.poke(a.ENTITY.TARGET, "I", TARGET)


def hunter_at(g: Game, x: float, z: float) -> None:
    struct.pack_into("<fff", g.hunter, a.ENTITY.POSITION, x, 0.0, z)


def test_the_aim_is_the_target(g: Game) -> None:
    aim_at(g, 5.0, 6.0)
    assert g.lib.mhfu_move_target_pos(ENT) == TARGET_POS


@pytest.mark.parametrize("pointer", [0, 0x1234, int(a.USER_RAM_END)])
def test_the_aim_falls_back_to_the_hunter(g: Game, pointer: int) -> None:
    """No target, or one that is not game memory."""
    g.poke(a.ENTITY.TARGET, "I", pointer)
    assert g.lib.mhfu_move_target_pos(ENT) == HUNTER_POS


def test_the_turn_follows_the_target_every_frame(g: Game) -> None:
    """The hunter is ahead; the target to the left, then to the right: it turns after whoever
    ENTITY.TARGET holds that frame, the way the donor's turn refreshes its target."""
    hunter_at(g, 0.0, 1000.0)
    steer(g, turn=1, rate=0x400)
    g.play(46)
    aim_at(g, 1000.0, 0.0)
    for _ in range(2 + 0x4000 // 0x400):
        g.frame()
    assert yaw(g) == 0x4000
    aim_at(g, -1000.0, 0.0)
    for _ in range(0x8000 // 0x400):
        g.frame()
    assert yaw(g) == 0xC000
    g.poke(a.ENTITY.TARGET, "I", 0)  # the target is gone: the hunter, ahead
    for _ in range(0x4000 // 0x400):
        g.frame()
    assert yaw(g) == 0


def test_the_lunge_goes_to_the_target(g: Game) -> None:
    g.poke(a.ENTITY.SIZE_SCALE, "f", 1.0)
    hunter_at(g, 0.0, 3000.0)
    aim_at(g, 3000.0, 0.0)
    steer(g, lunge=LUNGE)
    g.play(46)
    settle(g, 3)
    aim_at(g, 0.0, -3000.0)  # read at the first frame only
    settle(g, 40)
    assert at(g) == pytest.approx((lunge_total(3000.0), 0.0), abs=0.02)


# --- the turn and its rate


@pytest.mark.parametrize(("speed", "rate"), [(2.0, 1000), (2.8, 1400), (1.0, 500)])
def test_the_turn_rate_is_the_donors_at_clip_speed_1(g: Game, speed: float, rate: int) -> None:
    """A move's rate is its units a frame at speed 1; the move player times it by the clip's
    speed / 2 (1.0 calm, 1.4 enraged)."""
    g.speed = speed
    hunter_at(g, 1000.0, 0.0)  # 0x4000 to the left
    steer(g, turn=1, rate=1000)
    g.play(46)
    steps = []
    last = 0
    for _ in range(2 + 0x4000 // rate + 3):
        g.frame()
        steps.append(yaw(g) - last)
        last = yaw(g)
    turning = [d for d in steps if d]
    assert turning[:-1] == [rate] * (len(turning) - 1) and 0 < turning[-1] <= rate
    assert yaw(g) == 0x4000


def test_the_hunter_turn_turns_base_and_the_curve_rides_on_top(g: Game) -> None:
    """The entity's own YAW is what turns toward the target; the clip's facing is added to it, so
    the body ends facing the target plus the clip's last key."""
    keys = tuple(0x80 * k for k in range(int(CLIP_END) // 2 + 1))
    hunter_at(g, 1000.0, 1000.0)  # 0x2000
    steer(g, keys=keys, turn=1, rate=0x400)
    g.play(46)
    for _ in range(2 + int(CLIP_END / SPEED)):
        g.frame()
    assert g.steer_state("BASE", "H") == (0x2000,)
    assert yaw(g) == 0x2000 + keys[-1]
