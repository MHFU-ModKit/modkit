# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Brain tab's code pane over a brain file: tints, notes, the context menu and its gestures.
Offscreen, on `world`'s script; the em75 test needs MHFU_DATA."""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mhfu import files, inject
from mhfu.em.intel import SpeciesIntel
from mhfu.em.script import brainfile
from mhfu.files import Extracted
from mhfu_port.data import Data
from mhfu_studio.monster import brain_edit as be
from mhfu_studio.monster.panels.brain import ROW, BrainPanel
from mhfu_studio.monster.panels.brain_code import DEAD, EDIT, NOTE, TEXT, Spot
from mhfu_studio.monster.species_doc import SpeciesDocument
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.ui import theme
from PySide6.QtCore import QItemSelectionModel, QPoint
from PySide6.QtGui import QContextMenuEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import QMenu

from .test_brain import Clock, cursor, make, new_editor_ws, new_pak, settle, view


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


@pytest.fixture
def editor(qtbot: Any, edit_ws: MonsterWorkspace, clock: Clock) -> BrainPanel:
    """The panel on `edit_ws`, with the first build shown."""
    p = make(qtbot, edit_ws)
    settle(p, clock)
    return p


def find(p: BrainPanel, text: str, nth: int = 0) -> int:
    """The row of the `nth` line that says `text`."""
    return [i for i, ln in enumerate(view(p).lines) if ln.text.strip() == text][nth]


def node(p: BrainPanel, text: str, nth: int = 0) -> str:
    """The id of the node whose line says `text`."""
    got = view(p).lines[find(p, text, nth)].node
    assert got is not None
    return got


def goto(p: BrainPanel, text: str, nth: int = 0) -> int:
    """Puts the cursor on the line that says `text`, as the user does."""
    row = find(p, text, nth)
    p.tree.setCurrentIndex(p.model.index_of(row, TEXT))
    return row


def add(p: BrainPanel, text: str) -> None:
    """Adds the line that says `text` to the selection."""
    p.tree.selectionModel().select(
        p.model.index_of(find(p, text), TEXT),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )


def where(menu: QMenu, *path: str) -> Any:
    """The submenu, or the action, at the end of a path of texts. A step may skip the menus a
    long group is split into: it is looked for below the last one."""
    here: Any = menu
    for step in path:
        found = [a for a in below(here) if a.text() == step]
        assert found, f"{step!r} is not in {[a.text() for a in here.actions()]}"
        here = found[0].menu() or found[0]
    return here


def below(menu: QMenu) -> list[Any]:
    """The actions in a menu and in the menus under it, the nearest first."""
    out = list(menu.actions())
    for a in menu.actions():
        if a.menu() is not None:
            out += below(a.menu())
    return out


def leaves(menu: QMenu) -> set[str]:
    """The texts of the actions under a menu that are not menus."""
    return {a.text() for a in below(menu) if a.menu() is None}


