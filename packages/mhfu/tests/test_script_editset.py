# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import hashlib
import time

import pytest
from mhfu.em import script
from mhfu.em.script import asm, check, edit, editset, ir, lift
from mhfu.em.script.edit import EditError
from mhfu.em.script.editset import (
    Anchor,
    DocError,
    EditSet,
    Insert,
    ListEdit,
    apply,
    equal,
    revert_insert,
    revert_node,
    revert_routine,
    set_args,
    set_arms,
)
from mhfu.files import EM_SPECIES

BASE = 0x0010_0000


# ---- a native program by hand


def enter(main, sub):
    return edit.insn("enter", main, sub, 0)


def native(p):
    """Give a program the addresses, tails and ids a lift has: `{row}:{pc:08X}`."""
    for k, n in enumerate(p.nodes()):
        n.id = f"t{k}"
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
    for n in p.nodes():
        n.id = f"{p.row}:{n.pc:08X}"
    p.end = BASE + len(img.data)
    p.sha1 = hashlib.sha1(asm.assemble(p).data).hexdigest()
    return p


def world(row=1):
    """Four routines and two headers that share the lists; if, random, switch and repeat."""
    top = edit.if_("if_latch", then=[enter(0, 1)], else_=[enter(0, 2)])
    pick = edit.random([(16, [enter(1, 0)]), (8, [enter(1, 1)])])
    sw = edit.switch(
        "switch_hp", cases=[(1, [enter(2, 0)]), (2, [enter(2, 1)])], default=[enter(2, 2)]
    )
    loop = edit.repeat(3, [enter(3, 0)])
    fields = ["base_l", "call_l", *[None] * 13]
    return native(
        ir.Program(
            row,
            BASE,
            [
                ir.Routine("base_0", [top, edit.insn("call", 0), edit.insn("end", 0)]),
                ir.Routine("base_1", [enter(5, 0), edit.insn("goto", 0)]),
                ir.Routine("call_0", [pick, edit.insn("end", 1)]),
                ir.Routine("call_1", [sw, loop, enter(6, 0), edit.insn("end", 1)]),
                ir.Table("base_l", ["base_0", "base_1"]),
                ir.Table("call_l", ["call_0", "call_1", None]),
                ir.Header("h0", list(fields), tuple(range(5))),
                ir.Header("h1", list(fields), tuple(range(5, 17))),
            ],
        )
    )


def ids(p, name):
    """The ids of a routine's top-level nodes."""
    return [n.id for n in p.item(name).body]


def names(nodes):
    return [n.op.name for n in nodes]


def run(p, es):
    got = apply(p, es)
    assert got.findings == []
    return got


def same(want, have, by, base):
    """A routine's tree and the one decoded from its bytes, ids mapped back by offset."""
    assert len(want) == len(have)
    for w, h in zip(want, have, strict=True):
        assert (w.op, w.args) == (h.op, h.args)
        assert by[h.pc - base] == w.id
        if isinstance(w, ir.Block):
            assert isinstance(h, ir.Block) and len(w.arms) == len(h.arms)
            same(w.head, h.head, by, base)
            for a, b in zip(w.arms, h.arms, strict=True):
                assert (a.sub, a.args) == (b.sub, b.args) and by[b.pc - base] == a.id
                same(a.body, b.body, by, base)


def roundtrip(got):
    """The program assembles, checks clean and decodes to itself, every node by its id."""
    assert got.ok
    prog = got.program
    img = asm.assemble(prog)
    assert check.check(prog, img) == []
    by = edit.ids_by_offset(img)
    for r in prog.routines():
        same(r.body, check.decoded(img, r.name), by, img.base)
    return img


def shape(nodes):
    """A tree as nested tuples of names and operands."""
    return [
        (n.op.name, sorted(n.args.items()))
        if isinstance(n, ir.Insn)
        else (
            n.op.name,
            sorted(n.args.items()),
            shape(n.head),
            [(a.sub, sorted(a.args.items()), shape(a.body)) for a in n.arms],
        )
        for n in nodes
    ]


def errors(got):
    return [f for f in got.findings if f.level == "error"]


def test_world_is_native():
    p = world()
    assert check.check(p) == []
    assert asm.assemble(p).data == asm.assemble(edit.resolve(p)).data
    assert p.item("call_1").body[0].id.startswith("1:")


# ---- the empty set, and what refuses


def test_empty():
    p = world()
    got = apply(p, EditSet.of(p))
    assert got.program is p and got.findings == [] and got.touched == () and got.ok
    assert asm.assemble(edit.resolve(got.program)).data == asm.assemble(p).data


def test_build_refused():
    p = world()
    es = EditSet.of(p)
    es.build = "0" * 40
    es.node(ids(p, "base_1")[0]).delete = True
    got = apply(p, es)
    assert got.program is p and not got.ok
    assert [(f.level, f.code) for f in got.findings] == [("error", "DOC_BUILD")]
    es.build, es.row = p.sha1, 2
    assert [f.code for f in apply(p, es).findings] == ["DOC_BUILD"]


def test_names_and_notes_change_nothing():
    p = world()
    es = EditSet.of(p)
    es.names["base_0"], es.notes[ids(p, "base_0")[0]] = "Top", "the hub"
    assert es.is_empty() and apply(p, es).program is p


# ---- operands


def test_args():
    p = world()
    nid = p.item("base_0").body[0].head[0].id
    es = EditSet.of(p)
    set_args(es, p, nid, main=3, sub=0)
    got = run(p, es)
    n = got.program.item("base_0").body[0].head[0]
    assert (n.id, n.pc, n.origin, n.args) == (nid, None, nid, {"main": 3, "sub": 0, "gate": 0})
    img = roundtrip(got)
    assert nid in img.nodes and got.touched == ("base_0",)
    assert p.item("base_0").body[0].head[0].args["main"] == 0  # the lift is as it was


def test_args_of_a_block_and_an_arm():
    p = world()
    loop = p.item("call_1").body[1]
    pick = p.item("call_0").body[0]
    es = EditSet.of(p)
    set_args(es, p, loop.id, count=5)
    set_args(es, p, pick.arms[0].id, weight=8)
    set_args(es, p, pick.arms[2].id, weight=16)
    got = run(p, es)
    roundtrip(got)
    assert got.program.item("call_1").body[1].args == {"count": 5}
    assert [a.args["weight"] for a in got.program.item("call_0").body[0].arms] == [8, 8, 16]


def test_args_kind_changes_layout():
    q = native(
        ir.Program(
            1, BASE, [ir.Routine("base_0", [edit.insn("set_target", 1, 2, 3), edit.insn("end", 0)])]
        )
    )
    nid = q.item("base_0").body[0].id
    es = EditSet.of(q)
    set_args(es, q, nid, kind=3, section=0x101)
    assert es.nodes[nid].args == {"kind": 3, "section": 0x101}  # a is the game's
    got = run(q, es)
    assert got.program.item("base_0").body[0].args == {"kind": 3, "a": 2, "section": 0x101}
    roundtrip(got)
    set_args(es, q, nid, kind=1)
    assert es.nodes == {}  # the stale section went with the layout


