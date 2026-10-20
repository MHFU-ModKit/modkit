# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Brain tab's graph model: scenes and their layout, selection, the outline, the Code pane's
rows of an edited program."""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from mhfu.em.script import build, edit, ir
from mhfu.em.script import catalogue as cat
from mhfu.em.script.check import Finding
from mhfu.em.script.editset import Anchor, EditSet, Insert, set_args
from mhfu.em.script.graph import (
    ENTRY,
    ROOT,
    EdgeKind,
    Projection,
    RoutineGraph,
    edited_ids,
    head_key,
    join_id,
)
from mhfu.em.script.opcodes import FIRST_DEEP, LISTS
from mhfu.files import Extracted
from mhfu_studio.monster import brain
from mhfu_studio.monster import brain_graph as bg
from mhfu_studio.monster.brain_graph import IN, OUT, Scene

from .conftest import program

Rect = tuple[float, float, float, float]


def enter(main: int = 0, sub: int = 1) -> ir.Insn:
    return edit.insn("enter", main, sub, 0)


def end(kind: int = 0) -> ir.Insn:
    return edit.insn("end", kind)


def toy(
    routines: dict[str, list[ir.Node]], lists: dict[str, list[str | None]] | None = None
) -> ir.Program:
    """Routines by name with ids `n<k>` and addresses on every node; `lists` fills the header's
    lists by name (`deep0` too), by default the base list holding the first routine."""
    lists = lists or {"base": [next(iter(routines))]}
    items: list[ir.Item] = [ir.Routine(name, body) for name, body in routines.items()]
    fields: list[ir.Entry] = [None] * FIRST_DEEP
    for name, slots in lists.items():
        at = LISTS[name] if name in LISTS else FIRST_DEEP + int(name.removeprefix("deep"))
        fields += [None] * (at + 1 - len(fields))
        fields[at] = f"{name}_l"
        items.append(ir.Table(f"{name}_l", list(slots)))
    items.append(ir.Header("h0", fields, tuple(range(17))))
    p = ir.Program(75, 0x1000, items)
    for k, n in enumerate(p.nodes()):
        n.id, n.pc = f"n{k}", 0x1000 + 4 * k
    return p


def first(p: ir.Program) -> ir.Routine:
    return p.routines()[0]


def graph(p: ir.Program) -> RoutineGraph:
    return Projection(p).routine(first(p).name)


def block(n: ir.Node) -> ir.Block:
    assert isinstance(n, ir.Block)
    return n


def fit(count: int) -> ir.Block:
    """An if with `count` moves in it: the block is one node more."""
    return edit.if_("if_latch", then=[enter(1, k) for k in range(count)])


def nested() -> list[ir.Node]:
    """An if with an if and a move in it and an else, a pick with an empty branch, an end."""
    inner = edit.if_("if_enraged", then=[enter(0, 1)], else_=[enter(0, 2)])
    outer = edit.if_("if_latch", then=[inner, enter(2, 2)], else_=[enter(0, 3)])
    pick = edit.random([(8, [enter(1, 1)]), (8, [enter(1, 2), enter(1, 3)]), (8, [])])
    return [outer, pick, end()]


# ---- geometry


def inter(a: Rect, b: Rect, eps: float = 0.01) -> bool:
    return (
        a[0] < b[0] + b[2] - eps
        and b[0] < a[0] + a[2] - eps
        and a[1] < b[1] + b[3] - eps
        and b[1] < a[1] + a[3] - eps
    )


def within(a: Rect, b: Rect, eps: float = 0.01) -> bool:
    return (
        a[0] >= b[0] - eps
        and a[1] >= b[1] - eps
        and a[0] + a[2] <= b[0] + b[2] + eps
        and a[1] + a[3] <= b[1] + b[3] + eps
    )


def runs_through(a: bg.Point, b: bg.Point, r: Rect) -> bool:
    segment = (min(a[0], b[0]), min(a[1], b[1]), abs(a[0] - b[0]), abs(a[1] - b[1]))
    return inter(segment, r, -0.5)


def problems(sc: Scene) -> list[str]:
    """What is wrong with a scene's geometry: nodes that overlap, frames that do not hold their
    nodes or meet another's, wires that are not square or run through a node."""
    out = []
    by = {b.id: b for b in sc.nodes}
    for a, b in itertools.combinations(sc.nodes, 2):
        if inter(a.rect, b.rect):
            out.append(f"nodes {a.id} {b.id} overlap")
    for f in sc.frames:
        out += [f"{i} is out of {f.id}" for i in f.nodes if not within(by[i].rect, f.rect)]
        over = [b.id for b in sc.nodes if b.id not in f.nodes and inter(b.rect, f.rect)]
        out += [f"{i} is over {f.id}" for i in over]
    for f, g in itertools.combinations(sc.frames, 2):
        small, big = (f, g) if len(f.nodes) <= len(g.nodes) else (g, f)
        if set(f.nodes) & set(g.nodes):
            if not within(small.rect, big.rect):
                out.append(f"frame {small.id} sticks out of {big.id}")
        elif inter(f.rect, g.rect):
            out.append(f"frames {f.id} {g.id} overlap")
    for w in sc.wires:
        a, b = by[w.src], by[w.dst]
        if abs(w.points[0][1] - (a.at[1] + a.size[1])) > 0.01:
            out.append(f"wire {w.src} {w.dst} starts off its node")
        if w.kind is not EdgeKind.LOOP and abs(w.points[-1][1] - b.at[1]) > 0.01:
            out.append(f"wire {w.src} {w.dst} ends off its node")
        for p, q in itertools.pairwise(w.points):
            if p[0] != q[0] and p[1] != q[1]:
                out.append(f"wire {w.src} {w.dst} is not square")
            through = [
                n.id for n in sc.nodes if n.id not in (w.src, w.dst) and runs_through(p, q, n.rect)
            ]
            out += [f"wire {w.src} {w.dst} runs through {i}" for i in through]
    return out