def texts(menu: QMenu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def menu_of(p: BrainPanel, text: str, nth: int = 0) -> QMenu:
    goto(p, text, nth)
    menu = p.tree.menu()
    assert menu is not None
    return menu


def pick(p: BrainPanel, text: str, *path: str, nth: int = 0) -> None:
    where(menu_of(p, text, nth), *path).trigger()


def doc_of(p: BrainPanel) -> SpeciesDocument:
    assert p.ws.species_doc is not None
    return p.ws.species_doc


def dumped(p: BrainPanel) -> str:
    return brainfile.dumps(doc_of(p).brainfile)


def undo_steps(p: BrainPanel) -> int:
    """How many undos take the document back to where it opened."""
    doc, n = doc_of(p), 0
    while doc.can_undo():
        doc.undo()
        n += 1
    return n


# ---- tints ------------------------------------------------------------------------------------- #


def test_unedited_lines_have_no_tint(editor: BrainPanel) -> None:
    p, v = editor, view(editor)
    assert {p.model.data(p.model.index_of(r, TEXT), EDIT) for r in range(len(v.lines))} == {""}
    assert not any(p.model.data(p.model.index_of(r, TEXT), DEAD) for r in range(len(v.lines)))


def paint(p: BrainPanel, row: int, x: int = 1) -> str:
    """The colour of the tree's left edge on a line's row, as drawn."""
    p.tree.scrollTo(p.model.index_of(row, TEXT))
    box = p.tree.visualRect(p.model.index_of(row, TEXT))
    return str(p.tree.viewport().grab().toImage().pixelColor(x, box.center().y()).name())


def test_an_edited_line_is_tinted(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    p.goto(find(p, "routine base_1"))
    assert p.ws.edit_brain(be.set_pair(node(p, "enter 5 1 0"), 5, 3))
    settle(p, clock)
    p.goto(find(p, "routine base_1"))
    edited, routine = find(p, "enter 5 3 0"), find(p, "routine base_1")
    kind = lambda r: p.model.data(p.model.index_of(r, TEXT), EDIT)  # noqa: E731
    assert kind(edited) == "edited" and kind(routine) == "edited"
    assert kind(find(p, "enter 5 0 0")) == "" and kind(find(p, "routine call_0")) == ""
    assert paint(p, edited) == theme.edit("edited").name()
    assert paint(p, find(p, "enter 5 0 0")) != theme.edit("edited").name()


def test_new_code_is_new(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    assert p.ws.edit_brain(be.insert_entry(be.Place.after(node(p, "enter 5 0 0")), move()))
    settle(p, clock)
    p.goto(find(p, "routine base_1"))
    v = view(p)
    new = [r for r in v.span(find(p, "routine base_1")) if p.model.edit_kind(r) == "new"]
    assert [v.lines[r].text.strip() for r in new] == ["enter 0 1 0"]
    (fresh,) = new
    assert paint(p, fresh) == theme.edit("new").name()
    assert p.model.data(p.model.index_of(find(p, "enter 5 0 0"), TEXT), EDIT) == ""


def move() -> be.PaletteEntry:
    return be.PALETTE["Moves"][0]


def test_code_after_an_end_is_dead(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    end = node(p, "end return")  # the first: call_0's
    assert p.ws.edit_brain(be.insert_entry(be.Place.after(end), move()))
    settle(p, clock)
    p.goto(find(p, "routine call_0"))
    v = view(p)
    dead = [r for r in range(len(v.lines)) if p.model.data(p.model.index_of(r, TEXT), DEAD)]
    assert [v.lines[r].pc is None for r in dead] == [True]
    assert not p.model.is_dead(find(p, "end return"))
    assert not p.model.is_dead(find(p, "routine call_0"))


def test_notes_follow_their_lines(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    assert p.ws.edit_brain(be.set_note(node(p, "goto 0"), "loops back"))
    assert p.ws.edit_brain(be.set_note("call_1", "the last"))
    settle(p, clock)
    note = lambda t: p.model.data(p.model.index_of(find(p, t), TEXT), NOTE)  # noqa: E731
    assert note("goto 0") == "loops back" and note("routine call_1") == "the last"
    assert note("routine call_0") == "" and note("enter 5 0 0") == ""


# ---- the context menu -------------------------------------------------------------------------- #


def test_a_node_offers_its_gestures(editor: BrainPanel) -> None:
    p = editor
    menu = menu_of(p, "enter 6 1 0")
    assert texts(menu) == [
        "Insert before",
        "Insert after",
        "Insert at end of scope",
        "Delete",
        "Replace with",
        "Move up",
        "Move down",
        "Wrap in",
        "Note…",
    ]
    groups = texts(where(menu, "Insert before"))
    assert groups == ["Moves", "Conditions", "Switch", "Pick one", "Flow", "Other"]
    assert texts(where(menu, "Insert before", "Moves")) == ["Move"]
    assert len(texts(where(menu, "Insert before", "Conditions"))) < len(be.PALETTE["Conditions"])
    assert "Hunter" in texts(where(menu, "Insert before", "Conditions"))  # a long group by theme
    assert where(menu, "Insert before", "Moves", "Move").toolTip()
    replace = texts(where(menu, "Replace with"))
    assert "Moves" not in replace and "Conditions" not in replace  # a move is no block
    assert "Move" not in leaves(where(menu, "Replace with")) and "Set target" in leaves(
        where(menu, "Replace with")
    )
    wrap = texts(where(menu, "Wrap in"))
    assert "Conditions" in wrap and wrap[-1] == "Pick one" and "Moves" not in wrap


def test_a_block_offers_its_own_kind(editor: BrainPanel) -> None:
    p = editor
    inside = menu_of(p, "if_latch")
    assert "Insert inside" in texts(inside) and "Add else" not in texts(inside)  # it has an else
    swaps = where(inside, "Replace with")
    assert texts(swaps) == ["Conditions"] and "Latch" not in leaves(swaps)
    assert texts(where(menu_of(p, "switch_hp"), "Replace with")) == ["Switch"]
    repeat = menu_of(p, "repeat 3")
    assert "Insert inside" in texts(repeat) and "Add arm" not in texts(repeat)
    assert not where(repeat, "Replace with").isEnabled()  # the only one of its shape
    assert "Add else" in texts(menu_of(p, "if_aware"))
    assert "Add arm" in texts(menu_of(p, "random")) and "Add arm" in texts(menu_of(p, "switch_hp"))
    assert "Insert inside" not in texts(menu_of(p, "random"))


def test_an_arm_and_a_routine(editor: BrainPanel) -> None:
    p = editor
    assert texts(menu_of(p, "branch 16")) == [
        "Insert at start of arm",
        "Insert at end of arm",
        "Remove arm",
        "Note…",
    ]
    assert "Remove else" in texts(menu_of(p, "else"))
    assert texts(menu_of(p, "routine base_1")) == [
        "Insert at start of routine",
        "Insert at end of routine",
        "Note…",
        "Rename…",
    ]


def test_other_lines_have_no_menu(editor: BrainPanel) -> None:
    p = editor
    for text in (
        "table call_l: call_0 call_1 -",
        "header h1 variants 5-16",
        "tail 00 00 00 00 00 00",
    ):
        goto(p, text)
        assert p.tree.menu() is None
    p.tree.setCurrentIndex(p.model.index_of(0, TEXT))
    assert p.tree.spot(0) is None
    p.tree.editing = False
    goto(p, "enter 6 1 0")
    assert p.tree.menu() is None


def test_a_move_that_goes_nowhere_is_off(editor: BrainPanel) -> None:
    p = editor
    first, last = menu_of(p, "enter 5 0 0"), menu_of(p, "goto 0")
    assert not where(first, "Move up").isEnabled() and where(first, "Move down").isEnabled()
    assert where(last, "Move up").isEnabled() and not where(last, "Move down").isEnabled()


def test_the_spot_of_a_line(editor: BrainPanel) -> None:
    p = editor
    t = p.tree
    assert t.spot(find(p, "routine base_1")) == Spot("routine", "base_1", "base_1")
    s = t.spot(find(p, "enter 4 0 0"))
    assert s is not None and s.what == "node" and s.routine == "call_0" and s.op == "enter"
    assert s.scope == node(p, "branch 20") and s.at == 0 and s.size == 1  # an arm's list
    inner = t.spot(find(p, "enter 3 0 0"))
    assert inner is not None and inner.scope == node(p, "repeat 3")  # a body: its block
    top = t.spot(find(p, "goto 0"))
    assert top is not None and top.scope == "base_1" and top.at == 3 and top.size == 4
    arm = t.spot(find(p, "case 1"))
    assert arm is not None and arm.what == "arm" and arm.shape is not None


@pytest.mark.parametrize(
    ("line", "path"),
    [
        ("enter 6 1 0", ("Insert before", "Move")),
        ("enter 6 1 0", ("Insert after", "Move")),
        ("enter 6 1 0", ("Insert at end of scope", "Move")),
        ("repeat 3", ("Insert inside", "Move")),
        ("branch 16", ("Insert at start of arm", "Move")),
        ("branch 16", ("Insert at end of arm", "Move")),
        ("routine base_1", ("Insert at start of routine", "Move")),
        ("routine base_1", ("Insert at end of routine", "Move")),
        ("enter 6 1 0", ("Delete",)),
        ("enter 6 1 0", ("Replace with", "Set target")),
        ("enter 6 1 0", ("Move down",)),
        ("enter 6 1 0", ("Wrap in", "Pick one")),
        ("enter 6 1 0", ("Wrap in", "Provoked")),
        ("random", ("Add arm",)),
        ("if_aware", ("Add else",)),
        ("branch 16", ("Remove arm",)),
        ("else", ("Remove else",)),
    ],
)
def test_a_gesture_is_one_undo_step(editor: BrainPanel, line: str, path: tuple[str, ...]) -> None:
    p = editor
    doc = doc_of(p)
    before = doc.version
    pick(p, line, *path)
    assert doc.version == before + 1 and doc.dirty and p.studio.errors == []
    assert p.studio.message and "nothing changes" not in p.studio.message
    assert undo_steps(p) == 1
    assert doc.brainfile.edits(75) is None  # and the file is as it was


def test_one_undo_takes_back_a_build_too(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    first = view(p)
    pick(p, "enter 6 1 0", "Delete")
    settle(p, clock)
    assert view(p) is not first and "enter 6 1 0" not in [ln.text.strip() for ln in view(p).lines]
    doc_of(p).undo()
    p.studio.changed()
    settle(p, clock)
    assert "enter 6 1 0" in [ln.text.strip() for ln in view(p).lines]
    assert view(p).edited == frozenset()


def test_an_insert_puts_the_default_node(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    pick(p, "enter 5 0 0", "Insert after", "Move")
    settle(p, clock)
    v = view(p)
    rows = list(v.span(find(p, "routine base_1")))
    assert [v.lines[r].text.strip() for r in rows][1:4] == [
        "enter 5 0 0",
        "enter 0 1 0",
        "enter 5 1 0",
    ]
    pick(p, "enter 5 0 0", "Insert before", "Conditions", "Provoked")
    settle(p, clock)
    assert [ln.text.strip() for ln in view(p).lines][find(p, "routine base_1") + 1] == "if_provoked"


def test_a_refusal_changes_nothing(editor: BrainPanel) -> None:
    p = editor
    doc = doc_of(p)
    before, version = dumped(p), doc.version
    p.tree.run("Insert code", lambda: be.insert_code(be.Place.after(node(p, "goto 0")), "nonsense"))
    assert p.studio.message.startswith("Insert code:") and "nonsense" in p.studio.message
    p.tree.run("Delete", lambda: be.delete("no such node"))
    assert p.studio.message.startswith("Delete:") and "no such node" in p.studio.message
    p.tree.run("Move up", lambda: be.earlier(node(p, "enter 5 0 0")))
    assert p.studio.message.startswith("Earlier:") and "first already" in p.studio.message
    assert doc.version == version and dumped(p) == before and not doc.can_undo() and not doc.dirty
    assert p.studio.errors == []


def test_a_gesture_the_build_lags_behind_is_refused(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    pick(p, "enter 5 0 0", "Insert after", "Move")  # new code: the view lags until the build is in
    doc = doc_of(p)
    version = doc.version
    pick(p, "enter 5 0 0", "Insert after", "Move")
    assert doc.version == version and "still building" in p.studio.message
    settle(p, clock)
    pick(p, "enter 5 0 0", "Insert after", "Move")
    assert doc.version == version + 1


def keys(p: BrainPanel) -> dict[str, QShortcut]:
    return {s.key().toString(): s for s in p.tree.findChildren(QShortcut)}


def test_the_keys_are_the_gestures(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    key = keys(p)
    both = {QKeySequence("Delete").toString(), QKeySequence("Backspace").toString()}
    assert {"Alt+Up", "Alt+Down", *both} <= set(key)
    goto(p, "enter 5 1 0")
    key["Alt+Down"].activated.emit()
    settle(p, clock)
    v = view(p)
    after = [v.lines[r].text.strip() for r in v.span(find(p, "routine base_1"))]
    assert after[1:5] == ["enter 5 0 0", "enter 5 2 0", "enter 5 1 0", "goto 0"]
    assert cursor(p) == find(p, "enter 5 1 0")  # the line the cursor was on is still it
    key["Alt+Up"].activated.emit()
    settle(p, clock)
    assert doc_of(p).brainfile.edits(75) is None  # back where it was
    key[QKeySequence("Delete").toString()].activated.emit()
    settle(p, clock)
    assert "enter 5 1 0" not in [ln.text.strip() for ln in view(p).lines]
    version = doc_of(p).version
    goto(p, "routine base_1")
    key[QKeySequence("Delete").toString()].activated.emit()  # a routine is no node
    p.tree.editing = False
    goto(p, "enter 5 0 0")
    key["Alt+Down"].activated.emit()  # and with no file to edit, nothing happens
    assert doc_of(p).version == version and p.studio.errors == []


def test_wrap_takes_the_selection(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    goto(p, "enter 6 0 0")
    add(p, "enter 6 1 0")
    menu = p.tree.menu()
    assert menu is not None and texts(menu) == ["Wrap in"]
    where(menu, "Wrap in", "Pick one").trigger()
    settle(p, clock)
    v = view(p)
    lines = [v.lines[r].text for r in v.span(find(p, "routine call_1"))]
    assert lines[1:5] == ["  random", "  branch 16", "    enter 6 0 0", "    enter 6 1 0"]
    assert undo_steps(p) == 1


def test_a_selection_that_is_no_run_is_refused(editor: BrainPanel) -> None:
    p = editor
    goto(p, "enter 6 0 0")
    add(p, "enter 6 2 0")
    menu = p.tree.menu()
    assert menu is not None
    where(menu, "Wrap in", "Pick one").trigger()
    assert "follow each other" in p.studio.message and not doc_of(p).can_undo()
    add(p, "routine call_1")
    assert p.tree.menu() is None  # a routine line among them: no wrap


def test_back_to_the_games(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    assert "Back to the game's node" not in texts(menu_of(p, "enter 5 0 0"))
    assert p.ws.edit_brain(be.set_pair(node(p, "enter 5 1 0"), 5, 3))
    assert p.ws.edit_brain(be.set_pair(node(p, "enter 5 2 0"), 5, 4))
    settle(p, clock)
    menu = menu_of(p, "enter 5 3 0")
    assert "Back to the game's node" in texts(menu) and "Back to the game's routine" in texts(menu)
    assert "Back to the game's node" not in texts(menu_of(p, "enter 5 0 0"))
    assert "Back to the game's routine" in texts(menu_of(p, "enter 5 0 0"))  # the routine is edited
    assert "Back to the game's routine" not in texts(menu_of(p, "enter 6 0 0"))
    assert "Back to the game's node" not in texts(menu_of(p, "routine base_1"))
    where(menu_of(p, "enter 5 3 0"), "Back to the game's node").trigger()
    settle(p, clock)
    assert "enter 5 1 0" in [ln.text.strip() for ln in view(p).lines]
    assert "enter 5 4 0" in [ln.text.strip() for ln in view(p).lines]  # the other edit stays
    where(menu_of(p, "routine base_1"), "Back to the game's routine").trigger()
    settle(p, clock)
    assert view(p).edited == frozenset() and doc_of(p).brainfile.edits(75) is None


def test_a_note_asks_for_its_text(editor: BrainPanel, clock: Clock) -> None:
    p = editor
    asked: list[tuple[str, str]] = []
    answer: list[str | None] = ["a thought"]

    def ask(parent: Any, what: str, text: str) -> str | None:
        asked.append((what, text))
        return answer[0]

    p.tree.ask_note = ask
    pick(p, "goto 0", "Note…")
    assert asked == [(node(p, "goto 0"), "")] and p.ws.brain_notes(75) == {
        node(p, "goto 0"): "a thought"
    }
    settle(p, clock)
    answer[0] = None  # Cancel
    pick(p, "goto 0", "Note…")
    assert asked[-1][1] == "a thought" and p.ws.brain_notes(75) == {node(p, "goto 0"): "a thought"}
    answer[0] = ""  # blank takes it away
    pick(p, "goto 0", "Note…")
    assert p.ws.brain_notes(75) == {} and p.studio.errors == []
    answer[0] = "on the routine"
    pick(p, "routine call_1", "Note…")
    assert p.ws.brain_notes(75) == {"call_1": "on the routine"}


def test_the_menu_event_picks_the_line_under_the_pointer(editor: BrainPanel) -> None:
    p = editor
    goto(p, "enter 5 0 0")
    row = find(p, "enter 5 2 0")
    pos = p.tree.visualRect(p.model.index_of(row, TEXT)).center()
    p.tree.contextMenuEvent(
        QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos, p.tree.viewport().mapToGlobal(pos))
    )
    assert cursor(p) == row and p.tree.selected() == [row]
    [menu] = [m for m in p.tree.findChildren(QMenu) if m.isVisible()]
    assert "Delete" in texts(menu)
    menu.close()
    blank = QPoint(5, p.tree.viewport().height() - 2)
    p.tree.contextMenuEvent(
        QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse, blank, p.tree.viewport().mapToGlobal(blank)
        )
    )
    assert cursor(p) == row  # nothing under the pointer: nothing happens
    other = goto(p, "enter 5 1 0")  # the Menu key: the cursor's line, wherever the pointer is
    p.tree.contextMenuEvent(
        QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, QPoint(0, 0), QPoint(0, 0))
    )
    [menu] = [m for m in p.tree.findChildren(QMenu) if m.isVisible()]
    assert cursor(p) == other and "Delete" in texts(menu)
    menu.close()


# ---- the game's own em75 ----------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def tigrex(mhfu_data: Path) -> Extracted:
    return Extracted.find(mhfu_data)


@pytest.fixture
def real(
    tigrex: Extracted, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[MonsterWorkspace]:
    """The game's own em75 open on an empty brain file; no intel, and no scan of every overlay."""
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", (str(tmp_path / "no-stick"),))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    w = MonsterWorkspace(Data(tigrex, tigrex))
    w.intel_cache[75] = None
    w.scan_brain = lambda g: {75: 75}
    w.open(tigrex.path(files.monster_pac(75)))
    yield w
    w.close()


def test_em75_through_the_menu(qtbot: Any, real: MonsterWorkspace) -> None:
    ticks = itertools.count()
    real.clock = lambda: float(next(ticks))  # each look is a second on: a build is due at once
    p = make(qtbot, real)
    qtbot.waitUntil(lambda: p.status.text() != "" and not real.brain_pending(75), timeout=30000)
    o = p.outline
    groups = [o.topLevelItem(i).text(0).split(" (")[0] for i in range(o.topLevelItemCount())]
    assert groups[0] == "Decisions" and "Events" in groups and len(o.items) > 80
    assert p.status.text().startswith("longest run ") and " of 999 " in p.status.text()
    rows = [i.data(0, ROW) for i in o.items.values()]
    callee = next(r for r in rows if r.callers and r.entry == "")
    assert all(r.reached_from == len(r.callers) for r in rows)
    caller = callee.callers[0].routine
    o.reach_menu(callee).actions()[0].trigger()  # the first call in the first caller
    v = view(p)
    first = v.lines[v.children[v.routine_row[callee.routine]][0]].node
    assert v.lines[cursor(p)].target == first and v.routine_at(cursor(p)) == caller
    p.goto(v.routine_row["base_0"])
    p.tree.setCurrentIndex(p.model.index_of(v.children[v.routine_row["base_0"]][0], TEXT))
    menu = p.tree.menu()
    assert menu is not None
    where(menu, "Insert before", "Moves", "Move").trigger()
    qtbot.waitUntil(lambda: bool(view(p).edited), timeout=30000)
    v = view(p)
    base = v.routine_row["base_0"]
    assert p.model.edit_kind(base) == "edited"
    assert [p.model.edit_kind(r) for r in v.span(base)].count("new") == 1
    assert cursor(p) == v.children[base][1]  # the line the cursor was on, one down
    assert p.studio.errors == [] and undo_steps(p) == 1
