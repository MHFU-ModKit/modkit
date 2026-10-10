# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""em_vhook's brain on the host with the real move player: own moves played by slot when asked,
after the move before, or by a rule; the rules entering pairs; their conditions, effects and
signals on the board; the tail cut holding them for its drop; and the tail tip after the species
brain. One AI frame per call (mhfu_em_host_frame: the brain, then the C step), against
em_host.cpp's engine."""

import ctypes
import math
import struct
from collections.abc import Callable

import pytest
from mhfu import addresses as a

BASE = 0x09000000  # em_host.cpp's memory; noaddr
ENT = BASE + 0x1000
PMO = BASE + 0x3900
JOINTS = BASE + 0x4000
CLIP_END = 20.0
SPEED = 2.0
NO = ANY = 0xFF
UNLIMITED = 0xFFFF_FFFF
BACK, PAIR = 2, 3
PLAYING, DONE = 2, 4
KIND = a.MONSTER_EVENT_KIND.number
NOTICED, FLINCH, BROKEN, TAIL = (
    KIND("noticed"),
    KIND("flinch"),
    KIND("part_broken"),
    KIND("tail_cut"),
)
ENRAGED, CALMED = KIND("enraged"), KIND("calmed")
NO_PLAY = 4  # MHFU_EM_RULE_NO_PLAY
RULES = a.EM_CFG.RULES.count or 0
MOVES = a.EM_MOVES.MOVES.count or 0


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


class Op(ctypes.Structure):
    _fields_ = [("op", ctypes.c_uint8), ("arg", ctypes.c_uint8), ("value", ctypes.c_int16)]


def cond(name: str, arg: int = 0, value: int = 0) -> Op:
    return Op(a.EM_COND.number(name), arg, value)


def effect(name: str, arg: int = 0, value: int = 0) -> Op:
    return Op(a.EM_EFFECT.number(name), arg, value)


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
        ("signal", ctypes.c_uint8),
        ("conds", Op * (a.EM_RULE.CONDS.count or 0)),
        ("effects", Op * (a.EM_RULE.EFFECTS.count or 0)),
    ]


class Status(ctypes.Structure):
    """mhfu_em_status_t."""

    _fields_ = [
        *[(k, ctypes.c_uint32) for k in ("installed", "ai_ticks", "act_enters", "last_pair")],
        ("frames", ctypes.c_uint32),
        ("dist", ctypes.c_float),
        *[(k, ctypes.c_uint32) for k in ("sub_hits", "sub_landed", "sub_last_in", "brain_fires")],
        *[(k, ctypes.c_uint32) for k in ("req_pending", "req_done", "req_result", "ring_idx")],
        ("ring", ctypes.c_uint32 * 8),
        ("rule_fired", ctypes.c_uint32 * RULES),
        ("rule_left", ctypes.c_uint32 * RULES),
        ("sub_left", ctypes.c_uint32 * 4),
        *[(k, ctypes.c_uint32) for k in ("events_muted", "tip_pairs", "tip_copies", "cut_waits")],
        ("vars", ctypes.c_int32 * (a.EM_BOARD.VARS.count or 0)),
        ("broken", ctypes.c_uint32),
        ("sever_pct", ctypes.c_uint32),
        ("gate_refused", ctypes.c_uint32),
        ("natural_rage", ctypes.c_uint32),
    ]


class Game:
    def __init__(self, lib: ctypes.CDLL) -> None:
        self.lib = lib
        self.mem = (ctypes.c_uint8 * 0x8000).from_address(lib.host_mem())
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

    def rule(
        self,
        slot: int,
        conds: list[Op] | None = None,
        effects: list[Op] | None = None,
        signal: int | None = None,
        no_play: bool = False,
        **kw: float,
    ) -> None:
        """Rule `slot`; `signal` is the board index (the C field holds it plus one)."""
        r = Rule(from_sub=0xFE, dist_hi=1.0e9, count=UNLIMITED, part=ANY)
        for k, v in kw.items():
            setattr(r, k, v)
        for k, o in enumerate(conds or []):
            r.conds[k] = o
        for k, o in enumerate(effects or []):
            r.effects[k] = o
        r.signal = 0 if signal is None else signal + 1
        r.flags |= NO_PLAY if no_play else 0
        self.lib.mhfu_em_rule(slot, ctypes.byref(r))

    def var(self, k: int, v: int | None = None) -> int:
        if v is not None:
            self.lib.mhfu_em_set_var(k, v)
        return int(self.lib.mhfu_em_var(k))

    def board(self, field: str, fmt: str = "B", at: int = 0) -> int:
        """A field of the board; `at` the byte offset into it, for an array."""
        base = int(self.lib.mhfu_em_board()) + getattr(a.EM_BOARD, field) + at
        return int(struct.unpack_from("<" + fmt, ctypes.string_at(base, 4))[0])

    def go(self, pair: tuple[int, int] = (1, 3)) -> list[tuple[int, int]]:
        """One AI frame from `pair`: the pairs it entered."""
        self.pair = pair
        self.calls()
        self.frame()
        return self.entered()

    def status(self) -> "Status":
        st = Status()
        self.lib.mhfu_em_status(ctypes.byref(st))
        return st


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
    lib.mhfu_em_host_brain.argtypes = [ctypes.c_uint32]
    lib.mhfu_em_host_latch.argtypes = [ctypes.c_uint32]
    lib.mhfu_em_host_spawn.argtypes = [ctypes.c_uint32]
    lib.mhfu_em_var.argtypes = [ctypes.c_int]
    lib.mhfu_em_set_var.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.mhfu_em_signal.argtypes = [ctypes.c_int]
    lib.mhfu_em_board.restype = ctypes.c_void_p
    lib.mhfu_em_status.argtypes = [ctypes.c_void_p]
    lib.mhfu_em_rage.argtypes = [ctypes.c_int]
    lib.mhfu_em_natural_rage.argtypes = [ctypes.c_int]
    lib.mhfu_em_sever_gate.argtypes = [ctypes.c_int]
    lib.mhfu_em_host_gate.argtypes = [ctypes.c_uint32]
    lib.host_gate.argtypes = [ctypes.c_uint32]
    lib.mhfu_em_tip.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return lib


