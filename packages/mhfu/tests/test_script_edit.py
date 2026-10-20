# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct
import time

import pytest
from mhfu.em import script
from mhfu.em.script import asm, check, disasm, edit, ir, lift, walk
from mhfu.em.script.edit import EditError
from mhfu.em.script.opcodes import BY_NAME, Ref, slot
from mhfu.files import EM_SPECIES
from mhfu.memory import Image, Space

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


# ---- ir


def test_index():
    p = world()
    idx = ir.Index(p)
    top = p.items[0].body[0]
    first, other = top.head[0], top.arms[0]
    assert idx.at(top.pc) == (top,) and idx.at(top.close) == (top,)
    assert idx.at(other.pc) == (other,) and idx.at(top.pc + 1) == ()
    assert idx.ending_at(other.pc) is first and idx.ending_at(top.pc) is None
    assert idx.node(first.id) is first and idx.node(other.id) is other
    assert idx.routine(other.id) is p.items[0] and idx.routine(p.items[1].body[1].id) is p.items[1]
    assert idx.home(first.id) is top.head and idx.home(top.id) is p.items[0].body
    assert idx.home(other.body[0].id) is other.body
    assert first.id in idx and "zz" not in idx
    for bad in (idx.node, idx.routine, idx.home):
        with pytest.raises(KeyError):
            bad("zz")
    with pytest.raises(KeyError):
        idx.home(other.id)  # an arm sits in no list of nodes
    assert p.at(top.close) == idx.at(top.close) and p.ending_at(other.pc) is first


def test_origin_defaults():
    assert ins("end", kind=0).origin is None and blk("if_latch").origin is None
    assert arm(1).origin is None


def test_fresh_counts_extra():
    p = world()
    assert p.fresh() == "n11"
    assert p.fresh([ir.Insn(BY_NAME["end"], {"kind": 0}, "n20"), arm(1)]) == "n21"


def test_labels():
    call = ins("call", routine=ir.Sym("x"))
    assert ir.labels(call) == [("routine", ir.Sym("x"), Ref("call", 1, 0))]
    walk_ = ins("walk", mode=0, limit=1, stages=ir.Sym("a"), step=2, arrive=ir.Sym("b"))
    assert [(n, r.kind) for n, _, r in ir.labels(walk_)] == [
        ("stages", "stages"),
        ("arrive", "walk"),
    ]
    deep = ins("call_deep", table=2, routine=ir.Sym("d"))
    assert ir.labels(deep) == [("routine", ir.Sym("d"), Ref("call", 17, 0))]
    assert ir.labels(ins("call", routine=3)) == [] and ir.labels(blk("if_latch")) == []
    assert ir.labels(ins("enter", main=0, sub=ir.Sym("s"), gate=0)) == []  # no list there


def test_reach_follows_labels():
    p = world()
    spin = ir.Routine("spin", [ins("end", kind=1)])
    lone = ir.Routine("lone", [ins("end", kind=1)])
    p.items += [spin, lone]
    p.items[0].body.insert(1, ins("call", routine=ir.Sym("spin")))
    arm_ = ins("route_arm", list=ir.Sym("lone"), count=1, body=ir.Sym("lone"), loop=0)
    p.items[0].body.insert(1, arm_)  # a route's lists are roots of their own
    reach = p.reach()
    assert reach["spin"] == ("h0",) and reach["lone"] == ()


# ---- builders


def encoded(*nodes):
    return asm.assemble(ir.Program(1, BASE, [ir.Routine("r", list(nodes))])).data


def ent(main, sub):
    return edit.insn("enter", main, sub, 0)


def test_insn():
    n = edit.insn("enter", 3, 0, 0)
    assert (n.op.name, n.args, n.id, n.pc, n.origin) == (
        "enter",
        {"main": 3, "sub": 0, "gate": 0},
        "",
        None,
        None,
    )
    assert edit.insn("walk_next").args == {} and edit.insn("call", ir.Sym("x")).args[
        "routine"
    ] == ir.Sym("x")
    assert encoded(edit.insn("set_target", 3, 1, 0xBBCC)) == hexed("0603", "01", "bbcc")
    assert encoded(edit.insn("set_target", 2, 1, 2)) == hexed("0602", "0102")
    assert edit.insn("set_target", 2, 1, 2).args == {"kind": 2, "a": 1, "b": 2}
    assert encoded(edit.insn("end", 0xFB)) == hexed("fffb")


@pytest.mark.parametrize(
    ("args", "match"),
    [
        (("if_latch",), "opens a block"),
        (("nope",), "no opcode"),
        (("enter", 1), r"takes \['main', 'sub', 'gate'\]"),
        (("enter", 256, 0, 0), "does not fit 1 byte"),
        (("set_target", 3, 1), r"takes \['kind', 'a', 'section'\]"),
        (("enter", 1.5, 0, 0), "is no number"),
    ],
)
def test_insn_errors(args, match):
    with pytest.raises(EditError, match=match):
        edit.insn(*args)


def test_if_builder():
    a = edit.if_("if_latch", then=[ent(0, 1)])
    assert a.arms == [] and a.id == "" and a.args == {} and len(a.head) == 1
    b = edit.if_("if_in_section", 0x74, then=[], else_=[ent(0, 2)])
    assert b.args == {"section": 0x74} and [(x.sub, x.id) for x in b.arms] == [(1, "")]
    assert encoded(edit.if_("if_latch", then=[ent(0, 1)], else_=[ent(0, 2)])) == hexed(
        "3900", "05000100", "3901", "05000200", "3902"
    )
    for bad in ("enter", "switch_hp", "repeat"):
        with pytest.raises(EditError, match="is not a if block"):
            edit.if_(bad, then=[])
    with pytest.raises(EditError, match="takes"):
        edit.if_("if_latch", 1, then=[])


def test_switch_builder():
    sw = edit.switch("switch_hp", cases=[(5, [ent(0, 1)]), (9, [ent(0, 2)])], default=[ent(0, 3)])
    assert [(a.sub, a.args) for a in sw.arms] == [(1, {"case": 5}), (1, {"case": 9}), (2, {})]
    assert encoded(sw) == hexed(
        "1c0002", "1c0105", "05000100", "1c0109", "05000200", "1c02", "05000300", "1c03"
    )
    sec = edit.switch("switch_section", cases=[(0x74, [])])
    assert encoded(sec) == hexed("150001", "15010074", "1503")
    extra = edit.switch("switch_query", 7, cases=[(1, [])])
    assert extra.args == {"arg": 7} and encoded(extra) == hexed("79000107", "790101", "7903")
    bucket = edit.switch("switch_range", cases=[([ent(0, 1)],), ([ent(0, 2)],)])
    assert [a.args for a in bucket.arms] == [{}, {}] and encoded(bucket)[:3] == hexed("830001")
    zone = edit.switch("switch_zone_relation", cases=[([],), ([],)])
    assert encoded(zone) == hexed("6200", "6201", "6202", "62ff")
    with pytest.raises(EditError, match="no default"):
        edit.switch("switch_range", cases=[([],)], default=[])
    with pytest.raises(EditError, match="case takes"):
        edit.switch("switch_hp", cases=[([],)])
    with pytest.raises(EditError, match="is not a switch/bucket/zone"):
        edit.switch("if_latch", cases=[])


