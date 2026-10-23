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
from collections.abc import Callable, Sequence

import pytest
from mhfu import addresses as a
from mhfu import steering
from mhfu.live.moves import Lunge
from mhfu_port import layout, manifest, moves, travel
from mhfu_port.moves import Pools

BASE = 0x09000000  # em_host.cpp's memory; noaddr
ENT = BASE + 0x1000
PMO = BASE + 0x3900
JOINTS = BASE + 0x4000
CLIP_END = 20.0
SPEED = 2.0
NO = ANY = 0xFF
UNLIMITED = 0xFFFF_FFFF
CLIP, BACK, PAIR = 1, 2, 3
PLAYING, DONE = 2, 4
KIND = a.MONSTER_EVENT_KIND.number
NOTICED, FLINCH, BROKEN, TAIL = (
    KIND("noticed"),
    KIND("flinch"),
    KIND("part_broken"),
    KIND("tail_cut"),
)
ENRAGED, CALMED = KIND("enraged"), KIND("calmed")
ENTERED = KIND("combat_entered")
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
        ("hold_script", ctypes.c_uint8),
        ("_pad", ctypes.c_uint8),
        ("start", ctypes.c_uint16),
        ("stop", ctypes.c_uint16),
    ]


assert ctypes.sizeof(Move) == a.MOVE.size


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
        ("own_enters", ctypes.c_uint32),
        ("own_last", ctypes.c_uint32),
    ]


class FatStatus(ctypes.Structure):
    """mhfu_fatigue_status_t."""

    _fields_ = [
        ("armed", ctypes.c_uint32),
        ("stamina", ctypes.c_int32),
        ("tired", ctypes.c_uint32),
        ("left", ctypes.c_uint32),
        ("charges", ctypes.c_uint32),
        ("spells", ctypes.c_uint32),
        ("gauge", ctypes.c_int32),
    ]


