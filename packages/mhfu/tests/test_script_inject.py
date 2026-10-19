# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The AI script container, and pushing an assembled script into a (fake) running game."""

import asyncio
import hashlib
import struct
import threading
import time
from dataclasses import replace

import pytest
from mhfu import addresses as a
from mhfu.em import script
from mhfu.em.script import asm, edit, inject, ir, lift
from mhfu.em.script.inject import Container, ContainerError, Kind, container
from mhfu.live import Session
from mhfu.live import script as live_script
from mhfu.live.script import ScriptState
from mhfu.memory import Image, Space
from mhfu.views import layout
from mhfu.writes import Refused, apply
from ppsspp_debug.testing import FakePPSSPP

E = a.ENTITY
BASE = a.RAM.start + 0x1BB_0000  # the fake's first byte, below SCRIPT_HEADERS
N = BASE + 0x1000  # the synthetic native region
ENT = BASE + 0x40000
ROW = 5
PUSH = int(a.SCRIPT_PUSH)
LOAD = PUSH + inject.IMAGE_OFF
HEADER = 56  # the header's offset in the image
SIZE = int(a.CLI_BRIDGE_BLOCK) - BASE


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


def test_names_cover_the_entity_words():
    assert set(inject._words(state(), 0)) == set(inject._NAMES)
    assert tuple(inject._INT) == live_script.INTERRUPTS
    first = int(E.SCRIPT_HEADER)
    assert sorted(inject._NAMES) == list(range(first, first + 4 * len(inject._NAMES), 4))


# ---- a game to push into


def ent(k):
    return ENT + 0x1000 * k


def state(k=0, **over):
    s = ScriptState(
        ent(k),
        ROW,
        N + 0x38,
        (N + 4, N + 8, N + 0x16, N + 0x1C),
        0x20,
        0x02,
        1,
        3,
        {0x08: N + 0x10, 0x02: 0, 0x04: 0},
        N + 0x16,
        0,
        N + 0x24,
        0,
        N + 0x12,
        1,
    )
    return replace(s, **over)


def entity_image(s, spare=0):
    raw = bytearray(0x800)

    def put(field, *values):
        layout(field.type).pack_into(raw, field, *values)

    put(E.SPECIES, s.species)
    put(E.SCRIPT_WAKE, s.wake)
    put(E.SCRIPT_PENDING, s.pending)
    put(E.SCRIPT_CONTEXT, s.context)
    put(E.SCRIPT_HEADER, s.header)
    put(E.SCRIPT_PCS, *s.pcs)
    put(E.SCRIPT_INT_08, s.interrupts[0x08])
    put(E.SCRIPT_INT_02, s.interrupts[0x02])
    put(E.SCRIPT_INT_04, s.interrupts[0x04])
    put(E.SCRIPT_CALL_RETURN, s.call_return)
    put(E.SCRIPT_WALK_RETURN, s.walk_return)
    put(E.SCRIPT_ROUTE, s.route)
    put(E.SCRIPT_DEEP_RETURN, s.deep_return)
    put(E.SCRIPT_LOOP_RETURN, s.loop_return)
    put(E.SCRIPT_BASE, s.base)
    put(E.SCRIPT_WAIT, s.wait)
    struct.pack_into("<I", raw, inject._SPARE, spare)
    return bytes(raw)


def row_at():
    return inject._row_at(ROW)


def parts(states=(), *, words=None, spare=None):
    """Address and bytes of the synthetic game: the native region, the row, the registry, the
    entities."""
    spare = spare or {}
    slots = [s.entity for s in states]
    registry = u32s(0, *slots, *[0] * (20 - len(slots)))
    return [
        (N, NATIVE),
        (row_at(), u32s(*(words or [N + 0x38] * 17))),
        (int(a.ENTITY_REGISTRY), registry),
        *((s.entity, entity_image(s, spare.get(s.entity, 0))) for s in states),
    ]


def world(states=(), **kw):
    images = [Image(data, at) for at, data in parts(states, **kw)]
    return Space([*images, Image(bytes(inject.PUSH_SIZE), PUSH)])