def test_random_builder():
    r = edit.random([(16, [ent(1, 0)])])
    assert [a.args["weight"] for a in r.arms] == [16, 16] and r.arms[1].body == []
    assert encoded(r) == hexed("800002", "800110", "05010000", "800210", "80ff")
    assert [a.args["weight"] for a in edit.random([(16, []), (16, [])]).arms] == [16, 16]
    assert [a.args["weight"] for a in edit.random([]).arms] == [32]
    shifted = edit.random([(20, [])], shift=5)
    assert shifted.op.name == "random_shifted" and shifted.args == {"shift": 5}
    assert encoded(shifted) == hexed("85000205", "800114", "80020c", "80ff")
    full = edit.random([(2, [])] * 12 + [(8, [])])
    assert len(full.arms) == 13 and encoded(full)[2] == 13
    with pytest.raises(EditError, match="add up to 33"):
        edit.random([(20, []), (13, [])])
    with pytest.raises(EditError, match="at most 13"):
        edit.random([(1, [])] * 13)
    with pytest.raises(EditError, match="does not fit"):
        edit.random([], shift=300)


def test_repeat_builder():
    r = edit.repeat(3, [ent(0, 1)])
    assert (
        r.args == {"count": 3}
        and r.arms == []
        and encoded(r) == hexed("240003", "05000100", "2401")
    )
    with pytest.raises(EditError, match="does not fit"):
        edit.repeat(256, [])


# ---- node edits


def shape(nodes):
    """The tree as nested names: a block is `(name, head, [arm bodies])`."""
    return [
        n.op.name
        if isinstance(n, ir.Insn)
        else (n.op.name, shape(n.head), [shape(a.body) for a in n.arms])
        for n in nodes
    ]


def test_insert_before_after():
    p = world()
    body = p.items[0].body
    call = body[1]
    edit.insert(p, call.id, [edit.insn("wait", 5)])
    edit.insert(p, call.id, [edit.insn("wait", 6), edit.insn("wait", 7)], after=True)
    assert shape(body) == [shape(body[:1])[0], "wait", "call", "wait", "wait", "end"]
    assert [n.id for n in body[1:]] == ["n11", "n4", "n12", "n13", "n5"]
    assert [n.pc for n in body] == [body[0].pc, None, call.pc, None, None, body[5].pc]
    first = body[0].head[0]
    edit.insert(p, first.id, [edit.insn("wait", 1)])  # into the block's head
    assert shape(body[0].head) == ["wait", "enter"]


def test_insert_into_arm():
    p = world()
    top = p.items[0].body[0]
    other = top.arms[0]
    edit.insert(p, other.id, [edit.insn("wait", 1)])
    edit.insert(p, other.id, [edit.insn("wait", 2)], after=True)
    assert [n.args.get("frames") for n in other.body] == [1, None, 2]


def test_ids_reach_arms():
    p = world()
    block = edit.if_("if_latch", then=[edit.insn("wait", 1)], else_=[edit.insn("wait", 2)])
    edit.put(p, "call_0", [block])
    ids = [n.id for n in ir.walk([block])]
    assert ids == ["n11", "n12", "n13", "n14"] and len({n.id for n in p.nodes()}) == 15
    assert p.fresh() == "n15"
    kept = edit.insn("wait", 3)
    kept.id = "n40"
    edit.put(p, "call_0", [kept, edit.insn("wait", 4)])
    assert [n.id for n in p.items[1].body[:2]] == ["n40", "n41"]


def test_ids_in_use_refused():
    p = world()
    node = edit.insn("wait", 1)
    node.id = "n3"
    with pytest.raises(EditError, match="n3 is in the program already"):
        edit.put(p, "call_0", [node])
    twice = [edit.insn("wait", 1), edit.insn("wait", 1)]
    twice[0].id = twice[1].id = "x"
    with pytest.raises(EditError, match="x is in the program already"):
        edit.put(p, "call_0", twice)
    assert shape(p.items[1].body) == shape(world().items[1].body)


def test_put():
    p = world()
    edit.add(p, ir.Routine("empty", []))
    edit.put(p, "empty", [edit.insn("end", 1)])
    edit.put(p, "empty", [edit.insn("wait", 1)])
    edit.put(p, "empty", [edit.insn("wait", 2)], at=2)
    assert [n.args for n in p.item("empty").body] == [{"frames": 1}, {"kind": 1}, {"frames": 2}]
    for bad, match in ((3 + 1, "out of range"), (-1, "out of range")):
        with pytest.raises(EditError, match=match):
            edit.put(p, "empty", [], at=bad)
    with pytest.raises(EditError, match="no routine named base_l"):
        edit.put(p, "base_l", [])
    with pytest.raises(EditError, match="no routine named zz"):
        edit.put(p, "zz", [])


def test_remove_and_replace():
    p = world()
    edit.insert(p, p.items[0].body[1].id, [edit.insn("wait", 1), edit.insn("wait", 1)])
    body = p.items[0].body
    second = body[2]
    edit.remove(p, second.id)  # the first of two equal nodes stays
    assert [n.id for n in body] == ["n0", "n11", "n4", "n5"]
    edit.replace(p, "n11", edit.insn("wait", 9))
    assert body[1].args == {"frames": 9} and body[1].id == "n12"  # one past the highest in use
    nested = body[0].head[0]
    edit.replace(p, nested.id, edit.if_("if_latch", then=[]))
    assert shape(body[0].head) == [("if_latch", [], [])] and body[0].head[0].id == "n13"
    edit.remove(p, "n0")
    assert shape(body) == ["wait", "call", "end"]


def test_node_edit_errors():
    p = world()
    arm_ = p.items[0].body[0].arms[0]
    for call in (
        lambda: edit.insert(p, "zz", []),
        lambda: edit.remove(p, "zz"),
        lambda: edit.replace(p, "zz", edit.insn("walk_next")),
    ):
        with pytest.raises(EditError, match="no node zz"):
            call()
    with pytest.raises(EditError, match="is an arm"):
        edit.remove(p, arm_.id)
    with pytest.raises(EditError, match="is an arm"):
        edit.replace(p, arm_.id, edit.insn("walk_next"))
    assert shape(p.items[0].body) == shape(world().items[0].body)


