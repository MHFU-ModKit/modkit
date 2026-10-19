# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct

import pytest
from mhfu.em import script
from mhfu.em.script import asm, disasm, ir, lift, walk
from mhfu.em.script.opcodes import BY_NAME
from mhfu.files import EM_SPECIES

BASE = 0x0010_0000
MOVED = 0x0020_0000


def hexed(*parts):
    return bytes.fromhex("".join(parts))


def reader(raw):
    """The bytes at BASE; past them the sentinel."""

    def read(pc):
        return raw[pc - BASE] if 0 <= pc - BASE < len(raw) else script.SENTINEL

    return read


# ---- walk


def test_step_is_length():
    read = reader(hexed("3900", "05000100", "1c0002", "0603010001", "ff00"))
    assert [walk.step(read, BASE + o) for o in (0, 2, 6, 9)] == [2, 4, 3, 5]
    assert all(walk.step(read, BASE + o) == disasm.length(read, BASE + o) for o in (0, 2, 6, 9))


def test_step_short_and_stall():
    read = reader(hexed("940001", "940105", "9403", "00"))
    assert disasm.length(read, BASE) == 3 and walk.step(read, BASE) == 5
    assert walk.step(read, BASE + 3) == 3  # a case marker is not an opener
    assert walk.step(read, BASE + 8) == 0  # no case in the table


def test_scan_markers():
    read = reader(hexed("3900", "05000100", "3901", "05000200", "3902", "ff00"))
    s = walk.scan(read, BASE)
    assert (s.markers, s.steps, s.stop) == ([BASE + 6, BASE + 12], 2, None)
    assert disasm.markers(read, BASE) == s.markers
    assert walk.scan(read, BASE + 6).markers == [BASE + 12]  # from a marker on


def test_scan_skips_inner_blocks():
    read = reader(hexed("3900", "3900", "3901", "3902", "3901", "3902", "ff00"))
    assert walk.scan(read, BASE).markers == [BASE + 8, BASE + 10]
    assert walk.scan(read, BASE + 2).markers == [BASE + 4, BASE + 6]


def test_scan_guard():
    def scan(n):
        return walk.scan(reader(hexed("3900") + b"\x92" * n + hexed("3902")), BASE)

    assert walk.GUARD == 1000
    assert (scan(999).stop, scan(999).steps) == (None, 1000)
    assert (scan(1000).stop, scan(1000).markers) == ("guard", [])


def test_scan_stall_and_sentinel():
    assert walk.scan(reader(hexed("3900", "00", "3902")), BASE).stop == "stall"
    assert walk.scan(reader(hexed("3900", "05000100")), BASE).stop == "sentinel"
    assert walk.scan(reader(hexed("3900", "3901", "95", "3902")), BASE).markers == [BASE + 2]


def test_scan_short_opener():
    raw = hexed("940001", "940192", "05000100", "9403")
    read = reader(raw)
    assert disasm.markers(read, BASE) == [BASE + 3, BASE + 10]
    s = walk.scan(read, BASE)  # the first case is stepped over; its value reads as a nop
    assert (s.markers, s.stop) == ([BASE + 10], None)
    stalls = walk.scan(reader(hexed("940001", "940100", "9403")), BASE)
    assert (stalls.markers, stalls.stop) == ([], "stall")


# ---- a native program by hand


def ins(name, **args):
    return ir.Insn(BY_NAME[name], args)


def blk(name, head=(), arms=(), **args):
    return ir.Block(BY_NAME[name], args, list(head), list(arms))


def arm(sub, *body, **args):
    return ir.Arm(sub, args, list(body))


def enter(main, sub):
    return ins("enter", main=main, sub=sub, gate=0)


def native(p):
    """Give a program the addresses the game has: ids, `va`, `pc`, `close` and `end` from its
    own layout, a routine's padding up to the next item its tail."""
    for k, n in enumerate(p.nodes()):
        n.id = n.id or f"n{k}"
    img = asm.assemble(p)
    order = sorted(img.items.values())
    for r in p.routines():
        off, size = img.items[r.name]
        nxt = next((o for o, _ in order if o > off), len(img.data))
        r.tail += bytes(nxt - off - size)
    img = asm.assemble(p)
    for it in p.items:
        if not isinstance(it, ir.Blob):
            it.va = BASE + img.items[it.name][0]
    for n in p.nodes():
        n.pc = BASE + img.nodes[n.id][0]
        if isinstance(n, ir.Block):
            n.close = BASE + img.closes[n.id]
    p.end = BASE + len(img.data)
    return p


def world():
    """Two routines (18 and 17 bytes, 6 and 7 of padding), their lists and a header."""
    top = blk("if_latch", [enter(0, 1)], [arm(1, enter(0, 2))])
    pick = blk("random", arms=[arm(1, enter(1, 0), weight=16), arm(1, weight=16)])
    fields = ["base_l", "call_l", *[None] * 13]
    return native(
        ir.Program(
            1,
            BASE,
            [
                ir.Routine("base_0", [top, ins("call", routine=0), ins("end", kind=0)]),
                ir.Routine("call_0", [pick, ins("end", kind=1)]),
                ir.Table("base_l", ["base_0"]),
                ir.Table("call_l", ["call_0", None]),
                ir.Header("h0", fields, tuple(range(17))),
            ],
        )
    )


def word(img, name, i=0):
    off = img.items[name][0] + 4 * i
    return struct.unpack_from("<I", img.data, off)[0]


