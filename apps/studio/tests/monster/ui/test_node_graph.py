# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import time
from dataclasses import dataclass, replace
from typing import Any

import pytest
from mhfu_studio.monster.panels.node_graph import (
    NOTE,
    GraphView,
    LinkSpec,
    NodeSpec,
    option,
)
from mhfu_studio.shell.overlay import Ink
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox, QToolButton

LEFT, NONE = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier


@dataclass(frozen=True)
class P:
    """A parameter, as `mhfu_port.behaviour.Param` will have it."""

    name: str
    type: str
    title: str = ""
    lo: float | None = None
    hi: float | None = None
    choices: tuple[object, ...] = ()
    optional: bool = False
    tip: str = ""


COUNT = P("n", "int", "Times", 1, 9, tip="how often")
HP = P("hp", "float", "HP below", 0, 1, optional=True, tip="a fraction")
OP = P("op", "choice", "Compare", choices=("below", "above"), tip="which way")
PART = P("part", "part", "Part", optional=True, tip="a part")
MAINS = P("mains", "mains", "Mains", optional=True, tip="mains")
ROLES = {"trigger": Ink.HOT, "condition": Ink.AXIS_Z, "move": Ink.AXIS_X}


def nodes() -> list[NodeSpec]:
    return [
        NodeSpec("t", "On hit", "trigger", (0, 0), outputs=("out",), badge="2"),
        NodeSpec(
            "c",
            "Check",
            "condition",
            (300, 0),
            ("in",),
            ("out",),
            params=(OP, HP, PART),
            values={"op": "above", "hp": 0.5, "part": 2},
            options={"part": [("Head", 1), ("Tail", 2)]},
        ),
        NodeSpec(
            "m",
            "Spin",
            "move",
            (600, 0),
            ("in",),
            params=(COUNT, MAINS),
            values={"n": 3, "mains": (1,)},
            options={"mains": [("one", 1), ("two", 2), ("three", 3)]},
            tip="spins",
        ),
    ]


LINKS = [LinkSpec("t", "out", "c", "in"), LinkSpec("c", "out", "m", "in")]


class Rec:
    """Every intent signal of a view, as (name, args)."""

    def __init__(self, view: GraphView, qtbot: Any) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.qtbot = qtbot
        for name in (
            "link_requested",
            "unlink_requested",
            "delete_requested",
            "moved",
            "param_changed",
            "create_requested",
            "picked",
            "label_changed",
        ):
            getattr(view, name).connect(lambda *a, n=name: self.calls.append((n, a)))

    def got(self) -> list[tuple[str, tuple[Any, ...]]]:
        """What has come by now: the view says things an event-loop turn late."""
        self.qtbot.wait(20)
        out, self.calls = self.calls, []
        return out


def make(
    qtbot: Any, ns: list[NodeSpec] | None = None, ls: list[LinkSpec] | None = None
) -> GraphView:
    view = GraphView()
    qtbot.addWidget(view)
    view.resize(1000, 420)
    view.set_roles(ROLES)
    view.show()
    qtbot.waitExposed(view)
    view.show(nodes() if ns is None else ns, LINKS if ls is None else ls)
    qtbot.wait(20)
    return view


def pipes(view: GraphView) -> set[LinkSpec]:
    return set(view._pipes())


def at(view: GraphView, scene: QPointF) -> QPoint:
    return view.viewer.mapFromScene(scene)


def port_at(view: GraphView, node: str, port: str) -> QPoint:
    s = view._shown[node]
    item = (s.outs | s.ins)[port] if port in s.outs else s.ins[port]
    return at(view, item.mapToScene(item.boundingRect().center()))


def title_at(view: GraphView, node: str) -> QPoint:
    item = view._shown[node].view
    return at(view, item.mapToScene(QPointF(item.boundingRect().width() / 2, 8)))


def drag(view: GraphView, a: QPoint, b: QPoint) -> None:
    vp = view.viewer.viewport()
    QTest.mousePress(vp, LEFT, NONE, a)
    for i in range(1, 6):
        QTest.mouseMove(vp, a + (b - a) * (i / 5))
    QTest.mouseRelease(vp, LEFT, NONE, b)


def field(view: GraphView, node: str, name: str) -> Any:
    return view._shown[node].fields[name]


# ---- show ---------------------------------------------------------------------------------- #