def test_a_scope_is_a_column_with_arms_to_its_right() -> None:
    p = toy({"base_0": nested()})
    sc = bg.scene(graph(p))
    assert problems(sc) == []
    outer, pick, close = (n.id for n in first(p).body)
    by = {b.id: b for b in sc.nodes}
    spine = [ENTRY, outer, join_id(outer), pick, join_id(pick), close]
    assert [i for i in by if i in spine] == spine
    assert len({by[i].at[0] for i in spine}) == 1  # one column, top to bottom
    ys = [by[i].at[1] for i in spine]
    assert ys == sorted(ys) and len(set(ys)) == len(ys)
    frames = {f.id: f for f in sc.frames}
    then = frames[head_key(outer)]
    assert then.title == "then" and then.at[0] > by[outer].at[0] + by[outer].size[0]
    assert then.at[1] > by[outer].at[1] + by[outer].size[1]  # under the block
    assert by[join_id(outer)].at[1] >= then.at[1] + then.size[1]  # its join under them
    assert [f.title for f in sc.frames if f.depth == 1] == ["then", "else", "8/32", "8/32"]
    inner = frames[head_key(block(block(first(p).body[0]).head[0]).id)]
    assert inner.depth == 2 and within(inner.rect, then.rect) and set(inner.nodes) < set(then.nodes)
    assert (
        sc.size[1] >= by[close].at[1] + by[close].size[1]
        and sc.size[0] >= then.at[0] + then.size[0]
    )


def test_the_layout_is_the_same_every_time() -> None:
    a = bg.scene(graph(toy({"base_0": nested()})))
    b = bg.scene(graph(toy({"base_0": nested()})))
    assert a == b


