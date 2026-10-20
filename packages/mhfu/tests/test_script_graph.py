# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import time
from collections import Counter

import pytest
from mhfu.em import script
from mhfu.em.script import asm, edit, ir, lift
from mhfu.em.script import catalogue as cat
from mhfu.em.script.check import _terminal
from mhfu.em.script.editset import Anchor, EditSet, Insert, apply, set_args
from mhfu.em.script.graph import (
    ENTRY,
    LINKS,
    ROOT,
    EdgeKind,
    Entry,
    Join,
    Projection,
    RoutineGraph,
    Stmt,
    edited_ids,
    head_key,
    join_id,
)
from mhfu.em.script.opcodes import FIRST_DEEP, LISTS, OPCODES, Flow, Shape, operands
from mhfu.files import EM_SPECIES

BASE = 0x0010_0000
NEXT, PORT, JOIN, LOOP, LINK = (
    EdgeKind.NEXT,
    EdgeKind.PORT,
    EdgeKind.JOIN,
    EdgeKind.LOOP,
    EdgeKind.LINK,
)


def enter(main=0, sub=1):
    return edit.insn("enter", main, sub, 0)


def end(kind=0):
    return edit.insn("end", kind)


def nop():
    return edit.insn("nop")


def native(p):
    """Give a program the ids, addresses and closes a lift has."""
    for k, n in enumerate(p.nodes()):
        n.id = n.id or f"n{k}"
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


def build(routines, headers=None, data=()):
    """A native program of routines by name; `headers` maps a header's name to its lists by
    slot name, by default the base list holding the first routine."""
    headers = headers or {"h0": {"base": [next(iter(routines))]}}
    items = [ir.Routine(name, body) for name, body in routines.items()]
    items += [ir.Data(name, b"\x00\x00", {name: 0}) for name in data]
    for k, (name, lists) in enumerate(headers.items()):
        fields = [None] * FIRST_DEEP
        for lst, entries in lists.items():
            at = LISTS[lst] if lst in LISTS else FIRST_DEEP + int(lst.removeprefix("deep"))
            fields += [None] * (at + 1 - len(fields))
            fields[at] = f"{name}_{lst}_l"
            items.append(ir.Table(f"{name}_{lst}_l", list(entries)))
        types = range(0, 9) if k == 0 else range(9, 17)
        items.append(ir.Header(name, fields, tuple(types)))
    return native(ir.Program(1, BASE, items))


def graph_of(routines, name=None, **kw):
    return Projection(build(routines, **kw)).routine(name or next(iter(routines)))


def edges(g, kind=None):
    return {(e.kind, e.src, e.dst, e.port) for e in g.edges if kind is None or e.kind is kind}


def rebuild(g, key=ROOT):
    """The tree a graph stands for, from its nodes, ports and scopes alone."""
    out = []
    for nid in g.scopes[key].body:
        n = g.nodes[nid]
        assert isinstance(n, Stmt)
        if not n.block:
            out.append(ir.Insn(n.op, dict(n.args), n.id, n.pc, n.origin))
            continue
        head, arms = [], []
        for port in n.ports:
            if port.scope is None:
                continue
            body = rebuild(g, port.scope)
            if port.head:
                head = body
            else:
                arms.append(ir.Arm(port.sub, dict(port.args), body, port.arm, port.pc, port.origin))
        out.append(ir.Block(n.op, dict(n.args), head, arms, n.id, n.pc, n.close, n.origin))
    return out


# ---- blocks


def test_if():
    a, b, tail = enter(0, 1), enter(0, 2), end()
    top = edit.if_("if_latch", then=[a], else_=[b])
    g = graph_of({"base_0": [top, tail]})
    s = g.nodes[top.id]
    assert [p.name for p in s.ports] == ["then", "else"]
    assert [p.head for p in s.ports] == [True, False]
    assert s.join == join_id(top.id) and isinstance(g.nodes[s.join], Join)
    assert edges(g) == {
        (NEXT, ENTRY, top.id, None),
        (PORT, top.id, a.id, 0),
        (PORT, top.id, b.id, 1),
        (JOIN, a.id, s.join, None),
        (JOIN, b.id, s.join, None),
        (NEXT, s.join, tail.id, None),
    }
    assert g.scopes[head_key(top.id)].body == (a.id,)
    assert g.scopes[top.arms[0].id].body == (b.id,)
    assert g.scopes[ROOT].nodes == (ENTRY, top.id, s.join, tail.id)
    assert list(g.nodes) == [ENTRY, top.id, a.id, b.id, s.join, tail.id]
    assert (s.size, g.size, g.scopes[head_key(top.id)].size) == (3, 4, 1)


def test_if_no_else():
    a = enter()
    top = edit.if_("if_latch", then=[a])
    g = graph_of({"base_0": [top, end()]})
    s = g.nodes[top.id]
    assert [(p.name, p.scope, p.falls) for p in s.ports] == [
        ("then", head_key(top.id), True),
        ("else", None, True),
    ]
    assert s.ports[1].arm == "" and s.ports[1].sub is None
    assert (PORT, top.id, s.join, 1) in edges(g)


