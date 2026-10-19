# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Brain tab over a faked decode: the tree is the script, and each control does what it says;
and over the real em75 and em01, how fast the model stays."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from mhfu.em.script import ir, source
from mhfu.files import Extracted
from mhfu_port.data import Data
from mhfu_studio.monster import brain, species
from mhfu_studio.monster.brain import BrainView
from mhfu_studio.monster.panels.brain import (
    KIND,
    LABEL,
    LINK,
    PC,
    TEXT,
    BrainPanel,
    address,
)
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell import places
from mhfu_studio.shell.studio import Studio
from mhfu_studio.shell.workspace import Dock
from mhfu_studio.ui import kit, theme
from mhfu_studio.ui.testing import FakeWorkspace
from PySide6.QtCore import QItemSelectionModel, QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from ..conftest import program

LEFT, NONE = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
READING = "Reading Tigrex (em75)'s AI script…"


def one_header() -> ir.Program:
    """`program()` without its second header."""
    p = program()
    p.items = [i for i in p.items if not (isinstance(i, ir.Header) and i.name == "h1")]
    return p


def decoding(ws: MonsterWorkspace, build: Callable[[], ir.Program] = program) -> None:
    """Makes `ws` decode every row as `build()`."""

    def decode(game: Extracted, row: int) -> ir.Program:
        p = build()
        p.row = row
        return p

    ws.decode_brain = decode


@pytest.fixture
def ws(brain_ws: MonsterWorkspace) -> MonsterWorkspace:
    brain_ws.browse_brain(75)
    return brain_ws


def make(qtbot: Any, ws: MonsterWorkspace, *, ready: bool = True) -> BrainPanel:
    """Shown, synced as the window would, and waited for until it shows a script."""
    studio = Studio([ws])
    p = BrainPanel(ws, studio)
    studio.listen(p.sync)
    qtbot.addWidget(p)
    p.resize(1000, 600)
    p.show()
    qtbot.waitExposed(p)
    p.sync()
    if ready:
        qtbot.waitUntil(lambda: p.model.view is not None, timeout=20000)
    return p


@pytest.fixture
def panel(qtbot: Any, ws: MonsterWorkspace) -> BrainPanel:
    return make(qtbot, ws)


def view(p: BrainPanel) -> BrainView:
    assert p.model.view is not None
    return p.model.view


def shown(p: BrainPanel) -> list[str]:
    """The lines the tree shows now, top to bottom."""
    out, ix = [], p.model.index(0, PC)
    while ix.isValid():
        out.append(p.model.data(ix.siblingAtColumn(TEXT)))
        ix = p.tree.indexBelow(ix)
    return out


def row_of(p: BrainPanel, text: str) -> int:
    (row,) = [i for i, ln in enumerate(view(p).lines) if ln.text.strip() == text]
    return row


def opened(p: BrainPanel, row: int) -> bool:
    return p.tree.isExpanded(p.model.index_of(row))


def cursor(p: BrainPanel) -> int:
    ix = p.tree.currentIndex()
    assert ix.isValid()
    return int(ix.internalId())


def click(w: Any, pos: QPoint, count: int = 1) -> None:
    QTest.mouseClick(w.viewport(), LEFT, NONE, pos)
    if count == 2:  # the double click Qt sees follows a press
        QTest.mouseDClick(w.viewport(), LEFT, NONE, pos)


def at(p: BrainPanel, row: int) -> QPoint:
    return p.tree.visualRect(p.model.index_of(row, TEXT)).center()


# ---- the tree is the script -------------------------------------------------------------------- #


def test_expanded_is_the_source(panel: BrainPanel) -> None:
    v = view(panel)
    assert shown(panel) == [v.lines[r].text for r in v.top]  # routines start folded
    assert panel.tree.model() is panel.model and panel.tree.uniformRowHeights()
    panel.tree.expandAll()
    assert shown(panel) == [ln.text for ln in v.lines]