def test_show_draws(qtbot: Any) -> None:
    view = make(qtbot)
    assert set(view._shown) == {"t", "c", "m"} and pipes(view) == set(LINKS)
    assert view._shown["t"].view.badge == "2" and view._shown["m"].view.toolTip() == "spins"
    assert (
        field(view, "c", "op").get_value() == "above" and field(view, "c", "part").get_value() == 2
    )
    assert field(view, "m", "mains").get_value() == (1,) and field(view, "m", "n").get_value() == 3
    assert view._shown["c"].view.xy_pos == [300.0, 0.0]


def test_show_again_updates_in_place(qtbot: Any) -> None:
    view = make(qtbot)
    items = {i: s.view for i, s in view._shown.items()}
    view.select(["c"])
    zoom, rect = view.viewer.get_zoom(), view.viewer.scene_rect()
    ns = nodes()
    ns[0] = replace(ns[0], title="On damage", badge="9", dim=True, at=(0, 90), label="why")
    ns[1] = replace(ns[1], values={"op": "below", "hp": None, "part": 1})
    ns[2] = replace(ns[2], options={"mains": [("one", 1), ("two", 2), ("three", 3), ("four", 4)]})
    view.show(ns, [LINKS[0]])
    assert {i: s.view for i, s in view._shown.items()} == items
    t = items["t"]
    assert (t.name, t.badge, t.opacity() < 1, t.xy_pos) == ("On damage", "9", True, [0.0, 90.0])
    assert field(view, "t", NOTE).get_value() == "why"
    assert (field(view, "c", "op").get_value(), field(view, "c", "hp").get_value()) == (
        "below",
        None,
    )
    assert field(view, "c", "part").get_value() == 1
    assert (
        field(view, "m", "mains").get_value() == (1,) and len(field(view, "m", "mains")._opts) == 4
    )
    assert pipes(view) == {LINKS[0]}
    assert view.selected() == ["c"]
    assert (view.viewer.get_zoom(), view.viewer.scene_rect()) == (zoom, rect)


def test_show_adds_and_removes(qtbot: Any) -> None:
    view = make(qtbot)
    view.select(["m"])
    extra = NodeSpec("x", "Extra", "move", (0, 200), ("in",))
    view.show([*nodes()[:2], extra], [LINKS[0], LinkSpec("c", "out", "x", "in")])
    assert set(view._shown) == {"t", "c", "x"} and view.selected() == []
    assert pipes(view) == {LINKS[0], LinkSpec("c", "out", "x", "in")}
    assert len(view.viewer.all_nodes()) == 3 and len(view.viewer.all_pipes()) == 2
    view.show([])
    assert view._shown == {} and view.viewer.all_nodes() == [] and view.viewer.all_pipes() == []


def test_show_keeps_the_selection_through_additions(qtbot: Any) -> None:
    view = make(qtbot)
    view.select(["c", "m"])
    view.show([*nodes(), NodeSpec("x", "Extra", "move", (0, 200))], LINKS)
    assert sorted(view.selected()) == ["c", "m"]


def test_show_is_idempotent_and_quiet(qtbot: Any) -> None:
    view = make(qtbot)
    rec = Rec(view, qtbot)
    before = {i: s.view for i, s in view._shown.items()}
    for _ in range(3):
        view.show(nodes(), LINKS)
    assert rec.got() == [] and {i: s.view for i, s in view._shown.items()} == before
    assert len(view.viewer.all_pipes()) == 2
    assert view.selected() == []


def test_new_ports_rebuild_the_node(qtbot: Any) -> None:
    view = make(qtbot)
    old = view._shown["c"].view
    ns = nodes()
    ns[1] = replace(ns[1], outputs=("out", "else"))
    view.show(ns, [*LINKS, LinkSpec("c", "else", "m", "in")])
    assert view._shown["c"].view is not old and "else" in view._shown["c"].outs
    assert len(pipes(view)) == 3


def test_a_bad_spec_is_refused_before_anything_is_drawn(qtbot: Any) -> None:
    view = make(qtbot)
    with pytest.raises(ValueError, match="no such input"):
        view.show(nodes(), [LinkSpec("t", "out", "c", "nope")])
    with pytest.raises(ValueError, match="share an id"):
        view.show([nodes()[0], nodes()[0]], [])
    with pytest.raises(ValueError, match="twice"):
        view.show([replace(nodes()[0], outputs=("out", "out"))], [])
    with pytest.raises(ValueError, match="unknown type"):
        view.show([replace(nodes()[0], params=(P("x", "text"),))], [])
    assert pipes(view) == set(LINKS)  # nothing was drawn half way