def test_world_is_native():
    p = world()
    img = asm.assemble(p)
    assert [(n, img.items[n]) for n in ("base_0", "call_0", "base_l", "call_l", "h0")] == [
        ("base_0", (0, 24)),
        ("call_0", (24, 24)),
        ("base_l", (48, 4)),
        ("call_l", (56, 8)),
        ("h0", (64, 60)),
    ]
    assert p.end == BASE + 124 and len(img.data) == 124
    assert asm.assemble(p, MOVED).data == img.at(MOVED)


def test_relayout_zero_tail_absorbs():
    p = world()
    p.items[0].body.insert(-1, enter(0, 3))
    img = asm.assemble(p)
    assert img.items["base_0"] == (0, 24) and img.items["call_0"] == (24, 24)
    assert len(img.data) == 124 and img.data[20:22] == bytes([0xFF, 0x00])


def test_relayout_nonzero_tail_stays():
    p = world()
    p.items[0].tail = bytes(5) + b"\x12"
    p.items[0].body.insert(-1, ins("walk_next"))
    img = asm.assemble(p)
    assert img.items["base_0"][0] >= 124  # a byte more, and the tail ends in one it cannot give
    assert img.data[:24] == bytes(24)


def test_relayout_moves_routine():
    p = world()
    p.items[0].body[1:1] = [enter(0, 3), enter(0, 4), enter(0, 5)]
    img = asm.assemble(p)
    off, size = img.items["base_0"]
    assert (off, size) == (128, 36) and len(img.data) == 128 + 36
    assert img.data[:24] == bytes(24)  # the vacated span is zero
    assert word(img, "base_l") == BASE + off and img.items["base_l"][0] in img.relocs
    assert img.items["call_0"] == (24, 24) and img.items["h0"] == (64, 60)
    assert img.at(MOVED)[img.items["base_l"][0] :][:4] == struct.pack("<I", MOVED + off)


def test_relayout_origin_follows():
    p = world()
    p.items[0].body[1:1] = [enter(0, 3), enter(0, 4), enter(0, 5)]
    img = asm.assemble(p)
    native = [x for x in ir.walk(p.items[0].body) if x.pc is not None]
    assert len(native) == 6
    for x in native:
        assert img.origin[x.pc] == img.nodes[x.id][0] >= 128
    top = p.items[0].body[0]
    assert img.origin[top.close] == img.closes[top.id] and img.data[img.closes[top.id]] == 0x39


def test_relayout_grown_list_moves():
    p = world()
    p.items[3].slots.append("call_0")
    img = asm.assemble(p)
    assert img.items["call_l"] == (128, 12) and img.items["base_l"] == (48, 4)
    assert word(img, "h0", 1) == BASE + 128 and word(img, "call_l", 2) == BASE + 24
    assert img.data[56:64] == bytes(8)


def test_relayout_header_last():
    p = world()
    p.items[0].body[1:1] = [enter(0, 3), enter(0, 4), enter(0, 5)]
    p.items.append(ir.Routine("fresh", [ins("end", kind=1)]))
    p.items[4].fields.append("base_l")  # a field more: the header no longer fits its place
    img = asm.assemble(p)
    fresh, moved, header = (img.items[n][0] for n in ("fresh", "base_0", "h0"))
    assert moved < fresh < header  # new and moved items in program order, the header after
    assert header == img.headers[p.items[4].va] and header % 8 == 0
    assert len(img.data) == header + 64
    assert word(img, "h0", 15) == BASE + 48 and img.data[64:124] == bytes(60)


def test_relayout_unlifted_program():
    p = ir.Program(
        1, BASE, [ir.Routine("a", [ins("end", kind=0)], va=BASE), ir.Table("t", [], BASE + 2)]
    )
    assert asm.assemble(p).data == b"\xff\x00"


def test_sym_refused():
    p = ir.Program(1, BASE, [ir.Routine("a", [ins("call", routine=ir.Sym("x"))])])
    with pytest.raises(asm.AssembleError, match="call edit.resolve first"):
        asm.assemble(p)


def test_closes():
    p = world()
    img = asm.assemble(p)
    random = p.items[1].body[0]
    assert img.closes[random.id] == img.nodes[random.arms[1].id][0] + 3
    assert img.data[img.closes[random.id] :][:2] == bytes([0x80, 0xFF])


# ---- the game


@pytest.fixture(scope="module")
def rows(game):
    """Every overlay's species rows, lifted: {em: (Scripts, {row: Program})}."""
    out = {}
    for em in EM_SPECIES:
        sc = script.Scripts(game, em)
        out[em] = (sc, lift.lift_all(sc))
    return out


def test_game_scan(rows):
    blocks = short = longest = 0
    off = []
    for em, (sc, ps) in rows.items():
        for r in ps[em].routines():
            for n in ir.walk(r.body):
                if not isinstance(n, ir.Block):
                    continue
                s = walk.scan(sc.mem.u8, n.pc)
                blocks, longest = blocks + 1, max(longest, s.steps)
                if n.op.code == walk.SHORT:
                    short += 1
                    assert s.stop == "stall"
                else:
                    assert s.stop is None
                    if s.markers != [a.pc for a in n.arms] + [n.close]:
                        off.append((em, n.op.name))
    assert blocks > 5000 and short == 9 and longest == 794
    assert off == [(83, "random")]  # a random_shifted inside it shares its markers