def test_clone():
    p = world()
    top = p.items[0].body[0]
    (copy,) = edit.clone([top])
    assert isinstance(copy, ir.Block) and copy is not top
    assert (copy.id, copy.pc, copy.close, copy.origin) == ("", None, None, top.id)
    assert (copy.arms[0].id, copy.arms[0].pc, copy.arms[0].origin) == ("", None, top.arms[0].id)
    assert copy.head[0].origin == top.head[0].id and copy.arms[0].body[0].pc is None
    copy.head[0].args["sub"] = 7
    assert top.head[0].args["sub"] == 1 and copy.args is not top.args
    edit.put(p, "call_0", [copy])
    assert [n.id for n in ir.walk([copy])] == ["n11", "n12", "n13", "n14"]
    assert top.id == "n0" and copy.origin == "n0"
    assert edit.clone([edit.insn("walk_next")])[0].origin is None  # no source id yet


# ---- modify, move, unwrap, arms


def under(p, name, nid):
    """The node `nid` of routine `name`, wherever it sits."""
    return next(n for n in ir.walk(p.item(name).body) if n.id == nid)


def test_modify():
    p = world()
    first = under(p, "base_0", "n1")  # enter 0 1 0
    edit.modify(p, "n1", sub=2)
    assert (first.id, first.args, first.pc, first.origin) == (
        "n1",
        dict(main=0, sub=2, gate=0),
        None,
        "n1",
    )
    edit.modify(p, "n1", main=4, sub=3)
    assert first.args == dict(main=4, sub=3, gate=0) and first.origin == "n1"
    pick = under(p, "call_0", "n6")
    edit.modify(p, "n7", weight=20)  # an arm
    assert (
        pick.arms[0].args == {"weight": 20}
        and pick.arms[0].pc is None
        and pick.arms[1].pc is not None
    )
    edit.put(p, "call_0", [edit.repeat(2, [])])
    edit.modify(p, "n11", count=5)  # a block's opener
    assert under(p, "call_0", "n11").args == {"count": 5}
    assert pick.pc is not None  # nothing changed in the random itself


def test_modify_layout():
    p = ir.Program(1, 0, [ir.Routine("r", [edit.insn("set_target", 1, 2, 3)])])
    p.item("r").body[0].id = "x"
    edit.modify(p, "x", a=9)
    assert p.item("r").body[0].args == dict(kind=1, a=9, b=3)
    with pytest.raises(EditError, match=r"needs \['section'\] too"):
        edit.modify(p, "x", kind=3)
    edit.modify(p, "x", kind=3, section=0x101)  # b is gone with the layout that had it
    assert p.item("r").body[0].args == dict(kind=3, a=9, section=0x101)


@pytest.mark.parametrize(
    ("nid", "args", "match"),
    [
        ("zz", {"sub": 1}, "no node zz"),
        ("n1", {}, "nothing to change"),
        ("n1", {"volume": 1}, r"takes \['main', 'sub', 'gate'\], not \['volume'\]"),
        ("n1", {"sub": 256}, "does not fit"),
        ("n1", {"sub": "x"}, "no number"),
        ("n0", {"sub": 1}, r"takes \[\], not \['sub'\]"),  # an if has no operands
        ("n2", {"weight": 1}, "takes"),  # an else has none
        ("n7", {"sub": 1}, r"takes \['weight'\]"),
    ],
)
def test_modify_errors(nid, args, match):
    p = world()
    with pytest.raises(EditError, match=match):
        edit.modify(p, nid, **args)
    assert [shape(r.body) for r in p.routines()] == [shape(r.body) for r in world().routines()]
    assert all(n.pc is not None for n in p.nodes())


def test_move_beside():
    p = world()
    body = p.item("base_0").body  # n0 (if), n4 (call), n5 (end)
    edit.move(p, "n4", "n0")
    assert (
        [n.id for n in body] == ["n4", "n0", "n5"] and body[0].pc is None and body[0].origin == "n4"
    )
    assert body[1].pc is not None and body[1].head[0].pc is not None  # what is inside is as it was
    edit.move(p, "n0", "n5", "after")
    assert [n.id for n in body] == ["n4", "n5", "n0"]
    edit.move(p, "n4", "n1", "after")  # beside a node in a block's head
    assert [n.id for n in under(p, "base_0", "n0").head] == ["n1", "n4"]
    edit.move(p, "n4", "n2", "before")  # beside an arm: the start of its body
    assert [n.id for n in under(p, "base_0", "n2").body] == ["n4", "n3"]
    edit.move(p, "n4", "n2", "after")  # or the end
    assert [n.id for n in under(p, "base_0", "n2").body] == ["n3", "n4"]


def test_move_into():
    p = world()
    edit.move(p, "n8", "base_0", "start")
    edit.move(p, "n3", "base_0", "end")
    edit.move(p, "n5", "n0", "start")  # a block's head
    edit.move(p, "n4", "n9", "end")  # an arm
    ids = {n.id: [x.id for x in under(p, "base_0", n.id).head] for n in [p.item("base_0").body[1]]}
    assert [n.id for n in p.item("base_0").body] == ["n8", "n0", "n3"] and ids == {
        "n0": ["n5", "n1"]
    }
    assert [n.id for n in under(p, "call_0", "n9").body] == ["n4"]
    assert [n.id for n in p.item("call_0").body] == ["n6", "n10"]
    assert all(under(p, r, i).pc is None for r, i in (("base_0", "n8"), ("base_0", "n3")))


def test_move_a_block_whole():
    p = world()
    edit.move(p, "n0", "call_0", "end")
    out = p.item("call_0").body
    assert [n.id for n in out] == ["n6", "n10", "n0"]
    assert out[2].pc is None and out[2].head[0].pc is not None and out[2].close is not None
    assert [n.id for n in p.item("base_0").body] == ["n4", "n5"]


@pytest.mark.parametrize(
    ("nid", "to", "where", "match"),
    [
        ("n0", "n1", "before", "is n0 or inside it"),
        ("n0", "n0", "after", "is n0 or inside it"),
        ("n0", "n2", "end", "is inside n0"),
        ("n0", "n0", "start", "is inside n0"),
        ("n2", "base_0", "end", "is an arm"),
        ("zz", "base_0", "end", "no node zz"),
        ("n4", "zz", "before", "no node zz"),
        ("n4", "zz", "end", "no routine or node zz"),
        ("n4", "base_0", "before", "no node base_0"),
        ("n4", "n5", "end", "instruction, with no place"),
        ("n4", "n6", "start", "has no head"),
        ("n4", "n0", "sideways", "no place"),
    ],
)
def test_move_errors(nid, to, where, match):
    p = world()
    with pytest.raises(EditError, match=match):
        edit.move(p, nid, to, where)
    assert [shape(r.body) for r in p.routines()] == [shape(r.body) for r in world().routines()]
    assert all(n.pc is not None for n in p.nodes())


