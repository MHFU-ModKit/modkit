# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Behaviour dock over the synthetic port: what the canvas shows, and each gesture of it
through the real view."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mhfu_port import behaviour as model
from mhfu_port.behaviour import KINDS, Block, Kind, MoveNode, Rule
from mhfu_port.manifest import Move
from mhfu_studio.monster import behaviour as B
from mhfu_studio.monster.document import PortDocument
from mhfu_studio.monster.panels.behaviour import EFFECT, WARN, BehaviourPanel, roles
from mhfu_studio.monster.panels.node_graph import GraphView, LinkSpec
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.shell.workspace import Dock
from mhfu_studio.ui import kit, theme
from mhfu_studio.ui.testing import FakeWorkspace
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QComboBox, QDockWidget, QToolButton

LEFT, NONE = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
STAMP, FLINCH, STEP = B.move_id("stamp"), B.move_id("flinch_head"), B.move_id("stamp_2")


def graph(m: Any) -> None:
    """b1 plays stamp while charge plays; b2 and b3 are spare; every move sits in its column."""
    for name in ("stamp", "stamp_2", "flinch_head"):
        m.moves[name] = Move(clip="walk")
    b = m.behaviour
    b.blocks["b1"] = Block("played_for", (260.0, 0.0), {"frames": 20}, play=["stamp"])
    b.blocks["b2"] = Block("on_noticed", (260.0, 160.0))
    b.blocks["b3"] = Block("distance", (260.0, 300.0), {"lo": 0.0})
    b.moves["charge"] = MoveNode((0.0, 0.0), ["b1"])
    for k, name in enumerate(("stamp", "stamp_2", "flinch_head")):
        b.moves[name] = MoveNode((780.0, 160.0 * k))


@pytest.fixture
def ws(workspace: MonsterWorkspace) -> MonsterWorkspace:
    assert workspace.doc is not None
    workspace.doc.edit(graph)
    workspace.sync()
    return workspace


def make(qtbot: Any, ws: MonsterWorkspace) -> BehaviourPanel:
    """Shown, and synced on every change as the window would."""
    studio = Studio([ws])
    p = BehaviourPanel(ws, studio)
    studio.listen(p.sync)
    qtbot.addWidget(p)
    p.resize(1000, 560)
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


def pipes(view: GraphView) -> set[LinkSpec]:
    return set(view._pipes())


def drawn(p: BehaviourPanel) -> tuple[set[str], set[LinkSpec]]:
    """What the canvas holds: node ids and wires."""
    return set(p.view._shown), pipes(p.view)


def stored(ws: MonsterWorkspace) -> tuple[set[str], set[LinkSpec]]:
    m = doc(ws).manifest
    return (
        {*m.behaviour.blocks, *map(B.move_id, m.moves)},
        {LinkSpec(*w) for w in B.wires(m)},
    )


def in_step(p: BehaviourPanel, ws: MonsterWorkspace) -> bool:
    return drawn(p) == stored(ws)


def at(view: GraphView, scene: QPointF) -> QPoint:
    return view.viewer.mapFromScene(scene)


def port_at(view: GraphView, node: str, port: str) -> QPoint:
    s = view._shown[node]
    item = s.outs[port] if port in s.outs else s.ins[port]
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


def wire(qtbot: Any, p: BehaviourPanel, src: str, src_port: str, dst: str, dst_port: str) -> None:
    drag(p.view, port_at(p.view, src, src_port), port_at(p.view, dst, dst_port))
    qtbot.wait(30)


def field(p: BehaviourPanel, node: str, name: str) -> Any:
    return p.view._shown[node].fields[name]


def rank(*places: int) -> str:
    """The badge of the paths at `places`."""
    return " ".join(f"#{k}" for k in places)


def badge(p: BehaviourPanel, node: str) -> str:
    return str(p.view._shown[node].view.badge)


# ---- what it shows --------------------------------------------------------------------------- #


