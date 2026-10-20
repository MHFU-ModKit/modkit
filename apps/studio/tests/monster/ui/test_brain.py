# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Brain tab over a faked decode: the tree is the script, and each control does what it says;
and over the real em75 and em01, how fast the model stays."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
from mhfu import files, inject
from mhfu.em.intel import SpeciesIntel
from mhfu.em.script import ir, source
from mhfu.files import Extracted
from mhfu.live.script import Step, Trace
from mhfu.writes import Plan, Pushed
from mhfu_port.data import Data
from mhfu_studio.monster import brain, species
from mhfu_studio.monster import brain_edit as be
from mhfu_studio.monster import brain_game as bg
from mhfu_studio.monster import workspace as mw
from mhfu_studio.monster.brain import BrainView
from mhfu_studio.monster.panels.brain import PUSH_TIP, ROW, SEND_TIP, BrainPanel, details
from mhfu_studio.monster.panels.brain_code import (
    COUNT,
    KIND,
    LABEL,
    LINK,
    MARKS,
    PAD,
    PC,
    TEXT,
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
from PySide6.QtWidgets import QWidget

from ..conftest import program
from ..test_brain_edit import world
from ..test_brain_game import H0, H1, state

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


def node_of(p: BrainPanel, text: str) -> str:
    """The id of the node whose line says `text`."""
    node = view(p).lines[row_of(p, text)].node
    assert node is not None
    return node


def shown_all(p: BrainPanel) -> list[str]:
    return [ln.text.strip() for ln in view(p).lines]


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
    assert not opened(panel, a) and panel.tree.picked() == list(v.span(a))
    panel.tree.expand(panel.model.index_of(a))
    assert panel.tree.picked() == [a]


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


# ---- follow the game --------------------------------------------------------------------------- #


class Inline:
    """An executor that runs a call as it is asked, so a poll's read is done at once."""

    def submit(self, fn: Callable[..., Any], *args: Any) -> Future[Any]:
        done: Future[Any] = Future()
        try:
            done.set_result(fn(*args))
        except Exception as e:
            done.set_exception(e)
        return done

    def shutdown(self, *_: Any, **__: Any) -> None:
        pass


class Running:
    """A game for Follow to read: `monsters` is what each read finds, `t` the clock in seconds."""

    def __init__(self, ws: MonsterWorkspace) -> None:
        self.ws = ws
        self.t = [0.0]
        self.monsters: dict[int, Any] = {2: state(0x1008)}
        self.running = True
        self.asked: list[int] = []
        ws._pool = Inline()  # type: ignore[assignment]
        ws.read_follow = self.read
        ws.game_running = lambda: self.running
        ws.follow.clock = lambda: self.t[0]

    def read(self, attach: Any, row: int, known: Any = None, seen: Any = None) -> bg.Read:
        self.asked.append(row)
        return bg.Read(row, dict(self.monsters), None)

    def tick(self, p: BrainPanel, reads: int = 1) -> None:
        """A period on, each: a tick asks and the next takes the answer."""
        for _ in range(reads + 1):
            self.t[0] += bg.PERIOD
            p._tick()


@pytest.fixture
def run(ws: MonsterWorkspace) -> Running:
    return Running(ws)


def follow(p: BrainPanel, g: Running) -> None:
    """Follow on, and one read taken."""
    p.following.setChecked(True)
    g.tick(p)


def marks_at(p: BrainPanel, row: int) -> list[str]:
    return [m.kind for m in p.model.data(p.model.index_of(row, TEXT), MARKS)]


def test_follow_marks_the_lines(panel: BrainPanel, run: Running) -> None:
    call, enter = row_of(panel, "call 0"), row_of(panel, "enter 3 0 0")
    assert not panel.following.isChecked() and not panel.live.isVisible()
    assert panel.tree.isColumnHidden(PC) and not panel.ticker.isActive()
    panel.following.setChecked(True)
    assert panel.ws.follow.on and panel.live.isVisible() and panel.ticker.isActive()
    assert panel.ticker.interval() == 1000 and not panel.tree.isColumnHidden(PC)
    run.tick(panel)
    assert run.asked[0] == 75
    assert marks_at(panel, call) == ["next"] and marks_at(panel, enter) == ["playing"]
    assert panel.model.data(panel.model.index_of(call, PC), MARKS)[0].kind == "next"
    assert panel.live.text() == "following · slot 2" and panel.following.isChecked()
    assert marks_at(panel, row_of(panel, "routine r_b")) == []


def test_follow_goes_to_next_as_it_moves(panel: BrainPanel, run: Running) -> None:
    call, goto, b = (row_of(panel, t) for t in ("call 0", "goto 1", "routine r_b"))
    follow(panel, run)
    assert cursor(panel) == call and opened(panel, row_of(panel, "routine r_a"))
    run.monsters = {2: state(0x1014)}
    run.tick(panel)
    assert cursor(panel) == goto and marks_at(panel, goto) == ["next"]
    assert marks_at(panel, call) == [] and panel.following.isChecked()
    assert panel.history.rows == []  # the history is the user's
    run.monsters = {2: state(0x1018)}  # r_b: a routine folded until now
    run.tick(panel)
    assert opened(panel, b) and not opened(panel, row_of(panel, "routine r_a"))


def test_a_parked_line_is_kept_in_sight(panel: BrainPanel, run: Running) -> None:
    a, b, c = (row_of(panel, f"routine r_{x}") for x in "abc")
    run.monsters = {2: state(0x1008, parked={0x20: 0x1018})}  # r_b's enter
    follow(panel, run)
    assert opened(panel, a) and opened(panel, b) and not opened(panel, c)
    assert marks_at(panel, row_of(panel, "enter 0 3 0")) == ["parked"]
    run.monsters = {2: state(0x1014, parked={0x20: 0x1018})}  # next moves, within r_a
    run.tick(panel)
    assert opened(panel, a) and opened(panel, b)
    run.monsters = {2: state(0x1014)}  # nothing parks in r_b now, and the next move folds it
    run.tick(panel)
    run.monsters = {2: state(0x1008)}
    run.tick(panel)
    assert opened(panel, a) and not opened(panel, b)


def test_follow_leaves_the_view_where_it_is_while_next_stays(
    panel: BrainPanel, run: Running
) -> None:
    follow(panel, run)
    other = row_of(panel, "routine r_c")
    panel.tree.setCurrentIndex(panel.model.index_of(other, TEXT))  # a click is no navigation
    run.tick(panel)
    assert cursor(panel) == other and panel.following.isChecked()


def test_the_marks_are_drawn_in_the_gutter(panel: BrainPanel, run: Running) -> None:
    follow(panel, run)
    call = row_of(panel, "call 0")
    rect = panel.tree.visualRect(panel.model.index_of(call, PC))
    pill = panel.tree.viewport().grab().toImage()
    at = (rect.left() + PAD + 2, rect.center().y())
    assert pill.pixelColor(*at).name() == theme.mark("next").name()
    blank = panel.tree.visualRect(panel.model.index_of(row_of(panel, "routine r_a"), PC))
    cell = {
        pill.pixel(x, y) for x in range(blank.left(), blank.right()) for y in range(blank.height())
    }
    assert len(cell) == 1  # no address, no tag


def test_a_label_follows_the_line(panel: BrainPanel, run: Running) -> None:
    run.monsters = {2: state(0x1008, pair=(0, 3), parked={0x20: 0x1010})}
    follow(panel, run)
    enter = row_of(panel, "enter 3 0 0")
    (mark,) = panel.model.data(panel.model.index_of(enter, TEXT), MARKS)
    assert mark.label == "plays 0 3 instead (anim 9)"
    assert panel.model.data(panel.model.index_of(row_of(panel, "enter 1 4 0"), TEXT), MARKS)


def test_the_status_says_what_the_game_lacks(panel: BrainPanel, run: Running) -> None:
    run.running = False
    panel.following.setChecked(True)
    assert panel.live.text() == bg.NO_GAME and panel.model.marks == {}
    run.running = True
    run.monsters = {}
    run.tick(panel, 2)
    assert panel.live.text() == "no Tigrex (em75) in the game" and panel.model.marks == {}


def test_follow_off_clears_the_marks(panel: BrainPanel, run: Running) -> None:
    follow(panel, run)
    assert panel.model.marks
    panel.following.setChecked(False)
    assert panel.model.marks == {} and not panel.live.isVisible() and not panel.ws.follow.on
    assert panel.tree.isColumnHidden(PC) and not panel.ticker.isActive()
    asked = len(run.asked)
    run.tick(panel)
    assert len(run.asked) == asked


def test_the_gutter_keeps_the_addresses(panel: BrainPanel, run: Running) -> None:
    follow(panel, run)
    tags = panel.tree.columnWidth(PC)
    assert not panel.tree.isColumnHidden(PC) and tags > 0 and not panel.delegate.addresses
    panel.more.set_open(True)
    panel.addresses.setChecked(True)
    assert panel.tree.columnWidth(PC) > tags and panel.delegate.addresses
    panel.following.setChecked(False)
    assert not panel.tree.isColumnHidden(PC)  # the addresses stay
    panel.addresses.setChecked(False)
    assert panel.tree.isColumnHidden(PC)


def test_follow_polls_only_while_shown(qtbot: Any, panel: BrainPanel, run: Running) -> None:
    follow(panel, run)
    assert panel.ticker.isActive()
    panel.hide()
    assert not panel.ticker.isActive() and panel.ws.follow.on
    panel.show()
    assert panel.ticker.isActive()


def test_follow_switches_to_the_header_the_monster_runs(qtbot: Any, ws: MonsterWorkspace) -> None:
    def two() -> ir.Program:
        p = program()
        p.headers()[1].va = H1
        return p

    decoding(ws, two)
    g = Running(ws)
    p = make(qtbot, ws)
    g.monsters = {2: state(0x1008, header=H1)}
    p.following.setChecked(True)
    g.tick(p)
    qtbot.waitUntil(lambda: p.model.view is not None and p.model.view.header == "h1")
    assert p.variant.currentData() == "h1" and p.following.isChecked()
    call = next(r for r, ln in enumerate(view(p).lines) if ln.text.strip() == "call 0")
    assert marks_at(p, call) == ["next"] and cursor(p) == call
    g.monsters = {2: state(0x1008, header=H0)}  # and back, with no help from the user
    g.tick(p)
    qtbot.waitUntil(lambda: p.model.view is not None and p.model.view.header == "h0")
    assert p.following.isChecked() and marks_at(p, row_of(p, "call 0")) == ["next"]


def test_follow_waits_for_the_script(qtbot: Any, ws: MonsterWorkspace) -> None:
    release = threading.Event()
    ws.decode_brain = lambda g, row: (release.wait(10), program())[1]
    g = Running(ws)
    p = make(qtbot, ws, ready=False)
    p.following.setChecked(True)
    g.tick(p)
    assert g.asked == [] and p.live.text() == bg.READING
    release.set()
    qtbot.waitUntil(lambda: p.model.view is not None)
    g.tick(p)
    assert marks_at(p, row_of(p, "call 0")) == ["next"]


def test_another_species_drops_the_marks(qtbot: Any, panel: BrainPanel, run: Running) -> None:
    follow(panel, run)
    panel.ws.browse_brain(76)  # a new document, say: not the user's move in this tab
    panel.sync()
    assert panel.following.isChecked() and panel.model.marks == {}
    qtbot.waitUntil(lambda: panel.model.view is not None and panel.model.view.row == 76)
    assert panel.live.text() == bg.READING


@pytest.mark.parametrize(
    "go",
    ["goto", "link", "find", "outline", "back", "forward", "species", "variant"],
)
def test_going_anywhere_ends_follow(qtbot: Any, panel: BrainPanel, run: Running, go: str) -> None:
    panel.goto(row_of(panel, "goto 1"))
    panel.goto(row_of(panel, "call 0"))
    if go == "forward":
        panel.back.click()  # one step ahead again
    qtbot.waitUntil(lambda: panel.species.count() == 3)
    follow(panel, run)
    assert panel.following.isChecked()
    match go:
        case "goto":
            panel.goto(row_of(panel, "routine r_b"))
        case "link":
            panel._follow(row_of(panel, "goto 1"))
        case "find":
            panel.query.setText("enter")
            qtbot.keyClick(panel.query, Qt.Key.Key_Return)
        case "outline":
            lists = panel.lists
            click(lists, lists.visualItemRect(lists.topLevelItem(0).child(1)).center())
        case "back":
            panel.back.click()
        case "forward":
            panel.forward.click()
        case "species":
            panel.species.setCurrentIndex(1)
            panel.species.activated.emit(1)
        case "variant":
            panel.variant.setCurrentIndex(1)
            panel.variant.activated.emit(1)
    assert not panel.following.isChecked() and not panel.ws.follow.on
    assert panel.model.marks == {} and not panel.ticker.isActive() and not panel.live.isVisible()


def test_a_step_with_nowhere_to_go_keeps_follow(panel: BrainPanel, run: Running) -> None:
    follow(panel, run)
    panel._step(True)  # Alt+Left with no history
    assert panel.following.isChecked()


def test_follow_has_tips(panel: BrainPanel, run: Running) -> None:
    follow(panel, run)
    assert kit.missing_tips(panel) == [] and panel.following.toolTip()
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


# ---- the editor over a brain file ------------------------------------------------------------- #


class Clock:
    """The debounce's clock: a test moves it."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def new_pak(tmp_path: Path, synthetic_pac: bytes, monkeypatch: pytest.MonkeyPatch) -> Extracted:
    """An extraction with Tigrex's and its sibling's model PAC; no stick, a cache of its own."""
    (tmp_path / "data_files").mkdir()
    for sp in (75, 76):
        pac = tmp_path / "data_files" / f"file_{files.monster_pac(sp):05d}.bin"
        pac.write_bytes(synthetic_pac)
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", (str(tmp_path / "no-stick"),))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return Extracted(tmp_path)


def new_editor_ws(pak: Extracted, intel75: SpeciesIntel, clock: Clock) -> MonsterWorkspace:
    """The Tigrex open on an empty brain file, over a fake decode of `world`."""
    w = MonsterWorkspace(Data(pak, pak))
    w.intel_cache[75] = intel75
    w.decode_brain = lambda g, row: world(row)
    w.scan_brain = lambda g: {75: 75, 76: 75}
    w.clock = clock
    w.open(pak.path(files.monster_pac(75)))
    return w


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def pak(tmp_path: Path, synthetic_pac: bytes, monkeypatch: pytest.MonkeyPatch) -> Extracted:
    return new_pak(tmp_path, synthetic_pac, monkeypatch)


@pytest.fixture
def edit_ws(pak: Extracted, intel75: SpeciesIntel, clock: Clock) -> Iterator[MonsterWorkspace]:
    w = new_editor_ws(pak, intel75, clock)
    yield w
    w.close()


def settle(p: BrainPanel, clock: Clock) -> None:
    """The panel's timer, by hand: the clock passes the debounce each turn, until the build is in
    and shown with its budget."""
    stop = time.monotonic() + 20
    while True:
        clock.t += mw.DEBOUNCE
        p._pull()
        if not p.ws.brain_pending(75) and p.ws.brain_built(75) is p._built and p.status.text():
            return
        assert time.monotonic() < stop, "the build did not finish"
        time.sleep(0.002)


@pytest.fixture
def editor(qtbot: Any, edit_ws: MonsterWorkspace, clock: Clock) -> BrainPanel:
    """The panel on `edit_ws`, with the first build shown."""
    p = make(qtbot, edit_ws)
    settle(p, clock)
    return p


def test_a_species_file_makes_the_tab_the_editor(editor: BrainPanel) -> None:
    p = editor
    v = view(p)
    assert p._editing and p.tree.editing and p.left.currentWidget() is p.outline
    assert p.tools.isVisible() and p.graph_slot.isVisible() and p.bar_slot.isVisible()
    assert v.header == "h1" and shown(p) == [v.lines[r].text for r in v.top]
    budget = p.ws.brain_budget(75)
    assert (
        budget is not None
        and p.status.text() == f"longest run {mw.budget_text(budget)} instructions"
    )
    assert kit.missing_tips(p) == [] and p.studio.errors == []


def test_a_port_view_stays_read_only(panel: BrainPanel) -> None:
    p = panel
    assert not p._editing and not p.tree.editing and p.left.currentWidget() is p.lists
    assert not p.tools.isVisible() and not p.graph_slot.isVisible() and not p.bar_slot.isVisible()
    p.tree.setCurrentIndex(p.model.index_of(row_of(p, "enter 3 0 0"), TEXT))
    assert p.tree.menu() is None and p.ws.brain_focus is None and p.ws.brain_focused == 0
    assert p.model.edit_kind(0) == "" and not p.model.is_dead(1) and p.studio.errors == []


def test_a_row_the_file_does_not_serve_reads_only(qtbot: Any, editor: BrainPanel) -> None:
    p = editor
    p.ws.browse_brain(88)
    p.sync()
    qtbot.waitUntil(lambda: p.model.view is not None and p.model.view.row == 88)
    assert not p._editing and p.left.currentWidget() is p.lists and not p.tools.isVisible()
    p.ws.browse_brain(76)  # served by the same overlay: editable again
    p.sync()
    qtbot.waitUntil(lambda: p._editing and view(p).row == 76)
    assert p.left.currentWidget() is p.outline and p.tools.isVisible()


def test_the_outline_groups_the_routines(editor: BrainPanel) -> None:
    o = editor.outline
    groups = [o.topLevelItem(i).text(0) for i in range(o.topLevelItemCount())]
    assert groups == ["Decisions (2)", "Calls (2)"]
    rows = {r: o.items[r].data(0, ROW) for r in o.items}
    assert list(rows) == ["base_0", "base_1", "call_0", "call_1"]
    assert o.items["base_0"].text(0) == "base_0" and o.items["base_0"].text(1) == "\u2190 1"
    assert o.items["base_1"].text(1) == "" and o.items["call_0"].text(1) == "\u2190 1"
    assert details(rows["base_0"]) == [("Top", "muted"), ("shared", "muted"), ("11 nodes", "muted")]
    assert details(rows["call_1"]) == [("shared", "muted"), ("7 nodes", "muted")]
    assert "Top" in o.items["base_0"].toolTip(0) and "base 0" in o.items["base_0"].toolTip(0)
    assert o.items["base_0"].toolTip(1) and not o.items["base_1"].toolTip(1)


def test_an_edit_shows_on_its_row(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    assert p.ws.edit_brain(be.set_pair(node_of(p, "enter 5 0 0"), 5, 3))
    assert p.ws.edit_brain(be.insert_entry(be.Place.end("call_1"), be.PALETTE["Moves"][0]))
    settle(p, clock)
    rows = {r: p.outline.items[r].data(0, ROW) for r in p.outline.items}
    assert rows["base_1"].edited and rows["call_1"].edited and not rows["base_0"].edited
    assert details(rows["call_1"])[-1] == ("8 nodes", "muted")


def test_the_outline_keeps_its_place(editor: BrainPanel) -> None:
    o = editor.outline
    calls = next(
        o.topLevelItem(i) for i in range(2) if o.topLevelItem(i).text(0).startswith("Calls")
    )
    calls.setExpanded(False)
    o.select("base_1")
    o.verticalScrollBar().setValue(0)
    o.show_outline(editor.ws.brain_outline(75, "h1"), editor.ws.brain_names(75))
    assert not o.topLevelItem(1).isExpanded() and o.topLevelItem(0).isExpanded()
    assert o.routine() == "base_1" and set(o.items) == {"base_0", "base_1", "call_0", "call_1"}
    o.show_outline(None, {})
    assert o.topLevelItemCount() == 0 and o.items == {} and o.routine() is None


def test_a_click_on_a_routine_shows_it(editor: BrainPanel) -> None:
    p, o = editor, editor.outline
    item = o.items["call_1"]
    o.itemClicked.emit(item, 0)
    assert p.ws.brain_routine == "call_1" and p.ws.brain_focus is None
    assert cursor(p) == row_of(p, "routine call_1") and opened(p, cursor(p))
    assert o.routine() == "call_1" and p.studio.errors == []
    o.itemActivated.emit(o.items["base_1"], 0)  # Return does the same
    assert cursor(p) == row_of(p, "routine base_1")


def test_reached_from_lists_the_callers(editor: BrainPanel) -> None:
    p, o = editor, editor.outline
    row = o.items["base_0"].data(0, ROW)
    menu = o.reach_menu(row)
    assert [a.text() for a in menu.actions()] == ["base_1 (1 call)"]
    menu.actions()[0].trigger()  # the first call: base_1's goto
    assert p.ws.brain_focus == node_of(p, "goto 0") and p.ws.brain_routine == "base_1"
    assert cursor(p) == row_of(p, "goto 0")
    o.itemClicked.emit(o.items["base_1"], 1)  # a row with no caller: the click shows it
    assert cursor(p) == row_of(p, "routine base_1")


def test_the_cursor_tells_the_workspace(editor: BrainPanel) -> None:
    p = editor
    line = row_of(p, "enter 6 1 0")
    p.tree.setCurrentIndex(p.model.index_of(line, TEXT))
    assert p.ws.brain_focus == node_of(p, "enter 6 1 0") and p.ws.brain_routine == "call_1"
    assert p.ws.brain_header == "h1" and p.outline.routine() == "call_1"
    p.tree.setCurrentIndex(p.model.index_of(row_of(p, "routine call_0"), TEXT))
    assert p.ws.brain_focus is None and p.ws.brain_routine == "call_0"
    focused = p.ws.brain_focused
    p.tree.setCurrentIndex(p.model.index_of(row_of(p, "table call_l: call_0 call_1 -"), TEXT))
    assert p.ws.brain_focused == focused  # a list is no place of the script
    assert p.studio.errors == []


def test_a_place_asked_for_selects_its_line(editor: BrainPanel) -> None:
    p = editor
    p.ws.follow.on = True
    p.following.setChecked(True)
    assert p.ws.show_brain_node(node_of(p, "enter 4 1 0"))
    p.sync()
    assert cursor(p) == row_of(p, "enter 4 1 0") and opened(p, row_of(p, "routine call_0"))
    assert not p.ws.follow.on and p.history.rows[-1] == cursor(p)  # a jump ends Follow
    p.ws.show_brain_node(None, "base_1")
    p.sync()
    assert cursor(p) == row_of(p, "routine base_1") and p.outline.routine() == "base_1"
    p.ws.reveal(("node", node_of(p, "wait 3")))  # the shell's way, from a finding
    p.sync()
    assert cursor(p) == row_of(p, "wait 3")
    assert p.history.back() == row_of(p, "routine base_1")


def test_the_graph_hooks(editor: BrainPanel) -> None:
    p = editor
    p.on_node(node_of(p, "enter 1 1 0"))
    assert p.ws.brain_focus == node_of(p, "enter 1 1 0") and cursor(p) == row_of(p, "enter 1 1 0")
    p.on_node("call_1")  # a routine's name is a place too
    assert cursor(p) == row_of(p, "routine call_1")
    p.on_node(None)
    p.on_node("no such node")
    assert cursor(p) == row_of(p, "routine call_1")
    arm = node_of(p, "branch 16")
    p.on_scope("base_0", arm)
    assert p.ws.brain_routine == "base_0" and p.ws.brain_scope == arm
    assert cursor(p) == row_of(p, "branch 16") and p.outline.routine() == "base_0"
    p.on_scope("base_1", "")
    assert p.ws.brain_scope == "" and cursor(p) == row_of(p, "routine base_1")
    assert p.studio.errors == []


def test_a_rebuild_keeps_the_place(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    p.goto(row_of(p, "enter 6 1 0"))
    p.goto(row_of(p, "goto 0"))
    p.tree.expand(p.model.index_of(row_of(p, "routine call_0")))
    first = view(p)
    assert p.ws.edit_brain(be.set_pair(node_of(p, "enter 6 1 0"), 6, 5))
    settle(p, clock)
    v = view(p)
    assert v is not first and v.row == 75 and v.header == first.header
    assert cursor(p) == row_of(p, "goto 0") and opened(p, row_of(p, "routine base_1"))
    assert opened(p, row_of(p, "routine call_0")) and not opened(p, row_of(p, "routine call_1"))
    assert [v.lines[r].text.strip() for r in p.history.rows] == ["enter 6 5 0", "goto 0"]
    assert p.history.at == 1 and p.history.can_back and not p.history.can_forward
    assert p.studio.errors == []


def test_a_node_that_is_gone_leaves_its_routine(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    p.goto(row_of(p, "enter 5 1 0"))
    assert p.ws.edit_brain(be.delete(node_of(p, "enter 5 1 0")))
    settle(p, clock)
    assert cursor(p) == row_of(p, "routine base_1")
    assert "enter 5 1 0" not in shown_all(p)


def test_a_new_build_stales_the_findings(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    p.studio.findings.get(p.ws.document)
    assert not p.studio.findings._stale
    assert p.ws.edit_brain(be.set_pair(node_of(p, "enter 6 1 0"), 6, 5))
    settle(p, clock)
    assert p.studio.findings._stale
    p.studio.findings.get(p.ws.document)
    p._pull()
    assert not p.studio.findings._stale  # the same build is no news


def test_building_is_said_while_it_builds(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    assert p.ws.edit_brain(be.set_pair(node_of(p, "enter 6 1 0"), 6, 5))
    p.sync()
    assert p.status.text() == "building\u2026" and p.poll.isActive()
    assert not p.send.isEnabled() and "still building" in p.send.toolTip()
    settle(p, clock)
    assert p.status.text().startswith("longest run") and not p.poll.isActive()


def test_a_build_with_errors_is_said(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    assert p.ws.edit_brain(be.set_pair(node_of(p, "enter 6 1 0"), 9, 0))  # main past 0..7
    settle(p, clock)
    row = p.outline.items["call_1"].data(0, ROW)
    assert row.findings == 2 and details(row)[-1] == ("2 findings", "error")
    assert row.level == "error" and not p.outline.items["base_0"].data(0, ROW).findings
    assert p.ws.brain_built(75) is not None and not p.ws.brain_built(75).ok
    assert "main 9" in p.send.toolTip() and not p.send.isEnabled() and not p.push.isEnabled()
    assert p.status.text().startswith(
        "longest run"
    )  # the build has a budget; the errors are Findings'


# ---- Send, Push and Count --------------------------------------------------------------------- #


@pytest.fixture
def stick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "PSP"
    (root / inject.MODS_SUBDIR).mkdir(parents=True)
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", (str(root),))
    return root


def test_send_waits_for_a_stick(
    editor: BrainPanel, stick: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = editor
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", (str(stick.parent / "none"),))
    p.sync()
    assert not p.send.isEnabled() and "memory stick" in p.send.toolTip()
    assert p.push.isEnabled() and p.push.toolTip() == PUSH_TIP  # Push goes to the game, not a stick
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", (str(stick),))
    p.sync()
    assert p.send.isEnabled() and p.send.toolTip() == SEND_TIP


def test_send_puts_the_build_on_the_stick(editor: BrainPanel, stick: Path, clock: Clock) -> None:
    p = editor
    assert p.ws.edit_brain(be.set_pair(node_of(p, "enter 6 1 0"), 6, 5))
    settle(p, clock)
    p.sync()
    assert p.send.isEnabled()
    p.send.click()
    built = p.ws.brain_built(75)
    box = stick / inject.BRAIN_SUBDIR / inject.brain_filename(75)
    assert p.ws.message == mw.SENT and box.read_bytes() == built.container
    assert p.studio.message == mw.SENT and p.studio.errors == []


def test_push_writes_the_build_into_the_game(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    assert p.ws.edit_brain(be.set_pair(node_of(p, "enter 6 1 0"), 6, 5))
    settle(p, clock)
    calls: list[Any] = []
    p.ws.game_session = lambda: nullcontext("session")

    def push(s: Any, prog: ir.Program, img: Any) -> Pushed:
        calls.append((s, prog, img))
        return Pushed(Plan((), ()), 0.01, where="the game")

    p.ws.push_script = push
    p.sync()
    assert p.push.isEnabled()
    p.push.click()
    built = p.ws.brain_built(75)
    assert calls == [("session", built.program, built.image)] and p.ws.message == mw.PUSHED


def test_count_waits_for_a_followed_monster(editor: BrainPanel) -> None:
    p = editor
    assert not p.counting.isEnabled() and bg.NOT_FOLLOWING in p.counting.toolTip()
    p.ws.follow.on = True
    p.sync()
    assert not p.counting.isEnabled()  # on, and nothing read yet
    p.ws.follow.target = bg.Target(0x1234, bg.NATIVE, None, None)
    p.sync()
    assert p.counting.isEnabled() and p.counting.text() == "Count 30 s"


def test_count_shows_on_the_lines(qtbot: Any, editor: BrainPanel) -> None:
    p, w = editor, editor.ws
    hub = node_of(p, "if_latch")
    pc = next(n.pc for n in w.brain_lift(75).nodes() if n.id == hub)
    w.follow.on = True
    w.follow.target = bg.Target(0x1234, bg.NATIVE, None, None)
    w.game_session = lambda: nullcontext("session")
    w.trace_script = lambda s, e, sec: Trace(
        e, sec, tuple(Step(10 * k, pc, 0x30) for k in range(3)), 0
    )
    p.sync()
    p.counting.click()
    assert w.count.busy and not p.counting.isEnabled() and p.poll.isActive()
    qtbot.waitUntil(lambda: w.count.result is not None, timeout=10000)
    assert p.model.counts == {hub: 3} and not w.count.busy
    assert p.status.text().endswith("counted 3 instructions in 30 s")
    assert p.counting.isEnabled() and not p.poll.isActive()
    row = row_of(p, "if_latch")
    assert p.model.data(p.model.index_of(row, TEXT), COUNT) == 3
    assert p.model.data(p.model.index_of(row_of(p, "goto 0"), TEXT), COUNT) is None
    p.following.setChecked(True)
    p.following.setChecked(False)  # Follow off drops the count
    p._pull()
    assert w.count.result is None and p.model.counts == {}


def test_count_says_why_it_did_not_start(editor: BrainPanel, clock: Clock) -> None:
    p, w = editor, editor.ws
    assert w.edit_brain(be.set_pair(node_of(p, "enter 6 1 0"), 6, 5))
    settle(p, clock)
    built = w.brain_built(75)
    assert built is not None
    w.follow.on = True
    w.follow.target = bg.Target(0x1234, bg.BUILD, (built.id or 0) + 1, 0x4000)  # another build
    p.sync()
    p.counting.click()
    assert "older build" in p.studio.message and p.studio.errors == []
    assert not w.count.busy and not p.poll.isActive()


def test_next_tags_its_routine_in_the_outline(editor: BrainPanel) -> None:
    p = editor
    g = Running(p.ws)
    va = {h.header: h.va for h in p.ws.brain_headers(75)}["h1"]
    line = view(p).lines[row_of(p, "goto 0")]
    g.monsters = {2: state(line.pc, header=va)}
    p.following.setChecked(True)
    g.tick(p)
    assert p.ws.follow.next_row == row_of(p, "goto 0") and p.outline.rows.next == "base_1"
    assert cursor(p) == row_of(p, "goto 0") and p.ws.brain_focus is None  # Follow moves no one else
    p.following.setChecked(False)
    assert p.outline.rows.next is None


def test_a_new_document_reloads_the_tab(qtbot: Any, editor: BrainPanel, pak: Extracted) -> None:
    p, first = editor, editor.ws.species_doc
    p.ws.open(pak.path(files.monster_pac(76)))
    assert p.ws.species_doc is not first and p.ws.brain_species == 76
    p.sync()
    qtbot.waitUntil(lambda: p.model.view is not None and p.model.view.row == 76 and p._editing)
    assert p._doc is p.ws.species_doc and p.outline.items


def test_follow_says_it_runs_your_edits(editor: BrainPanel, clock: Clock) -> None:
    p, f = editor, editor.ws.follow
    f.on, f.status = True, "following \u00b7 slot 2"
    f.target = bg.Target(0x1234, bg.BUILD, 7, 0x4000)
    p._marks()
    assert p.live.text() == "following \u00b7 slot 2 \u00b7 runs your edits"
    f.target = bg.Target(0x1234, bg.NATIVE, None, None)
    p._marks()
    assert p.live.text() == "following \u00b7 slot 2"


def test_plugged_widgets_are_synced(editor: BrainPanel) -> None:
    class Part(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.synced = 0

        def sync(self) -> None:
            self.synced += 1

    p, graph, bar = editor, Part(), Part()
    first = (p.graph_slot, p.bar_slot)
    p.plug(graph, bar)
    assert p.graph_slot is graph and p.bar_slot is bar and p.views.widget(0) is graph
    assert p.views.indexOf(first[0]) == -1
    p.sync()
    assert (graph.synced, bar.synced) == (1, 1) and graph.isVisible() and bar.isVisible()
    p.ws.browse_brain(88)
    p.sync()
    assert not graph.isVisible() and not bar.isVisible()


def test_window_editor(
    make_window: Callable[..., Any], qtbot: Any, edit_ws: MonsterWorkspace
) -> None:
    w = make_window(Hosted(edit_ws))
    panel = w.findChild(BrainPanel)
    assert panel is not None
    [opener] = [a for a in w.panels_menu.actions() if a.text() == "Brain"]
    opener.trigger()
    w.sync()
    qtbot.waitUntil(lambda: panel._editing and panel.model.view is not None)
    assert panel.isVisible() and kit.missing_tips(panel) == []
    qtbot.waitUntil(lambda: bool(panel.status.text()) and not panel.poll.isActive())
    panel.tree.setCurrentIndex(panel.model.index_of(row_of(panel, "routine base_1"), TEXT))
    assert edit_ws.brain_routine == "base_1" and panel.outline.routine() == "base_1"
    w.show_tab("3D view")
    assert not panel.isVisible() and not panel.poll.isActive() and w.studio.errors == []


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


@pytest.mark.parametrize("family", theme.FAMILIES)
@pytest.mark.parametrize("dark", [True, False])
def test_theme_marks(family: str, dark: bool) -> None:
    t = theme.theme(family, dark)
    assert set(t.marks) == {*theme.MARK_KINDS, "ink"} and len(set(t.marks.values())) == 4


def test_every_mark_has_a_colour() -> None:
    assert bg.KINDS == theme.MARK_KINDS


def test_current_marks(qapp: Any) -> None:
    theme.apply(theme.theme("Moss", True))
    dark = theme.mark("next")
    theme.apply(theme.theme("Moss", False))
    assert theme.mark("next") != dark and theme.mark("no such kind") == theme.accent()
    assert theme.mark("ink") != theme.mark("next")


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