def test_args_bad_operand():
    p = world()
    es = EditSet.of(p)
    nid = ids(p, "base_1")[0]
    es.node(nid).args = {"volume": 3}
    got = apply(p, es)
    assert [(f.code, f.node, f.item) for f in got.findings] == [("DOC_CONFLICT", nid, "base_1")]
    assert got.touched == () and got.program.item("base_1") is p.item("base_1")


def test_args_wrong_total_is_the_checks():
    p = world()
    pick = p.item("call_0").body[0]
    es = EditSet.of(p)
    set_args(es, p, pick.arms[0].id, weight=20)
    got = run(p, es)
    assert [f.code for f in check.check(got.program)] == ["RANDOM_WEIGHTS"]


# ---- arm order


def test_arm_order():
    p = world()
    sw, pick = p.item("call_1").body[0], p.item("call_0").body[0]
    es = EditSet.of(p)
    set_arms(es, p, sw.id, [sw.arms[1].id, sw.arms[0].id, sw.arms[2].id])
    set_arms(es, p, pick.id, [a.id for a in reversed(pick.arms)])
    got = run(p, es)
    roundtrip(got)
    out = got.program
    assert [a.args for a in out.item("call_1").body[0].arms[:2]] == [{"case": 2}, {"case": 1}]
    assert [a.args["weight"] for a in out.item("call_0").body[0].arms] == [8, 8, 16]
    set_arms(es, p, pick.id, [a.id for a in pick.arms])  # the game's order is no edit
    assert pick.id not in es.nodes


def test_arm_order_wrong():
    p = world()
    sw = p.item("call_1").body[0]
    for order in ([sw.arms[0].id], [sw.arms[2].id, sw.arms[0].id, sw.arms[1].id]):
        es = EditSet.of(p)
        es.node(sw.id).arms = order
        assert [f.code for f in apply(p, es).findings] == ["DOC_CONFLICT"], order


# ---- moves


def test_move_beside():
    p = world()
    a, b, c = (n.id for n in p.item("base_0").body)
    es = EditSet.of(p)
    es.node(b).move = Anchor("before", a)
    got = run(p, es)
    roundtrip(got)
    assert ids(got.program, "base_0") == [b, a, c]
    assert got.program.item("base_0").body[0].origin == b


def test_move_into():
    p = world()
    top, call, _ = p.item("base_0").body
    es = EditSet.of(p)
    es.node(call.id).move = Anchor("into", top.arms[0].id)  # the else arm, at its end
    es.node(top.head[0].id).move = Anchor("into", "base_1", 0)
    got = run(p, es)
    roundtrip(got)
    out = got.program
    assert names(out.item("base_1").body) == ["enter", "enter", "goto"]
    assert out.item("base_1").body[0].id == top.head[0].id
    assert names(out.item("base_0").body[0].arms[0].body) == ["enter", "call"]
    assert out.item("base_0").body[0].head == []


def test_move_across_routines_and_into_head():
    p = world()
    e = p.item("base_1").body[0]
    loop = p.item("call_1").body[1]
    es = EditSet.of(p)
    es.node(e.id).move = Anchor("into", loop.id, 0)
    got = run(p, es)
    roundtrip(got)
    out = got.program
    assert out.item("call_1").body[1].head[0].id == e.id
    assert names(out.item("base_1").body) == ["goto"]
    assert got.touched == ("base_1", "call_1")


def test_move_a_block_whole():
    p = world()
    top = p.item("base_0").body[0]
    es = EditSet.of(p)
    es.node(top.id).move = Anchor("into", "base_1", 0)
    got = run(p, es)
    roundtrip(got)
    assert names(got.program.item("base_1").body) == ["if_latch", "enter", "goto"]
    assert shape(got.program.item("base_1").body[:1]) == shape([top])
    assert names(got.program.item("base_0").body) == ["call", "end"]


def test_move_depends_on_move():
    p = world()
    a, b, c, d = (n.id for n in p.item("call_1").body)
    es = EditSet.of(p)
    es.node(a).move = Anchor("after", b)  # b is moved first
    es.node(b).move = Anchor("after", c)
    got = run(p, es)
    roundtrip(got)
    assert ids(got.program, "call_1") == [c, b, a, d]


def test_move_cycle_and_inside():
    p = world()
    top, call, _ = p.item("base_0").body
    es = EditSet.of(p)
    es.node(top.id).move = Anchor("after", call.id)
    es.node(call.id).move = Anchor("after", top.id)
    assert [f.code for f in apply(p, es).findings] == ["DOC_CONFLICT"] * 2
    es = EditSet.of(p)
    es.node(top.id).move = Anchor("into", top.arms[0].id)
    got = apply(p, es)
    assert [(f.code, f.node) for f in got.findings] == [("DOC_CONFLICT", top.id)]


# ---- inserts


def test_insert_beside_and_into():
    p = world()
    a, b, c = (n.id for n in p.item("base_0").body)
    es = EditSet.of(p)
    es.inserts["u1"] = Insert(Anchor("before", a), [enter(7, 0)])
    es.inserts["u2"] = Insert(Anchor("after", b), [enter(7, 1), enter(7, 2)])
    es.inserts["u3"] = Insert(Anchor("into", "base_1", 0), [enter(7, 3)])
    es.inserts["u4"] = Insert(Anchor("into", p.item("call_1").body[1].id), [enter(7, 4)])
    got = run(p, es)
    roundtrip(got)
    out = got.program
    assert ids(out, "base_0") == ["u1.0", a, b, "u2.0", "u2.1", c]
    assert out.item("base_1").body[0].id == "u3.0"
    assert [n.id for n in out.item("call_1").body[1].head][-1] == "u4.0"
    assert out.item("base_0").body[0].pc is None and out.item("base_0").body[0].origin is None


def test_insert_block_ids_in_source_order():
    p = world()
    block = edit.if_("if_enraged", then=[enter(7, 0)], else_=[enter(7, 1), enter(7, 2)])
    es = EditSet.of(p)
    es.inserts["u1"] = Insert(Anchor("into", "call_0", 0), [block])
    got = run(p, es)
    roundtrip(got)
    blk = got.program.item("call_0").body[0]
    got_ids = [blk.id, blk.head[0].id, blk.arms[0].id, *(n.id for n in blk.arms[0].body)]
    assert got_ids == [f"u1.{i}" for i in range(5)]
    assert block.id == "" and block.head[0].id == ""  # the edit set's nodes stay as they were


