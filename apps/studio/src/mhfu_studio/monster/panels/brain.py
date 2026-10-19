# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Brain tab: the AI script a species runs (`monster/brain.py`) as code, read only.

The script's lines are a tree beside an outline of its lists, with a find, a history and links
from a call to the routine it leads to. The model walks the view as it is and one delegate draws
the lines, so a script of ten thousand lines opens as fast as one of a thousand. Follow the game
marks where the running monster's script stands (`monster/brain_game.py`) in the first column.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QPersistentModelIndex,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QFontMetricsF,
    QGuiApplication,
    QHideEvent,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QShortcut,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QHeaderView,
    QLineEdit,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mhfu_studio.monster import brain_game, species
from mhfu_studio.monster.brain import BrainView, History, Outline
from mhfu_studio.monster.brain_game import Mark, Marks
from mhfu_studio.ui import kit, theme

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

#: how often a panel still waiting for the brain worker asks it
POLL_MS = 150
#: the model's columns: the address of a line, and the line, which the tree is drawn in
PC, TEXT = 0, 1
#: item roles: a line's kind, whether it leads to a routine, what an `enter` line plays, and
#: Follow the game's marks on it
KIND = Qt.ItemDataRole.UserRole
LINK = Qt.ItemDataRole.UserRole + 1
LABEL = Qt.ItemDataRole.UserRole + 2
MARKS = Qt.ItemDataRole.UserRole + 3
PAD = 4
INDENT = 16
#: a selected row is one band: the branches' own fill would be a second layer over the gutter
ROW_QSS = "QTreeView { show-decoration-selected: 0; }"
LISTS_W = 220
ARROW = "  \u2192 "
BACK = "  \u2190 "
PICK = "Pick a monster"
NO_GAME = "No game files"
SETUP = "Choose your MHFU extraction under Setup on the start page."
NO_INDEX = QModelIndex()


def tag_width(word: str) -> int:
    """The width a tag of `word` takes, with the space round it."""
    font = theme.mono()
    font.setBold(True)
    return round(QFontMetricsF(font).horizontalAdvance(word)) + 3 * PAD


def address(pc: int | None) -> str:
    """A line's address as the column writes it; empty for a line that has none."""
    return "" if pc is None else f"0x{pc:08X}"


class BrainModel(QAbstractItemModel):
    """A `BrainView` as a tree of its lines: an index's id is its line's row in the view."""

    def __init__(self) -> None:
        super().__init__()
        self.view: BrainView | None = None
        self.marks: Marks = {}
        #: a row's place among its siblings
        self._at: list[int] = []

    def set_view(self, view: BrainView | None) -> None:
        self.beginResetModel()
        self.view = view
        self.marks = {}
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
        return None


class _Lines(QStyledItemDelegate):
    """A line: its text in the colour of its kind, underlined when it leads to a routine, and
    after it, quiet, what an `enter` plays and what Follow the game says of it. The tree indents,
    so the text's own indent goes. The first column holds the address when `addresses` is set, and
    the tag of the line's first mark."""

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
            body = text.lstrip()
            parts = [(body, theme.code(index.data(KIND)), bool(index.data(LINK)))]
            if label := index.data(LABEL):
                parts.append((ARROW + label, theme.code("label"), False))
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


class _Code(QTreeView):
    """The script's tree. Return activates the current line on every platform, not only where the
    style does: on macOS Qt's own Return renames."""

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.activated.emit(self.currentIndex())
            return
        super().keyPressEvent(event)


class _Find(QLineEdit):
    """The find field: Return steps to the next match, Shift+Return to the one before."""

    step = Signal(int)

    def __init__(self, tip: str) -> None:
        super().__init__()
        self.setToolTip(tip)
        self.setPlaceholderText("Find a line or an address")
        self.setClearButtonEnabled(True)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            back = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.step.emit(-1 if back else 1)
            return
        super().keyPressEvent(event)