@pytest.mark.parametrize(
    ("keep", "want"),
    [
        ("then", ["n1", "n4", "n5"]),
        ("else", ["n3", "n4", "n5"]),
        ("n2", ["n3", "n4", "n5"]),
        (None, ["n4", "n5"]),
    ],
)
def test_unwrap(keep, want):
    p = world()
    edit.unwrap(p, "n0", keep)
    body = p.item("base_0").body
    assert [n.id for n in body] == want
    assert [n.pc is None for n in body] == [True] * (len(want) - 2) + [False, False]
    assert body[0].origin == want[0] if keep else True


def test_unwrap_arms_and_repeats():
    p = world()
    edit.put(p, "call_0", [edit.repeat(2, [edit.insn("wait", 1)])], at=0)
    edit.unwrap(p, "n11", "then")
    edit.unwrap(p, "n6", "n7")
    assert [n.id for n in p.item("call_0").body] == ["n12", "n8", "n10"]


@pytest.mark.parametrize(
    ("nid", "keep", "match"),
    [
        ("n1", "then", "is no block"),
        ("n2", None, "is an arm"),
        ("zz", None, "no node zz"),
        ("n6", "then", "has no then-branch"),
        ("n6", "else", "has no else"),
        ("n6", "n2", "is no arm of n6"),
        ("n0", "n7", "is no arm of n0"),
    ],
)
def test_unwrap_errors(nid, keep, match):
    p = world()
    with pytest.raises(EditError, match=match):
        edit.unwrap(p, nid, keep)
    assert [shape(r.body) for r in p.routines()] == [shape(r.body) for r in world().routines()]


def switch_world():
    p = world()
    edit.put(p, "call_0", [edit.switch("switch_hp", cases=[(1, []), (2, [])], default=[])], at=0)
    return p  # n11 is the switch, n12 and n13 its cases, n14 its default


def test_add_arm():
    p = world()
    pick = under(p, "call_0", "n6")
    edit.modify(p, "n9", weight=8)
    edit.add_arm(p, "n6", ir.Arm(1, {"weight": 8}, [edit.insn("wait", 1)]), at=1)
    assert [a.args["weight"] for a in pick.arms] == [16, 8, 8]
    assert (pick.arms[1].id, pick.arms[1].body[0].id, pick.pc) == ("n11", "n12", None)
    assert pick.origin == "n6" and pick.arms[0].pc is not None
    edit.insert_arm(p, "n9", ir.Arm(1, {"weight": 1}, []), after=True)
    assert [a.id for a in pick.arms] == ["n7", "n11", "n9", "n13"]
    sw = switch_world()
    edit.add_arm(sw, "n11", ir.Arm(1, {"case": 3}, []), at=2)
    edit.insert_arm(sw, "n12", ir.Arm(1, {"case": 0}, []))
    assert [(a.sub, a.args.get("case")) for a in under(sw, "call_0", "n11").arms] == [
        (1, 0),
        (1, 1),
        (1, 2),
        (1, 3),
        (2, None),
    ]
    top = world()
    edit.delete_arm(top, "n2")  # an if loses its else, and may get one back
    edit.add_arm(top, "n0", ir.Arm(1, {}, [edit.insn("wait", 3)]))
    assert under(top, "base_0", "n0").arms[0].body[0].args == {"frames": 3}


@pytest.mark.parametrize(
    ("block", "arm", "at", "match"),
    [
        ("n0", ir.Arm(1, {}, []), None, "at most one arm"),
        ("n0", ir.Arm(2, {}, []), 0, "at most one arm"),
        ("n11", ir.Arm(1, {"case": 1}, []), 3, "cases and one default, last"),
        ("n11", ir.Arm(2, {}, []), 0, "cases and one default, last"),
        ("n11", ir.Arm(3, {}, []), 0, "cases and one default, last"),
        ("n11", ir.Arm(1, {}, []), 0, "takes \\['case'\\]"),
        ("n11", ir.Arm(1, {"case": 300}, []), 0, "does not fit"),
        ("n11", ir.Arm(1, {"case": 1}, []), 9, "out of range"),
        ("n6", ir.Arm(1, {"case": 1}, []), 0, "takes \\['weight'\\]"),
        ("n6", ir.Arm(2, {"weight": 1}, []), 0, "arms, each of kind 1"),
        ("n1", ir.Arm(1, {}, []), 0, "is no block"),
        ("zz", ir.Arm(1, {}, []), 0, "no node zz"),
    ],
)
def test_add_arm_errors(block, arm, at, match):
    p = switch_world()
    before = [shape(r.body) for r in p.routines()]
    with pytest.raises(EditError, match=match):
        edit.add_arm(p, block, arm, at)
    assert [shape(r.body) for r in p.routines()] == before


def test_add_arm_too_many():
    p = world()
    for _ in range(11):
        edit.add_arm(p, "n6", ir.Arm(1, {"weight": 0}, []))
    assert len(under(p, "call_0", "n6").arms) == 13
    with pytest.raises(EditError, match="has no marker 14"):
        edit.add_arm(p, "n6", ir.Arm(1, {"weight": 0}, []))
    top = p.item("base_0")
    with pytest.raises(EditError, match="is no arm"):
        edit.insert_arm(p, "n0", ir.Arm(1, {}, []))
    assert top is p.item("base_0")


def test_delete_arm():
    p = switch_world()
    sw = under(p, "call_0", "n11")
    edit.delete_arm(p, "n14")
    assert [a.id for a in sw.arms] == ["n12", "n13"] and sw.pc is None and sw.origin is None
    pick = under(p, "call_0", "n6")
    edit.delete_arm(p, "n7")
    assert [a.id for a in pick.arms] == ["n9"] and pick.pc is None and pick.origin == "n6"
    with pytest.raises(EditError, match="needs arms"):
        edit.delete_arm(p, "n9")
    for nid, match in (("n6", "is no arm"), ("zz", "no node zz")):
        with pytest.raises(EditError, match=match):
            edit.delete_arm(p, nid)
    assert [a.id for a in pick.arms] == ["n9"]