def test_model_tree(panel: BrainPanel) -> None:
    m, v = panel.model, view(panel)
    for row in range(len(v.lines)):
        ix = m.index_of(row, TEXT)
        assert ix.internalId() == row and m.index(ix.row(), TEXT, ix.parent()) == ix
        assert (ix.parent().internalId() if ix.parent().isValid() else -1) == v.parent[row]
        assert m.rowCount(ix.siblingAtColumn(PC)) == len(v.children[row])
        assert m.rowCount(ix) == 0  # only the first column has children
    assert m.rowCount() == len(v.top) and m.columnCount() == 2
    m.set_view(None)
    assert m.rowCount() == 0 and not m.index(0, 0).isValid()


def test_roles(panel: BrainPanel) -> None:
    m, v = panel.model, view(panel)
    kinds = {
        v.lines[r].text.strip(): m.data(m.index_of(r, TEXT), KIND) for r in range(len(v.lines))
    }
    assert kinds["if_latch"] == "condition" and kinds["enter 3 0 0"] == "action"
    assert kinds["goto 1"] == "flow" and kinds["routine r_a"] == "structure"
    assert (
        kinds["wait 5"] == "action"
        and kinds["d0: 00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f"] == "data"
    )
    links = {r for r in range(len(v.lines)) if m.data(m.index_of(r, TEXT), LINK)}
    assert links == {row_of(panel, "goto 1"), row_of(panel, "call 0")}
    labels = {v.lines[r].text.strip(): m.data(m.index_of(r, TEXT), LABEL) for r in v.enters}
    assert labels == {
        "enter 3 0 0": "keeps the last clip",
        "enter 1 4 0": "anim 1",
        "enter 0 3 0": "anim 9",
    }
    assert not m.data(m.index_of(row_of(panel, "goto 1"), TEXT), LABEL)


# ---- the species and the variant --------------------------------------------------------------- #


def test_species_rows(qtbot: Any, ws: MonsterWorkspace) -> None:
    release = threading.Event()

    def scan(game: Extracted) -> dict[int, int]:
        release.wait(10)
        return {75: 75, 76: 75, 88: 75}

    ws.scan_brain = scan
    p = make(qtbot, ws)
    assert [p.species.itemData(i) for i in range(p.species.count())] == ["75"]  # until the scan
    assert p.species.currentText() == "Tigrex (em75)"
    release.set()
    qtbot.waitUntil(lambda: p.species.count() == 3)
    assert [p.species.itemText(i) for i in range(3)] == [species.label(r) for r in (75, 76, 88)]
    assert p.species.currentData() == "75"
    assert not p.poll.isActive()  # nothing is left to wait for


def test_browse(qtbot: Any, panel: BrainPanel) -> None:
    ws, first = panel.ws, view(panel)
    qtbot.waitUntil(lambda: panel.species.count() == 3)
    panel.species.setCurrentIndex(1)
    panel.species.activated.emit(1)
    assert ws.brain_species == 76
    qtbot.waitUntil(lambda: panel.model.view is not None and panel.model.view.row == 76)
    assert view(panel) is not first and panel.species.currentData() == "76"
    assert panel.history.rows == [] and shown(panel)  # a view of its own, with no way back
    panel.species.setCurrentIndex(0)
    panel.species.activated.emit(0)
    qtbot.waitUntil(lambda: panel.model.view is first)  # the first is kept, not read again


def test_one_header(qtbot: Any, ws: MonsterWorkspace) -> None:
    decoding(ws, one_header)
    p = make(qtbot, ws)
    assert [h.header for h in ws.brain_headers(75)] == ["h0"] and not p.variant.isVisible()


def test_variant(qtbot: Any, panel: BrainPanel) -> None:
    v0 = view(panel)
    assert panel.variant.isVisible() and panel.variant.count() == 2
    assert [panel.variant.itemText(i) for i in range(2)] == ["Map types 0-3", "Map types 4-6, 8"]
    assert panel.variant.currentData() == "h0"
    panel.goto(row_of(panel, "routine r_b"))
    panel.variant.setCurrentIndex(1)
    panel.variant.activated.emit(1)
    qtbot.waitUntil(lambda: panel.model.view is not None and panel.model.view.header == "h1")
    assert shown(panel) != [v0.lines[r].text for r in v0.top]
    assert panel.variant.currentData() == "h1" and panel.history.rows == []
    panel.tree.expandAll()
    assert shown(panel) == [ln.text for ln in view(panel).lines]