def make_plan(mem, p, img):
    snap = inject._snapshot(mem, ROW)
    return inject.plan(p, img, snap.live, snap.row_words, snap.home, spare=snap.spare)


def row_words_at(mem, at):
    return list(struct.unpack("<17I", mem.read(at, 68)))


def row_words(mem):
    return row_words_at(mem, row_at())


def pushed_world(*states, **kw):
    mem = world(list(states), **kw)
    apply(mem, make_plan(mem, *program()))
    return mem


def at(addr):
    """Where the unedited program has the native address in the pushed image."""
    return LOAD + addr - N


# ---- the plan


def test_plan_fresh_push():
    mem = world([state(0), state(1, context=0x00)])
    p, img = program()
    plan = make_plan(mem, p, img)
    assert [w.what for w in plan.writes[:2]] == ["container", f"row {ROW} headers"]
    assert plan.writes[0].at == PUSH and plan.writes[0].data == FULL
    assert plan.notes == tuple(f"entity 0x{ent(k):08X} rebased" for k in (0, 1))
    pushed = apply(mem, plan)
    assert pushed.mismatched == []
    assert mem.read(PUSH, len(FULL)) == FULL
    assert row_words(mem) == [LOAD + HEADER] * 17
    assert live_script.read(mem, ent(0)) == state(
        0,
        header=LOAD + HEADER,
        pcs=(LOAD + 4, LOAD + 8, LOAD + 0x16, LOAD + 0x1C),
        interrupts={0x08: LOAD + 0x10, 0x02: 0, 0x04: 0},
        call_return=LOAD + 0x16,
        route=LOAD + 0x24,
        loop_return=LOAD + 0x12,
    )


def test_plan_writes_what_changes_in_runs():
    mem = world([state(0)])
    p, img = program()
    plan = make_plan(mem, p, img)
    first, *rest = plan.writes[2:]
    assert first.at == ent(0) + int(E.SCRIPT_HEADER)
    labels = [label for w in (first, *rest) for _, label in w.pieces]
    assert labels[0] == f"SCRIPT_HEADER 0x{N + 0x38:08X} -> 0x{LOAD + HEADER:08X}"
    assert [len(w.pieces) for w in (first, *rest)] == [6, 1, 1, 1]  # runs of the words that moved
    assert len(labels) == 9  # the header, four PCs, an interrupt start, two returns, the route
    assert len(first.guards) == 2  # every word and the base, then the context byte
    assert all(not w.guards for w in rest)


def test_plan_restarts_what_cannot_map():
    s = state(0, pcs=(N + 4, N + 8, N + 0x0C, N + 0x1C), pending=0x12, base=2)
    mem = world([s])
    p, img = program()
    plan = make_plan(mem, p, img)
    assert plan.notes == (
        f"entity 0x{ent(0):08X} restarted: SCRIPT_PCS[2] 0x{N + 0x0C:08X} has no place in the "
        "new script",
    )
    apply(mem, plan)
    got = live_script.read(mem, ent(0))
    assert got.header == LOAD + HEADER and got.pcs == (LOAD,) * 4
    assert (got.context, got.base, got.pending) == (0, 0, 0x12)
    assert got.call_return == LOAD + 0x16 and got.interrupts[0x08] == LOAD + 0x10


def test_plan_counts_the_stuck_words():
    s = state(0, pcs=(N + 4, N + 0x0C, N + 0x0D, N + 0x1C))
    plan = make_plan(world([s]), *program())
    assert "SCRIPT_PCS[1] 0x" in plan.notes[0] and "and 1 more" in plan.notes[0]


def test_plan_leaves_other_headers_alone():
    other = state(1, header=N + 0x44)
    mem = world([state(0), other])
    plan = make_plan(mem, *program())
    assert plan.notes == (f"entity 0x{ent(0):08X} rebased",)
    apply(mem, plan)
    assert live_script.read(mem, ent(1)) == other