def test_reorder_arms():
    p = switch_world()
    sw = under(p, "call_0", "n11")
    edit.reorder_arms(p, "n11", ["n13", "n12", "n14"])
    assert [a.id for a in sw.arms] == ["n13", "n12", "n14"] and sw.pc is None
    pick = under(p, "call_0", "n6")
    edit.reorder_arms(p, "n6", ["n9", "n7"])
    assert [a.id for a in pick.arms] == ["n9", "n7"] and pick.origin == "n6"
    for nid, order, match in (
        ("n11", ["n13", "n12"], "has the arms"),
        ("n11", ["n13", "n12", "n12"], "has the arms"),
        ("n11", ["n13", "n14", "n12"], "cases and one default, last"),
        ("n6", ["n9"], "has the arms"),
        ("n1", [], "is no block"),
        ("zz", [], "no node zz"),
    ):
        with pytest.raises(EditError, match=match):
            edit.reorder_arms(p, nid, order)
    assert [a.id for a in sw.arms] == ["n13", "n12", "n14"]


def test_put_in():
    p = world()
    edit.put_in(p, "call_0", [edit.insn("wait", 1)])
    edit.put_in(p, "n0", [edit.insn("wait", 2)], 0)  # a block's head
    edit.put_in(p, "n9", [edit.insn("wait", 3)])  # an arm
    assert [n.args.get("frames") for n in p.item("call_0").body] == [None, None, 1]
    assert [n.args.get("frames") for n in under(p, "base_0", "n0").head] == [2, None]
    assert [n.id for n in under(p, "call_0", "n9").body] == ["n13"]
    for target, at, match in (
        ("zz", None, "no routine or node zz"),
        ("n6", 0, "has no head"),
        ("n1", 0, "no place inside"),
        ("call_0", 9, "out of range"),
        ("call_0", -1, "out of range"),
    ):
        with pytest.raises(EditError, match=match):
            edit.put_in(p, target, [edit.insn("wait", 9)], at)
    assert len(p.item("call_0").body) == 3


def test_build():
    assert edit.build("enter", dict(main=1, sub=2, gate=3)) == edit.insn("enter", 1, 2, 3)
    assert edit.build("set_target", dict(kind=3, a=1, section=2)) == edit.insn(
        "set_target", 3, 1, 2
    )
    block = edit.build(
        "if_latch", {}, head=[edit.insn("wait", 1)], arms=[ir.Arm(1, {}, [edit.insn("wait", 2)])]
    )
    assert block == edit.if_("if_latch", then=[edit.insn("wait", 1)], else_=[edit.insn("wait", 2)])
    pick = edit.build("random", {}, arms=[ir.Arm(1, {"weight": 32}, [])])
    assert pick == edit.random([(32, [])])
    for name, args, kw, match in (
        ("nope", {}, {}, "no opcode"),
        ("enter", {"main": 1}, {}, "takes"),
        ("enter", dict(main=1, sub=2, gate=300), {}, "does not fit"),
        ("enter", dict(main=1, sub=2, gate=3), {"head": [edit.insn("wait", 1)]}, "is no block"),
        ("random", {}, {"head": [edit.insn("wait", 1)]}, "has no head"),
        ("random", {}, {}, "needs arms"),
        ("if_latch", {}, {"arms": [ir.Arm(1, {}, []), ir.Arm(1, {}, [])]}, "at most one arm"),
        ("switch_hp", {}, {"arms": [ir.Arm(1, {}, [])]}, "takes"),
    ):
        with pytest.raises(EditError, match=match):
            edit.build(name, args, **kw)


def test_copy():
    p = world()
    top = p.item("base_0").body[0]
    (twin,) = edit.copy([top])
    assert twin == top and twin is not top and twin.arms[0] is not top.arms[0]
    twin.head[0].args["sub"] = 7
    assert top.head[0].args["sub"] == 1 and twin.close == top.close
    spare = edit.copy_arm(top.arms[0])
    assert spare == top.arms[0] and spare.body[0] is not top.arms[0].body[0]


def test_ids_by_offset():
    img = asm.assemble(world())
    by = edit.ids_by_offset(img)
    assert len(by) == len(img.nodes) == 11 and by[0] == "n0"
    assert {nid: (off, 0) for off, nid in by.items()}.keys() == img.nodes.keys()
    assert all(img.nodes[nid][0] == off for off, nid in by.items())


def test_resolve_share():
    p = pair(["a"], ["b"])
    edit.add(p, ir.Routine("plain", [edit.insn("end", 1)]))
    q = edit.resolve(p, share=True)
    assert q.item("plain") is p.item("plain") and q.item("h0") is p.item("h0")
    assert q.item("base_l") is p.item("base_l") and q.item("a") is p.item("a")
    assert q.item("base_0") is not p.item("base_0")  # it has a label
    assert q.item("c0") is not p.item("c0") and p.item("c0").slots == ["a"]  # and grew these
    other = edit.resolve(p)
    assert other.item("plain") is not p.item("plain") and other.item("h0") is not p.item("h0")
    assert asm.assemble(other).data == asm.assemble(q).data


def test_taken():
    p = world()
    assert edit.taken(p) == {"base_0", "call_0", "base_l", "call_l", "h0"}


# ---- items


def test_add():
    p = world()
    r = ir.Routine("spin", [edit.insn("end", 1)])
    edit.add(p, r)
    assert p.items[-1] is r and r.va is None and r.body[0].id == "n11"
    edit.add(p, ir.Table("t2", []))
    edit.add(p, ir.Blob(b"12"))
    edit.add(p, ir.Data("d", b"1234", {"d": 0, "e": 2}))
    for dup in (ir.Table("spin", []), ir.Data("x", b"", {"e": 0}), ir.Header("h0", [])):
        with pytest.raises(EditError, match="names something already"):
            edit.add(p, dup)
    with pytest.raises(EditError, match="no address"):
        edit.add(p, ir.Table("t3", [], va=BASE))
    assert len(p.items) == 9


def test_set_slot():
    p = world()
    edit.set_slot(p, "call_l", 1, "base_0")
    edit.set_slot(p, "call_l", 2, ir.External(5))
    edit.set_slot(p, "call_l", 0, None)
    assert p.item("call_l").slots == [None, "base_0", ir.External(5)]
    with pytest.raises(EditError, match="leave a gap"):
        edit.set_slot(p, "call_l", 4, None)
    with pytest.raises(EditError, match="leave a gap"):
        edit.set_slot(p, "call_l", -1, None)
    with pytest.raises(EditError, match="no table named h0"):
        edit.set_slot(p, "h0", 0, None)


def test_set_slot_limit():
    p = world()
    for i in range(2, 256):
        edit.set_slot(p, "call_l", i, "call_0")
    assert len(p.item("call_l").slots) == 256
    with pytest.raises(EditError, match="more than 256"):
        edit.set_slot(p, "call_l", 256, "call_0")
    edit.set_slot(p, "call_l", 255, None)  # a slot that is there is no growth


