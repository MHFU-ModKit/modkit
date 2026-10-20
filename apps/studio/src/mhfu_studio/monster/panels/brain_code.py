# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Brain tab's code pane: the script's lines as a tree, and the gestures a species file takes.

The model walks a `BrainView` as it is and one delegate draws the lines, so a script of ten
thousand lines opens as fast as one of a thousand. Edited and new lines are tinted (`theme.edit`),
dead ones dimmed. With a brain file open a context menu on a line runs a gesture of `brain_edit` as
one undo step through `studio.act`; a refusal says why in `ws.message` and commits nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, Literal

from mhfu.em.script.edit import EditError
from mhfu.em.script.graph import ROOT, RoutineGraph, Stmt, head_key
from mhfu.em.script.opcodes import Shape
from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import (
    QContextMenuEvent,
    QFontMetricsF,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QInputDialog,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
    QWidget,
)

from mhfu_studio.monster import brain_edit as be
from mhfu_studio.monster.brain import BrainView
from mhfu_studio.monster.brain_game import Mark, Marks
from mhfu_studio.ui import theme

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

#: the model's columns: the address of a line, and the line, which the tree is drawn in
PC, TEXT = 0, 1
#: item roles: a line's kind, whether it leads to a routine, what an `enter` line plays, Follow the
#: game's marks on it, what an edit did to it (`theme.EDIT_KINDS` but dead), whether it never runs,
#: the user's note on it, and how often Count saw it run
KIND = Qt.ItemDataRole.UserRole
LINK = Qt.ItemDataRole.UserRole + 1
LABEL = Qt.ItemDataRole.UserRole + 2
MARKS = Qt.ItemDataRole.UserRole + 3
EDIT = Qt.ItemDataRole.UserRole + 4
DEAD = Qt.ItemDataRole.UserRole + 5
NOTE = Qt.ItemDataRole.UserRole + 6
COUNT = Qt.ItemDataRole.UserRole + 7
PAD = 4
INDENT = 16
#: a selected row is one band: the branches' own fill would be a second layer over the gutter
ROW_QSS = "QTreeView { show-decoration-selected: 0; }"
#: an edited row's tint (its alpha) and the bar at its left edge
TINT, BAR = 0.16, 3
ARROW = "  \u2192 "
BACK = "  \u2190 "
TIMES = "  \u00d7"
HASH = "  # "
NO_INDEX = QModelIndex()
#: a palette group longer than this is split by what the catalogue files its opcodes under
TIER = 12
#: a line's key: what it is, its id or name, and its routine; what a rebuilt view looks it up by
Key = tuple[Literal["node", "routine"], str, str]
What = Literal["node", "arm", "routine"]


def tag_width(word: str) -> int:
    """The width a tag of `word` takes, with the space round it."""
    font = theme.mono()
    font.setBold(True)
    return round(QFontMetricsF(font).horizontalAdvance(word)) + 3 * PAD


def address(pc: int | None) -> str:
    """A line's address as the column writes it; empty for a line that has none."""
    return "" if pc is None else f"0x{pc:08X}"


def key_of(view: BrainView, row: int) -> Key | None:
    """What a view of the next build finds `row` again by: a node's id, a routine's name."""
    name = view.routine_at(row)
    if name is None:
        return None
    node = view.node_at(row)
    return ("routine", name, name) if node is None else ("node", node, name)


def row_of(view: BrainView, key: Key | None) -> int | None:
    """The row `key` is in `view`; a node that is gone falls back to its routine's line."""
    if key is None:
        return None
    kind, ref, routine = key
    if kind == "node" and (row := view.row_for(ref)) is not None:
        return row
    return view.routine_row.get(routine)


def dead_ids(rg: RoutineGraph) -> frozenset[str]:
    """The ids of the statements that never run, and of the arms of those that are blocks."""
    dead = {i for i, n in rg.nodes.items() if isinstance(n, Stmt) and n.dead}
    arms = {k for k, s in rg.scopes.items() if s.owner in dead and k != head_key(s.owner or "")}
    return frozenset(dead | arms)


