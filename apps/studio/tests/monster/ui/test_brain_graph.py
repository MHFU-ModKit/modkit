# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Brain tab's graph pane over a small script the workspace builds for real, and over the
real em75: what a scope draws, how the user moves in it and edits it, and what the game and the
file add to it. The tree canvas it draws on is tested here too (`GraphView(tree=True)`)."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from mhfu import files, inject
from mhfu.em.intel import SpeciesIntel
from mhfu.em.script import catalogue as cat
from mhfu.em.script import ir
from mhfu.em.script.graph import ENTRY, ROOT
from mhfu.em.script.opcodes import BY_NAME
from mhfu.files import Extracted
from mhfu_port.data import Data
from mhfu_studio.monster import brain_edit as be
from mhfu_studio.monster import brain_game as bg
from mhfu_studio.monster.brain_graph import Box, Scene
from mhfu_studio.monster.node_size import NODE_W
from mhfu_studio.monster.panels import brain_graph as pane_mod
from mhfu_studio.monster.panels import brain_names
from mhfu_studio.monster.panels.brain_graph import GraphPane
from mhfu_studio.monster.panels.node_graph import (
    PROXY_PX,
    FrameSpec,
    GraphView,
    LinkSpec,
    NodeSpec,
)
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell import places
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit, theme
from NodeGraphQt.errors import PortError
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu, QToolButton, QWidget

from ..conftest import end, enter, header, ins, routine
from ..test_workspace_brain import Clock, settle
from .test_node_graph import at, drag, port_at

LEFT, NONE = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
ROW = 75
WARN = pane_mod.LEVEL_MARK["warning"]


def rich(row: int = ROW) -> ir.Program:
    """base_0: a hub move, a pick of three branches (the third holds an if), a call, an if over
    the collapse size and an end; base_1: a switch of three cases and a default, over the
    collapse size too; call_0: a wait; long: thirty moves. One header."""
    pick = be.default_node("random")
    assert isinstance(pick, ir.Block)
    latch = BY_NAME["if_latch"]
    pick.arms = [
        ir.Arm(1, {"weight": 8}, [enter(1, 4), ins("call", routine=0)]),
        ir.Arm(1, {"weight": 16}, [enter(1, 5)]),
        ir.Arm(1, {"weight": 8}, [ir.Block(latch, {}, [enter(7, 7)], [])]),
    ]
    big = ir.Block(latch, {}, [enter(2, k) for k in range(14)], [])
    switch = be.default_node("switch_section")
    assert isinstance(switch, ir.Block)
    switch.arms = [
        ir.Arm(1, {"section": k}, [enter(0, 3 + j) for j in range(5)]) for k in range(3)
    ] + [ir.Arm(2, {}, [enter(0, 9)])]
    items: list[ir.Item] = [
        routine("base_0", enter(3, 0), pick, ins("call", routine=0), big, end(), va=0x2000),
        routine("base_1", switch, end(), va=0x2400),
        routine("call_0", ins("wait", frames=5), end(), va=0x2500),
        routine("long", *[enter(4, k) for k in range(30)], end(), va=0x2600),
        ir.Table("base_l", ["base_0", "base_1"], 0x2800),
        ir.Table("call_l", ["call_0", None], 0x2810),
        header("h0", tuple(range(17)), base="base_l", call="call_l"),
    ]
    p = ir.Program(row, 0x2000, items, 0x2900)
    p.sha1 = "ab" * 20
    for k, n in enumerate(p.nodes()):
        n.id, n.pc = f"{row}:{0x2000 + 4 * k:08X}", 0x2000 + 4 * k
    return p


def node_of(p: ir.Program, routine_name: str, pick: Callable[[ir.Located], bool]) -> str:
    """The id of the first node of a routine that `pick` takes."""
    r = p.item(routine_name)
    assert isinstance(r, ir.Routine)
    return next(n.id for n in ir.walk(r.body) if pick(n))


def hub_id(p: ir.Program) -> str:
    return node_of(p, "base_0", lambda n: isinstance(n, ir.Insn) and n.op.name == "enter")


def pick_id(p: ir.Program) -> str:
    return node_of(p, "base_0", lambda n: isinstance(n, ir.Block) and n.op.name == "random")


def call_id(p: ir.Program) -> str:
    return node_of(p, "base_0", lambda n: isinstance(n, ir.Insn) and n.op.name == "call")


def big_id(p: ir.Program) -> str:
    return node_of(p, "base_0", lambda n: isinstance(n, ir.Block) and len(n.head) == 14)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test finds the owner's memory stick or writes their cache."""
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", (str(tmp_path / "no-stick"),))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def body_at(view: GraphView, node: str) -> QPoint:
    """A point on a node clear of its ports (the input at the top middle is locked)."""
    item = view._shown[node].view
    return at(view, item.mapToScene(QPointF(24.0, item.boundingRect().height() / 2)))


def action(menu: QMenu, *path: str) -> QAction:
    """The action at `path` through the submenus of `menu`."""
    found = None
    for name in path:
        found = next(a for a in menu.actions() if a.text() == name)
        if found.menu() is not None and name != path[-1]:
            menu = found.menu()
    assert found is not None
    return found