def test_roles_colour_the_nodes(qtbot: Any) -> None:
    view = make(qtbot)
    colours = {i: tuple(s.view.border_color) for i, s in view._shown.items()}
    assert len(set(colours.values())) == 3
    view.set_roles({})
    assert len({tuple(s.view.border_color) for s in view._shown.values()}) == 1


def test_options_come_from_choices_too(qtbot: Any) -> None:
    assert option("a") == ("a", "a") and option(("A", 1)) == ("A", 1) and option(3) == ("3", 3)
    view = make(qtbot)
    combo = field(view, "c", "op")._ctl
    assert isinstance(combo, QComboBox) and [combo.itemText(i) for i in range(2)] == [
        "below",
        "above",
    ]


# ---- intents ------------------------------------------------------------------------------- #


def test_drag_a_link(qtbot: Any) -> None:
    view = make(qtbot, ls=[LINKS[0]])
    rec = Rec(view, qtbot)
    drag(view, port_at(view, "c", "out"), port_at(view, "m", "in"))
    assert rec.got() == [("link_requested", ("c", "out", "m", "in"))]
    assert pipes(view) == {LINKS[0]}  # the view drew nothing: that is the owner's
    drag(view, port_at(view, "c", "out"), port_at(view, "m", "in"))
    assert rec.got() == [("link_requested", ("c", "out", "m", "in"))]  # and asks again


def test_drag_a_link_from_the_input_side(qtbot: Any) -> None:
    view = make(qtbot, ls=[])
    rec = Rec(view, qtbot)
    drag(view, port_at(view, "c", "in"), port_at(view, "t", "out"))
    assert rec.got() == [("link_requested", ("t", "out", "c", "in"))]


def test_an_accepted_link_is_drawn(qtbot: Any) -> None:
    view = make(qtbot, ls=[LINKS[0]])
    view.link_requested.connect(lambda *a: view.show(nodes(), [*view._links, LinkSpec(*a)]))
    drag(view, port_at(view, "c", "out"), port_at(view, "m", "in"))
    qtbot.wait(20)
    assert pipes(view) == set(LINKS)


def test_a_link_no_wire_may_make_is_never_asked(qtbot: Any) -> None:
    view = make(qtbot)
    rec = Rec(view, qtbot)
    drag(view, port_at(view, "c", "out"), port_at(view, "c", "in"))  # onto itself
    drag(view, port_at(view, "t", "out"), port_at(view, "t", "out"))
    assert rec.got() == []


def test_drag_a_wire_off_is_an_unlink_and_a_refusal_puts_it_back(qtbot: Any) -> None:
    view = make(qtbot)
    rec = Rec(view, qtbot)
    pipe = view._pipes()[LINKS[1]]
    mid = at(view, pipe.path().pointAtPercent(0.7))  # nearer the input end: that end is picked up
    drag(view, mid, mid + QPoint(0, 150))
    assert rec.got() == [("unlink_requested", ("c", "out", "m", "in"))]
    assert pipes(view) == set(LINKS)  # the owner said nothing: the wire is back


def spare() -> list[NodeSpec]:
    """`nodes()` and a node to re-route a wire to."""
    return [*nodes(), NodeSpec("x", "Extra", "move", (600, 200), ("in",))]


def reroute(view: GraphView) -> None:
    """Picks up the c to m wire at its m end and drops it on x."""
    mid = at(view, view._pipes()[LINKS[1]].path().pointAtPercent(0.7))
    drag(view, mid, port_at(view, "x", "in"))


def test_a_reroute_is_an_unlink_then_a_link(qtbot: Any) -> None:
    view = make(qtbot, spare())
    rec = Rec(view, qtbot)
    reroute(view)
    assert rec.got() == [
        ("unlink_requested", ("c", "out", "m", "in")),
        ("link_requested", ("c", "out", "x", "in")),
    ]


def test_a_refused_reroute_restores_the_old_wire(qtbot: Any) -> None:
    view = make(qtbot, spare())
    rec = Rec(view, qtbot)
    reroute(view)
    assert len(rec.got()) == 2
    assert pipes(view) == set(LINKS)
    reroute(view)  # the model went back with the canvas: asking again asks again
    assert len(rec.got()) == 2 and pipes(view) == set(LINKS)