@pytest.fixture
def g(lib: ctypes.CDLL) -> Game:
    lib.host_reset(ctypes.c_float(CLIP_END))
    lib.mhfu_em_init()
    lib.mhfu_em_host_spawn(ENT)
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


# --- the tail: the cut waits for its drop, the tip follows its carriers until it


def tailed(g: Game, meshes: int = 7) -> None:
    """The monster's joints, its model's mesh count and the wrapped species' vtable."""
    g.poke(a.ENTITY.VTABLE, "I", int(a.TIGREX_VTABLE))
    g.poke(a.ENTITY.JOINTS, "I", JOINTS)
    g.poke(a.ENTITY.JOINT_COUNT, "H", 12)
    g.poke(a.ENTITY.PMO, "I", PMO)
    struct.pack_into("<H", g.mem, PMO - BASE + a.PMO.MESH_COUNT, meshes)
    g.lib.mhfu_em_host_latch(int(a.TIGREX_VTABLE))


def cut(g: Game, meshes: int = 7) -> None:
    tailed(g, meshes)
    g.pair = (4, 4)
    g.poke(a.ENTITY.SEVERED, "B", 1)


def drop(g: Game) -> None:
    g.poke(a.ENTITY.FLAGS, "I", 0x4000)
    g.pair = (4, 15)


def _pose_at(joint: int) -> int:
    assert a.JOINT.size
    return JOINTS - BASE + joint * a.JOINT.size + a.JOINT.POSE


def pose(g: Game, joint: int) -> tuple[float, ...]:
    return struct.unpack_from("<16f", g.mem, _pose_at(joint))


def set_pose(g: Game, joint: int, v: float) -> None:
    struct.pack_into("<16f", g.mem, _pose_at(joint), *[v + k for k in range(16)])


def tip(g: Game, pairs: list[tuple[int, int]]) -> int:
    arr = (ctypes.c_uint8 * (2 * max(len(pairs), 1)))(*[x for p in pairs for x in p])
    return int(g.lib.mhfu_em_tip(arr, len(pairs)))


def test_tip_follows_after_the_brain(g: Game) -> None:
    tailed(g)
    g.lib.host_attach(5, 1)  # the brain's ATTACH poses 5 from 1
    for j in (1, 3, 4):
        set_pose(g, j, 10.0 * j)
    assert tip(g, [(5, 3), (6, 4)])
    g.lib.mhfu_em_host_brain(ENT)
    assert g.calls()[0][0] == "B"
    assert pose(g, 5) == pose(g, 3) and pose(g, 6) == pose(g, 4)


def test_tip_stops_at_the_drop(g: Game) -> None:
    tailed(g)
    tip(g, [(5, 3)])
    set_pose(g, 3, 30.0)
    drop(g)
    g.lib.mhfu_em_host_brain(ENT)
    assert pose(g, 5) == (0.0,) * 16


def test_tip_skips_other_vtables_and_joints(g: Game) -> None:
    tailed(g)
    tip(g, [(5, 3), (12, 3), (6, 12)])  # JOINT_COUNT is 12
    set_pose(g, 3, 30.0)
    g.poke(a.ENTITY.VTABLE, "I", int(a.GIADROME_VTABLE))
    g.lib.mhfu_em_host_brain(ENT)
    assert pose(g, 5) == (0.0,) * 16
    g.poke(a.ENTITY.VTABLE, "I", int(a.TIGREX_VTABLE))
    g.lib.mhfu_em_host_brain(ENT)
    assert pose(g, 5) == pose(g, 3) and pose(g, 6) == (0.0,) * 16


def test_tip_takes_up_to_eight_and_clears(g: Game) -> None:
    tailed(g)
    assert not tip(g, [(5, 3)] * 9)
    assert tip(g, [(5, 3)] * 8) and tip(g, [])
    set_pose(g, 3, 30.0)
    g.lib.mhfu_em_host_brain(ENT)
    assert pose(g, 5) == (0.0,) * 16


def test_the_cut_holds_a_rule_until_the_drop(g: Game) -> None:
    g.rule(0, from_mask=0xFF, to_main=3, to_sub=6)
    cut(g)
    g.frames(5)
    assert g.entered() == []
    drop(g)
    g.frame()
    assert g.entered() == [(3, 6)]