class BrainModel(QAbstractItemModel):
    """A `BrainView` as a tree of its lines: an index's id is its line's row in the view."""

    def __init__(self) -> None:
        super().__init__()
        self.view: BrainView | None = None
        self.marks: Marks = {}
        #: the ids of the game's nodes the edits change (`graph.edited_ids`): an edited node has no
        #: native address either, which new code has none of but a game node's id
        self.changed: Collection[str] = frozenset()
        #: the user's notes by node id or routine name, and Count's counts by node id
        self.notes: dict[str, str] = {}
        self.counts: dict[str, int] = {}
        #: the ids of a routine's lines that never run; asked once per routine and view
        self.dead: Callable[[str], Collection[str]] = lambda routine: ()
        #: a row's place among its siblings
        self._at: list[int] = []
        self._never: dict[str, Collection[str]] = {}

    def set_view(self, view: BrainView | None) -> None:
        self.beginResetModel()
        self.view = view
        self.marks = {}
        self._never = {}
        self._at = [0] * (0 if view is None else len(view.lines))
        for siblings in () if view is None else (view.top, *view.children):
            for k, row in enumerate(siblings):
                self._at[row] = k
        self.endResetModel()

    def set_marks(self, marks: Marks) -> None:
        """Shows `marks` on the lines, repainting the ones whose marks changed."""
        old, self.marks = self.marks, marks
        for row in sorted({*old, *marks}):
            if old.get(row) != marks.get(row) and row < len(self._at):
                self.dataChanged.emit(self.index_of(row, PC), self.index_of(row, TEXT))

    def index_of(self, row: int, column: int = PC) -> QModelIndex:
        """The index of line `row`. Expanding and folding go by the first column."""
        return self.createIndex(self._at[row], column, row)

    def edit_kind(self, row: int) -> str:
        """`edited` for a game line the edits changed, `new` for code they add, else empty."""
        v = self.view
        if v is None or row not in v.edited:
            return ""
        ln = v.lines[row]
        fresh = ln.pc is None and (ln.node is None or ln.node not in self.changed)
        return "new" if fresh else "edited"

    def is_dead(self, row: int) -> bool:
        v = self.view
        if v is None or (node := v.lines[row].node) is None:
            return False
        name = v.routine_at(row)
        if name is None:
            return False
        if name not in self._never:
            self._never[name] = self.dead(name)
        return node in self._never[name]

    def _note(self, row: int) -> str:
        v = self.view
        if v is None:
            return ""
        node = v.lines[row].node
        if node is not None:
            return self.notes.get(node, "")
        name = v.routine_at(row)
        return "" if name is None else self.notes.get(name, "")

    def _below(self, parent: QModelIndex | QPersistentModelIndex) -> Sequence[int]:
        v = self.view
        if v is None:
            return ()
        if not parent.isValid():
            return v.top
        return v.children[parent.internalId()] if parent.column() == 0 else ()

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = NO_INDEX) -> int:  # noqa: N802
        return len(self._below(parent))

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = NO_INDEX) -> int:  # noqa: N802
        return 2

    def index(
        self, row: int, column: int, parent: QModelIndex | QPersistentModelIndex = NO_INDEX
    ) -> QModelIndex:
        below = self._below(parent)
        if 0 <= row < len(below) and 0 <= column < 2:
            return self.createIndex(row, column, below[row])
        return QModelIndex()

    def parent(self, child: QModelIndex | QPersistentModelIndex | None = None) -> Any:
        if child is None:
            return super().parent()
        if self.view is None or not child.isValid():
            return QModelIndex()
        up = self.view.parent[child.internalId()]
        return QModelIndex() if up < 0 else self.createIndex(self._at[up], 0, up)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = 0) -> Any:
        v = self.view
        if v is None or not index.isValid():
            return None
        row = index.internalId()
        if role == Qt.ItemDataRole.DisplayRole:
            ln = v.lines[row]
            return address(ln.pc) if index.column() == PC else ln.text
        if role == KIND:
            return v.lines[row].kind
        if role == LINK:
            return v.target_row(row) is not None
        if role == LABEL:
            return v.labels.get(row, "")
        if role == MARKS:
            return self.marks.get(row, ())
        if role == EDIT:
            return self.edit_kind(row)
        if role == DEAD:
            return self.is_dead(row)
        if role == NOTE:
            return self._note(row)
        if role == COUNT:
            node = v.lines[row].node
            return None if node is None else self.counts.get(node)
        return None