def test_if_empty_then():
    top = edit.if_("if_latch", then=[], else_=[enter()])
    g = graph_of({"base_0": [top, end()]})
    s = g.nodes[top.id]
    assert (PORT, top.id, s.join, 0) in edges(g)
    assert g.scopes[head_key(top.id)].body == ()


def test_terminal_arm():
    a, b = end(1), enter()
    top = edit.if_("if_latch", then=[a], else_=[b])
    g = graph_of({"base_0": [top, nop()]})
    s = g.nodes[top.id]
    assert [p.falls for p in s.ports] == [False, True]
    assert not s.terminal and s.join is not None
    assert g.out(a.id) == ()
    assert [e.kind for e in g.out(b.id)] == [JOIN]
    assert g.nodes[a.id].terminal and g.nodes[a.id].end.name == "return"


def test_block_that_ends():
    a, b, after = end(0), end(1), nop()
    top = edit.if_("if_latch", then=[a], else_=[b])
    g = graph_of({"base_0": [top, after]})
    s = g.nodes[top.id]
    assert s.terminal and s.join is None and join_id(top.id) not in g.nodes
    assert g.out(top.id) == tuple(e for e in g.edges if e.src == top.id)
    assert {e.kind for e in g.out(top.id)} == {PORT}
    nxt = g.nodes[after.id]
    assert nxt.dead and not nxt.falls_off
    assert not [e for e in g.edges if e.dst == after.id]


def test_dead_after_end():
    top, dead = end(), nop()
    g = graph_of({"base_0": [top, dead]})
    assert g.nodes[dead.id].dead and not g.nodes[top.id].dead
    assert g.out(top.id) == () and not g.nodes[dead.id].falls_off
    assert [n.id for n in g.nodes.values() if n.dead] == [dead.id]


def test_dead_inside():
    a = end()
    inner = edit.if_("if_latch", then=[nop()], else_=[nop()])
    g = graph_of({"base_0": [a, inner]})
    assert all(n.dead for n in g.nodes.values() if n.id not in (ENTRY, a.id))
    assert not [e for e in g.edges if e.dst == inner.id]
    assert [e.kind for e in g.edges if e.src == inner.id] == [PORT, PORT]


def test_switch():
    x, y, z = enter(2, 0), enter(2, 1), enter(2, 2)
    sw = edit.switch("switch_hp", cases=[(30, [x]), (60, [y])], default=[z])
    g = graph_of({"base_0": [sw, end()]})
    s = g.nodes[sw.id]
    assert [p.name for p in s.ports] == ["HP below 30 %", "HP below 60 %", "otherwise"]
    assert [p.name for p in s.ports] == list(cat.ports(sw).arms)
    assert [(p.arm, p.sub) for p in s.ports] == [(a.id, a.sub) for a in sw.arms]
    assert all(p.args == a.args for p, a in zip(s.ports, sw.arms, strict=True))
    assert not any(p.scope is None for p in s.ports)
    got = {(e.kind, e.port) for e in g.out(sw.id)}
    assert got == {(PORT, 0), (PORT, 1), (PORT, 2)}
    assert {e.src for e in g.edges if e.kind is JOIN} == {x.id, y.id, z.id}


def test_switch_no_default():
    x = enter(2, 0)
    sw = edit.switch("switch_hp", cases=[(30, [x])])
    g = graph_of({"base_0": [sw, end()]})
    s = g.nodes[sw.id]
    assert [p.name for p in s.ports] == ["HP below 30 %", "otherwise"]
    assert s.ports[1].scope is None and s.ports[1].falls
    assert (PORT, sw.id, s.join, 1) in edges(g)


def test_switch_all_end():
    sw = edit.switch("switch_hp", cases=[(30, [end()])], default=[end(1)])
    g = graph_of({"base_0": [sw]})
    assert g.nodes[sw.id].terminal and g.nodes[sw.id].join is None
    assert not any(isinstance(n, Join) for n in g.nodes.values())
    sw2 = edit.switch("switch_hp", cases=[(30, [end()])])  # no default: a case may not match
    g2 = graph_of({"base_0": [sw2]})
    assert not g2.nodes[sw2.id].terminal
    assert g2.nodes[g2.nodes[sw2.id].join].falls_off


def test_pick():
    a, b, c = enter(1, 0), enter(1, 1), enter(1, 2)
    pick = edit.random([(16, [a]), (8, [b]), (8, [c])])
    g = graph_of({"base_0": [pick, end()]})
    s = g.nodes[pick.id]
    assert [p.name for p in s.ports] == ["16/32", "8/32", "8/32"]
    assert [(p.weight, p.share) for p in s.ports] == [(16, 16), (8, 8), (8, 8)]
    assert s.chance is None and s.title == "Pick one"
    assert [p.scope for p in s.ports] == [arm.id for arm in pick.arms]


def test_chance():
    a = enter(1, 0)
    pick = edit.random([(8, [a])])
    g = graph_of({"base_0": [pick, end()]})
    s = g.nodes[pick.id]
    assert s.title == "Chance 8/32" and s.chance == cat.Chance(0, 8)
    assert [p.name for p in s.ports] == ["8/32", "otherwise"]
    assert [p.share for p in s.ports] == [8, 24]
    assert (PORT, pick.id, s.join, 1) in edges(g)