def test_insert_in_an_insert_any_key_order():
    p = world()
    a = p.item("base_0").body[0].id
    es = EditSet.of(p)
    es.inserts["u1"] = Insert(Anchor("after", "u2.1"), [enter(7, 2)])  # needs u2
    es.inserts["u2"] = Insert(Anchor("after", a), [enter(7, 0), enter(7, 1)])
    es.inserts["u3"] = Insert(Anchor("before", "u1.0"), [enter(7, 3)])
    got = run(p, es)
    roundtrip(got)
    assert ids(got.program, "base_0")[:5] == [a, "u2.0", "u2.1", "u3.0", "u1.0"]


def test_insert_same_anchor_later_key_is_closer():
    p = world()
    a = p.item("base_0").body[0].id
    es = EditSet.of(p)
    es.inserts["u1"] = Insert(Anchor("after", a), [enter(7, 1)])
    es.inserts["u2"] = Insert(Anchor("after", a), [enter(7, 2)])
    es.inserts["u10"] = Insert(Anchor("before", a), [enter(7, 3)])
    got = run(p, es)
    assert ids(got.program, "base_0")[:4] == ["u10.0", a, "u2.0", "u1.0"]


def test_insert_arms():
    p = world()
    pick, sw = p.item("call_0").body[0], p.item("call_1").body[0]
    es = EditSet.of(p)
    set_args(es, p, pick.arms[2].id, weight=4)  # the empty last branch gives up 4
    es.inserts["u1"] = Insert(
        Anchor("after", pick.arms[1].id), arm=ir.Arm(1, {"weight": 4}, [enter(1, 0)])
    )
    es.inserts["u2"] = Insert(Anchor("into", sw.id, 1), arm=ir.Arm(1, {"case": 5}, [enter(1, 1)]))
    got = run(p, es)
    roundtrip(got)
    out = got.program
    arms = out.item("call_0").body[0].arms
    assert [a.args["weight"] for a in arms] == [16, 8, 4, 4]
    assert arms[2].id == "u1.0" and arms[2].body[0].id == "u1.1"
    assert [a.args.get("case") for a in out.item("call_1").body[0].arms] == [1, 5, 2, None]


def test_new_routine():
    p = world()
    a = p.item("base_0").body[0].id
    es = EditSet.of(p)
    es.routines["spin"] = [enter(2, 0), edit.insn("end", 1)]
    es.inserts["u1"] = Insert(Anchor("before", a), [edit.insn("call", ir.Sym("spin"))])
    got = run(p, es)
    roundtrip(got)
    out = got.program
    assert out.item("call_l").slots == ["call_0", "call_1", None, "spin"]
    assert out.item("base_0").body[0].args == {"routine": 3}
    assert [n.id for n in out.item("spin").body] == ["spin.0", "spin.1"]
    assert got.touched == ("base_0", "spin")
    assert p.item("call_l").slots == ["call_0", "call_1", None]  # the lift's list is as it was


def test_move_into_a_new_routine():
    p = world()
    e = p.item("base_1").body[0]
    es = EditSet.of(p)
    es.routines["spin"] = [edit.insn("end", 1)]
    es.node(e.id).move = Anchor("into", "spin", 0)
    es.inserts["u1"] = Insert(Anchor("into", "base_1", 0), [edit.insn("call", ir.Sym("spin"))])
    got = run(p, es)
    roundtrip(got)
    assert names(got.program.item("spin").body) == ["enter", "end"]
    assert got.program.item("spin").body[0].id == e.id
    assert names(got.program.item("base_1").body) == ["call", "goto"]


# ---- moves into new code


def guard(p):
    """A new `if_aware` before base_0's first node: u1.0 the block, u1.1 its enter, u1.2..."""
    top = p.item("base_0").body[0]
    es = EditSet.of(p)
    block = edit.if_("if_aware", then=[enter(7, 0)], else_=[enter(7, 1)])
    es.inserts["u1"] = Insert(Anchor("before", top.id), [block])
    return es


def test_move_beside_and_into_inserted_code():
    p = world()
    e = p.item("base_1").body[0]
    for anchor, want in (
        (Anchor("after", "u1.1"), ["enter", "enter"]),  # then: u1.1 e
        (Anchor("before", "u1.1"), ["enter", "enter"]),
        (Anchor("into", "u1.0", 0), ["enter", "enter"]),  # the head, first
        (Anchor("into", "u1.0"), ["enter", "enter"]),
    ):
        es = guard(p)
        es.node(e.id).move = anchor
        got = run(p, es)
        roundtrip(got)
        block = got.program.item("base_0").body[0]
        assert names(block.head) == want and e.id in [n.id for n in block.head]
        assert names(got.program.item("base_1").body) == ["goto"]
        assert sorted(got.touched) == ["base_0", "base_1"]
    es = guard(p)
    es.node(e.id).move = Anchor("after", "u1.1")
    assert [n.id for n in run(p, es).program.item("base_0").body[0].head] == ["u1.1", e.id]
    es.node(e.id).move = Anchor("into", "u1.2")  # the else arm, at its end
    block = run(p, es).program.item("base_0").body[0]
    assert [n.id for n in block.arms[0].body] == ["u1.3", e.id]
    es.node(e.id).move = Anchor("into", "u1.0", 0)
    assert [n.id for n in run(p, es).program.item("base_0").body[0].head] == [e.id, "u1.1"]


def test_move_a_block_into_inserted_code():
    p = world()
    pick = p.item("call_0").body[0]
    es = guard(p)
    es.node(pick.id).move = Anchor("into", "u1.2")
    got = run(p, es)
    roundtrip(got)
    inner = got.program.item("base_0").body[0].arms[0].body
    assert [n.id for n in inner] == ["u1.3", pick.id]
    assert shape(inner[1:]) == shape([pick])


def test_inserts_sit_beside_a_node_that_moved():
    p = world()
    e, g = p.item("base_1").body
    es = guard(p)
    es.node(e.id).move = Anchor("after", "u1.1")
    es.inserts["u2"] = Insert(Anchor("after", e.id), [enter(7, 2)])
    es.inserts["u3"] = Insert(Anchor("before", e.id), [enter(7, 3)])
    got = run(p, es)
    roundtrip(got)
    head = got.program.item("base_0").body[0].head
    assert [n.id for n in head] == ["u1.1", "u3.0", e.id, "u2.0"]
    assert ids(got.program, "base_1") == [g.id]


def test_moves_go_before_inserts_that_share_an_anchor():
    p = world()
    a, b, c = (n.id for n in p.item("base_0").body)
    es = EditSet.of(p)
    es.node(c).move = Anchor("after", a)
    es.inserts["u1"] = Insert(Anchor("after", a), [enter(7, 0)])
    got = run(p, es)
    assert ids(got.program, "base_0") == [a, "u1.0", c, b]


