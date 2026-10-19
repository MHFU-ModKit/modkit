# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The AI script container: its bytes, and the maps across it."""

import hashlib
import struct

import pytest
from mhfu import addresses as a
from mhfu.em import script
from mhfu.em.script import asm, edit, inject, ir, lift
from mhfu.em.script.inject import Container, ContainerError, Kind, container

BASE = a.RAM.start + 0x1BB_0000  # below SCRIPT_HEADERS
N = BASE + 0x1000  # the synthetic native region
ROW = 5
PUSH = int(a.SCRIPT_PUSH)
LOAD = PUSH + inject.IMAGE_OFF
HEADER = 56  # the header's offset in the image


def u32s(*words):
    return struct.pack(f"<{len(words)}I", *words)


# ---- a tiny native region: two routines, a route, two lists, a header, floats

NATIVE = (
    bytes.fromhex("0500010005000200ff00")  # base_0: two enters, an end
    + bytes(6)
    + bytes.fromhex("3900050003003901050004003902ff00")  # base_1: if / else
    + bytes(range(1, 9))  # route
    + u32s(N, N + 0x10)  # base_l
    + u32s(N + 0x20, 0)  # walk_l
    + u32s(N + 0x28, 0, N + 0x30)  # h0: base, -, walk
    + bytes.fromhex("0000803f00000040")  # its floats
)


def program():
    def read(pc):
        return NATIVE[pc - N] if 0 <= pc - N < len(NATIVE) else script.SENTINEL

    body0, end0 = lift.routine(read, N, ROW)
    body1, _ = lift.routine(read, N + 0x10, ROW)
    items = [
        ir.Routine("base_0", body0, NATIVE[end0 - N : 0x10], N),
        ir.Routine("base_1", body1, b"", N + 0x10),
        ir.Data("route", NATIVE[0x20:0x28], {"route": 0}, N + 0x20),
        ir.Table("base_l", ["base_0", "base_1"], N + 0x28),
        ir.Table("walk_l", ["route", None], N + 0x30),
        ir.Header("h0", ["base_l", None, "walk_l"], tuple(range(17)), N + 0x38),
        ir.Blob(NATIVE[0x44:0x4C], N + 0x44),
    ]
    p = ir.Program(ROW, N, items, N + len(NATIVE))
    return p, asm.assemble(p)


def edited():
    """The program with a new enter in front of base_0, which moves its other instructions."""
    p, _ = program()
    edit.put(p, "base_0", [edit.insn("enter", 0, 9, 0)], 0)
    return p, asm.assemble(p)


STICK = bytes.fromhex(
    ""
    "4d48414901000500a321a31300000000400000004c0000009000000005000000"
    "a800000001000000b000000000000000b0000000000000000000000000000000"
    "0500010005000200ff000000000000003900050003003901050004003902ff00"
    "0102030405060708000000001000000020000000000000002800000000000000"
    "300000000000803f0000004000000000280000002c0000003000000038000000"
    "40000000000000003810bb0938000000"
)
FULL = bytes.fromhex(
    ""
    "4d48414901000500a321a3134000700a400000004c0000009000000005000000"
    "a800000001000000b00000000b00000008010000070000000000000000000000"
    "0500010005000200ff000000000000003900050003003901050004003902ff00"
    "01020304050607084000700a5000700a6000700a000000006800700a00000000"
    "7000700a0000803f0000004000000000280000002c0000003000000038000000"
    "40000000000000003810bb09380000000010bb09000000000410bb0904000000"
    "0810bb09080000000a10bb090a0000001010bb09100000001210bb0912000000"
    "1610bb09160000001810bb09180000001c10bb091c0000001e10bb091e000000"
    "2010bb09200000000010bb090000000010000000000000001010bb0910000000"
    "10000000000000002010bb092000000008000000020000002810bb0928000000"
    "08000000010000003010bb093000000008000000010000003810bb0938000000"
    "0c000000030000004410bb09440000000800000004000000"
)


# ---- the container


def fields(raw):
    """The header words after magic, version and row: id, load, then offset and size or count of
    the image, relocs, patches, origin and items."""
    magic, version, row, *words = struct.unpack_from("<4sHH12I", raw)
    return magic, version, row, words


def test_golden_stick():
    p, img = program()
    assert container(p, img, 0, origin=False) == STICK
    magic, version, row, words = fields(STICK)
    assert (magic, version, row, words[1]) == (b"MHAI", 1, ROW, 0)
    assert words[2:] == [0x40, 76, 0x90, 5, 0xA8, 1, 0xB0, 0, 0xB0, 0]
    assert len(STICK) == 0xB0
    assert STICK[0x38:0x40] == bytes(8)


