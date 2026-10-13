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
from mhfu_studio.monster.behaviour import ALL
from mhfu_studio.monster.document import PortDocument
from mhfu_studio.monster.panels import rows, triggers
from mhfu_studio.monster.panels.behaviour import SCOPED, WARN, BehaviourPanel
from mhfu_studio.monster.panels.node_graph import FRAME_BOTTOM, FRAME_TOP, NodeSpec
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest

LEFT, NONE = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier


def rk(*places: int) -> str:
    """Places as the outline writes them, comma-separated."""
    return ", ".join(f"#{k}" for k in places)


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
    empty = [
        "on_combat_entered",
        "on_combat_left",
        "on_tail_cut",
        "on_enraged",
        "on_calmed",
        "on_signal",
        "any_time",
        "idle",
        "host_state",
    ]
    for key in empty:
        assert rowsby[key][1:] == ("—", "", "none"), key
    assert [k for k, c in rowsby.items() if c[3] == "none"] == empty
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
    assert triggers.owner(list(ts.values()), "b7") == "on_noticed"
    assert triggers.owner(list(ts.values()), "nope") is None


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
    assert [rows.node_of(i) for i in ("b1", "b1~2", "ref:stamp:2", "move:charge")] == [
        "b1",
        "b1",
        "move:stamp",
        "move:charge",
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
    plan = rows.plan(t, BLOCKS, NOTES, MOVES, {"m": (1, 2, 9)})
    assert [r.title for r in plan.rows] == [f"{rk(1)} · go", f"{rk(2)} · then"]
    assert [[n.id for n in r.nodes] for r in plan.rows] == [
        ["b1", "b2", "ref:m:1"],
        ["b3", "ref:m:2"],
    ]
    ref = plan.rows[0].nodes[-1]
    assert (ref.title, ref.ref, ref.badge) == ("▸ m", True, "×3")
    assert ref.tip == f"The move m, played by 3 rules: {rk(1, 2, 9)}"
    assert {(w.src, w.dst) for w in plan.links} == {
        ("b1", "b2"),
        ("b2", "ref:m:1"),
        ("b3", "ref:m:2"),
    }
    assert all(n.badge == WARN for n in plan.rows[0].nodes[:2]), "the rank is on the frame"


def test_a_move_played_by_one_rule_has_no_count() -> None:
    plan = rows.plan(trigger(Path(("b1",), "m", None)), BLOCKS, NOTES, MOVES, {"m": (1,)})
    ref = plan.rows[0].nodes[-1]
    assert ref.badge == "" and ref.tip == f"The move m, played by 1 rule: {rk(1)}"


def test_a_rule_with_no_note_says_so() -> None:
    plan = rows.plan(trigger(Path(("b2",), "m", None)), BLOCKS, NOTES, MOVES, {})
    assert plan.rows[0].title == f"{rk(1)} · (no note)"


def test_a_rule_that_plays_nothing_ends_at_its_last_block() -> None:
    plan = rows.plan(trigger(Path(("b1", "b2"), None, None)), BLOCKS, NOTES, MOVES, {})
    assert [n.id for n in plan.rows[0].nodes] == ["b1", "b2"] and len(plan.links) == 1


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


def test_all_is_the_stored_canvas_unchanged(panel: BehaviourPanel, ws: MonsterWorkspace) -> None:
    m = doc(ws).manifest
    assert panel.ws.scope == ALL
    assert shown(panel) == {*m.behaviour.blocks, *map(B.move_id, m.moves)}
    assert at(panel, "b2") == [460.0, 0.0] and panel.spread.isEnabled()
    assert panel.title.text().startswith("8 blocks · 6 of")


def test_a_trigger_shows_only_its_rules_in_rank_order(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    assert ws.scope == "on_flinch" and shown(panel) == {
        "b4",
        "b5",
        "ref:stamp:1",
        "ref:flinch_head:1",
    }
    assert frames(panel) == [f"{rk(3)} · any flinch", f"{rk(4)} · (no note)"]
    assert panel.title.text() == f"All › Flinch — 2 rules, {rng(3, 4)} of 6"
    assert not panel.spread.isEnabled() and panel.hint.text() == SCOPED
    assert panel.view._shown["ref:stamp:1"].view.badge == "×3"
    assert panel.view._shown["ref:stamp:1"].view.toolTip() == (
        f"The move stamp, played by 3 rules: {rk(1, 3, 6)}"
    )
    pick(qtbot, panel, ALL)
    assert ws.scope == ALL and "b8" in shown(panel) and panel.spread.isEnabled()


def test_a_rule_that_plays_nothing_is_a_row_without_a_move(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    def count(m: Any) -> None:
        b = m.behaviour.blocks
        b["b9"] = Block("on_tail_cut", (260.0, 600.0), next=["b10"], label="count the cuts")
        b["b10"] = Block("counter_add", (460.0, 600.0), {"counter": "cuts"})

    doc(ws).edit(count)
    ws.sync()
    panel.sync()
    pick(qtbot, panel, "on_tail_cut")
    assert shown(panel) == {"b9", "b10"}
    assert frames(panel) == [f"{rk(1)} · count the cuts"], "a rule that plays nothing ranks first"
    assert {(w.src, w.dst) for w in panel.view._links} == {("b9", "b10")}
    assert panel.title.text() == f"All › Tail cut — 1 rule, {rk(1)} of 7"


def test_a_while_trigger_draws_its_move_once_wired_into_every_row(
    qtbot: Any, panel: BehaviourPanel
) -> None:
    pick(qtbot, panel, "during:charge")
    assert B.move_id("charge") in shown(panel)
    assert {"b1", "b2", "b3", "ref:stamp:1", "ref:stamp_2:1"} <= shown(panel)
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
    assert ws.scope == ALL and panel.view.selected() == ["b9"]


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


def test_creating_in_a_scope_is_refused_with_the_hint(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    before, where = doc(ws).manifest, QPoint(400, 40)
    vp = panel.view.viewer.viewport()
    QTest.mouseMove(vp, where)
    QTest.mousePress(vp, Qt.MouseButton.RightButton, NONE, where)
    QTest.mouseRelease(vp, Qt.MouseButton.RightButton, NONE, where)
    menu = panel.view.viewer.context_menus()["graph"]
    [act] = [a for sub in menu.actions() for a in sub.menu().actions() if a.text() == "Right away"]
    act.trigger()
    qtbot.wait(40)
    assert doc(ws).manifest is before and ws.message == SCOPED


def test_wiring_in_a_scope_is_refused_with_the_hint(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    pick(qtbot, panel, "on_flinch")
    before = doc(ws).manifest
    drag(panel, port_at(panel, "b4", "out"), port_at(panel, "b5", "in"))  # a block into a block
    qtbot.wait(40)
    assert doc(ws).manifest is before and ws.message == SCOPED
    assert {(w.src, w.dst) for w in panel.view._links} == {
        ("b4", "ref:stamp:1"),
        ("b5", "ref:flinch_head:1"),
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
    panel.view.select(["b4", "ref:stamp:1"])
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
    ref = panel.view._shown["ref:stamp:1"].view
    QTest.mouseClick(
        panel.view.viewer.viewport(),
        LEFT,
        NONE,
        panel.view.viewer.mapFromScene(ref.mapToScene(QPointF(ref.width / 2, 8))),
    )
    qtbot.wait(60)
    assert panel.view.selected() == ["ref:stamp:1"] and ws.move == "stamp"
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