def test_a_move_that_waits_still_goes_before_the_inserts():
    p = world()
    a, b, c, d = (n.id for n in p.item("call_1").body)
    es = EditSet.of(p)
    es.node(a).move = Anchor("after", b)  # waits for b's move
    es.node(b).move = Anchor("after", c)
    es.inserts["u1"] = Insert(Anchor("after", b), [enter(7, 0)])  # waits for it too
    got = run(p, es)
    assert ids(got.program, "call_1") == [c, b, "u1.0", a, d]


def test_a_move_into_inserted_code_waits_for_a_move_it_hangs_on():
    p = world()
    top, call, end = p.item("base_0").body
    es = EditSet.of(p)
    es.inserts["u1"] = Insert(Anchor("after", call.id), [enter(7, 0)])
    es.node(call.id).move = Anchor("after", end.id)  # u1 goes with call
    es.node(top.id).move = Anchor("after", "u1.0")
    got = run(p, es)
    assert ids(got.program, "base_0") == [end.id, call.id, "u1.0", top.id]


def test_a_move_and_an_insert_that_hang_on_each_other_are_findings():
    p = world()
    e = p.item("base_1").body[0]
    es = guard(p)
    es.inserts["u1"].anchor = Anchor("after", e.id)
    es.node(e.id).move = Anchor("after", "u1.0")
    got = apply(p, es)
    assert sorted((f.code, f.node) for f in got.findings) == [
        ("DOC_CONFLICT", e.id),
        ("DOC_CONFLICT", "u1"),
    ]
    assert names(got.program.item("base_1").body) == ["enter", "goto"]


def test_a_move_into_inserted_code_that_was_lost():
    p = world()
    e = p.item("base_1").body[0]
    es = guard(p)
    es.inserts["u1"].anchor = Anchor("after", "1:DEADBEEF")
    es.node(e.id).move = Anchor("after", "u1.1")
    got = apply(p, es)
    assert sorted((f.code, f.node) for f in got.findings) == [
        ("DOC_ANCHOR", e.id),
        ("DOC_ANCHOR", "u1"),
    ]
    es = guard(p)
    es.node(e.id).move = Anchor("after", "u1.9")
    assert [(f.code, f.node) for f in apply(p, es).findings] == [("DOC_ANCHOR", e.id)]


# ---- a node of another opcode


def test_op_a_block_keeps_its_arms():
    p = world()
    top = p.item("base_0").body[0]
    es = EditSet.of(p)
    es.node(top.id).op = "if_aware"
    got = run(p, es)
    roundtrip(got)
    n = got.program.item("base_0").body[0]
    assert (n.op.name, n.id, n.pc, n.origin) == ("if_aware", top.id, None, top.id)
    assert shape(n.head) == shape(top.head) and shape(n.arms[0].body) == shape(top.arms[0].body)
    assert n.head[0] is not top.head[0] and got.touched == ("base_0",)
    assert top.op.name == "if_latch"  # the lift is as it was


def test_op_an_instruction_keeps_the_operands_it_names_alike():
    p = world()
    call = p.item("base_0").body[1]
    es = EditSet.of(p)
    es.node(call.id).op = "call_ret"  # the same operand, in another list
    got = run(p, es)
    n = got.program.item("base_0").body[1]
    assert (n.op.name, n.args, n.id, n.pc) == ("call_ret", {"routine": 0}, call.id, None)
    es.node(call.id).args = {"routine": 2}
    assert run(p, es).program.item("base_0").body[1].args == {"routine": 2}


def test_op_an_instruction_lacking_an_operand():
    p = world()
    e = p.item("base_1").body[0]
    es = EditSet.of(p)
    es.node(e.id).op = "call"
    got = apply(p, es)
    assert [(f.code, f.node) for f in got.findings] == [("DOC_CONFLICT", e.id)]
    assert "needs ['routine']" in got.findings[0].text and got.touched == ()
    es.node(e.id).args = {"routine": 1}
    got = run(p, es)
    roundtrip(got)
    assert got.program.item("base_1").body[0].args == {"routine": 1}
    es.node(e.id).args = {"routine": 300}
    assert "does not fit" in apply(p, es).findings[0].text


def test_op_a_block_whose_arms_take_other_operands():
    p = world()
    pick, sw = p.item("call_0").body[0], p.item("call_1").body[0]
    es = EditSet.of(p)
    es.node(pick.id).op = "random_shifted"
    assert "needs ['shift']" in apply(p, es).findings[0].text
    es.node(pick.id).args = {"shift": 1}
    got = run(p, es)
    roundtrip(got)
    n = got.program.item("call_0").body[0]
    assert (n.op.name, n.args) == ("random_shifted", {"shift": 1})
    assert [a.args["weight"] for a in n.arms] == [16, 8, 8]
    es = EditSet.of(p)
    es.node(sw.id).op = "switch_section"
    assert "needs ['section']" in apply(p, es).findings[0].text
    for k, a in enumerate(sw.arms[:2]):
        es.node(a.id).args = {"section": 10 + k}
    got = run(p, es)
    roundtrip(got)
    n = got.program.item("call_1").body[0]
    assert [a.args for a in n.arms] == [{"section": 10}, {"section": 11}, {}]
    assert [a.sub for a in n.arms] == [1, 1, 2]
    assert n.arms[0].origin == sw.arms[0].id


def test_op_is_refused_where_the_layout_does_not_allow_it():
    p = world()
    top, call = p.item("base_0").body[:2]
    sw = p.item("call_1").body[0]
    for nid, op, why in (
        (top.id, "switch_hp", "not alike"),
        (top.id, "enter", "not alike"),
        (call.id, "if_aware", "cannot become the block"),
        (call.id, "nope", "no opcode"),
    ):
        es = EditSet.of(p)
        es.node(nid).op = op
        got = apply(p, es)
        assert [f.code for f in got.findings] == ["DOC_CONFLICT"] and why in got.findings[0].text
        assert got.touched == ()
    es = EditSet.of(p)
    es.node(call.id).delete = True
    es.node(call.id).op = "call_ret"
    es.node(sw.arms[0].id).op = "case"
    es.node(top.id).bypass = "then"
    es.node(top.id).op = "if_aware"
    got = apply(p, es)
    assert sorted(f.node for f in got.findings) == sorted([call.id, sw.arms[0].id, top.id])
    assert {f.code for f in got.findings} == {"DOC_CONFLICT"}


def test_op_the_same_opcode_changes_nothing():
    p = world()
    top = p.item("base_0").body[0]
    es = EditSet.of(p)
    es.node(top.id).op = top.op.name
    got = run(p, es)
    assert got.touched == () and got.program.item("base_0") is p.item("base_0")


