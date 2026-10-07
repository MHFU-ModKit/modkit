# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""em_vhook's brain on the host with the real move player: own moves played by slot when asked,
after the move before, or by a rule; and the rules entering pairs. One AI frame per call
(mhfu_em_host_frame: the brain, then the C step), against em_host.cpp's engine."""

import ctypes
import struct
from collections.abc import Callable

import pytest
from mhfu import addresses as a

BASE = 0x09000000  # em_host.cpp's memory; noaddr
ENT = BASE + 0x1000
CLIP_END = 20.0
SPEED = 2.0
NO = ANY = 0xFF
UNLIMITED = 0xFFFF_FFFF
BACK, PAIR = 2, 3
PLAYING, DONE = 2, 4
NOTICED, FLINCH, BROKEN, TAIL = 1, 4, 5, 6


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


class Rule(ctypes.Structure):
    _fields_ = [
        ("from_mask", ctypes.c_uint8),
        ("from_sub", ctypes.c_uint8),
        ("to_main", ctypes.c_uint8),
        ("to_sub", ctypes.c_uint8),
        ("mode", ctypes.c_uint8),
        ("flags", ctypes.c_uint8),
        ("from_move", ctypes.c_uint8),
        ("play_move", ctypes.c_uint8),
        ("min_frames", ctypes.c_uint32),
        ("dist_lo", ctypes.c_float),
        ("dist_hi", ctypes.c_float),
        ("cooldown", ctypes.c_uint32),
        ("count", ctypes.c_uint32),
        ("on", ctypes.c_uint8),
        ("part", ctypes.c_uint8),
        ("force", ctypes.c_uint8),
        ("_pad", ctypes.c_uint8),
    ]


class Game:
    def __init__(self, lib: ctypes.CDLL) -> None:
        self.lib = lib
        self.mem = (ctypes.c_uint8 * 0x4000).from_address(lib.host_mem())
        self.hunter = (ctypes.c_uint8 * 0x300).from_address(lib.host_hunter())

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

    def hunter_at(self, x: float, z: float) -> None:
        struct.pack_into("<fff", self.hunter, a.ENTITY.TRANSLATION, x, 0.0, z)

    def frame(self) -> None:
        """One AI frame: the brain and the step, then each playing clip advances."""
        self.lib.mhfu_em_host_frame(ENT)
        assert a.CLIP_BLOCK.size
        for k in range(3):
            b = int(a.ENTITY.CLIP_BLOCKS) + k * a.CLIP_BLOCK.size
            (phase,) = self.peek(b + a.CLIP_BLOCK.PHASE, "f")
            (end,) = self.peek(b + a.CLIP_BLOCK.END, "f")
            if self.peek(b + a.CLIP_BLOCK.FLAGS, "H")[0] & 1:
                phase = min(phase + SPEED, end)
                self.poke(b + a.CLIP_BLOCK.PHASE, "f", phase)
                if phase >= end:
                    self.poke(b + a.CLIP_BLOCK.FLAGS, "H", 0)

    def frames(self, n: int) -> None:
        for _ in range(n):
            self.frame()

    def calls(self) -> list[tuple[str, int, int, int, int]]:
        out = (ctypes.c_uint32 * (64 * 5))()
        n = self.lib.host_calls(out)
        return [(chr(out[5 * i]), *out[5 * i + 1 : 5 * i + 5]) for i in range(n)]

    def entered(self) -> list[tuple[int, int]]:
        return [(c[2], c[3]) for c in self.calls() if c[0] == "E"]

    def move(self, field: str, fmt: str = "I") -> int:
        base = int(self.lib.mhfu_move_state())
        return int(
            struct.unpack_from("<" + fmt, ctypes.string_at(base + getattr(a.MOVE_STATE, field), 4))[
                0
            ]
        )

    def moves(self, field: str) -> int:
        base = int(self.lib.mhfu_em_moves())
        return int(
            struct.unpack_from("<I", ctypes.string_at(base + getattr(a.EM_MOVES, field), 4))[0]
        )

    def own(self, slot: int, entry: int, after: int = NO, keys: int = 0, **kw: int) -> int:
        mv = Move(entry=entry, carrier_main=0, carrier_sub=2, back_main=NO)
        for k, v in kw.items():
            setattr(mv, k, v)
        spec = ctypes.create_string_buffer(a.STEER_SPEC.size)
        self.lib.mhfu_steer_init_spec(spec)
        struct.pack_into("<H", spec, a.STEER_SPEC.KEY_COUNT, keys)
        return int(self.lib.mhfu_em_move(slot, ctypes.byref(mv), spec, after))

    def flinch(self, parts: int, sub: int = 1) -> None:
        """A host step whose REACTION_CHECK flinched `parts` and entered (4, sub); the next AI
        frame, after which the mask is cleared again."""
        self.poke(a.ENTITY.FLINCH_MASK, "B", parts)
        self.lib.mhfu_em_host_reaction(ENT, 4, sub)
        self.frame()
        self.poke(a.ENTITY.FLINCH_MASK, "B", 0)

    def rule(self, slot: int, **kw: float) -> None:
        r = Rule(from_sub=0xFE, dist_hi=1.0e9, count=UNLIMITED, part=ANY)
        for k, v in kw.items():
            setattr(r, k, v)
        self.lib.mhfu_em_rule(slot, ctypes.byref(r))