class Rig:
    """A workspace over `rich()`, a studio and a pane that syncs as the window would."""

    def __init__(self, qtbot: Any, ws: MonsterWorkspace, clock: Clock) -> None:
        self.ws, self.clock, self.qtbot = ws, clock, qtbot
        self.studio = Studio([ws])
        self.pane = GraphPane(ws, self.studio)
        self.studio.listen(self.pane.sync)
        qtbot.addWidget(self.pane)
        self.pane.resize(1100, 800)
        self.pane.show()
        qtbot.waitExposed(self.pane)
        self.program = rich()
        self.opened: list[tuple[str, str]] = []
        self.picked: list[str] = []
        self.followed: list[str] = []
        self.pane.scope_opened.connect(lambda r, s: self.opened.append((r, s)))
        self.pane.node_picked.connect(self.picked.append)
        self.pane.link_followed.connect(self.followed.append)
        settle(ws, clock)

    @property
    def view(self) -> GraphView:
        return self.pane.view

    def show(self, routine_name: str = "base_0", scope: str = ROOT) -> None:
        self.pane.show_scope(ROW, routine_name, scope)
        self.qtbot.wait(20)

    def edit(self, g: be.Gesture) -> bool:
        ok = self.ws.edit_brain(g, ROW)
        settle(self.ws, self.clock)
        self.pane.sync()
        return ok

    def spec(self, node: str) -> NodeSpec:
        """What the canvas last drew for a node."""
        return next(n for n in self.view._nodes if n.id == node)

    @property
    def scene(self) -> Scene:
        assert self.pane._scene is not None
        return self.pane._scene

    def box(self, node: str) -> Box:
        b = self.pane._box(node)
        assert b is not None
        return b

    def ids(self) -> set[str]:
        return {b.id for b in self.scene.nodes}

    def double_click(self, node: str) -> None:
        QTest.mouseDClick(self.view.viewer.viewport(), LEFT, NONE, body_at(self.view, node))
        self.qtbot.wait(30)

    def click(self, node: str) -> None:
        QTest.mouseClick(self.view.viewer.viewport(), LEFT, NONE, body_at(self.view, node))
        self.qtbot.wait(30)


def built(tmp_path: Path, pac: bytes, intel: SpeciesIntel) -> tuple[MonsterWorkspace, Clock]:
    """A workspace on Tigrex's model PAC with `rich()` as the script of rows 75 and 76, and its
    debounce clock; the caller closes it."""
    (tmp_path / "data_files").mkdir()
    for sp in (75, 76):
        (tmp_path / "data_files" / f"file_{files.monster_pac(sp):05d}.bin").write_bytes(pac)
    game = Extracted(tmp_path)
    ws = MonsterWorkspace(Data(game, game))
    ws.intel_cache[75] = intel
    ws.decode_brain = lambda g, row: rich(row)
    ws.scan_brain = lambda g: {75: 75, 76: 75}
    clock = Clock()
    ws.clock = clock
    ws.open(game.path(files.monster_pac(75)))
    return ws, clock


@pytest.fixture
def rig(qtbot: Any, tmp_path: Path, synthetic_pac: bytes, intel75: SpeciesIntel) -> Iterator[Rig]:
    ws, clock = built(tmp_path, synthetic_pac, intel75)
    yield Rig(qtbot, ws, clock)
    ws.close()


# ---- what a scope draws -------------------------------------------------------------------- #


def test_a_scope_draws_its_nodes_and_frames(rig: Rig) -> None:
    rig.show()
    scene = rig.pane._scene
    assert scene is not None and len(scene.frames) == 4
    assert set(rig.view._shown) == rig.ids() and len(rig.ids()) == len(scene.nodes)
    assert [f.id for f in scene.frames] == list(rig.view._backdrops)
    assert set(rig.view._pipes()) == {
        LinkSpec(w.src, w.src_port, w.dst, w.dst_port) for w in scene.wires
    }
    assert rig.pane.pages.currentWidget() is rig.pane.pages.page


def test_nodes_show_title_summary_ports_and_links(rig: Rig) -> None:
    p = rig.program
    rig.show()
    pick, call = rig.spec(pick_id(p)), rig.spec(call_id(p))
    assert pick.title == "Pick one" and pick.captions == ("8/32", "16/32", "8/32")
    assert pick.outputs == ("p0", "p1", "p2")
    assert call.link == "→ call_0" and "call to call_0" in call.tip
    big = rig.spec(big_id(p))
    assert big.summary.endswith("15 nodes") and big.link.startswith(pane_mod.ARROW)
    assert "Double-click to open" in big.tip


def test_the_roles_cover_every_kind() -> None:
    assert set(pane_mod.CODE) == {k.name.lower() for k in cat.Kind} | {"entry", "join"}
    assert set(pane_mod.CODE.values()) <= set(theme.CODE_KINDS)
    assert set(pane_mod.tags()) >= {*theme.EDIT_KINDS, *theme.MARK_KINDS}


def test_the_canvas_is_drawn_at_the_sizes_it_measured(rig: Rig) -> None:
    rig.show()
    scene = rig.pane._scene
    assert scene is not None
    drawn = rig.view.sizes()
    for b in scene.nodes:
        assert drawn[b.id] == pytest.approx(b.size, abs=0.5)
        x, y = rig.view._shown[b.id].view.xy_pos
        assert (x, y) == pytest.approx(b.at, abs=0.5)
    assert any(b.size != pane_mod.model.estimate(b.summary) for b in scene.nodes)  # it did differ


def test_a_pass_that_changes_nothing_draws_nothing(rig: Rig) -> None:
    rig.show()
    before = rig.view._renders
    rig.pane.sync()
    rig.pane.sync()
    assert rig.view._renders == before
    rig.show()  # the same scope again is no change either
    assert rig.view._renders == before


def test_an_empty_pane_says_why(rig: Rig) -> None:
    assert rig.pane.pages.currentWidget() is rig.pane.pages.empty
    rig.show("nowhere")
    assert rig.pane.pages.currentWidget() is rig.pane.pages.empty
    assert "No routine nowhere" in rig.pane.empty.title.text()
    rig.show()
    assert rig.pane.pages.currentWidget() is rig.pane.pages.page


def test_every_control_has_a_tip(rig: Rig) -> None:
    rig.show()
    assert kit.missing_tips(rig.pane) == []


# ---- moving about -------------------------------------------------------------------------- #