def test_op_goes_through_the_other_edits():
    p = world()
    top = p.item("base_0").body[0]
    es = EditSet.of(p)
    es.node(top.id).op = "if_aware"
    es.node(top.head[0].id).move = Anchor("into", "base_1", 0)
    es.inserts["u1"] = Insert(Anchor("into", top.id, 0), [enter(7, 0)])
    got = run(p, es)
    roundtrip(got)
    n = got.program.item("base_0").body[0]
    assert (n.op.name, [x.id for x in n.head]) == ("if_aware", ["u1.0"])


def test_op_in_data():
    p = world()
    top = p.item("base_0").body[0]
    es = EditSet.of(p)
    es.node(top.id).op = "if_aware"
    set_args(es, p, top.head[0].id, main=3)
    data = es.to_data()
    assert data["node"][top.id] == {"op": "if_aware"}
    back = EditSet.from_data(1, data)
    assert back == es and back.node(top.id).copy().op == "if_aware"
    assert back.retarget(2).nodes[top.id.replace("1:", "2:")].op == "if_aware"
    assert not es.node(top.id).is_empty()
    with pytest.raises(DocError, match="op is no text"):
        EditSet.from_data(1, {"node": {"a": {"op": 3}}})


def test_set_args_follows_the_new_opcode():
    p = world()
    e = p.item("base_1").body[0]
    es = EditSet.of(p)
    es.node(e.id).op = "call"
    set_args(es, p, e.id, routine=1, sub=4)
    assert es.nodes[e.id].args == {"routine": 1}


# ---- bypass and delete


@pytest.mark.parametrize(
    ("keep", "want"),
    [("then", ["enter", "call"]), ("else", ["enter", "call"]), (editset.NOTHING, ["call"])],
)
def test_bypass_if(keep, want):
    p = world()
    top = p.item("base_0").body[0]
    es = EditSet.of(p)
    es.node(top.id).bypass = keep
    got = run(p, es)
    roundtrip(got)
    body = got.program.item("base_0").body
    assert names(body)[:-1] == want
    if keep != editset.NOTHING:
        kept = top.head[0] if keep == "then" else top.arms[0].body[0]
        assert body[0].id == kept.id and body[0].pc is None and body[0].origin == kept.id


def test_bypass_arm_of_a_switch_and_a_repeat():
    p = world()
    sw, loop = p.item("call_1").body[:2]
    es = EditSet.of(p)
    es.node(sw.id).bypass = sw.arms[1].id
    es.node(loop.id).bypass = "then"
    got = run(p, es)
    roundtrip(got)
    body = got.program.item("call_1").body
    assert [(n.op.name, n.args["main"]) for n in body[:3]] == [
        ("enter", 2),
        ("enter", 3),
        ("enter", 6),
    ]
    assert body[0].args["sub"] == 1


def test_bypass_keeps_what_is_inserted_in_it():
    p = world()
    top = p.item("base_0").body[0]
    es = EditSet.of(p)
    es.node(top.id).bypass = "then"
    es.inserts["u1"] = Insert(Anchor("after", top.head[0].id), [enter(7, 7)])
    es.inserts["u2"] = Insert(Anchor("into", top.arms[0].id), [enter(7, 8)])
    got = apply(p, es)
    assert [(f.code, f.node) for f in got.findings] == [("DOC_ANCHOR", "u2")]  # the else is lost
    assert ids(got.program, "base_0")[:2] == [top.head[0].id, "u1.0"]


def test_delete():
    p = world()
    top, call, _ = p.item("base_0").body
    pick, sw = p.item("call_0").body[0], p.item("call_1").body[0]
    es = EditSet.of(p)
    es.node(call.id).delete = True
    es.node(top.arms[0].body[0].id).delete = True  # the else arm stays, empty
    es.node(pick.arms[0].id).delete = True
    es.node(sw.arms[2].id).delete = True
    set_args(es, p, pick.arms[1].id, weight=24)
    got = run(p, es)
    roundtrip(got)
    out = got.program
    assert names(out.item("base_0").body) == ["if_latch", "end"]
    assert out.item("base_0").body[0].arms[0].body == []
    assert [a.args["weight"] for a in out.item("call_0").body[0].arms] == [24, 8]
    assert [a.sub for a in out.item("call_1").body[0].arms] == [1, 1]


def test_delete_a_block_and_what_is_in_it():
    p = world()
    top = p.item("base_0").body[0]
    es = EditSet.of(p)
    es.node(top.id).delete = True
    es.node(top.head[0].id).delete = True
    got = run(p, es)
    roundtrip(got)
    assert names(got.program.item("base_0").body) == ["call", "end"]


# ---- lists


def test_lists_set_append():
    p = world()
    es = EditSet.of(p)
    es.lists["h0.call"] = ListEdit({2: "base_1", 1: "call_0"}, ["call_1", None])
    got = apply(p, es)
    assert [(f.level, f.code) for f in got.findings] == [("info", "SHARED_LIST")]
    roundtrip(got)
    out = got.program
    assert out.item("call_l").slots == ["call_0", "call_0", "base_1", "call_1", None]
    assert p.item("call_l").slots == ["call_0", "call_1", None]
    assert out.item("h1") is p.item("h1") and out.item("h0") is p.item("h0")


def test_lists_fork():
    p = world()
    es = EditSet.of(p)
    es.lists["h1.call"] = ListEdit({0: "call_1"}, fork=True)
    got = run(p, es)
    roundtrip(got)
    out = got.program
    assert out.item("h1").fields[1] == "h1_call_l" and out.item("h0").fields[1] == "call_l"
    assert out.item("h1_call_l").slots == ["call_1", "call_1", None]
    assert out.item("call_l") is p.item("call_l")
    assert p.item("h1").fields[1] == "call_l"


def test_lists_labels_must_agree_after_a_fork():
    p = world()
    es = EditSet.of(p)
    es.lists["h1.call"] = ListEdit(append=[None], fork=True)
    es.routines["spin"] = [edit.insn("end", 1)]
    es.inserts["u1"] = Insert(Anchor("into", "base_1", 0), [edit.insn("call", ir.Sym("spin"))])
    got = apply(p, es)
    assert [f.code for f in got.findings] == ["DOC_CONFLICT"]  # slot 3 under h0, 4 under h1


def test_lists_bad():
    p = world()
    es = EditSet.of(p)
    es.lists["h9.call"] = ListEdit({0: "call_0"})
    es.lists["h0.walk"] = ListEdit({0: "call_0"})
    es.lists["h0.call_x"] = ListEdit({0: "call_0"})
    es.lists["h0.base"] = ListEdit({0: "nowhere"})
    es.lists["h0.int_0x20"] = ListEdit({0: "base_0"})
    got = apply(p, es)
    assert [(f.code, f.item) for f in errors(got)] == [
        ("DOC_ANCHOR", "base_l"),
        ("DOC_ANCHOR", "h0.call_x"),
        ("DOC_ANCHOR", "h0.int_0x20"),
        ("DOC_ANCHOR", "h0.walk"),
        ("DOC_ANCHOR", "h9.call"),
    ]