@pytest.fixture(scope="module")
def lib(host_lib: Callable[..., ctypes.CDLL]) -> ctypes.CDLL:
    lib = host_lib(
        "src/core/em_vhook.cpp",
        "src/core/move.cpp",
        "src/core/steer.cpp",
        "src/core/monster_events.cpp",
        "tests/em_host.cpp",
    )
    for name in ("host_mem", "host_hunter", "mhfu_move_state", "mhfu_em_moves"):
        getattr(lib, name).restype = ctypes.c_void_p
    lib.host_reset.argtypes = [ctypes.c_float]
    lib.mhfu_em_host_frame.argtypes = [ctypes.c_uint32]
    lib.mhfu_em_play.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_int]
    lib.mhfu_em_move.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint8]
    lib.mhfu_em_rule.argtypes = [ctypes.c_int, ctypes.c_void_p]
    lib.mhfu_steer_init_spec.argtypes = [ctypes.c_void_p]
    lib.mhfu_em_host_reaction.argtypes = [ctypes.c_uint32, ctypes.c_uint8, ctypes.c_uint8]
    return lib


@pytest.fixture
def g(lib: ctypes.CDLL) -> Game:
    lib.host_reset(ctypes.c_float(CLIP_END))
    lib.mhfu_em_init()
    game = Game(lib)
    game.pair = (1, 3)
    game.hunter_at(500.0, 0.0)
    return game


def test_play_asked_starts_that_frame(g: Game) -> None:
    assert g.own(0, 46) and g.lib.mhfu_em_play(ENT, 0, 0)
    g.frame()
    assert g.entered() == [(0, 2)] and g.lib.mhfu_em_playing() == 0
    g.frame()
    assert ("X", ENT, 46, 0, 0) in g.calls() and g.moves("PLAYS") == 1
    assert g.lib.mhfu_em_play(ENT, 1, 0) == 0  # an empty slot


def test_after_follows_through_the_carrier(g: Game) -> None:
    """A slot with an AFTER ends into its carrier, and the next plays on the following frame."""
    g.own(0, 46, after=1)
    g.own(1, 53)
    g.lib.mhfu_em_play(ENT, 0, 0)
    g.frames(2 + int(CLIP_END / SPEED))
    assert g.move("END", "B") == BACK and g.entered() == [(0, 2), (0, 2)]
    g.frame()
    g.frame()
    assert ("X", ENT, 53, 0, 0) in g.calls()
    assert (g.moves("CHAINED"), g.lib.mhfu_em_playing()) == (1, 1)


def test_after_does_not_follow_a_reaction(g: Game) -> None:
    g.own(0, 46, after=1)
    g.own(1, 53)
    g.lib.mhfu_em_play(ENT, 0, 0)
    g.frames(4)
    g.pair = (4, 1)
    g.frames(3)
    assert g.move("END", "B") == PAIR and g.moves("CHAINED") == 0


def test_a_back_pair_given_is_kept(g: Game) -> None:
    g.own(0, 46, after=1, back_main=0, back_sub=3)
    g.own(1, 53)
    g.lib.mhfu_em_play(ENT, 0, 0)
    g.frames(2 + int(CLIP_END / SPEED))
    assert g.entered()[-1] == (0, 3)


def test_rule_from_a_pair_plays_an_own_move(g: Game) -> None:
    g.own(2, 46)
    g.rule(0, from_mask=1 << 1, min_frames=3, dist_hi=600.0, play_move=3)
    g.frames(3)
    assert g.entered() == []
    g.frame()  # the pair has stood 3 frames: the move starts in that AI frame
    assert g.entered() == [(0, 2)] and g.lib.mhfu_em_playing() == 2


def test_rule_out_of_its_distance_waits(g: Game) -> None:
    g.own(2, 46)
    g.rule(0, from_mask=1 << 1, dist_hi=400.0, play_move=3)
    g.frames(5)
    assert g.entered() == []
    g.hunter_at(300.0, 0.0)
    g.frame()
    assert g.entered() == [(0, 2)]