def test_repeat():
    a, b = enter(0, 1), nop()
    rep = edit.repeat(3, [a, b])
    nxt = end()
    g = graph_of({"base_0": [rep, nxt]})
    s = g.nodes[rep.id]
    assert s.ports[0].name == "body" and s.join is None
    assert edges(g) == {
        (NEXT, ENTRY, rep.id, None),
        (PORT, rep.id, a.id, 0),
        (NEXT, a.id, b.id, None),
        (LOOP, b.id, rep.id, None),
        (NEXT, rep.id, nxt.id, None),
    }
    assert g.port(next(e for e in g.edges if e.kind is PORT)) is s.ports[0]


def test_repeat_empty_and_ending():
    empty = edit.repeat(2, [])
    g = graph_of({"base_0": [empty, end()]})
    assert (LOOP, empty.id, empty.id, 0) in edges(g)
    stop = edit.repeat(2, [end()])
    g2 = graph_of({"base_0": [stop, nop()]})
    assert g2.nodes[stop.id].terminal
    assert not [e for e in g2.edges if e.kind in (LOOP, NEXT) and e.src == stop.id]
    assert g2.nodes[g2.scopes[ROOT].body[1]].dead


def test_move_resumes():
    a, b = enter(0, 1), nop()
    g = graph_of({"base_0": [a, b, end()]})
    assert g.nodes[a.id].yields and g.nodes[a.id].kind is cat.Kind.MOVE
    assert (NEXT, a.id, b.id, None) in edges(g)
    assert not g.nodes[b.id].yields
    w = edit.insn("walk", 0, 1, 0, 0, 0)
    assert graph_of({"base_0": [w, end()]}).nodes[w.id].yields


def test_end_kinds():
    stop, back = end(0), end(1)
    g = graph_of({"base_0": [edit.if_("if_latch", then=[stop], else_=[back])]})
    assert g.nodes[stop.id].end == cat.end_kind(0) and g.nodes[back.id].end.name == "return"
    assert g.nodes[stop.id].end.title == "Restart" and g.nodes[stop.id].terminal
    assert graph_of({"base_0": [nop(), end()]}).nodes["n1"].end.code == 0


def test_falls_off():
    last = nop()
    g = graph_of({"base_0": [enter(), last]})
    assert g.nodes[last.id].falls_off and not g.nodes["n0"].falls_off
    g2 = graph_of({"base_0": []})
    assert g2.nodes[ENTRY].falls_off and edges(g2) == set()
    inner = edit.if_("if_latch", then=[enter()], else_=[enter()])
    g3 = graph_of({"base_0": [nop(), inner]})
    j = g3.nodes[inner.id].join
    assert g3.nodes[j].falls_off and not g3.nodes[inner.id].falls_off


def test_nested():
    deep = enter(0, 3)
    sw = edit.switch("switch_hp", cases=[(30, [deep]), (60, [nop()])], default=[nop()])
    top = edit.if_("if_latch", then=[nop()], else_=[sw, nop()])
    g = graph_of({"base_0": [top, end()]})
    case0 = sw.arms[0].id
    assert [c.label for c in g.crumbs(case0)] == [
        "base_0",
        g.nodes[top.id].title,
        "else",
        g.nodes[sw.id].title,
        "HP below 30 %",
    ]
    assert [c.scope for c in g.crumbs(case0)] == [ROOT, ROOT, top.arms[0].id, top.arms[0].id, case0]
    assert [c.node for c in g.crumbs(case0)] == [None, top.id, None, sw.id, None]
    assert g.crumbs(ROOT) == (g.crumbs(ROOT)[0],) and g.crumbs(ROOT)[0].label == "base_0"
    s = g.scopes[case0]
    assert (s.parent, s.owner, s.port, s.depth) == (top.arms[0].id, sw.id, 0, 2)
    assert g.nodes[deep.id].depth == 2 and g.nodes[deep.id].scope == case0
    assert g.nodes[top.id].size == 7 and g.scopes[top.arms[0].id].size == 5
    after = g.scopes[top.arms[0].id].body[1]  # the nop that follows the switch
    assert (NEXT, g.nodes[sw.id].join, after, None) in edges(g)
    assert (JOIN, after, g.nodes[top.id].join, None) in edges(g)


def test_no_id():
    p = ir.Program(1, BASE, [ir.Routine("base_0", [nop()])])
    with pytest.raises(ValueError, match="no id"):
        Projection(p).routine("base_0")
    with pytest.raises(KeyError):
        Projection(build({"base_0": [end()]})).routine("nothing")


# ---- links


def link_edges(g):
    return [(e.src, e.link) for e in g.edges if e.kind is LINK]