def test_lists_gap_is_conflict():
    p = world()
    es = EditSet.of(p)
    es.lists["h0.base"] = ListEdit({5: "base_0"})
    got = apply(p, es)
    assert [(f.code, f.item) for f in errors(got)] == [("DOC_CONFLICT", "base_l")]
    assert got.program.item("base_l").slots == ["base_0", "base_1"]


# ---- anchors and conflicts


def test_anchors():
    p = world()
    a, b, c = (n.id for n in p.item("base_0").body)
    es = EditSet.of(p)
    es.inserts["u1"] = Insert(Anchor("after", "1:DEADBEEF"), [enter(7, 0)])
    es.inserts["u2"] = Insert(Anchor("after", b), [enter(7, 1)])
    es.inserts["u3"] = Insert(Anchor("after", "u9.0"), [enter(7, 2)])
    es.inserts["u4"] = Insert(Anchor("into", "nowhere"), [enter(7, 3)])
    es.inserts["u5"] = Insert(Anchor("after", "u1.0"), [enter(7, 4)])
    es.node(b).delete = True
    es.node("1:DEADBEEF").delete = True
    got = apply(p, es)
    assert sorted((f.code, f.node) for f in got.findings) == [
        ("DOC_ANCHOR", "1:DEADBEEF"),
        ("DOC_ANCHOR", "u1"),
        ("DOC_ANCHOR", "u2"),
        ("DOC_ANCHOR", "u3"),
        ("DOC_ANCHOR", "u4"),
        ("DOC_ANCHOR", "u5"),
    ]
    assert names(got.program.item("base_0").body) == ["if_latch", "end"]  # the rest was applied


def test_move_anchors():
    p = world()
    a, b, c = (n.id for n in p.item("base_0").body)
    es = EditSet.of(p)
    es.node(a).move = Anchor("after", "u1.0")
    es.node(b).move = Anchor("before", c)
    es.node(c).delete = True
    got = apply(p, es)
    assert sorted((f.code, f.node) for f in got.findings) == [("DOC_ANCHOR", a), ("DOC_ANCHOR", b)]


def test_insert_cycle():
    p = world()
    es = EditSet.of(p)
    es.inserts["u1"] = Insert(Anchor("after", "u2.0"), [enter(7, 0)])
    es.inserts["u2"] = Insert(Anchor("after", "u1.0"), [enter(7, 1)])
    got = apply(p, es)
    assert [(f.code, f.node) for f in got.findings] == [
        ("DOC_CONFLICT", "u1"),
        ("DOC_CONFLICT", "u2"),
    ]


def test_conflicts():
    p = world()
    top, call, end = p.item("base_0").body
    es = EditSet.of(p)
    es.node(call.id).delete = True
    es.node(call.id).args = {"routine": 1}
    es.node(top.id).bypass = "then"
    es.node(top.id).arms = [top.arms[0].id]
    es.node(top.arms[0].id).move = Anchor("after", end.id)
    es.node(end.id).bypass = "then"
    got = apply(p, es)
    assert sorted(f.node for f in got.findings) == sorted([call.id, top.id, top.arms[0].id, end.id])
    assert {f.code for f in got.findings} == {"DOC_CONFLICT"}
    assert got.touched == () and got.program.item("base_0") is p.item("base_0")


def test_bad_edits_are_findings_not_errors():
    p = world()
    top = p.item("base_0").body[0]
    es = EditSet.of(p)
    es.node(top.head[0].id).bypass = "then"  # not a block
    es.node("nope").delete = True
    es.node(top.id).bypass = "elsewhere"
    es.routines["u7"] = []
    es.routines["base_0"] = []
    es.node(p.item("call_1").body[0].id).bypass = p.item("call_1").body[0].arms[1].id
    es.node(p.item("call_1").body[0].arms[1].id).delete = True
    es.inserts["u1"] = Insert(Anchor("into", top.head[0].id), [enter(7, 0)])
    es.inserts["u2"] = Insert(Anchor("into", "base_0"), [enter(7, 0)], ir.Arm(1, {}, []))
    got = apply(p, es)
    assert len(got.findings) == 8
    assert {f.code for f in got.findings} == {"DOC_CONFLICT", "DOC_ANCHOR"}


def test_unknown_label():
    p = world()
    es = EditSet.of(p)
    es.inserts["u1"] = Insert(Anchor("into", "base_1"), [edit.insn("call", ir.Sym("nowhere"))])
    got = apply(p, es)
    assert [(f.code, f.node, f.item) for f in got.findings] == [("DOC_ANCHOR", "u1.0", "base_1")]


# ---- sharing and aliases


def test_copy_on_write():
    p = world()
    es = EditSet.of(p)
    es.node(ids(p, "base_1")[0]).delete = True
    es.inserts["u1"] = Insert(Anchor("into", "call_0", 0), [enter(7, 0)])
    got = run(p, es)
    items = {i.name: i for i in p.items if not isinstance(i, ir.Blob)}
    for it in got.program.items:
        assert (it is items[it.name]) == (it.name not in ("base_1", "call_0")), it.name
    assert got.touched == ("base_1", "call_0")


def test_apply_twice():
    p = world()
    es = EditSet.of(p)
    block = edit.if_("if_enraged", then=[enter(7, 0)])
    es.inserts["u1"] = Insert(Anchor("into", "call_0", 0), [block])
    es.routines["spin"] = [edit.insn("end", 1)]
    es.inserts["u2"] = Insert(Anchor("into", "base_1", 0), [edit.insn("call", ir.Sym("spin"))])
    before = es.copy()
    first, second = run(p, es), run(p, es)
    assert asm.assemble(first.program).data == asm.assemble(second.program).data
    assert es == before


def test_alias_row():
    p, q = world(1), world(2)
    assert p.sha1 == q.sha1
    es = EditSet.of(p)
    es.also = [2]
    es.node(ids(p, "base_1")[0]).delete = True
    es.inserts["u1"] = Insert(Anchor("before", ids(p, "base_0")[0]), [enter(7, 0)])
    got = run(q, es)
    assert names(got.program.item("base_1").body) == ["goto"]
    assert ids(got.program, "base_0")[1].startswith("2:")
    assert [r for r in es.retarget(2).nodes] == [ids(q, "base_1")[0]]
    assert asm.assemble(got.program).data == asm.assemble(run(p, es).program).data
    assert [f.code for f in apply(world(3), es).findings] == ["DOC_BUILD"]


# ---- as data