def test_the_cut_holds_a_request(g: Game) -> None:
    cut(g)
    assert g.lib.mhfu_em_request(3, 6, 1)
    g.frames(3)
    assert g.entered() == []
    drop(g)
    g.frame()
    assert [c for c in g.calls() if c[0] == "E"] == [("E", ENT, 3, 6, 1)]


def test_the_cut_holds_a_play_forced_or_not(g: Game) -> None:
    g.own(0, 46)
    cut(g)
    g.lib.mhfu_em_play(ENT, 0, 1)
    g.frames(3)
    assert g.entered() == []
    drop(g)
    g.frame()
    assert g.entered() == [(0, 2)] and g.lib.mhfu_em_playing() == 0


def test_the_tail_cut_event_reaches_its_rule_after_the_drop(g: Game) -> None:
    g.rule(0, on=TAIL, to_main=3, to_sub=6, count=1)
    g.frame()
    cut(g)  # SEVERED rises with (4, 4)
    g.frames(4)
    assert g.entered() == []
    drop(g)
    g.frame()
    assert g.entered() == [(3, 6)]


def test_a_one_mesh_model_leaves_the_cut_as_before(g: Game) -> None:
    """TAIL_SPAWN's object draws mesh 1: without it the drop must not run."""
    g.rule(0, from_mask=1 << 4, to_main=3, to_sub=6)
    cut(g, meshes=1)
    g.frame()
    assert g.entered() == [(3, 6)]


# --- conditions, effects, signals and the board

PLAY = {"from_mask": 1 << 1, "to_main": 3, "to_sub": 6}  # from the fixture's pair, enter (3, 6)
VARS = a.EM_BOARD.VARS.count or 0
SIGNALS = a.EM_BOARD.SIGNALS.count or 0


def hp(g: Game, now: int, full: int = 1000) -> None:
    g.poke(a.ENTITY.MAX_HP, "H", full)
    g.poke(a.ENTITY.HP, "H", now)


def test_hp_conditions(g: Game) -> None:
    g.rule(0, **PLAY, conds=[cond("hp_below", 0, 30)])
    hp(g, 300)
    assert g.go() == []  # 30 percent is not below 30
    hp(g, 299)
    assert g.go() == [(3, 6)]
    g.rule(0, **PLAY, conds=[cond("hp_at_least", 0, 30)])
    assert g.go() == []
    hp(g, 300)
    assert g.go() == [(3, 6)]
    hp(g, 65535, 65535)  # the products stay within 32 bits
    g.rule(0, **PLAY, conds=[cond("hp_at_least", 0, 100)])
    assert g.go() == [(3, 6)]


def test_broken_conditions_follow_the_latch(g: Game) -> None:
    g.rule(0, **PLAY, conds=[cond("broken", 2)])
    g.rule(1, **PLAY | {"to_sub": 7}, conds=[cond("not_broken", 2)])
    assert g.go() == [(3, 7)]
    g.poke(a.ENTITY.FLINCH_MASK, "B", 0b100)
    g.poke(a.ENTITY.BROKEN, "H", 1)
    g.pair = (3, 7)
    g.frame()  # part 2 broke
    g.poke(a.ENTITY.FLINCH_MASK, "B", 0)
    assert g.board("BROKEN") == 0b100
    assert g.go() == [(3, 6)]  # it stays latched after the flinch


def test_a_break_without_a_flinching_part_latches_nothing(g: Game) -> None:
    g.frame()
    g.poke(a.ENTITY.BROKEN, "H", 1)
    g.frame()
    assert g.board("BROKEN") == 0


def test_enraged_and_calm(g: Game) -> None:
    g.rule(0, **PLAY, conds=[cond("enraged")])
    g.rule(1, **PLAY | {"to_sub": 7}, conds=[cond("calm")])
    assert g.go() == [(3, 7)]
    g.poke(a.ENTITY.FLAGS, "I", 0x20 | 0x8000)
    assert g.go() == [(3, 6)]


def face(g: Game, yaw: float, forward: float = 1.0, left: float = 1.0) -> None:
    """ENTITY.ROTATION as the engine builds it from YAW: row 2 forward, row 0 the monster's left,
    each row scaled as given."""
    t = math.radians(yaw)
    rows = [0.0] * 12
    rows[0], rows[2] = left * math.cos(t), -left * math.sin(t)
    rows[5] = 1.0
    rows[8], rows[10] = forward * math.sin(t), forward * math.cos(t)
    g.poke(a.ENTITY.ROTATION, "12f", *rows)


SIDES = {"front": 1, "left": 2, "right": 4, "behind": 8}


def side_at(g: Game, x: float, z: float, yaw: float = 0.0, forward: float = 1.0) -> str:
    """The sector the hunter at (x, z) stands in, by which single-side rule fires."""
    face(g, yaw, forward)
    g.hunter_at(x, z)
    sides = []
    for name, mask in SIDES.items():
        g.rule(0, **PLAY, conds=[cond("side", mask)])
        if g.go():
            sides.append(name)
    assert len(sides) == 1
    return sides[0]