def test_ports_and_wires() -> None:
    p = toy({"base_0": nested()})
    sc = bg.scene(graph(p))
    outer, pick, close = (n.id for n in first(p).body)
    by = {b.id: b for b in sc.nodes}
    assert by[ENTRY].inputs == () and by[ENTRY].outputs == (OUT,)
    assert by[outer].inputs == (IN,) and by[outer].outputs == ("p0", "p1")
    assert [(q.name, q.scope is not None) for q in by[outer].ports] == [
        ("then", True),
        ("else", True),
    ]
    ports = by[pick].ports
    assert [q.name for q in ports] == ["8/32"] * 4  # a name may repeat, a key does not
    assert [q.key for q in ports] == ["p0", "p1", "p2", "p3"] and {q.share for q in ports} == {8}
    assert [q.size for q in ports] == [1, 2, 0, 0]
    assert by[join_id(pick)].outputs == (OUT,)
    assert by[close].outputs == () and by[close].terminal
    wires = {(w.src, w.src_port, w.dst, w.dst_port) for w in sc.wires}
    assert (ENTRY, OUT, outer, IN) in wires and (join_id(outer), OUT, pick, IN) in wires
    assert (pick, "p3", join_id(pick), IN) in wires  # an empty branch goes straight to the join
    assert {w.kind for w in sc.wires} == {EdgeKind.NEXT, EdgeKind.PORT, EdgeKind.JOIN}


def test_a_summary_does_not_repeat_the_title() -> None:
    p = toy(
        {"base_0": [edit.insn("target_last_aware"), edit.insn("set_target", 2, 0, 0), enter(3, 4)]}
    )
    got = {b.id: (b.title, b.summary) for b in bg.scene(graph(p)).nodes if b.id != ENTRY}
    assert list(got.values()) == [
        ("Target last aware hunter", ""),
        ("Set target", "Kind 2 (walk stop), A 0, B 0"),
        ("Move", "Move (3, 4)"),
    ]


# ---- collapse


@pytest.mark.parametrize(
    ("count", "collapse", "compact"),
    [(11, 12, False), (12, 12, True), (12, 13, False), (3, 2, True), (0, 0, True)],
)
def test_a_block_over_the_threshold_is_one_node(count: int, collapse: int, compact: bool) -> None:
    b = fit(count)
    p = toy({"base_0": [b, end()]})
    g = graph(p)
    assert g.nodes[b.id].size == count + 1  # type: ignore[union-attr]
    sc = bg.scene(g, ROOT, collapse)
    assert problems(sc) == []
    assert sc.box(b.id).compact is compact
    assert (join_id(b.id) in {x.id for x in sc.nodes}) is not compact
    assert (sc.frames == ()) is (compact or count == 0)
    if not compact:
        return
    box = sc.box(b.id)
    assert box.summary.endswith(f"2 branches · {count + 1} nodes") and box.ports == ()
    assert box.exit and box.outputs == (OUT,) and box.nodes == count + 1
    assert [(o.scope, o.name, o.size) for o in box.opens] == [(head_key(b.id), "then", count)]
    # the wire out of the block is the join's: to what follows, the end
    assert {(w.src, w.dst) for w in sc.wires} == {(ENTRY, b.id), (b.id, f"n{count + 1}")}
    assert not any("#join" in w.src + w.dst for w in sc.wires)


def test_a_compact_block_opens_as_the_scopes_of_its_ports() -> None:
    b = edit.if_("if_latch", then=[enter(1, k) for k in range(20)], else_=[enter(2, 0)])
    p = toy({"base_0": [b, end()]})
    g = graph(p)
    assert [(o.scope, o.name, o.size) for o in bg.scene(g).box(b.id).opens] == [
        (head_key(b.id), "then", 20),
        (b.arms[0].id, "else", 1),
    ]
    inside = bg.scene(g, head_key(b.id))
    assert [c.label for c in inside.crumbs] == ["base_0", "Latch set", "then"]
    assert (
        len(inside.nodes) == 20
        and inside.frames == ()
        and ENTRY not in {x.id for x in inside.nodes}
    )
    assert problems(inside) == [] and [w.kind for w in inside.wires] == [EdgeKind.NEXT] * 19