def test_double_clicking_a_compact_block_opens_its_branch(rig: Rig) -> None:
    p = rig.program
    rig.show()
    big = big_id(p)
    assert rig.box(big).compact
    rig.double_click(big)
    (routine_name, scope) = rig.opened[-1]
    assert routine_name == "base_0" and scope == f"{big}#head"
    assert rig.pane._at == (ROW, "base_0", scope)
    assert len(rig.ids()) == 14  # the head's own statements
    assert [c.label for c in rig.pane._crumbs] == ["base_0", "Latch set", "then"]


def test_a_block_with_several_branches_offers_them(rig: Rig) -> None:
    p = rig.program
    rig.show("base_1")
    sw = node_of(p, "base_1", lambda n: isinstance(n, ir.Block))
    box = rig.box(sw)
    assert box.compact and len(box.opens) == 4
    menu = QMenu()
    rig.pane._menu(sw, menu)
    sub = action(menu, "Open").menu()
    assert sub is not None and len(sub.actions()) == 4
    rig.double_click(sw)
    popup = rig.pane.findChild(QMenu)
    assert popup is not None and popup.isVisible() and rig.opened == []
    assert [a.text() for a in popup.actions()][-1].startswith("otherwise")
    popup.actions()[1].trigger()
    popup.close()
    assert rig.opened == [("base_1", box.opens[1].scope)]
    assert rig.pane._at == (ROW, "base_1", box.opens[1].scope)


def trail(rig: Rig) -> list[QWidget]:
    """The breadcrumb's widgets without its separators: a button per way in, then the scope's
    own name."""
    items = [rig.pane.trail.itemAt(i).widget() for i in range(rig.pane.trail.count())]
    return [w for w in items if w.text() != pane_mod.SEP]


def test_a_breadcrumb_opens_its_scope(rig: Rig) -> None:
    p = rig.program
    rig.show()
    rig.double_click(big_id(p))
    here = trail(rig)
    assert [w.text() for w in here] == ["base_0", "Latch set", "then"]
    assert [isinstance(w, QToolButton) for w in here] == [True, True, False]
    assert all(w.toolTip() for w in here)
    rig.opened.clear()
    rig.picked.clear()
    here[1].click()  # the block, in the routine's scope: opened and selected
    assert rig.opened == [("base_0", ROOT)]
    assert rig.picked == [big_id(p)] and rig.view.selected() == [big_id(p)]
    assert [w.text() for w in trail(rig)] == ["base_0"]
    rig.double_click(big_id(p))
    trail(rig)[0].click()
    assert rig.opened[-1] == ("base_0", ROOT) and rig.pane._at == (ROW, "base_0", ROOT)


def test_double_clicking_a_call_follows_its_link(rig: Rig) -> None:
    p = rig.program
    rig.show()
    rig.double_click(call_id(p))
    assert rig.followed == ["call_0"] and rig.opened == []
    rig.followed.clear()
    rig.click(call_id(p))
    QTest.keyClick(rig.view.viewer, Qt.Key.Key_Return)
    rig.qtbot.wait(30)
    assert rig.followed == ["call_0"]
    rig.double_click(hub_id(p))  # a move has no link
    assert rig.followed == ["call_0"]


def test_a_link_to_nothing_follows_nowhere(rig: Rig) -> None:
    p = rig.program
    assert rig.edit(be.set_operands(call_id(p), {"routine": 1}))
    rig.show()
    rig.double_click(call_id(p))
    assert rig.followed == [] and "nothing" in rig.pane.status.text()


def test_selecting_tells_the_panel_and_selecting_for_it_does_not(rig: Rig) -> None:
    p = rig.program
    rig.show()
    rig.click(hub_id(p))
    assert rig.picked == [hub_id(p)]
    rig.pane.select(call_id(p))
    rig.qtbot.wait(30)
    assert rig.picked == [hub_id(p)] and rig.view.selected() == [call_id(p)]
    rig.pane.select("not-here")
    assert rig.view.selected() == [] and rig.picked == [hub_id(p)]
    rig.pane.select(None)
    rig.pane.select(hub_id(p))
    rig.qtbot.wait(30)
    assert rig.picked == [hub_id(p)]  # still the one the user's click told


def test_a_pick_does_not_outlive_its_scope(rig: Rig) -> None:
    p = rig.program
    rig.show()
    rig.click(hub_id(p))
    assert rig.pane._picked == hub_id(p)
    rig.show("call_0")
    assert rig.pane._picked is None
    rig.pane._shift(be.earlier)  # nothing picked: nothing moves
    assert rig.ws.species_doc is not None and not rig.ws.species_doc.dirty


def test_the_entry_and_a_join_are_not_picks(rig: Rig) -> None:
    rig.show()
    rig.click(ENTRY)
    assert rig.picked == [] and rig.pane._picked == ENTRY


def test_an_arm_is_a_pick(rig: Rig) -> None:
    rig.show()
    arm = next(f.id for f in rig.scene.frames if f.title == "16/32")
    rig.pane.select(arm)
    assert rig.view.selected_frames() == [arm]


# ---- gestures ------------------------------------------------------------------------------ #


def undone(rig: Rig) -> None:
    """One undo takes back the one gesture: the file is as it was opened."""
    doc = rig.ws.species_doc
    assert doc is not None and doc.dirty
    rig.studio.undo()
    assert not doc.dirty and not doc.can_undo()
    settle(rig.ws, rig.clock)
    rig.pane.sync()


def test_a_menu_gesture_is_one_undo_step(rig: Rig) -> None:
    p = rig.program
    rig.show()
    was = rig.ids()
    menu = QMenu()
    rig.pane._menu(hub_id(p), menu)
    action(menu, "Insert after", "Moves", "Move").trigger()
    assert rig.studio.message == "Insert Move"
    settle(rig.ws, rig.clock)
    rig.pane.sync()
    assert len(rig.ids()) == len(was) + 1 and (new := rig.ids() - was)
    assert rig.spec(next(iter(new))).flag == "new"
    undone(rig)
    assert rig.ids() == was


