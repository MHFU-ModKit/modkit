# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The fatigue helper's arithmetic and state machine (src/core/fatigue_logic.h) on the host. The
brain's use of it, against a game, is in test_em_brain.py."""

import ctypes
import struct
from collections.abc import Callable

import pytest
from mhfu import addresses as a

F = a.FATIGUE
C = a.FATIGUE_CFG
GAUGE_MAX = 120000  # em75's gauge A


@pytest.fixture(scope="module")
def lib(host_lib: Callable[..., ctypes.CDLL]) -> ctypes.CDLL:
    lib = host_lib("tests/fatigue_host.cpp")
    i32, u32, vp = ctypes.c_int32, ctypes.c_uint32, ctypes.c_void_p
    lib.fat_host_low.restype = i32
    lib.fat_host_low.argtypes = [i32]
    lib.fat_host_gauge.restype = i32
    lib.fat_host_gauge.argtypes = [i32, i32, ctypes.c_int, i32]
    lib.fat_host_check.restype = ctypes.c_char_p
    lib.fat_host_check.argtypes = [vp]
    lib.fat_host_pair_cost.restype = i32
    lib.fat_host_pair_cost.argtypes = [vp, u32, u32]
    lib.fat_host_own_cost.restype = i32
    lib.fat_host_own_cost.argtypes = [vp, u32]
    lib.fat_host_low_hp.argtypes = [u32, u32, u32]
    lib.fat_host_reset.argtypes = [vp]
    lib.fat_host_start.argtypes = [vp, u32, u32]
    lib.fat_host_charge.argtypes = [vp, i32, ctypes.c_int]
    lib.fat_host_settle.argtypes = [vp, ctypes.c_int]
    lib.fat_host_tick.argtypes = [vp]
    return lib


class Block:
    """A FATIGUE block in memory, its fields by name."""

    def __init__(self, **cfg: int) -> None:
        self.buf = ctypes.create_string_buffer(int(F.size or 0))
        base = int(F.CFG)
        self.put(base + int(C.MAX), "i", cfg.get("max", 1000))
        self.put(base + int(C.STARTS), "ii", cfg.get("a", 400), cfg.get("b", 600))
        self.put(base + int(C.REFILL), "i", cfg.get("refill", 500))
        self.put(base + int(C.TIRED_FRAMES), "I", cfg.get("tired", 3))
        self.put(base + int(C.LOW_HP_PCT), "B", cfg.get("low_hp", 0))

    def put(self, at: int, fmt: str, *v: int) -> None:
        struct.pack_into("<" + fmt, self.buf, at, *v)

    def get(self, field: str, fmt: str = "i") -> int:
        return int(struct.unpack_from("<" + fmt, self.buf, int(getattr(F, field)))[0])

    def pairs(self, *rows: tuple[int, int, int]) -> None:
        base = int(F.CFG) + int(C.PAIRS)
        for k, row in enumerate(rows):
            self.put(base + 4 * k, "BBh", *row)
        self.put(int(F.CFG) + int(C.PAIR_COUNT), "H", len(rows))

    def own(self, slot: int, cost: int) -> None:
        self.put(int(F.CFG) + int(C.OWN) + 2 * slot, "h", cost)


def engine_low(gauge_max: int) -> int:
    """if_gauge_a_low's threshold as the engine computes it: trunc(max * 0.3f), in float32."""

    def f32(x: float) -> float:
        return float(ctypes.c_float(x).value)

    return int(f32(f32(gauge_max) * f32(0.3)))


def test_layout() -> None:
    assert (a.ENTITY.GAUGE_A, a.ENTITY.GAUGE_A_MAX) == (0x498, 0x4A0)
    assert C.OWN.count == a.EM_MOVES.MOVES.count
    assert F.CFG + C.size == F.STAMINA


def test_low_threshold_is_the_engines(lib: ctypes.CDLL) -> None:
    for gauge_max in [*range(0, 400_001, 7), GAUGE_MAX]:
        assert lib.fat_host_low(gauge_max) == engine_low(gauge_max), gauge_max
    assert lib.fat_host_low(GAUGE_MAX) == 36000
    assert lib.fat_host_low(-5) == 0
    for gauge_max in (2**31 - 1, 2**31 - 2, 10**9 + 7):  # past a 32-bit product
        assert lib.fat_host_low(gauge_max) == gauge_max * 30 // 100