def test_a_compact_block_carries_what_is_inside() -> None:
    low = edit.insn("refresh_pursuit")
    b = edit.if_("if_latch", then=[enter(), low, *[enter(1, k) for k in range(14)]])
    p = toy({"base_0": [b, end()]})
    edit.modify(p, block(first(p).body[0]).head[0].id, sub=5)
    found = [
        Finding("warning", "W", "w", block(first(p).body[0]).head[1].id, "base_0"),
        Finding("error", "E", "elsewhere", block(first(p).body[0]).head[1].id, "base_1"),
    ]
    g = graph(p)
    sc = bg.scene(g, ROOT, findings=found)
    box = sc.box(b.id)
    assert box.compact and box.inner and box.findings == 1 and box.level == "warning"
    assert not box.edited and box.confidence == cat.Confidence.LOW
    open_ = bg.scene(g, ROOT, collapse=99, findings=found)
    assert not open_.box(b.id).compact and open_.box(b.id).findings == 0
    assert open_.box(low.id).findings == 1 and open_.box(low.id).confidence == cat.Confidence.LOW


# ---- the nodes' flags and badges


def test_findings_count_on_their_nodes() -> None:
    p = toy({"base_0": nested()})
    outer, _, close = first(p).body
    arm = block(outer).arms[0]
    found = [
        Finding("warning", "A", "a", outer.id, "base_0"),
        Finding("error", "B", "b", arm.id, "base_0"),  # an arm: its block's
        Finding("info", "C", "c", None, "base_0"),  # the routine's: the entry's
        Finding("error", "D", "d", close.id, "base_9"),  # another routine's
        Finding("error", "F", "f", "nope", "base_0"),
    ]
    by = {b.id: b for b in bg.scene(graph(p), findings=found).nodes}
    assert (by[outer.id].findings, by[outer.id].level) == (2, "error")
    assert (by[ENTRY].findings, by[ENTRY].level) == (1, "info")
    assert (by[close.id].findings, by[close.id].level) == (0, None)
    assert "error: b" in by[outer.id].tip and "warning: a" in by[outer.id].tip


def test_edited_and_dead_nodes_are_flagged() -> None:
    a, tail, after = enter(0, 1), end(), enter(9, 9)
    p = toy({"base_0": [a, tail, after]})
    edit.modify(p, a.id, sub=4)
    sc = bg.scene(graph(p))
    by = {b.id: b for b in sc.nodes}
    assert by[a.id].edited and not by[a.id].new and by[ENTRY].edited
    assert by[after.id].dead and not by[tail.id].dead and by[tail.id].terminal
    assert not any(w.dst == after.id for w in sc.wires)  # nothing leads to dead code
    plain = bg.scene(graph(toy({"base_0": [enter(), end()]})))
    assert not any(b.edited or b.new or b.dead for b in plain.nodes)
    fresh = enter(5, 5)
    edit.insert(p, tail.id, [fresh])
    assert bg.scene(graph(p)).box(fresh.id).new


def test_links_are_badges_with_names() -> None:
    call, jump = edit.insn("call", 0), edit.insn("goto", 1)
    p = toy(
        {
            "base_0": [call, edit.insn("call", 1), jump],
            "call_0": [end(1)],
            "base_1": [end()],
        },
        {"base": ["base_0", "base_1"], "call": ["call_0", None]},
    )
    g = graph(p)
    sc = bg.scene(g, labels={"call_0": "Roar", "base_1": "Chase"})
    by = {b.id: b for b in sc.nodes}
    (to,) = by[call.id].links
    assert (to.kind, to.list, to.index, to.target) == ("call", "call", 0, "call_0")
    assert to.text == "call to Roar (call_0)" and by[call.id].badge == "→ Roar"
    assert by[jump.id].links[0].text == "goto to Chase (base_1)"
    empty = next(b for b in sc.nodes if b.links and b.links[0].target is None)
    assert empty.links[0].text == "call to call[1]: nothing" and empty.badge == "→ ?"
    assert not any(w.kind is EdgeKind.LINK for w in sc.wires)
    assert by[ENTRY].title == "base_0" and bg.scene(g).crumbs[0].label == "base_0"
    top = bg.scene(g, labels={"base_0": "Top loop"})
    assert top.box(ENTRY).title == top.crumbs[0].label == "Top loop (base_0)"