def test_the_dock_is_registered_at_the_bottom() -> None:
    [dock] = [d for d in MonsterWorkspace().docks() if d.label == "Behaviour"]
    assert (dock.area, dock.shown, dock.alone) == ("bottom", False, False)


class Hosted(FakeWorkspace):
    """A fake workspace carrying the monster workspace's own Behaviour dock, and a strip that
    is never tabbed under it, as the Timeline is."""

    def __init__(self, own: MonsterWorkspace) -> None:
        super().__init__("monster")
        self.own = own

    def docks(self) -> tuple[Dock, ...]:
        [spec] = [d for d in self.own.docks() if d.label == "Behaviour"]
        strip = Dock("Strip", "bottom", lambda s: kit.Panel(), "A panel never tabbed", alone=True)
        return (*super().docks(), spec, strip)


def test_the_window_opens_the_dock(
    make_window: Callable[..., Any], qtbot: Any, port_doc: PortDocument
) -> None:
    own = MonsterWorkspace()
    own.doc = port_doc
    host = Hosted(own)
    w = make_window(host)
    dock = w.findChild(QDockWidget, "monster/Behaviour")
    strip = w.findChild(QDockWidget, "monster/Strip")
    assert dock is not None and strip is not None and not dock.isVisible()
    assert isinstance(dock.widget(), BehaviourPanel) and kit.missing_tips(dock.widget()) == []
    host.focus = "Behaviour"  # what the Moves dock's button asks for
    w.studio.changed()
    w.sync()
    qtbot.wait(20)
    assert dock.isVisible() and w.dockWidgetArea(dock) == Qt.DockWidgetArea.BottomDockWidgetArea
    assert dock.height() > 2 * strip.height() and dock.geometry().bottom() < strip.geometry().top()


def test_empty(qtbot: Any) -> None:
    p = make(qtbot, MonsterWorkspace())
    assert p.pages.currentWidget() is p.no_scene


def test_every_block_and_move_is_a_node(panel: BehaviourPanel, ws: MonsterWorkspace) -> None:
    assert in_step(panel, ws) and len(panel.view._shown) == 3 + 4
    assert panel.title.text() == f"3 blocks · 1 of {model.SEAM_RULES} paths · 2 loose"
    s = panel.view._shown
    assert (s["b1"].view.name, s[STAMP].view.name) == (KINDS["played_for"].title, "stamp")
    assert list(s["b1"].ins) == ["in"] and list(s["b1"].outs) == ["out"]
    assert list(s[STAMP].ins) == ["play"] and list(s[STAMP].outs) == ["while playing", "then"]
    assert "plays walk" in s[B.move_id("charge")].view.toolTip()
    assert s["b1"].view.toolTip() == KINDS["played_for"].tip
    assert kit.missing_tips(panel) == []


def test_a_block_without_a_path_is_dimmed_and_a_path_has_its_place(panel: BehaviourPanel) -> None:
    s = panel.view._shown
    assert [badge(panel, n) for n in ("b1", "b2", "b3")] == [rank(1), "", ""]
    assert s["b1"].view.opacity() == 1.0 and s["b2"].view.opacity() < 1.0


def test_a_refused_path_carries_the_reason(panel: BehaviourPanel, ws: MonsterWorkspace) -> None:
    def bad(m: Any) -> None:
        m.behaviour.blocks["b2"].next = ["b4"]
        m.behaviour.blocks["b4"] = Block("cooldown", (400.0, 160.0), {"frames": 5}, next=["b5"])
        m.behaviour.blocks["b5"] = Block(
            "cooldown", (600.0, 160.0), {"frames": 9}, play=["stamp_2"]
        )

    doc(ws).edit(bad)
    ws.sync()
    panel.sync()
    assert badge(panel, "b5") == f"{rank(2)} {WARN}" and badge(panel, "b2") == WARN
    tip = panel.view._shown["b4"].view.toolTip()
    assert tip.startswith(KINDS["cooldown"].tip) and "two 'Then wait' blocks" in tip
    assert WARN not in badge(panel, "b1")


