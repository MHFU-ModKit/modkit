# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The monster events (monster_events.cpp) on the host: the step's frame call against game memory
(move_host.cpp), and the poll's raising."""

import ctypes
import struct
from collections.abc import Callable

import pytest
from mhfu import addresses as a
from mhfu.live import monster_events

BASE = 0x09000000  # move_host.cpp's memory; noaddr
ENT = BASE + 0x1000
NOTICED, ENTERED, LEFT, FLINCH, BROKEN, TAIL = range(1, 7)
ENRAGED, CALMED = a.MONSTER_EVENT_KIND.number("enraged"), a.MONSTER_EVENT_KIND.number("calmed")


class Event(ctypes.Structure):
    _fields_ = [
        ("entity", ctypes.c_uint32),
        ("frame", ctypes.c_uint32),
        ("usec", ctypes.c_uint32),
        ("kind", ctypes.c_uint8),
        ("part", ctypes.c_uint8),
        ("main", ctypes.c_uint8),
        ("sub", ctypes.c_uint8),
        ("data", ctypes.c_uint16),
        ("_pad", ctypes.c_uint16),
    ]


class Ctx(ctypes.Structure):
    _fields_ = [("event_id", ctypes.c_int), ("ev", Event), ("delay", ctypes.c_uint32)]