# ---- two passes, loops


def test_measured_sizes_place_the_nodes_again() -> None:
    first_pass = bg.scene(graph(toy({"base_0": nested()})))
    sizes = {
        b.id: (b.size[0] + 40 * (k % 3), b.size[1] + 30 * (k % 2))
        for k, b in enumerate(first_pass.nodes)
    }
    second = first_pass.placed(sizes)
    assert problems(second) == [] and second.nodes != first_pass.nodes
    assert {b.id: b.size for b in second.nodes} == sizes
    assert second.size[0] > first_pass.size[0] and second.size[1] > first_pass.size[1]
    assert bg.plan(first_pass.plan.rg).place(sizes) == second
    some = first_pass.placed({ENTRY: (400.0, 90.0)})  # a node not drawn yet keeps the estimate
    assert (
        some.box(ENTRY).size == (400.0, 90.0) and some.box("n1").size == first_pass.box("n1").size
    )
    assert problems(some) == []


def test_a_repeat_loops_back() -> None:
    body, empty = edit.repeat(3, [enter(1, 1), enter(1, 2)]), edit.repeat(2, [])
    p = toy({"base_0": [body, empty, enter(), end()]})
    sc = bg.scene(graph(p))
    assert problems(sc) == []
    by = {b.id: b for b in sc.nodes}
    assert by[body.id].outputs == ("p0", OUT) and join_id(body.id) not in by
    loops = {(w.src, w.src_port, w.dst) for w in sc.wires if w.kind is EdgeKind.LOOP}
    assert loops == {("n2", OUT, body.id), (empty.id, "p0", empty.id)}
    assert any(w.src == body.id and w.src_port == OUT and w.dst == empty.id for w in sc.wires)


# ---- selection


def test_the_scope_that_holds_a_node() -> None:
    p = toy({"base_0": nested()})
    outer, pick, close = first(p).body
    inner = block(block(outer).head[0])
    leaf = inner.head[0]
    g = graph(p)
    assert bg.scope_of(g, ENTRY) == bg.scope_of(g, close.id) == bg.scope_of(g, outer.id) == ROOT
    assert bg.scope_of(g, join_id(outer.id)) == ROOT
    assert bg.scope_of(g, inner.id) == head_key(outer.id)
    assert bg.scope_of(g, join_id(inner.id)) == head_key(outer.id)
    assert bg.scope_of(g, leaf.id) == head_key(inner.id)
    arm = block(outer).arms[0].id
    assert bg.scope_of(g, arm) == arm and bg.scope_of(g, head_key(inner.id)) == head_key(inner.id)
    assert bg.scope_of(g, "nope") is None
    # a scene that draws the node already is where to look; a compact block hides what it holds
    assert bg.scope_for(g, leaf.id, ROOT) == ROOT
    assert bg.scope_for(g, leaf.id, ROOT, collapse=2) == head_key(inner.id)
    assert bg.scope_for(g, leaf.id, head_key(outer.id)) == head_key(outer.id)
    assert bg.scope_for(g, close.id, head_key(outer.id)) == ROOT
    assert bg.scope_for(g, "nope") is None


def test_siblings() -> None:
    p = toy({"base_0": nested()})
    outer, pick, close = first(p).body
    g = graph(p)
    assert bg.sibling(g, ENTRY) == outer.id and bg.sibling(g, ENTRY, -1) is None
    assert [bg.sibling(g, outer.id, s) for s in (1, 2, 3, -1)] == [pick.id, close.id, None, None]
    assert bg.sibling(g, close.id) is None and bg.sibling(g, close.id, -2) == outer.id
    moves = block(outer).head
    assert bg.sibling(g, moves[0].id) == moves[1].id and bg.sibling(g, moves[1].id) is None
    assert bg.sibling(g, moves[1].id, -1) == moves[0].id