def test_each_gesture_of_the_menu_does_its_one_thing(rig: Rig) -> None:
    p = rig.program
    rig.show()
    pick = pick_id(p)

    def run(node: str, *path: str) -> None:
        menu = QMenu()
        rig.pane._menu(node, menu)
        action(menu, *path).trigger()
        settle(rig.ws, rig.clock)
        rig.pane.sync()

    run(pick, "Add arm")
    assert len(rig.box(pick).ports) == 4
    undone(rig)
    run(pick, "Remove", "16/32 (1 node)")
    assert [q.name for q in rig.box(pick).ports] == ["24/32", "8/32"]
    undone(rig)
    run(call_id(p), "Delete")
    assert call_id(p) not in rig.ids()
    undone(rig)
    run(hub_id(p), "Move down (Alt+Down)")
    assert rig.scene.plan.rg.scopes[ROOT].body[:2] == (pick, hub_id(p))
    undone(rig)
    run(hub_id(p), "Wrap in", "a pick")
    assert len([b for b in rig.scene.nodes if b.kind == "pick"]) == 2
    undone(rig)
    run(call_id(p), "Replace with…", "Flow", "Call (with return)")
    assert rig.spec(call_id(p)).title == "Call (with return)"  # the same node, as another opcode
    undone(rig)
    run(big_id(p), "Add else")
    assert "else" in [o.name for o in rig.box(big_id(p)).opens]
    undone(rig)


def test_back_to_the_games_undoes_one_nodes_edits(rig: Rig) -> None:
    p = rig.program
    assert rig.edit(be.set_pair(hub_id(p), 7, 7))
    rig.show()
    assert rig.spec(hub_id(p)).flag == "edited"
    menu = QMenu()
    rig.pane._menu(hub_id(p), menu)
    action(menu, "Back to the game's").trigger()
    settle(rig.ws, rig.clock)
    rig.pane.sync()
    assert rig.spec(hub_id(p)).flag == ""
    menu = QMenu()
    rig.pane._menu(hub_id(p), menu)
    assert "Back to the game's" not in [a.text() for a in menu.actions()]