@pytest.mark.parametrize(
    ("yaw", "x", "z", "want"),
    [
        (0, 0, 500, "front"),
        (0, 500, 0, "left"),  # YAW grows toward +x, the monster's left
        (0, -500, 0, "right"),
        (0, 0, -500, "behind"),
        (90, 500, 0, "front"),
        (90, 0, -500, "left"),
        (90, 0, 500, "right"),
        (90, -500, 0, "behind"),
        (0, 500, 400, "left"),  # the sectors are 90 degrees
        (0, 400, 500, "front"),
        (180, 0, -500, "front"),
    ],
)
def test_side_sectors(g: Game, yaw: float, x: float, z: float, want: str) -> None:
    assert side_at(g, x, z, yaw) == want


def test_side_ignores_the_rows_scale(g: Game) -> None:
    assert side_at(g, 500, 400, forward=3.0) == "left"
    assert side_at(g, 400, 500, forward=0.25) == "front"


def test_side_masks_combine(g: Game) -> None:
    face(g, 0)
    g.rule(0, **PLAY, conds=[cond("side", SIDES["front"] | SIDES["behind"])])
    for x, z, want in ((0, 500, True), (0, -500, True), (500, 0, False), (-500, 0, False)):
        g.hunter_at(x, z)
        assert bool(g.go()) is want


def test_chance_of_all_and_none(g: Game) -> None:
    g.rule(0, **PLAY, conds=[cond("chance", 0, 100)])
    assert all(g.go() for _ in range(20))
    g.rule(0, **PLAY, conds=[cond("chance", 0, 0)])
    assert not any(g.go() for _ in range(20))


def test_chance_is_a_fraction(g: Game) -> None:
    g.rule(0, **PLAY, conds=[cond("chance", 0, 50)])
    assert 120 < sum(bool(g.go()) for _ in range(400)) < 280


def test_chance_is_rolled_last(g: Game) -> None:
    """Only a rule whose other conditions and gates hold rolls its chance."""
    hp(g, 500)
    g.rule(0, **PLAY, conds=[cond("chance", 0, 50), cond("hp_below", 0, 1)])
    g.rule(1, **PLAY, cooldown=10_000, conds=[cond("chance", 0, 50)])
    g.frame()  # seeds the board
    rng = g.board("RNG", "I")
    g.frames(10)
    assert g.board("RNG", "I") == rng
    g.hunter_at(5000.0, 0.0)
    g.rule(1, **PLAY, dist_hi=100.0, conds=[cond("chance", 0, 50)])
    g.frames(10)
    assert g.board("RNG", "I") == rng


@pytest.mark.parametrize(
    ("op", "var", "want"),
    [
        ("var_at_least", 1, False),
        ("var_at_least", 2, True),
        ("var_at_least", 3, True),
        ("var_below", 2, False),
        ("var_below", 1, True),
        ("var_below", -1, True),
        ("var_equal", 2, True),
        ("var_equal", 3, False),
    ],
)
def test_var_conditions(g: Game, op: str, var: int, want: bool) -> None:
    g.rule(0, **PLAY, conds=[cond(op, 3, 2 if var > 0 else 0)])
    g.var(3, var)
    assert bool(g.go()) is want


def test_every_condition_must_hold(g: Game) -> None:
    hp(g, 100)
    g.rule(0, **PLAY, conds=[cond("hp_below", 0, 50), Op(0), cond("var_equal", 0, 1)])
    assert g.go() == []
    g.var(0, 1)
    assert g.go() == [(3, 6)]
    hp(g, 900)
    assert g.go() == []


def test_effects_run_each_fire(g: Game) -> None:
    g.rule(0, **PLAY, effects=[effect("var_add", 1, 5), effect("var_set", 2, 9)])
    for _ in range(3):
        g.go()
    assert (g.var(1), g.var(2)) == (15, 9)
    g.rule(0, **PLAY, count=1, effects=[effect("var_add", 1, 1)])
    for _ in range(3):
        g.go()
    assert g.var(1) == 16


def test_var_add_saturates(g: Game) -> None:
    g.rule(0, **PLAY, effects=[effect("var_add", 0, 20000)])
    g.go()
    g.go()
    assert g.var(0) == 32767
    g.rule(0, **PLAY, effects=[effect("var_add", 0, -20000)])
    for _ in range(4):
        g.go()
    assert g.var(0) == -32768


def test_a_play_that_fails_applies_no_effect(g: Game) -> None:
    g.rule(0, **PLAY | {"play_move": 5}, effects=[effect("var_add", 0, 1)])  # an empty slot
    g.go()
    assert g.var(0) == 0


def test_a_flinch_take_applies_effects(g: Game) -> None:
    g.own(0, 59)
    g.rule(0, on=FLINCH, part=0, play_move=1, effects=[effect("var_add", 0, 1)])
    g.frame()
    g.flinch(0b001)
    assert g.entered() == [(0, 2)] and g.var(0) == 1


def test_a_flinch_rule_keeps_its_conditions(g: Game) -> None:
    g.own(0, 59)
    g.rule(0, on=FLINCH, play_move=1, conds=[cond("var_at_least", 0, 1)])
    g.frame()
    g.flinch(0b001)
    assert g.entered() == [(4, 1)]  # the counter is 0: the host's reaction
    g.var(0, 1)
    g.frame()
    g.flinch(0b001)
    assert g.entered() == [(0, 2)]