class Lines(QStyledItemDelegate):
    """A line: its text in the colour of its kind (grey when it never runs), underlined when it
    leads to a routine, and after it, quiet, what an `enter` plays, how often Count saw it run, the
    user's note and what Follow the game says of it. The tree indents, so the text's own indent
    goes. The first column holds the address when `addresses` is set, and the tag of the line's
    first mark."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.addresses = False

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text, opt.text = opt.text, ""
        opt.state &= ~QStyle.StateFlag.State_HasFocus  # the selected band marks the current row
        widget = option.widget
        (widget.style() if widget else QApplication.style()).drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget
        )
        room = QRectF(opt.rect).adjusted(PAD, 0, -PAD, 0)
        side = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        font = theme.mono()
        marks: Sequence[Mark] = index.data(MARKS) or ()
        painter.save()
        painter.setClipRect(room)
        if index.column() == PC and marks:
            self._tag(painter, room, marks[0])
        elif index.column() == PC:
            if self.addresses:
                painter.setFont(font)
                painter.setPen(theme.text(muted=True))
                painter.drawText(room, side, text)
        else:
            quiet = theme.code("label")
            ink = theme.edit("dead") if index.data(DEAD) else theme.code(index.data(KIND))
            parts = [(text.lstrip(), ink, bool(index.data(LINK)))]
            if label := index.data(LABEL):
                parts.append((ARROW + label, quiet, False))
            if (count := index.data(COUNT)) is not None:
                parts.append((f"{TIMES}{count}", theme.text(muted=True), False))
            if note := index.data(NOTE):
                parts.append((HASH + note, quiet, False))
            parts += [(BACK + m.label, theme.mark(m.kind), False) for m in marks if m.label]
            for words, colour, underline in parts:
                font.setUnderline(underline)
                painter.setFont(font)
                painter.setPen(colour)
                painter.drawText(room, side, words)
                room.setLeft(room.left() + QFontMetricsF(font).horizontalAdvance(words))
        painter.restore()

    @staticmethod
    def _tag(painter: QPainter, room: QRectF, mark: Mark) -> None:
        """A mark's tag, filled in its colour."""
        font = theme.mono()
        font.setBold(True)
        painter.setFont(font)
        word = mark.kind.upper()
        pill = QRectF(room.left(), room.top() + 2, tag_width(word) - PAD, room.height() - 4)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.mark(mark.kind))
        painter.drawRoundedRect(pill, 3, 3)
        painter.setPen(theme.mark("ink"))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, word)

    def sizeHint(  # noqa: N802
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        return QSize(0, round(QFontMetricsF(theme.mono()).height()) + 6)


@dataclass(frozen=True)
class Kept:
    """What a view of the next build takes over from the one before: the line the cursor was on,
    the routines open and where the tree was scrolled to."""

    current: Key | None
    opened: tuple[str, ...]
    scroll: int


@dataclass(frozen=True)
class Spot:
    """What a line is, for the gestures."""

    what: What
    ref: str
    """The id of a node or an arm; the name of a routine."""
    routine: str
    edit: str = ""
    """What the edits did to it: `edited`, `new` or empty."""
    op: str = ""
    """A node's opcode."""
    scope: str | None = None
    """The list that holds a node as a `Place` names it (a routine, an arm, or the block of a
    then-branch or body); None when the graph is not at hand."""
    at: int = 0
    size: int = 0
    """Its position in that list, and the list's length."""
    shape: Shape | None = None
    """A block's, or for an arm its block's."""
    arms: int = 0
    """The arms of a block."""


def ask_note(parent: QWidget, what: str, text: str) -> str | None:
    """The new text of a note (blank takes it away); None for Cancel."""
    got, ok = QInputDialog.getText(parent, "Note", f"A note on {what}:", text=text)
    return got if ok else None


class Code(QTreeView):
    """The script's tree. Return activates the current line on every platform, not only where the
    style does: on macOS Qt's own Return renames. With `editing` set, the context menu and the keys
    of the gestures work."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio, model: BrainModel) -> None:
        super().__init__()
        self.ws, self.studio, self.brain = ws, studio, model
        self.editing = False
        self.ask_note = ask_note
        self.setStyleSheet(ROW_QSS)
        self.setModel(model)
        self.setUniformRowHeights(True)
        self.setHeaderHidden(True)
        self.setTreePosition(TEXT)
        self.setIndentation(INDENT)
        self.setMouseTracking(True)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        head = self.header()
        head.setStretchLastSection(True)
        head.setSectionResizeMode(PC, QHeaderView.ResizeMode.Fixed)
        head.setSectionResizeMode(TEXT, QHeaderView.ResizeMode.Stretch)
        self.delegate = Lines(self)
        self.setItemDelegate(self.delegate)
        model.dead = self._dead
        for keys, fn in (
            ("Alt+Up", lambda: self._shift(-1)),
            ("Alt+Down", lambda: self._shift(1)),
            ("Delete", self._delete),
            ("Backspace", self._delete),  # the Mac's delete key
        ):
            go = QShortcut(QKeySequence(keys), self)
            go.setContext(Qt.ShortcutContext.WidgetShortcut)
            go.activated.connect(fn)

    # ---- where the cursor is ----------------------------------------------------------- #

    def row(self) -> int | None:
        ix = self.currentIndex()
        return int(ix.internalId()) if ix.isValid() else None

    def selected(self) -> list[int]:
        """The rows selected, in the view's order."""
        return sorted(int(ix.internalId()) for ix in self.selectionModel().selectedRows(TEXT))

    def picked(self) -> list[int]:
        """The rows selected, a folded one with everything under it."""
        v = self.brain.view
        if v is None:
            return []
        rows: set[int] = set()
        for r in self.selected():
            folded = v.children[r] and not self.isExpanded(self.brain.index_of(r))
            rows.update(v.span(r) if folded else (r,))
        return sorted(rows)

    def reveal(self, row: int) -> None:
        """Selects line `row`, opening its routine and folding the others, except those that hold
        a mark of Follow the game."""
        v, m = self.brain.view, self.brain
        if v is None:
            return
        top = v.top_of(row)
        kept = {top, *(v.top_of(r) for r in m.marks)}
        for r in v.top:
            if r not in kept and self.isExpanded(m.index_of(r)):
                self.collapse(m.index_of(r))
        for r in (*v.ancestors(row), top):
            self.expand(m.index_of(r))
        ix = m.index_of(row, TEXT)
        self.setCurrentIndex(ix)
        self.scrollTo(ix, QAbstractItemView.ScrollHint.PositionAtCenter)

    def keep(self) -> Kept:
        """What to take over to the view of the next build."""
        v = self.brain.view
        if v is None:
            return Kept(None, (), 0)
        here = self.row()
        opened = tuple(
            n for n, r in v.routine_row.items() if self.isExpanded(self.brain.index_of(r))
        )
        return Kept(
            None if here is None else key_of(v, here), opened, self.verticalScrollBar().value()
        )

    def restore(self, kept: Kept) -> None:
        """After `BrainModel.set_view` of the next build: the same routines open, the cursor on
        the same line (a routine's, when its line is gone) and the tree where it was."""
        v, m = self.brain.view, self.brain
        if v is None:
            return
        for name in kept.opened:
            if (row := v.routine_row.get(name)) is not None:
                self.expand(m.index_of(row))
        if (row := row_of(v, kept.current)) is not None:
            self.setCurrentIndex(m.index_of(row, TEXT))
        self.verticalScrollBar().setValue(kept.scroll)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.activated.emit(self.currentIndex())
            return
        super().keyPressEvent(event)

    def drawRow(  # noqa: N802
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        """A row of edited or new code under a tint, with a bar at the edge of the tree."""
        if kind := index.data(EDIT):
            band = QRect(0, option.rect.top(), self.viewport().width(), option.rect.height())
            ink = theme.edit(kind)
            painter.save()
            painter.setOpacity(TINT)
            painter.fillRect(band, ink)
            painter.setOpacity(1.0)
            painter.fillRect(QRect(0, band.top(), BAR, band.height()), ink)
            painter.restore()
        super().drawRow(painter, option, index)

    # ---- what a line is ---------------------------------------------------------------- #

    def _graph(self, routine: str) -> RoutineGraph | None:
        v = self.brain.view
        return None if v is None else self.ws.brain_routine_graph(v.row, routine, v.header)

    def _dead(self, routine: str) -> Collection[str]:
        rg = self._graph(routine)
        return () if rg is None else dead_ids(rg)

    def spot(self, row: int) -> Spot | None:
        """What line `row` is; None for a line of a list, a header or data."""
        v, m = self.brain.view, self.brain
        if v is None or (routine := v.routine_at(row)) is None:
            return None
        node, edit = v.node_at(row), m.edit_kind(row)
        if node is None:
            return (
                Spot("routine", routine, routine, edit) if v.routine_row[routine] == row else None
            )
        rg = self._graph(routine)
        if rg is None:
            return Spot("node", node, routine, edit)
        if node in rg.scopes:  # an arm: its block is the scope's owner
            owner = rg.nodes.get(rg.scopes[node].owner or "")
            shape = owner.op.shape if isinstance(owner, Stmt) else None
            return Spot("arm", node, routine, edit, shape=shape)
        stmt = rg.nodes.get(node)
        if not isinstance(stmt, Stmt):
            return Spot("node", node, routine, edit)
        scope = rg.scopes[stmt.scope]
        into = stmt.scope == head_key(scope.owner or "")
        ref = routine if stmt.scope == ROOT else (scope.owner or "") if into else stmt.scope
        arms = sum(1 for k, s in rg.scopes.items() if s.owner == node and k != head_key(node))
        at = scope.body.index(node) if node in scope.body else 0
        shape = stmt.op.shape if stmt.block else None
        return Spot(
            "node", node, routine, edit, stmt.op.name, ref, at, len(scope.body), shape, arms
        )

    # ---- gestures ---------------------------------------------------------------------- #

    def run(self, label: str, make: Callable[[], be.Gesture]) -> None:
        """The gesture `make` builds, as one undo step. A gesture the constructor refuses, or the
        workspace does, says why in the message and changes nothing."""
        try:
            g = make()
        except EditError as e:
            self.ws.message = f"{label}: {e}"
            self.studio.changed()
            return
        self.studio.act(g.label, lambda: self.ws.edit_brain(g))()

    def _single(self) -> Spot | None:
        """The line the cursor is on, when gestures are on and it is the only one selected."""
        v, row = self.brain.view, self.row()
        if not self.editing or v is None or row is None or not self.ws.brain_editable(v.row):
            return None
        return self.spot(row) if len(self.selected()) <= 1 else None

    def _shift(self, by: int) -> None:
        spot = self._single()
        if spot is not None and spot.what == "node":
            move = be.earlier if by < 0 else be.later
            self.run("Move up" if by < 0 else "Move down", lambda: move(spot.ref))

    def _delete(self) -> None:
        spot = self._single()
        if spot is not None and spot.what == "node":
            self.run("Delete", lambda: be.delete(spot.ref))

    def _outer(self) -> list[Spot]:
        """The outermost nodes selected, when nothing else is selected: what Wrap takes."""
        v = self.brain.view
        if v is None:
            return []
        rows = set(self.selected())
        out = []
        for r in sorted(rows):
            if rows.isdisjoint(v.ancestors(r)):
                spot = self.spot(r)
                if spot is None or spot.what != "node":
                    return []
                out.append(spot)
        return out

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802
        """The menu of the line under the pointer, or of the cursor's line for the Menu key."""
        key = event.reason() == QContextMenuEvent.Reason.Keyboard
        ix = (self.currentIndex() if key else self.indexAt(event.pos())).siblingAtColumn(TEXT)
        if not ix.isValid():
            return
        if not self.selectionModel().isSelected(ix):
            self.setCurrentIndex(ix)
        at = self.viewport().mapToGlobal(self.visualRect(ix).center()) if key else event.globalPos()
        if (menu := self.menu()) is not None:
            menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            menu.popup(at)

    def menu(self) -> QMenu | None:
        """The gestures on the line the cursor is on, or on the lines selected; None when there is
        no brain file to edit, or nothing to do there."""
        v, row = self.brain.view, self.row()
        if not self.editing or v is None or row is None or not self.ws.brain_editable(v.row):
            return None
        if len(self.selected()) > 1:
            spots = self._outer()
            if not spots:
                return None
            menu = self._menu()
            self._wrap(menu, spots)
            return menu
        spot = self.spot(row)
        if spot is None:
            return None
        menu = self._menu()
        {"node": self._node, "arm": self._arm, "routine": self._routine}[spot.what](menu, spot)
        return menu

    def _menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        return menu

    def _item(
        self, menu: QMenu, text: str, tip: str, fn: Callable[[], object], enabled: bool = True
    ) -> None:
        act = menu.addAction(text)
        act.setToolTip(tip)
        act.setEnabled(enabled)
        act.triggered.connect(lambda _=False: fn())

    def _add(
        self,
        menu: QMenu,
        text: str,
        make: Callable[[], be.Gesture],
        tip: str,
        enabled: bool = True,
    ) -> None:
        self._item(menu, text, tip, lambda: self.run(text, make), enabled)

    def _palette(
        self,
        menu: QMenu,
        title: str,
        pick: Callable[[be.PaletteEntry], be.Gesture],
        keep: Callable[[be.PaletteEntry], bool] = lambda e: True,
    ) -> QMenu:
        """A submenu of the palette: a submenu a group, a long one split by theme."""
        sub = self._sub(menu, title)
        groups = {g: got for g, es in be.PALETTE.items() if (got := [e for e in es if keep(e)])}
        sub.setEnabled(bool(groups))
        for group, got in groups.items():
            into = self._sub(sub, group)
            long = len(got) > TIER
            for theme_ in dict.fromkeys(e.theme for e in got) if long else [""]:
                at = self._sub(into, theme_) if long else into
                for e in (x for x in got if x.theme == theme_ or not long):
                    self._add(at, e.title, partial(pick, e), e.meaning)
        return sub

    def _sub(self, menu: QMenu, title: str) -> QMenu:
        sub = menu.addMenu(title)
        sub.setToolTipsVisible(True)
        return sub

    def _insert(self, menu: QMenu, title: str, place: be.Place) -> None:
        pairs = self.ws.brain_pairs()
        self._palette(menu, title, lambda e: be.insert_entry(place, e, pairs))

    def _node(self, menu: QMenu, spot: Spot) -> None:
        ref = spot.ref
        self._insert(menu, "Insert before", be.Place.before(ref))
        self._insert(menu, "Insert after", be.Place.after(ref))
        if spot.scope:
            self._insert(menu, "Insert at end of scope", be.Place.end(spot.scope))
        if spot.shape in (Shape.IF, Shape.REPEAT):
            self._insert(menu, "Insert inside", be.Place.end(ref))
        menu.addSeparator()
        self._add(
            menu, "Delete", lambda: be.delete(ref), "Takes the line out, with all in it (Del)"
        )

        def alike(e: be.PaletteEntry) -> bool:
            op = e.entry.op
            return op.name != spot.op and (
                op.shape is spot.shape if spot.shape is not None else not op.coded
            )

        self._palette(menu, "Replace with", lambda e: be.replace(ref, e.op), alike)
        known = spot.scope is not None
        for text, tip, make, stop in (
            ("Move up", "Swaps it with the line above it (Alt+Up)", be.earlier, spot.at == 0),
            (
                "Move down",
                "Swaps it with the line below it (Alt+Down)",
                be.later,
                spot.at >= spot.size - 1,
            ),
        ):
            self._add(menu, text, partial(make, ref), tip, not (known and stop))
        self._wrap(menu, [spot])
        if spot.shape is Shape.IF and not spot.arms:
            self._add(menu, "Add else", lambda: be.add_else(ref), "Gives the if an else branch")
        elif spot.shape in (Shape.SWITCH, Shape.RANDOM, Shape.BUCKET, Shape.ZONE):
            self._add(menu, "Add arm", lambda: be.add_arm(ref), "Adds a branch to the block")
        self._words(menu, spot)

    def _wrap(self, menu: QMenu, spots: Sequence[Spot]) -> None:
        refs = [s.ref for s in spots]
        sub = self._palette(
            menu,
            "Wrap in",
            lambda e: be.wrap_if(refs, e.op),
            lambda e: e.group == "Conditions" and e.entry.op.shape is Shape.IF,
        )
        self._add(sub, "Pick one", lambda: be.wrap_pick(refs), "Two branches of an even draw")

    def _arm(self, menu: QMenu, spot: Spot) -> None:
        ref = spot.ref
        self._insert(menu, "Insert at start of arm", be.Place.start(ref))
        self._insert(menu, "Insert at end of arm", be.Place.end(ref))
        menu.addSeparator()
        if spot.shape is Shape.IF:
            self._add(menu, "Remove else", lambda: be.remove_else(ref), "Takes the else out")
        else:
            self._add(menu, "Remove arm", lambda: be.remove_arm(ref), "Takes the branch out")
        self._words(menu, spot)

    def _routine(self, menu: QMenu, spot: Spot) -> None:
        ref = spot.ref
        self._insert(menu, "Insert at start of routine", be.Place.start(ref))
        self._insert(menu, "Insert at end of routine", be.Place.end(ref))
        self._words(menu, spot)

    def _words(self, menu: QMenu, spot: Spot) -> None:
        """Back to the game's, for the line and for its routine; a note."""
        v, ref = self.brain.view, spot.ref
        menu.addSeparator()
        if v is None:
            return
        if spot.what != "routine" and spot.edit == "edited":
            self._add(
                menu,
                f"{be.BACK} {spot.what}",
                lambda: be.back_to_game(ref),
                "Drops the edits to this line, and the code and moves hanging on it",
            )
        mine = spot.routine
        kind = self.brain.edit_kind(v.routine_row[mine])
        if kind == "new":
            self._add(
                menu,
                "Remove this new routine",
                lambda: be.back_to_game(mine),
                "Takes the routine out, with all in it",
            )
        elif kind:
            self._add(
                menu,
                f"{be.BACK} routine",
                lambda: be.back_to_game(mine),
                "Drops all the edits to this routine, with the code and moves in it",
            )
        self._item(
            menu, "Note\u2026", "A note on this line, shown after it", lambda: self._note(spot)
        )

    def _note(self, spot: Spot) -> None:
        """Asks for the note; blank takes it away, Cancel leaves it as it was."""
        text = self.brain.notes.get(spot.ref, "")
        got = self.ask_note(self, spot.ref, text)
        if got is not None:
            self.run("Note", lambda: be.set_note(spot.ref, got))