def test_fork_table():
    p = world()
    twin = ir.Header("h1", ["base_l", "call_l", *[None] * 13], (1,))
    edit.add(p, twin)
    name = edit.fork_table(p, "h0", 1)
    assert name == "h0_call_l" and p.item(name).va is None
    assert p.item(name).slots == ["call_0", None] and p.item(name) is not p.item("call_l")
    assert p.items[4].fields[1] == name and twin.fields[1] == "call_l"
    edit.set_slot(p, name, 2, "call_0")
    assert p.item("call_l").slots == ["call_0", None]
    assert edit.fork_table(p, "h0", 1) == "h0_call_l2"
    for field in (2, 99):
        with pytest.raises(EditError, match="no list in field"):
            edit.fork_table(p, "h0", field)


def test_add_deep():
    p = world()
    edit.add(p, ir.Table("deep", ["call_0"]))
    assert edit.add_deep(p, "h0", "deep") == 0
    assert p.items[4].fields[15] == "deep" and len(p.items[4].fields) == 16
    edit.add(p, ir.Table("deep2", []))
    assert edit.add_deep(p, "h0", "deep2") == 1
    with pytest.raises(EditError, match="no table named zz"):
        edit.add_deep(p, "h0", "zz")
    p.items[4].fields = ["base_l"]
    with pytest.raises(EditError, match="fewer than the fixed 15"):
        edit.add_deep(p, "h0", "deep")


# ---- labels


def pair(call0, call1, label="x"):
    """Two headers over one base routine that calls `@label`; their call lists as given."""
    fields = [None] * 13
    return ir.Program(
        1,
        BASE,
        [
            ir.Routine("base_0", [ins("call", routine=ir.Sym(label)), ins("end", kind=0)]),
            ir.Routine("a", [ins("end", kind=1)]),
            ir.Routine("b", [ins("end", kind=1)]),
            ir.Routine("x", [ins("end", kind=1)]),
            ir.Table("base_l", ["base_0"]),
            ir.Table("c0", call0),
            ir.Table("c1", call1),
            ir.Header("h0", ["base_l", "c0", *fields]),
            ir.Header("h1", ["base_l", "c1", *fields]),
        ],
    )


def test_resolve_appends():
    p = world()
    edit.add(p, ir.Routine("spin", [edit.insn("end", 1)]))
    edit.put(p, "base_0", [edit.insn("call", ir.Sym("spin")), edit.insn("call", ir.Sym("spin"))])
    q = edit.resolve(p)
    assert [n.args["routine"] for n in q.items[0].body[:2]] == [2, 2]
    assert q.item("call_l").slots == ["call_0", None, "spin"]
    assert p.item("call_l").slots == ["call_0", None]  # the program as it was
    assert p.items[0].body[0].args["routine"] == ir.Sym("spin")
    assert q.items[0].body[0].id == p.items[0].body[0].id and q.items[0].body[2].pc is not None
    assert [n.id for n in q.nodes()] == [n.id for n in p.nodes()]
    assert q.items[0].body is not p.items[0].body and q.items[2] is not p.items[2]


def test_resolve_finds_label_in_list():
    p = world()
    edit.put(p, "base_0", [edit.insn("call", ir.Sym("call_0"))])
    q = edit.resolve(p)
    assert q.items[0].body[0].args["routine"] == 0 and q.item("call_l").slots == ["call_0", None]


def test_resolve_every_operand():
    p = world()
    edit.add(p, ir.Routine("spin", [edit.insn("end", 1)]))
    edit.add(p, ir.Table("deep", ["call_0"]))
    k = edit.add_deep(p, "h0", "deep")
    edit.put(
        p,
        "base_0",
        [
            edit.insn("call_deep", k, ir.Sym("spin")),
            edit.insn("goto", ir.Sym("base_0")),
        ],
    )
    q = edit.resolve(p)
    assert q.item("deep").slots == ["call_0", "spin"]
    assert [n.args for n in q.items[0].body[:2]] == [{"table": 0, "routine": 1}, {"routine": 0}]
    assert q.item("base_l").slots == ["base_0"]


def test_resolve_shared_table():
    shared = pair(["a"], ["a"])
    shared.item("h1").fields[1] = "c0"
    q = edit.resolve(shared)
    assert q.items[0].body[0].args["routine"] == 1 and q.item("c0").slots == ["a", "x"]
    assert q.item("c1").slots == ["a"]  # nothing reaches it any more


def test_resolve_agreeing_tables():
    q = edit.resolve(pair(["a", "x"], ["b", "x"]))
    assert q.items[0].body[0].args["routine"] == 1
    assert (q.item("c0").slots, q.item("c1").slots) == (["a", "x"], ["b", "x"])
    q = edit.resolve(pair(["a"], ["b"]))  # each appends, at the same place
    assert q.item("c0").slots == ["a", "x"] and q.item("c1").slots == ["b", "x"]


def test_resolve_conflict():
    with pytest.raises(EditError, match=r"'x' is slot 1 under h0 and 2 under h1"):
        edit.resolve(pair(["a"], ["a", "b"]))
    with pytest.raises(EditError, match=r"'x' is slot 0 under h0 and 2 under h1"):
        edit.resolve(pair(["x"], ["a", "b"]))


def test_resolve_errors():
    lone = pair(["a"], ["a"])
    lone.items[0].body.insert(0, ins("call", routine=0))
    lone.items.insert(1, ir.Routine("lost", [ins("call", routine=ir.Sym("a")), ins("end", kind=0)]))
    with pytest.raises(EditError, match="lost: the label 'a' is in a routine no header reaches"):
        edit.resolve(lone)
    untagged = pair(["a"], ["a"])
    untagged.items[0].body.insert(0, ins("enter", main=0, sub=ir.Sym("x"), gate=0))
    with pytest.raises(EditError, match="base_0: sub is the label 'x' and names no list"):
        edit.resolve(untagged)
    in_arm = pair(["a"], ["a"])
    in_arm.items[0].body.insert(0, blk("switch_hp", arms=[arm(1, case=ir.Sym("x"))]))
    with pytest.raises(EditError, match="case is the label"):
        edit.resolve(in_arm)
    with pytest.raises(EditError, match="nothing is named nope"):
        edit.resolve(pair(["a"], ["a"], "nope"))
    bare = pair(["a"], ["a"])
    bare.item("h1").fields[1] = None
    with pytest.raises(EditError, match="h1 has no call list for 'x'"):
        edit.resolve(bare)
    short = pair(["a"], ["a"])
    short.item("h1").fields = ["base_l"]
    with pytest.raises(EditError, match="h1 has no call list"):
        edit.resolve(short)