def test_gauge_values(lib: ctypes.CDLL) -> None:
    """Full stamina fills the gauge, 0 stays one above the threshold; tired is half of it."""
    g = lib.fat_host_gauge
    t = engine_low(GAUGE_MAX)
    assert g(1000, 1000, 0, GAUGE_MAX) == GAUGE_MAX
    assert g(1000, 0, 0, GAUGE_MAX) == t + 1
    assert g(1000, 500, 0, GAUGE_MAX) == t + 1 + (GAUGE_MAX - t - 1) // 2
    assert g(1000, 400, 1, GAUGE_MAX) == g(1000, 0, 1, GAUGE_MAX) == t // 2


def test_gauge_is_low_exactly_when_tired(lib: ctypes.CDLL) -> None:
    """if_gauge_a_low is gauge <= threshold: never for a stamina, always while tired, and the
    gauge is never 0, which a spawn would refill."""
    for gauge_max in (GAUGE_MAX, 150_000, 1000, 11, 4, 1):
        t = engine_low(gauge_max)
        for stamina in range(0, 1001, 13):
            now = lib.fat_host_gauge(1000, stamina, 0, gauge_max)
            assert now > t and now >= 1, (gauge_max, stamina)
            assert now >= lib.fat_host_gauge(1000, max(stamina - 13, 0), 0, gauge_max)
        tired = lib.fat_host_gauge(1000, 0, 1, gauge_max)
        assert tired >= 1
        if gauge_max >= 4:  # a gauge of 1 to 3 has a threshold of 0, which no value is at or below
            assert tired <= t, gauge_max


def test_gauge_clamps_its_stamina(lib: ctypes.CDLL) -> None:
    g = lib.fat_host_gauge
    assert g(1000, 5000, 0, GAUGE_MAX) == GAUGE_MAX
    assert g(1000, -5, 0, GAUGE_MAX) == g(1000, 0, 0, GAUGE_MAX)
    assert g(0, 5, 0, GAUGE_MAX) >= 1  # a maximum being rewritten reads as 1