def test_golden_full():
    p, img = program()
    assert container(p, img, LOAD) == FULL
    magic, version, row, words = fields(FULL)
    assert (magic, version, row, words[1]) == (b"MHAI", 1, ROW, LOAD)
    assert words[0] == int.from_bytes(hashlib.sha1(img.at(0)).digest()[:4], "little")
    assert words[2:] == [0x40, 76, 0x90, 5, 0xA8, 1, 0xB0, 11, 0x108, 7]
    assert len(FULL) == 0x108 + 7 * 16


def test_the_id_is_the_images_at_base_0():
    p, img = program()
    ids = {fields(container(p, img, load))[3][0] for load in (0, LOAD, N)}
    assert len(ids) == 1
    other, other_img = edited()
    assert fields(container(other, other_img))[3][0] not in ids


def test_read_back():
    c = Container.read(FULL)
    p, img = program()
    assert (c.row, c.load, c.head.extent, c.image) == (ROW, LOAD, len(FULL), img.at(LOAD))
    assert c.relocs == tuple(img.relocs) == (40, 44, 48, 56, 64)
    assert c.patches == ((N + 0x38, HEADER),) and c.headers == {N + 0x38: LOAD + HEADER}
    assert c.origin == tuple(sorted(img.origin.items())) and len(c.origin) == 11
    assert [(i.native - N, i.offset, i.size, i.kind) for i in c.items] == [
        (0x00, 0, 16, Kind.ROUTINE),
        (0x10, 16, 16, Kind.ROUTINE),
        (0x20, 32, 8, Kind.DATA),
        (0x28, 40, 8, Kind.TABLE),
        (0x30, 48, 8, Kind.TABLE),
        (0x38, 56, 12, Kind.HEADER),
        (0x44, 68, 8, Kind.BLOB),
    ]
    assert c.entry(LOAD + HEADER) == LOAD


def test_read_ignores_what_follows():
    assert Container.read(FULL + bytes(64)).image == Container.read(FULL).image


def test_a_stick_container_has_no_maps_and_loads_anywhere():
    c = Container.read(STICK)
    assert (c.load, c.origin, c.items) == (0, (), ())
    assert c.patches == ((N + 0x38, HEADER),) and c.entry(HEADER) == 0
    p, img = program()
    placed = c.place(LOAD)
    assert placed == container(p, img, LOAD, origin=False)
    assert Container.read(placed).image == img.at(LOAD)
    assert c.place(0) == STICK


def poke(raw, at, value, size=4):
    out = bytearray(raw)
    out[at : at + size] = value.to_bytes(size, "little")
    return bytes(out)


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        (b"XHAI" + FULL[4:], "MHAI"),
        (poke(FULL, 4, 2, 2), "version 2"),
        (FULL[:20], "shorter"),
        (FULL[:-8], "needs 376 bytes and 368"),
        (poke(FULL, 0x42, 0xFF), "its id is not"),
        (poke(FULL, 0x90, 74), "relocation"),
        (poke(FULL, 0xB4, 77), "offset lies outside"),
        (poke(FULL, 0xAC, 77), "offset lies outside"),
        (poke(FULL, 0x108 + 8, 99), "item lies outside"),
        (poke(FULL, 0x108 + 12, 9), "unknown kind"),
    ],
)
def test_read_refuses(raw, why):
    with pytest.raises(ContainerError, match=why):
        Container.read(raw)


def test_rebase_and_native():
    c = Container.read(FULL)
    at = LOAD
    found = {
        N: at,  # an instruction start
        N + 0x04: at + 4,
        N + 0x0A: at + 0x0A,  # the end of base_0's last instruction
        N + 0x16: at + 0x16,  # a marker
        N + 0x24: at + 0x24,  # inside the route
        N + 0x2C: at + 0x2C,  # inside a list
        N + 0x38: at + 0x38,  # a header
    }
    for native, image in found.items():
        assert c.rebase(native) == image
        assert c.native(image) == native
    for native in (N + 0x0C, N + 0x13, N + 0x40, N + 0x44, N - 4, N + 0x4C, N + 0x1000):
        assert c.rebase(native) is None  # a tail, mid-instruction, a header's inside, a blob, out
    for image in (at + 0x0C, at + 0x13, at + 0x44, at - 4, at + 76, at + 0x1000):
        assert c.native(image) is None
    assert all(c.native(c.rebase(n)) == n for n, _ in c.origin)


def test_native_is_the_lowest_of_several():
    image = bytes(16)
    raw = inject._pack(ROW, inject._id(image), 0, image, (), (), [(100, 8), (104, 8)], ())
    assert Container.read(raw).native(8) == 100
