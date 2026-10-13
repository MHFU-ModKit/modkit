# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The trigger outline and a trigger's rows: what each row says, how the rows are laid out, and
how the scope behaves in the panel."""

from __future__ import annotations

from typing import Any

import pytest
from mhfu_port import behaviour as model
from mhfu_port.behaviour import Block, MoveNode, Path, Trigger
from mhfu_port.manifest import Move
from mhfu_studio.monster import behaviour as B
from mhfu_studio.monster.behaviour import ALL, CANVAS, ORDER
from mhfu_studio.monster.document import PortDocument
from mhfu_studio.monster.panels import rows, triggers
from mhfu_studio.monster.panels.behaviour import (
    ORDER_HINT,
    SCOPED,
    SCOPED_HINT,
    UNPLACED,
    WARN,
    BehaviourPanel,
)
from mhfu_studio.monster.panels.node_graph import FRAME_BOTTOM, FRAME_TOP, NodeSpec
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QAction, QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

LEFT, NONE = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier


def rk(*places: int) -> str:
    """Places as the outline writes them, comma-separated."""
    return ", ".join(f"#{k}" for k in places)


def by(place: int, trigger: str) -> str:
    """A line of a move's tip: a rule and its trigger."""
    return f"{rk(place)} \u00b7 {trigger}"


def rng(lo: int, hi: int) -> str:
    return f"#{lo}\u2013#{hi}"


def graph(m: Any) -> None:
    """Six rules: two while charge plays (ranks 1, 2), a flinch of any part (3), a flinch of
    the head (4), a notice (5) and a part break (6); stamp is played by three of them."""
    for name in ("stamp", "stamp_2", "flinch_head", "howl"):
        m.moves[name] = Move(clip="walk")
    b = m.behaviour
    b.blocks["b1"] = Block("played_for", (260.0, 0.0), {"frames": 20}, next=["b2"], label="first")
    b.blocks["b2"] = Block("cooldown", (460.0, 0.0), {"frames": 30}, play=["stamp"])
    b.blocks["b3"] = Block("played_for", (260.0, 100.0), {"frames": 90}, play=["stamp_2"])
    b.blocks["b4"] = Block("on_flinch", (260.0, 200.0), play=["stamp"], label="any flinch")
    b.blocks["b5"] = Block("on_flinch", (260.0, 300.0), {"part": 1}, play=["flinch_head"])
    b.blocks["b6"] = Block("on_noticed", (260.0, 400.0), next=["b7"])
    b.blocks["b7"] = Block("force", (460.0, 400.0), play=["howl"], label="at once")
    b.blocks["b8"] = Block("on_part_broken", (260.0, 500.0), {"part": 1}, play=["stamp"])
    b.moves["charge"] = MoveNode((0.0, 0.0), ["b1", "b3"])
    m.moves["charge"] = Move(clip="walk")
    for k, name in enumerate(("stamp", "stamp_2", "flinch_head", "howl")):
        b.moves[name] = MoveNode((900.0, 160.0 * k))


@pytest.fixture
def ws(workspace: MonsterWorkspace) -> MonsterWorkspace:
    assert workspace.doc is not None
    workspace.doc.edit(graph)
    workspace.sync()
    return workspace


def make(qtbot: Any, ws: MonsterWorkspace) -> BehaviourPanel:
    studio = Studio([ws])
    p = BehaviourPanel(ws, studio)
    studio.listen(p.sync)
    qtbot.addWidget(p)
    p.resize(1100, 640)
    p.show()
    qtbot.waitExposed(p)
    p.sync()
    qtbot.wait(20)
    return p


@pytest.fixture
def panel(qtbot: Any, ws: MonsterWorkspace) -> BehaviourPanel:
    return make(qtbot, ws)


def doc(ws: MonsterWorkspace) -> PortDocument:
    assert ws.doc is not None
    return ws.doc


def pick(qtbot: Any, p: BehaviourPanel, key: str) -> None:
    """A click on the outline's row for `key`."""
    out = p.outline
    QTest.mouseClick(out.viewport(), LEFT, NONE, out.visualItemRect(out.item_of(key)).center())
    qtbot.wait(40)


def shown(p: BehaviourPanel) -> set[str]:
    return set(p.view._shown)


def at(p: BehaviourPanel, node: str) -> list[float]:
    return list(p.view._shown[node].view.xy_pos)


def frames(p: BehaviourPanel) -> list[str]:
    """The frame titles top to bottom."""
    nodes = [n for n in p.view.viewer.all_nodes() if not hasattr(n, "text_item")]
    return [n.name for n in sorted(nodes, key=lambda n: n.xy_pos[1])]


def cells(p: BehaviourPanel) -> dict[str, tuple[str, str, str, str]]:
    """The outline's rows by key: title, count, range, kind."""
    out = p.outline
    return {
        str(out.item(i).data(triggers.KEY)): out.item(i).data(triggers.CELL)
        for i in range(out.count())
        if out.item(i).data(triggers.KEY) is not None
    }


# ---- the outline ----------------------------------------------------------------------------- #


def test_the_outline_lists_all_then_every_trigger_with_its_count_and_range(
    panel: BehaviourPanel,
) -> None:
    rowsby = cells(panel)
    assert rowsby[ALL][0] == "All rules (6)"
    assert rowsby["during:charge"][:3] == ("charge", "2", rng(1, 2))
    assert rowsby["on_flinch"][:3] == ("Flinch", "2", rng(3, 4))
    assert rowsby["on_noticed"][:3] == ("Notice", "1", rk(5))
    assert rowsby["on_part_broken"][:3] == ("Part break", "1", rk(6))
    texts = [panel.outline.item(i).data(triggers.CELL) for i in range(panel.outline.count())]
    heads = [c[0] for c in texts if c[3] == "head"]
    assert heads == ["When", "Between moves", "While a move plays"]


def test_a_trigger_with_no_rules_is_greyed_and_dashed(panel: BehaviourPanel) -> None:
    rowsby = cells(panel)
    for key in ("on_combat_entered", "on_combat_left", "on_tail_cut", "host_state"):
        assert rowsby[key][1:] == ("—", "", "none"), key
    assert [k for k, c in rowsby.items() if c[3] == "none"] == [
        "on_combat_entered",
        "on_combat_left",
        "on_tail_cut",
        "host_state",
    ]
    assert rowsby["host_state"][0] == "In state"


def test_every_row_has_a_tip(panel: BehaviourPanel) -> None:
    out = panel.outline
    for i in range(out.count()):
        item = out.item(i)
        if item.data(triggers.KEY) is not None:
            assert item.toolTip(), item.data(triggers.KEY)
    assert out.item_of("during:charge").toolTip().startswith("The rules that start from")