def full(p):
    top = p.item("base_0").body[0]
    pick, sw = p.item("call_0").body[0], p.item("call_1").body[0]
    es = EditSet.of(p)
    es.also = [2, 3]
    set_args(es, p, top.head[0].id, main=3)
    set_args(es, p, pick.arms[0].id, weight=8)
    set_arms(es, p, sw.id, [sw.arms[1].id, sw.arms[0].id, sw.arms[2].id])
    es.node(p.item("base_0").body[1].id).delete = True
    es.node(top.id).bypass = editset.NOTHING
    es.node(p.item("base_1").body[0].id).move = Anchor("into", "call_0", 0)
    es.node(p.item("call_1").body[1].id).move = Anchor("after", p.item("call_1").body[2].id)
    block = edit.if_("if_enraged", then=[enter(7, 0)])
    es.inserts["u1"] = Insert(Anchor("before", top.id), [block])
    es.inserts["u12"] = Insert(Anchor("into", "spin"), [edit.insn("call", ir.Sym("spin"))])
    arm = ir.Arm(1, {"weight": 4}, [enter(1, 0)])
    es.inserts["u3"] = Insert(Anchor("after", pick.arms[1].id), arm=arm)
    es.routines["spin"] = [enter(2, 0), edit.insn("end", 1)]
    es.lists["h0.call"] = ListEdit({0: "spin", 5: None}, ["call_1", ir.External(0x1234)])
    es.lists["h1.deep2"] = ListEdit(fork=True)
    es.names["call_0"], es.notes[top.id] = "Pick", "the hub"
    return es


def test_data_round_trip():
    p = world()
    es = full(p)
    data = es.to_data()
    assert data["build"] == p.sha1 and data["also"] == [2, 3]
    assert list(data["insert"]) == ["u1", "u3", "u12"]
    assert data["list"]["h0.call"] == {
        "set": {"0": "spin", "5": "-"},
        "append": ["call_1", "extern 0x00001234"],
    }
    back = EditSet.from_data(1, data)
    assert back == es and back.to_data() == data and equal(back, es)
    assert back.inserts["u1"].nodes is not es.inserts["u1"].nodes


def test_data_with_code_text():
    p = world()
    data = full(p).to_data(lambda nodes: f"<{len(nodes)}>")
    assert data["insert"]["u1"]["code"] == "<1>" and data["routine"]["spin"] == "<2>"
    assert data["insert"]["u3"]["arm"] == {"args": {"weight": 4}}
    parsed = EditSet.from_data(1, data, lambda text: [enter(int(text[1]), 0)])
    assert parsed.inserts["u1"].nodes == [enter(1, 0)]
    assert parsed.inserts["u3"].arm.body == [enter(1, 0)]
    with pytest.raises(DocError, match="code text"):
        EditSet.from_data(1, data)


def test_data_is_plain():
    def plain(v):
        if isinstance(v, dict):
            return all(isinstance(k, str) and plain(x) for k, x in v.items())
        if isinstance(v, list):
            return all(plain(x) for x in v)
        return isinstance(v, str | int | bool)

    assert plain(full(world()).to_data())
    assert EditSet(1, "x").to_data() == {"build": "x"}


ONE = {"op": "enter", "args": {"main": 0, "sub": 0, "gate": 0}}


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ({"nodes": {}}, "no nodes"),
        ({"node": {"a": {"delet": True}}}, "no delet"),
        ({"node": {"a": {"args": {"x": 1.5}}}}, "no number"),
        ({"node": {"a": {"args": {"x": "y"}}}}, "no operand"),
        ({"node": {"a": {"move": {"before": "x", "after": "y"}}}}, "one of before"),
        ({"node": {"a": {"move": {"into": "x", "at": -1}}}}, "from 0"),
        ({"node": {"a": {"delete": 1}}}, "yes or no"),
        ({"insert": {"x1": {"before": "a"}}}, "u<k>"),
        ({"insert": {"u1": {"before": "a", "code": [{"op": "enter"}]}}}, "takes"),
        ({"insert": {"u1": {"before": "a", "code": [{"op": "nope"}]}}}, "no opcode"),
        ({"insert": {"u1": {"before": "a", "code": [{**ONE, "head": [ONE]}]}}}, "no block"),
        ({"routine": {"1a": []}}, "no routine name"),
        ({"list": {"h0.nope": {}}}, "header.list"),
        ({"list": {"h0.call": {"set": {"x": "a"}}}}, "no number"),
        ({"list": {"h0.call": {"append": ["extern zz"]}}}, "no address"),
        ({"build": 5}, "no text"),
    ],
)
def test_data_errors(data, match):
    with pytest.raises(DocError, match=match):
        EditSet.from_data(1, {"build": "x", **data})


# ---- gestures


def test_set_args_normal_form():
    p = world()
    n = p.item("base_0").body[0].head[0]
    es = EditSet.of(p)
    set_args(es, p, n.id, main=0)
    assert es.nodes == {} and es.is_empty()
    set_args(es, p, n.id, main=2, sub=1)
    assert es.nodes[n.id].args == {"main": 2}
    set_args(es, p, n.id, main=0)
    assert es.nodes == {}
    with pytest.raises(EditError, match="no node"):
        set_args(es, p, "1:DEADBEEF", main=1)


def test_revert_node():
    p = world()
    top, pick = p.item("base_0").body[0], p.item("call_0").body[0]
    es = full(p)
    es.inserts["u4"] = Insert(Anchor("after", "u1.0"), [enter(7, 7)])
    es.inserts["u5"] = Insert(Anchor("into", top.arms[0].id), [enter(7, 8)])
    revert_node(es, p, top.id)
    assert top.id not in es.nodes and top.head[0].id not in es.nodes
    assert set(es.inserts) == {"u12", "u3"}  # u1 and what hangs on it, and u5, went
    assert top.id not in es.notes
    assert pick.arms[0].id in es.nodes
    revert_node(es, p, pick.id)
    assert pick.arms[0].id not in es.nodes and "u3" not in es.inserts


def test_revert_insert():
    p = world()
    es = full(p)
    es.inserts["u4"] = Insert(Anchor("after", "u1.0"), [enter(7, 7)])
    es.inserts["u5"] = Insert(Anchor("after", "u4.0"), [enter(7, 8)])
    es.node(p.item("call_1").body[2].id).move = Anchor("before", "u5.0")
    es.notes["u1"] = "hub guard"
    revert_insert(es, "u1")
    assert set(es.inserts) == {"u12", "u3"} and "u1" not in es.notes
    assert p.item("call_1").body[2].id not in es.nodes
    revert_insert(es, "u99")
    assert set(es.inserts) == {"u12", "u3"}