def test_breadcrumbs() -> None:
    p = toy({"base_0": nested()})
    outer, pick, _ = first(p).body
    inner = block(outer).head[0]
    g = graph(p)
    assert [(c.label, c.scope, c.node) for c in bg.scene(g).crumbs] == [("base_0", ROOT, None)]
    deep = bg.scene(g, head_key(inner.id), labels={"base_0": "Top"}).crumbs
    assert [(c.label, c.scope, c.node) for c in deep] == [
        ("Top (base_0)", ROOT, None),
        ("Latch set", ROOT, outer.id),
        ("then", head_key(outer.id), None),
        ("Enraged", head_key(outer.id), inner.id),
        ("then", head_key(inner.id), None),
    ]
    arm = block(pick).arms[1].id
    assert [(c.label, c.scope) for c in bg.scene(g, arm).crumbs] == [
        ("base_0", ROOT),
        ("Pick one", ROOT),
        ("8/32", arm),
    ]
    with pytest.raises(KeyError):
        bg.scene(g, "nope")


# ---- the outline


def test_the_outline_groups_a_header() -> None:
    ov = Projection(program()).overview()
    out = bg.outline(ov, "h0")
    assert (out.header, out.variants) == ("h0", (0, 1, 2, 3))
    assert [g.name for g in out.groups] == ["Decisions", "Events", "Calls", "Data"]
    by = {g.name: g for g in out.groups}
    assert [r.routine for r in by["Decisions"].rows] == ["r_a", "r_b"]
    assert [r.routine for r in by["Events"].rows] == ["r_i"] and by["Data"].rows == ()
    assert [(x.table, x.names, x.label) for x in by["Decisions"].lists] == [
        ("h0_base_l", ("base",), "base")
    ]
    assert by["Events"].lists[0].label == "int_0x10" and by["Events"].lists[0].title == "On sensed"
    assert [(s.index, s.name, s.kind) for s in by["Calls"].lists[0].slots] == [
        (0, "r_c", "routine"),
        (1, None, "empty"),
    ]
    with pytest.raises(KeyError):
        bg.outline(ov, "h9")


def test_outline_rows_say_what_a_routine_is() -> None:
    p = program()
    found = [
        Finding("warning", "W", "w", "n0", "r_a"),
        Finding("error", "E", "e", None, "r_a"),
        Finding("info", "I", "i", None, "r_b"),
    ]
    ov = Projection(p, edited={"n1"}).overview()
    out = bg.outline(ov, "h0", {"r_a": "Hub", "r_c": "Wait"}, found)
    a, b = out.groups[0].rows
    assert (a.routine, a.label, a.text, a.nodes) == ("r_a", "Hub", "Hub (r_a)", 5)
    assert (b.label, b.text) == ("", "r_b")
    assert (a.findings, a.level, b.findings, b.level) == (2, "error", 1, "info")
    assert (a.edited, b.edited, a.new) == (True, False, False)
    assert (a.entry, a.confidence, b.entry, b.confidence) == ("Top", cat.Confidence.HIGH, "", None)
    assert a.shared and a.variants == (0, 1, 2, 3, 4, 5, 6, 8) and not b.shared
    assert (a.where, b.where) == (("base 0",), ("base 1",))
    (wait,) = out.groups[2].rows
    assert wait.text == "Wait (r_c)" and wait.shared and wait.where == ("call 0",)
    assert [(c.routine, c.sites) for c in wait.callers] == [("r_a", 1)] and wait.reached_from == 1
    assert [(c.routine, c.sites) for c in b.callers] == [("r_a", 1)] and a.reached_from == 0
    assert out.rows("r_a") == [a] and out.rows("nope") == []
    # the same routines as another header runs them: its goto leads elsewhere
    other = bg.outline(ov, "h1")
    assert [r.routine for r in other.groups[0].rows] == ["r_a", "r_d"]
    assert [(c.routine, c.sites) for c in other.rows("r_d")[0].callers] == [("r_a", 1)]