def test_gauge_arithmetic_is_exact_in_32_bits(lib: ctypes.CDLL) -> None:
    """The gauge is t + 1 + floor((max_gauge - t - 1) * s / max), for any gauge the game holds."""
    for gauge_max in (1, 4, 99, 100, 101, GAUGE_MAX, 123_457, 2**31 - 1):
        t = gauge_max * 30 // 100
        for top in (1, 7, 1000, 29_999, 30_000):
            for stamina in sorted({0, 1, top // 3, top // 2, top - 1, top}):
                want = t + 1 + (gauge_max - t - 1) * stamina // top
                assert lib.fat_host_gauge(top, stamina, 0, gauge_max) == want


def test_charge_halves_clamps_and_counts(lib: ctypes.CDLL) -> None:
    b = Block()
    lib.fat_host_start(b.buf, 0, 0x0102)
    assert (b.get("STAMINA"), b.get("SEEN", "I"), b.get("PAIR", "I")) == (400, 1, 0x0102)
    lib.fat_host_charge(b.buf, 15, 0)
    lib.fat_host_charge(b.buf, 15, 1)  # low HP: 7
    assert (b.get("STAMINA"), b.get("CHARGES", "I")) == (400 - 15 - 7, 2)
    lib.fat_host_charge(b.buf, 0, 0)  # free
    assert b.get("CHARGES", "I") == 2
    lib.fat_host_charge(b.buf, -2000, 1)  # a refill is not halved, and stops at the maximum
    assert b.get("STAMINA") == 1000
    lib.fat_host_charge(b.buf, 5000, 0)
    assert b.get("STAMINA") == 0


def test_start_picks_by_the_roll(lib: ctypes.CDLL) -> None:
    for roll, want in ((0, 400), (1, 600), (0x100, 400), (0x101, 600)):
        b = Block()
        lib.fat_host_start(b.buf, roll, 0)
        assert b.get("STAMINA") == want


def test_tired_spell(lib: ctypes.CDLL) -> None:
    """At 0 outside rage the spell begins; it lasts `tired` frames and ends refilled; a tired
    monster is not charged."""
    b = Block(tired=3, refill=500)
    lib.fat_host_start(b.buf, 0, 0)
    lib.fat_host_charge(b.buf, 400, 0)
    assert b.get("STAMINA") == 0
    assert lib.fat_host_settle(b.buf, 1) == 0 and b.get("TIRED", "I") == 0  # enraged: waits
    assert lib.fat_host_settle(b.buf, 0) == 1
    assert (b.get("TIRED", "I"), b.get("LEFT", "I"), b.get("SPELLS", "I")) == (1, 3, 1)
    assert lib.fat_host_settle(b.buf, 0) == 0 and b.get("SPELLS", "I") == 1
    lib.fat_host_charge(b.buf, -300, 0)  # nothing while tired
    assert b.get("STAMINA") == 0
    assert [lib.fat_host_tick(b.buf) for _ in range(3)] == [0, 0, 1]
    assert (b.get("TIRED", "I"), b.get("STAMINA")) == (0, 500)
    assert lib.fat_host_tick(b.buf) == 0


def test_a_start_of_zero_is_tired_at_once(lib: ctypes.CDLL) -> None:
    b = Block(a=0, b=0)
    lib.fat_host_start(b.buf, 0, 0)
    assert lib.fat_host_settle(b.buf, 0) == 1


def test_reset_keeps_the_settings(lib: ctypes.CDLL) -> None:
    b = Block(max=800)
    b.pairs((2, 10, 15))
    lib.fat_host_start(b.buf, 1, 5)
    lib.fat_host_charge(b.buf, 100, 0)
    lib.fat_host_reset(b.buf)
    assert [b.get(k, "I") for k in ("SEEN", "TIRED", "CHARGES", "PAIR", "HELD")] == [0] * 5
    assert b.get("STAMINA") == 0 and b.get("GAUGE") == 0
    assert lib.fat_host_pair_cost(b.buf, 2, 10) == 15
    assert struct.unpack_from("<i", b.buf, int(F.CFG) + int(C.MAX)) == (800,)


def test_cost_tables(lib: ctypes.CDLL) -> None:
    b = Block()
    b.pairs((2, 10, 15), (7, 1, 5), (4, 0, -50))
    assert [lib.fat_host_pair_cost(b.buf, m, s) for m, s in ((2, 10), (7, 1), (4, 0))] == [
        15,
        5,
        -50,
    ]
    assert lib.fat_host_pair_cost(b.buf, 2, 11) == 0 and lib.fat_host_pair_cost(b.buf, 0, 2) == 0
    b.own(3, 40)
    n = int(a.EM_MOVES.MOVES.count or 0)
    assert [lib.fat_host_own_cost(b.buf, k) for k in (0, 3, n - 1, n, 1000)] == [0, 40, 0, 0, 0]


def test_low_hp(lib: ctypes.CDLL) -> None:
    assert lib.fat_host_low_hp(25, 100, 25) == 1
    assert lib.fat_host_low_hp(26, 100, 25) == 0
    assert lib.fat_host_low_hp(0, 100, 0) == 0  # no percent: never
    assert lib.fat_host_low_hp(10, 0, 25) == 0  # no maximum: never


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("MAX", 0, b"max"),
        ("MAX", 30_001, b"max"),
        ("REFILL", 0, b"refill"),
        ("REFILL", 2000, b"refill"),
        ("TIRED_FRAMES", 0, b"tired"),
    ],
)
def test_check_refuses(lib: ctypes.CDLL, field: str, value: int, error: bytes) -> None:
    b = Block()
    assert lib.fat_host_check(ctypes.byref(b.buf, int(F.CFG))) is None
    struct.pack_into("<I", b.buf, int(F.CFG) + int(getattr(C, field)), value & 0xFFFFFFFF)
    msg = lib.fat_host_check(ctypes.byref(b.buf, int(F.CFG)))
    assert msg is not None and error in msg


def test_check_refuses_starts_percent_and_pairs(lib: ctypes.CDLL) -> None:
    def check(b: Block) -> bytes | None:
        return lib.fat_host_check(ctypes.byref(b.buf, int(F.CFG)))  # type: ignore[no-any-return]

    b = Block(a=1001)
    assert (check(b) or b"").startswith(b"a start")
    b = Block(b=-1)
    assert (check(b) or b"").startswith(b"a start")
    b = Block(low_hp=101)
    assert check(b) == b"low_hp is a percent"
    b = Block()
    b.put(int(F.CFG) + int(C.PAIR_COUNT), "H", int(C.PAIRS.count or 0) + 1)
    assert check(b) == b"too many pairs"