def test_an_accepted_reroute_is_drawn_once(qtbot: Any) -> None:
    view = make(qtbot, spare())
    moved = [LINKS[0], LinkSpec("c", "out", "x", "in")]
    view.link_requested.connect(lambda *a: view.show(spare(), moved))
    reroute(view)
    qtbot.wait(20)
    assert pipes(view) == set(moved) and len(view.viewer.all_pipes()) == 2


def test_an_accepted_unlink_stays_gone(qtbot: Any) -> None:
    view = make(qtbot)
    view.unlink_requested.connect(lambda *a: view.show(nodes(), [LINKS[0]]))
    pipe = view._pipes()[LINKS[1]]
    mid = at(view, pipe.path().pointAtPercent(0.7))
    drag(view, mid, mid + QPoint(0, 150))
    qtbot.wait(20)
    assert pipes(view) == {LINKS[0]}


def test_drag_a_node(qtbot: Any) -> None:
    view = make(qtbot)
    rec = Rec(view, qtbot)
    a = title_at(view, "c")
    drag(view, a, a + QPoint(40, 60))
    got = rec.got()
    moved = [c for c in got if c[0] == "moved"]
    assert len(moved) == 1 and moved[0][1][0] == "c"
    x, y = moved[0][1][1:]
    assert (x, y) != (300.0, 0.0) and y > 0
    assert view._shown["c"].view.xy_pos == [300.0, 0.0]  # refused: back where the owner has it


def test_a_click_is_not_a_move_and_a_group_drag_moves_each_node_once(qtbot: Any) -> None:
    view = make(qtbot)
    rec = Rec(view, qtbot)
    QTest.mouseClick(view.viewer.viewport(), LEFT, NONE, title_at(view, "c"))
    assert [c for c in rec.got() if c[0] == "moved"] == []
    view.select(["c", "m"])
    a = title_at(view, "c")
    drag(view, a, a + QPoint(0, 50))
    moved = [c[1][0] for c in rec.got() if c[0] == "moved"]
    assert sorted(moved) == ["c", "m"]


def test_an_accepted_move_stays(qtbot: Any) -> None:
    view = make(qtbot)
    placed: list[tuple[float, float]] = []

    def take(i: str, x: float, y: float) -> None:
        placed.append((x, y))
        view.show([replace(n, at=(x, y)) if n.id == i else n for n in nodes()], LINKS)

    view.moved.connect(take)
    a = title_at(view, "c")
    drag(view, a, a + QPoint(40, 60))
    qtbot.wait(20)
    assert len(placed) == 1 and view._shown["c"].view.xy_pos == list(placed[0])


def test_delete_key(qtbot: Any) -> None:
    view = make(qtbot)
    rec = Rec(view, qtbot)
    view.select(["c", "m"])
    view.viewer.setFocus()
    QTest.keyClick(view.viewer, Qt.Key.Key_Delete)
    got = rec.got()
    assert [c for c in got if c[0] == "delete_requested"] == [("delete_requested", (["c", "m"],))]
    assert len(view._shown) == 3  # nothing is deleted until the owner says


def test_delete_key_on_a_wire(qtbot: Any) -> None:
    view = make(qtbot)
    rec = Rec(view, qtbot)
    view._pipes()[LINKS[0]].setSelected(True)
    view.viewer.setFocus()
    QTest.keyClick(view.viewer, Qt.Key.Key_Backspace)
    assert rec.got() == [("unlink_requested", ("t", "out", "c", "in"))]


def test_pick(qtbot: Any) -> None:
    view = make(qtbot)
    rec = Rec(view, qtbot)
    vp = view.viewer.viewport()
    QTest.mouseClick(vp, LEFT, NONE, title_at(view, "t"))
    assert rec.got() == [("picked", ("t",))]
    QTest.mouseClick(vp, LEFT, NONE, title_at(view, "m"))
    assert rec.got() == [("picked", ("m",))]
    QTest.mouseClick(vp, LEFT, NONE, at(view, QPointF(300, 400)))
    assert rec.got() == [("picked", (None,))]


def test_select_and_show_do_not_pick(qtbot: Any) -> None:
    view = make(qtbot)
    rec = Rec(view, qtbot)
    view.select(["c"])
    view.show(nodes()[:1], [])
    assert rec.got() == [] and view.selected() == []