class Lists(QTreeWidget):
    """The header's lists, each with its slots; `jumped` carries the row of a routine picked."""

    jumped = Signal(int)
    ROW = Qt.ItemDataRole.UserRole

    def __init__(self) -> None:
        super().__init__()
        self.setToolTip(
            "The lists the header names, with what each slot runs: pick a routine to jump to it."
            " Greyed: an empty slot, or data."
        )
        self.setHeaderHidden(True)
        self.itemClicked.connect(self._picked)
        self.itemActivated.connect(self._picked)

    def show_lists(self, outline: Sequence[Outline]) -> None:
        self.clear()
        for o in outline:
            top = QTreeWidgetItem([f"{o.name} ({len(o.items)})"])
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.addTopLevelItem(top)
            for it in o.items:
                slot = QTreeWidgetItem([f"{it.index} {it.name or '-'}"])
                if it.row is None:
                    slot.setFlags(Qt.ItemFlag.NoItemFlags)
                else:
                    slot.setData(0, self.ROW, it.row)
                top.addChild(slot)
        self.expandAll()

    def _picked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        row = item.data(0, self.ROW)
        if isinstance(row, int):
            self.jumped.emit(row)


class BrainPanel(kit.Panel):
    """The tab: a bar (species, variant, find, history, copy), the lists and the script."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__(scroll=False)
        self.ws, self.studio = ws, studio
        #: whether `sync` has read the species yet, and the species shown
        self._loaded = False
        self._species: int | None = None
        #: the header the variant choice picked; None: the one most map types use
        self._header: str | None = None
        self._view: BrainView | None = None
        self.history = History()
        #: the rows the find text matches, and whether no Return has stepped through them yet
        self._matches: list[int] = []
        self._fresh = True
        self._opening = False
        #: the row Follow the game last took the view to
        self._seen: int | None = None

        self.species = kit.choice(
            [],
            tip="Whose AI script to read: any monster the game's overlays serve a script for",
            on=self._pick_species,
        )
        self.species.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.variant = kit.choice(
            [],
            tip="Which version of the script to read: each runs on the map types it names",
            on=self._pick_variant,
        )
        self.query = _Find(
            "Finds a line holding the text, or the line at an address (0x...). Return goes to the"
            " next match, Shift+Return to the one before."
        )
        self.query.textChanged.connect(self._rematch)
        self.query.step.connect(self._find)
        self.count = kit.label(role="muted", wrap=False)
        self.back = kit.icon_button(
            "ph.arrow-left",
            tip="Back to the line you came from (Alt+Left)",
            on=lambda: self._step(True),
        )
        self.forward = kit.icon_button(
            "ph.arrow-right",
            tip="Forward again, after going back (Alt+Right)",
            on=lambda: self._step(False),
        )
        self.copy = kit.button(
            "Copy",
            tip="Copies the lines selected; with one selected, the whole routine it is in",
            on=self._copy,
            icon="ph.copy",
        )
        self.following = kit.check(
            "Follow the game",
            tip="Marks where the running monster's script stands: NEXT is the line it resumes at,"
            " PLAYING the move it was last asked for, PARKED where its other contexts wait. Read"
            " once a second while this tab is open; going anywhere yourself ends it",
            on=lambda on: studio.act("follow the game", lambda: self._following(on))(),
            checked=ws.follow.on,
        )
        self.live = kit.Elided("", None, role="muted")
        self.live.setVisible(False)
        bar = kit.row(
            self.species,
            self.variant,
            self.query,
            self.count,
            self.back,
            self.forward,
            self.copy,
            self.following,
            grow=self.query,
        )

        self.model = BrainModel()
        self.tree = _Code()
        self.tree.setToolTip(
            "The AI script as code. Double-click or Return on an underlined call, goto or walk"
            " to follow it. Routines start folded."
        )
        self.tree.setStyleSheet(ROW_QSS)
        self.tree.setModel(self.model)
        self.delegate = _Lines(self.tree)
        self.tree.setItemDelegate(self.delegate)
        self.tree.setUniformRowHeights(True)
        self.tree.setHeaderHidden(True)
        self.tree.setTreePosition(TEXT)
        self.tree.setIndentation(INDENT)
        self.tree.setMouseTracking(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        head = self.tree.header()
        head.setStretchLastSection(True)
        head.setSectionResizeMode(PC, QHeaderView.ResizeMode.Fixed)
        head.setSectionResizeMode(TEXT, QHeaderView.ResizeMode.Stretch)
        self.tree.expanded.connect(self._opened)
        self.tree.activated.connect(lambda i: self._follow(i.internalId()))
        self.tree.entered.connect(self._hover)
        self.tree.viewportEntered.connect(lambda: self.tree.viewport().unsetCursor())
        selection = self.tree.selectionModel()
        selection.currentChanged.connect(lambda *_: self._moved())
        self.lists = Lists()
        self.lists.jumped.connect(self.goto)
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.lists)
        self.split.addWidget(self.tree)
        self.split.setSizes([LISTS_W, 1000])
        self.split.setStretchFactor(1, 1)
        self.note = kit.Empty("", "")
        self.main = kit.Pages(self.split, self.note)

        self.addresses = kit.check(
            "Show addresses",
            tip="A column with the address in the game's memory that each line stands for",
            on=self._addresses,
        )
        self.more = kit.More(tip="Controls you will rarely need")
        self.more.body.addWidget(self.addresses)
        self._gutter()

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(bar)
        lay.addWidget(self.live)
        lay.addWidget(self.main, 1)
        lay.addWidget(self.more)
        self.gate = kit.Empty(NO_GAME, "")
        self.pages = kit.Pages(page, self.gate)
        self.body.addWidget(self.pages)

        for keys, fn in (
            ("Alt+Left", lambda: self._step(True)),
            ("Alt+Right", lambda: self._step(False)),
        ):
            go = QShortcut(QKeySequence(keys), self)
            go.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            go.activated.connect(fn)
        look = QShortcut(QKeySequence.StandardKey.Find, self)
        look.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        look.activated.connect(self._look)
        lines = QShortcut(QKeySequence.StandardKey.Copy, self.tree)
        lines.setContext(Qt.ShortcutContext.WidgetShortcut)
        lines.activated.connect(self._copy_lines)

        self.poll = QTimer(self)
        self.poll.setInterval(POLL_MS)
        self.poll.timeout.connect(studio.guard("brain panel", self._pull))
        self.ticker = QTimer(self)
        self.ticker.setInterval(round(brain_game.PERIOD * 1000))
        self.ticker.timeout.connect(studio.guard("follow the game", self._tick))
        self._ready(False)

    # ---- what is shown ----------------------------------------------------------------- #

    def sync(self) -> None:
        """Only notices another species; everything slower waits for `_pull`'s timer."""
        sp = self.ws.brain_species
        if not self._loaded or sp != self._species:
            self._load(sp)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self.sync()
        self._pull()  # the game may have been set up since, and a decode may be waiting
        self._arm()
        self._tick()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        super().hideEvent(event)
        self.poll.stop()
        self._arm()

    def _load(self, sp: int | None) -> None:
        self._loaded, self._species, self._header = True, sp, None
        self.ws.follow.reset()
        self._clear()
        self._pull()

    def _clear(self) -> None:
        self._view = None
        self._seen = None
        self.model.set_view(None)
        self.history.clear()
        self.lists.show_lists(())
        self._matches = []
        self._ready(False)

    def _pull(self) -> None:
        """Asks the workspace for what the panel waits for, and shows it; the timer runs until
        it is all there (a decode takes seconds, the scan of the species a few more)."""
        ws, sp = self.ws, self._species
        view = None if sp is None else ws.brain(sp, self._header)
        rows = ws.brain_rows()  # after the decode: the worker takes them in order
        waiting = rows is None and not ws.brain_note
        self._fill_species(rows)
        if ws.brain_note:
            if self._view is not None:
                self._clear()
            self.gate.say(NO_GAME, ws.brain_note if "Setup" in ws.brain_note else SETUP)
            self.pages.show_page(False)
        else:
            self.pages.show_page(True)
            if sp is None:
                self._say("No monster to read", "Pick a monster in the list above.")
            elif view is not None:
                if view is not self._view:
                    self._show(view)
            elif err := ws.brain_error(sp):
                if self._view is not None:
                    self._clear()
                self._say("No AI script to show", err)
            else:
                waiting = True
                if self._view is None:  # a header on its way keeps the one shown
                    self._say(f"Reading {species.label(sp)}'s AI script…", "")
        if waiting and self.isVisible():
            self.poll.start()
        else:
            self.poll.stop()

    def _say(self, title: str, hint: str) -> None:
        self.note.say(title, hint)
        self.main.show_page(False)

    def _show(self, view: BrainView) -> None:
        self._view = view
        self.model.set_view(view)
        self.history.clear()
        self.lists.show_lists(view.outline)
        heads = self.ws.brain_headers(view.row)
        kit.refill(self.variant, [(h.header, h.words) for h in heads], view.header)
        self.variant.setVisible(len(heads) > 1)
        self._gutter()
        self.main.show_page(True)
        self._ready(True)
        self._rematch()
        self._seen = None
        self.ws.place_follow(view)
        self._marks()

    def _ready(self, on: bool) -> None:
        """The controls that work on a script, enabled once there is one."""
        for w in (self.query, self.copy):
            w.setEnabled(on)
        self.count.setVisible(on)
        if not on:
            self.variant.setVisible(False)
        self._arrows()

    def _fill_species(self, rows: dict[int, int] | None) -> None:
        sp = self._species
        shown = sorted({*(rows or ()), *(() if sp is None else (sp,))})
        items = [("", PICK)] if sp is None else []
        items += [(str(r), species.label(r)) for r in shown]
        kit.refill(self.species, items, "" if sp is None else str(sp))

    def _pick_species(self, cid: str) -> None:
        if cid:
            self._leave()
            self.ws.browse_brain(int(cid))
            self.sync()

    def _pick_variant(self, cid: str) -> None:
        self._leave()
        self._switch(cid)

    def _switch(self, header: str) -> None:
        """Shows the script as `header` runs it."""
        if self._view is not None and header != self._view.header:
            self._header = header
            self._pull()

    # ---- getting around ---------------------------------------------------------------- #

    def _cursor(self) -> int | None:
        ix = self.tree.currentIndex()
        return int(ix.internalId()) if ix.isValid() else None

    def goto(self, row: int) -> None:
        """Selects line `row`, folding the other routines; Back leads to where the cursor was."""
        self._leave()
        self._mark()
        self.history.go(row)
        self._reveal(row)
        self._arrows()

    def _mark(self) -> None:
        """Puts the cursor in the history, when it got where it is by no step of the history."""
        here = self._cursor()
        if here is not None and here != self.history.current:
            self.history.go(here)

    def _reveal(self, row: int) -> None:
        v = self._view
        if v is None:
            return
        top, m, t = v.top_of(row), self.model, self.tree
        kept = {top, *(v.top_of(r) for r in m.marks)}  # a mark stays in sight
        for r in v.top:
            if r not in kept and t.isExpanded(m.index_of(r)):
                t.collapse(m.index_of(r))
        for r in (*v.ancestors(row), top):
            t.expand(m.index_of(r))
        ix = m.index_of(row, TEXT)
        t.setCurrentIndex(ix)
        t.scrollTo(ix, QAbstractItemView.ScrollHint.PositionAtCenter)

    def _step(self, back: bool) -> None:
        if back:
            self._mark()
        row = self.history.back() if back else self.history.forward()
        if row is not None:
            self._leave()
            self._reveal(row)
        self._arrows()

    def _arrows(self) -> None:
        self.back.setEnabled(self.history.can_back)
        self.forward.setEnabled(self.history.can_forward)

    def _follow(self, row: int) -> None:
        """A call, goto or walk line goes to the routine it leads to."""
        v = self._view
        if v is not None and (to := v.target_row(row)) is not None:
            self.goto(to)

    def _opened(self, index: QModelIndex) -> None:
        """A routine opened shows all it holds, nested blocks too."""
        if self._opening or index.parent().isValid():
            return
        self._opening = True
        try:
            self.tree.expandRecursively(index)
        finally:
            self._opening = False

    def _hover(self, index: QModelIndex) -> None:
        link = bool(index.data(LINK))
        self.tree.viewport().setCursor(
            Qt.CursorShape.PointingHandCursor if link else Qt.CursorShape.ArrowCursor
        )

    # ---- find -------------------------------------------------------------------------- #

    def _rematch(self) -> None:
        v = self._view
        self._matches = [] if v is None else v.find(self.query.text())
        self._fresh = True
        self._count()

    def _moved(self) -> None:
        """The cursor went somewhere: find goes on from there."""
        self._fresh = False
        self._count()

    def _count(self) -> None:
        n, here = len(self._matches), self._cursor()
        if not self.query.text().strip():
            text = ""
        elif not n:
            text = "No match"
        elif here in self._matches:
            text = f"{self._matches.index(here) + 1} of {n}"
        else:
            text = f"{n} match{'' if n == 1 else 'es'}"
        self.count.setText(text)

    def _find(self, step: int) -> None:
        """The next match after the cursor, or the one before it; round the ends. The first
        Return after typing may land on the line the cursor is on."""
        found = self._matches
        if not found:
            return
        here = self._cursor()
        at = -1 if here is None else here
        if step > 0:
            row = next((r for r in found if r > at or (self._fresh and r == at)), found[0])
        else:
            row = next((r for r in reversed(found) if r < at), found[-1])
        self._fresh = False
        self.goto(row)

    def _look(self) -> None:
        self.query.setFocus()
        self.query.selectAll()

    # ---- copying and addresses --------------------------------------------------------- #

    def _picked(self) -> list[int]:
        """The rows selected, a folded one with everything under it."""
        v = self._view
        if v is None:
            return []
        rows: set[int] = set()
        for ix in self.tree.selectionModel().selectedRows(TEXT):
            r = int(ix.internalId())
            folded = v.children[r] and not self.tree.isExpanded(self.model.index_of(r))
            rows.update(v.span(r) if folded else (r,))
        return sorted(rows)

    def _put(self, rows: Sequence[int]) -> None:
        v = self._view
        if v is None or not rows:
            self.studio.message = "nothing to copy: pick a line first"
            return
        QGuiApplication.clipboard().setText("\n".join(v.lines[r].text for r in rows))
        self.studio.message = f"copied {len(rows)} line{'' if len(rows) == 1 else 's'}"

    def _copy(self) -> None:
        """The lines selected; with one selected, or none, the routine the cursor is in."""
        v, rows, here = self._view, self._picked(), self._cursor()
        if v is not None and len(rows) < 2:
            row = rows[0] if rows else here
            rows = [] if row is None else list(v.span(v.top_of(row)))
        self._put(rows)

    def _copy_lines(self) -> None:
        """The standard Copy key: the lines selected, as in any text view."""
        self._put(self._picked())

    def _addresses(self, on: bool) -> None:
        self._gutter()

    # ---- follow the game --------------------------------------------------------------- #

    def _gutter(self) -> None:
        """The first column: the addresses when asked for, and the tags while Follow is on."""
        addresses = self.addresses.isChecked()
        head = self.tree.header()
        self.delegate.addresses = addresses
        self.tree.setColumnHidden(PC, not (addresses or self.ws.follow.on))
        self.tree.viewport().update()
        if addresses:
            width = QFontMetricsF(theme.mono()).horizontalAdvance(address(0)) + 6 * PAD
        else:
            width = tag_width(max(brain_game.KINDS, key=len).upper()) + PAD
        head.resizeSection(PC, round(width))

    def _following(self, on: bool) -> None:
        self.ws.follow.set(on)
        self._seen = None
        self._gutter()
        self._arm()
        self._tick()
        self._marks()

    def _leave(self) -> None:
        """The user went somewhere: the view stays where they put it."""
        if self.ws.follow.on:
            self.following.setChecked(False)  # its slot ends Follow

    def _arm(self) -> None:
        """Polls once a second while Follow is on and this tab is shown, else not at all."""
        if self.ws.follow.on and self.isVisible():
            if not self.ticker.isActive():
                self.ticker.start()
        else:
            self.ticker.stop()

    def _tick(self) -> None:
        if self.ws.poll_follow(self._view):
            self._marks()

    def _marks(self) -> None:
        """Shows what Follow says: its status and marks, and goes where the monster is."""
        f, v = self.ws.follow, self._view
        self.model.set_marks(f.marks if f.on and v is not None else {})
        if v is not None:
            for row in self.model.marks:  # a routine holding a mark is open
                self.tree.expand(self.model.index_of(v.top_of(row)))
        self.live.setVisible(f.on)
        self.live.setText(f.status)
        self.live.setToolTip(f.status)
        self.live.set_level(None if f.next_row is not None else "warning")
        if not f.on or v is None:
            return
        if f.header is not None and f.header != v.header:
            self._switch(f.header)
        elif f.next_row is not None and f.next_row != self._seen:
            self._seen = f.next_row
            self._reveal(f.next_row)