def test_links():
    c1, c2, c3 = edit.insn("call", 0), edit.insn("call_ret", 0), edit.insn("call_deep", 0, 1)
    j = edit.insn("goto", 1)
    gb = edit.insn("goto_base")
    w = edit.insn("walk", 0, 2, 0, 1, 2)
    arm = edit.insn("route_arm", 1, 3, 0, 0)
    run, spot = edit.insn("route_run"), edit.insn("call_spot", 5, 10)
    body = [c1, c2, c3, w, arm, run, spot, j, gb, end()]
    lists = {
        "base": ["base_0", "base_1"],
        "call": ["call_0"],
        "ret": ["ret_0"],
        "deep0": [None, "deep_1"],
        "walk": ["stages_a", "step_0", "arrive_0"],
        "stages": ["stages_a", "stages_a"],
        "loop": ["loop_0", None, "loop_1"],
        "node": ["node_0"],
    }
    names = ["call_0", "ret_0", "deep_1", "step_0", "arrive_0", "loop_0", "loop_1", "node_0"]
    routines = {"base_0": body, "base_1": [end()], **{n: [end(1)] for n in names}}
    prog = build(routines, {"h0": lists}, data=["stages_a"])
    g = Projection(prog).routine("base_0")
    got = {
        (e.src, e.link.kind, e.link.role, e.link.list, e.link.index, e.link.target)
        for e in g.edges
        if e.kind is LINK
    }
    assert got == {
        (c1.id, "call", "", "call", 0, "call_0"),
        (c2.id, "call_ret", "", "ret", 0, "ret_0"),
        (c3.id, "call_deep", "", "deep0", 1, "deep_1"),
        (w.id, "walk", "step", "walk", 1, "step_0"),
        (w.id, "walk", "arrive", "walk", 2, "arrive_0"),
        (arm.id, "route", "body", "loop", 0, "loop_0"),
        (run.id, "route", "", "loop", None, "loop_0"),
        (run.id, "route", "", "loop", None, "loop_1"),
        (spot.id, "call_spot", "", "node", None, "node_0"),
        (j.id, "goto", "", "base", 1, "base_1"),
        (gb.id, "goto", "", "base", 0, "base_0"),
    }
    assert {k for _, k, *_ in got} <= set(LINKS)
    assert all(e.dst is None and e.port is None for e in g.edges if e.kind is LINK)
    assert all(e.link.headers == ("h0",) for e in g.edges if e.kind is LINK)


def test_links_unresolved():
    c = edit.insn("call", 3)
    far = edit.insn("call", 9)
    lists = {"base": ["base_0"], "call": ["call_a", None, None, None]}
    g = graph_of({"base_0": [c, far, end()], "call_a": [end(1)]}, headers={"h0": lists})
    assert [(s, k.index, k.target) for s, k in link_edges(g)] == [
        (c.id, 3, None),
        (far.id, 9, None),
    ]
    spot = edit.insn("call_spot", 5, 10)
    g2 = graph_of({"base_0": [spot, end()]})
    assert [k.target for _, k in link_edges(g2)] == [None]


def test_links_by_header():
    c = edit.insn("call", 0)
    routines = {"base_0": [c, end()], "a": [end(1)], "b": [end(1)], "base_1": [end()]}
    headers = {
        "h0": {"base": ["base_0"], "call": ["a"]},
        "h1": {"base": ["base_0"], "call": ["b"]},
        "h2": {"base": ["base_1"], "call": ["a"]},
    }
    prog = build(routines, headers)
    proj = Projection(prog)
    g = proj.routine("base_0")
    assert g.headers == ("h0", "h1")
    assert sorted((k.target, k.headers) for _, k in link_edges(g)) == [
        ("a", ("h0",)),
        ("b", ("h1",)),
    ]
    only = proj.routine("base_0", header="h1")
    assert only.headers == ("h1",) and [(k.target, k.headers) for _, k in link_edges(only)] == [
        ("b", ("h1",))
    ]
    assert proj.routine("base_0", "h1") is only and proj.routine("base_0") is g
    with pytest.raises(KeyError):
        proj.routine("base_0", header="h9")
    # a routine no header runs links through all of them
    prog2 = build({**routines, "lost": [edit.insn("call", 0), end()]}, headers)
    lost = Projection(prog2).routine("lost")
    assert lost.headers == ("h0", "h1", "h2")
    assert sorted(k.target for _, k in link_edges(lost)) == ["a", "b"]
    assert next(k.headers for _, k in link_edges(lost) if k.target == "a") == ("h0", "h2")


# ---- marks


def test_marks():
    a, b = enter(0, 1), enter(0, 2)
    top = edit.if_("if_latch", then=[a], else_=[b])
    prog = build({"base_0": [top, end()]})
    g = Projection(prog).routine("base_0")
    assert not any(n.edited or n.new for n in g.nodes.values() if isinstance(n, Stmt))
    assert not g.edited and not any(s.edited for s in g.scopes.values())
    g = Projection(prog, edited={a.id, top.arms[0].id}).routine("base_0")
    assert {n.id for n in g.nodes.values() if isinstance(n, Stmt) and n.edited} == {a.id}
    assert g.nodes[top.id].inner_edits and not g.nodes[top.id].edited
    assert not g.nodes[a.id].new
    assert [p.edited for p in g.nodes[top.id].ports] == [False, True]
    assert g.edited and g.scopes[head_key(top.id)].edited and g.scopes[top.arms[0].id].edited
    assert g.scopes[ROOT].edited
    # no native address: new code, or a native node an edit changed
    b.pc, b.origin = None, b.id
    n = nop()
    n.id = "u1.0"
    prog.item("base_0").body.append(n)
    g = Projection(prog).routine("base_0")
    assert {x.id for x in g.nodes.values() if isinstance(x, Stmt) and x.edited} == {b.id, n.id}
    assert {x.id for x in g.nodes.values() if isinstance(x, Stmt) and x.new} == {n.id}