# ---- find -------------------------------------------------------------------------------------- #


def test_find(qtbot: Any, panel: BrainPanel) -> None:
    v = view(panel)
    a = row_of(panel, "routine r_a")
    hits = v.find("enter")
    assert len(hits) == 3 and panel.count.text() == ""
    qtbot.keyClicks(panel.query, "enter")
    assert panel.count.text() == "3 matches" and not panel.tree.currentIndex().isValid()
    for k in (0, 1, 2, 0):  # Return goes on, and round
        qtbot.keyClick(panel.query, Qt.Key.Key_Return)
        assert cursor(panel) == hits[k] and panel.count.text() == f"{k + 1} of 3"
    assert panel.tree.selectionModel().selectedRows(TEXT)[0].internalId() == hits[0]
    assert opened(panel, a) and not opened(panel, row_of(panel, "routine r_b"))
    qtbot.keyClick(panel.query, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert cursor(panel) == hits[2] and panel.count.text() == "3 of 3"  # back, and round
    assert opened(panel, row_of(panel, "routine r_b")) and not opened(panel, a)  # others fold
    qtbot.keyClick(panel.query, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert cursor(panel) == hits[1] and opened(panel, a) and opened(panel, v.parent[hits[1]])
    assert panel.query.text() == "enter"


def test_find_none(qtbot: Any, panel: BrainPanel) -> None:
    qtbot.keyClicks(panel.query, "no such text")
    assert panel.count.text() == "No match"
    qtbot.keyClick(panel.query, Qt.Key.Key_Return)
    assert not panel.tree.currentIndex().isValid() and panel.history.rows == []
    panel.query.setText("")
    assert panel.count.text() == ""


def test_find_address(qtbot: Any, panel: BrainPanel) -> None:
    panel.query.setText("0x1004")
    assert panel.count.text() == "1 match"
    qtbot.keyClick(panel.query, Qt.Key.Key_Return)
    assert cursor(panel) == view(panel).row_of["n1"] and panel.count.text() == "1 of 1"


def test_find_from_a_click(qtbot: Any, panel: BrainPanel) -> None:
    hits = view(panel).find("enter")
    panel.query.setText("enter")
    panel.goto(hits[1])
    assert panel.count.text() == "2 of 3"
    qtbot.keyClick(panel.query, Qt.Key.Key_Return)
    assert cursor(panel) == hits[2]
    panel.goto(row_of(panel, "routine r_a"))  # not a match
    assert panel.count.text() == "3 matches"
    qtbot.keyClick(panel.query, Qt.Key.Key_Return)
    assert cursor(panel) == hits[0]


# ---- links, history and the outline ------------------------------------------------------------ #


def test_link_and_back(qtbot: Any, panel: BrainPanel) -> None:
    v = view(panel)
    goto, a, b = row_of(panel, "goto 1"), row_of(panel, "routine r_a"), row_of(panel, "routine r_b")
    target = v.target_row(goto)
    assert target == b + 1
    panel.goto(goto)
    assert not panel.back.isEnabled() and not panel.forward.isEnabled()
    qtbot.keyClick(panel.tree, Qt.Key.Key_Return)
    assert cursor(panel) == target and panel.history.rows == [goto, target]
    assert opened(panel, b) and not opened(panel, a)
    assert panel.back.isEnabled() and not panel.forward.isEnabled()
    panel.back.click()
    assert cursor(panel) == goto and opened(panel, a) and not opened(panel, b)
    assert panel.forward.isEnabled() and not panel.back.isEnabled()
    panel.forward.click()
    assert cursor(panel) == target and not opened(panel, a)


def test_double_click(qtbot: Any, panel: BrainPanel) -> None:
    v = view(panel)
    call = row_of(panel, "call 0")
    panel.goto(call)
    click(panel.tree, at(panel, call), 2)
    assert cursor(panel) == v.target_row(call) == row_of(panel, "wait 5")
    assert panel.history.rows == [call, cursor(panel)]


def test_no_link(qtbot: Any, panel: BrainPanel) -> None:
    row = row_of(panel, "enter 3 0 0")
    panel.goto(row)
    qtbot.keyClick(panel.tree, Qt.Key.Key_Return)
    assert cursor(panel) == row and panel.history.rows == [row]


def test_alt_arrows(qtbot: Any, panel: BrainPanel) -> None:
    rows = [row_of(panel, t) for t in ("routine r_b", "routine r_c", "routine r_a")]
    for r in rows:
        panel.goto(r)
    panel.tree.setFocus()
    qtbot.keyClick(panel.tree, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
    assert cursor(panel) == rows[1]
    qtbot.keyClick(panel.tree, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
    assert cursor(panel) == rows[0] and not panel.back.isEnabled()
    qtbot.keyClick(panel.tree, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    assert cursor(panel) == rows[1]


def test_back_after_a_click(qtbot: Any, panel: BrainPanel) -> None:
    far, here = row_of(panel, "routine r_c"), row_of(panel, "routine r_a")
    panel.goto(far)
    click(panel.tree, at(panel, here))  # a click is no step of the history
    assert cursor(panel) == here
    panel.goto(row_of(panel, "routine r_b"))
    panel.back.click()
    assert cursor(panel) == here
    panel.back.click()
    assert cursor(panel) == far


def test_outline(qtbot: Any, panel: BrainPanel) -> None:
    v, lists = view(panel), panel.lists
    names = [lists.topLevelItem(i).text(0) for i in range(lists.topLevelItemCount())]
    assert names == ["base (2)", "call (2)", "interrupt 0x10 (1)", "stages (1)"]
    slots = [
        [top.child(k).text(0) for k in range(top.childCount())]
        for top in map(lists.topLevelItem, range(4))
    ]
    assert slots == [["0 r_a", "1 r_b"], ["0 r_c", "1 -"], ["0 r_i"], ["0 d1"]]
    base, call = lists.topLevelItem(0), lists.topLevelItem(1)
    click(lists, lists.visualItemRect(base.child(1)).center())
    assert cursor(panel) == v.outline[0].items[1].row == row_of(panel, "routine r_b")
    assert opened(panel, cursor(panel))
    for item in (call.child(1), lists.topLevelItem(3).child(0)):  # an empty slot, and data
        click(lists, lists.visualItemRect(item).center())
        assert cursor(panel) == row_of(panel, "routine r_b")
    click(lists, lists.visualItemRect(call.child(0)).center())
    assert cursor(panel) == row_of(panel, "routine r_c") and panel.history.rows[-2:] == [
        row_of(panel, "routine r_b"),
        row_of(panel, "routine r_c"),
    ]


def test_open_whole(panel: BrainPanel) -> None:
    v = view(panel)
    a, inner = row_of(panel, "routine r_a"), row_of(panel, "if_latch")
    panel.tree.expand(panel.model.index_of(a))
    assert opened(panel, inner)
    now, whole = shown(panel), [v.lines[r].text for r in v.span(a)]
    assert now[a : a + len(whole)] == whole  # the routine's lines, and the next top row after them
    assert now[a + len(whole)] == v.lines[row_of(panel, "routine r_b")].text
    panel.tree.collapse(panel.model.index_of(inner))  # a nested block can still be folded
    assert not opened(panel, inner) and opened(panel, a)


# ---- copy and addresses ------------------------------------------------------------------------ #


def text_of(p: BrainPanel, rows: Any) -> str:
    return "\n".join(view(p).lines[r].text for r in rows)


def select(p: BrainPanel, *rows: int) -> None:
    """Selects exactly `rows`, as a shift-click does."""
    sel = p.tree.selectionModel()
    sel.clearSelection()
    for r in rows:
        sel.select(
            p.model.index_of(r, TEXT),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )


def test_copy_routine(panel: BrainPanel) -> None:
    v = view(panel)
    a = row_of(panel, "routine r_a")
    panel.goto(row_of(panel, "enter 1 4 0"))
    panel.copy.click()
    assert QGuiApplication.clipboard().text() == text_of(panel, v.span(a))
    assert (
        "  if_latch" in QGuiApplication.clipboard().text()
        and "routine r_b" not in QGuiApplication.clipboard().text()
    )
    assert panel.studio.message == f"copied {len(v.span(a))} lines"


def test_copy_lines(panel: BrainPanel) -> None:
    panel.tree.expandAll()
    first, last = row_of(panel, "enter 3 0 0"), row_of(panel, "enter 1 4 0")
    select(panel, first, last)
    panel.copy.click()
    assert QGuiApplication.clipboard().text() == text_of(panel, (first, last))
    panel.tree.clearSelection()
    panel.goto(first)
    QGuiApplication.clipboard().clear()
    panel.copy.click()  # one line selected: its routine
    assert QGuiApplication.clipboard().text().startswith("routine r_a")


def test_copy_key(qtbot: Any, panel: BrainPanel) -> None:
    row = row_of(panel, "enter 3 0 0")
    panel.goto(row)
    panel.tree.setFocus()
    qtbot.keyClick(panel.tree, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QGuiApplication.clipboard().text() == view(panel).lines[row].text


def test_copy_folded(panel: BrainPanel) -> None:
    v = view(panel)
    a = row_of(panel, "routine r_a")
    select(panel, a)
    assert not opened(panel, a) and panel._picked() == list(v.span(a))
    panel.tree.expand(panel.model.index_of(a))
    assert panel._picked() == [a]


def test_addresses(qtbot: Any, panel: BrainPanel) -> None:
    m, row = panel.model, view(panel).row_of["n1"]
    assert panel.tree.isColumnHidden(PC) and panel.more.toggle.isVisible()
    assert not panel.addresses.isChecked()
    panel.more.set_open(True)
    panel.addresses.setChecked(True)
    assert not panel.tree.isColumnHidden(PC)
    assert m.data(m.index_of(row, PC)) == "0x00001004" == address(0x1004)
    assert m.data(m.index_of(row, TEXT)) == view(panel).lines[row].text
    no_pc = next(r for r, ln in enumerate(view(panel).lines) if ln.pc is None)
    assert m.data(m.index_of(no_pc, PC)) == "" and address(None) == ""
    panel.addresses.setChecked(False)
    assert panel.tree.isColumnHidden(PC)
    panel.addresses.setChecked(True)
    panel.variant.setCurrentIndex(1)  # another script keeps the choice
    panel.variant.activated.emit(1)
    qtbot.waitUntil(lambda: panel.model.view is not None and panel.model.view.header == "h1")
    assert not panel.tree.isColumnHidden(PC)


# ---- the states -------------------------------------------------------------------------------- #


def test_decoding(qtbot: Any, ws: MonsterWorkspace) -> None:
    release = threading.Event()

    def slow(game: Extracted, row: int) -> ir.Program:
        release.wait(10)
        p = program()
        p.row = row
        return p

    ws.decode_brain = slow
    p = make(qtbot, ws, ready=False)
    assert p.note.title.text() == READING and p.main.currentWidget() is p.note
    assert p.poll.isActive() and not p.query.isEnabled() and not p.copy.isEnabled()
    release.set()
    qtbot.waitUntil(lambda: p.model.view is not None)
    assert p.main.currentWidget() is p.split and p.query.isEnabled() and p.copy.isEnabled()
    qtbot.waitUntil(lambda: not p.poll.isActive())


def test_error(qtbot: Any, ws: MonsterWorkspace) -> None:
    def bad(game: Extracted, row: int) -> ir.Program:
        raise ValueError("a routine does not end")

    ws.decode_brain = bad
    p = make(qtbot, ws, ready=False)
    qtbot.waitUntil(lambda: p.note.title.text() == "No AI script to show")
    assert (
        p.note.hint.text() == ws.brain_error(75) and "a routine does not end" in p.note.hint.text()
    )
    assert p.model.view is None and p.main.currentWidget() is p.note and not p.query.isEnabled()
    qtbot.waitUntil(lambda: not p.poll.isActive())
    assert p.species.isEnabled()  # another species is a click away


def test_no_script(qtbot: Any, ws: MonsterWorkspace) -> None:
    def none(game: Extracted, row: int) -> ir.Program:
        raise brain.NoScript(row)

    ws.decode_brain = none
    p = make(qtbot, ws, ready=False)
    qtbot.waitUntil(lambda: p.note.title.text() == "No AI script to show")
    assert p.note.hint.text() == "Tigrex (em75) has no AI script the studio reads"


def test_no_species(qtbot: Any, brain_ws: MonsterWorkspace) -> None:
    assert brain_ws.brain_species is None
    p = make(qtbot, brain_ws, ready=False)
    assert p.note.title.text() == "No monster to read" and p.main.currentWidget() is p.note
    assert p.species.currentData() == "" and not p.query.isEnabled()
    qtbot.waitUntil(lambda: p.species.count() == 4)  # the choice's words, then the scanned rows
    assert [p.species.itemData(i) for i in range(4)] == ["", "75", "76", "88"]
    p.species.setCurrentIndex(2)
    p.species.activated.emit(2)
    qtbot.waitUntil(lambda: p.model.view is not None)
    assert view(p).row == 76 and p.species.currentData() == "76" and p.species.count() == 3
    qtbot.waitUntil(lambda: not p.poll.isActive())


def test_no_game(qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    for place in places.PLACES:
        monkeypatch.delenv(place.env, raising=False)
    ws = MonsterWorkspace()
    ws.browse_brain(75)
    p = make(qtbot, ws, ready=False)
    assert p.pages.currentWidget() is p.gate and p.gate.title.text() == "No game files"
    assert ws.brain_note in p.gate.hint.text() and "Setup" in p.gate.hint.text()
    assert not p.poll.isActive() and ws._brain_pool is None  # nothing was started
    ws.close()


def test_game_set_up_later(
    qtbot: Any, ws: MonsterWorkspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws.brain_note = "no MHFU extraction found (choose it under Setup on the start page)"
    gone = [True]
    real = ws._brain_game
    monkeypatch.setattr(ws, "_brain_game", lambda: None if gone[0] else real())
    p = make(qtbot, ws, ready=False)
    assert p.pages.currentWidget() is p.gate
    gone[0] = False
    p.hide()
    p.show()
    qtbot.waitUntil(lambda: p.model.view is not None)
    assert p.pages.currentWidget() is not p.gate


def test_hidden_does_not_poll(qtbot: Any, ws: MonsterWorkspace) -> None:
    release = threading.Event()
    ws.decode_brain = lambda g, row: (release.wait(10), program())[1]
    p = make(qtbot, ws, ready=False)
    assert p.poll.isActive()
    p.hide()
    assert not p.poll.isActive()
    release.set()
    p.show()
    qtbot.waitUntil(lambda: p.model.view is not None)


def test_sync_is_cheap(panel: BrainPanel, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    for name in ("brain", "brain_rows", "brain_headers", "brain_error"):
        monkeypatch.setattr(panel.ws, name, lambda *a, n=name, **k: calls.append(n))
    for _ in range(50):
        panel.sync()
    assert calls == []
    panel.ws.browse_brain(76)
    panel.sync()
    assert "brain" in calls  # another species is noticed


def test_tips(panel: BrainPanel) -> None:
    assert kit.missing_tips(panel) == []
    panel.more.set_open(True)
    assert kit.missing_tips(panel) == []
    panel.pages.show_page(False)
    assert kit.missing_tips(panel) == []
    assert panel.studio.errors == []


# ---- the window ---------------------------------------------------------------------------- #


class Hosted(FakeWorkspace):
    """A fake workspace carrying the monster workspace's own Brain tab."""

    def __init__(self, own: MonsterWorkspace) -> None:
        super().__init__("monster")
        self.own = own

    def docks(self) -> tuple[Dock, ...]:
        [spec] = [d for d in self.own.docks() if d.label == "Brain"]
        return (*super().docks(), spec)


def test_dock() -> None:
    [dock] = [d for d in MonsterWorkspace().docks() if d.label == "Brain"]
    assert dock.area == "centre" and not dock.shown and dock.tip


def test_window(make_window: Callable[..., Any], qtbot: Any, ws: MonsterWorkspace) -> None:
    host = Hosted(ws)
    w = make_window(host)
    panel = w.findChild(BrainPanel)
    assert panel is not None and not panel.isVisible() and panel.model.view is None
    assert [w.tabs.tabText(i) for i in range(w.tabs.count())] == ["3D view", "Brain"]
    assert kit.missing_tips(panel) == []
    [opener] = [a for a in w.panels_menu.actions() if a.text() == "Brain"]
    opener.trigger()
    w.sync()
    qtbot.waitUntil(lambda: panel.model.view is not None)
    assert panel.isVisible() and w.centre.currentWidget() is panel and shown(panel)
    qtbot.waitUntil(lambda: not panel.poll.isActive())
    w.show_tab("3D view")
    assert not panel.isVisible() and not panel.poll.isActive()
    assert w.studio.errors == []


# ---- colours ----------------------------------------------------------------------------------- #


@pytest.mark.parametrize("family", theme.FAMILIES)
@pytest.mark.parametrize("dark", [True, False])
def test_theme_colours(family: str, dark: bool) -> None:
    t = theme.theme(family, dark)
    assert set(t.code) == {*theme.CODE_KINDS, "label"} and len(set(t.code.values())) == 8


def test_every_kind_has_a_colour() -> None:
    kinds = {ln.kind for ln in source.render(program(), "h0")}
    assert kinds <= set(theme.CODE_KINDS) and len(theme.CODE_KINDS) == 7


def test_current_theme(qapp: Any) -> None:
    theme.apply(theme.theme("Moss", True))
    dark = theme.code("flow")
    theme.apply(theme.theme("Moss", False))
    assert theme.code("flow") != dark and theme.code("no such kind") == theme.text()


# ---- the real scripts -------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def game(mhfu_data: Path) -> Extracted:
    return Extracted.find(mhfu_data)


@pytest.fixture
def real(game: Extracted) -> Iterator[MonsterWorkspace]:
    """The game's own scripts; no intel (labelling takes a minute) and no scan of every overlay."""
    w = MonsterWorkspace(Data(game, game))
    w.intel_cache.update({75: None, 1: None})
    w.scan_brain = lambda g: {75: 75, 1: 1}
    yield w
    w.close()


@pytest.mark.parametrize(("row", "lines"), [(75, 1700), (1, 10000)])
def test_real_speed(
    qtbot: Any, real: MonsterWorkspace, row: int, lines: int, capsys: pytest.CaptureFixture[str]
) -> None:
    real.browse_brain(row)
    t0 = time.perf_counter()
    p = make(qtbot, real)
    load = time.perf_counter() - t0
    v = view(p)
    assert len(v.lines) >= lines and v.header == brain.default_header(
        real._programs[real.fu().root, row]
    )
    assert shown(p) == [v.lines[r].text for r in v.top]
    t0 = time.perf_counter()
    p.tree.expandAll()
    expand = time.perf_counter() - t0
    t0 = time.perf_counter()
    every = shown(p)
    walk = time.perf_counter() - t0
    assert every == [ln.text for ln in v.lines]
    p.query.setText("enter")
    t0 = time.perf_counter()
    hits = len(p._matches)
    qtbot.keyClick(p.query, Qt.Key.Key_Return)
    first = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(20):
        qtbot.keyClick(p.query, Qt.Key.Key_Return)
    step = (time.perf_counter() - t0) / 20
    t0 = time.perf_counter()
    p.query.setText("0x")
    typing = time.perf_counter() - t0
    link = next(r for r in range(len(v.lines)) if v.target_row(r) is not None)
    t0 = time.perf_counter()
    p._follow(link)
    follow = time.perf_counter() - t0
    with capsys.disabled():
        print(
            f"\nem{row:02d} {len(v.lines)} lines: load {load:.2f}s (decode and view, the worker's),"
            f" expand all {expand:.3f}s, walk {walk:.3f}s, find 'enter' ({hits} hits) {first:.3f}s,"
            f" next match {step * 1000:.0f} ms, retype {typing * 1000:.0f} ms,"
            f" follow {follow * 1000:.0f} ms"
        )
    assert expand < 2 and walk < 2 and first < 0.5 and step < 0.2 and typing < 0.5 and follow < 0.5
    assert opened(p, v.top_of(cursor(p)))