def test_resolve_list_limit():
    full = pair(["a"] * 256, ["a"] * 256)
    with pytest.raises(EditError, match="c0 cannot hold more than 256"):
        edit.resolve(full)
    full.item("c0").slots[255] = "x"
    full.item("c1").slots[255] = "x"
    assert edit.resolve(full).items[0].body[0].args["routine"] == 255


# ---- checks


def codes(p, img=None):
    """The (level, code) of each finding of the resolved program."""
    return [(f.level, f.code) for f in check.check(edit.resolve(p), img)]


def found(p):
    return {f.code for f in check.check(edit.resolve(p))}


def test_native_world_is_clean():
    p = world()
    assert check.check(p) == []
    top, pick = p.items[0].body[0], p.items[1].body[0]
    edit.insert(p, top.head[0].id, [edit.insn("wait", 1)])  # a touched block: still fine
    edit.insert(p, pick.arms[0].id, [edit.insn("wait", 1)])
    assert check.check(p) == []


def test_unresolved():
    p = world()
    edit.add(p, ir.Routine("spin", [edit.insn("end", 1)]))
    edit.put(p, "base_0", [edit.insn("call", ir.Sym("spin"))])
    (f,) = check.check(p)
    assert (f.level, f.code, f.node, f.item) == ("error", "UNRESOLVED", "n12", "base_0")
    assert "label 'spin'" in f.text
    assert check.check(edit.resolve(p)) == []


def test_random_weights():
    p = world()
    bad = blk("random", arms=[arm(1, weight=10), arm(1, weight=10)])
    edit.put(p, "base_0", [bad])
    assert ("error", "RANDOM_WEIGHTS") in codes(p)
    q = world()
    edit.put(q, "base_0", [edit.random([(10, []), (10, [])])])
    assert codes(q) == []
    pick = q.items[1].body[0]  # a native random that gets a branch more
    edit.insert(q, pick.arms[1].id, [edit.insn("wait", 1)])
    assert codes(q) == []
    pick.arms.append(ir.Arm(1, {"weight": 5}, [], "n99"))
    assert codes(q) == [("error", "RANDOM_WEIGHTS")]


def test_random_nested():
    inner = edit.random([(8, [])], shift=3)
    p = world()
    edit.put(p, "base_0", [edit.random([(16, [inner])])])
    assert {"RANDOM_NESTED", "SKIP_DISAGREES"} <= found(p)  # and the game's scan agrees
    q = world()
    edit.put(
        q, "base_0", [edit.random([(16, [edit.if_("if_latch", then=[edit.clone([inner])[0]])])])]
    )
    assert "RANDOM_NESTED" in found(q)
    ok = world()
    edit.put(
        ok, "base_0", [edit.random([(16, [edit.random([(8, [])])])]), edit.random([], shift=3)]
    )
    assert codes(ok) == []  # a random in a random is skipped whole


def test_skip_short():
    p = world()
    edit.put(p, "base_0", [edit.switch("switch_enemy_var", cases=[(4, [])])])
    assert found(p) == {"SKIP_SHORT", "SKIP_DISAGREES"}
    q = world()
    edit.put(q, "base_0", [edit.switch("switch_hp", cases=[(4, [])])])
    assert codes(q) == []


@pytest.mark.parametrize(("value", "code"), [(0, "SKIP_STALL"), (0x95, "SKIP_SENTINEL")])
def test_skip_stops(value, code):
    p = world()  # the first case value is where the game's scan lands, and reads as an opcode
    edit.put(p, "base_0", [edit.switch("switch_enemy_var", cases=[(value, [])])])
    assert code in found(p)


def test_skip_guard():
    def nops(n):
        p = world()
        edit.put(p, "base_0", [edit.if_("if_latch", then=[edit.insn("nop")] * n)])
        return found(p)

    assert nops(1000) == {"SKIP_GUARD"}
    assert nops(999) == set()  # the marker is the 1000th step


def test_repeat():
    p = world()
    edit.put(p, "base_0", [edit.repeat(0, [edit.insn("wait", 1)])])
    assert codes(p) == [("error", "REPEAT_COUNT")]
    nested = world()
    edit.put(nested, "base_0", [edit.repeat(2, [edit.repeat(2, [])])])
    assert codes(nested) == [("error", "REPEAT_NESTED")]
    around = world()
    inner = edit.repeat(2, [])
    edit.put(around, "base_0", [edit.repeat(2, [edit.if_("if_latch", then=[inner])])])
    assert codes(around) == [("error", "REPEAT_NESTED")]
    ok = world()
    edit.put(ok, "base_0", [edit.repeat(2, []), edit.repeat(1, [edit.insn("wait", 1)])])
    assert codes(ok) == []


def test_ordered():
    def switch(*cases, name="switch_species"):
        p = world()
        edit.put(p, "base_0", [edit.switch(name, cases=[(c, []) for c in cases])])
        return codes(p)

    assert switch(3, 5, 9) == []
    assert switch(5, 3) == [("error", "ORDERED")]
    assert switch(3, 3) == [("error", "ORDERED")]
    assert switch(5, 3, name="switch_hp") == []  # only some switches stop at the first case above


def test_native_equal_cases_stay():
    sw = blk("switch_species", arms=[arm(1, case=6), arm(1, case=6)])
    p = native(ir.Program(1, BASE, [ir.Routine("r", [sw, ins("end", kind=0)])]))
    assert check.check(p) == []
    edit.insert(p, sw.arms[1].id, [edit.insn("wait", 1)])  # a touched block with a pair as it was
    assert check.check(p) == []
    edit.insert(p, sw.arms[1].id, [edit.insn("wait", 1)], after=True)
    p.items[0].body[0].arms.append(ir.Arm(1, {"case": 6}, [], "n99"))
    assert [f.code for f in check.check(p)] == ["ORDERED"]


def test_index_checks():
    def with_call(index, p=None):
        p = p or world()
        edit.put(p, "base_0", [edit.insn("call", index)])
        return [(f.level, f.code) for f in check.check(p)]

    assert with_call(0) == []
    assert with_call(1) == [("warning", "EMPTY_SLOT")]
    assert with_call(2) == [("error", "INDEX_PAST_LIST")]
    bare = world()
    bare.item("h0").fields[1] = None
    assert with_call(0, bare) == [("error", "INDEX_PAST_LIST")]
    edit.add(bare, ir.Routine("orphan", [edit.insn("end", 1)]))
    edit.put(bare, "orphan", [edit.insn("call", 9)])
    assert [f.item for f in check.check(bare)] == ["base_0"]  # nothing runs the orphan


def test_list_too_long():
    p = world()
    p.item("call_l").slots += ["call_0"] * 255
    (f,) = check.check(p)
    assert (f.level, f.code, f.item) == ("error", "LIST_TOO_LONG", "call_l")
    p.item("call_l").slots.pop()
    assert check.check(p) == []