def test_lists_that_name_one_table_are_one() -> None:
    routines: dict[str, list[ir.Node]] = {
        "base_0": [end()],
        "deep0_1": [end(2)],
        "deep0_2": [end(2)],
        "spin": [end(1)],
    }
    lists: dict[str, list[str | None]] = {
        "base": ["base_0"],
        "deep0": [None, "deep0_1", "deep0_2"],
        "deep3": [None, "spin"],
    }
    p = toy(routines, lists)
    fields = p.headers()[0].fields
    fields[FIRST_DEEP + 1] = fields[FIRST_DEEP + 2] = fields[FIRST_DEEP]  # three fields, one table
    deep = next(
        g for g in bg.outline(Projection(p).overview(), "h0").groups if g.name == "Deep calls"
    )
    assert [(x.table, x.fields, x.label, x.title) for x in deep.lists] == [
        ("deep0_l", (FIRST_DEEP, FIRST_DEEP + 1, FIRST_DEEP + 2), "deep0-2", "Deep calls 0-2"),
        ("deep3_l", (FIRST_DEEP + 3,), "deep3", "Deep calls 3"),
    ]
    assert deep.lists[0].names == ("deep0", "deep1", "deep2")
    assert [r.routine for r in deep.rows] == ["deep0_1", "deep0_2", "spin"]
    assert deep.rows[0].where == ("deep0-2 1",) and deep.rows[2].where == ("deep3 1",)


def test_routines_only_links_reach_have_a_group_of_their_own() -> None:
    p = toy({"base_0": [edit.insn("call", ir.Sym("spin")), end()], "spin": [end(1)]})
    out = bg.outline(Projection(p).overview(), "h0")
    assert [(g.name, [r.routine for r in g.rows]) for g in out.groups[-1:]] == [
        (bg.UNLISTED, ["spin"])
    ]
    assert out.groups[-1].lists == ()


# ---- the real game


@pytest.fixture(scope="module")
def game(mhfu_data: Path) -> Extracted:
    return Extracted.find(mhfu_data)


@pytest.fixture(scope="module")
def lift75(game: Extracted) -> ir.Program:
    return brain.decode(game, 75)


@pytest.fixture(scope="module")
def lift01(game: Extracted) -> ir.Program:
    return brain.decode(game, 1)


def every_scope(p: ir.Program, collapse: int = bg.COLLAPSE) -> list[str]:
    """The problems of every scope of every routine."""
    proj = Projection(p)
    out = []
    for r in p.routines():
        g = proj.routine(r.name)
        for key in g.scopes:
            out += [f"{r.name} {key}: {x}" for x in problems(bg.scene(g, key, collapse))]
    return out


def test_every_scope_of_em75_lays_out_clean(lift75: ir.Program) -> None:
    assert every_scope(lift75) == []
    assert every_scope(lift75, 3) == []
    assert every_scope(lift75, 10_000) == []


def test_every_scope_of_em01_lays_out_clean(lift01: ir.Program) -> None:
    assert every_scope(lift01) == []