def test_the_outline_follows_the_document(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    doc(ws).edit(
        lambda m: m.behaviour.blocks.__setitem__(
            "b9", Block("on_tail_cut", (0.0, 600.0), play=["howl"])
        )
    )
    ws.refresh()
    panel.sync()
    rowsby = cells(panel)
    assert rowsby[ALL][0] == "All rules (7)" and rowsby["on_tail_cut"][1:3] == ("1", rk(7))


def test_the_words_of_a_trigger(ws: MonsterWorkspace) -> None:
    assert ws.manifest is not None
    ts = {t.key: t for t in model.triggers(ws.manifest)}
    assert triggers.span(ts["on_flinch"]) == rng(3, 4) and triggers.span(ts["on_noticed"]) == rk(5)
    assert triggers.span(ts["on_tail_cut"]) == ""
    assert triggers.title(ts["on_part_broken"]) == "Part break"
    assert triggers.says(ts["on_part_broken"], 6) == f"All › Part break — 1 rule, {rk(6)} of 6"
    assert triggers.says(ts["during:charge"], 6) == (
        f"All › While charge plays — 2 rules, {rng(1, 2)} of 6"
    )
    assert triggers.says(ts["on_tail_cut"], 6) == "All › Tail cut — no rules"
    assert triggers.owner(list(ts.values()), {}, "b7") == "on_noticed"
    assert triggers.owner(list(ts.values()), {}, "nope") is None


# ---- the rows -------------------------------------------------------------------------------- #


def spec(i: str, title: str = "x", **kw: Any) -> NodeSpec:
    return NodeSpec(i, title, "condition", (7.0, 7.0), ("in",), ("out",), **kw)


def trigger(*paths: Path, section: Any = "event", name: str = "") -> Trigger:
    held = tuple((k, p) for k, p in enumerate(paths, 1))
    return Trigger("t", section, "on_noticed", name, held)


BLOCKS = {i: spec(i, f"B{i}", badge=f"{rk(1)} {WARN}") for i in ("b1", "b2", "b3")}
NOTES = {"b1": "go", "b2": "", "b3": "then"}
MOVES = {"m": spec("move:m", "m"), "n": spec("move:n", "n")}


def test_node_of_maps_a_displayed_node_back(ws: MonsterWorkspace) -> None:
    shown = ("b1", "b1~2", "ref:b3:stamp", "ref:b3~2:stamp", "open:b3", "frame:4", "move:charge")
    assert [rows.node_of(i) for i in shown] == [
        "b1",
        "b1",
        "move:stamp",
        "move:stamp",
        "",
        "",
        "move:charge",
    ]
    assert [rows.ref_of(i) for i in shown] == [
        None,
        None,
        ("b3", "stamp"),
        ("b3", "stamp"),
        ("b3", None),
        None,
        None,
    ]
    assert ws.manifest is not None
    assert rows.players(model.paths(ws.manifest)) == {
        "stamp": (1, 3, 6),
        "stamp_2": (2,),
        "flinch_head": (4,),
        "howl": (5,),
    }


def test_a_rule_is_a_row_of_its_blocks_and_the_move_it_plays() -> None:
    t = trigger(Path(("b1", "b2"), "m", None), Path(("b3",), "m", None))
    plan = rows.plan(t, BLOCKS, NOTES, MOVES, {"m": (1, 2, 9)}, tips={"m": "m's tip"})
    assert [r.title for r in plan.rows] == [f"{rk(1)} · go", f"{rk(2)} · then"]
    assert [[n.id for n in r.nodes] for r in plan.rows] == [
        ["b1", "b2", "ref:b2:m"],
        ["b3", "ref:b3:m"],
    ]
    ref = plan.rows[0].nodes[-1]
    assert (ref.title, ref.ref, ref.badge) == ("▸ m", True, "×3")
    assert ref.tip == "m's tip"
    assert {(w.src, w.dst) for w in plan.links} == {
        ("b1", "b2"),
        ("b2", "ref:b2:m"),
        ("b3", "ref:b3:m"),
    }
    assert all(n.badge == WARN for n in plan.rows[0].nodes[:2]), "the rank is on the frame"


def test_a_move_played_by_one_rule_has_no_count() -> None:
    plan = rows.plan(trigger(Path(("b1",), "m", None)), BLOCKS, NOTES, MOVES, {"m": (1,)})
    ref = plan.rows[0].nodes[-1]
    assert ref.badge == "" and ref.tip == "m", "the move's name when nothing says more"


def test_a_rule_with_no_note_says_so() -> None:
    plan = rows.plan(trigger(Path(("b2",), "m", None)), BLOCKS, NOTES, MOVES, {})
    assert plan.rows[0].title == f"{rk(1)} · (no note)"


def test_a_rule_with_no_move_yet_ends_in_a_placeholder_after_the_others() -> None:
    open_rule = Path(("b1", "b2"), "", None)
    t = trigger(Path(("b3",), "m", None))
    plan = rows.plan(t, BLOCKS, NOTES, MOVES, {"m": (1,)}, [open_rule])
    assert [r.rank for r in plan.rows] == [1, None]
    assert [r.id for r in plan.rows] == ["frame:1", "frame:new:b1:1"]
    last = plan.rows[1]
    assert [n.id for n in last.nodes] == ["b1", "b2", "open:b2"]
    assert last.title == "unfinished \u00b7 go" and last.path == open_rule
    end = last.nodes[-1]
    assert (end.title, end.ref) == ("\u25b8 plays\u2026", True) and end.tip
    assert ("b2", "open:b2") in {(w.src, w.dst) for w in plan.links}
    assert plan.row_of("open:b2") is last and plan.row_of("frame:1") is plan.rows[0]
    assert plan.row_of("nope") is None


def test_a_rule_that_plays_nothing_by_design_ends_at_its_last_block() -> None:
    plan = rows.plan(trigger(Path(("b1", "b2"), "", None)), BLOCKS, NOTES, MOVES, {})
    assert [n.id for n in plan.rows[0].nodes] == ["b1", "b2"] and len(plan.links) == 1
    assert plan.rows[0].rank == 1


def test_a_while_trigger_starts_from_its_move_once() -> None:
    t = trigger(Path(("b1",), "n", "m"), Path(("b3",), "n", "m"), section="move", name="m")
    plan = rows.plan(t, BLOCKS, NOTES, MOVES, {"n": (1, 2)})
    assert plan.start is not None and plan.start.id == "move:m"
    assert [n.id for n in plan.nodes].count("move:m") == 1
    wired = {(w.src, w.src_port, w.dst) for w in plan.links if w.src == "move:m"}
    assert wired == {("move:m", "while playing", "b1"), ("move:m", "while playing", "b3")}


def test_a_block_on_two_rules_is_drawn_twice_and_the_copy_is_faded() -> None:
    t = trigger(Path(("b1", "b2"), "m", None), Path(("b1", "b3"), "m", None))
    plan = rows.plan(t, BLOCKS, NOTES, MOVES, {})
    ids = [n.id for r in plan.rows for n in r.nodes]
    assert ids.count("b1") == 1 and "b1~2" in ids
    copy = next(n for r in plan.rows for n in r.nodes if n.id == "b1~2")
    assert copy.dim and not next(n for n in plan.rows[0].nodes if n.id == "b1").dim


def sizes(plan: rows.Plan) -> dict[str, tuple[float, float]]:
    return {n.id: (168.0, 60.0 + 13 * (k % 2)) for k, n in enumerate(plan.nodes)}


def test_place_stacks_the_rows_in_rank_order_and_runs_them_left_to_right() -> None:
    t = trigger(Path(("b1", "b2"), "m", None), Path(("b3",), "n", None), Path(("b2",), "m", None))
    plan = rows.plan(t, BLOCKS, NOTES, MOVES, {})
    size = sizes(plan)
    nodes, frames = rows.place(plan, size)
    by = {n.id: n for n in nodes}
    tops = [by[r.nodes[0].id].at[1] for r in plan.rows]
    assert tops == sorted(tops) and len(set(tops)) == 3
    for r in plan.rows:
        xs = [by[n.id].at[0] for n in r.nodes]
        assert xs == sorted(xs) and len(set(xs)) == len(xs)
        gaps = [xs[k + 1] - xs[k] - size[r.nodes[k].id][0] for k in range(len(xs) - 1)]
        assert gaps == [rows.GAP_X] * len(gaps)
    assert [f.title for f in frames] == [r.title for r in plan.rows]
    for above, below in zip(plan.rows, plan.rows[1:], strict=False):  # frames do not touch
        bottom = by[above.nodes[0].id].at[1] + max(size[n.id][1] for n in above.nodes)
        assert by[below.nodes[0].id].at[1] - FRAME_TOP >= bottom + FRAME_BOTTOM + rows.GAP_Y - 1e-6


def test_place_puts_a_move_trigger_left_of_the_rows_and_level_with_their_middle() -> None:
    t = trigger(Path(("b1",), "n", "m"), Path(("b3",), "n", "m"), section="move", name="m")
    plan = rows.plan(t, BLOCKS, NOTES, MOVES, {})
    size = sizes(plan)
    nodes, _ = rows.place(plan, size)
    by = {n.id: n for n in nodes}
    start = by["move:m"]
    assert start.at[0] == 0.0 and all(
        n.at[0] > start.at[0] + size["move:m"][0] for n in nodes if n is not start
    )
    first, last = by["b1"], by["b3"]
    middle = (first.at[1] + last.at[1] + size["b3"][1]) / 2
    assert abs(start.at[1] + size["move:m"][1] / 2 - middle) < 40


def test_place_leaves_the_specs_it_was_given_alone() -> None:
    t = trigger(Path(("b1",), "m", None))
    plan = rows.plan(t, BLOCKS, NOTES, MOVES, {})
    before = [n.at for n in plan.nodes]
    rows.place(plan, sizes(plan))
    assert [n.at for n in plan.nodes] == before


# ---- the scope in the panel ------------------------------------------------------------------ #


def to_canvas(qtbot: Any, panel: BehaviourPanel) -> None:
    """The Canvas half of the header's toggle, clicked."""
    QTest.mouseClick(panel.mode.buttons[CANVAS], LEFT, NONE)
    qtbot.wait(60)


def to_order(qtbot: Any, panel: BehaviourPanel) -> None:
    QTest.mouseClick(panel.mode.buttons[ORDER], LEFT, NONE)
    qtbot.wait(60)


def test_all_canvas_is_the_stored_canvas_unchanged(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    to_canvas(qtbot, panel)
    m = doc(ws).manifest
    assert panel.ws.scope == ALL and ws.all_view == CANVAS
    assert shown(panel) == {*m.behaviour.blocks, *map(B.move_id, m.moves)}
    assert at(panel, "b2") == [460.0, 0.0] and not panel.spread.isHidden()
    assert panel.title.text().startswith("8 blocks · 6 of")


def test_a_trigger_shows_only_its_rules_in_rank_order(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    assert ws.scope == "on_flinch" and shown(panel) == {
        "b4",
        "b5",
        "ref:b4:stamp",
        "ref:b5:flinch_head",
    }
    assert frames(panel) == [f"{rk(3)} · any flinch", f"{rk(4)} · (no note)"]
    assert panel.title.text() == f"All › Flinch — 2 rules, {rng(3, 4)} of 6"
    assert panel.spread.isHidden() and panel.hint.text() == SCOPED_HINT
    assert panel.view._shown["ref:b4:stamp"].view.badge == "×3"
    assert panel.view._shown["ref:b4:stamp"].view.toolTip().splitlines() == [
        "stamp \u00b7 played by 3 rules",
        by(1, "while charge"),
        by(3, "Flinch"),
        by(6, "Part break"),
    ]
    pick(qtbot, panel, ALL)
    assert ws.scope == ALL and "b8" in shown(panel) and panel.spread.isHidden()


def test_a_while_trigger_draws_its_move_once_wired_into_every_row(
    qtbot: Any, panel: BehaviourPanel
) -> None:
    pick(qtbot, panel, "during:charge")
    assert B.move_id("charge") in shown(panel)
    assert {"b1", "b2", "b3", "ref:b2:stamp", "ref:b3:stamp_2"} <= shown(panel)
    wires = {(w.src, w.dst) for w in panel.view._links}
    assert wires >= {(B.move_id("charge"), "b1"), (B.move_id("charge"), "b3"), ("b1", "b2")}
    assert frames(panel) == [f"{rk(1)} · first", f"{rk(2)} · (no note)"]
    first = panel.view._shown["b1"].view
    start = panel.view._shown[B.move_id("charge")].view
    assert start.xy_pos[0] + start.width < first.xy_pos[0]


def test_an_empty_trigger_shows_nothing_and_says_so(qtbot: Any, panel: BehaviourPanel) -> None:
    pick(qtbot, panel, "on_tail_cut")
    assert shown(panel) == set() and frames(panel) == []
    assert panel.title.text() == "All › Tail cut — no rules"


def test_a_scope_changes_fit_the_canvas(qtbot: Any, panel: BehaviourPanel) -> None:
    pick(qtbot, panel, "on_noticed")
    seen = panel.view.viewer.mapToScene(panel.view.viewer.viewport().rect()).boundingRect()
    assert all(seen.contains(s.view.sceneBoundingRect()) for s in panel.view._shown.values())
    panel.view.viewer.set_zoom(0.2)
    pick(qtbot, panel, "on_part_broken")
    seen = panel.view.viewer.mapToScene(panel.view.viewer.viewport().rect()).boundingRect()
    assert all(seen.contains(s.view.sceneBoundingRect()) for s in panel.view._shown.values())
    assert panel.view.viewer.transform().m11() > 0.5


def test_the_scope_survives_a_sync_and_a_rebuilt_panel(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "during:charge")
    before = shown(panel)
    panel.sync()
    ws.sync()
    panel.sync()
    assert ws.scope == "during:charge" and shown(panel) == before
    again = make(qtbot, ws)
    assert (
        shown(again) == before and again.outline.currentItem().data(triggers.KEY) == "during:charge"
    )


def test_a_scope_that_is_gone_falls_back_to_all(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "during:charge")
    doc(ws).edit(lambda m: setattr(m.behaviour.moves["charge"], "during", []))
    ws.refresh()
    panel.sync()
    assert ws.scope == ALL and "b8" in shown(panel)


def test_a_finding_on_a_block_outside_the_scope_switches_to_its_trigger(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    ws.reveal(("block", "b8"), "behaviour")
    panel.sync()
    assert ws.scope == "on_part_broken" and "b8" in shown(panel)
    assert panel.view.selected() == ["b8"]
    assert panel.outline.currentItem().data(triggers.KEY) == "on_part_broken"
    ws.reveal(("block", "b8"), "behaviour")
    panel.sync()
    assert ws.scope == "on_part_broken", "a block in the scope keeps it"


def test_a_finding_on_a_loose_block_switches_to_all(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    doc(ws).edit(
        lambda m: m.behaviour.blocks.__setitem__(
            "b9", Block("cooldown", (0.0, 700.0), {"frames": 5})
        )
    )
    ws.refresh()
    panel.sync()
    pick(qtbot, panel, "on_flinch")
    ws.reveal(("block", "b9"), "behaviour")
    panel.sync()
    assert ws.scope == ALL and panel.view.selected() == ["b9"] and ws.all_view == CANVAS


def test_a_move_picked_elsewhere_does_not_switch_the_scope(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    ws.select_move("stamp")
    panel.sync()
    assert ws.scope == "on_flinch"


def test_a_drag_in_a_scope_writes_nothing_and_snaps_back(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    before, here = doc(ws).manifest, at(panel, "b4")
    item = panel.view._shown["b4"].view
    a = panel.view.viewer.mapFromScene(item.mapToScene(QPointF(item.width / 2, 8)))
    vp = panel.view.viewer.viewport()
    QTest.mousePress(vp, LEFT, NONE, a)
    for k in range(1, 6):
        QTest.mouseMove(vp, a + QPoint(10 * k, 14 * k))
    QTest.mouseRelease(vp, LEFT, NONE, a + QPoint(50, 70))
    qtbot.wait(60)
    assert doc(ws).manifest is before and at(panel, "b4") == here


def port_at(p: BehaviourPanel, node: str, port: str) -> QPoint:
    s = p.view._shown[node]
    item = s.outs[port] if port in s.outs else s.ins[port]
    return p.view.viewer.mapFromScene(item.mapToScene(item.boundingRect().center()))


def drag(p: BehaviourPanel, a: QPoint, b: QPoint) -> None:
    vp = p.view.viewer.viewport()
    QTest.mousePress(vp, LEFT, NONE, a)
    for k in range(1, 6):
        QTest.mouseMove(vp, a + (b - a) * (k / 5))
    QTest.mouseRelease(vp, LEFT, NONE, b)


def test_a_trigger_has_no_free_block_menu_and_all_has_it_back(
    qtbot: Any, panel: BehaviourPanel
) -> None:
    menu = panel.view.viewer.context_menus()["graph"]
    assert menu.actions() == [], "the rows of All take no free blocks either"
    to_canvas(qtbot, panel)
    assert len(menu.actions()) == 4
    pick(qtbot, panel, "on_flinch")
    assert menu.actions() == []
    pick(qtbot, panel, ALL)
    assert [a.text() for a in menu.actions()] == ["Events", "State", "Conditions", "Modifiers"]


def test_wiring_in_a_scope_is_refused_with_the_hint(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    before = doc(ws).manifest
    drag(panel, port_at(panel, "b4", "out"), port_at(panel, "b5", "in"))  # a block into a block
    qtbot.wait(40)
    assert doc(ws).manifest is before and ws.message == SCOPED
    assert {(w.src, w.dst) for w in panel.view._links} == {
        ("b4", "ref:b4:stamp"),
        ("b5", "ref:b5:flinch_head"),
    }
    ws.message = ""
    pipe = panel.view._pipes()[next(w for w in panel.view._links if w.src == "b4")]
    a = panel.view.viewer.mapFromScene(pipe.path().pointAtPercent(0.25))
    drag(panel, a, a + QPoint(0, 120))  # a wire pulled off
    qtbot.wait(40)
    assert doc(ws).manifest is before and ws.message == SCOPED


def test_delete_still_works_in_a_scope_and_a_reference_is_its_move(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    panel.view.select(["b4", "ref:b4:stamp"])
    QTest.keyClick(panel.view, Qt.Key.Key_Delete)
    qtbot.wait(40)
    blocks = doc(ws).manifest.behaviour.blocks
    assert "b4" not in blocks and "stamp" in doc(ws).manifest.moves
    assert "b4" not in shown(panel)


def test_picking_a_reference_shows_the_real_move_with_its_note_in_the_bar(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    doc(ws).edit(lambda m: setattr(m.moves["stamp"], "label", "the stamp"))
    ws.refresh()
    panel.sync()
    pick(qtbot, panel, "on_flinch")
    ref = panel.view._shown["ref:b4:stamp"].view
    QTest.mouseClick(
        panel.view.viewer.viewport(),
        LEFT,
        NONE,
        panel.view.viewer.mapFromScene(ref.mapToScene(QPointF(ref.width / 2, 8))),
    )
    qtbot.wait(60)
    assert panel.view.selected() == ["ref:b4:stamp"] and ws.move == "stamp"
    assert panel.bar.note is not None and panel.bar.note.get_value() == "the stamp"
    panel.bar.note.ctl.setText("again")
    panel.bar.note.ctl.editingFinished.emit()
    qtbot.wait(40)
    assert doc(ws).manifest.moves["stamp"].label == "again"


def test_a_bar_edit_in_a_scope_redraws_the_rows_and_keeps_the_scope(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "during:charge")
    panel.view.select(["b1"])
    panel.sync()
    panel.bar.controls["frames"].ctl.setValue(45)
    qtbot.wait(60)
    assert doc(ws).manifest.behaviour.blocks["b1"].params == {"frames": 45}
    assert (
        ws.scope == "during:charge" and panel.view._shown["b1"].view.summary == "45 frames or more"
    )


def test_a_scope_writes_no_block_position(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    before = doc(ws).manifest
    for key in ("during:charge", "on_flinch", "on_noticed", "on_part_broken", ALL):
        pick(qtbot, panel, key)
        panel.view.fit()
    assert doc(ws).manifest is before, "looking is not an edit"


# ---- editing in a trigger's rows -------------------------------------------------------------- #


def node_pos(p: BehaviourPanel, node: str) -> QPoint:
    item = p.view._shown[node].view
    return p.view.viewer.mapFromScene(item.mapToScene(QPointF(item.width / 2, 8)))


def frame_pos(p: BehaviourPanel, frame: str) -> QPoint:
    item = p.view._backdrops[frame].view
    return p.view.viewer.mapFromScene(item.mapToScene(QPointF(item.width / 2, 12)))


def click(qtbot: Any, p: BehaviourPanel, where: QPoint) -> None:
    QTest.mouseClick(p.view.viewer.viewport(), LEFT, NONE, where)
    qtbot.wait(60)


def right_click(qtbot: Any, p: BehaviourPanel, node: str) -> QMenu:
    """The menu a right-click on `node` offers, shown and closed again."""
    got: list[QMenu] = []
    p.view.menu_requested.connect(lambda _i, m: got.append(m))
    vp, where = p.view.viewer.viewport(), node_pos(p, node)
    QTest.mouseMove(vp, where + QPoint(3, 3))  # the viewer takes its place from the last move
    QTest.mouseMove(vp, where)

    def close() -> None:
        pop = QApplication.activePopupWidget()
        while pop is not None:
            pop.close()
            pop = QApplication.activePopupWidget()

    QTimer.singleShot(150, close)
    QApplication.sendEvent(
        vp, QContextMenuEvent(QContextMenuEvent.Reason.Mouse, where, vp.mapToGlobal(where))
    )
    qtbot.wait(40)
    return got[-1]


def entry(menu: QMenu, *path: str) -> QAction:
    """The action at `path` in `menu`'s submenus."""
    for name in path[:-1]:
        [menu] = [a.menu() for a in menu.actions() if a.menu() is not None and a.text() == name]
    [act] = [a for a in menu.actions() if a.text() == path[-1]]
    return act


def names(menu: QMenu, *path: str) -> list[str]:
    """The texts of the actions of the submenu at `path` of `menu`."""
    for name in path:
        [menu] = [a.menu() for a in menu.actions() if a.menu() is not None and a.text() == name]
    return [a.text() for a in menu.actions()]


def blocks(ws: MonsterWorkspace) -> dict[str, Block]:
    return doc(ws).manifest.behaviour.blocks


def test_plus_rule_is_for_a_trigger_and_adds_a_rule_checked_last(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    assert panel.add_rule.isHidden()
    pick(qtbot, panel, "on_flinch")
    assert (
        not panel.add_rule.isHidden()
        and "checked after every other rule" in panel.add_rule.toolTip()
    )
    before = doc(ws).manifest
    panel.add_rule.click()
    qtbot.wait(60)
    new = blocks(ws)["b9"]
    assert (new.kind, new.params, new.play) == ("on_flinch", {}, [])
    assert new.at[1] > max(b.at[1] for i, b in blocks(ws).items() if i != "b9")
    assert model.paths(doc(ws).manifest) == model.paths(before), "no move yet: not a rule yet"
    assert panel.view.selected() == ["b9"] and "b9" in shown(panel) and "open:b9" in shown(panel)
    assert frames(panel)[-1] == "unfinished · (no note)"
    assert list(panel.bar.controls) == ["part"] and panel.bar.note is not None
    assert panel.outline.item_of("on_flinch").data(triggers.CELL)[1] == "2", "it does not run yet"
    doc(ws).undo()
    ws.refresh()
    panel.sync()
    assert doc(ws).manifest == before and "b9" not in shown(panel)


def test_plus_rule_in_a_while_trigger_hangs_from_the_moves_port(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "during:charge")
    panel.add_rule.click()
    qtbot.wait(60)
    assert blocks(ws)["b9"].kind == "played_for"
    assert doc(ws).manifest.behaviour.moves["charge"].during == ["b1", "b3", "b9"]
    start = B.move_id("charge")
    assert (start, "b9") in {(w.src, w.dst) for w in panel.view._links}


def test_the_placeholder_picks_the_move_and_the_new_rule_ranks_last(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    panel.add_rule.click()
    qtbot.wait(60)
    click(qtbot, panel, node_pos(panel, "open:b9"))
    assert panel.bar.plays is not None and panel.bar.play is None and panel.bar.note is None
    ctl = panel.bar.plays.ctl
    assert [ctl.itemText(i) for i in range(ctl.count())][:2] == ["pick a move", "charge"]
    ctl.setCurrentIndex(ctl.findText("howl"))
    qtbot.wait(60)
    assert blocks(ws)["b9"].play == ["howl"]
    assert [r.play for r in model.compile(doc(ws).manifest)][-1] == "howl"
    assert panel.view.selected() == ["ref:b9:howl"]
    assert panel.outline.item_of("on_flinch").data(triggers.CELL)[1:3] == ("3", rng(3, 7))
    assert frames(panel)[-1] == f"{rk(7)} · (no note)"


def test_the_plays_picker_of_a_reference_retargets_or_empties_it(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    click(qtbot, panel, node_pos(panel, "ref:b4:stamp"))
    assert panel.bar.plays is not None and panel.bar.play is not None
    assert panel.bar.plays.get_value() == "stamp"
    ctl = panel.bar.plays.ctl
    ctl.setCurrentIndex(ctl.findText("howl"))
    qtbot.wait(60)
    assert blocks(ws)["b4"].play == ["howl"] and panel.view.selected() == ["ref:b4:howl"]
    assert panel.bar.plays.get_value() == "howl"
    panel.bar.plays.ctl.setCurrentIndex(0)  # nothing
    qtbot.wait(60)
    assert blocks(ws)["b4"].play == [] and panel.view.selected() == ["open:b4"]
    assert [f for f in frames(panel) if f.startswith("unfinished")] == [
        "unfinished \u00b7 any flinch"
    ]
    doc(ws).undo()
    ws.refresh()
    panel.sync()
    assert blocks(ws)["b4"].play == ["howl"]


def test_the_insert_menu_offers_a_rules_blocks_and_puts_one_after_the_clicked(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "during:charge")
    menu = right_click(qtbot, panel, "b1")
    after = entry(menu, "Insert after", "Conditions", "Hunter distance")
    assert names(menu, "Insert after", "Conditions")[:1] == ["Played for at least"]
    assert {a.text() for a in menu.actions()} == {"Insert after", "Delete the rule"}
    assert names(menu, "Insert after") == ["Conditions", "Modifiers"]
    assert entry(menu, "Delete the rule") is not None
    before = doc(ws).manifest
    after.trigger()
    qtbot.wait(60)
    b = blocks(ws)
    assert b["b1"].next == ["b9"] and b["b9"].kind == "distance" and b["b9"].next == ["b2"]
    assert panel.view.selected() == ["b9"]
    assert [p.blocks for p in model.paths(doc(ws).manifest)][0] == ("b1", "b9", "b2")
    doc(ws).undo()
    ws.refresh()
    panel.sync()
    assert doc(ws).manifest == before


def test_the_menu_of_the_last_block_and_of_a_reference_insert_before_the_play(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    entry(right_click(qtbot, panel, "b4"), "Insert after", "Modifiers", "At most").trigger()
    qtbot.wait(60)
    b = blocks(ws)
    assert b["b4"].next == ["b9"] and b["b4"].play == [] and b["b9"].play == ["stamp"]
    menu = right_click(qtbot, panel, "ref:b9:stamp")
    assert {a.text() for a in menu.actions()} == {
        "Insert before the play",
        "Delete the play",
        "Play in game",
    }
    entry(menu, "Insert before the play", "Modifiers", "Then wait").trigger()
    qtbot.wait(60)
    b = blocks(ws)
    assert b["b9"].next == ["b10"] and b["b10"].play == ["stamp"] and b["b9"].play == []


def test_the_menu_of_the_placeholder_inserts_and_has_no_play(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    panel.add_rule.click()
    qtbot.wait(60)
    menu = right_click(qtbot, panel, "open:b9")
    assert {a.text() for a in menu.actions()} == {"Insert before the play"}
    entry(menu, "Insert before the play", "Modifiers", "At most").trigger()
    qtbot.wait(60)
    assert blocks(ws)["b9"].next == ["b10"] and blocks(ws)["b10"].play == []
    assert "open:b10" in shown(panel)


def three(qtbot: Any, panel: BehaviourPanel) -> None:
    """Rule 1 of while-charge made b1 -> b9 -> b2."""
    pick(qtbot, panel, "during:charge")
    entry(
        right_click(qtbot, panel, "b1"), "Insert after", "Conditions", "Hunter distance"
    ).trigger()
    qtbot.wait(60)


def test_deleting_a_block_closes_the_chain_up(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    three(qtbot, panel)
    mid = doc(ws).manifest
    click(qtbot, panel, node_pos(panel, "b9"))
    QTest.keyClick(panel.view, Qt.Key.Key_Delete)
    qtbot.wait(60)
    b = blocks(ws)
    assert "b9" not in b and b["b1"].next == ["b2"] and b["b2"].play == ["stamp"]
    assert [p.blocks for p in model.paths(doc(ws).manifest)][0] == ("b1", "b2")
    doc(ws).undo()
    ws.refresh()
    panel.sync()
    assert doc(ws).manifest == mid


def test_deleting_the_last_block_gives_its_play_back_to_the_one_before(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    three(qtbot, panel)
    click(qtbot, panel, node_pos(panel, "b2"))
    QTest.keyClick(panel.view, Qt.Key.Key_Delete)
    qtbot.wait(60)
    b = blocks(ws)
    assert "b2" not in b and b["b9"].play == ["stamp"] and b["b9"].next == []
    assert "ref:b9:stamp" in shown(panel)


def test_deleting_the_first_block_or_the_frame_deletes_the_rule(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    three(qtbot, panel)
    before = doc(ws).manifest
    click(qtbot, panel, node_pos(panel, "b1"))
    QTest.keyClick(panel.view, Qt.Key.Key_Delete)
    qtbot.wait(60)
    b = blocks(ws)
    assert not {"b1", "b9", "b2"} & set(b) and doc(ws).manifest.behaviour.moves[
        "charge"
    ].during == ["b3"]
    assert frames(panel) == [f"{rk(2)} · (no note)"] or len(frames(panel)) == 1
    doc(ws).undo()
    ws.refresh()
    panel.sync()
    assert doc(ws).manifest == before
    click(qtbot, panel, frame_pos(panel, "frame:2"))
    assert panel.view.selected_frames() == ["frame:2"]
    QTest.keyClick(panel.view, Qt.Key.Key_Delete)
    qtbot.wait(60)
    assert "b3" not in blocks(ws) and "b1" in blocks(ws)


def test_deleting_a_reference_takes_only_the_play(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    click(qtbot, panel, node_pos(panel, "ref:b4:stamp"))
    QTest.keyClick(panel.view, Qt.Key.Key_Delete)
    qtbot.wait(60)
    assert blocks(ws)["b4"].play == [] and "stamp" in doc(ws).manifest.moves
    assert "open:b4" in shown(panel)
    doc(ws).undo()
    ws.refresh()
    panel.sync()
    assert blocks(ws)["b4"].play == ["stamp"]


def test_the_menu_deletes_a_block_or_a_play(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    three(qtbot, panel)
    entry(right_click(qtbot, panel, "b9"), "Delete the block").trigger()
    qtbot.wait(60)
    assert "b9" not in blocks(ws)
    entry(right_click(qtbot, panel, "ref:b2:stamp"), "Delete the play").trigger()
    qtbot.wait(60)
    assert blocks(ws)["b2"].play == [] and panel.view.selected() == ["open:b2"]


def test_a_frame_shows_the_rules_note_and_edits_it(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    click(qtbot, panel, frame_pos(panel, "frame:3"))
    assert panel.view.selected_frames() == ["frame:3"]
    assert panel.bar.note is not None and panel.bar.note.get_value() == "any flinch"
    title = [w.text() for w in panel.bar.findChildren(type(panel.title))][0]
    assert title == f"Rule {rk(3)}" and not panel.bar.controls
    panel.bar.note.ctl.setText("the flinch rule")
    panel.bar.note.ctl.editingFinished.emit()
    qtbot.wait(60)
    assert blocks(ws)["b4"].label == "the flinch rule"
    assert frames(panel)[0] == f"{rk(3)} · the flinch rule"
    assert panel.view.selected_frames() == ["frame:3"], "the frame stays selected"
    assert panel.bar.note.get_value() == "the flinch rule"


def test_a_block_note_is_not_the_rules_note_but_joins_it(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "during:charge")
    entry(right_click(qtbot, panel, "b1"), "Insert after", "Modifiers", "At most").trigger()
    qtbot.wait(60)
    assert panel.bar.note is not None
    panel.bar.note.ctl.setText("limit it")
    panel.bar.note.ctl.editingFinished.emit()
    qtbot.wait(60)
    assert frames(panel)[0] == f"{rk(1)} · first; limit it"


# ---- order and play ------------------------------------------------------------------------- #


def drag_frame(qtbot: Any, p: BehaviourPanel, frame: str, onto: str, below: bool = True) -> None:
    """A frame's title dragged to the title of another frame, a little past it."""
    vp, a = p.view.viewer.viewport(), frame_pos(p, frame)
    b = frame_pos(p, onto) + QPoint(0, 24 if below else -24)
    QTest.mousePress(vp, LEFT, NONE, a)
    for k in range(1, 8):
        QTest.mouseMove(vp, a + (b - a) * (k / 7))
    QTest.mouseRelease(vp, LEFT, NONE, b)
    qtbot.wait(80)


def order(ws: MonsterWorkspace) -> list[str]:
    return [r.play for r in model.compile(doc(ws).manifest)]


def test_dragging_a_frame_down_swaps_two_ranks_and_leaves_the_rest(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    before, rules = doc(ws).manifest, model.compile(doc(ws).manifest)
    assert order(ws) == ["stamp", "stamp_2", "stamp", "flinch_head", "howl", "stamp"]
    drag_frame(qtbot, panel, "frame:3", "frame:4")
    after = model.compile(doc(ws).manifest)
    assert after[2] == rules[3] and after[3] == rules[2]
    assert after[:2] == rules[:2] and after[4:] == rules[4:], "no other rule changes its place"
    assert frames(panel) == [f"{rk(3)} · (no note)", f"{rk(4)} · any flinch"]
    assert ws.message.startswith("rule moved")
    b = blocks(ws)
    assert (b["b4"].at[1], b["b5"].at[1]) == (300.0, 200.0), "the places were handed round"
    assert panel.view.selected_frames() == ["frame:4"], "the frame that was dragged stays picked"
    assert panel.bar.note is not None and panel.bar.note.get_value() == "any flinch"
    doc(ws).undo()
    ws.refresh()
    panel.sync()
    assert doc(ws).manifest == before and model.compile(doc(ws).manifest) == rules


def test_dragging_a_frame_up_takes_the_place_above(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "during:charge")
    rules = model.compile(doc(ws).manifest)
    drag_frame(qtbot, panel, "frame:2", "frame:1", below=False)
    after = model.compile(doc(ws).manifest)
    assert after[0] == rules[1] and after[1] == rules[0] and after[2:] == rules[2:]


def test_a_drop_in_the_same_place_changes_nothing(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    before, here = doc(ws).manifest, panel.view.frame_rects()["frame:3"]
    vp, a = panel.view.viewer.viewport(), frame_pos(panel, "frame:3")
    QTest.mousePress(vp, LEFT, NONE, a)
    for k in range(1, 5):
        QTest.mouseMove(vp, a + QPoint(0, 3 * k))
    QTest.mouseRelease(vp, LEFT, NONE, a + QPoint(0, 12))
    qtbot.wait(80)
    assert doc(ws).manifest is before
    assert panel.view.frame_rects()["frame:3"] == here, "the frame went back"


def test_a_drop_that_cannot_change_the_rank_is_refused_and_says_why(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    doc(ws).edit(lambda m: setattr(m.behaviour.blocks["b5"], "at", (260.0, 200.0)))  # level with b4
    ws.refresh()
    panel.sync()
    pick(qtbot, panel, "on_flinch")
    before = doc(ws).manifest
    drag_frame(qtbot, panel, "frame:4", "frame:3", below=False)
    assert doc(ws).manifest is before and "would not change the rule's rank" in ws.message


def test_a_rule_with_no_move_cannot_be_dragged(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    panel.add_rule.click()
    qtbot.wait(60)
    before = doc(ws).manifest
    drag_frame(qtbot, panel, "frame:new:b9:1", "frame:3", below=False)
    assert doc(ws).manifest is before and ws.message == UNPLACED


def test_a_reorder_moves_a_rules_own_blocks_with_it_and_no_move_node(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "during:charge")
    nodes = {i: n.at for i, n in doc(ws).manifest.behaviour.moves.items()}
    drag_frame(qtbot, panel, "frame:1", "frame:2")
    b = blocks(ws)
    assert (b["b1"].at, b["b2"].at, b["b3"].at) == ((260.0, 100.0), (460.0, 100.0), (260.0, 0.0))
    assert {i: n.at for i, n in doc(ws).manifest.behaviour.moves.items()} == nodes


def test_a_reference_plays_in_the_game_from_its_menu_and_the_bar(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    played: list[str | None] = []
    monkeypatch.setattr(ws, "play_move_in_game", lambda force=False: played.append(ws.move))
    pick(qtbot, panel, "on_flinch")
    menu = right_click(qtbot, panel, "ref:b4:stamp")
    assert {a.text() for a in menu.actions()} == {
        "Insert before the play",
        "Delete the play",
        "Play in game",
    }
    entry(menu, "Play in game").trigger()
    qtbot.wait(40)
    assert played == ["stamp"]
    click(qtbot, panel, node_pos(panel, "ref:b5:flinch_head"))
    assert panel.bar.play is not None
    panel.bar.play.click()
    qtbot.wait(40)
    assert played == ["stamp", "flinch_head"]


def test_the_move_a_while_trigger_starts_from_plays_in_the_game_too(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    played: list[str | None] = []
    monkeypatch.setattr(ws, "play_move_in_game", lambda force=False: played.append(ws.move))
    pick(qtbot, panel, "during:charge")
    assert "Play in game" in [
        a.text() for a in right_click(qtbot, panel, B.move_id("charge")).actions()
    ]
    click(qtbot, panel, node_pos(panel, B.move_id("charge")))
    assert panel.bar.play is not None and panel.bar.plays is None
    panel.bar.play.click()
    qtbot.wait(40)
    assert played == ["charge"]


def test_play_in_game_says_so_when_no_game_is_there(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    ws.game_running = lambda: False
    pick(qtbot, panel, "on_flinch")
    click(qtbot, panel, node_pos(panel, "ref:b4:stamp"))
    assert panel.bar.play is not None
    panel.bar.play.click()
    qtbot.wait(40)
    assert ws.message.startswith("not played in the game")


# ---- All, in order --------------------------------------------------------------------------- #


def test_all_opens_in_order_and_the_toggle_is_there_only_for_all(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    assert ws.all_view == ORDER and not panel.mode.isHidden()
    assert panel.mode.toolTip() and all(b.toolTip() for b in panel.mode.buttons.values())
    assert panel.mode.value == ORDER and panel.spread.isHidden() and panel.add_rule.isHidden()
    pick(qtbot, panel, "on_flinch")
    assert panel.mode.isHidden()
    pick(qtbot, panel, ALL)
    assert not panel.mode.isHidden() and panel.hint.text() == ORDER_HINT


def test_in_order_draws_every_rule_in_rank_order_and_names_its_trigger(
    panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    assert frames(panel) == [
        f"{rk(1)} \u00b7 while charge \u00b7 first",
        f"{rk(2)} \u00b7 while charge \u00b7 (no note)",
        f"{rk(3)} \u00b7 Flinch \u00b7 any flinch",
        f"{rk(4)} \u00b7 Flinch \u00b7 (no note)",
        f"{rk(5)} \u00b7 Notice \u00b7 at once",
        f"{rk(6)} \u00b7 Part break \u00b7 (no note)",
    ]
    assert panel.title.text() == "All rules \u2014 6 in the order they are checked"


def test_a_while_rule_starts_from_a_dashed_reference_with_the_count(
    panel: BehaviourPanel,
) -> None:
    start = panel.view._shown["start:b1:charge"].view
    assert (start.name, start.badge, start.ref) == ("while charge plays", "\u00d72", True)
    assert "start:b3:charge" in shown(panel)
    wires = {(w.src, w.src_port, w.dst) for w in panel.view._links}
    assert ("start:b1:charge", "while playing", "b1") in wires
    assert B.move_id("charge") not in shown(panel), "the real move is not drawn once for every row"
    assert at(panel, "start:b1:charge")[0] < at(panel, "b1")[0]


def test_in_order_has_no_free_position_to_write(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    before = doc(ws).manifest
    for key in (ALL, "on_flinch", ALL):
        pick(qtbot, panel, key)
    to_canvas(qtbot, panel)
    to_order(qtbot, panel)
    assert doc(ws).manifest is before


def test_the_choice_belongs_to_the_document_and_outlives_a_panel(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    to_canvas(qtbot, panel)
    assert ws.all_view == CANVAS and "b8" in shown(panel) and B.move_id("charge") in shown(panel)
    again = make(qtbot, ws)
    assert again.mode.value == CANVAS and shown(again) == shown(panel)
    to_order(qtbot, panel)
    assert ws.all_view == ORDER and B.move_id("charge") not in shown(panel)


def test_a_finding_on_a_block_in_a_rule_keeps_the_rows(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    ws.reveal(("block", "b8"), "behaviour")
    panel.sync()
    assert ws.all_view == ORDER and panel.view.selected() == ["b8"]


def test_editing_in_order_is_as_in_a_trigger(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    entry(right_click(qtbot, panel, "b4"), "Insert after", "Modifiers", "At most").trigger()
    qtbot.wait(60)
    assert blocks(ws)["b4"].next == ["b9"] and blocks(ws)["b9"].play == ["stamp"]
    assert ws.all_view == ORDER and "b9" in shown(panel)
    doc(ws).undo()
    ws.refresh()
    panel.sync()
    click(qtbot, panel, node_pos(panel, "b4"))
    QTest.keyClick(panel.view, Qt.Key.Key_Delete)
    qtbot.wait(60)
    assert "b4" not in blocks(ws) and "b4" not in shown(panel)
    doc(ws).undo()
    ws.refresh()
    panel.sync()
    click(qtbot, panel, node_pos(panel, "ref:b4:stamp"))
    assert panel.bar.plays is not None
    panel.bar.plays.ctl.setCurrentIndex(panel.bar.plays.ctl.findText("howl"))
    qtbot.wait(60)
    assert blocks(ws)["b4"].play == ["howl"]
    click(qtbot, panel, frame_pos(panel, "frame:3"))
    assert panel.bar.note is not None and panel.bar.note.get_value() == "any flinch"


def test_wiring_and_dragging_are_refused_in_order_as_in_a_trigger(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    before, here = doc(ws).manifest, at(panel, "b4")
    drag(panel, port_at(panel, "b4", "out"), port_at(panel, "b5", "in"))
    qtbot.wait(40)
    assert doc(ws).manifest is before and ws.message == SCOPED
    item = panel.view._shown["b4"].view
    a = panel.view.viewer.mapFromScene(item.mapToScene(QPointF(item.width / 2, 8)))
    drag(panel, a, a + QPoint(40, 60))
    qtbot.wait(60)
    assert doc(ws).manifest is before and at(panel, "b4") == here


def test_selecting_a_start_shows_its_move_and_deleting_it_does_nothing(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    before = doc(ws).manifest
    click(qtbot, panel, node_pos(panel, "start:b1:charge"))
    assert panel.view.selected() == ["start:b1:charge"] and ws.move == "charge"
    assert [w.text() for w in panel.bar.findChildren(type(panel.title))][0] == "charge"
    QTest.keyClick(panel.view, Qt.Key.Key_Delete)
    qtbot.wait(60)
    assert doc(ws).manifest is before


def test_a_drop_among_other_triggers_rules_moves_the_rule_only_among_its_own(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    rules = model.compile(doc(ws).manifest)
    drag_frame(qtbot, panel, "frame:3", "frame:5")  # past the flinch rule below it and the notice
    after = model.compile(doc(ws).manifest)
    assert after[2] == rules[3] and after[3] == rules[2], "swapped with its own trigger's rule"
    assert after[:2] == rules[:2] and after[4:] == rules[4:], "no other trigger's rule moved"
    assert "only among its own trigger's rules" in ws.message and ws.message.startswith(
        "rule moved"
    )


def test_a_drop_among_its_own_rules_says_nothing_of_triggers(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    drag_frame(qtbot, panel, "frame:3", "frame:4")
    assert ws.message.startswith("rule moved") and "only among" not in ws.message


# ---- what a reference says ------------------------------------------------------------------- #


def tip(p: BehaviourPanel, node: str) -> str:
    return p.view._shown[node].view.toolTip()


def test_a_reference_lists_the_rules_that_play_the_move_and_the_sequence(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    doc(ws).edit(lambda m: setattr(m.moves["stamp"], "after", "stamp_2"))
    doc(ws).edit(lambda m: setattr(m.moves["stamp_2"], "after", "howl"))
    ws.refresh()
    panel.sync()
    assert tip(panel, "ref:b2:stamp").splitlines() == [
        "stamp \u00b7 played by 3 rules",
        by(1, "while charge"),
        by(3, "Flinch"),
        by(6, "Part break"),
        "then stamp_2 \u2192 howl",
    ]
    assert tip(panel, "ref:b3:stamp_2").splitlines() == [
        "stamp_2 \u00b7 played by 1 rule",
        by(2, "while charge"),
        "then howl",
    ]


def test_a_move_no_rule_plays_and_a_ring_are_said_plainly() -> None:
    assert triggers.plays_tip("m", [], []) == "m \u00b7 no rule plays it"
    assert triggers.plays_tip("m", [(1, "Flinch")], ["a", "b", "m"]).splitlines()[-1] == (
        "then a \u2192 b \u2192 m"
    )


def test_a_long_list_is_cut() -> None:
    by = [(k, "Flinch") for k in range(1, 12)]
    lines = triggers.plays_tip("m", by, []).splitlines()
    assert len(lines) == 1 + triggers.MOST + 1 and lines[-1] == f"and {11 - triggers.MOST} more"


def test_the_start_of_a_while_rule_says_who_plays_the_move_it_starts_from(
    panel: BehaviourPanel,
) -> None:
    head, *rest = tip(panel, "start:b1:charge").splitlines()
    assert head == "This rule starts from charge's while-playing port"
    assert rest[0] == "charge \u00b7 no rule plays it"