def test_pairs():
    a = enter(0, 1)
    prog = build({"base_0": [a, end()]})
    named = Projection(prog, pairs={(0, 1): "Idle"}).routine("base_0")
    assert named.nodes[a.id].summary == "Move (0, 1) Idle"
    assert Projection(prog).routine("base_0").nodes[a.id].summary == "Move (0, 1)"


# ---- overview


def small():
    routines = {
        "base_0": [edit.insn("call", 0), edit.insn("call", 0), edit.insn("goto", 1), end()],
        "base_1": [edit.insn("call", 1), end()],
        "call_0": [end(1)],
        "call_1": [end(1)],
        "ev": [edit.insn("call", 1), end(0xFC)],
        "lone": [end(0)],
    }
    headers = {
        "h0": {"base": ["base_0", "base_1"], "call": ["call_0", "call_1"], "int_0x20": ["ev"]},
        "h1": {"base": ["base_0", "base_1"], "call": ["call_0", "call_1"]},
    }
    return build(routines, headers)


def test_overview():
    ov = Projection(small()).overview()
    assert [h.name for h in ov.headers] == ["h0", "h1"]
    h0, h1 = ov.header("h0"), ov.header("h1")
    assert h0.variants == tuple(range(9)) and h1.variants == tuple(range(9, 17))
    top = {e.point.title: e.routine for e in h0.entry_points}
    assert top["Top"] == "base_0" and top["On notice"] == "ev" and top["On enrage"] is None
    assert {e.point.title: e.routine for e in h1.entry_points}["On notice"] is None
    assert [e.point.title for e in h0.entry_points] == [p.title for p in cat.ENTRY_POINTS]
    assert [g.name for g in h0.groups] == ["Decisions", "Events", "Calls"]
    assert [g.routines for g in h0.groups] == [
        ("base_0", "base_1"),
        ("ev",),
        ("call_0", "call_1"),
    ]
    assert [g.name for g in h1.groups] == ["Decisions", "Calls"]
    assert h0.groups[0].lists[0].info == cat.list_info("base")
    assert [(s.index, s.name, s.kind) for s in h0.groups[1].lists[0].slots] == [
        (0, "ev", "routine")
    ]
    assert h0.routines == ("base_0", "base_1", "call_0", "call_1", "ev") and h1.routines == (
        "base_0",
        "base_1",
        "call_0",
        "call_1",
    )
    assert h0.unlisted == () and ov.routines["lone"].headers == ()
    calls = {(c.src, c.dst, c.kind): c.count for c in h0.calls}
    assert calls == {
        ("base_0", "call_0", "call"): 2,
        ("base_0", "base_1", "goto"): 1,
        ("base_1", "call_1", "call"): 1,
        ("ev", "call_1", "call"): 1,
    }
    assert {(c.src, c.dst) for c in h1.calls} == {
        ("base_0", "call_0"),
        ("base_0", "base_1"),
        ("base_1", "call_1"),
    }
    info = ov.routines
    assert [(c.routine, c.sites) for c in info["call_0"].callers] == [("base_0", 2)]
    assert sorted((c.routine, c.sites) for c in info["call_1"].callers) == [
        ("base_1", 1),
        ("ev", 1),
    ]
    assert info["call_1"].reached_from == 2 and info["base_0"].reached_from == 0
    assert info["base_0"].entries == ("Top",) and info["ev"].entries == ("On notice",)
    assert info["call_1"].entries == ()
    assert info["base_0"].headers == ("h0", "h1") and info["base_0"].shared
    assert info["ev"].headers == ("h0",) and not info["ev"].shared
    assert info["base_0"].variants == tuple(range(17)) and info["ev"].variants == tuple(range(9))
    assert info["call_0"].nodes == 1 and info["base_0"].nodes == 4
    assert not any(i.edited or i.new for i in info.values())


def test_overview_marks():
    prog = small()
    ed = prog.item("call_0").body[0]
    ed.pc = None
    ov = Projection(prog).overview()
    assert [n for n, i in ov.routines.items() if i.edited] == ["call_0"]
    prog.routines()[1].va = None
    ov = Projection(prog).overview()
    assert [n for n, i in ov.routines.items() if i.new] == ["base_1"]
    ov = Projection(prog, edited={prog.item("base_0").body[0].id}).overview()
    assert sorted(n for n, i in ov.routines.items() if i.edited) == ["base_0", "call_0"]


# ---- every opcode