def test_em75_scopes_fit_a_view(lift75: ir.Program) -> None:
    proj = Projection(lift75)
    shown = sorted(
        len(bg.scene(g, k).nodes)
        for r in lift75.routines()
        for g in [proj.routine(r.name)]
        for k in g.scopes
    )
    assert shown[-1] < 50 and shown[len(shown) // 2] <= 6
    root = bg.scene(proj.routine("call_3"))
    assert [b.compact for b in root.nodes].count(True) >= 1 and len(root.nodes) < 15
    whole = bg.scene(proj.routine("call_3"), collapse=10_000)
    assert len(whole.nodes) > len(root.nodes) and not any(b.compact for b in whole.nodes)


def test_the_em75_outline(lift75: ir.Program) -> None:
    out = bg.outline(Projection(lift75).overview(), "h0", {"base_0": "Top loop"})
    names = ["Decisions", "Events", "Calls", "Deep calls", "Routes", "Loops", "Data"]
    assert [g.name for g in out.groups] == names
    by = {g.name: g for g in out.groups}
    deep = by["Deep calls"]
    merged = deep.lists[0]
    assert (merged.table, merged.fields, merged.label) == ("deep0_l", (15, 16, 17), "deep0-2")
    assert len({x.table for x in deep.lists}) == len(deep.lists) == 7
    top = by["Decisions"].rows[0]
    assert (top.routine, top.text, top.entry, top.where) == (
        "base_0",
        "Top loop (base_0)",
        "Top",
        ("base 0",),
    )
    events = {r.routine: r.entry for r in by["Events"].rows}
    assert events["int_0x20_0"] == "On notice" and events["int_0x08_0"] == "On enrage"
    notice = next(r for r in by["Events"].rows if r.routine == "int_0x20_0")
    assert notice.confidence == cat.Confidence.HIGH and not notice.shared
    assert [len(g.rows) for g in out.groups] == [11, 6, 24, 48, 5, 1, 0]
    assert bg.UNLISTED not in by


def test_an_edit_shows_in_the_graph_and_the_outline(lift75: ir.Program) -> None:
    base = lift75.item("base_0")
    assert isinstance(base, ir.Routine)
    hub = next(n for n in ir.walk(base.body) if isinstance(n, ir.Insn) and n.op.name == "enter")
    es = EditSet.of(lift75)
    set_args(es, lift75, hub.id, main=3, sub=0)
    es.inserts["u1"] = Insert(Anchor("after", hub.id), [edit.if_("if_enraged", then=[enter(3, 0)])])
    built = build.Built.of(lift75, es)
    assert built.ok
    new = {n.id for n in built.program.nodes() if n.id.startswith("u1.")}
    proj = Projection(built.applied, edited=edited_ids(built.applied))
    g = proj.routine("base_0")
    root = bg.scene(g, ROOT, findings=built.findings)
    assert root.box(ENTRY).edited and any(b.inner for b in root.nodes)
    sc = bg.scene(g, bg.scope_of(g, hub.id) or "", findings=built.findings)
    assert sc.box(hub.id).edited and not sc.box(hub.id).new
    assert {b.id for b in sc.nodes if b.new} == new and {b.id for b in sc.nodes if b.edited} == {
        hub.id,
        *new,
    }
    out = bg.outline(proj.overview(), "h0", findings=built.findings)
    assert {r.routine for gr in out.groups for r in gr.rows if r.edited} == {"base_0"}
    assert every_scope(built.program) == []


def best(fn: Callable[[], object], n: int = 5) -> float:
    """The fastest of `n` runs, in ms."""
    times = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t)
    return min(times) * 1000


@pytest.mark.parametrize("fixture", ["lift75", "lift01"])
def test_timings(fixture: str, request: pytest.FixtureRequest) -> None:
    lift: ir.Program = request.getfixturevalue(fixture)
    h = brain.default_header(lift)
    proj = Projection(lift)
    big = max(lift.routines(), key=lambda r: sum(1 for _ in ir.walk(r.body))).name
    g = proj.routine(big)
    one = best(lambda: bg.scene(g, ROOT))
    every = best(lambda: [bg.scene(g, k) for k in g.scopes], 3)
    ov = proj.overview()
    rows = best(lambda: bg.outline(ov, h))
    overview = best(lambda: Projection(lift).overview(), 3)
    print(
        f"\n{fixture}: scene of {big} {one:.1f} ms, its {len(g.scopes)} scopes {every:.0f} ms; "
        f"outline {rows:.1f} ms (the overview itself {overview:.0f} ms)"
    )
    assert one < 100 and rows < 100