def test_rule_from_an_own_move(g: Game) -> None:
    """While own move 0 has played 5 frames, rule 1 plays own move 1 over it."""
    g.own(0, 46)
    g.own(1, 53)
    g.rule(1, from_move=1, min_frames=5, play_move=2)
    g.lib.mhfu_em_play(ENT, 0, 0)
    g.frames(2 + 5)
    assert g.lib.mhfu_em_playing() == 0
    g.frame()
    assert g.move("STARTED") == 2 and g.lib.mhfu_em_playing() == 1


def test_pair_rules_wait_while_a_move_plays(g: Game) -> None:
    g.own(0, 46)
    g.rule(0, from_mask=1, to_main=3, to_sub=6)  # main 0: the carrier's
    g.lib.mhfu_em_play(ENT, 0, 0)
    g.frames(2 + int(CLIP_END / SPEED) - 1)
    assert (3, 6) not in g.entered()
    g.frames(3)
    assert (3, 6) in g.entered()


def test_rule_enters_a_pair(g: Game) -> None:
    g.rule(0, from_mask=1 << 1, from_sub=3, min_frames=2, to_main=3, to_sub=6, mode=1, count=1)
    g.frames(4)
    assert [c for c in g.calls() if c[0] == "E"] == [("E", ENT, 3, 6, 1)]
    g.pair = (1, 3)
    g.frames(5)
    assert g.entered() == []  # its one fire is spent


def test_one_fire_a_frame(g: Game) -> None:
    g.rule(0, from_mask=1 << 1, to_main=3, to_sub=6)
    g.rule(1, from_mask=1 << 1, to_main=2, to_sub=9)
    g.frame()
    assert g.entered() == [(3, 6)]


def test_receding_and_cooldown(g: Game) -> None:
    g.rule(0, from_mask=0xFF, flags=1, cooldown=10, to_main=1, to_sub=3)
    g.frames(12)
    assert g.entered() == []  # cooling down from the start, then the hunter stands
    g.hunter_at(600.0, 0.0)
    g.frame()
    assert g.entered() == [(1, 3)]
    for x in (700.0, 800.0, 900.0):
        g.hunter_at(x, 0.0)
        g.frame()
    assert g.entered() == []  # cooling down


def test_keys_have_room(g: Game) -> None:
    assert g.own(0, 46, keys=256)
    for slot in range(1, 8):
        assert g.own(slot, 46, keys=256)
    assert not g.own(8, 46, keys=1)
    g.lib.mhfu_em_moves_clear()
    assert g.own(8, 46, keys=256) and g.moves("KEY_TOP") == 256


def test_a_rule_on_an_event_fires_in_the_frame_it_is_seen(g: Game) -> None:
    g.own(0, 46)
    g.rule(0, on=NOTICED, play_move=1, force=1)
    g.frame()
    g.poke(a.ENTITY.AWARE, "B", 1)  # the notice, in the host step
    g.frame()
    assert g.entered() == [(0, 2)] and g.lib.mhfu_em_playing() == 0
    g.frames(3)
    assert g.move("STARTED") == 1  # an edge, not a level


def test_a_move_on_the_notice_waits_unless_forced(g: Game) -> None:
    g.own(0, 46, force=1)  # a slot's own FORCE is not the call's
    g.rule(0, on=NOTICED, play_move=1)
    g.frame()
    g.poke(a.ENTITY.AWARE, "B", 1)
    g.frames(3)
    assert g.entered() == [] and g.move("WAITED") == 3


def test_play_asked_forced(g: Game) -> None:
    g.own(0, 46, after=1)
    g.own(1, 53)
    g.poke(a.ENTITY.AWARE, "B", 1)
    g.lib.mhfu_em_play(ENT, 0, 0)
    g.frames(2)
    assert g.entered() == []
    g.lib.mhfu_em_play(ENT, 0, 1)
    g.frames(2 + int(CLIP_END / SPEED))
    g.frames(2)
    assert ("X", ENT, 53, 0, 0) in g.calls()  # AFTER forced as the move before


def test_an_event_rule_keeps_its_gates(g: Game) -> None:
    g.rule(0, on=NOTICED, from_mask=1 << 3, to_main=3, to_sub=6)
    g.rule(1, on=TAIL, dist_hi=100.0, to_main=3, to_sub=7)
    g.frame()
    g.poke(a.ENTITY.AWARE, "B", 1)
    g.poke(a.ENTITY.SEVERED, "B", 1)
    g.frames(2)
    assert g.entered() == []  # not in main 3, the hunter too far