def sample(op):
    """Nodes of the opcode with every operand 0: one per layout of a plain opcode."""
    if op.shape is Shape.PLAIN:
        out = []
        for key in (None, *op.by):
            args = dict.fromkeys((o.name for o in operands(op.layout(key))), 0)
            if key is not None:
                args[next(iter(args))] = key
            out.append(ir.Insn(op, args))
        return out
    case = dict.fromkeys((o.name for o in operands(op.label)), 16)
    match op.shape:
        case Shape.SWITCH:
            arms = [ir.Arm(1, dict.fromkeys(case, 0), [nop()]), ir.Arm(2, {}, [])]
        case Shape.RANDOM:
            arms = [ir.Arm(1, case, [nop()]), ir.Arm(1, case, [])]
        case Shape.BUCKET | Shape.ZONE:
            arms = [ir.Arm(1, {}, [nop()]) for _ in range(3)]
        case Shape.IF:
            arms = [ir.Arm(1, {}, [nop()])]
        case _:
            arms = []
    head = [nop()] if op.shape in (Shape.IF, Shape.REPEAT) else []
    return [ir.Block(op, dict.fromkeys((o.name for o in op.opener), 0), head, arms)]


def test_every_opcode():
    nodes = [n for op in OPCODES.values() for n in sample(op)]
    for k, n in enumerate(x for x in ir.walk(nodes)):
        n.id = f"s{k}"
    prog = ir.Program(1, BASE, [ir.Routine("all", nodes)])
    g = Projection(prog).routine("all")
    assert {n.op.name for n in g.nodes.values() if isinstance(n, Stmt)} == {
        o.name for o in OPCODES.values()
    }
    assert rebuild(g) == nodes
    for n in g.nodes.values():
        if isinstance(n, Stmt):
            assert n.title and n.summary and "{" not in n.summary
            assert n.kind is cat.kind_of(n.op) and n.confidence in cat.Confidence
            assert (n.end is not None) == (n.op.flow is Flow.END)


# ---- the game


@pytest.fixture(scope="module")
def progs(game):
    """Every species row of every overlay, lifted."""
    out: dict[int, ir.Program] = {}
    for em in EM_SPECIES:
        out.update(lift.lift_all(script.Scripts(game, em)))
    return out


def expected(nodes, live, stmts, joins):
    """The statements control reaches, and the joins it ends in, from the tree alone."""
    ended = not live
    for n in nodes:
        if not ended:
            stmts.add(n.id)
        if isinstance(n, ir.Block):
            expected(n.head, not ended, stmts, joins)
            for a in n.arms:
                expected(a.body, not ended, stmts, joins)
            ports = n.head or n.arms or n.op.shape is Shape.IF
            if not ended and not _terminal(n) and n.op.shape is not Shape.REPEAT and ports:
                joins.add(join_id(n.id))
        ended = ended or _terminal(n)


def reached(g: RoutineGraph) -> set[str]:
    seen, todo = set(), [ENTRY]
    while todo:
        at = todo.pop()
        if at not in seen:
            seen.add(at)
            todo += [e.dst for e in g.out(at) if e.dst is not None]
    return seen


def check_graph(g: RoutineGraph, r: ir.Routine):
    assert rebuild(g) == r.body, (r.name, "tree")
    assert list(g.nodes)[0] == ENTRY and isinstance(g.nodes[ENTRY], Entry)
    stmts, joins = set(), set()
    expected(r.body, True, stmts, joins)
    assert reached(g) == {ENTRY, *stmts, *joins}, (r.name, "reach")
    live = {n.id for n in g.nodes.values() if not n.dead}
    assert live == {ENTRY, *stmts, *joins}, (r.name, "dead")
    assert {n.id for n in g.nodes.values() if isinstance(n, Join)} >= joins
    for e in g.edges:
        assert (e.dst is None) == (e.kind is LINK) and e.src in g.nodes
        assert e.dst is None or e.dst in g.nodes
    for n in g.nodes.values():
        if isinstance(n, Stmt) and n.block:
            for i in range(len(n.ports)):
                ours = [e for e in g.out(n.id) if e.port == i and e.kind in (PORT, LOOP)]
                assert len(ours) == 1, (r.name, n.id, i)
            assert n.size == 1 + sum(p.size for p in n.ports)
        onward = [e for e in g.out(n.id) if e.kind in (NEXT, JOIN, LOOP) and e.port is None]
        if n.dead or (isinstance(n, Stmt) and n.block and n.join):
            continue
        if isinstance(n, Stmt) and n.terminal:
            assert not onward, (r.name, n.id)
        else:
            assert len(onward) == 1 - n.falls_off, (r.name, n.id)
    assert g.size == sum(isinstance(n, Stmt) for n in g.nodes.values()) == g.scopes[ROOT].size
    flow = [x for s in g.scopes.values() for x in s.nodes]
    assert sorted(flow) == sorted(g.nodes)


def test_game_lossless(progs):
    assert len(progs) == 42
    count = 0
    for p in progs.values():
        proj = Projection(p)
        assert {h.name for h in p.headers()} == {h.name for h in proj.overview().headers}
        for r in p.routines():
            check_graph(proj.routine(r.name), r)
            count += 1
    assert count > 4000


