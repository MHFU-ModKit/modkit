# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import time
from dataclasses import replace
from typing import Any

import pytest
from mhfu_studio.monster.panels.node_graph import (
    FADED,
    NODE_W,
    GraphView,
    LinkSpec,
    NodeSpec,
    wrap,
)
from mhfu_studio.shell.overlay import Ink
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import (
    QFont,
    QFontMetricsF,
    QHelpEvent,
    QInputDevice,
    QKeySequence,
    QNativeGestureEvent,
    QPointingDevice,
    QShortcut,
    QWheelEvent,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolTip

LEFT, NONE = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier


ROLES = {"trigger": Ink.HOT, "condition": Ink.AXIS_Z, "move": Ink.AXIS_X}
LONG = "a summary that goes on and on past what two lines of a compact node can hold at all"


def nodes() -> list[NodeSpec]:
    return [
        NodeSpec("t", "On hit", "trigger", (0, 0), outputs=("out",), badge="2"),
        NodeSpec("c", "Check", "condition", (300, 0), ("in",), ("out",), "above 0.5"),
        NodeSpec("m", "Spin", "move", (600, 0), ("in",), summary="3 times", tip="spins"),
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
            "create_requested",
            "picked",
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


# ---- show ---------------------------------------------------------------------------------- #


def test_show_draws(qtbot: Any) -> None:
    view = make(qtbot)
    assert set(view._shown) == {"t", "c", "m"} and pipes(view) == set(LINKS)
    assert view._shown["t"].view.badge == "2" and view._shown["m"].view.toolTip() == "spins"
    assert [view._shown[i].view.summary for i in "tcm"] == ["", "above 0.5", "3 times"]
    assert view._shown["c"].view.xy_pos == [300.0, 0.0]
    assert all(not s.view.widgets for s in view._shown.values()), "a node holds no editor"


def test_show_again_updates_in_place(qtbot: Any) -> None:
    view = make(qtbot)
    items = {i: s.view for i, s in view._shown.items()}
    view.select(["c"])
    zoom, rect = view.viewer.get_zoom(), view.viewer.scene_rect()
    ns = nodes()
    ns[0] = replace(ns[0], title="On damage", badge="9", dim=True, at=(0, 90))
    ns[1] = replace(ns[1], summary="below 0.1")
    view.show(ns, [LINKS[0]])
    assert {i: s.view for i, s in view._shown.items()} == items
    t = items["t"]
    assert (t.name, t.badge, t.opacity() < 1, t.xy_pos) == ("On damage", "9", True, [0.0, 90.0])
    assert items["c"].summary == "below 0.1"
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
    assert pipes(view) == set(LINKS)  # nothing was drawn half way


def test_roles_colour_the_nodes(qtbot: Any) -> None:
    view = make(qtbot)
    colours = {i: tuple(s.view.border_color) for i, s in view._shown.items()}
    assert len(set(colours.values())) == 3
    view.set_roles({})
    assert len({tuple(s.view.border_color) for s in view._shown.values()}) == 1


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


def size(view: GraphView, node: str) -> tuple[float, float]:
    item = view._shown[node].view
    return item.width, item.height


def test_a_node_is_as_wide_as_its_least_and_as_tall_as_its_summary(qtbot: Any) -> None:
    view = make(qtbot)
    assert all(size(view, i)[0] == NODE_W for i in "tcm")
    none, one = size(view, "t")[1], size(view, "c")[1]
    ns = nodes()
    ns[1] = replace(ns[1], summary=LONG)
    view.show(ns, LINKS)
    two = size(view, "c")[1]
    assert none <= one < two and size(view, "c")[0] == NODE_W
    ns[1] = replace(ns[1], summary="x " * 400)
    view.show(ns, LINKS)
    assert size(view, "c")[1] == two, "no more than two lines"


def test_the_summary_wraps_to_two_lines_and_elides() -> None:
    font = QFont(QApplication.font())
    metrics = QFontMetricsF(font)
    assert wrap("", metrics, 100) == [] and wrap("short", metrics, 100) == ["short"]
    lines = wrap(LONG, metrics, 120)
    assert len(lines) == 2 and lines[1].endswith("\u2026") and lines[0].startswith("a summary")
    assert all(metrics.horizontalAdvance(ln) <= 120 for ln in lines)
    assert wrap("unbreakable" * 30, metrics, 100)[0].endswith("\u2026")


def test_a_redraw_keeps_the_node_sizes_when_zoomed_out(qtbot: Any) -> None:
    view = make(qtbot)
    before = {i: size(view, i) for i in "tcm"}
    view.viewer.scale(0.15, 0.15)
    view.grab()  # a paint: NodeGraphQt draws a small node as a box, and a redraw must not follow
    ns = nodes()
    ns[1] = replace(ns[1], summary="below 0.1")
    ns[2] = replace(ns[2], summary="4 times", title="Spin")
    view.show(ns, LINKS)
    assert {i: size(view, i) for i in before} == before


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


# ---- navigation ------------------------------------------------------------------------------ #

PAD = QPointingDevice(
    "pad",
    1,
    QInputDevice.DeviceType.TouchPad,
    QPointingDevice.PointerType.Finger,
    QInputDevice.Capability.Position | QInputDevice.Capability.Scroll,
    1,
    0,
)
HERE = QPoint(500, 200)


def scroll(
    view: GraphView,
    dx: int,
    dy: int,
    *,
    pad: bool = True,
    pixels: bool = True,
    keys: Qt.KeyboardModifier = NONE,
) -> None:
    """A wheel event over the canvas: a touchpad's (pixels, a phase) or a mouse wheel's."""
    vp = view.viewer.viewport()
    phase = Qt.ScrollPhase.ScrollUpdate if pad else Qt.ScrollPhase.NoScrollPhase
    px = QPoint(dx, dy) if pad and pixels else QPoint()
    device = PAD if pad else QPointingDevice.primaryPointingDevice()
    ev = QWheelEvent(
        QPointF(HERE),
        QPointF(vp.mapToGlobal(HERE)),
        px,
        QPoint(dx, dy),
        Qt.MouseButton.NoButton,
        keys,
        phase,
        False,
        Qt.MouseEventSource.MouseEventNotSynthesized,
        device,
    )
    QApplication.sendEvent(vp, ev)


def pinch(view: GraphView, by: float) -> None:
    pos = QPointF(HERE)
    glob = QPointF(view.viewer.viewport().mapToGlobal(HERE))
    ev = QNativeGestureEvent(
        Qt.NativeGestureType.ZoomNativeGesture, PAD, 2, pos, pos, glob, by, QPointF()
    )
    QApplication.sendEvent(view.viewer.viewport(), ev)


def look(view: GraphView) -> tuple[float, float, float]:
    """The scale, and the scene point under the canvas's centre."""
    v = view.viewer
    c = v.mapToScene(v.viewport().rect().center())
    return v.transform().m11(), c.x(), c.y()


def test_two_finger_scroll_pans(qtbot: Any) -> None:
    view = make(qtbot)
    rec = Rec(view, qtbot)
    s, x, y = look(view)
    scroll(view, 40, -30)
    assert look(view) == pytest.approx((s, x - 40 / s, y + 30 / s))
    assert rec.got() == [], "a pan is no edit"


def test_a_touchpad_scroll_of_angles_pans_too(qtbot: Any) -> None:
    view = make(qtbot)
    s, x, y = look(view)
    scroll(view, 60, 0, pixels=False)
    s2, x2, y2 = look(view)
    assert (s2, y2) == pytest.approx((s, y)) and x2 < x


def test_a_mouse_wheel_zooms(qtbot: Any) -> None:
    view = make(qtbot)
    s = look(view)[0]
    scroll(view, 0, 120, pad=False)
    assert look(view)[0] > s
    scroll(view, 0, -240, pad=False)
    assert look(view)[0] < s


def test_ctrl_scroll_zooms_on_a_touchpad(qtbot: Any) -> None:
    view = make(qtbot)
    s = look(view)[0]
    scroll(view, 0, 40, keys=Qt.KeyboardModifier.ControlModifier)
    assert look(view)[0] > s


def test_a_pinch_zooms(qtbot: Any) -> None:
    view = make(qtbot)
    s = look(view)[0]
    pinch(view, 0.25)
    assert look(view)[0] == pytest.approx(s * 1.25)
    pinch(view, -0.2)
    assert look(view)[0] == pytest.approx(s * 1.25 * 0.8)


def test_zoom_keeps_to_its_limits(qtbot: Any) -> None:
    view = make(qtbot)
    for _ in range(80):
        scroll(view, 0, -120, pad=False)
        pinch(view, -0.2)
    assert look(view)[0] == pytest.approx(0.05)
    for _ in range(80):
        scroll(view, 0, 120, pad=False)
        pinch(view, 0.2)
    assert look(view)[0] == pytest.approx(3.0)


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
            "above 0.5",
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
    ns[7] = replace(ns[7], summary="below 0.1", badge="3")
    t0 = time.perf_counter()
    view.show(ns, ls)
    one = time.perf_counter() - t0
    assert again < 0.25 and one < 0.25


def test_settled_follows_the_intents_of_a_gesture(qtbot: Any) -> None:
    view = make(qtbot, spare())
    calls: list[str] = []
    for name in ("unlink_requested", "link_requested", "settled"):
        getattr(view, name).connect(lambda *_, n=name: calls.append(n))
    reroute(view)
    qtbot.wait(20)
    assert calls == ["unlink_requested", "link_requested", "settled"]
    calls.clear()
    a = title_at(view, "c")
    drag(view, a, a + QPoint(30, 30))
    qtbot.wait(20)
    assert calls == ["settled"]


def test_the_owner_may_show_when_settled(qtbot: Any) -> None:
    view = make(qtbot)
    view.settled.connect(lambda: view.show(nodes(), [LINKS[0]]))
    pipe = view._pipes()[LINKS[1]]
    mid = at(view, pipe.path().pointAtPercent(0.7))
    drag(view, mid, mid + QPoint(0, 150))
    qtbot.wait(20)
    assert pipes(view) == {LINKS[0]}


def test_the_canvas_takes_a_colour_and_frames_nodes(qtbot: Any) -> None:
    view = make(qtbot)
    scene = view.viewer.scene()
    view.set_canvas((0.1, 0.2, 0.3, 1.0))
    assert tuple(scene.background_color) == (26, 51, 77) and tuple(scene.grid_color) != (26, 51, 77)
    view.set_canvas((0.9, 0.9, 0.9, 1.0))
    assert sum(scene.grid_color) < sum(scene.background_color), "a darker grid on a light canvas"
    view.viewer.zoom_to_nodes([s.view for s in view._shown.values()])
    view.frame(["m"])
    centre = view.viewer.mapToScene(view.viewer.viewport().rect().center())
    node = view._shown["m"].view
    assert abs(centre.x() - (node.scenePos().x() + node.boundingRect().width() / 2)) < 120
    view.frame(["nope"])


def test_a_nodes_tip_shows_over_its_title(qtbot: Any) -> None:
    view = make(qtbot)
    item = view._shown["m"].view
    assert item.text_item.toolTip() == ""
    vp = view.viewer.viewport()
    where = view.viewer.mapFromScene(item.mapToScene(QPointF(item.width / 2, 8)))
    QApplication.sendEvent(vp, QHelpEvent(QEvent.Type.ToolTip, where, vp.mapToGlobal(where)))
    qtbot.wait(20)
    assert QToolTip.text() == "spins"
    QToolTip.hideText()


def test_faded_links_are_drawn_back_and_come_forward_again(qtbot: Any) -> None:
    view = make(qtbot)

    def ops() -> dict[LinkSpec, float]:
        return {w: p.opacity() for w, p in view._pipes().items()}

    assert set(ops().values()) == {1.0}
    view.show(nodes(), LINKS, faded=[LINKS[0]])
    assert ops() == {LINKS[0]: FADED, LINKS[1]: 1.0}
    view.show(nodes(), LINKS)
    assert set(ops().values()) == {1.0}


def test_a_selection_is_told_whole_and_a_show_does_not_tell_it(qtbot: Any) -> None:
    view = make(qtbot)
    got: list[list[str]] = []
    view.selection_changed.connect(got.append)
    view.select(["t"])
    qtbot.wait(20)
    assert got == []
    view._shown["c"].view.setSelected(True)
    qtbot.wait(20)
    assert got == [["t", "c"]]
    view.show(nodes(), LINKS)
    qtbot.wait(20)
    assert got == [["t", "c"]] and view.selected() == ["t", "c"]