def test_a_flinch_rule_with_a_chance_decides_before_the_flinch(g: Game) -> None:
    g.own(0, 59)
    g.rule(0, on=FLINCH, part=0, play_move=1, conds=[cond("chance", 0, 100)])
    g.rule(1, on=FLINCH, part=1, play_move=1, conds=[cond("chance", 0, 0)])
    g.frame()
    g.flinch(0b010)
    assert g.entered() == [(4, 1)]  # part 1's rule never rolls a hit
    g.frames(2)
    g.flinch(0b001)
    assert g.entered() == [(0, 2)]


def test_a_no_play_rule_goes_on_to_the_next(g: Game) -> None:
    g.rule(0, **PLAY, no_play=True, effects=[effect("var_add", 0, 1)])
    g.rule(1, **PLAY)
    assert g.go() == [(3, 6)] and g.var(0) == 1


def test_a_no_play_rule_does_not_stop_the_scan_or_restart_the_dwell(g: Game) -> None:
    g.rule(0, **PLAY, no_play=True, effects=[effect("var_add", 0, 1)])
    g.rule(1, **PLAY, min_frames=3)
    g.frames(3)
    assert g.entered() == []
    g.frame()  # the pair has stood 3 frames, however often rule 0 fired
    assert g.entered() == [(3, 6)] and g.var(0) == 4


def test_a_no_play_rule_counts_its_fires(g: Game) -> None:
    g.rule(0, **PLAY, no_play=True, count=2, effects=[effect("var_add", 0, 1)])
    g.frames(5)
    st = g.status()
    assert g.var(0) == 2 and (st.rule_fired[0], st.rule_left[0]) == (2, 0)
    g.rule(1, **PLAY, no_play=True, cooldown=3, effects=[effect("var_add", 1, 1)])
    g.frames(9)
    assert g.var(1) == 3


def test_a_no_play_rule_takes_no_play_target(g: Game) -> None:
    g.own(0, 46)
    g.rule(0, **PLAY, no_play=True, play_move=1)
    g.frames(2)
    assert g.entered() == [] and g.lib.mhfu_em_playing() == -1


def test_a_no_play_flinch_rule_leaves_the_reaction(g: Game) -> None:
    g.rule(0, on=FLINCH, no_play=True, effects=[effect("var_add", 0, 1)])
    g.frame()
    g.flinch(0b001)
    assert g.entered() == [(4, 1)] and g.var(0) == 1
    g.flinch(0b100)
    assert g.var(0) == 2


def test_a_no_play_flinch_rule_counts_a_replaced_flinch(g: Game) -> None:
    g.own(0, 59)
    g.rule(0, on=FLINCH, no_play=True, effects=[effect("var_add", 0, 1)])
    g.rule(1, on=FLINCH, part=0, play_move=1)
    g.frame()
    g.flinch(0b001)
    assert g.entered() == [(0, 2)] and g.var(0) == 1


def test_a_no_play_rule_counts_while_a_request_takes_the_frame(g: Game) -> None:
    g.rule(0, on=NOTICED, no_play=True, effects=[effect("var_add", 0, 1)])
    g.frame()
    g.lib.mhfu_em_request(3, 6, 1)
    g.poke(a.ENTITY.AWARE, "B", 1)
    g.frame()
    assert (3, 6) in g.entered() and g.var(0) == 1


def test_a_counter_then_a_rule_on_it_in_one_frame(g: Game) -> None:
    """on flinch +1, then at two flinches a rule plays: the scan runs in slot order."""
    g.rule(0, on=FLINCH, no_play=True, effects=[effect("var_add", 0, 1)])
    g.rule(1, from_mask=1 << 4, to_main=3, to_sub=6, conds=[cond("var_at_least", 0, 2)])
    g.frame()
    g.flinch(0b001)
    assert g.var(0) == 1 and (3, 6) not in g.entered()
    g.flinch(0b001)
    assert g.var(0) == 2 and (3, 6) in g.entered()


def test_a_rule_before_its_counter_plays_in_the_next_frame(g: Game) -> None:
    g.rule(0, from_mask=1 << 4, to_main=3, to_sub=6, conds=[cond("var_at_least", 0, 2)])
    g.rule(1, on=FLINCH, no_play=True, effects=[effect("var_add", 0, 1)])
    g.frame()
    g.flinch(0b001)
    g.flinch(0b001)
    assert g.var(0) == 2 and (3, 6) not in g.entered()
    g.frame()
    assert (3, 6) in g.entered()


def test_a_signal_fires_once(g: Game) -> None:
    g.rule(0, signal=2, to_main=3, to_sub=6)
    g.frame()
    assert g.entered() == []
    assert g.lib.mhfu_em_signal(2) == 1
    g.frame()
    assert g.entered() == [(3, 6)]
    g.frame()
    assert g.entered() == []


def test_a_signal_is_taken_even_when_its_gates_fail(g: Game) -> None:
    g.rule(0, signal=0, dist_hi=100.0, to_main=3, to_sub=6)
    g.lib.mhfu_em_signal(0)
    g.frame()
    assert g.board("SIGNALS") == 0
    g.hunter_at(50.0, 0.0)
    g.frame()
    assert g.entered() == []