def test_edits_on_the_node(qtbot: Any) -> None:
    view = make(qtbot)
    rec = Rec(view, qtbot)
    spin, real = field(view, "m", "n")._ctl, field(view, "c", "hp")._ctl
    combo, part = field(view, "c", "op")._ctl, field(view, "c", "part")._ctl
    mains, note = field(view, "m", "mains")._ctl, field(view, "t", NOTE)._ctl
    assert isinstance(spin, QSpinBox) and isinstance(real, QDoubleSpinBox)
    assert isinstance(mains, QToolButton) and isinstance(note, QLineEdit)
    spin.setValue(5)
    real.setValue(0.25)
    combo.setCurrentIndex(0)
    part.setCurrentIndex(0)  # "none"
    mains.menu().actions()[2].trigger()
    note.setText("why")
    note.editingFinished.emit()
    assert rec.got() == [
        ("param_changed", ("m", "n", 5)),
        ("param_changed", ("c", "hp", 0.25)),
        ("param_changed", ("c", "op", "below")),
        ("param_changed", ("c", "part", None)),
        ("param_changed", ("m", "mains", (1, 3))),
        ("label_changed", ("t", "why")),
    ]


def test_an_unchanged_note_says_nothing(qtbot: Any) -> None:
    view = make(qtbot)
    rec = Rec(view, qtbot)
    field(view, "t", NOTE)._ctl.editingFinished.emit()
    assert rec.got() == []


def test_a_refused_edit_goes_back(qtbot: Any) -> None:
    view = make(qtbot)
    rec = Rec(view, qtbot)
    field(view, "m", "n")._ctl.setValue(7)
    field(view, "c", "op")._ctl.setCurrentIndex(0)
    field(view, "t", NOTE)._ctl.setText("x")
    field(view, "t", NOTE)._ctl.editingFinished.emit()
    assert len(rec.got()) == 3
    assert field(view, "m", "n").get_value() == 3 and field(view, "c", "op").get_value() == "above"
    assert field(view, "t", NOTE).get_value() == ""


def test_an_accepted_edit_stays(qtbot: Any) -> None:
    view = make(qtbot)

    def take(i: str, name: str, value: object) -> None:
        ns = nodes()
        ns[2] = replace(ns[2], values={**ns[2].values, name: value})
        view.show(ns, LINKS)

    view.param_changed.connect(take)
    field(view, "m", "n")._ctl.setValue(7)
    qtbot.wait(20)
    assert field(view, "m", "n").get_value() == 7


def test_optional_numbers_have_none(qtbot: Any) -> None:
    view = make(qtbot)
    real = field(view, "c", "hp")
    assert real._ctl.specialValueText() == "none" and real.get_value() == 0.5
    view.show([replace(nodes()[1], values={"hp": None})], [])
    assert real.get_value() is None and real._ctl.value() == real._ctl.minimum()
    real._ctl.setValue(0.0)
    assert real.get_value() == 0.0


def test_a_value_the_options_lack_still_shows(qtbot: Any) -> None:
    view = make(qtbot)
    view.show([replace(nodes()[1], values={"part": 9})], [])
    part = field(view, "c", "part")
    assert part.get_value() == 9 and part._ctl.currentText() == "9"


def test_options_change_keeps_the_pick(qtbot: Any) -> None:
    view = make(qtbot)
    new = [("Tail", 2), ("Wing", 3)]
    view.show([replace(nodes()[1], options={"part": new}, values={"part": 2})], [])
    part = field(view, "c", "part")
    assert part.get_value() == 2 and part._ctl.count() == 3  # none, Tail, Wing


def test_the_palette_creates(qtbot: Any) -> None:
    view = make(qtbot)
    rec = Rec(view, qtbot)
    view.set_palette(
        [("Triggers", [("hit", "On hit", "when hit")]), ("Moves", [("m", "Move", "a move")])]
    )
    menu = view.viewer.context_menus()["graph"]
    groups = [a.text() for a in menu.actions()]
    assert groups == ["Triggers", "Moves"]
    act = menu.actions()[0].menu().actions()[0]
    assert (act.text(), act.toolTip()) == ("On hit", "when hit")
    QTest.mouseMove(view.viewer.viewport(), QPoint(120, 80))
    QTest.mousePress(view.viewer.viewport(), Qt.MouseButton.RightButton, NONE, QPoint(120, 80))
    QTest.mouseRelease(view.viewer.viewport(), Qt.MouseButton.RightButton, NONE, QPoint(120, 80))
    want = view.viewer.mapToScene(QPoint(120, 80))
    act.trigger()
    [(name, (kind, x, y))] = rec.got()
    assert (name, kind) == ("create_requested", "hit")
    assert (x, y) == pytest.approx((want.x(), want.y()), abs=1.0)
    view.set_palette([("Only", [("a", "A", "tip")])])
    assert [a.text() for a in menu.actions()] == ["Only"]