def test_the_zinogre_opens(
    qtbot: Any, ws: MonsterWorkspace, zinogre_toml: Path, synthetic_pac: bytes
) -> None:
    from mhfu_studio.monster.core.scene import Scene

    zin = PortDocument.open(zinogre_toml)
    ws.load(Scene.from_bytes(synthetic_pac, "z", path=zinogre_toml.with_suffix(".bin")), zin)
    p = make(qtbot, ws)
    m = zin.manifest
    assert len(m.behaviour.blocks) == 36 and len(p.view._shown) == 36 + len(m.moves)
    plays = sorted(badge(p, i) for i in m.behaviour.blocks if badge(p, i))
    assert plays == sorted(f"#{k}" for k in range(1, 17))
    for k, path in enumerate(B.read(m).paths, 1):  # a path that plays nothing has its place too
        assert badge(p, path.blocks[-1]) == rank(k)
    assert [badge(p, i) for i in ("b15", "b17", "b23")] == [rank(1), rank(2), rank(3)]
    assert badge(p, "b4") == rank(4) and badge(p, "b36") == rank(16)
    assert field(p, "b23", "counter").get_value() == "flinches"
    assert options(p, "b15", "flag") == ["flinches", "in_combat"]
    assert all(p.view._shown[i].view.opacity() == 1.0 for i in m.behaviour.blocks)
    assert in_step(p, ws)
    wrote = {i: p.view._shown[i].view.xy_pos for i in m.behaviour.blocks}
    assert wrote["b12"] == [float(model.LEFT), 4.0 * model.ROW]
    note = field(p, "b18", "@note").get_value()
    assert note.startswith("horns break")
    assert field(p, "b18", "part").get_value() == 0
    assert field(p, "b2", "hi").get_value() == 1000.0


def test_spread_out_is_one_step(panel: BehaviourPanel, ws: MonsterWorkspace) -> None:
    before = doc(ws).manifest
    panel.spread.click()
    assert doc(ws).manifest.behaviour.blocks["b2"].at == (390.0, 272.0) and in_step(panel, ws)
    assert panel.view._shown["b2"].view.xy_pos == [390.0, 272.0]
    doc(ws).undo()
    assert doc(ws).manifest == before


def test_part_and_main_options(panel: BehaviourPanel, ws: MonsterWorkspace) -> None:
    doc(ws).edit(lambda m: m.behaviour.blocks.__setitem__("b4", Block("on_flinch", (0.0, 0.0))))
    panel.sync()
    part = field(panel, "b4", "part")
    labels = [part._ctl.itemText(i) for i in range(part._ctl.count())]
    assert labels[:3] == ["any", "head", "part 0"] and part.get_value() is None
    assert len(labels) == len(model.PARTS) + 1 and "part 1" not in labels


# ---- gestures -------------------------------------------------------------------------------- #


def test_the_palette_offers_every_kind(panel: BehaviourPanel) -> None:
    menu = panel.view.viewer.context_menus()["graph"]
    offered = {a.text() for sub in menu.actions() for a in sub.menu().actions()}
    assert offered == {k.title for k in KINDS.values()}
    titles = [a.text() for a in menu.actions()]
    assert titles[:4] == ["Events", "State", "Conditions", "Modifiers"]
    assert titles[4:] in ([], ["Effects"])


def create(qtbot: Any, p: BehaviourPanel, title: str, where: QPoint) -> None:
    vp = p.view.viewer.viewport()
    QTest.mouseMove(vp, where)
    QTest.mousePress(vp, Qt.MouseButton.RightButton, NONE, where)
    QTest.mouseRelease(vp, Qt.MouseButton.RightButton, NONE, where)
    menu = p.view.viewer.context_menus()["graph"]
    [act] = [a for sub in menu.actions() for a in sub.menu().actions() if a.text() == title]
    act.trigger()
    qtbot.wait(30)