def test_a_break_of_its_part_only(g: Game) -> None:
    g.rule(0, on=BROKEN, part=2, to_main=3, to_sub=6)
    g.frame()
    g.poke(a.ENTITY.FLINCH_MASK, "B", 0b001)
    g.poke(a.ENTITY.BROKEN, "H", 1)
    g.frame()
    assert g.entered() == []
    g.poke(a.ENTITY.FLINCH_MASK, "B", 0b100)
    g.poke(a.ENTITY.BROKEN, "H", 3)
    g.frame()
    assert g.entered() == [(3, 6)]


def test_a_flinch_rule_plays_in_place_of_the_reaction(g: Game) -> None:
    g.own(0, 59)
    g.rule(0, on=FLINCH, part=0, play_move=1, count=1)
    g.frame()  # armed for the host step that follows
    g.flinch(0b001)
    assert g.entered() == [(0, 2)]  # the carrier, never (4, 1)
    assert g.move("END", "B") == 0 and g.lib.mhfu_em_playing() == 0
    g.frame()
    assert ("X", ENT, 59, 0, 0) in g.calls() and g.move("REACTIONS") == 1
    g.frames(int(CLIP_END / SPEED))
    g.flinch(0b001)
    assert g.entered()[-1] == (4, 1)  # its one fire is spent: the host's own


def test_a_flinch_of_another_part_is_the_hosts(g: Game) -> None:
    g.own(0, 59)
    g.rule(0, on=FLINCH, part=0, play_move=1)
    g.frame()
    g.flinch(0b010)
    assert g.entered() == [(4, 1)] and g.move("STARTED") == 0


def test_flinch_rules_pick_by_part_and_keep_their_gates(g: Game) -> None:
    g.own(0, 59)
    g.own(1, 60)
    g.rule(0, on=FLINCH, part=0, play_move=1, dist_hi=100.0)
    g.rule(1, on=FLINCH, part=1, play_move=2)
    g.frame()
    g.flinch(0b001)
    assert g.entered() == [(4, 1)]  # part 0's rule: the hunter is too far
    g.frames(2)
    g.flinch(0b011)
    g.frame()
    calls = g.calls()
    assert ("E", ENT, 0, 2, 2) in calls and ("X", ENT, 60, 0, 0) in calls


def test_a_flinch_rule_ends_a_running_move(g: Game) -> None:
    g.own(0, 46)
    g.own(1, 59)
    g.rule(0, on=FLINCH, play_move=2)
    g.lib.mhfu_em_play(ENT, 0, 0)
    g.frames(4)
    g.flinch(0b100)
    g.frame()
    assert g.move("STARTED") == 2 and g.lib.mhfu_em_playing() == 1
    assert ("X", ENT, 59, 0, 0) in g.calls()


def test_a_flinch_rule_needs_an_own_move(g: Game) -> None:
    g.rule(0, on=FLINCH, to_main=3, to_sub=6)
    g.frame()
    g.flinch(0b001)
    assert g.entered() == [(4, 1)]


def test_clear_hands_the_flinch_back(g: Game) -> None:
    g.own(0, 59)
    g.rule(0, on=FLINCH, play_move=1)
    g.frame()
    g.lib.mhfu_em_host_quest()
    g.flinch(0b001)
    assert g.entered() == [(4, 1)]


def test_every_rule_slot_holds_one(g: Game) -> None:
    n = a.EM_CFG.RULES.count or 0
    assert n >= 8
    for k in range(n):
        g.rule(k, from_mask=1 << 6, to_main=3, to_sub=k)
    g.rule(n - 1, from_mask=1 << 1, to_main=3, to_sub=n)
    g.rule(n, from_mask=1 << 1, to_main=2, to_sub=9)  # past the end: ignored
    g.frame()
    assert g.entered() == [(3, n)]


def test_a_flinch_that_breaks_plays_the_break_rule(g: Game) -> None:
    g.own(0, 59)
    g.own(1, 61)
    g.rule(0, on=FLINCH, part=0, play_move=1)
    g.rule(1, on=BROKEN, play_move=2, count=1)
    g.frame()
    g.poke(a.ENTITY.BROKEN, "H", 2)
    g.flinch(0b001)
    g.frame()
    assert ("X", ENT, 61, 0, 0) in g.calls() and g.lib.mhfu_em_playing() == 1
    g.frames(int(CLIP_END / SPEED) + 2)
    g.flinch(0b001)
    g.frame()
    assert ("X", ENT, 59, 0, 0) in g.calls()  # no new break: the flinch rule's