def test_a_signal_reaches_every_rule_on_it(g: Game) -> None:
    g.rule(0, signal=1, no_play=True, effects=[effect("var_add", 0, 1)])
    g.rule(1, signal=1, to_main=3, to_sub=6)
    g.rule(2, signal=1, to_main=3, to_sub=7)
    g.lib.mhfu_em_signal(1)
    g.frame()
    assert g.entered() == [(3, 6)] and g.var(0) == 1


def test_a_signal_with_gates_and_conditions(g: Game) -> None:
    g.rule(0, signal=0, **PLAY, conds=[cond("var_at_least", 0, 1)])
    g.lib.mhfu_em_signal(0)
    assert g.go() == []
    g.var(0, 1)
    g.lib.mhfu_em_signal(0)
    assert g.go() == [(3, 6)]
    g.lib.mhfu_em_signal(0)
    assert g.go((2, 2)) == []  # not in its pair


def test_the_cut_holds_a_signal(g: Game) -> None:
    g.rule(0, signal=1, to_main=3, to_sub=6)
    cut(g)
    g.lib.mhfu_em_signal(1)
    g.frames(2)
    g.lib.mhfu_em_signal(1)
    g.frames(2)
    assert g.entered() == [] and g.board("SIGNALS", at=1) == 1  # held on the board
    drop(g)
    g.frame()
    assert g.entered() == [(3, 6)]
    g.frame()
    assert g.entered() == []


def test_the_cut_holds_a_counter(g: Game) -> None:
    g.rule(0, from_mask=0xFF, no_play=True, effects=[effect("var_add", 0, 1)])
    cut(g)
    g.frames(3)
    assert g.var(0) == 0
    drop(g)
    g.frame()
    assert g.var(0) == 1


ENT2 = BASE + 0x2000


def test_the_board_belongs_to_one_monster(g: Game) -> None:
    g.var(3, 7)
    g.frame()
    assert g.var(3) == 7 and g.board("ENTITY", "I") == ENT  # its spawn started the board
    g.lib.mhfu_em_signal(5)
    g.poke(a.ENTITY.FLINCH_MASK, "B", 1)
    g.poke(a.ENTITY.BROKEN, "H", 1)
    g.frame()
    g.poke(a.ENTITY.FLINCH_MASK, "B", 0)
    assert g.var(3) == 7 and g.board("BROKEN") == 1
    g.lib.mhfu_em_signal(5)
    g.lib.mhfu_em_host_frame(ENT2)  # a second monster of the species shares it
    assert g.var(3) == 7 and g.board("ENTITY", "I") == ENT
    g.lib.mhfu_em_host_spawn(ENT2)  # a new monster starts it afresh
    assert (g.var(3), g.board("SIGNALS", at=5), g.board("BROKEN")) == (0, 0, 0)
    assert g.board("ENTITY", "I") == ENT2 and g.board("RNG", "I") != 0


def test_the_first_monster_seen_owns_an_unclaimed_board(g: Game) -> None:
    g.lib.mhfu_em_host_quest()  # no monster owns it
    g.var(3, 7)
    g.frame()
    assert g.var(3) == 0 and g.board("ENTITY", "I") == ENT


def test_the_quest_clears_the_board_and_reseeds_the_roll(g: Game) -> None:
    g.rule(0, **PLAY, conds=[cond("chance", 0, 50)])
    seed = g.board("RNG", "I")
    for _ in range(5):
        g.go()
    assert g.board("RNG", "I") != seed
    g.var(1, 4)
    g.lib.mhfu_em_host_quest()
    g.frame()
    assert g.var(1) == 0 and g.board("RNG", "I") == seed


def test_the_status_carries_the_board(g: Game) -> None:
    g.frame()
    g.var(2, -5)
    g.poke(a.ENTITY.FLINCH_MASK, "B", 0b10)
    g.poke(a.ENTITY.BROKEN, "H", 1)
    g.frame()
    st = g.status()
    assert (st.vars[2], st.broken) == (-5, 0b10)


def test_a_bad_board_index_is_ignored(g: Game) -> None:
    g.var(VARS, 5)
    g.var(-1, 5)
    assert g.var(VARS) == 0 and g.var(-1) == 0
    assert g.lib.mhfu_em_signal(SIGNALS) == 0 and g.lib.mhfu_em_signal(-1) == 0
    g.var(0, 40_000)
    assert g.var(0) == 32767


def test_enraged_and_calmed_reach_their_rules(g: Game) -> None:
    g.rule(0, on=ENRAGED, to_main=3, to_sub=6)
    g.rule(1, on=CALMED, to_main=3, to_sub=7)
    g.frame()
    g.poke(a.ENTITY.FLAGS, "I", 0x20)
    g.frame()
    assert g.entered() == [(3, 6)]
    g.frame()
    assert g.entered() == []  # an edge, not a level
    g.poke(a.ENTITY.FLAGS, "I", 0)
    g.frame()
    assert g.entered() == [(3, 7)]


def test_a_monster_first_seen_enraged_raises_nothing(g: Game) -> None:
    g.poke(a.ENTITY.FLAGS, "I", 0x20)
    g.rule(0, on=ENRAGED, to_main=3, to_sub=6)
    g.frames(2)
    assert g.entered() == []