def test_plan_without_a_monster_notes_it():
    mem = world([state(0, header=N + 0x44)])
    plan = make_plan(mem, *program())
    assert plan.notes == (f"no live monster runs row {ROW}'s script",)
    assert len(plan.writes) == 2


def test_plan_moves_the_header_words_it_knows():
    words = [N + 0x38] * 16 + [0x1234]
    mem = world(words=words)
    apply(mem, make_plan(mem, *program()))
    assert row_words(mem) == [LOAD + HEADER] * 16 + [0x1234]


def test_plan_reads_the_spare_word():
    mem = world([state(0), state(1)], spare={ent(0): N + 0x1C, ent(1): N + 0x0C})
    plan = make_plan(mem, *program())
    assert plan.notes[0].endswith("rebased") and "restarted: +0x4FC 0x" in plan.notes[1]
    apply(mem, plan)
    assert mem.u32(ent(0) + inject._SPARE) == LOAD + 0x1C


def test_plan_repushes_over_the_old_container():
    mem = pushed_world(state(0), state(1), state(2))
    mem.write_u32(ent(1) + int(E.SCRIPT_PCS), LOAD + 0x0C)  # a place only the old image has
    p, img = edited()
    plan = make_plan(mem, p, img)
    assert plan.writes[0].guards == ((PUSH, FULL[: inject.GUARD]),)
    assert [n.rpartition(" ")[2] for n in plan.notes] == ["rebased", "script", "rebased"]
    assert "restarted: SCRIPT_PCS[0] 0x" in plan.notes[1]
    apply(mem, plan)
    assert Container.read(mem.read(PUSH, len(plan.writes[0].data))).image == img.at(LOAD)
    got = live_script.read(mem, ent(0))
    assert got.header == LOAD + HEADER
    assert got.pcs == (LOAD + 8, LOAD + 12, LOAD + 0x16, LOAD + 0x1C)  # N + 4, N + 8 moved on
    assert live_script.read(mem, ent(1)).pcs == (LOAD,) * 4  # restarted at the new base_0
    assert live_script.read(mem, ent(2)).pcs == got.pcs
    assert row_words(mem) == [LOAD + HEADER] * 17


def test_plan_repush_of_the_same_script_changes_no_entity():
    mem = world([state(0)])
    apply(mem, make_plan(mem, *program()))
    before = live_script.read(mem, ent(0))
    plan = make_plan(mem, *program())
    assert plan.notes == (f"entity 0x{ent(0):08X} unchanged",) and len(plan.writes) == 2
    apply(mem, plan)
    assert live_script.read(mem, ent(0)) == before


def test_plan_takes_a_stick_container():
    mem = world([state(0)])
    p, img = program()
    snap = inject._snapshot(mem, ROW)
    plan = inject.plan(p, Container.read(STICK), snap.live, snap.row_words, snap.home)
    assert plan.writes[0].data == container(p, img, LOAD, origin=False)
    assert plan.notes[0].startswith(f"entity 0x{ent(0):08X} restarted: SCRIPT_PCS[0]")
    assert "and 7 more" in plan.notes[0]
    apply(mem, plan)  # no maps: every monster restarts, at the base routine
    got = live_script.read(mem, ent(0))
    assert got.header == LOAD + HEADER and got.pcs == (LOAD,) * 4
    assert row_words(mem) == [LOAD + HEADER] * 17


# ---- the guards and refusals


def test_guards_cover_the_row_the_entities_and_the_home():
    p, img = program()

    def dump(mem):
        return mem.read(PUSH, len(FULL)), mem.read(row_at(), 68), mem.read(ent(0), 0x800)

    def stale(change):
        mem = world([state(0)])
        plan = make_plan(mem, p, img)
        change(mem)
        held = dump(mem)
        with pytest.raises(Refused, match="does not hold what this plan was built against"):
            apply(mem, plan)
        assert dump(mem) == held  # nothing was written

    stale(lambda mem: mem.write_u32(row_at() + 8, 0x1234))
    stale(lambda mem: mem.write_u32(ent(0) + int(E.SCRIPT_PCS), 0x1234))
    stale(lambda mem: mem.write_u32(ent(0) + inject._SPARE, 0x1234))
    stale(lambda mem: mem.write_u8(ent(0) + int(E.SCRIPT_CONTEXT), 0x10))
    stale(lambda mem: mem.write_u16(ent(0) + int(E.SCRIPT_BASE), 7))
    stale(lambda mem: mem.write_u8(PUSH + 5, 1))