class ChgStatus(ctypes.Structure):
    """mhfu_charge_status_t."""

    _fields_ = [
        ("armed", ctypes.c_uint32),
        ("charged", ctypes.c_uint32),
        ("gauge", ctypes.c_int32),
        ("durability", ctypes.c_int32),
        ("cooldown", ctypes.c_int32),
        *[(k, ctypes.c_uint32) for k in ("up_starts", "transforms", "discharges", "hits", "asked")],
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

    def own(
        self,
        slot: int,
        entry: int,
        after: int = NO,
        keys: Sequence[int] = (),
        *,
        turn: int = 0,
        window: tuple[int, int] = (0, 0),
        samples: Sequence[tuple[int, int]] = (),
        lunge: tuple[int, int, float, float, float, float] | None = None,
        **kw: int,
    ) -> int:
        """Own move `slot`; `keys` are its turn keys, `turn` and `window` its steer's (a hunter
        turn at rate 64), `samples` its travel in sample units and `lunge` (lo, hi, near, span,
        base, range)."""
        mv = Move(entry=entry, carrier_main=0, carrier_sub=2, back_main=NO)
        for k, v in kw.items():
            setattr(mv, k, v)
        spec = ctypes.create_string_buffer(a.STEER_SPEC.size)
        self.lib.mhfu_steer_init_spec(spec)
        struct.pack_into("<H", spec, a.STEER_SPEC.KEY_COUNT, len(keys))
        struct.pack_into(f"<{len(keys)}H", spec, a.STEER_SPEC.KEYS, *keys)
        p = a.STEER_SPEC.STEER
        struct.pack_into("<BBH", spec, p + a.STEER_PARAMS.TURN, turn, 0, 64)
        struct.pack_into("<HH", spec, p + a.STEER_PARAMS.WIN_FROM, *window)
        struct.pack_into("<H", spec, a.STEER_SPEC.SAMPLE_COUNT, len(samples))
        for k, xz in enumerate(samples):
            struct.pack_into("<hh", spec, int(a.STEER_SPEC.SAMPLES) + 4 * k, *xz)
        if lunge:
            struct.pack_into("<HHffff", spec, a.STEER_SPEC.LUNGE, *lunge)
        return int(self.lib.mhfu_em_move(slot, ctypes.byref(mv), spec, after))

    def slot(self, slot: int, field: str, fmt: str = "B") -> int:
        """A field of the registry's own move `slot`."""
        base = int(self.lib.mhfu_em_moves()) + a.EM_MOVES.MOVES + slot * int(a.EM_OWN.size or 0)
        size = struct.calcsize("<" + fmt)
        return int(
            struct.unpack_from("<" + fmt, ctypes.string_at(base + getattr(a.EM_OWN, field), size))[
                0
            ]
        )

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

    def fatigue(
        self,
        *,
        max: int = 1000,
        starts: tuple[int, int] = (400, 600),
        tired: int = 5,
        refill: int = 500,
        low_hp: int = 0,
        calm: int = 0,
        pairs: tuple[tuple[int, int, int], ...] = (),
        own: dict[int, int] | None = None,
    ) -> int:
        """mhfu_em_fatigue with these settings; what it returned."""
        c = a.FATIGUE_CFG
        buf = ctypes.create_string_buffer(int(c.size or 0))
        struct.pack_into(
            "<i2iiIBBH", buf, c.MAX, max, *starts, refill, tired, low_hp, calm, len(pairs)
        )
        for k, row in enumerate(pairs):
            struct.pack_into("<BBh", buf, int(c.PAIRS) + 4 * k, *row)
        for slot, cost in (own or {}).items():
            struct.pack_into("<h", buf, int(c.OWN) + 2 * slot, cost)
        return int(self.lib.mhfu_em_fatigue(buf))

    def fat(self) -> FatStatus:
        st = FatStatus()
        self.lib.mhfu_em_fatigue_status(ctypes.byref(st))
        return st

    def gauge(self, value: int | None = None) -> int:
        """ENTITY.GAUGE_A, set first when given."""
        if value is not None:
            self.poke(a.ENTITY.GAUGE_A, "i", value)
        return int(self.peek(a.ENTITY.GAUGE_A, "i")[0])

    def charge(
        self,
        *,
        transform: int = 2,
        ups: dict[int, tuple[int, int, int, int]] | None = None,
        pairs: tuple[tuple[int, int], ...] = (),
        chip_gauge: int = 0,
        chip_durability: int = 0,
        up: int = 420,
        out: int = 900,
    ) -> int:
        """mhfu_em_charge with these settings (an up gains 100 at any HP unless told); what it
        returned."""
        ups = {0: (100, 100, 100, 100)} if ups is None else ups
        c = a.CHARGE_CFG
        buf = ctypes.create_string_buffer(int(c.size or 0))
        struct.pack_into("<BBB", buf, c.TRANSFORM, transform, len(ups), len(pairs))
        struct.pack_into("<hhHH", buf, c.CHIP_GAUGE, chip_gauge, chip_durability, up, out)
        for k, (slot, gain) in enumerate(ups.items()):
            struct.pack_into("<Bxhhhh", buf, int(c.UPS) + 10 * k, slot, *gain)
        for k, (m, sub) in enumerate(pairs):
            struct.pack_into("<BB", buf, int(c.PAIRS) + 2 * k, m, sub)
        return int(self.lib.mhfu_em_charge(buf))

    def chg(self) -> ChgStatus:
        st = ChgStatus()
        self.lib.mhfu_em_charge_status(ctypes.byref(st))
        return st

    def ask(self, arg: int, ent: int = ENT) -> int:
        """The species' query as the stub answers it."""
        return int(self.lib.mhfu_em_host_query(ent, arg))

    def hp(self, hp: int) -> None:
        self.poke(a.ENTITY.HP, "H", hp)

    def until(self, done: Callable[[], bool], limit: int = 60) -> int:
        """AI frames until done(); asserts it comes."""
        for k in range(limit + 1):
            if done():
                return k
            self.frame()
        raise AssertionError(f"not in {limit} frames")


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
    lib.mhfu_em_host_latches.argtypes = [ctypes.c_uint32]
    lib.host_groups.argtypes = [ctypes.c_uint32]
    lib.host_target.argtypes = [ctypes.c_int, ctypes.c_uint32]
    lib.host_big.argtypes = [ctypes.c_uint32]
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
    lib.host_break_row.argtypes = [
        ctypes.c_int,
        ctypes.c_uint8,
        ctypes.c_uint8,
        ctypes.c_uint8,
        ctypes.c_uint16,
    ]
    lib.mhfu_em_tip.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.mhfu_host_enter.argtypes = [ctypes.c_uint32] * 4
    lib.mhfu_em_fatigue.argtypes = [ctypes.c_void_p]
    lib.mhfu_em_fatigue_status.argtypes = [ctypes.c_void_p]
    lib.mhfu_em_charge.argtypes = [ctypes.c_void_p]
    lib.mhfu_em_charge_status.argtypes = [ctypes.c_void_p]
    lib.mhfu_em_host_query.restype = ctypes.c_uint32
    lib.mhfu_em_host_query.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
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


def test_a_slots_start_and_stop_reach_the_move(g: Game) -> None:
    assert g.own(0, 46, start=6, stop=14) and g.lib.mhfu_em_play(ENT, 0, 0)
    g.frames(2)
    assert ("X", ENT, 46, 0, 6) in g.calls()
    g.until(lambda: g.move("STATE", "B") == DONE)
    assert g.move("END", "B") == BACK and g.move("END_FRAME") == 4  # (14 - 6) / SPEED


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


def yaws(g: Game, frames: int) -> list[tuple[float, int]]:
    """(the move player's clip cursor, YAW change) of each of `frames` AI frames."""
    base = int(g.lib.mhfu_move_state()) + a.MOVE_STATE.STEER
    out = []
    for _ in range(frames):
        before = int(g.peek(a.ENTITY.YAW, "H")[0])
        g.frame()
        (cursor,) = struct.unpack_from("<f", ctypes.string_at(base + a.STEER_STATE.CURSOR, 4))
        out.append((cursor, int(g.peek(a.ENTITY.YAW, "H")[0]) - before))
    return out


@pytest.mark.parametrize(("window", "turning"), [((6, 12), range(6, 12)), ((0, 0), range(0, 99))])
def test_an_own_moves_hunter_turn_keeps_to_its_steer_window(
    g: Game, window: tuple[int, int], turning: range
) -> None:
    """The registry carries the window to the move player: it turns toward the hunter, 64 a frame,
    only on the frames whose clip cursor is in [from, to); no window turns on all of them."""
    struct.pack_into("<fff", g.hunter, a.ENTITY.POSITION, 500.0, 0.0, 0.0)  # the steer reads this
    assert g.own(0, 46, turn=1, window=window) and g.lib.mhfu_em_play(ENT, 0, 1)
    seen = [(c, d) for c, d in yaws(g, 2 + int(CLIP_END / SPEED)) if c > 0]
    assert len(seen) >= 8
    assert [d for c, d in seen] == [64 if c in turning else 0 for c, _ in seen]
    assert any(d for _, d in seen)


def test_keys_have_room(g: Game) -> None:
    cap = a.EM_MOVES.KEYS.count
    for slot, start in enumerate(range(0, cap, 256)):
        assert g.own(slot, 46, keys=[slot] * min(256, cap - start))
    full = slot + 1
    assert g.moves("KEY_TOP") == cap and not g.own(full, 46, keys=[full])
    assert g.own(full, 46, keys=[0] * 7)  # held already: no room needed
    g.lib.mhfu_em_moves_clear()
    assert g.own(full, 46, keys=[full] * 256) and g.moves("KEY_TOP") == 256


def played_to_the_end(g: Game, slot: int) -> tuple[float, float]:
    """Where the monster stands after own move `slot` ran its clip out, from the origin."""
    g.poke(a.ENTITY.POSITION, "fff", 0.0, 0.0, 0.0)
    g.poke(a.ENTITY.SIZE_SCALE, "f", 1.0)
    assert g.lib.mhfu_em_play(ENT, slot, 1)
    g.frames(2 + int(CLIP_END / SPEED) + 2)
    x, _, z = g.peek(a.ENTITY.POSITION, "fff")
    return x, z


def test_the_registry_hands_a_slots_travel_and_lunge_to_the_move_player(g: Game) -> None:
    """Each slot plays its own samples out of the shared pool, and the lunge only the slot that
    has one: the sizes and offsets come from the table both sides read."""
    g.hunter_at(0.0, 0.0)
    struct.pack_into("<fff", g.hunter, a.ENTITY.POSITION, 0.0, 0.0, 800.0)
    lunge = (0, 10, 400.0, 1000.0, 0.0, 500.0)
    other = (0, 10, 400.0, 1000.0, 0.0, 600.0)
    assert g.own(0, 46, samples=steering.samples([(0, 0), (5, 20)]))
    assert g.own(1, 46, samples=steering.samples([(0, 0), (-7, 3), (-9, 9)]), lunge=lunge)
    assert g.own(2, 46, lunge=other) and g.own(3, 46)
    assert [g.slot(k, "SAMPLE_AT", "H") for k in range(4)] == [0, 2, 0, 0]
    assert [g.slot(k, "LUNGE_AT") for k in range(4)] == [NO, 0, 1, NO]
    assert played_to_the_end(g, 0) == pytest.approx((5.0, 20.0), abs=0.05)
    assert played_to_the_end(g, 1) == pytest.approx((-9.0, 9.0 + 200.0), abs=0.05)
    assert played_to_the_end(g, 2) == pytest.approx((0.0, 240.0), abs=0.05)
    assert played_to_the_end(g, 3) == pytest.approx((0.0, 0.0), abs=1e-6)


def test_samples_have_room(g: Game) -> None:
    cap, most = a.EM_MOVES.SAMPLES.count, a.STEER_SPEC.SAMPLES.count
    for slot, start in enumerate(range(0, cap, most)):
        assert g.own(slot, 46, samples=[(slot, k) for k in range(min(most, cap - start))])
    full = slot + 1
    assert g.moves("SAMPLE_TOP") == cap and not g.own(full, 46, samples=[(full, 0)])
    assert g.own(full, 46) and g.own(full, 46, samples=[(0, 0), (0, 1)])  # held already
    g.lib.mhfu_em_moves_clear()
    assert g.moves("SAMPLE_TOP") == 0
    assert g.own(full, 46, samples=[(0, 0)] * 3) and g.moves("SAMPLE_TOP") == 3


def test_lunges_have_room_and_a_rewrite_adds_only_what_is_new(g: Game) -> None:
    def lunge(k: int) -> tuple[int, int, float, float, float, float]:
        return 0, 10, 400.0 + k, 1000.0, 0.0, 500.0

    for slot in range(a.EM_LUNGES):
        assert g.own(slot, 46, lunge=lunge(slot))
    assert not g.own(a.EM_LUNGES, 46, lunge=lunge(99)) and g.moves("LUNGE_TOP") == a.EM_LUNGES
    assert g.own(a.EM_LUNGES, 46, lunge=lunge(5)) and g.slot(a.EM_LUNGES, "LUNGE_AT") == 5
    assert g.own(3, 46, lunge=lunge(3)) and g.slot(3, "LUNGE_AT") == 3  # again: nothing new
    assert not g.own(3, 46, lunge=(0, 20, 1.0, 2.0, 3.0, 4.0))  # a changed one is new
    assert g.own(3, 46) and g.slot(3, "LUNGE_AT") == NO
    g.lib.mhfu_em_moves_clear()
    assert g.moves("LUNGE_TOP") == 0 and g.own(a.EM_LUNGES, 46, lunge=lunge(0))


def test_the_registry_places_runs_where_pools_does(g: Game) -> None:
    """Slots with equal runs, a run inside another and equal lunges: the registry's tops and each
    slot's offsets are `Pools`' for the same moves in the same order."""
    run = [(k, -k) for k in range(1, 9)]
    lunge = (0, 20, 100.0, 400.0, -300.0, 900.0)
    spec = [
        ([1, 2, 3, 4, 5, 6], run[:5], None),
        ([1, 2, 3, 4, 5, 6], run[:5], lunge),
        ([3, 4, 5], run[2:5], lunge),
        ([7, 7], [], (0, 20, 100.0, 400.0, -300.0, 901.0)),
        ([6, 7], run, lunge),
        ([], [], None),
        ([1, 2, 3, 4, 5, 6, 7], run[:5], (0, 20, 100.0, 400.0, -300.0, 901.0)),
    ]
    pools = Pools()
    for slot, (keys, samples, lunge_) in enumerate(spec):
        record = None if lunge_ is None else Lunge((lunge_[0], lunge_[1]), *lunge_[2:]).pack()
        want = pools.add(keys, samples, record)
        assert g.own(slot, 46, keys=keys, samples=samples, lunge=lunge_)
        got = (
            g.slot(slot, "KEY_AT", "H"),
            g.slot(slot, "SAMPLE_AT", "H"),
            g.slot(slot, "LUNGE_AT"),
        )
        assert got == (want[0], want[1], NO if want[2] is None else want[2])
    tops = (g.moves("KEY_TOP"), g.moves("SAMPLE_TOP"), g.moves("LUNGE_TOP"))
    assert tops == (len(pools.keys), len(pools.samples), len(pools.lunges))
    assert tops == (8, 13, 2)  # a run holds what is inside it; a longer one is not extended


PORT = '[port]\nname = "z"\nhost_species = 75\npac = "z.bin"\n[source]\nmodel = 5339\n'


def test_the_registry_holds_what_a_ports_capacity_check_counts(g: Game) -> None:
    """A port whose own moves repeat turn keys, travel and lunges, registered slot by slot as
    `mhfu_port.lua` does: the registry's tops are those of `moves.pools`, the check's count."""
    keys = (1, 2, 3, 4, 5, 6)
    lay = layout.Layout(
        {},
        turns={
            9: travel.Turn(12, keys, 0, None),
            10: travel.Turn(6, keys[2:5], 0, None),
            11: travel.Turn(12, keys[::-1], 0, None),
        },
    )
    path = [[0, 0], [1, 2], [3, 4]]
    rows = [  # entry, travel, the lunge's range
        (9, path, 900),
        (9, path, 900),
        (10, path[1:], 900),
        (11, path + [[5, 6]], 901),
        (10, [], None),
    ]
    text = PORT
    for i, (entry, route, reach) in enumerate(rows):
        text += f"[moves.m{i}]\nanim = {entry}\ncarrier = [0, 1]\ntravel = {route}\n"
        if reach is not None:
            text += f"[moves.m{i}.lunge]\nwindow = [0, 20]\nnear = 100\nspan = 400\nbase = -300\n"
            text += f"range = {reach}\n"
    m = manifest.loads(text)
    assert moves.problems(m, lay) == []
    held = moves.pools(m, lay)
    for name, slot in moves.slots(m).items():
        mv = m.moves[name]
        lng = mv.lunge
        assert g.own(
            slot,
            46,
            keys=moves.keys_of(moves.curve(m, name, lay)),
            samples=steering.samples(mv.travel or ()),
            lunge=None if lng is None else (*lng.window, lng.near, lng.span, lng.base, lng.range),
        )
    tops = (g.moves("KEY_TOP"), g.moves("SAMPLE_TOP"), g.moves("LUNGE_TOP"))
    assert tops == (len(held.keys), len(held.samples), len(held.lunges))
    assert tops == (12, 7, 2)  # of 24 keys, 12 samples and 4 lunges, each run held once


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


def test_a_counter_runs_before_the_rules_that_play_wherever_it_sits(g: Game) -> None:
    g.rule(0, from_mask=1 << 4, to_main=3, to_sub=6, conds=[cond("var_at_least", 0, 2)])
    g.rule(1, on=FLINCH, no_play=True, effects=[effect("var_add", 0, 1)])
    g.frame()
    g.flinch(0b001)
    assert g.var(0) == 1 and (3, 6) not in g.entered()
    g.flinch(0b001)
    assert g.var(0) == 2 and (3, 6) in g.entered()  # counted this frame, the rule above saw it


def test_a_playing_rule_does_not_starve_the_rules_that_count(g: Game) -> None:
    """A re-notice raises noticed and combat_entered in one frame: the rule on the notice plays,
    and the counter on combat_entered below it in the slots still counts."""
    g.rule(0, on=NOTICED, **{"to_main": 3, "to_sub": 6})
    g.rule(1, on=ENTERED, no_play=True, effects=[effect("var_set", 0, 1)])
    g.poke(a.ENTITY.FLAGS, "I", 0x8)  # the eye's own condition
    g.frame()
    g.poke(a.ENTITY.AWARE, "B", 1)
    g.poke(a.ENTITY.COMBAT_MODE, "B", 1)
    g.frame()
    assert (3, 6) in g.entered() and g.var(0) == 1


def test_a_break_that_flinches_is_counted_whatever_plays(g: Game) -> None:
    g.rule(0, on=BROKEN, to_main=3, to_sub=6)
    g.rule(1, on=FLINCH, no_play=True, effects=[effect("var_add", 0, 1)])
    g.frame()
    g.poke(a.ENTITY.FLINCH_MASK, "B", 0b001)
    g.poke(a.ENTITY.BROKEN, "H", 1)
    g.frame()
    assert (3, 6) in g.entered() and g.var(0) == 1


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


def flinch_part(g: Game, part: int, count: int) -> None:
    g.poke(a.ENTITY.FLINCH_PARTS + part * a.FLINCH_PART.size + a.FLINCH_PART.COUNT, "B", count)


def test_the_second_foreleg_break_reaches_its_rule(g: Game) -> None:
    """Both forelegs break at their first flinch with one BREAK_ID: ENTITY.BROKEN gains no bit for
    the second, and only the part that reached its count counts as broken."""
    g.lib.host_break_row(0, 0x4B, 4, 1, 1)
    g.lib.host_break_row(1, 0x4B, 6, 1, 1)
    g.poke(a.ENTITY.SPECIES, "B", 0x4B)
    g.rule(0, on=BROKEN, part=4, to_main=3, to_sub=6)
    g.rule(1, on=BROKEN, part=6, to_main=3, to_sub=7)
    g.frame()
    flinch_part(g, 4, 1)
    g.poke(a.ENTITY.FLINCH_MASK, "B", 1 << 4)
    g.poke(a.ENTITY.BROKEN, "H", 0b10)
    g.frame()
    assert g.entered() == [(3, 6)] and g.board("BROKEN") == 1 << 4
    flinch_part(g, 4, 2)
    flinch_part(g, 6, 1)
    g.poke(a.ENTITY.FLINCH_MASK, "B", (1 << 4) | (1 << 6))
    g.frame()
    assert g.entered() == [(3, 7)] and g.board("BROKEN") == (1 << 4) | (1 << 6)


TIGREX_ID, POPO_ID = 0x4B, 0x46


def latches(g: Game, species: int) -> int:
    g.poke(a.ENTITY.SPECIES, "B", species)
    return int(g.lib.mhfu_em_host_latches(ENT))


def test_only_a_target_species_latches(g: Game) -> None:
    g.lib.host_groups(1)
    g.lib.host_target(0, TIGREX_ID)
    assert latches(g, TIGREX_ID) == 1 and latches(g, POPO_ID) == 0


def test_every_target_group_counts(g: Game) -> None:
    g.lib.host_groups(2)
    g.lib.host_target(0, TIGREX_ID)
    g.lib.host_target(1, 0x4D)
    assert latches(g, 0x4D) == 1 and latches(g, POPO_ID) == 0


def test_a_group_past_the_count_does_not_count(g: Game) -> None:
    g.lib.host_groups(1)
    g.lib.host_target(0, TIGREX_ID)
    g.lib.host_target(1, POPO_ID)  # stale: the count is 1
    assert latches(g, POPO_ID) == 0


def test_a_wild_group_count_stops_at_the_groups_there_are(g: Game) -> None:
    g.lib.host_groups(0xFFFFFFFF)
    g.lib.host_target(0, TIGREX_ID)
    assert latches(g, TIGREX_ID) == 1 and latches(g, POPO_ID) == 0


def test_a_host_spawned_under_another_target_latches(g: Game) -> None:
    g.lib.host_groups(1)
    g.lib.host_target(0, 0x4D)  # a swap's record names the replaced species
    g.lib.host_big(1)
    assert latches(g, TIGREX_ID) == 1
    g.lib.host_big(0)
    assert latches(g, POPO_ID) == 0


def test_a_quest_without_groups_asks_the_species_list(g: Game) -> None:
    g.lib.host_big(1)
    assert latches(g, TIGREX_ID) == 1
    g.lib.host_big(0)
    assert latches(g, POPO_ID) == 0


HUB, SCRIPT_PAIR = (0, 2), (1, 3)  # the carrier of g.own; the pair the AI script enters next


def script_frame(g: Game) -> list[tuple[str, int, int, int, int]]:
    """An AI frame with the engine's SCRIPT_TICK after the host step: a set wake runs the script,
    which enters SCRIPT_PAIR (mode 1) and has its wake cleared after the run. Returns the calls."""
    g.frame()
    if g.peek(a.ENTITY.SCRIPT_WAKE, "B")[0]:
        g.lib.mhfu_host_enter(ENT, *SCRIPT_PAIR, 1)
        g.poke(a.ENTITY.SCRIPT_WAKE, "B", 0)
    return g.calls()


def script_enters(g: Game, slot: int) -> None:
    """`enter 16 slot 0` as the slot-32 stub turns it (test_em_vhook_stubs.py runs the stub): the
    carrier in mode 1, the forced play filed, and the wake the hub raised cleared by SCRIPT_TICK."""
    g.lib.mhfu_host_enter(ENT, *HUB, 1)
    assert g.lib.mhfu_em_play(ENT, slot, 1)
    g.poke(a.ENTITY.SCRIPT_WAKE, "B", 0)


def test_a_script_enter_plays_the_move_to_its_end(g: Game) -> None:
    """The play starts the AI frame after the enter, past the notice, and the script, woken by the
    hub twice, does not enter its pair until the carrier hands back."""
    g.own(2, 46)
    g.poke(a.ENTITY.AWARE, "B", 1)  # the notice runs: an unforced move would wait
    g.calls()
    script_enters(g, 2)
    assert g.calls() == [("E", ENT, *HUB, 1)]
    seen = script_frame(g)
    assert seen == [("E", ENT, *HUB, 0)] and g.lib.mhfu_em_playing() == 2
    for _ in range(2 + int(CLIP_END / SPEED)):
        seen += script_frame(g)
    assert g.move("END", "B") == CLIP and g.moves("PLAYS") == 1
    assert ("X", ENT, 46, 0, 0) in seen and ("E", ENT, *SCRIPT_PAIR, 1) not in seen
    g.poke(a.ENTITY.SCRIPT_WAKE, "B", 1)  # the carrier hands back: the hub wakes the script
    assert script_frame(g) == [("E", ENT, *SCRIPT_PAIR, 1)]


def test_a_script_enter_follows_its_after_chain(g: Game) -> None:
    g.own(0, 46, after=1)
    g.own(1, 53)
    script_enters(g, 0)
    seen = []
    for _ in range(4 + 2 * int(CLIP_END / SPEED)):
        seen += script_frame(g)
    assert [x[2] for x in seen if x[0] == "X"] == [46, 53]
    assert ("E", ENT, *SCRIPT_PAIR, 1) not in seen and g.moves("CHAINED") == 1


# --- fatigue

GAUGE_MAX = 120_000  # em75's gauge A
LOW = GAUGE_MAX * 30 // 100  # if_gauge_a_low: at or below it


def gauge_of(stamina: int, max: int = 1000) -> int:
    """Gauge A for a stamina that is not tired."""
    return LOW + 1 + (GAUGE_MAX - LOW - 1) * stamina // max


def armed(g: Game, **kw: object) -> None:
    """The helper armed on a monster at full HP with a full gauge A, and its first AI step taken."""
    g.poke(a.ENTITY.GAUGE_A_MAX, "i", GAUGE_MAX)
    g.gauge(GAUGE_MAX)
    g.poke(a.ENTITY.HP, "H", 100)
    g.poke(a.ENTITY.MAX_HP, "H", 100)
    assert g.fatigue(**kw)  # type: ignore[arg-type]
    g.frame()


def test_fatigue_starts_by_the_boards_roll_and_shows_gauge_a(g: Game) -> None:
    seen = set()
    for k in range(24):
        armed(g, starts=(400, 600), tired=100 + k)  # a new setting starts afresh, and rolls
        st = g.fat()
        seen.add(st.stamina)
        assert st.stamina in (400, 600) and g.gauge() == st.gauge == gauge_of(st.stamina)
    assert seen == {400, 600}


def test_a_pair_entered_is_charged_once(g: Game) -> None:
    armed(g, starts=(500, 500), pairs=((2, 10, 15), (4, 1, 50)))
    assert (g.fat().stamina, g.fat().charges) == (500, 0)  # the pair it starts in is free
    g.go((2, 10))
    g.frames(5)  # standing in it
    assert (g.fat().stamina, g.fat().charges) == (485, 1)
    g.go((1, 3))  # not in the table
    g.go((2, 10))
    g.go((4, 1))
    assert (g.fat().stamina, g.fat().charges) == (485 - 15 - 50, 3)
    assert g.gauge() == g.fat().gauge == gauge_of(420)


def test_costs_halve_at_low_hp(g: Game) -> None:
    armed(g, starts=(500, 500), low_hp=25, pairs=((2, 10, 20),))
    g.poke(a.ENTITY.HP, "H", 26)
    g.go((2, 10))
    assert g.fat().stamina == 480
    g.poke(a.ENTITY.HP, "H", 25)
    g.go((1, 3))
    g.go((2, 10))
    assert g.fat().stamina == 470


def test_nothing_is_charged_while_enraged(g: Game) -> None:
    armed(g, starts=(500, 500), pairs=((2, 10, 20), (2, 11, 600)))
    g.poke(a.ENTITY.FLAGS, "I", ENRAGED_FLAG)
    g.go((2, 10))
    g.go((2, 11))
    assert (g.fat().stamina, g.fat().charges, g.fat().tired) == (500, 0, 0)
    g.poke(a.ENTITY.FLAGS, "I", 0)
    g.go((1, 3))
    g.go((2, 11))  # a cost past the stamina: 0, and the spell begins
    assert (g.fat().stamina, g.fat().tired, g.fat().spells) == (0, 1, 1)


def test_a_tired_spell_waits_for_the_rage_to_end(g: Game) -> None:
    g.poke(a.ENTITY.FLAGS, "I", ENRAGED_FLAG)
    armed(g, starts=(0, 0))
    assert g.fat().tired == 0 and g.gauge() > LOW
    g.poke(a.ENTITY.FLAGS, "I", 0)
    g.frame()
    assert g.fat().tired == 1 and 1 <= g.gauge() <= LOW


def test_the_tired_spell(g: Game) -> None:
    """Gauge A reads low for `tired` AI frames, never 0, then the stamina is the refill."""
    armed(g, starts=(500, 500), tired=5, refill=500, pairs=((2, 10, 1000),))
    g.go((2, 10))
    st = g.fat()
    assert (st.stamina, st.tired, st.left, st.spells) == (0, 1, 5, 1)
    assert 1 <= g.gauge() <= LOW
    states = []
    for _ in range(6):
        g.frame()
        states.append((g.fat().tired, 1 <= g.gauge() <= LOW, g.gauge() > LOW))
    assert states == [(1, True, False)] * 4 + [(0, False, True)] * 2
    st = g.fat()
    assert (st.stamina, st.spells, g.gauge()) == (500, 1, gauge_of(500))
    g.go((1, 3))
    g.go((2, 10))  # nothing is charged while tired; here it is not tired again at once
    assert g.fat().tired == 1 and g.fat().spells == 2


def test_nothing_is_charged_in_a_tired_spell(g: Game) -> None:
    armed(g, starts=(500, 500), tired=50, pairs=((2, 10, 1000), (2, 11, -300)))
    g.go((2, 10))
    g.go((2, 11))  # a refill does not reach a tired monster either
    assert (g.fat().stamina, g.fat().charges) == (0, 1)


def test_calm_keeps_the_species_anger_off_while_tired(g: Game) -> None:
    g.poke(a.ENTITY.ANGER_THRESHOLD, "h", 600)
    armed(g, starts=(500, 500), calm=1, tired=3, pairs=((2, 10, 1000),))
    assert g.status().natural_rage == 1
    g.go((2, 10))
    g.frame()  # the brain takes the threshold the frame after
    assert g.status().natural_rage == 0 and g.peek(a.ENTITY.ANGER_THRESHOLD, "h") == (32767,)
    g.frames(3)
    assert g.status().natural_rage == 1 and g.peek(a.ENTITY.ANGER_THRESHOLD, "h") == (600,)


def test_calm_leaves_a_setting_lua_made(g: Game) -> None:
    g.lib.mhfu_em_natural_rage(0)
    armed(g, starts=(500, 500), calm=1, tired=3, pairs=((2, 10, 1000),))
    g.go((2, 10))
    g.frames(6)
    assert g.fat().spells == 1 and g.fat().tired == 0 and g.status().natural_rage == 0


def test_a_tired_spell_without_calm_leaves_the_anger(g: Game) -> None:
    armed(g, starts=(500, 500), tired=3, pairs=((2, 10, 1000),))
    g.go((2, 10))
    assert g.fat().tired == 1 and g.status().natural_rage == 1


def test_an_own_move_started_is_charged_by_its_slot(g: Game) -> None:
    armed(g, starts=(500, 500), own={0: 40, 1: 25})
    g.own(0, 46, after=1)
    g.own(1, 53)
    g.lib.mhfu_em_play(ENT, 0, 0)
    g.frame()  # the move player takes slot 0
    assert (g.fat().stamina, g.fat().charges) == (460, 1)
    g.frames(2 + int(CLIP_END / SPEED) + 2)  # its end, then AFTER's slot 1
    assert (g.fat().stamina, g.fat().charges) == (435, 2)
    assert g.gauge() == gauge_of(435)


def test_an_own_move_in_place_of_a_flinch_is_charged(g: Game) -> None:
    armed(g, starts=(500, 500), own={0: 30})
    g.own(0, 59)
    g.rule(0, on=FLINCH, part=0, play_move=1, count=1)
    g.frame()
    g.flinch(0b001)
    assert g.fat().stamina == 470


def test_an_own_move_at_low_hp_halves_and_a_free_slot_costs_nothing(g: Game) -> None:
    armed(g, starts=(500, 500), low_hp=50, own={0: 40})
    g.poke(a.ENTITY.HP, "H", 50)
    g.own(0, 46)
    g.own(1, 53)
    g.lib.mhfu_em_play(ENT, 0, 0)
    g.frame()
    assert g.fat().stamina == 480
    g.lib.mhfu_em_moves_clear()
    g.own(1, 53)
    g.lib.mhfu_em_play(ENT, 1, 1)
    g.frame()
    assert (g.fat().stamina, g.fat().charges) == (480, 1)


def test_arming_again_with_the_same_settings_keeps_the_stamina(g: Game) -> None:
    kw = {"starts": (500, 500), "pairs": ((2, 10, 15),)}
    armed(g, **kw)
    g.go((2, 10))
    assert g.fatigue(**kw) == 1  # type: ignore[arg-type]
    g.frame()
    assert (g.fat().stamina, g.fat().armed) == (485, 1)
    assert g.fatigue(max=900, starts=(300, 300)) == 1  # other settings start afresh
    g.frame()
    assert g.fat().stamina == 300 and g.gauge() == gauge_of(300, 900)


def test_a_refused_setting_arms_nothing(g: Game) -> None:
    assert g.fatigue(max=0) == 0 and g.fatigue(refill=2000) == 0 and g.fatigue(tired=0) == 0
    assert g.fatigue(starts=(2000, 1)) == 0 and g.fatigue(low_hp=101) == 0
    assert g.fat().armed == 0
    armed(g)
    assert g.fatigue(max=0) == 0 and g.fat().armed == 1  # a refusal leaves the armed ones


def test_the_helper_ends_with_none_a_clear_or_the_quest(g: Game) -> None:
    for end in (
        lambda: g.lib.mhfu_em_fatigue(None),
        g.lib.mhfu_em_clear,
        g.lib.mhfu_em_host_quest,
    ):
        armed(g, starts=(500, 500))
        assert g.fat().armed == 1
        end()
        assert g.fat().armed == 0
        g.gauge(777)
        g.frames(2)
        assert g.gauge() == 777 and g.fat().stamina == 0  # nothing writes, nothing charges


def test_a_new_monster_starts_afresh(g: Game) -> None:
    armed(g, starts=(500, 500), pairs=((2, 10, 15),))
    g.go((2, 10))
    g.lib.mhfu_em_host_spawn(ENT2)
    st = g.fat()
    assert (st.stamina, st.charges, st.armed) == (0, 0, 1)
    struct.pack_into("<ii", g.mem, ENT2 - BASE + a.ENTITY.GAUGE_A, 0, 0)
    struct.pack_into("<i", g.mem, ENT2 - BASE + a.ENTITY.GAUGE_A_MAX, GAUGE_MAX)
    g.lib.mhfu_em_host_frame(ENT2)
    assert g.fat().stamina == 500
    (written,) = struct.unpack_from("<i", g.mem, ENT2 - BASE + a.ENTITY.GAUGE_A)
    assert written == gauge_of(500)


def test_only_the_boards_monster_is_charged_and_shown(g: Game) -> None:
    armed(g, starts=(500, 500), pairs=((2, 10, 15),))
    struct.pack_into("<i", g.mem, ENT2 - BASE + a.ENTITY.GAUGE_A_MAX, GAUGE_MAX)
    struct.pack_into("BB", g.mem, ENT2 - BASE + a.ENTITY.MAIN_STATE, 1, 3)
    g.lib.mhfu_em_host_frame(ENT2)
    struct.pack_into("BB", g.mem, ENT2 - BASE + a.ENTITY.MAIN_STATE, 2, 10)
    g.lib.mhfu_em_host_frame(ENT2)
    assert (g.fat().stamina, g.fat().charges) == (500, 0)
    assert struct.unpack_from("<i", g.mem, ENT2 - BASE + a.ENTITY.GAUGE_A) == (0,)


def test_a_gauge_the_engine_refilled_is_written_back(g: Game) -> None:
    armed(g, starts=(500, 500))
    g.gauge(0)  # a spawn refill, or anything else
    g.frame()
    assert g.gauge() == gauge_of(500)


def test_a_monster_without_gauge_a_is_left_alone(g: Game) -> None:
    g.poke(a.ENTITY.GAUGE_A_MAX, "i", 0)
    g.gauge(0)
    assert g.fatigue(starts=(500, 500))
    g.frames(3)
    assert g.gauge() == 0 and g.fat().stamina == 500


def test_the_gauge_is_never_0_through_a_fight(g: Game) -> None:
    """Costs, tired spells and refills in a row: the gauge is above 0 after every AI frame, and
    low exactly while tired."""
    armed(g, starts=(500, 500), tired=4, refill=300, pairs=((2, 10, 200), (2, 11, 100)))
    for k in range(60):
        g.pair = (2, 10) if k % 3 == 0 else (2, 11) if k % 3 == 1 else (1, 3)
        g.frame()
        assert g.gauge() >= 1
        assert (g.gauge() <= LOW) == bool(g.fat().tired)
    assert g.fat().spells >= 2


# --- charge

CHARGED_Q, COOLDOWN_Q = int(a.CHARGE_QUERY_CHARGED), int(a.CHARGE_QUERY_COOLDOWN)
UP_A, UP_B, XFORM = 0, 1, 2  # own-move slots: two charge-ups and the transform
XFORM_ENTRY = 59


def charge_armed(g: Game, **kw: object) -> None:
    """Three own moves, the helper armed on a monster at full HP, and its first AI step taken."""
    g.own(UP_A, 46)
    g.own(UP_B, 53)
    g.own(XFORM, XFORM_ENTRY)
    g.poke(a.ENTITY.MAX_HP, "H", 100)
    g.hp(100)
    assert g.charge(**kw)  # type: ignore[arg-type]
    g.frame()


def start(g: Game, slot: int) -> None:
    """The move player takes own move `slot` in the next AI frame, as for the script's enter."""
    assert g.lib.mhfu_em_play(ENT, slot, 1)
    g.frame()


def charged(g: Game, **kw: object) -> None:
    """Armed and charged: one charge-up that fills the gauge, then the transform."""
    charge_armed(g, **kw)
    start(g, UP_A)
    g.until(lambda: g.chg().charged == 1)
    g.calls()


def test_charge_answers_for_nobody_before_the_first_ai_step(g: Game) -> None:
    g.own(UP_A, 46)
    assert g.charge() == 1 and g.chg().armed == 1
    assert [g.ask(k) for k in range(8)] == [0] * 8 and g.chg().asked == 0
    g.frame()
    assert g.chg().armed == 1 and g.ask(CHARGED_Q) == 0 and g.chg().asked == 1


def test_a_charge_up_raises_the_gauge_by_hp_and_starts_the_cooldown(g: Game) -> None:
    ups = {UP_A: (20, 15, 10, 5), UP_B: (40, 30, 25, 5)}
    for hp, gain in ((100, 20), (95, 20), (94, 15), (75, 15), (74, 10), (40, 10), (39, 5), (1, 5)):
        g.lib.mhfu_em_host_quest()
        g.lib.mhfu_em_init()
        g.lib.mhfu_em_host_spawn(ENT)
        charge_armed(g, ups=ups)
        g.hp(hp)
        g.frame()  # a hit, which the gauge's chip (0) does not mind
        start(g, UP_A)
        st = g.chg()
        assert (st.gauge, st.cooldown, st.up_starts) == (gain, 420, 1), hp
        assert (g.ask(COOLDOWN_Q), g.ask(CHARGED_Q)) == (1, 0)


def test_the_cooldown_runs_down_every_ai_frame(g: Game) -> None:
    charge_armed(g, ups={UP_A: (10, 10, 10, 10)}, up=5)
    assert g.ask(COOLDOWN_Q) == 0
    start(g, UP_A)
    seen = []
    for _ in range(6):
        seen.append((g.chg().cooldown, g.ask(COOLDOWN_Q)))
        g.frame()
    assert seen == [(5, 1), (4, 1), (3, 1), (2, 1), (1, 1), (0, 0)]


def test_a_full_gauge_plays_the_transform_after_the_charge_up(g: Game) -> None:
    charge_armed(g)
    start(g, UP_A)
    assert g.chg().gauge == 100 and g.lib.mhfu_em_playing() == UP_A
    seen = []
    for _ in range(40):
        g.frame()
        seen += g.calls()
        if g.chg().charged:
            break
    played = [c[2] for c in seen if c[0] == "X"]
    assert played[:1] == [46]  # the transform waited for the up to end
    st = g.chg()
    assert (st.charged, st.gauge, st.durability, st.transforms) == (1, 100, 100, 1)
    assert (g.ask(CHARGED_Q), g.ask(COOLDOWN_Q)) == (1, 1)
    seen = []
    for _ in range(30):
        g.frame()
        seen += g.calls()
    assert [c[2] for c in seen if c[0] == "X"] == [XFORM_ENTRY]  # asked once, and played
    assert g.chg().transforms == 1 and g.lib.mhfu_em_playing() == -1


def test_the_transform_waits_for_the_chain_of_the_move_before(g: Game) -> None:
    charge_armed(g)
    g.own(UP_A, 46, after=UP_B)
    start(g, UP_A)
    g.calls()
    seen = []
    for _ in range(80):
        g.frame()
        seen += g.calls()
    assert [c[2] for c in seen if c[0] == "X"] == [46, 53, XFORM_ENTRY]  # the chain, then ours
    assert g.chg().charged == 1


def test_no_transform_slot_never_plays_one(g: Game) -> None:
    charge_armed(g, transform=0xFF)
    start(g, UP_A)
    seen = []
    for _ in range(40):
        g.frame()
        seen += g.calls()
    assert (g.chg().gauge, g.chg().charged) == (100, 0)
    assert XFORM_ENTRY not in [c[2] for c in seen if c[0] == "X"]


def test_hits_wear_the_gauge_then_the_durability(g: Game) -> None:
    charge_armed(g, ups={UP_A: (50, 50, 50, 50)}, chip_gauge=8, chip_durability=40)
    start(g, UP_A)
    assert g.chg().gauge == 50
    g.hp(90)
    g.frame()
    assert (g.chg().gauge, g.chg().hits) == (42, 1)
    g.hp(95)  # healing is no hit
    g.frame()
    g.frame()
    assert (g.chg().gauge, g.chg().hits) == (42, 1)
    g.hp(60)  # several hits in one AI frame count once
    g.frame()
    assert (g.chg().gauge, g.chg().hits) == (34, 2)


def test_a_charged_monster_is_discharged_by_hits(g: Game) -> None:
    charged(g, chip_gauge=8, chip_durability=40, out=700)
    assert g.chg().durability == 100
    for left, hp in ((60, 90), (20, 80)):
        g.hp(hp)
        g.frame()
        st = g.chg()
        assert (st.charged, st.durability, st.gauge) == (1, left, 100)
    g.hp(70)
    g.frame()
    st = g.chg()
    assert (st.charged, st.gauge, st.durability, st.cooldown) == (0, 0, 0, 700)
    assert (st.discharges, st.hits) == (1, 3)
    assert (g.ask(CHARGED_Q), g.ask(COOLDOWN_Q)) == (0, 1)
    g.frames(700)
    assert g.ask(COOLDOWN_Q) == 0


def test_the_cooldown_stands_while_charged(g: Game) -> None:
    charged(g, up=500)
    c = g.chg().cooldown
    g.frames(25)
    assert g.chg().cooldown == c and g.ask(COOLDOWN_Q) == 1


def test_a_discharge_pair_discharges_a_charged_monster_at_once(g: Game) -> None:
    charged(g, pairs=((11, 0), (11, 1)))
    g.go((2, 10))
    assert g.chg().charged == 1
    g.go((11, 1))
    st = g.chg()
    assert (st.charged, st.gauge, st.cooldown, st.discharges) == (0, 0, 900, 1)
    assert g.ask(CHARGED_Q) == 0


def test_the_transform_is_refused_in_a_discharge_pair(g: Game) -> None:
    charge_armed(g, pairs=((11, 0),))
    start(g, UP_A)
    g.pair = (11, 0)  # the knockout: it ends the move, the helper must not transform
    seen = []
    for _ in range(40):
        g.frame()
        seen += g.calls()
    assert g.chg().charged == 0 and XFORM_ENTRY not in [c[2] for c in seen if c[0] == "X"]
    g.pair = (1, 3)
    g.until(lambda: g.chg().charged == 1, 10)


def test_the_state_stays_across_an_area_change(g: Game) -> None:
    charged(g, chip_durability=30)
    g.hp(95)
    g.frame()
    before = g.chg()
    for section, pair in ((0x0102, (2, 10)), (0x0103, (1, 3)), (0x0104, (2, 11))):
        g.poke(a.ENTITY.SECTION, "H", section)  # the monster moves to the player's next area
        g.go(pair)
        g.frames(3)
        st = g.chg()
        assert (st.charged, st.durability, st.gauge) == (1, before.durability, 100)
        assert (g.ask(CHARGED_Q), g.ask(COOLDOWN_Q)) == (1, 1 if st.cooldown else 0)


def test_a_new_monster_starts_afresh_and_keeps_the_settings(g: Game) -> None:
    charged(g)
    g.lib.mhfu_em_host_spawn(ENT2)
    st = g.chg()
    assert (st.armed, st.charged, st.gauge, st.up_starts, st.transforms) == (1, 0, 0, 0, 0)
    assert g.ask(CHARGED_Q) == 0 and g.ask(CHARGED_Q, ENT2) == 0  # nobody owns it yet
    struct.pack_into("<HH", g.mem, ENT2 - BASE + a.ENTITY.HP, 100, 0)
    g.lib.mhfu_em_host_frame(ENT2)
    assert g.chg().armed == 1 and g.chg().charged == 0


def test_only_the_boards_monster_is_tracked_and_answered(g: Game) -> None:
    charged(g, chip_durability=10)
    struct.pack_into("<H", g.mem, ENT2 - BASE + a.ENTITY.HP, 100)
    g.lib.mhfu_em_host_frame(ENT2)
    struct.pack_into("<H", g.mem, ENT2 - BASE + a.ENTITY.HP, 10)
    g.lib.mhfu_em_host_frame(ENT2)
    assert (g.chg().hits, g.chg().durability) == (0, 100)
    assert (g.ask(CHARGED_Q), g.ask(CHARGED_Q, ENT2), g.ask(COOLDOWN_Q, ENT2)) == (1, 0, 0)


def test_the_query_answers_nothing_but_the_two(g: Game) -> None:
    charged(g, chip_durability=10)
    assert [g.ask(k) for k in range(12)] == [1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0]


def test_the_charge_ends_with_none_a_clear_or_the_quest(g: Game) -> None:
    for end in (
        lambda: g.lib.mhfu_em_charge(None),
        g.lib.mhfu_em_clear,
        g.lib.mhfu_em_host_quest,
    ):
        g.lib.mhfu_em_host_quest()
        charged(g)
        assert g.ask(CHARGED_Q) == 1
        end()
        st = g.chg()
        assert (st.armed, st.charged, st.gauge) == (0, 0, 0)
        assert g.ask(CHARGED_Q) == 0 and g.ask(COOLDOWN_Q) == 0
        g.hp(10)
        g.frames(2)
        assert g.chg().hits == 0  # nothing counts


def test_arming_again_with_the_same_settings_keeps_the_state(g: Game) -> None:
    charged(g)
    assert g.charge() == 1
    assert (g.chg().charged, g.chg().transforms) == (1, 1)
    assert g.charge(chip_gauge=1) == 1  # other settings start afresh
    g.frame()
    assert (g.chg().charged, g.chg().armed) == (0, 1)


def test_a_refused_setting_arms_nothing_and_leaves_the_armed_ones(g: Game) -> None:
    slots = a.EM_MOVES.MOVES.count or 0
    assert g.charge(transform=slots) == 0 and g.charge(ups={slots: (1, 1, 1, 1)}) == 0
    assert g.charge(ups={0: (1, 1, 1, 101)}) == 0 and g.charge(chip_gauge=-1) == 0
    assert g.charge(chip_durability=101) == 0
    assert g.chg().armed == 0
    charge_armed(g)
    assert g.charge(chip_gauge=-1) == 0 and g.chg().armed == 1


def test_a_transform_started_from_anywhere_charges(g: Game) -> None:
    charge_armed(g)
    start(g, XFORM)  # the script, a rule or Lua plays it with the gauge low
    st = g.chg()
    assert (st.charged, st.gauge, st.durability, st.transforms) == (1, 100, 100, 1)


def test_a_charge_up_a_rule_or_the_script_plays_counts(g: Game) -> None:
    charge_armed(g, ups={UP_A: (30, 30, 30, 30), UP_B: (5, 5, 5, 5)})
    g.rule(0, from_mask=1 << 1, min_frames=2, play_move=UP_B + 1, count=1)
    g.frames(3)
    assert g.chg().gauge == 5
    g.until(lambda: g.lib.mhfu_em_playing() == -1, 40)
    script_enters(g, UP_A)
    script_frame(g)
    assert (g.chg().gauge, g.chg().up_starts) == (35, 2)


def test_a_charged_monster_gains_nothing_from_a_charge_up(g: Game) -> None:
    charged(g)
    c = g.chg().cooldown
    start(g, UP_A)
    st = g.chg()
    assert (st.up_starts, st.cooldown) == (1, c)


def test_a_dead_monster_does_not_transform(g: Game) -> None:
    charge_armed(g)
    start(g, UP_A)
    g.hp(0)
    g.frames(40)
    assert (g.chg().charged, g.chg().gauge) == (0, 100)


def test_the_transform_waits_for_the_tail_cut(g: Game) -> None:
    charge_armed(g)
    start(g, UP_A)
    g.until(lambda: g.lib.mhfu_em_playing() == -1, 40)
    cut(g)  # em75's tail cut, the drop to come
    seen = []
    for _ in range(3):
        g.frame()
        seen += g.calls()
    assert g.chg().charged == 0 and XFORM_ENTRY not in [c[2] for c in seen if c[0] == "X"]
    drop(g)
    g.until(lambda: g.chg().charged == 1, 20)
