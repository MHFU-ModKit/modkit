# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The village NPCs' pure parts (src/core/npc_logic.h) on the host: the spawn rows and when to
patch them, a row's slot, the hunter's frame and the turn, the clip lookup, the part roots and
the clip state machine."""

import ctypes
import math
import struct
from collections.abc import Callable

import pytest
from mhfu import addresses as a
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Bone, Skeleton

TURN = 0x10000
NONE = 0xFFFF
OWN_KIND = 5
SHIPPED = 16
ROW = int(a.NPC_SPAWN_ROW.size or 0)
STILL, POINT, HUNTER = 0, 1, 2
SKELETON = 0xC0000000
HOME = 0x08812340  # where the block put it; noaddr


class Face(ctypes.Structure):
    _fields_ = [
        ("mode", ctypes.c_uint8),
        ("_pad", ctypes.c_uint8),
        ("rate", ctypes.c_uint16),
        ("x", ctypes.c_float),
        ("z", ctypes.c_float),
    ]


class Play(ctypes.Structure):
    _fields_ = [
        ("seq", ctypes.c_uint32),
        ("entry", ctypes.c_uint16),
        ("then", ctypes.c_uint16),
        ("blend", ctypes.c_uint8),
        ("_pad", ctypes.c_uint8 * 3),
    ]

    def ask(self, entry: int, blend: int = 6, then: int = NONE) -> None:
        self.entry, self.blend, self.then = entry, blend, then
        self.seq += 1


class Anim(ctypes.Structure):
    _fields_ = [
        ("seq", ctypes.c_uint32),
        ("entry", ctypes.c_uint16),
        ("then", ctypes.c_uint16),
        ("blend", ctypes.c_uint8),
        ("armed", ctypes.c_uint8),
        ("_pad", ctypes.c_uint8 * 2),
    ]


@pytest.fixture(scope="module")
def lib(host_lib: Callable[..., ctypes.CDLL]) -> ctypes.CDLL:
    lib = host_lib("tests/npc_host.cpp", "src/core/steer.cpp")
    u8, u16, u32, f = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_float
    p = ctypes.c_char_p
    pf = ctypes.POINTER(f)
    lib.npc_host_rows.argtypes = [p, p, p, pf, ctypes.c_int, u32]
    lib.npc_host_home.argtypes = [p, u32]
    lib.npc_host_chunk.argtypes = [u32, u32, u32, u8]
    lib.npc_host_slot.argtypes = [u32, u32]
    lib.npc_host_point.argtypes = [f, f, u16, f, f, pf, pf]
    lib.npc_host_turn.restype = u16
    lib.npc_host_turn.argtypes = [u16, ctypes.POINTER(Face), f, f, f, f, u16]
    for name in ("npc_host_clip", "npc_host_first_clip"):
        getattr(lib, name).restype = u32
        getattr(lib, name).argtypes = [p, u32, u32]
    lib.npc_host_roots.argtypes = [p, u32, ctypes.POINTER(u16)]
    lib.npc_host_sub.restype = u32
    lib.npc_host_sub.argtypes = [p, u32, u32, ctypes.POINTER(u32)]
    for name in ("npc_host_step", "npc_host_spawn"):
        getattr(lib, name).restype = u16
    lib.npc_host_step.argtypes = [ctypes.POINTER(Anim), ctypes.POINTER(Play), ctypes.c_int]
    lib.npc_host_spawn.argtypes = [ctypes.POINTER(Anim), ctypes.POINTER(Play)]
    lib.npc_host_alive.argtypes = [u32, u8, u16, ctypes.c_int, u32, u32]
    return lib


def test_rows(lib: ctypes.CDLL) -> None:
    shipped = bytes(i % 251 for i in range(SHIPPED * ROW))
    out = ctypes.create_string_buffer((SHIPPED + 2) * ROW)
    sizes = (ctypes.c_float * 2)(0.5, 2.0)
    assert lib.npc_host_rows(out, shipped, bytes([0x1E, 0x21]), sizes, 2, HOME) == SHIPPED + 2
    assert out.raw[: SHIPPED * ROW] == shipped
    r = a.NPC_SPAWN_ROW
    for k, (char, size) in enumerate(((0x1E, 0.5), (0x21, 2.0))):
        row = out.raw[(SHIPPED + k) * ROW : (SHIPPED + k + 1) * ROW]
        assert row[r.CHAR] == char and row[r.BEHAVIOUR] == 0xFF
        assert row[r.KIND] == OWN_KIND and row[r.FLAGS] == 0
        assert struct.unpack_from("<H", row, r.YAW)[0] == 0
        assert struct.unpack_from("<ff", row, r.SCALE) == (size, 1.0)
        assert struct.unpack_from("<II", row, r.HOME) == (HOME, 0)  # SCRIPT 0: placed by C


def test_home(lib: ctypes.CDLL) -> None:
    out = ctypes.create_string_buffer(0x10)
    lib.npc_host_home(out, HOME)
    count, list_at = struct.unpack_from("<hxxI", out.raw)
    assert (count, list_at) == (1, HOME + 8)
    assert struct.unpack_from("<hhI", out.raw, list_at - HOME) == (0, 0, 0)  # group 0, no swaps


def test_rows_in_place(lib: ctypes.CDLL) -> None:
    out = ctypes.create_string_buffer(bytes(i % 251 for i in range(SHIPPED * ROW)) + bytes(ROW))
    lib.npc_host_rows(out, out, bytes([0x1E]), (ctypes.c_float * 1)(1.0), 1, HOME)
    assert out.raw[: SHIPPED * ROW] == bytes(i % 251 for i in range(SHIPPED * ROW))


def test_patch_decision(lib: ctypes.CDLL) -> None:
    rows, counts = int(a.NPC_SPAWN_ROWS_VILLAGE), int(a.NPC_SPAWN_COUNTS)
    live = (0x09AFF200, 0x10D80)  # the lobby chunk seen live; noaddr
    assert lib.npc_host_chunk(*live, rows, SHIPPED)
    assert not lib.npc_host_chunk(*live, 0x0BF00000, SHIPPED + 1)  # patched already; noaddr
    assert not lib.npc_host_chunk(*live, 0x1234, 0x56)  # another overlay's bytes
    assert not lib.npc_host_chunk(*live, rows, SHIPPED + 1)
    assert not lib.npc_host_chunk(rows - 0x800, 0x800, rows, SHIPPED)  # ends before the rows
    assert not lib.npc_host_chunk(counts + 6, 0x800, rows, SHIPPED)  # starts past the counts
    assert lib.npc_host_chunk(counts, 0x800, rows, SHIPPED)  # a split table's second chunk


def test_slot_of(lib: ctypes.CDLL) -> None:
    assert [lib.npc_host_slot(i, 2) for i in (0, 15, 16, 17, 18)] == [-1, -1, 0, 1, -1]
    assert lib.npc_host_slot(16, 0) == -1


def point(lib: ctypes.CDLL, yaw: int, right: float, ahead: float) -> tuple[float, float]:
    x, z = ctypes.c_float(), ctypes.c_float()
    lib.npc_host_point(1000.0, -500.0, yaw, right, ahead, ctypes.byref(x), ctypes.byref(z))
    return x.value - 1000.0, z.value + 500.0


@pytest.mark.parametrize(
    ("yaw", "right", "ahead", "want"),
    [
        (0, 0, 100, (0, 100)),  # YAW 0 faces +z
        (0, 300, 0, (-300, 0)),  # right of +z is -x, as the stick maps
        (0x4000, 0, 100, (100, 0)),
        (0x4000, 300, 0, (0, 300)),
        (0x8000, 300, 100, (300, -100)),
    ],
)
def test_hunter_frame(
    lib: ctypes.CDLL, yaw: int, right: float, ahead: float, want: tuple[float, float]
) -> None:
    assert point(lib, yaw, right, ahead) == pytest.approx(want, abs=0.05)


def turn(
    lib: ctypes.CDLL,
    yaw: int,
    mode: int,
    x: float = 0,
    z: float = 0,
    rate: int = 0x200,
    at: tuple[float, float] = (0, 0),
    hunter: tuple[float, float, int] = (0, 1000, 0),
) -> int:
    f = Face(mode=mode, rate=rate, x=x, z=z)
    return int(lib.npc_host_turn(yaw, ctypes.byref(f), *at, *hunter))


def test_turn_point(lib: ctypes.CDLL) -> None:
    assert turn(lib, 0, POINT, 100, 0) == 0x200  # toward +x, capped
    assert turn(lib, 0, POINT, -100, 0) == TURN - 0x200
    assert turn(lib, 0x3F00, POINT, 100, 0) == 0x4000  # within the rate: lands
    assert turn(lib, 0x1234, STILL, 100, 0) == 0x1234
    assert turn(lib, 0x1234, POINT, 0.2, 0.2) == 0x1234  # on the spot


def test_turn_hunter(lib: ctypes.CDLL) -> None:
    assert turn(lib, 0x2000, HUNTER, rate=0x4000) == 0  # the hunter dead ahead on +z
    # 1000 to the right of a hunter at (0, 1000) facing +x is (0, 2000)
    assert turn(lib, 0x1000, HUNTER, 1000, 0, rate=0x4000, hunter=(0, 1000, 0x4000)) == 0
    # and of one facing +z, (-1000, 1000): -45 degrees
    want = round(-math.pi / 4 / math.tau * TURN) % TURN
    assert turn(lib, 0, HUNTER, 1000, 0, rate=0x4000) == pytest.approx(want, abs=1)


def pack(streams: list[list[int | None]]) -> tuple[bytes, list[int]]:
    """An animation pack whose clips are 16 bytes each; the offsets of the clips in slot order."""
    n = len(streams)
    head = 8 * (n + 1)
    tables, at = [], head
    for s in streams:
        tables.append(at)
        at += 4 * len(s)
    out = bytearray()
    for s, t in zip(streams, tables, strict=True):
        out += struct.pack("<II", len(s), t)
    out += struct.pack("<II", 0, at)
    offs: list[int] = []
    for s in streams:
        for c in s:
            if c is None:
                out += struct.pack("<I", 0xFFFFFFFF)
            else:
                out += struct.pack("<I", at + 16 * c)
                offs.append(at + 16 * c)
    return bytes(out) + bytes(16 * 8), offs


def test_clip_lookup(lib: ctypes.CDLL) -> None:
    data, _ = pack([[0, None, 1], [], [None, 2]])
    head = 8 * 4 + 4 * 5
    assert lib.npc_host_clip(data, 0, 0) == head
    assert lib.npc_host_clip(data, 0, 1) == 0  # an empty slot
    assert lib.npc_host_clip(data, 0, 2) == head + 16
    assert lib.npc_host_clip(data, 0, 3) == 0  # past the stream
    assert lib.npc_host_clip(data, 1, 0) == 0
    assert lib.npc_host_clip(data, 2, 1) == head + 32
    assert lib.npc_host_clip(data, 3, 0) == 0  # past the pack


def test_first_clip(lib: ctypes.CDLL) -> None:
    data, _ = pack([[0, None, 1], [], [None, 2]])
    head = 8 * 4 + 4 * 5
    assert lib.npc_host_first_clip(data, 0, 1) == head
    assert lib.npc_host_first_clip(data, 2, 0) == head + 32
    assert lib.npc_host_first_clip(data, 0, 2) == head + 16
    assert lib.npc_host_first_clip(data, 1, 0) == 0


def skeleton(parts: list[int]) -> bytes:
    bones = [Bone(parent=i - 1, stream=s) for i, s in enumerate(parts)]
    return Skeleton(bones, [0, len(parts)]).to_bytes()


def roots(lib: ctypes.CDLL, data: bytes) -> tuple[int, list[int]]:
    out = (ctypes.c_uint16 * 3)()
    rc = lib.npc_host_roots(data, len(data), out)
    return rc, list(out)


def test_part_roots(lib: ctypes.CDLL) -> None:
    # the ported Zinogre's partition: body, head 33, tail 39, the severed tail 46
    zinogre = [0] * 33 + [1] * 6 + [2] * 7 + [3] * 5
    assert roots(lib, skeleton(zinogre)) == (0, [0, 33, 39])
    assert roots(lib, skeleton([0, 0, 2])) == (0, [0, 0, 2])  # no head part
    assert roots(lib, skeleton(zinogre)[:0x200])[0] == -1


def test_pac_sub(lib: ctypes.CDLL) -> None:
    skel = skeleton([0, 1, 2])
    data = Pac([b"pmo\0" + bytes(60), skel, b"\0" * 16]).to_bytes()
    size = ctypes.c_uint32()
    off = lib.npc_host_sub(data, len(data), SKELETON, ctypes.byref(size))
    assert data[off : off + size.value] == skel
    assert lib.npc_host_sub(data, len(data), 0x12345678, ctypes.byref(size)) == 0
    assert lib.npc_host_sub(data[:8], 8, SKELETON, ctypes.byref(size)) == 0


def step(lib: ctypes.CDLL, anim: Anim, play: Play, playing: bool) -> int:
    return int(lib.npc_host_step(ctypes.byref(anim), ctypes.byref(play), int(playing)))


def test_then_after_the_clip(lib: ctypes.CDLL) -> None:
    anim, play = Anim(then=NONE), Play(then=NONE)
    assert step(lib, anim, play, False) == NONE
    play.ask(5, 6, then=1)
    assert step(lib, anim, play, False) == 5 and anim.blend == 6
    assert [step(lib, anim, play, True) for _ in range(3)] == [NONE] * 3
    assert step(lib, anim, play, False) == 1  # stop_walk_forward ended: idle
    assert anim.entry == 1 and anim.then == NONE
    assert [step(lib, anim, play, p) for p in (False, True, False)] == [NONE] * 3


def test_then_waits_for_the_clip(lib: ctypes.CDLL) -> None:
    anim, play = Anim(then=NONE), Play(then=NONE)
    play.ask(5, then=1)
    step(lib, anim, play, False)
    assert step(lib, anim, play, False) == NONE  # not seen playing yet
    assert step(lib, anim, play, True) == NONE
    assert step(lib, anim, play, False) == 1


def test_fields_before_seq(lib: ctypes.CDLL) -> None:
    anim, play = Anim(then=NONE), Play(then=NONE)
    play.entry, play.blend, play.then = 5, 6, 1  # the writer stopped before SEQ
    assert step(lib, anim, play, True) == NONE and anim.entry == 0
    play.seq += 1
    assert step(lib, anim, play, True) == 5 and (anim.blend, anim.then) == (6, 1)


def test_a_loop_never_ends(lib: ctypes.CDLL) -> None:
    anim, play = Anim(then=NONE), Play(then=NONE)
    play.ask(4, then=1)
    step(lib, anim, play, False)
    assert {step(lib, anim, play, True) for _ in range(100)} == {NONE}


def test_a_request_replaces_then(lib: ctypes.CDLL) -> None:
    anim, play = Anim(then=NONE), Play(then=NONE)
    play.ask(5, then=1)
    step(lib, anim, play, False)
    step(lib, anim, play, True)
    play.ask(65, 3)
    assert step(lib, anim, play, True) == 65 and anim.blend == 3
    assert step(lib, anim, play, False) == NONE


def test_spawn(lib: ctypes.CDLL) -> None:
    anim, play = Anim(then=NONE), Play(then=NONE)
    assert lib.npc_host_spawn(ctypes.byref(anim), ctypes.byref(play)) == 0
    play.ask(1)
    assert lib.npc_host_spawn(ctypes.byref(anim), ctypes.byref(play)) == 1  # asked before it
    play.ask(5, then=1)
    step(lib, anim, play, False)
    step(lib, anim, play, True)  # left the village mid-clip
    assert lib.npc_host_spawn(ctypes.byref(anim), ctypes.byref(play)) == 5
    assert step(lib, anim, play, False) == NONE  # the fresh clip is not seen yet
    assert step(lib, anim, play, True) == NONE
    assert step(lib, anim, play, False) == 1


def test_alive(lib: ctypes.CDLL) -> None:
    vt = int(a.NPC_VTABLE)
    assert lib.npc_host_alive(vt, OWN_KIND, 17, 1, 33_000, 250_000)
    assert not lib.npc_host_alive(int(a.OBJ_BASE_VTABLE), OWN_KIND, 17, 1, 0, 250_000)
    assert not lib.npc_host_alive(vt, 2, 17, 1, 0, 250_000)
    assert not lib.npc_host_alive(vt, OWN_KIND, 16, 1, 0, 250_000)
    assert not lib.npc_host_alive(vt, OWN_KIND, 17, 1, 300_000, 250_000)