def test_foreign_block_refuses():
    p, img = program()
    words = [N + 0x38] * 17
    with pytest.raises(Refused, match="no script container"):
        inject.plan(p, img, [], words, b"\x7fELF" + bytes(60))
    with pytest.raises(Refused, match="no script container"):
        inject.plan(p, img, [], words, bytes(15) + b"\x01" + bytes(48))


def test_other_rows_container_refuses():
    p, img = program()
    other = container(replace(p, row=ROW + 1), img, LOAD)
    with pytest.raises(Refused, match=f"row {ROW + 1}'s script, not row {ROW}'s"):
        inject.plan(p, img, [], [N + 0x38] * 17, other)


def test_a_cut_container_refuses():
    p, img = program()
    with pytest.raises(Refused, match="does not read"):
        inject.plan(p, img, [], [N + 0x38] * 17, FULL[:100])
    with pytest.raises(Refused, match="version 2"):
        inject.plan(p, img, [], [N + 0x38] * 17, poke(FULL, 4, 2, 2))


def test_too_big_refuses(monkeypatch):
    p, img = program()
    monkeypatch.setattr(inject, "PUSH_SIZE", len(FULL) - 1)
    with pytest.raises(Refused, match=f"holds {len(FULL) - 1}"):
        inject.plan(p, img, [], [N + 0x38] * 17, bytes(16))
    monkeypatch.setattr(inject, "PUSH_SIZE", len(FULL))
    inject.plan(p, img, [], [N + 0x38] * 17, bytes(16))


@pytest.mark.parametrize("words", [[0] * 17, [0x1234] * 17, [N + 0x44] * 17])
def test_no_header_word_of_the_row_refuses(words):
    p, img = program()
    with pytest.raises(Refused, match=f"none of row {ROW}'s header words"):
        inject.plan(p, img, [], words, bytes(16))


def test_a_row_is_17_words():
    p, img = program()
    with pytest.raises(ValueError, match="17 header words"):
        inject.plan(p, img, [], [N + 0x38] * 16, bytes(16))


# ---- restoring


def native_first(mem):
    return lambda header: mem.u32(mem.u32(header)) or None


def restore_plan(mem):
    snap = inject._snapshot(mem, ROW)
    entry = native_first(mem)
    return inject.plan_restore(ROW, snap.live, snap.row_words, snap.home, entry, spare=snap.spare)


def test_restore_puts_the_native_words_back():
    mem = pushed_world(state(0), state(1, context=0x00))
    plan = restore_plan(mem)
    assert plan.writes[0].what == f"row {ROW} headers" and plan.writes[-1].what == "container"
    apply(mem, plan)
    assert row_words(mem) == [N + 0x38] * 17
    assert live_script.read(mem, ent(0)) == state(0)
    assert live_script.read(mem, ent(1)) == state(1, context=0x00)
    assert mem.read(PUSH, inject.GUARD) == bytes(inject.GUARD)


def test_restore_restarts_what_has_no_native_place():
    mem = world([state(0)])
    apply(mem, make_plan(mem, *edited()))
    mem.write_u32(ent(0) + int(E.SCRIPT_PCS), LOAD)  # the new enter, which the game has not
    plan = restore_plan(mem)
    assert "restarted: SCRIPT_PCS[0]" in plan.notes[0]
    apply(mem, plan)
    got = live_script.read(mem, ent(0))
    assert got.header == N + 0x38 and got.pcs == (N,) * 4 and (got.context, got.base) == (0, 0)