@pytest.mark.parametrize(
    "bad",
    [
        {"conds": [Op(99)]},
        {"effects": [Op(99)]},
        {"conds": [cond("var_at_least", VARS)]},
        {"effects": [effect("var_add", VARS, 1)]},
        {"conds": [cond("side", 0)]},
        {"conds": [cond("broken", 8)]},
        {"signal": SIGNALS},
        {"signal": 0, "on": NOTICED},
        {"from_mask": 0},
    ],
)
def test_a_rule_that_cannot_hold_is_refused(g: Game, bad: dict[str, object]) -> None:
    g.rule(0, **{**PLAY, **bad})  # type: ignore[arg-type]
    assert g.go() == [] and g.status().rule_left[0] == 0


def test_the_last_rule_and_move_slots_work(g: Game) -> None:
    assert RULES > 32 and MOVES > 32  # past a 32-bit mask
    assert g.own(MOVES - 1, 59) and not g.own(MOVES, 59)
    g.rule(RULES - 1, on=FLINCH, part=0, play_move=MOVES)
    g.frame()
    g.flinch(0b001)
    assert g.entered() == [(0, 2)]
    g.frame()
    assert ("X", ENT, 59, 0, 0) in g.calls() and g.lib.mhfu_em_playing() == MOVES - 1


# --- rage and the tail cut's gate

ENRAGED_FLAG, START_FLAG = 0x20, 0x400


def flags(g: Game) -> int:
    return int(g.peek(a.ENTITY.FLAGS, "I")[0])


def timer(g: Game) -> int:
    return int(g.peek(a.ENTITY.RAGE_TIMER, "h")[0])


def threshold(g: Game) -> int:
    return int(g.peek(a.ENTITY.ANGER_THRESHOLD, "h")[0])


def test_the_enrage_effect_sets_the_start_pending(g: Game) -> None:
    g.rule(0, **PLAY, no_play=True, count=1, effects=[effect("enrage")])
    g.poke(a.ENTITY.FLAGS, "I", 0x8000)
    g.frame()
    assert flags(g) == 0x8000 | START_FLAG


def test_the_enrage_effect_never_sets_it_while_enraged(g: Game) -> None:
    g.rule(0, **PLAY, no_play=True, effects=[effect("enrage", 99)])
    g.poke(a.ENTITY.FLAGS, "I", 0x8000 | ENRAGED_FLAG)
    g.frames(3)
    assert flags(g) == 0x8000 | ENRAGED_FLAG
    assert g.status().rule_fired[0] == 3  # a counter-free effect takes any arg


def test_the_calm_effect_leaves_one_frame_of_rage(g: Game) -> None:
    g.rule(0, **PLAY, no_play=True, effects=[effect("calm")])
    g.poke(a.ENTITY.RAGE_TIMER, "h", 2400)
    g.frame()
    assert timer(g) == 2400  # not enraged: nothing to end
    g.poke(a.ENTITY.FLAGS, "I", ENRAGED_FLAG)
    g.frame()
    assert timer(g) == 1 and flags(g) == ENRAGED_FLAG  # the engine's own end clears the flag


def test_rage_effects_ride_with_a_counter(g: Game) -> None:
    g.rule(0, **PLAY, no_play=True, count=1, effects=[effect("var_add", 2, 1), effect("enrage")])
    g.frame()
    assert g.var(2) == 1 and flags(g) & START_FLAG


def test_a_flinch_take_applies_the_rage_effect(g: Game) -> None:
    g.own(0, 59)
    g.rule(0, on=FLINCH, part=0, play_move=1, effects=[effect("enrage")])
    g.frame()
    g.flinch(0b001)
    assert g.entered() == [(0, 2)] and flags(g) & START_FLAG


def test_lua_queues_the_rage_for_the_brain(g: Game) -> None:
    assert g.lib.mhfu_em_rage(1) == 1
    assert flags(g) == 0  # the caller's thread writes nothing
    g.frame()
    assert flags(g) == START_FLAG and g.board("RAGE") == 0
    g.poke(a.ENTITY.FLAGS, "I", 0)
    g.frame()
    assert flags(g) == 0  # asked once
    g.poke(a.ENTITY.FLAGS, "I", ENRAGED_FLAG)
    assert g.lib.mhfu_em_rage(0) == 1
    g.frame()
    assert timer(g) == 1


def test_the_last_rage_request_wins(g: Game) -> None:
    g.lib.mhfu_em_rage(1)
    g.lib.mhfu_em_rage(0)
    g.frame()
    assert flags(g) == 0


def test_the_cut_holds_a_rage_request(g: Game) -> None:
    cut(g)
    g.lib.mhfu_em_rage(1)
    g.frames(3)
    assert not flags(g) & START_FLAG
    drop(g)
    g.poke(a.ENTITY.FLAGS, "I", 0x4000)
    g.frame()
    assert flags(g) & START_FLAG


def test_a_new_monster_drops_the_queued_rage(g: Game) -> None:
    g.lib.mhfu_em_rage(1)
    g.lib.mhfu_em_host_spawn(ENT2)
    g.frame()
    assert flags(g) == 0


def test_natural_rage_is_on_by_default(g: Game) -> None:
    g.poke(a.ENTITY.ANGER_THRESHOLD, "h", 600)
    g.frames(3)
    assert threshold(g) == 600 and g.status().natural_rage == 1