def test_revert_routine():
    p = world()
    es = full(p)
    revert_routine(es, p, "base_1")
    assert p.item("base_1").body[0].id not in es.nodes
    revert_routine(es, p, "base_0")
    left = {p.item("call_0").body[0].arms[0].id, *ids(p, "call_1")[:2]}
    assert set(es.nodes) == left
    assert "u1" not in es.inserts and p.item("base_0").body[0].id not in es.notes
    revert_routine(es, p, "spin")
    assert "spin" not in es.routines and "u12" not in es.inserts
    assert es.lists["h0.call"].slots == {5: None} and es.lists["h0.call"].append[0] == "call_1"
    revert_routine(es, p, "call_0")
    assert "call_0" not in es.names
    with pytest.raises(EditError, match="no routine"):
        revert_routine(es, p, "nope")


def test_empty_and_equal():
    p = world()
    a, b = EditSet.of(p), EditSet.of(p)
    a.node("x")  # an empty edit is none
    a.lists["h0.call"] = ListEdit()
    assert equal(a, b) and a != b and a.is_empty() and editset.is_empty(a)
    a.notes["x"] = "note"
    assert a.is_empty() and not equal(a, b)
    a.lists["h0.call"].fork = True
    assert not a.is_empty()
    assert EditSet.of(p).new_key() == "u1"
    assert full(p).new_key() == "u13"


# ---- the game


@pytest.fixture(scope="module")
def rows(game):
    """Every overlay's species rows, lifted: {em: (Scripts, {row: Program})}."""
    out = {}
    for em in EM_SPECIES:
        sc = script.Scripts(game, em)
        out[em] = (sc, lift.lift_all(sc))
    return out


def hub(p):
    """The `enter 0 1 0` of em75's base routine 0."""
    (n,) = [
        n
        for n in ir.walk(p.item("base_0").body)
        if isinstance(n, ir.Insn)
        and n.op.name == "enter"
        and n.args == {"main": 0, "sub": 1, "gate": 0}
    ]
    return n


def test_game_empty_set_every_row(rows):
    count = 0
    for em, (sc, ps) in rows.items():
        for row, p in ps.items():
            got = apply(p, EditSet.of(p))
            assert got.program is p and got.findings == [], (em, row)
            img = asm.assemble(edit.resolve(got.program))
            assert img.data == sc.ovl.read(p.base, p.end - p.base), (em, row)
            assert hashlib.sha1(img.data).hexdigest() == p.sha1
            count += 1
    assert count == 42


def test_game_modify_every_row(rows):
    for em, (_, ps) in rows.items():
        # rows with one region differ in nothing the edit touches
        for p in {(p.sha1, len(p.routines())): p for p in ps.values()}.values():
            n = next(
                n for n in p.nodes() if isinstance(n, ir.Insn) and n.op.name == "enter" and n.id
            )
            es = EditSet.of(p)
            set_args(es, p, n.id, sub=n.args["sub"] ^ 1)
            got = apply(p, es)
            roundtrip(got)
            assert len(got.touched) == 1, (em, p.row)
            same_items = sum(a is b for a, b in zip(got.program.items, p.items, strict=True))
            assert same_items == len(p.items) - 1


def test_game_enter_and_if_enraged(rows):
    p = rows[75][1][75]
    es = EditSet.of(p)
    nid = hub(p).id
    set_args(es, p, nid, main=3, sub=0)
    es.inserts["u1"] = Insert(Anchor("after", nid), [edit.if_("if_enraged", then=[enter(3, 0)])])
    got = run(p, es)
    img = roundtrip(got)
    assert (len(asm.assemble(p).data), len(img.data)) == (6320, 6432)
    assert img.items["base_0"][0] >= p.end - p.base  # it grew, so it moved
    assert got.touched == ("base_0",)
    assert sum(a is b for a, b in zip(got.program.items, p.items, strict=True)) == len(p.items) - 1


def test_game_every_operation(rows):
    p = rows[75][1][75]
    home, b3 = p.item("base_0"), p.item("base_3")
    pick = next(n for n in ir.walk(home.body) if isinstance(n, ir.Block) and n.op.name == "random")
    target, enraged = b3.body[0], b3.body[1]
    assert (target.op.name, enraged.op.name, len(pick.arms)) == (
        "target_remembered",
        "if_enraged",
        6,
    )
    es = EditSet.of(p)
    set_args(es, p, pick.arms[0].id, weight=pick.arms[0].args["weight"] - 1)
    set_args(es, p, pick.arms[1].id, weight=pick.arms[1].args["weight"] + 1)
    set_args(
        es, p, pick.arms[5].id, weight=pick.arms[5].args["weight"] + pick.arms[4].args["weight"]
    )
    es.node(pick.arms[4].id).delete = True
    set_arms(es, p, pick.id, [pick.arms[i].id for i in (2, 0, 1, 3, 4, 5)])
    es.node(target.id).move = Anchor("after", enraged.id)
    es.node(enraged.id).bypass = "else"
    es.routines["extra"] = [enter(3, 0), edit.insn("end", 1)]
    es.inserts["u1"] = Insert(Anchor("into", "base_4", 0), [edit.insn("call", ir.Sym("extra"))])
    es.inserts["u2"] = Insert(
        Anchor("into", "base_5", 0), [edit.if_("if_enraged", then=[enter(3, 0)])]
    )
    es.lists["h0.int_0x20"] = ListEdit({1: "extra"})
    got = run(p, es)
    roundtrip(got)
    assert names(got.program.item("base_3").body) == ["call", "target_remembered", "end"]
    assert got.program.item("int_0x20_l").slots == ["int_0x20_0", "extra"]
    assert got.touched == ("base_0", "base_3", "base_4", "base_5", "extra")


def test_game_alias_rows(rows):
    ps = rows[75][1]
    a, b = ps[76], ps[88]
    assert a.sha1 == b.sha1 and a.row != b.row
    es = EditSet.of(a)
    es.also = [88]
    nid = next(n for n in ir.walk(a.item("base_0").body) if isinstance(n, ir.Insn)).id
    set_args(es, a, nid, main=3)
    got = run(b, es)
    roundtrip(got)
    (twin,) = es.retarget(88).nodes
    assert twin.startswith("88:") and ir.Index(got.program).node(twin).origin == twin
    assert got.touched == ("base_0",)
    assert apply(ps[75], es).findings[0].code == "DOC_BUILD"


def test_game_em01_time(rows):
    p = rows[1][1][1]
    assert len(p.routines()) == 438
    es = EditSet.of(p)
    target = p.routines()[200]
    nid = next(n for n in ir.walk(target.body) if isinstance(n, ir.Insn)).id
    es.node(nid).delete = True
    es.inserts["u1"] = Insert(Anchor("into", target.name, 0), [edit.insn("nop")])
    start = time.perf_counter()
    got = apply(p, es)
    assert time.perf_counter() - start < 0.5
    assert got.findings == [] and got.touched == (target.name,)
    assert sum(a is b for a, b in zip(got.program.items, p.items, strict=True)) == len(p.items) - 1
    start = time.perf_counter()
    apply(p, EditSet.of(p))
    assert time.perf_counter() - start < 0.05