@pytest.fixture(scope="module")
def lib(host_lib: Callable[..., ctypes.CDLL]) -> ctypes.CDLL:
    lib = host_lib(
        "src/core/monster_events.cpp",
        "src/core/move.cpp",
        "src/core/steer.cpp",
        "tests/move_host.cpp",
    )
    lib.host_mem.restype = ctypes.c_void_p
    lib.host_set.argtypes = [ctypes.c_int, ctypes.c_float]
    lib.mhfu_monster_events_frame.argtypes = [
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    lib.host_break_row.argtypes = [
        ctypes.c_int,
        ctypes.c_uint8,
        ctypes.c_uint8,
        ctypes.c_uint8,
        ctypes.c_uint16,
    ]
    lib.mhfu_monster_events_frame.restype = ctypes.c_uint16
    lib.mhfu_monster_events.restype = ctypes.c_void_p
    return lib


class Game:
    def __init__(self, lib: ctypes.CDLL) -> None:
        self.lib = lib
        self.mem = (ctypes.c_uint8 * 0x4000).from_address(lib.host_mem())

    def poke(self, off: int, fmt: str, *values: int) -> None:
        struct.pack_into("<" + fmt, self.mem, ENT - BASE + off, *values)

    def frame(self, stale: int = 0) -> tuple[int, int]:
        """(edges, parts) of one frame; self.broke the parts that broke in it."""
        parts, broke = ctypes.c_uint8(), ctypes.c_uint8()
        edges = self.lib.mhfu_monster_events_frame(
            ENT, stale, ctypes.byref(parts), ctypes.byref(broke)
        )
        self.broke = broke.value
        return int(edges), parts.value

    def flinch_part(self, part: int, count: int) -> None:
        """A flinch of `part` that took its flinch count to `count`."""
        self.poke(
            a.ENTITY.FLINCH_PARTS + part * a.FLINCH_PART.size + a.FLINCH_PART.COUNT, "B", count
        )
        self.poke(a.ENTITY.FLINCH_MASK, "B", 1 << part)

    def raised(self) -> list[tuple[int, int, int, int]]:
        """(kind, part, data, frame) of each event the poll raises."""
        self.lib.mhfu_monster_events_drain()
        out = (Ctx * 64)()
        n = self.lib.host_raised(out)
        return [(c.ev.kind, c.ev.part, c.ev.data, c.ev.frame) for c in out[:n]]


@pytest.fixture
def g(lib: ctypes.CDLL) -> Game:
    lib.mhfu_move_init()
    lib.host_set(1, ctypes.c_float(228.0))
    game = Game(lib)
    ctypes.memset(ctypes.addressof(game.mem), 0, 0x4000)
    game.poke(a.ENTITY.FLAGS, "I", 0x8)
    game.frame()  # the baseline
    return game


def test_first_sight_raises_nothing(g: Game) -> None:
    g.poke(a.ENTITY.AWARE, "B", 1)
    g.poke(a.ENTITY.FLAGS, "I", 0x8 | 0x20)
    g.poke(a.ENTITY.BROKEN, "H", 4)
    g.lib.mhfu_monster_events_host_quest()
    g.frame()
    assert g.raised() == []


def test_notice_once(g: Game) -> None:
    g.poke(a.ENTITY.AWARE, "B", 2)  # the Felyne's bit
    g.frame()
    g.poke(a.ENTITY.AWARE, "B", 3)
    g.frame()
    g.frame()
    assert g.raised() == [(NOTICED, 0xFF, 3, 3)]


def test_combat_follows_the_eye_rule(g: Game) -> None:
    g.poke(a.ENTITY.AWARE, "B", 1)
    g.poke(a.ENTITY.COMBAT_MODE, "B", 1)
    g.frame()
    g.poke(a.ENTITY.SECTION, "H", 99)  # the player is elsewhere
    g.frame()
    g.poke(a.ENTITY.SECTION, "H", 0)
    g.frame()
    g.poke(a.ENTITY.MAIN_STATE, "B", 5)  # dead
    g.frame()
    kinds = [r[0] for r in g.raised()]
    assert kinds == [NOTICED, ENTERED, LEFT, ENTERED, LEFT]


def test_flinch_with_part_and_pair(g: Game) -> None:
    g.poke(a.ENTITY.FLINCH_MASK, "B", 0b1010)
    g.poke(a.ENTITY.MAIN_STATE, "BB", 4, 1)
    g.frame()
    g.frame(stale=1)  # the host step did not run: the mask is left over
    out = g.raised()
    assert out == [(FLINCH, 1, 0b1010, 2)]


def test_break_and_tail_once(g: Game) -> None:
    g.poke(a.ENTITY.FLINCH_MASK, "B", 1)
    g.poke(a.ENTITY.BROKEN, "H", 4)
    g.poke(a.ENTITY.SEVERED, "B", 1)
    g.poke(a.ENTITY.SEVER_COUNT, "B", 1)
    g.frame()
    g.poke(a.ENTITY.FLINCH_MASK, "B", 0)
    g.frame()
    assert g.raised() == [(FLINCH, 0, 1, 2), (BROKEN, 0, 4, 2), (TAIL, 0xFF, 1, 2)]


def test_enraged_and_calmed_once(g: Game) -> None:
    g.poke(a.ENTITY.FLAGS, "I", 0x8 | 0x20)
    g.frame()
    g.frame()
    g.poke(a.ENTITY.FLAGS, "I", 0x8)
    g.frame()
    assert g.raised() == [(ENRAGED, 0xFF, 1, 2), (CALMED, 0xFF, 0, 4)]


def test_parts_sharing_a_break_id_each_break(g: Game) -> None:
    """em75's forelegs break at their first flinch with one BREAK_ID, so ENTITY.BROKEN gains a bit
    for the first only; the head breaks at its second, with another."""
    g.lib.host_break_row(0, 0x4B, 4, 1, 1)
    g.lib.host_break_row(1, 0x4B, 6, 1, 1)
    g.lib.host_break_row(2, 0x4B, 0, 2, 2)
    g.poke(a.ENTITY.SPECIES, "B", 0x4B)
    g.flinch_part(4, 1)
    g.poke(a.ENTITY.BROKEN, "H", 0b10)
    g.frame()
    assert g.broke == 1 << 4
    g.flinch_part(6, 1)  # BROKEN has the bit already
    g.frame()
    assert g.broke == 1 << 6
    g.flinch_part(0, 1)  # the head's first flinch breaks nothing
    g.frame()
    assert g.broke == 0
    g.flinch_part(0, 2)
    g.poke(a.ENTITY.BROKEN, "H", 0b110)
    g.frame()
    assert g.broke == 1 << 0
    kinds = [(r[0], r[1], r[2]) for r in g.raised() if r[0] == BROKEN]
    assert kinds == [(BROKEN, 4, 0b10), (BROKEN, 6, 0b10), (BROKEN, 0, 0b100)]


def test_a_part_breaks_once(g: Game) -> None:
    g.lib.host_break_row(0, 0x4B, 6, 1, 1)
    g.poke(a.ENTITY.SPECIES, "B", 0x4B)
    g.flinch_part(6, 1)
    g.frame()
    for count in (2, 3):  # later flinches pass the row's count
        g.flinch_part(6, count)
        g.frame()
    assert [r[0] for r in g.raised()].count(BROKEN) == 1


def test_another_species_row_or_a_stale_frame_breaks_nothing(g: Game) -> None:
    g.lib.host_break_row(0, 0x4C, 6, 1, 1)
    g.poke(a.ENTITY.SPECIES, "B", 0x4B)
    g.flinch_part(6, 1)
    g.frame()
    g.lib.host_break_row(1, 0x4B, 6, 1, 1)
    g.frame(stale=1)  # the host step did not run: the mask is left over
    assert BROKEN not in [r[0] for r in g.raised()]


def test_a_break_the_table_does_not_explain_is_still_raised(g: Game) -> None:
    g.poke(a.ENTITY.FLINCH_MASK, "B", 0b100)
    g.poke(a.ENTITY.BROKEN, "H", 0b100)
    g.frame()
    assert g.broke == 0b100 and g.raised()[-1][:3] == (BROKEN, 2, 0b100)


def test_a_full_ring_drops(g: Game) -> None:
    for k in range(40):
        g.poke(a.ENTITY.FLINCH_MASK, "B", 1 + k % 2)
        g.frame()
    block = int(g.lib.mhfu_monster_events())
    (dropped,) = struct.unpack_from("<I", ctypes.string_at(block + a.MONSTER_EVENTS.DROPPED, 4))
    assert len(g.raised()) == a.MONSTER_EVENTS.RING.count and dropped == 8


def test_frame_returns_edges_and_parts(g: Game) -> None:
    g.poke(a.ENTITY.FLINCH_MASK, "B", 0b100)
    g.poke(a.ENTITY.BROKEN, "H", 2)
    assert g.frame() == ((1 << FLINCH) | (1 << BROKEN), 0b100)
    g.poke(a.ENTITY.FLINCH_MASK, "B", 0)
    assert g.frame() == (0, 0)


def test_the_names_are_the_hosts(lib: ctypes.CDLL) -> None:
    """Lua takes a manifest's `on` by these names: mhfu's, kind for kind, then a terminator."""
    n = len(monster_events.KINDS)
    names = (ctypes.c_char_p * (n + 1)).in_dll(lib, "mhfu_monster_event_names")
    assert tuple(names[i].decode() for i in range(n)) == monster_events.KINDS
    assert names[n] is None