def test_restore_without_a_container_refuses():
    mem = world([state(0)])
    snap = inject._snapshot(mem, ROW)
    with pytest.raises(Refused, match="nothing is pushed"):
        inject.plan_restore(ROW, snap.live, snap.row_words, snap.home, lambda h: None)


def test_restore_clears_a_container_nothing_points_at():
    mem = pushed_world()
    mem.write(row_at(), u32s(*[N + 0x38] * 17))  # a quest load put the game's words back
    plan = restore_plan(mem)
    assert plan.notes == (f"no header word of row {ROW} pointed into the container",)
    apply(mem, plan)
    assert mem.read(PUSH, inject.GUARD) == bytes(inject.GUARD)


def test_restore_restart_needs_a_base_routine():
    mem = pushed_world(state(0))
    mem.write_u32(ent(0) + int(E.SCRIPT_PCS), LOAD + 0x0C)
    snap = inject._snapshot(mem, ROW)
    with pytest.raises(Refused, match="no base routine"):
        inject.plan_restore(ROW, snap.live, snap.row_words, snap.home, lambda h: None)


def test_restore_refuses_the_other_rows_container():
    mem = pushed_world(state(0))
    snap = inject._snapshot(mem, ROW)
    with pytest.raises(Refused, match="restore row 5 first"):
        inject.plan_restore(ROW + 1, snap.live, snap.row_words, snap.home, lambda h: None)


# ---- the fake PPSSPP


class Emu:
    """A FakePPSSPP over BASE up, on its own loop thread, for the blocking client."""

    def __init__(self, size=SIZE):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.fake = FakePPSSPP(base=BASE, size=size)
        self.run(self.fake.__aenter__())
        self.conditions = []

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def put(self, address, data):
        self.fake.memory[address - BASE : address - BASE + len(data)] = data

    def get(self, address, size):
        return bytes(self.fake.memory[address - BASE : address - BASE + size])

    def tick(self):
        """Trip SCRIPT_TICK as soon as the code arms it."""

        def trip():
            until = time.monotonic() + 5
            while time.monotonic() < until:
                if (bp := self.fake.breakpoints.get(a.SCRIPT_TICK)) is not None:
                    self.conditions.append(bp["condition"])
                    self.run(self.fake.execute(a.SCRIPT_TICK))
                    return
                time.sleep(0.005)

        thread = threading.Thread(target=trip, daemon=True)
        thread.start()
        return thread

    def events(self):
        return [m["event"] for m in self.fake.received]

    def close(self):
        self.run(self.fake.__aexit__(None, None, None))
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()
        self.loop.close()


@pytest.fixture
def emu():
    e = Emu()
    yield e
    e.close()


def play(emu, states=(), **kw):
    for address, data in parts(states, **kw):
        emu.put(address, data)
    return Session.attach(emu.fake.port, timeout=5)


def test_push_status_restore(emu):
    states = [
        state(0),
        state(1, pcs=(N + 4, N + 8, N + 0x0C, N + 0x1C)),
        state(2, species=ROW + 1, header=N + 0x44),
    ]
    p, img = program()
    with play(emu, states) as s:
        before = inject.status(s.mem)
        assert before.mapped and before.container is None and not before.foreign
        assert before.rows == {} and [m.pushed for m in before.monsters] == [False] * 3

        ticking = emu.tick()
        pushed = inject.push(s, p, img)
        ticking.join()
        assert pushed.mismatched == [] and "stopped at SCRIPT_TICK" in pushed.where
        assert emu.conditions == [f"a0 == {ent(0):#x} || a0 == {ent(1):#x}"]
        assert not emu.fake.breakpoints and not emu.fake.stepping
        assert emu.get(PUSH, len(FULL)) == FULL
        assert live_script.read(s.mem, ent(0)).header == LOAD + HEADER
        assert live_script.read(s.mem, ent(1)).pcs == (LOAD,) * 4
        assert emu.get(ent(2), 0x800) == entity_image(states[2])

        now = inject.status(s.mem)
        assert now.container is not None and now.container.row == ROW and now.mapped
        assert (now.container.load, now.container.extent) == (LOAD, len(FULL))
        assert now.rows == {ROW: tuple(range(17))}
        assert [(m.slot, m.species, m.pushed) for m in now.monsters] == [
            (1, ROW, True),
            (2, ROW, True),
            (3, ROW + 1, False),
        ]
        assert now.monsters[0].pc == LOAD + 8  # context 0x20 resumes at pcs[1]

        ticking = emu.tick()
        done = inject.restore(s, ROW)
        ticking.join()
        assert done.mismatched == [] and not emu.fake.stepping and not emu.fake.breakpoints
        assert live_script.read(s.mem, ent(0)) == state(0)
        got = live_script.read(s.mem, ent(1))
        assert got.header == N + 0x38 and got.pcs == (N,) * 4
        assert emu.get(row_at(), 68) == u32s(*[N + 0x38] * 17)
        assert emu.get(PUSH, inject.GUARD) == bytes(inject.GUARD)
        after = inject.status(s.mem)
        assert after.container is None and after.rows == {}
        assert not any(m.pushed for m in after.monsters)


