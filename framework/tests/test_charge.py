# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The charge helper's arithmetic and state machine (src/core/charge_logic.h) on the host. The
brain's use of it, against a game, is in test_em_brain.py; the query stub's, in
test_em_vhook_stubs.py."""

import ctypes
import struct
from collections.abc import Callable

import pytest
from mhfu import addresses as a

C = a.CHARGE
G = a.CHARGE_CFG
NO_SLOT = 0xFF
FULL = 100
CHARGED, COOLDOWN = int(a.CHARGE_QUERY_CHARGED), int(a.CHARGE_QUERY_COOLDOWN)
PAIR_KO = (11 << 8) | 0


@pytest.fixture(scope="module")
def lib(host_lib: Callable[..., ctypes.CDLL]) -> ctypes.CDLL:
    lib = host_lib("tests/charge_host.cpp")
    u32, vp = ctypes.c_uint32, ctypes.c_void_p
    lib.chg_host_check.restype = ctypes.c_char_p
    lib.chg_host_check.argtypes = [vp]
    lib.chg_host_band.argtypes = [u32, u32]
    lib.chg_host_up_index.argtypes = [vp, u32]
    lib.chg_host_pair.argtypes = [vp, u32]
    for name in ("reset", "answers", "tick", "hit"):
        getattr(lib, f"chg_host_{name}").argtypes = [vp]
    lib.chg_host_start.argtypes = [vp, u32, u32]
    lib.chg_host_up_start.argtypes = [vp, u32, u32, u32]
    lib.chg_host_transform_start.argtypes = [vp, u32]
    lib.chg_host_discharge.argtypes = [vp]
    lib.chg_host_settle.argtypes = [vp, u32]
    lib.chg_host_wants_transform.argtypes = [vp]
    return lib


class Block:
    """A CHARGE block in memory: a Zinogre-like setting unless told otherwise."""

    def __init__(
        self,
        *,
        transform: int = 9,
        ups: tuple[tuple[int, tuple[int, int, int, int]], ...] = (
            (3, (20, 15, 10, 5)),
            (4, (30, 25, 20, 10)),
        ),
        pairs: tuple[tuple[int, int], ...] = ((11, 0), (11, 1)),
        chip_gauge: int = 5,
        chip_durability: int = 10,
        up: int = 420,
        out: int = 900,
    ) -> None:
        self.buf = ctypes.create_string_buffer(int(C.size or 0))
        self.cfg = ctypes.create_string_buffer(int(G.size or 0))
        struct.pack_into("<BBB", self.cfg, G.TRANSFORM, transform, len(ups), len(pairs))
        struct.pack_into("<hhHH", self.cfg, G.CHIP_GAUGE, chip_gauge, chip_durability, up, out)
        for k, (slot, gain) in enumerate(ups):
            struct.pack_into("<Bxhhhh", self.cfg, int(G.UPS) + 10 * k, slot, *gain)
        for k, (m, s) in enumerate(pairs):
            struct.pack_into("<BB", self.cfg, int(G.PAIRS) + 2 * k, m, s)
        self.buf[int(C.CFG) : int(C.CFG) + int(G.size or 0)] = self.cfg.raw

    def get(self, field: str, fmt: str = "i") -> int:
        return int(struct.unpack_from("<" + fmt, self.buf, int(getattr(C, field)))[0])

    def put(self, field: str, v: int, fmt: str = "i") -> None:
        struct.pack_into("<" + fmt, self.buf, int(getattr(C, field)), v)

    def answers(self) -> list[int]:
        return list(struct.unpack_from("<8I", self.buf, int(C.ANSWERS)))


def test_layout() -> None:
    assert C.CFG + G.size == C.GAUGE
    assert (C.ANSWERS.count, G.UPS.count, G.PAIRS.count) == (8, 8, 8)
    assert (CHARGED, COOLDOWN) == (0, 6)
    assert a.MONSTER_VTABLE.QUERY == 0xDC and a.EM_BLOCK.CHARGE + C.size == a.EM_BLOCK.size


def test_gain_bands_by_hp(lib: ctypes.CDLL) -> None:
    band = lib.chg_host_band
    assert [band(hp, 100) for hp in (100, 95, 94, 75, 74, 40, 39, 1, 0)] == [
        0,
        0,
        1,
        1,
        2,
        2,
        3,
        3,
        3,
    ]
    assert band(5, 0) == 0  # no maximum: full
    assert band(9500, 10000) == 0 and band(9499, 10000) == 1


def test_a_charge_up_raises_the_gauge_by_hp_and_starts_the_cooldown(lib: ctypes.CDLL) -> None:
    b = Block()
    lib.chg_host_start(b.buf, 0x1000, 100)
    assert lib.chg_host_up_start(b.buf, 3, 100, 100) == 1
    assert (b.get("GAUGE"), b.get("COOLDOWN"), b.get("UP_STARTS", "I")) == (20, 420, 1)
    assert lib.chg_host_up_start(b.buf, 3, 80, 100) == 1  # >= 75 %
    assert lib.chg_host_up_start(b.buf, 3, 50, 100) == 1  # >= 40 %
    assert b.get("GAUGE") == 20 + 15 + 10
    assert lib.chg_host_up_start(b.buf, 4, 10, 100) == 1  # another up, low HP
    assert b.get("GAUGE") == 55
    assert lib.chg_host_up_start(b.buf, 4, 100, 100) == 1 and b.get("GAUGE") == 85
    assert lib.chg_host_up_start(b.buf, 4, 100, 100) == 1 and b.get("GAUGE") == FULL  # clamped
    assert lib.chg_host_up_start(b.buf, 5, 100, 100) == 0 and b.get("GAUGE") == FULL  # no up


def test_a_charged_monster_takes_no_gain(lib: ctypes.CDLL) -> None:
    b = Block()
    lib.chg_host_transform_start(b.buf, 9)
    b.put("COOLDOWN", 0)
    assert lib.chg_host_up_start(b.buf, 3, 100, 100) == 0
    assert (b.get("GAUGE"), b.get("COOLDOWN"), b.get("UP_STARTS", "I")) == (FULL, 0, 0)


def test_the_transform_start_charges(lib: ctypes.CDLL) -> None:
    b = Block()
    assert lib.chg_host_transform_start(b.buf, 3) == 0  # an up is not the transform
    assert (b.get("CHARGED", "I"), b.get("TRANSFORMS", "I")) == (0, 0)
    assert lib.chg_host_transform_start(b.buf, 9) == 1
    assert (b.get("CHARGED", "I"), b.get("GAUGE"), b.get("DURABILITY")) == (1, FULL, FULL)
    assert b.get("TRANSFORMS", "I") == 1
    nothing = Block(transform=NO_SLOT)
    assert lib.chg_host_transform_start(nothing.buf, NO_SLOT) == 0  # no slot is not slot 255
    assert nothing.get("CHARGED", "I") == 0


def test_a_hit_chips_the_gauge_then_the_durability(lib: ctypes.CDLL) -> None:
    b = Block(chip_gauge=5, chip_durability=40)
    b.put("GAUGE", 12)
    lib.chg_host_hit(b.buf)
    lib.chg_host_hit(b.buf)
    lib.chg_host_hit(b.buf)
    assert (b.get("GAUGE"), b.get("DURABILITY"), b.get("HITS", "I")) == (0, 0, 3)  # not below 0
    lib.chg_host_transform_start(b.buf, 9)
    lib.chg_host_hit(b.buf)
    assert (b.get("GAUGE"), b.get("DURABILITY")) == (FULL, 60)
    lib.chg_host_hit(b.buf)
    lib.chg_host_hit(b.buf)
    assert b.get("DURABILITY") == 0 and b.get("CHARGED", "I") == 1  # settle discharges, not hit


def test_durability_at_zero_discharges(lib: ctypes.CDLL) -> None:
    b = Block(chip_durability=50, out=900)
    lib.chg_host_transform_start(b.buf, 9)
    lib.chg_host_hit(b.buf)
    assert lib.chg_host_settle(b.buf, 0) == 0
    lib.chg_host_hit(b.buf)
    assert lib.chg_host_settle(b.buf, 0) == 1
    assert (b.get("CHARGED", "I"), b.get("GAUGE"), b.get("DURABILITY")) == (0, 0, 0)
    assert (b.get("COOLDOWN"), b.get("DISCHARGES", "I")) == (900, 1)
    assert lib.chg_host_settle(b.buf, 0) == 0 and b.get("DISCHARGES", "I") == 1  # once


def test_a_discharge_pair_discharges_at_once(lib: ctypes.CDLL) -> None:
    b = Block()
    lib.chg_host_transform_start(b.buf, 9)
    assert lib.chg_host_settle(b.buf, (2 << 8) | 10) == 0
    assert lib.chg_host_settle(b.buf, PAIR_KO) == 1
    assert (b.get("CHARGED", "I"), b.get("COOLDOWN")) == (0, 900)
    assert lib.chg_host_settle(b.buf, PAIR_KO) == 0  # not charged: nothing to discharge
    assert lib.chg_host_pair(b.buf, (11 << 8) | 1) == 1 and lib.chg_host_pair(b.buf, 11 << 9) == 0


def test_the_cooldown_runs_only_while_not_charged(lib: ctypes.CDLL) -> None:
    b = Block()
    b.put("COOLDOWN", 3)
    for want in (2, 1, 0, 0):
        lib.chg_host_tick(b.buf)
        assert b.get("COOLDOWN") == want
    b.put("COOLDOWN", 5)
    lib.chg_host_transform_start(b.buf, 9)
    lib.chg_host_tick(b.buf)
    assert b.get("COOLDOWN") == 5  # frozen while charged
    lib.chg_host_discharge(b.buf)
    assert b.get("COOLDOWN") == 900
    lib.chg_host_tick(b.buf)
    assert b.get("COOLDOWN") == 899


def test_answers_are_charged_and_cooldown_only(lib: ctypes.CDLL) -> None:
    b = Block()
    lib.chg_host_answers(b.buf)
    assert b.answers() == [0] * 8
    b.put("COOLDOWN", 7)
    lib.chg_host_answers(b.buf)
    assert b.answers() == [0, 0, 0, 0, 0, 0, 1, 0]
    b.put("CHARGED", 1, "I")
    b.put("COOLDOWN", 0)
    lib.chg_host_answers(b.buf)
    assert b.answers() == [1, 0, 0, 0, 0, 0, 0, 0]
    b.put("COOLDOWN", 900)  # cooldown running while charged: both
    lib.chg_host_answers(b.buf)
    assert b.answers()[CHARGED] == 1 and b.answers()[COOLDOWN] == 1


def test_the_transform_is_wanted_at_a_full_gauge_uncharged(lib: ctypes.CDLL) -> None:
    b = Block()
    for gauge, charged, want in ((99, 0, 0), (100, 0, 1), (100, 1, 0), (0, 0, 0)):
        b.put("GAUGE", gauge)
        b.put("CHARGED", charged, "I")
        assert lib.chg_host_wants_transform(b.buf) == want
    none = Block(transform=NO_SLOT)
    none.put("GAUGE", FULL)
    assert lib.chg_host_wants_transform(none.buf) == 0


def test_reset_keeps_the_settings(lib: ctypes.CDLL) -> None:
    b = Block()
    lib.chg_host_start(b.buf, 0x1000, 50)
    lib.chg_host_up_start(b.buf, 3, 100, 100)
    lib.chg_host_transform_start(b.buf, 9)
    lib.chg_host_answers(b.buf)
    lib.chg_host_reset(b.buf)
    for f in ("ENTITY", "SEEN", "HP", "CHARGED", "UP_STARTS", "TRANSFORMS", "XFORM_AT"):
        assert b.get(f, "I") == 0, f
    assert (b.get("GAUGE"), b.get("DURABILITY"), b.get("COOLDOWN")) == (0, 0, 0)
    assert b.answers() == [0] * 8
    assert lib.chg_host_up_index(b.buf, 4) == 1 and lib.chg_host_up_index(b.buf, 9) == -1
    assert b.buf[int(C.CFG) : int(C.CFG) + int(G.size or 0)] == b.cfg.raw


def test_start_takes_the_baseline(lib: ctypes.CDLL) -> None:
    b = Block()
    lib.chg_host_start(b.buf, 0x1000, 420)
    assert (b.get("ENTITY", "I"), b.get("HP", "I"), b.get("SEEN", "I")) == (0x1000, 420, 1)


def check(b: Block, lib: ctypes.CDLL) -> bytes | None:
    return lib.chg_host_check(ctypes.byref(b.buf, int(C.CFG)))  # type: ignore[no-any-return]


def test_check_accepts_the_defaults_and_refuses_the_rest(lib: ctypes.CDLL) -> None:
    assert check(Block(), lib) is None
    assert check(Block(transform=NO_SLOT), lib) is None
    slots = int(a.EM_MOVES.MOVES.count or 0)
    assert check(Block(transform=slots), lib) == b"a transform slot past the registry"
    assert check(Block(transform=slots - 1), lib) is None
    assert check(Block(ups=((slots, (1, 1, 1, 1)),)), lib) == b"a charge-up slot past the registry"
    assert check(Block(ups=((3, (1, 1, 1, 101)),)), lib) == b"a gain is 0..100"
    assert check(Block(ups=((3, (1, -1, 1, 1)),)), lib) == b"a gain is 0..100"
    assert check(Block(chip_gauge=-1), lib) == b"a chip is 0..100"
    assert check(Block(chip_durability=101), lib) == b"a chip is 0..100"
    b = Block()
    struct.pack_into("<B", b.buf, int(C.CFG) + int(G.UP_COUNT), 9)
    assert check(b, lib) == b"too many charge-ups"
    b = Block()
    struct.pack_into("<B", b.buf, int(C.CFG) + int(G.PAIR_COUNT), 9)
    assert check(b, lib) == b"too many discharge pairs"