def test_natural_rage_off_raises_the_threshold_once(g: Game) -> None:
    g.poke(a.ENTITY.ANGER_THRESHOLD, "h", 600)
    g.lib.mhfu_em_natural_rage(0)
    assert threshold(g) == 600  # the brain writes it, in its next frame
    g.frame()
    assert threshold(g) == 32767 and g.status().natural_rage == 0
    g.poke(a.ENTITY.ANGER_THRESHOLD, "h", 500)
    g.frames(3)
    assert threshold(g) == 500  # not maintained


def test_natural_rage_on_restores_the_species_value(g: Game) -> None:
    g.poke(a.ENTITY.ANGER_THRESHOLD, "h", 600)
    g.lib.mhfu_em_natural_rage(0)
    g.frame()
    g.lib.mhfu_em_natural_rage(0)  # again: the saved value is the species', not ours
    g.frame()
    g.lib.mhfu_em_natural_rage(1)
    g.frame()
    assert threshold(g) == 600
    g.lib.mhfu_em_natural_rage(1)
    g.frame()
    assert threshold(g) == 600


def test_natural_rage_off_reaches_each_new_monster(g: Game) -> None:
    g.lib.mhfu_em_natural_rage(0)
    g.frame()
    struct.pack_into("<h", g.mem, ENT2 - BASE + a.ENTITY.ANGER_THRESHOLD, 800)
    g.lib.mhfu_em_host_spawn(ENT2)
    g.lib.mhfu_em_host_frame(ENT2)
    read = lambda: struct.unpack_from("<h", g.mem, ENT2 - BASE + a.ENTITY.ANGER_THRESHOLD)[0]  # noqa: E731
    assert read() == 32767
    g.lib.mhfu_em_natural_rage(1)
    g.lib.mhfu_em_host_frame(ENT2)
    assert read() == 800


def test_clear_sets_natural_rage_back_on(g: Game) -> None:
    g.poke(a.ENTITY.ANGER_THRESHOLD, "h", 600)
    g.lib.mhfu_em_natural_rage(0)
    g.frame()
    g.lib.mhfu_em_clear()
    g.frame()
    assert threshold(g) == 600 and g.status().natural_rage == 1


def gate(g: Game, ent: int = ENT) -> int:
    return int(g.lib.mhfu_em_host_gate(ent))


def gate_asked(g: Game) -> int:
    return sum(1 for c in g.calls() if c[0] == "G")


def test_the_gate_refuses_at_or_above_the_percent(g: Game) -> None:
    tailed(g)
    assert g.lib.mhfu_em_sever_gate(50) == 1
    for now in (1000, 500):
        hp(g, now)
        assert gate(g) == 0
    assert gate_asked(g) == 0 and g.status().gate_refused == 2
    hp(g, 499)
    assert gate(g) == 1 and gate_asked(g) == 1


def test_the_gate_keeps_the_species_own_refusals(g: Game) -> None:
    tailed(g)
    g.lib.mhfu_em_sever_gate(50)
    hp(g, 100)
    g.lib.host_gate(0)
    assert gate(g) == 0 and gate_asked(g) == 1
    g.lib.host_gate(1)
    assert gate(g) == 1


def test_the_gate_passes_other_species_through(g: Game) -> None:
    tailed(g)
    g.lib.mhfu_em_sever_gate(50)
    struct.pack_into("<I", g.mem, ENT2 - BASE + a.ENTITY.VTABLE, int(a.GIADROME_VTABLE))
    struct.pack_into("<HH", g.mem, ENT2 - BASE + a.ENTITY.MAX_HP, 1000, 0)
    struct.pack_into("<H", g.mem, ENT2 - BASE + a.ENTITY.HP, 1000)
    assert gate(g, ENT2) == 1 and gate_asked(g) == 1
    assert g.status().gate_refused == 0


def test_the_gate_off_asks_the_species_only(g: Game) -> None:
    tailed(g)
    hp(g, 1000)
    assert gate(g) == 1 and gate_asked(g) == 1  # never set
    g.lib.mhfu_em_sever_gate(50)
    assert gate(g) == 0
    assert g.lib.mhfu_em_sever_gate(0) == 1
    assert gate(g) == 1 and gate_asked(g) == 1


def test_the_gate_needs_a_max_hp(g: Game) -> None:
    tailed(g)
    g.lib.mhfu_em_sever_gate(1)
    hp(g, 0, 0)
    assert gate(g) == 1 and gate_asked(g) == 1


def test_the_gate_takes_a_percent_only(g: Game) -> None:
    tailed(g)
    assert g.lib.mhfu_em_sever_gate(101) == 0 and g.lib.mhfu_em_sever_gate(-1) == 0
    assert g.status().sever_pct == 0
    assert g.lib.mhfu_em_sever_gate(100) == 1 and g.status().sever_pct == 100
    hp(g, 1000)
    assert gate(g) == 0  # at 100 percent of the HP and above


def test_clear_lifts_the_gate(g: Game) -> None:
    tailed(g)
    g.lib.mhfu_em_sever_gate(50)
    g.lib.mhfu_em_clear()
    hp(g, 1000)
    assert gate(g) == 1 and g.status().sever_pct == 0