def test_push_pauses_when_no_monster_ticks(emu):
    p, img = program()
    with play(emu, [state(0)]) as s:
        pushed = inject.push(s, p, img, timeout=0.2)
    assert pushed.where == "the game, paused" and pushed.mismatched == []
    assert "cpu.breakpoint.add" in emu.events() and not emu.fake.stepping
    assert emu.get(PUSH, len(FULL)) == FULL
    assert emu.get(ent(0) + int(E.SCRIPT_HEADER), 4) == u32s(LOAD + HEADER)


def test_push_without_a_monster_stops_nowhere(emu):
    p, img = program()
    with play(emu) as s:
        pushed = inject.push(s, p, img)
    assert pushed.where == "the game, paused" and not emu.fake.stepping
    assert "cpu.breakpoint.add" not in emu.events()
    assert pushed.plan.notes == (f"no live monster runs row {ROW}'s script",)
    assert emu.get(row_at(), 68) == u32s(*[LOAD + HEADER] * 17)


def test_push_refuses_outside_a_quest(emu):
    p, img = program()
    with play(emu, [state(0)], words=[0] * 17) as s:
        ticking = emu.tick()
        with pytest.raises(Refused, match="none of row 5's header words"):
            inject.push(s, p, img, timeout=0.5)
        ticking.join()
    assert not emu.fake.stepping and not emu.fake.breakpoints
    assert emu.get(PUSH, 64) == bytes(64)
    assert emu.get(ent(0), 0x800) == entity_image(state(0))


def test_push_refuses_a_stale_plan(emu, monkeypatch):
    p, img = program()
    with play(emu, [state(0)]) as s:
        real = inject.plan

        def stale(*args, **kw):
            built = real(*args, **kw)
            emu.put(row_at(), u32s(*[0x1234] * 17))  # the game moves on after the read
            return built

        monkeypatch.setattr(inject, "plan", stale)
        ticking = emu.tick()
        with pytest.raises(Refused, match="row 5 headers"):
            inject.push(s, p, img)
        ticking.join()
    assert not emu.fake.stepping and emu.get(PUSH, 64) == bytes(64)


def test_push_needs_the_64_mb_mode():
    short = Emu(size=PUSH - BASE)
    try:
        p, img = program()
        with play(short, [state(0)]) as s:
            with pytest.raises(Refused, match="64 MB"):
                inject.push(s, p, img)
            with pytest.raises(Refused, match="64 MB"):
                inject.restore(s, ROW)
            now = inject.status(s.mem)
            assert not now.mapped and now.container is None and len(now.monsters) == 1
        assert not short.fake.stepping and "cpu.breakpoint.add" not in short.events()
    finally:
        short.close()


def test_status_sees_a_foreign_block(emu):
    with play(emu) as s:
        emu.put(PUSH, b"\x7fELF" + bytes(60))
        now = inject.status(s.mem)
        assert now.foreign and now.container is None
        emu.put(PUSH, poke(FULL, 4, 2, 2)[:64])
        assert inject.status(s.mem).foreign