def test_the_users_example_through_the_canvas(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    """On part break (head) -> play flinch_head."""
    before = doc(ws).manifest
    create(qtbot, panel, KINDS["on_part_broken"].title, QPoint(300, 480))
    assert "b4" in doc(ws).manifest.behaviour.blocks and panel.view.selected() == ["b4"]
    combo = field(panel, "b4", "part")._ctl
    combo.setCurrentIndex(combo.findText("head"))
    qtbot.wait(30)
    assert doc(ws).manifest.behaviour.blocks["b4"].params == {"part": 1}
    wire(qtbot, panel, "b4", "out", FLINCH, "play")
    m = doc(ws).manifest
    assert m.behaviour.blocks["b4"].play == ["flinch_head"]
    assert Rule("flinch_head", on="part_broken", part=1) in model.compile(m)
    assert badge(panel, "b4") == rank(2) and in_step(panel, ws)
    for _ in range(3):  # the wire, the part, the block
        doc(ws).undo()
        ws.refresh()
        panel.sync()
        assert in_step(panel, ws)
    assert doc(ws).manifest == before
    for _ in range(3):
        doc(ws).redo()
        ws.refresh()
        panel.sync()
        assert in_step(panel, ws)
    assert doc(ws).manifest.behaviour.blocks["b4"].play == ["flinch_head"]


def test_a_refused_link_goes_back(qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace) -> None:
    before = doc(ws).manifest
    wire(qtbot, panel, STAMP, "then", "b3", "in")  # a move's then hands to a move
    assert doc(ws).manifest is before and "cannot feed" in ws.message
    assert in_step(panel, ws)
    wire(qtbot, panel, "b3", "out", "b3", "in")
    assert doc(ws).manifest is before and in_step(panel, ws)


def test_a_link_between_blocks_and_into_a_move(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    wire(qtbot, panel, "b2", "out", "b3", "in")
    wire(qtbot, panel, "b3", "out", STEP, "play")
    wire(qtbot, panel, STAMP, "while playing", "b2", "in")
    b = doc(ws).manifest.behaviour
    assert b.blocks["b2"].next == ["b3"] and b.blocks["b3"].play == ["stamp_2"]
    assert b.moves["stamp"].during == ["b2"] and in_step(panel, ws)
    assert badge(panel, "b3") == rank(2)  # b2 sits lower than b1: the path ranks after it
    doc(ws).undo()
    doc(ws).undo()
    doc(ws).undo()
    ws.refresh()
    panel.sync()
    assert in_step(panel, ws) and doc(ws).manifest.behaviour.blocks["b2"].next == []


def test_then_hands_over_and_replaces(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    wire(qtbot, panel, STAMP, "then", STEP, "play")
    assert doc(ws).manifest.moves["stamp"].after == "stamp_2" and in_step(panel, ws)
    wire(qtbot, panel, STAMP, "then", FLINCH, "play")
    assert doc(ws).manifest.moves["stamp"].after == "flinch_head" and in_step(panel, ws)
    assert [k for k in pipes(panel.view) if k.src == STAMP and k.src_port == "then"] == [
        LinkSpec(STAMP, "then", FLINCH, "play")
    ]


def pick_up(p: BehaviourPanel, w: LinkSpec) -> QPoint:
    """A point on wire `w` near its input end: that end is picked up."""
    return at(p.view, p.view._pipes()[w].path().pointAtPercent(0.7))


def test_a_reroute_is_one_undo_step(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    before = doc(ws).manifest
    drag(
        panel.view,
        pick_up(panel, LinkSpec("b1", "out", STAMP, "play")),
        port_at(panel.view, STEP, "play"),
    )
    qtbot.wait(30)
    assert doc(ws).manifest.behaviour.blocks["b1"].play == ["stamp_2"], "unlinked, then linked"
    assert in_step(panel, ws)
    doc(ws).undo()
    assert doc(ws).manifest == before, "both in one step"
    ws.refresh()
    panel.sync()
    assert in_step(panel, ws)


def test_a_refused_reroute_puts_the_wire_back(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    before = doc(ws).manifest
    during = LinkSpec(B.move_id("charge"), "while playing", "b1", "in")
    drag(panel.view, pick_up(panel, during), port_at(panel.view, STEP, "play"))
    qtbot.wait(30)
    assert doc(ws).manifest is before and "cannot feed" in ws.message and in_step(panel, ws)


def test_delete_takes_a_block_and_its_links(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    wire(qtbot, panel, "b2", "out", "b3", "in")
    wire(qtbot, panel, "b3", "out", FLINCH, "play")
    full = doc(ws).manifest
    panel.view.select(["b3"])
    QTest.keyClick(panel.view, Qt.Key.Key_Delete)
    qtbot.wait(30)
    b = doc(ws).manifest.behaviour
    assert "b3" not in b.blocks and b.blocks["b2"].next == []
    assert in_step(panel, ws)
    doc(ws).undo()
    assert doc(ws).manifest == full


def test_a_move_node_is_not_deleted(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    before = doc(ws).manifest
    panel.view.select([STAMP])
    QTest.keyClick(panel.view, Qt.Key.Key_Delete)
    qtbot.wait(30)
    assert doc(ws).manifest is before and "Moves dock" in ws.message and in_step(panel, ws)


def test_cutting_a_wire_unlinks_it(qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace) -> None:
    wire(qtbot, panel, STAMP, "then", STEP, "play")
    pipe = panel.view._pipes()[LinkSpec(STAMP, "then", STEP, "play")]
    pipe.setSelected(True)
    QTest.keyClick(panel.view, Qt.Key.Key_Delete)
    qtbot.wait(30)
    assert doc(ws).manifest.moves["stamp"].after is None and in_step(panel, ws)


def test_params_and_notes(qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace) -> None:
    before = doc(ws).manifest
    field(panel, "b1", "frames")._ctl.setValue(45)
    qtbot.wait(30)
    assert doc(ws).manifest.behaviour.blocks["b1"].params == {"frames": 45}
    far = field(panel, "b3", "hi")._ctl
    far.setValue(900)
    qtbot.wait(30)
    assert doc(ws).manifest.behaviour.blocks["b3"].params == {"lo": 0.0, "hi": 900.0}
    far.setValue(far.minimum())  # "none"
    qtbot.wait(30)
    assert doc(ws).manifest.behaviour.blocks["b3"].params == {"lo": 0.0}
    note = field(panel, "b1", "@note")._ctl
    note.setText("the first")
    note.editingFinished.emit()
    qtbot.wait(30)
    assert doc(ws).manifest.behaviour.blocks["b1"].label == "the first"
    for _ in range(4):
        doc(ws).undo()
    ws.refresh()
    panel.sync()
    assert doc(ws).manifest == before and in_step(panel, ws)
    assert field(panel, "b1", "frames").get_value() == 20


def test_a_refused_value_goes_back(qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace) -> None:
    mains = B.Gesture(doc(ws))
    mains.run(lambda e: B.add_block(e, "host_state", (0, 0)))
    ws.edit("", mains.commit)
    panel.sync()
    picks = field(panel, "b4", "mains")._ctl.menu().actions()
    assert [a.isChecked() for a in picks][:2] == [True, False]
    picks[0].trigger()  # unticks the only main state
    qtbot.wait(30)
    assert doc(ws).manifest.behaviour.blocks["b4"].params == {"mains": [0]}
    assert "main states" in ws.message and field(panel, "b4", "mains").get_value() == (0,)


def test_a_drag_moves_a_node_and_a_group_drag_is_one_step(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    before = doc(ws).manifest
    a = title_at(panel.view, "b2")
    drag(panel.view, a, a + QPoint(40, 25))
    qtbot.wait(30)
    x, y = doc(ws).manifest.behaviour.blocks["b2"].at
    assert x > 260.0 and y > 160.0
    doc(ws).undo()
    assert doc(ws).manifest == before
    ws.refresh()
    panel.sync()
    panel.view.select(["b2", "b3"])
    a = title_at(panel.view, "b2")
    drag(panel.view, a, a + QPoint(30, 0))
    qtbot.wait(30)
    b = doc(ws).manifest.behaviour.blocks
    assert b["b2"].at[0] > 260.0 and b["b3"].at[0] > 260.0
    doc(ws).undo()
    assert doc(ws).manifest == before, "two nodes moved, one step"


def test_the_first_drag_of_a_move_writes_its_node(
    qtbot: Any, ws: MonsterWorkspace, panel: BehaviourPanel
) -> None:
    doc(ws).edit(lambda m: m.behaviour.moves.pop("stamp_2"))
    ws.sync()
    panel.sync()
    here = panel.view._shown[STEP].view.xy_pos
    assert here == list(B.spots(doc(ws).manifest)["stamp_2"])
    a = title_at(panel.view, STEP)
    drag(panel.view, a, a + QPoint(0, 60))
    qtbot.wait(30)
    node = doc(ws).manifest.behaviour.moves["stamp_2"]
    assert node.at[1] > here[1] and node.during == []


# ---- following the rest of the studio -------------------------------------------------------- #


def test_picking_a_move_node_selects_the_move(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    a = title_at(panel.view, STAMP)
    QTest.mouseClick(panel.view.viewer.viewport(), LEFT, NONE, a)
    qtbot.wait(30)
    assert ws.move == "stamp" and panel.view.selected() == [STAMP]
    QTest.mouseClick(panel.view.viewer.viewport(), LEFT, NONE, title_at(panel.view, "b2"))
    qtbot.wait(30)
    assert ws.move == "stamp", "a block picks no move"


def test_a_move_selected_elsewhere_is_selected_here(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    panel.view.select(["b3"])
    ws.select_move("flinch_head")
    panel.sync()
    assert panel.view.selected() == [FLINCH]


def test_a_finding_on_a_block_picks_and_frames_it(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    ws.reveal(("block", "b3"), "behaviour")
    assert ws.take_focus() == "Behaviour"
    ws.reveal(("block", "b3"), "behaviour")
    panel.sync()
    assert panel.view.selected() == ["b3"]
    centre = panel.view.viewer.mapToScene(panel.view.viewer.viewport().rect().center())
    node = panel.view._shown["b3"].view
    assert (
        abs(centre.x() - node.scenePos().x()) < 400 and abs(centre.y() - node.scenePos().y()) < 150
    )
    ws.reveal(("block", "b99"), "behaviour")
    assert ws.take_block() is None


def test_the_canvas_takes_the_theme(qtbot: Any, panel: BehaviourPanel) -> None:
    scene = panel.view.viewer.scene()
    try:
        theme.apply(theme.theme("Moss", False))
        panel.sync()
        light = scene.background_color
        theme.apply(theme.theme("Ember", True))
        panel.sync()
        assert scene.background_color != light
        want = theme.color(theme.current().view)
        assert tuple(scene.background_color) == (want.red(), want.green(), want.blue())
    finally:
        theme.apply(theme.theme("Ember", True))


def test_renaming_a_move_keeps_the_graph(panel: BehaviourPanel, ws: MonsterWorkspace) -> None:
    ws.select_move("stamp")
    ws.rename_move("stomp")
    panel.sync()
    assert doc(ws).manifest.behaviour.blocks["b1"].play == ["stomp"]
    assert in_step(panel, ws) and badge(panel, "b1") == rank(1)


# ---- effects, names, sides and paths that play nothing -------------------------------------- #


def test_a_path_that_plays_nothing_ranks_first_wherever_it_sits(
    panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    def add(m: Any) -> None:
        b = m.behaviour.blocks
        b["b4"] = Block("on_enraged", (260.0, 500.0), next=["b5"])
        b["b5"] = Block("counter_add", (540.0, 500.0), {"counter": "hits"})

    doc(ws).edit(add)
    ws.sync()
    panel.sync()
    assert [badge(panel, n) for n in ("b1", "b2", "b3", "b5")] == [rank(2), "", "", rank(1)]
    assert panel.title.text() == f"5 blocks · 2 of {model.SEAM_RULES} paths · 2 loose"
    assert panel.view._shown["b5"].view.opacity() == 1.0
    assert "play nothing run before" in panel.hint.text()


@pytest.mark.parametrize(
    ("family", "dark"), [("Ember", True), ("Ember", False), ("Moss", True), ("Moss", False)]
)
def test_the_effect_role_has_a_colour_of_its_own(qtbot: Any, family: str, dark: bool) -> None:
    try:
        theme.apply(theme.theme(family, dark))
        seen = {role: theme.color(ink).getRgbF()[:3] for role, ink in roles().items()}
    finally:
        theme.apply(theme.theme("Ember", True))
    assert set(seen) == {"event", "state", "condition", "modifier", "effect", "move"}
    for role, rgb in seen.items():
        if role != "effect":
            gap = max(abs(a - b) for a, b in zip(rgb, seen["effect"], strict=True))
            assert gap > 0.2, role


def test_an_effect_block_wears_the_effect_colour(
    new_kinds: tuple[Kind, ...], panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    doc(ws).edit(
        lambda m: m.behaviour.blocks.__setitem__(
            "b4", Block("t_count", (0.0, 400.0), {"counter": "hits"})
        )
    )
    try:
        for dark in (True, False):
            theme.apply(theme.theme("Ember", dark))
            panel.sync()
            edge = theme.color(EFFECT[dark])
            have = panel.view._shown["b4"].view.border_color
            assert tuple(have[:3]) == (edge.red(), edge.green(), edge.blue())
    finally:
        theme.apply(theme.theme("Ember", True))


def options(p: BehaviourPanel, node: str, name: str) -> list[str]:
    ctl = field(p, node, name)._ctl
    return [ctl.itemText(i) for i in range(ctl.count())]


def type_name(qtbot: Any, p: BehaviourPanel, node: str, name: str, text: str) -> None:
    ctl = field(p, node, name)._ctl
    ctl.setEditText(text)
    ctl.lineEdit().editingFinished.emit()
    qtbot.wait(30)


def test_counters_and_flags_through_the_canvas(
    qtbot: Any, new_kinds: tuple[Kind, ...], panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    count, flag = new_kinds[0], new_kinds[1]
    create(qtbot, panel, count.title, QPoint(300, 480))
    assert doc(ws).manifest.behaviour.blocks["b4"].params == {"counter": "counter1", "by": 1}
    create(qtbot, panel, flag.title, QPoint(60, 480))
    assert doc(ws).manifest.behaviour.blocks["b5"].params == {"flag": "flag1"}
    assert options(panel, "b4", "counter") == options(panel, "b5", "flag") == ["counter1", "flag1"]
    type_name(qtbot, panel, "b5", "flag", "armor_hits")
    assert doc(ws).manifest.behaviour.blocks["b5"].params == {"flag": "armor_hits"}
    assert options(panel, "b4", "counter") == ["armor_hits", "counter1"]
    assert field(panel, "b5", "flag").get_value() == "armor_hits" and in_step(panel, ws)
    doc(ws).undo()
    assert doc(ws).manifest.behaviour.blocks["b5"].params == {"flag": "flag1"}
    doc(ws).redo()
    ws.refresh()
    panel.sync()
    assert options(panel, "b5", "flag") == ["armor_hits", "counter1"]
    kept = doc(ws).manifest
    type_name(qtbot, panel, "b5", "flag", "Bad Name")
    assert doc(ws).manifest is kept and field(panel, "b5", "flag").get_value() == "armor_hits"


def test_signals_and_sides_through_the_canvas(
    qtbot: Any, new_kinds: tuple[Kind, ...], panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    signal, side = new_kinds[2], new_kinds[3]
    create(qtbot, panel, signal.title, QPoint(300, 480))
    create(qtbot, panel, side.title, QPoint(60, 480))
    blocks = doc(ws).manifest.behaviour.blocks
    assert blocks["b4"].params == {"name": "signal1"} and blocks["b5"].params == {
        "sides": ["front"]
    }
    type_name(qtbot, panel, "b4", "name", "rage")
    assert doc(ws).manifest.behaviour.blocks["b4"].params == {"name": "rage"}
    create(qtbot, panel, signal.title, QPoint(500, 480))
    assert doc(ws).manifest.behaviour.blocks["b6"].params == {"name": "signal1"}
    assert options(panel, "b4", "name") == ["rage", "signal1"]
    sides = field(panel, "b5", "sides")._ctl
    picks = sides.menu().actions()
    assert [a.text() for a in picks] == list(new_kinds[3].params[0].choices)
    assert sides.text() == "front"
    picks[3].trigger()
    qtbot.wait(30)
    assert doc(ws).manifest.behaviour.blocks["b5"].params == {"sides": ["front", "behind"]}
    assert sides.text() == "front, behind" and in_step(panel, ws)
    picks[0].trigger()
    qtbot.wait(30)
    assert doc(ws).manifest.behaviour.blocks["b5"].params == {"sides": ["behind"]}
    kept = doc(ws).manifest
    picks[3].trigger()  # none left
    qtbot.wait(30)
    assert doc(ws).manifest is kept and "side" in ws.message
    assert field(panel, "b5", "sides").get_value() == ("behind",)


def put(qtbot: Any, p: BehaviourPanel, node: str, name: str, value: Any) -> None:
    """Sets a node's parameter through its widget, as a hand would."""
    ctl = field(p, node, name)._ctl
    if isinstance(ctl, QToolButton):
        [act] = [a for a in ctl.menu().actions() if a.text() == value]
        act.trigger()
    elif isinstance(ctl, QComboBox) and ctl.isEditable():
        type_name(qtbot, p, node, name, value)
        return
    elif isinstance(ctl, QComboBox):
        ctl.setCurrentIndex(ctl.findText(value))
    else:
        ctl.setValue(value)
    qtbot.wait(30)


#: (kind, param, what is put in the widget, what the manifest then holds)
EDITS = [
    ("counter_is", "counter", "hits_taken", "hits_taken"),
    ("counter_is", "test", "below", "below"),
    ("counter_is", "value", 7, 7),
    ("flag_is", "flag", "armor", "armor"),
    ("flag_is", "state", "clear", "clear"),
    ("counter_add", "by", -3, -3),
    ("counter_set", "to", 9, 9),
    ("flag_set", "state", "clear", "clear"),
    ("monster_hp", "lo", 10, 10),
    ("monster_hp", "hi", 50, 50),
    ("part_broken", "part", "head", 1),
    ("part_broken", "state", "not broken", "not broken"),
    ("rage", "state", "calm", "calm"),
    ("hunter_side", "sides", "left", ["front", "left"]),
    ("chance", "percent", 50, 50),
    ("on_signal", "name", "roar", "roar"),
]


def test_every_real_kind_draws_and_edits(
    qtbot: Any, panel: BehaviourPanel, ws: MonsterWorkspace
) -> None:
    ids: dict[str, str] = {}
    for n, kind in enumerate(KINDS):
        ids[kind] = model.new_id(doc(ws).manifest.behaviour)
        B.add_block(doc(ws), kind, (300.0 + 20 * n, 900.0 + 60 * n))
    ws.sync()
    panel.sync()
    assert in_step(panel, ws)
    blocks = doc(ws).manifest.behaviour.blocks
    for kind, i in ids.items():
        held = model.params(blocks[i])
        for p in KINDS[kind].params:
            shown = field(panel, i, p.name).get_value()
            want = held.get(p.name)
            assert shown == (tuple(want) if isinstance(want, list) else want), (kind, p.name)
    drawn = doc(ws).manifest
    for kind, name, put_, stored in EDITS:
        put(qtbot, panel, ids[kind], name, put_)
        assert doc(ws).manifest.behaviour.blocks[ids[kind]].params[name] == stored, (kind, name)
        assert in_step(panel, ws)
    for _ in EDITS:
        doc(ws).undo()
    assert doc(ws).manifest == drawn