def test_a_note_is_a_gesture_and_shows_in_the_tip(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = rig.program
    asked: list[str] = []
    monkeypatch.setattr(pane_mod, "ask_note", lambda parent, cur: asked.append(cur) or "hub move")
    rig.show()
    menu = QMenu()
    rig.pane._menu(hub_id(p), menu)
    action(menu, "Note…").trigger()
    settle(rig.ws, rig.clock)
    rig.pane.sync()
    assert asked == [""] and "Note: hub move" in rig.spec(hub_id(p)).tip
    undone(rig)
    assert "Note:" not in rig.spec(hub_id(p)).tip


def test_the_menu_names_the_routine_and_where_a_call_leads(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = rig.program
    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(
        brain_names, "ask_name", lambda parent, what, cur: asked.append((what, cur)) or "Roar"
    )
    rig.show()
    menu = QMenu()
    rig.pane._menu(call_id(p), menu)
    action(menu, "Rename call_0…").trigger()
    assert asked == [("routine call_0", "")] and rig.ws.brain_names(ROW) == {"call_0": "Roar"}
    rig.pane.sync()
    assert rig.box(call_id(p)).badge == "\u2192 Roar"
    menu = QMenu()
    rig.pane._menu(ENTRY, menu)
    assert "Rename routine…" in [a.text() for a in menu.actions()]


def test_the_end_of_the_scope_takes_a_new_node(rig: Rig) -> None:
    p = rig.program
    rig.show()
    was = rig.ids()
    rig.pane._create("enter", 0.0, 0.0)  # the empty canvas's menu
    settle(rig.ws, rig.clock)
    rig.pane.sync()
    (new,) = rig.ids() - was
    order = [b.id for b in rig.scene.nodes]
    assert order.index(new) == len(order) - 2  # before the End, which stays last
    assert call_id(p) in order


def test_the_empty_canvas_offers_the_palette(rig: Rig) -> None:
    rig.show()
    menu = rig.view.viewer.context_menus()["graph"]
    titles = [a.text() for a in menu.actions()]
    assert "Add at the end: Moves" in titles and "Add at the end: Conditions · State" in titles


def test_a_gesture_that_changes_nothing_is_refused_and_says_why(rig: Rig) -> None:
    p = rig.program
    rig.show()
    doc = rig.ws.species_doc
    assert doc is not None
    menu = QMenu()
    rig.pane._menu(hub_id(p), menu)
    action(menu, "Move up (Alt+Up)").trigger()  # the hub's list starts with it already
    assert not doc.dirty and not doc.can_undo()
    assert rig.ws.message and rig.pane.status.text() == rig.ws.message
    assert rig.studio.message == rig.ws.message
    menu = QMenu()
    rig.pane._menu(hub_id(p), menu)
    action(menu, "Wrap in", "a pick").trigger()  # fine: one node
    assert doc.dirty
    rig.ws.message = ""
    rig.pane._run(be.earlier("not-a-node"))
    assert rig.pane.status.text() == rig.ws.message != ""


def test_a_menu_without_a_file_that_takes_edits_only_goes_places(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = rig.program
    rig.show()
    monkeypatch.setattr(rig.ws, "brain_editable", lambda row: False)
    menu = QMenu()
    rig.pane._menu(call_id(p), menu)
    assert [a.text() for a in menu.actions()] == ["Follow: call to call_0"]
    action(menu, "Follow: call to call_0").trigger()
    assert rig.followed == ["call_0"]
    rig.pane._create("enter", 0.0, 0.0)
    rig.pane._delete([call_id(p)])
    rig.pane._shift(be.earlier)
    assert rig.ws.species_doc is not None and not rig.ws.species_doc.dirty


def test_keys_delete_and_move(rig: Rig) -> None:
    p = rig.program
    rig.show()
    pick, hub = pick_id(p), hub_id(p)
    rig.click(pick)
    QTest.keyClick(rig.view.viewer, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
    rig.qtbot.wait(30)
    settle(rig.ws, rig.clock)
    rig.pane.sync()
    assert rig.scene.plan.rg.scopes[ROOT].body[:2] == (pick, hub)
    undone(rig)
    rig.click(call_id(p))
    QTest.keyClick(rig.view.viewer, Qt.Key.Key_Delete)
    rig.qtbot.wait(30)
    settle(rig.ws, rig.clock)
    rig.pane.sync()
    assert call_id(p) not in rig.ids()
    undone(rig)


def test_the_delete_key_takes_an_arm_and_refuses_a_crowd(rig: Rig) -> None:
    p = rig.program
    rig.show()
    pick = rig.box(pick_id(p))
    assert pick is not None
    arm = pick.ports[1].arm
    rig.pane.select(arm)
    rig.pane._delete([arm, *[n for f in rig.scene.frames if f.id == arm for n in f.nodes]])
    settle(rig.ws, rig.clock)
    rig.pane.sync()
    assert len(rig.box(pick.id).ports) == 2
    undone(rig)
    rig.pane._delete([call_id(p), hub_id(p)])
    assert rig.pane.status.text() == "Select one node to delete"
    assert rig.ws.species_doc is not None and not rig.ws.species_doc.dirty


# ---- the tree canvas ----------------------------------------------------------------------- #


def test_ports_are_locked_and_a_wire_cannot_be_drawn(rig: Rig) -> None:
    p = rig.program
    rig.show()
    ports = [
        port
        for s in rig.view._shown.values()
        for port in (*s.node.input_ports(), *s.node.output_ports())
    ]
    assert ports and all(port.locked() for port in ports)
    s_hub, s_call = rig.view._shown[hub_id(p)], rig.view._shown[call_id(p)]
    with pytest.raises(PortError):
        s_hub.node.get_output("out").connect_to(s_call.node.get_input("in"), push_undo=False)
    wires = set(rig.view._pipes())
    asked: list[Any] = []
    rig.view.link_requested.connect(lambda *a: asked.append(a))
    drag(rig.view, port_at(rig.view, hub_id(p), "out"), port_at(rig.view, call_id(p), "in"))
    rig.qtbot.wait(30)
    assert set(rig.view._pipes()) == wires and asked == []


def test_a_dragged_node_goes_back(rig: Rig) -> None:
    p = rig.program
    rig.show()
    item = rig.view._shown[call_id(p)].view
    was = item.xy_pos
    drag(rig.view, body_at(rig.view, call_id(p)), body_at(rig.view, call_id(p)) + QPoint(60, 30))
    rig.qtbot.wait(50)
    assert item.xy_pos == pytest.approx(was, abs=0.5)


def screen_width(view: GraphView, node: str) -> float:
    r = view.node_rect(node)
    assert r is not None
    return view.viewer.mapFromScene(r.topRight()).x() - view.viewer.mapFromScene(r.topLeft()).x()


def test_fit_keeps_the_nodes_above_the_collapse_size(rig: Rig) -> None:
    rig.show("long")
    assert len(rig.ids()) == 32
    scale = rig.view.viewer.transform().m11()
    assert scale * NODE_W >= PROXY_PX and screen_width(rig.view, ENTRY) >= PROXY_PX
    seen = rig.view.viewer.mapToScene(rig.view.viewer.viewport().rect()).boundingRect()
    entry = rig.view.node_rect(ENTRY)
    assert entry is not None and seen.contains(entry)  # it starts at the top
    rig.pane.resize(420, 300)
    rig.qtbot.wait(20)
    rig.show("long", "")
    rig.pane.view.fit()
    assert screen_width(rig.view, ENTRY) >= PROXY_PX
    rig.pane.grab()  # painted: a node under the size would have hidden its title
    assert not rig.view._shown[ENTRY].view._proxy_mode
    rig.show("call_0")
    assert rig.view.viewer.transform().m11() > 0.9  # a small scope is not shrunk


def test_a_new_scope_is_framed_and_the_same_one_is_not(rig: Rig) -> None:
    rig.show()
    rig.view.viewer.scale(0.7, 0.7)
    zoomed = rig.view.viewer.transform().m11()
    rig.show()
    rig.pane.sync()
    assert rig.view.viewer.transform().m11() == zoomed  # a sync leaves the user's view alone
    rig.show("call_0")
    assert rig.view.viewer.transform().m11() != zoomed


def test_a_hidden_pane_frames_when_it_is_shown(rig: Rig) -> None:
    rig.pane.hide()
    rig.show("long")
    rig.pane.show()
    rig.qtbot.waitExposed(rig.pane)
    rig.qtbot.wait(30)
    assert screen_width(rig.view, ENTRY) >= PROXY_PX


# ---- what the game and the file add -------------------------------------------------------- #


def inner_id(p: ir.Program) -> str:
    """A move inside the block that draws compact."""
    return node_of(
        p,
        "base_0",
        lambda n: isinstance(n, ir.Insn) and (n.args.get("main"), n.args.get("sub")) == (2, 5),
    )


def counted(rig: Rig, **over: Any) -> bg.Counted:
    p = rig.program
    box = rig.box(pick_id(p))
    assert box is not None
    arms = [q.scope for q in box.ports]
    base: dict[str, Any] = {
        "entity": 1,
        "seconds": 30.0,
        "id": None,
        "steps": 40,
        "outside": 0,
        "dropped": 0,
        "nodes": {hub_id(p): 9, pick_id(p): 9, call_id(p): 3},
        "taken": {arms[0]: 4, arms[1]: 3},
        "routines": {"base_0": bg.Total(9, 40)},
        "never": frozenset({big_id(p)}),
    }
    return bg.Counted(**(base | over))


def test_follow_marks_chip_the_nodes_that_run(rig: Rig) -> None:
    p = rig.program
    rig.show()
    view = rig.ws.brain_view(ROW, rig.ws.brain_header)
    assert view is not None
    f = rig.ws.follow
    assert rig.spec(call_id(p)).marks == ()
    f.on, f.next_node = True, call_id(p)
    f.marks = {
        view.row_of[call_id(p)]: (bg.Mark(bg.NEXT, ""),),
        view.row_of[hub_id(p)]: (bg.Mark(bg.PARKED, ""), bg.Mark(bg.PLAYING, "plays 3 1 instead")),
    }
    rig.pane.sync()
    assert rig.spec(call_id(p)).marks == ("next",)
    assert rig.spec(hub_id(p)).marks == ("playing", "parked")  # the order a line shows them
    assert "plays 3 1 instead" in rig.spec(hub_id(p)).tip
    f.next_node = None
    f.marks = {view.row_of[inner_id(p)]: (bg.Mark(bg.NEXT, ""),)}
    rig.pane.sync()
    assert rig.spec(big_id(p)).marks == ("next",) and rig.spec(call_id(p)).marks == ()
    f.on = False
    rig.pane.sync()
    assert all(n.marks == () for n in rig.view._nodes)


def test_the_marks_are_chips_in_the_themes_colours(rig: Rig) -> None:
    p = rig.program
    rig.show()
    f = rig.ws.follow
    f.on, f.next_node, f.marks = True, hub_id(p), {}
    rig.pane.sync()
    item = rig.view._shown[hub_id(p)].view
    assert [t for t, _ in item.chips] == ["NEXT"]
    assert item.chips[0][1].name() == theme.mark("next").name()
    assert rig.view._shown[call_id(p)].view.chips == ()


def test_with_follow_on_the_scope_goes_to_where_the_monster_is(rig: Rig) -> None:
    p = rig.program
    rig.show()
    f = rig.ws.follow
    f.on, f.next_node = True, inner_id(p)
    rig.pane.sync()
    scope = f"{big_id(p)}#head"
    assert rig.pane._at == (ROW, "base_0", scope) and rig.opened == [("base_0", scope)]
    assert rig.view.selected() == [inner_id(p)] and rig.picked == []
    rig.pane.sync()  # the same NEXT again moves nothing
    assert rig.opened == [("base_0", scope)]
    rig.show("base_0", scope)
    f.next_node = hub_id(p)
    rig.pane.sync()
    assert rig.pane._at == (ROW, "base_0", ROOT) and rig.opened[-1] == ("base_0", ROOT)
    f.next_node = node_of(p, "call_0", lambda n: isinstance(n, ir.Insn))
    rig.pane.sync()  # another routine is the panel's to go to
    assert rig.pane._at == (ROW, "base_0", ROOT) and len(rig.opened) == 2
    f.on = False
    rig.pane.sync()
    f.on, f.next_node = True, inner_id(p)
    rig.pane.sync()
    assert rig.pane._at == (ROW, "base_0", scope)  # off and on again follows once more


def test_counts_badge_the_nodes_and_the_branches_taken(rig: Rig) -> None:
    p = rig.program
    rig.show()
    rig.ws.count.result = counted(rig)
    rig.pane.sync()
    assert rig.spec(hub_id(p)).badge == "×9" and rig.spec(ENTRY).badge == "×9"
    assert rig.spec(pick_id(p)).captions == ("8/32 ×4", "16/32 ×3", "8/32")
    assert rig.spec(big_id(p)).dim and "Did not run" in rig.spec(big_id(p)).tip
    assert "Ran 3 times" in rig.spec(call_id(p)).tip and not rig.spec(call_id(p)).dim
    marks = {f.id: f.mark for f in rig.view._frames}
    arm = rig.box(pick_id(p)).ports[0].scope
    assert marks[arm].endswith("×4") and marks[rig.box(pick_id(p)).ports[2].scope] == ""
    rig.ws.count.result = None
    rig.pane.sync()
    assert rig.spec(hub_id(p)).badge == "" and not rig.spec(big_id(p)).dim


def test_counts_of_another_build_are_not_drawn(rig: Rig) -> None:
    p = rig.program
    rig.show()
    rig.ws.count.result = counted(rig, id=0xABCD)
    rig.ws.count._now = 0x1234  # the build the view shows is another
    assert rig.ws.count.stale
    rig.pane.sync()
    assert rig.spec(hub_id(p)).badge == "" and rig.spec(pick_id(p)).captions[0] == "8/32"


def test_edits_findings_and_new_code_show_on_the_nodes(rig: Rig) -> None:
    p = rig.program
    assert rig.edit(be.set_pair(hub_id(p), 7, 7))
    assert rig.edit(be.insert_entry(be.Place.after(call_id(p)), be.PALETTE["Moves"][0]))
    rig.show()
    hub = rig.spec(hub_id(p))
    assert hub.flag == "edited" and f"{WARN}1" in hub.badge and "PAIR" not in hub.tip
    assert "warning" in hub.tip
    (new,) = [n for n in rig.view._nodes if n.flag == "new"]
    assert new.id.startswith("u")
    assert rig.spec(ENTRY).flag == "edited"  # an edited routine says so at its start
    menu = QMenu()
    rig.pane._menu(ENTRY, menu)
    action(menu, "Back to the game's").trigger()  # the whole routine as the game has it
    settle(rig.ws, rig.clock)
    rig.pane.sync()
    assert rig.spec(ENTRY).flag == "" and not [n for n in rig.view._nodes if n.flag]
    rig.show("call_0")
    assert rig.spec(ENTRY).flag == ""


def test_a_block_with_an_edit_inside_is_marked_inner(rig: Rig) -> None:
    p = rig.program
    assert rig.edit(be.set_pair(inner_id(p), 7, 7))
    rig.show()
    assert rig.spec(big_id(p)).flag == "inner" and f"{WARN}1" in rig.spec(big_id(p)).badge


def test_a_scope_that_goes_falls_back_to_its_routine(rig: Rig) -> None:
    p = rig.program
    scope = f"{big_id(p)}#head"
    rig.show("base_0", scope)
    assert len(rig.ids()) == 14
    assert rig.ws.edit_brain(be.delete(big_id(p)), ROW)
    settle(rig.ws, rig.clock)
    rig.pane.sync()
    assert rig.pane._at == (ROW, "base_0", ROOT) and rig.opened[-1] == ("base_0", ROOT)
    assert big_id(p) not in rig.ids()


def test_the_pane_shows_the_last_build_while_the_next_is_made(rig: Rig) -> None:
    p = rig.program
    rig.show()
    assert rig.ws.edit_brain(be.set_pair(hub_id(p), 7, 7), ROW)
    rig.pane.sync()
    assert rig.ws.brain_pending(ROW) and rig.pane.status.text() == "updating…"
    assert rig.spec(hub_id(p)).flag == ""  # not yet built
    settle(rig.ws, rig.clock)
    rig.pane.sync()
    assert rig.pane.status.text() == "" and rig.spec(hub_id(p)).flag == "edited"


def test_a_new_theme_recolours_the_canvas(rig: Rig) -> None:
    rig.show()
    old = theme.current()
    try:
        theme.apply(theme.theme("Moss", not old.dark))
        rig.pane.sync()
        assert rig.pane._theme is theme.current()
        assert rig.view._tags["edited"] == pane_mod.tags()["edited"] != rgba_of(old, "edited")
    finally:
        theme.apply(old)


def rgba_of(t: theme.Theme, kind: str) -> tuple[float, float, float, float]:
    from PySide6.QtGui import QColor

    c = QColor(t.edits[kind])
    return (c.redF(), c.greenF(), c.blueF(), c.alphaF())


# ---- the tree canvas on its own ------------------------------------------------------------ #


TAGS = {"edited": (0.2, 0.6, 1.0, 1.0), "next": (0.95, 0.7, 0.2, 1.0)}


def tree(qtbot: Any, specs: list[NodeSpec], links: list[LinkSpec] | None = None) -> GraphView:
    view = GraphView(tree=True)
    qtbot.addWidget(view)
    view.resize(900, 600)
    view.set_tags(TAGS)
    view.show()
    qtbot.waitExposed(view)
    view.show(specs, links or [])
    qtbot.wait(20)
    return view


def test_a_node_is_as_wide_as_its_outputs_and_captions_need(qtbot: Any) -> None:
    few = NodeSpec("a", "A", "x", (0, 0), ("in",), ("p0", "p1"), "s", captions=("a", "b"))
    wide = NodeSpec(
        "b", "B", "x", (0, 200), ("in",), tuple(f"p{k}" for k in range(8)),
        captions=tuple(f"case {k} ×12" for k in range(8)),
    )  # fmt: skip
    view = tree(qtbot, [few, wide])
    sizes = view.sizes()
    assert sizes["a"][0] == NODE_W
    assert sizes["b"][0] > 8 * 40
    item = view._shown["b"].view
    xs = [p.x() + p.boundingRect().width() / 2 for p in item.outputs]
    assert xs == pytest.approx([item.width * (k + 1) / 9 for k in range(8)])
    assert view._shown["a"].view.inputs[0].y() + 11 == pytest.approx(
        0
    )  # the input is on the top edge
    bottom = [p.y() + p.boundingRect().height() / 2 for p in item.outputs]
    assert bottom == pytest.approx([item.height] * 8)


def test_a_tree_node_takes_a_double_click_and_return(qtbot: Any) -> None:
    specs = [
        NodeSpec("a", "A", "x", (0, 0), ("in",), ("out",), "one"),
        NodeSpec("b", "B", "x", (0, 150), ("in",), ("out",), "two"),
    ]
    view = tree(qtbot, specs)
    got: list[str] = []
    view.activated.connect(got.append)
    QTest.mouseDClick(view.viewer.viewport(), LEFT, NONE, body_at(view, "a"))
    qtbot.wait(30)
    assert got == ["a"]
    view.select(["a", "b"])
    QTest.keyClick(view.viewer, Qt.Key.Key_Return)
    qtbot.wait(30)
    assert got == ["a"]  # two selected: Return means nothing
    view.select(["b"])
    QTest.keyClick(view.viewer, Qt.Key.Key_Return)
    qtbot.wait(30)
    assert got == ["a", "b"]


def test_a_double_click_on_a_title_does_not_edit_it(qtbot: Any) -> None:
    specs = [NodeSpec("a", "A node", "x", (0, 0), ("in",), ("out",), "one")]
    view = tree(qtbot, specs)
    got: list[str] = []
    view.activated.connect(got.append)
    item = view._shown["a"].view
    title = view.viewer.mapFromScene(item.text_item.sceneBoundingRect().center())
    QTest.mouseDClick(view.viewer.viewport(), LEFT, NONE, title)
    qtbot.wait(30)
    assert got == ["a"]
    assert item.text_item.textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction


def test_a_flag_and_chips_are_drawn_in_their_tags_colours(qtbot: Any) -> None:
    spec = NodeSpec(
        "a",
        "A",
        "x",
        (0, 0),
        ("in",),
        ("out",),
        "s",
        badge="×3",
        flag="edited",
        marks=("next", "none"),
    )
    view = tree(qtbot, [spec])
    item = view._shown["a"].view
    assert item.flag is not None and item.flag.name() == "#3399ff"
    assert [t for t, _ in item.chips] == ["NEXT"]  # a tag with no colour is not drawn
    wider = view.sizes()["a"][0]
    view.show([NodeSpec("a", "A", "x", (0, 0), ("in",), ("out",), "s", badge="×3")])
    assert item.flag is None and item.chips == () and view.sizes()["a"][0] <= wider
    view.set_tags({"edited": (1.0, 0.0, 0.0, 1.0)})
    view.show([spec])
    assert item.flag is not None and item.flag.name() == "#ff0000" and item.chips == ()
    view.grab()  # paints


def test_a_frame_can_be_outlined_in_a_tag(qtbot: Any) -> None:
    specs = [NodeSpec("a", "A", "x", (0, 0), ("in",), ("out",))]
    view = tree(qtbot, specs)
    view.show(specs, [], [FrameSpec("f", "arm", ("a",), "x", flag="edited")])
    assert len(view._glows) == 1 and view._glows[0].pen().color().name() == "#3399ff"
    view.show(specs, [], [FrameSpec("f", "arm", ("a",), "x")])
    assert view._glows == []


def test_the_behaviour_canvas_is_unchanged(qtbot: Any) -> None:
    view = GraphView()
    qtbot.addWidget(view)
    view.show()
    view.show(
        [
            NodeSpec("a", "A", "x", (0, 0), (), ("out",)),
            NodeSpec("b", "B", "x", (300, 0), ("in",), ()),
        ],
        [LinkSpec("a", "out", "b", "in")],
    )
    qtbot.wait(20)
    assert view.viewer.get_layout_direction() == 0
    assert not any(
        p.locked()
        for s in view._shown.values()
        for p in (*s.node.input_ports(), *s.node.output_ports())
    )
    a, b = view._shown["a"].view, view._shown["b"].view
    assert a.outputs[0].x() > 0 and b.inputs[0].x() < 0  # ports at the sides, as ever


# ---- the real em75 ------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def em75(mhfu_data: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Any, Clock]]:
    """Tigrex's script, read and built from the game files; skipped without MHFU_DATA."""
    root = tmp_path_factory.mktemp("em75")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(places.BRAINS.env, str(root / "brains"))
        mp.setenv("XDG_CACHE_HOME", str(root / "cache"))
        mp.setattr(inject, "MEMSTICK_ROOTS", (str(root / "no-stick"),))
        game = Extracted.find(mhfu_data)
        ws = MonsterWorkspace(Data(game, game))
        clock = Clock()
        ws.clock = clock
        pac = game.path(files.monster_pac(75))
        warm = ws.warmup(pac)
        assert warm is not None and warm.done is not None
        warm.done(warm.run())
        ws.open(pac)
        settle(ws, clock)
        yield ws, clock
        ws.close()


@pytest.fixture
def real(qtbot: Any, em75: tuple[Any, Clock]) -> Rig:
    return Rig(qtbot, *em75)


def overlaps(rig: Rig) -> list[tuple[str, str]]:
    """The pairs of nodes drawn over each other."""
    rects = {i: rig.view.node_rect(i) for i in rig.view._shown}
    ids = list(rects)
    return [
        (a, b) for k, a in enumerate(ids) for b in ids[k + 1 :] if rects[a].intersects(rects[b])
    ]


def test_em75_draws_every_routine_without_overlap(real: Rig) -> None:
    ws = real.ws
    proj = ws.brain_projection(ROW)
    assert proj is not None and len(proj.routines) > 90
    worst = 0.0
    for name in proj.routines:
        t = time.perf_counter()
        real.show(name)
        worst = max(worst, time.perf_counter() - t)
        scene = real.pane._scene
        assert scene is not None and set(real.view._shown) == real.ids()
        assert overlaps(real) == [], name
        drawn = real.view.sizes()
        assert all(drawn[b.id] == pytest.approx(b.size, abs=0.5) for b in scene.nodes), name
    assert worst < 2.0  # the biggest scope is ~60 ms here; a slow machine has room


def test_em75_biggest_scopes_and_their_arms(real: Rig) -> None:
    rg = real.ws.brain_routine_graph(ROW, "int_0x20_0")
    assert rg is not None
    real.show("int_0x20_0")
    assert len(real.ids()) == 40 and len(real.scene.frames) == 11
    assert screen_width(real.view, ENTRY) >= PROXY_PX
    arms = [k for k in rg.scopes if k != ROOT]
    for scope in arms[:12]:
        real.show("int_0x20_0", scope)
        assert overlaps(real) == [] and len(real.ids()) >= 1


def test_em75_a_compact_block_opens_and_an_edit_comes_back(real: Rig) -> None:
    real.show("call_3")
    (compact,) = [b for b in real.scene.nodes if b.compact]
    assert [o.name for o in compact.opens] == ["then", "else"]
    real.double_click(compact.id)  # two branches: the user picks
    popup = real.pane.findChild(QMenu)
    assert popup is not None and real.opened == []
    popup.actions()[0].trigger()
    popup.close()
    scope = compact.opens[0].scope
    assert real.opened == [("call_3", scope)] and real.pane._at == (ROW, "call_3", scope)
    assert screen_width(real.view, real.scene.nodes[0].id) >= PROXY_PX
    hub = next(b.id for b in real.scene.nodes if b.kind == "move")
    assert real.ws.edit_brain(be.set_pair(hub, 7, 7), ROW)
    settle(real.ws, real.clock)
    real.pane.sync()
    assert real.spec(hub).flag == "edited"
    real.studio.undo()
    settle(real.ws, real.clock)
    real.pane.sync()
    assert real.spec(hub).flag == ""