def test_game_ports(progs):
    for p in progs.values():
        proj = Projection(p)
        for r in p.routines():
            g = proj.routine(r.name)
            for b in ir.walk(r.body):
                if not isinstance(b, ir.Block):
                    continue
                s = g.nodes[b.id]
                assert isinstance(s, Stmt)
                spec = cat.ports(b)
                have = [pt for pt in s.ports if pt.scope is not None]
                want = ([spec.head] if spec.head else []) + list(spec.arms)
                assert [pt.name for pt in have] == want
                assert [pt.arm for pt in have if not pt.head] == [a.id for a in b.arms]
                assert s.title == cat.title(b) and s.chance == cat.chance(b)
                extra = [pt for pt in s.ports if pt.scope is None]
                if b.op.shape is Shape.IF and not b.arms:
                    assert [pt.name for pt in extra] == ["else"]
                elif b.op.shape is Shape.SWITCH and not any(a.sub == 2 for a in b.arms):
                    assert [pt.name for pt in extra] == ["otherwise"]
                else:
                    assert not extra
                if b.op.shape is Shape.RANDOM:
                    assert sum(pt.share for pt in s.ports) == cat.DRAW
                    assert [pt.weight for pt in s.ports] == [a.args["weight"] for a in b.arms]


def test_game_ops(progs):
    used, seen = set(), set()
    kinds: Counter[str] = Counter()
    for p in progs.values():
        proj = Projection(p)
        used |= {n.op.name for n in p.nodes() if isinstance(n, ir.Insn | ir.Block)}
        for r in p.routines():
            g = proj.routine(r.name)
            for n in g.nodes.values():
                if isinstance(n, Stmt):
                    seen.add(n.op.name)
                    assert n.title and n.summary and "{" not in n.summary
            kinds.update(e.link.kind for e in g.edges if e.link)
    assert used == seen and len(used) > 100
    assert set(kinds) <= set(LINKS) and set(LINKS) - set(kinds) <= {"call_spot"}
    assert kinds["call_deep"] > kinds["call"] > 0


def hub_ids(p):
    return {
        n.id
        for n in ir.walk(p.item("base_0").body)
        if isinstance(n, ir.Insn) and n.op.name == "enter"
    }


def test_game_em75(progs):
    p = progs[75]
    ov = Projection(p).overview()
    assert [h.name for h in ov.headers] == ["h0"] and ov.headers[0].variants == tuple(range(17))
    h = ov.headers[0]
    points = [(e.point.title, e.routine, e.point.confidence.label) for e in h.entry_points]
    assert points == [
        ("Top", "base_0", "high"),
        ("On reset", "int_0x80_0", "medium"),
        ("On roaming", "int_0x40_0", "low"),
        ("On notice", "int_0x20_0", "high"),
        ("On sensed", "int_0x10_0", "low"),
        ("On enrage", "int_0x08_0", "high"),
        ("On other notice", None, "medium"),
        ("On repositioning", "int_0x02_0", "low"),
    ]
    assert [(g.name, len(g.routines)) for g in h.groups] == [
        ("Decisions", 11),
        ("Events", 6),
        ("Calls", 24),
        ("Deep calls", 48),
        ("Routes", 5),
        ("Loops", 1),
        ("Data", 0),
    ]
    assert [g.name for g in h.groups] == [g for g in cat.GROUPS if g in {x.name for x in h.groups}]
    assert len(h.routines) == len(p.routines()) == 95 and h.unlisted == ()
    info = ov.routines
    assert [(c.routine, c.sites) for c in info["call_3"].callers] == [("base_3", 1)]
    assert [(c.routine, c.sites) for c in info["deep0_2"].callers] == [("call_0", 2)]
    assert info["base_3"].reached_from == 1 and info["base_0"].entries == ("Top",)
    assert info["int_0x20_0"].entries == ("On notice",) and info["int_0x20_0"].reached_from == 0
    calls = {(c.src, c.dst, c.kind): c.count for c in h.calls}
    assert calls[("base_0", "call_7", "call")] == 1 and calls[("base_0", "base_1", "goto")] == 1
    assert calls[("base_2", "base_3", "goto")] >= 1
    assert all(not i.shared and i.headers == ("h0",) for i in info.values())
    assert info["call_3"].nodes == 174 == Projection(p).routine("call_3").size


def test_game_calls(progs):
    """The overview's call counts, from the instructions' own list refs."""
    for row in (75, 82, 1):
        p = progs[row]
        proj = Projection(p)
        ov = proj.overview()
        tables = p.tables()
        names = {r.name for r in p.routines()}
        reach = p.reach()
        for h in p.headers():
            want: Counter[tuple[str, str, str]] = Counter()
            for r in p.routines():
                if h.name not in reach[r.name]:
                    continue
                for n in ir.walk(r.body):
                    if not isinstance(n, ir.Insn) or n.op.name in ("route_run", "call_spot"):
                        continue
                    if n.op.flow not in (Flow.GOTO, Flow.CALL):
                        continue
                    for ref in ir.refs(n):
                        dst = p.entry(h, ref, tables)
                        if dst in names and ref.kind in ("goto", "call", "walk"):
                            kind = "goto" if n.op.flow is Flow.GOTO else n.op.name
                            want[(r.name, dst, kind)] += 1
            have = {
                (c.src, c.dst, c.kind): c.count
                for c in ov.header(h.name).calls
                if c.kind not in ("route",)
            }
            assert have == dict(want), (row, h.name)