# ---- the game's own scripts


@pytest.fixture(scope="module", params=[75, 1], ids=["em75", "em01"])
def lifted(game, request):
    """em75 (one header) and em01 (seven, and floats between them), lifted and assembled."""
    sc = script.Scripts(game, request.param)
    prog = lift.lift(sc, request.param)
    return sc, prog, asm.assemble(prog)


FIELDS = {
    "header": E.SCRIPT_HEADER,
    "pcs": E.SCRIPT_PCS,
    "int_08": E.SCRIPT_INT_08,
    "call_return": E.SCRIPT_CALL_RETURN,
    "loop_return": E.SCRIPT_LOOP_RETURN,
}


def planned(plan):
    """Address and value of every word a plan writes."""
    out = {}
    for w in plan.writes:
        at = w.at
        for data, _ in w.pieces:
            if len(data) == 4:
                out[at] = struct.unpack("<I", data)[0]
            at += len(data)
    return out


def test_game_container_maps(lifted):
    _, prog, img = lifted
    c = Container.read(container(prog, img, LOAD))
    assert c.headers == {va: LOAD + off for va, off in img.headers.items()}
    assert all(c.native(c.rebase(n)) == n for n, _ in c.origin)
    assert all(c.entry(h) for h in c.headers.values())
    for it in c.items:
        if it.kind in (Kind.TABLE, Kind.DATA):
            last = it.size - 1
            assert c.rebase(it.native) == LOAD + it.offset
            assert c.native(LOAD + it.offset + last) == it.native + last
    stick = Container.read(container(prog, img, 0, origin=False))
    assert stick.image == img.at(0) and stick.place(LOAD) == container(prog, img, LOAD, False)


def test_game_every_planned_word_is_an_instruction_start(lifted):
    sc, prog, img = lifted
    words = [sc.header_at(k, prog.row) for k in range(17)]
    native, states = {}, []
    for h in prog.headers():
        native[h.va] = {i.pc: i for r in sc.listing(h.va).routines.values() for i in r.insns}
        pcs = sorted(native[h.va])
        for k in range(0, len(pcs) - 3, 4):
            chunk = tuple(pcs[k : k + 4])
            states.append(
                replace(
                    state(),
                    entity=ENT + 0x800 * len(states),
                    species=prog.row,
                    header=h.va,
                    pcs=chunk,
                    context=0,
                    interrupts={0x08: chunk[0], 0x02: 0, 0x04: 0},
                    call_return=chunk[1],
                    route=0,
                    loop_return=chunk[2],
                )
            )
    assert len(states) > 400
    plan = inject.plan(prog, img, states, words, bytes(16))
    assert plan.notes == tuple(f"entity 0x{s.entity:08X} rebased" for s in states)

    put = planned(plan)
    moved_header = {va: LOAD + off for va, off in img.headers.items()}
    at_row = inject._row_at(prog.row)
    assert [put[at_row + 4 * k] for k in range(17)] == [moved_header[w] for w in words]
    space = Space([Image(img.at(LOAD), LOAD), sc.ovl])
    window = range(sc.ovl.text.stop, LOAD + len(img.data))
    moved = {}
    for va, at in moved_header.items():
        decoded = script.Header.read(space, at, window, range(LOAD, LOAD + len(img.data)))
        moved[va] = {
            i.pc: i
            for r in script.disassemble(space.u8, decoded).routines.values()
            for i in r.insns
        }
        assert len(moved[va]) == len(native[va])
    for before in states:
        word = {name: before.entity + int(field) for name, field in FIELDS.items()}
        assert put[word["header"]] == moved_header[before.header]
        old = [*before.pcs, before.interrupts[0x08], before.call_return, before.loop_return]
        offsets = [word["pcs"] + 4 * k for k in range(4)]
        offsets += [word["int_08"], word["call_return"], word["loop_return"]]
        for was, at in zip(old, offsets, strict=True):
            assert put[at] in moved[before.header], hex(put[at])
            assert moved[before.header][put[at]].raw == native[before.header][was].raw