def test_decode():
    p = world()
    edit.add(p, ir.Routine("loose", [edit.insn("wait", 1)]))  # falls off its end
    edit.set_slot(p, "call_l", 2, "loose")
    (f,) = check.check(p)
    assert (f.code, f.item) == ("DECODE", "loose") and "does not decode" in f.text
    q = world()
    edit.add(q, ir.Routine("dead", [edit.insn("end", 1), edit.insn("wait", 1)]))
    edit.set_slot(q, "call_l", 2, "dead")
    (f,) = check.check(q)
    assert (f.code, f.item, f.node) == ("DECODE", "dead", "n12")
    assert f.text == "dead decodes to nothing, not wait 1"
    ok = world()
    edit.add(ok, ir.Routine("fine", [edit.insn("end", 1)]))
    edit.set_slot(ok, "call_l", 2, "fine")
    assert check.check(ok) == []


def test_check_takes_an_image():
    p = world()
    edit.put(p, "base_0", [edit.insn("wait", 5)])
    img = asm.assemble(p, MOVED)
    assert check.check(p, img) == []
    tree = check.decoded(img, "base_0")
    assert [n.op.name for n in tree] == ["wait", "if_latch", "call", "end"]
    assert tree[0].args == {"frames": 5} and tree[1].arms[0].sub == 1


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


def plain(nodes):
    """A tree as nested tuples, without ids and addresses."""
    return [
        (n.op.name, sorted(n.args.items()))
        if isinstance(n, ir.Insn)
        else (
            n.op.name,
            sorted(n.args.items()),
            plain(n.head),
            [(a.sub, sorted(a.args.items()), plain(a.body)) for a in n.arms],
        )
        for n in nodes
    ]


def flat(tree):
    """The nodes of a `plain` tree in order, a block as its opener."""
    for n in tree:
        if len(n) == 2:
            yield n
        else:
            yield n[:2]
            yield from flat(n[2])
            for _, _, body in n[3]:
                yield from flat(body)


def hub(p):
    """The `enter 0 1 0` of em75's base routine 0, where it picks the hub move."""
    (n,) = [
        n
        for n in ir.walk(p.item("base_0").body)
        if isinstance(n, ir.Insn)
        and n.op.name == "enter"
        and n.args == {"main": 0, "sub": 1, "gate": 0}
    ]
    return n


def header_of(img, p, name):
    """A header as the moved image has it, its lists read from the tables' words. (The floats
    behind a header are not carried, so `Header.read` would take the next items for fields.)"""
    data = img.at(MOVED)

    def u32(off):
        return struct.unpack_from("<I", data, off)[0]

    h = p.item(name)
    tables = p.tables()
    fields = tuple(u32(img.items[name][0] + 4 * i) for i in range(len(h.fields)))
    words = {
        f: tuple(u32(img.items[t][0] + 4 * i) for i in range(len(tables[t].slots)))
        for f, t in enumerate(h.fields)
        if isinstance(t, str)
    }
    lists = {f: ws for f, ws in words.items() if slot(f).kind != "data"}
    return script.Header(MOVED + img.items[name][0], fields, lists, words)


def test_game_replace_hub(rows):
    sc, ps = rows[75]
    p = lift.lift(sc, 75)
    before = plain(p.item("base_0").body)
    native = asm.assemble(p).data
    at = hub(p).pc - p.base
    edit.replace(p, hub(p).id, edit.insn("enter", 3, 0, 0))
    img = asm.assemble(p, MOVED)
    assert check.check(p, img) == []
    assert [i for i, (a, b) in enumerate(zip(native, img.at(p.base), strict=True)) if a != b] == [
        at + 1,
        at + 2,
    ]
    got = plain(check.decoded(img, "base_0"))
    assert got == plain(p.item("base_0").body) and got != before
    changed = [(a, b) for a, b in zip(flat(before), flat(got), strict=True) if a != b]
    assert changed == [
        (
            ("enter", [("gate", 0), ("main", 0), ("sub", 1)]),
            ("enter", [("gate", 0), ("main", 3), ("sub", 0)]),
        )
    ]


def test_game_new_routine_by_label(rows):
    sc, ps = rows[75]
    p = lift.lift(sc, 75)
    edit.add(p, ir.Routine("it", [edit.insn("enter", 3, 0, 0), edit.insn("end", 1)]))
    edit.put(p, "base_0", [edit.insn("call", ir.Sym("it"))])
    assert {f.code for f in check.check(p)} == {"UNRESOLVED"}
    q = edit.resolve(p)
    slots = q.item("call_l").slots
    assert len(slots) == 23 and slots[-1] == "it" and len(p.item("call_l").slots) == 22
    assert q.item("base_0").body[0].args == {"routine": 22}
    img = asm.assemble(q)
    assert check.check(q, img) == []
    native = q.end - q.base
    assert img.items["call_l"][0] >= native and img.items["it"][0] >= native  # it grew, so it moved
    h = img.items["h0"][0]
    assert word(img, "h0", 1) == q.base + img.items["call_l"][0] and h < native
    assert word(img, "call_l", 22) == q.base + img.items["it"][0]
    space = Space([Image(img.at(MOVED), MOVED), sc.ovl])
    want, got = sc.listing(p.headers()[0].va), script.disassemble(space.u8, header_of(img, q, "h0"))
    assert len(got.routines) == len(want.routines) + 1 and all(
        r.clean for r in got.routines.values()
    )
    assert MOVED + img.items["it"][0] in got.routines


def test_game_every_routine_grows(rows):
    sc, ps = rows[75]
    p = lift.lift(sc, 75)
    for r in p.routines():
        edit.put(p, r.name, [edit.insn("nop")])
    img = asm.assemble(p)
    moved = [r.name for r in p.routines() if img.items[r.name][0] >= p.end - p.base]
    assert 0 < len(moved) < len(p.routines())  # a zero tail takes the byte, or the routine moves
    assert check.check(p, img) == []
    space = Space([Image(img.at(MOVED), MOVED), sc.ovl])
    want, got = sc.listing(p.headers()[0].va), script.disassemble(space.u8, header_of(img, p, "h0"))
    assert len(got.routines) == len(want.routines) and all(r.clean for r in got.routines.values())
    assert got.stats().instructions == want.stats().instructions + len(want.routines)


def test_game_rows_are_clean(rows):
    for em, (_, ps) in rows.items():
        for row, p in ps.items():
            assert check.check(p) == [], (em, row)


def test_game_check_time(rows):
    p = rows[1][1][1]
    start = time.perf_counter()
    assert check.check(p) == []
    assert time.perf_counter() - start < 30