def test_the_undo_stack_stays_empty(qtbot: Any) -> None:
    view = make(qtbot)
    drag(view, port_at(view, "c", "out"), port_at(view, "m", "in"))
    a = title_at(view, "c")
    drag(view, a, a + QPoint(30, 30))
    field(view, "m", "n")._ctl.setValue(8)
    view.show([*nodes(), NodeSpec("x", "X", "move", (0, 300))], [])
    qtbot.wait(20)
    stack = view._graph.undo_stack()
    assert stack.count() == 0 and not stack.canUndo() and not stack.canRedo()
    viewer = view.viewer
    assert (
        viewer.qaction_for_undo().shortcuts() == [] and viewer.qaction_for_redo().shortcuts() == []
    )
    assert viewer.qaction_for_undo() not in viewer.context_menus()["graph"].actions()


def test_a_shortcut_of_the_window_gets_ctrl_z(qtbot: Any) -> None:
    view = make(qtbot)
    hit: list[int] = []
    QShortcut(
        QKeySequence("Ctrl+Z"),
        view,
        lambda: hit.append(1),
        context=Qt.ShortcutContext.WindowShortcut,
    )
    view.window().activateWindow()
    view.viewer.setFocus()
    QTest.keyClick(view.viewer, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert hit == [1]


def test_the_first_show_frames_the_nodes_and_later_ones_do_not(qtbot: Any) -> None:
    view = GraphView()
    qtbot.addWidget(view)
    view.resize(1000, 420)
    view.show()
    qtbot.waitExposed(view)
    view.show(nodes(), LINKS)
    seen = view.viewer.mapToScene(view.viewer.viewport().rect()).boundingRect()
    assert all(seen.contains(s.view.sceneBoundingRect()) for s in view._shown.values())
    view.viewer.set_zoom(0.3)
    zoom = view.viewer.get_zoom()
    view.show([*nodes(), NodeSpec("far", "Far", "move", (9000, 9000))], LINKS)
    assert view.viewer.get_zoom() == zoom


def test_a_view_shown_late_frames_when_it_is_shown(qtbot: Any) -> None:
    view = GraphView()
    qtbot.addWidget(view)
    view.resize(1000, 420)
    view.show(nodes(), LINKS)  # not visible yet
    view.show()
    qtbot.waitExposed(view)
    qtbot.wait(20)
    seen = view.viewer.mapToScene(view.viewer.viewport().rect()).boundingRect()
    assert all(seen.contains(s.view.sceneBoundingRect()) for s in view._shown.values())


def test_show_80_nodes_is_cheap_after_the_first_time(qtbot: Any) -> None:
    """About 2 ms where it was measured (the first build, 0.2 s); the bound is a guard against
    anything that grows with the square."""
    ns = [
        NodeSpec(
            f"n{i}",
            f"Node {i}",
            "condition" if i % 3 else "move",
            (i % 10 * 300.0, i // 10 * 200.0),
            ("in",),
            ("out",),
            params=(OP, HP, PART),
            values={"op": "above", "hp": 0.5, "part": 2},
            options={"part": [("Head", 1), ("Tail", 2)]},
        )
        for i in range(80)
    ]
    ls = [LinkSpec(f"n{i}", "out", f"n{i + 1}", "in") for i in range(79)]
    view = GraphView()
    qtbot.addWidget(view)
    view.resize(1000, 600)
    view.show()
    qtbot.waitExposed(view)
    view.show(ns, ls)
    t0 = time.perf_counter()
    view.show(ns, ls)
    again = time.perf_counter() - t0
    ns[7] = replace(ns[7], values={"op": "below", "hp": 0.1, "part": 1}, badge="3")
    t0 = time.perf_counter()
    view.show(ns, ls)
    one = time.perf_counter() - t0
    assert again < 0.25 and one < 0.25