def test_game_shared(progs):
    p = progs[1]
    ov = Projection(p).overview()
    assert len(ov.headers) == 7
    shared = {n for n, i in ov.routines.items() if i.shared}
    assert shared and len(shared) < len(ov.routines)
    reach = p.reach()
    assert shared == {n for n, hs in reach.items() if len(hs) > 1}
    some = next(i for i in ov.routines.values() if i.shared)
    types = {v for n in some.headers for h in p.headers() if h.name == n for v in h.variants}
    assert some.variants == tuple(sorted(types))
    # a link resolves by the headers that run the routine
    proj = Projection(p)
    g = proj.routine(some.name)
    assert g.headers == some.headers
    for e in g.edges:
        if e.link and e.link.target:
            assert set(e.link.headers) <= set(some.headers)


def test_game_marks(progs):
    p = progs[75]
    hub = next(
        n
        for n in ir.walk(p.item("base_0").body)
        if isinstance(n, ir.Insn)
        and n.op.name == "enter"
        and n.args == {"main": 0, "sub": 1, "gate": 0}
    )
    es = EditSet.of(p)
    set_args(es, p, hub.id, main=3, sub=0)
    es.inserts["u1"] = Insert(Anchor("after", hub.id), [edit.if_("if_enraged", then=[enter(3, 0)])])
    got = apply(p, es)
    assert got.ok and got.touched == ("base_0",)
    want = {hub.id} | {n.id for n in got.program.nodes() if n.id.startswith("u1.")}
    assert len(want) == 3
    assert edited_ids(got) == want
    proj = Projection(got)
    flagged, new = set(), set()
    for r in got.program.routines():
        g = proj.routine(r.name)
        flagged |= {n.id for n in g.nodes.values() if isinstance(n, Stmt) and n.edited}
        new |= {n.id for n in g.nodes.values() if isinstance(n, Stmt) and n.new}
        assert g.edited == (r.name == "base_0")
    assert flagged == want and new == want - {hub.id}
    top = proj.routine("base_0")
    block = next(n for n in top.nodes.values() if isinstance(n, Stmt) and n.block and n.new)
    assert block.id.startswith("u1.") and block.inner_edits
    assert block.scope == top.nodes[hub.id].scope and top.nodes[hub.id].edited
    assert top.nodes[hub.id].edited and not top.nodes[hub.id].new
    ov = proj.overview()
    assert [n for n, i in ov.routines.items() if i.edited] == ["base_0"]
    assert not any(i.new for i in ov.routines.values())
    assert rebuild(top) == got.program.item("base_0").body
    # a delete leaves no node to mark: the routine is still edited
    dele = EditSet.of(p)
    gone = next(
        (r.name, n)
        for r in p.routines()
        if r.name != "base_0"
        for n in ir.walk(r.body)
        if isinstance(n, ir.Insn) and n.op.flow is Flow.NEXT
    )
    dele.node(gone[1].id).delete = True
    cut = apply(p, dele)
    name = gone[0]
    assert cut.touched == (name,) and edited_ids(cut) == frozenset()
    proj = Projection(cut)
    assert proj.routine(name).edited and not proj.routine("base_0").edited
    assert not any(n.edited for n in proj.routine(name).nodes.values() if isinstance(n, Stmt))
    assert [n for n, i in proj.overview().routines.items() if i.edited] == [name]
    # the lift with the edit set's ids: the node the set changes
    assert edited_ids(es) == {hub.id}
    plain = Projection(p, edited=edited_ids(es))
    on = {n.id for n in plain.routine("base_0").nodes.values() if isinstance(n, Stmt) and n.edited}
    assert on == {hub.id}
    assert not any(i.edited for i in plain.overview().routines.values() if i.name != "base_0")
    assert plain.overview().routines["base_0"].edited
    assert not any(n.new for n in plain.routine("base_0").nodes.values() if isinstance(n, Stmt))
    # the same program with nothing edited has no marks at all
    clean = Projection(apply(p, EditSet.of(p)))
    assert not any(i.edited for i in clean.overview().routines.values())


def test_game_timing(progs):
    p = progs[75]
    start = time.perf_counter()
    proj = Projection(p)
    proj.routine("call_3")
    one = time.perf_counter() - start
    big = progs[1]
    start = time.perf_counter()
    whole = Projection(big)
    for r in big.routines():
        whole.routine(r.name)
    graphs = time.perf_counter() - start
    start = time.perf_counter()
    whole.overview()
    over = time.perf_counter() - start
    nodes = sum(1 for n in big.nodes() if isinstance(n, ir.Insn | ir.Block))
    print(
        f"graph: em75 call_3 alone {one * 1000:.0f} ms (with reach); em01 {len(big.routines())} "
        f"routines, {nodes} nodes: graphs {graphs * 1000:.0f} ms, overview {over * 1000:.0f} ms"
    )
    assert one < 0.5 and graphs < 10 and over < 2
