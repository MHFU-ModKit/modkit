# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Brain tab: the AI script a species runs (`monster/brain.py`) as code and as a graph.

With a species' brain file open (`ws.brain_editable`) it is the editor: an outline of the script's
routines by meaning, a Graph | Code pair linked by node, the gestures of `brain_edit` in the code's
context menu (`brain_code.py`), and Count, Send and Push in a bar. The graph pane and the operand
bar are other widgets, put in `graph_slot` and `bar_slot` with `plug`. With a port open it only
reads: the script's lines beside an outline of its lists, with a find, a history and links from a
call to the routine it leads to. Follow the game marks where the running monster's script stands
(`monster/brain_game.py`) in the first column.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal

from mhfu.em.script import catalogue as cat
from mhfu.em.script.graph import ROOT, edited_ids
from PySide6.QtCore import (
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QCursor,
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
    QApplication,
    QComboBox,
    QHeaderView,
    QLineEdit,
    QMenu,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mhfu_studio.monster import brain, brain_game, brain_graph, species
from mhfu_studio.monster.panels import brain_names
from mhfu_studio.monster.panels.brain_code import (
    LINK,
    PAD,
    PC,
    BrainModel,
    Code,
    address,
    key_of,
    row_of,
    tag_width,
)
from mhfu_studio.monster.panels.brain_graph import GraphPane
from mhfu_studio.monster.panels.move_preview import MovePopup
from mhfu_studio.monster.panels.operand_bar import OperandBar
from mhfu_studio.monster.workspace import budget_text
from mhfu_studio.shell.findings import LEVELS, Level
from mhfu_studio.ui import kit, theme

if TYPE_CHECKING:
    from mhfu.em.script.budget import Budget
    from mhfu.em.script.build import Built

    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

#: how often a panel still waiting for the brain worker asks it
POLL_MS = 150
#: how many polls wait for a build's budget before it is given up on
BUDGET_TRIES = 40
LISTS_W = 220
PICK = "Pick a monster"
NO_GAME = "No game files"
SETUP = "Choose your MHFU extraction under Setup on the start page."
BUILDING = "building\u2026"
YOURS = "runs your edits"
COUNT_TIP = (
    "Counts how often each line of the followed monster's script runs, for 30 emulated seconds:"
    " the count shows after each line and on the graph. Follow waits meanwhile"
)
SEND_TIP = "Puts the script on the memory stick as a mod: the next quest runs it. No save needed"
PUSH_TIP = (
    "Writes the script into the running game, read back: its monsters run it until the next quest"
    " load"
)
#: an outline row: the routine's `OutlineRow`, its group's name
ROW = Qt.ItemDataRole.UserRole
GROUP = Qt.ItemDataRole.UserRole + 1
DOT = 6.0
FROM = "\u2190 "


def details(r: brain_graph.OutlineRow) -> list[tuple[str, Literal["muted"] | Level]]:
    """The second line of an outline row, as (words, what colours them): `muted`, or a finding's
    level."""
    out: list[tuple[str, Literal["muted"] | Level]] = []
    if r.entry:
        weak = r.confidence is not None and r.confidence < cat.Confidence.HIGH
        out.append(
            (f"{r.entry} ({r.confidence.label})" if weak and r.confidence else r.entry, "muted")
        )
    if r.hint:
        out.append((r.hint, "muted"))
    if r.shared:
        out.append(("shared", "muted"))
    out.append((f"{r.nodes} node{'' if r.nodes == 1 else 's'}", "muted"))
    if r.findings:
        level: Level = next((lv for lv in LEVELS if lv == r.level), "info")
        out.append((f"{r.findings} finding{'' if r.findings == 1 else 's'}", level))
    return out


def tip_of(r: brain_graph.OutlineRow) -> str:
    """What the outline says of a routine when the pointer rests on it."""
    lines = [r.text]
    if r.hint:
        lines.append(f"What it does: {r.hint}")
    if r.entry:
        sure = r.confidence.label if r.confidence else "unknown"
        lines.append(f"The game starts it as: {r.entry} (confidence {sure})")
    if r.where:
        lines.append(f"Named by the list slots: {', '.join(r.where)}")
    if r.shared:
        lines.append("Several map types run it: an edit here changes all of them")
    if r.new:
        lines.append("A routine of your own")
    lines.append("F2 or the context menu names it")
    return "\n".join(lines)


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

    def show_lists(self, outline: Sequence[brain.Outline]) -> None:
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


class _Rows(QStyledItemDelegate):
    """An outline row: a dot when the routine is edited, its name, and under it quiet words about
    it (`details`) and the NEXT tag when Follow the game is in it."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.next: str | None = None

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        row = index.data(ROW)
        if not isinstance(row, brain_graph.OutlineRow) or index.column() != 0:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        widget = option.widget
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        chosen = bool(opt.state & QStyle.StateFlag.State_Selected)
        room = QRectF(opt.rect).adjusted(PAD, 2, -PAD, -2)
        mono, quiet = theme.mono(), option.font
        quiet.setPointSizeF(max(quiet.pointSizeF() - 1, 6.0))
        high = option.palette.highlightedText().color()
        painter.save()
        painter.setClipRect(room)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        top = QRectF(room.left(), room.top(), room.width(), QFontMetricsF(mono).height())
        if row.edited or row.new:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.edit("new" if row.new else "edited"))
            painter.drawEllipse(QRectF(top.left(), top.center().y() - DOT / 2, DOT, DOT))
            top.setLeft(top.left() + DOT + PAD)
        painter.setFont(mono)
        painter.setPen(high if chosen else theme.text())
        painter.drawText(top, int(side), row.text)
        line = QRectF(room.left(), top.bottom() + 1, room.width(), room.bottom() - top.bottom())
        painter.setFont(quiet)
        for k, (words, how) in enumerate(details(row)):
            words = words if k == 0 else f" \u00b7 {words}"
            ink = theme.text(muted=True) if how == "muted" else theme.level(how)
            painter.setPen(high if chosen else ink)
            painter.drawText(line, int(side), words)
            line.setLeft(line.left() + QFontMetricsF(quiet).horizontalAdvance(words))
        if row.routine == self.next:
            self._tag(painter, line, quiet)
        painter.restore()

    @staticmethod
    def _tag(painter: QPainter, line: QRectF, font: object) -> None:
        """The NEXT tag, filled in the mark's colour."""
        word = brain_game.NEXT.upper()
        pill = QRectF(line.left() + PAD, line.top(), tag_width(word) - PAD, line.height())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.mark(brain_game.NEXT))
        painter.drawRoundedRect(pill, 3, 3)
        painter.setPen(theme.mark("ink"))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, word)

    def sizeHint(  # noqa: N802
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        if not isinstance(index.data(ROW), brain_graph.OutlineRow):
            return super().sizeHint(option, index)
        mono = round(QFontMetricsF(theme.mono()).height())
        return QSize(0, mono + round(QFontMetricsF(option.font).height()) + 8)


class Routines(QTreeWidget):
    """The script's routines grouped by meaning (`brain_graph.Outline`): each row says what it is
    (`details`), and its "reached from" column lists the routines that lead to it. `picked` carries
    the routine of a row clicked, `reached` a caller picked in that list as (caller, routine)."""

    picked = Signal(str)
    reached = Signal(str, str)
    renamed = Signal(str)
    """The user asked to name a routine."""

    def __init__(self) -> None:
        super().__init__()
        self.setToolTip(
            "The routines this script runs, grouped by what starts them: pick one to show it."
            " A dot marks one you changed; the arrow column lists the routines that lead to it."
            " F2 or the context menu names one."
        )
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.names: dict[str, str] = {}
        self.items: dict[str, QTreeWidgetItem] = {}
        self.setColumnCount(2)
        self.setHeaderHidden(True)
        self.setIndentation(12)
        self.rows = _Rows(self)
        self.setItemDelegate(self.rows)
        head = self.header()
        head.setStretchLastSection(False)
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.itemClicked.connect(self._clicked)
        self.itemActivated.connect(self._activated)

    def routine(self) -> str | None:
        """The routine of the row picked."""
        item = self.currentItem()
        row = None if item is None else item.data(0, ROW)
        return row.routine if isinstance(row, brain_graph.OutlineRow) else None

    def show_outline(self, o: brain_graph.Outline | None, names: Mapping[str, str]) -> None:
        """Shows `o`, keeping the groups shut, the row picked and the scroll of the one before."""
        here, scroll = self.routine(), self.verticalScrollBar().value()
        shut: set[str] = set()
        for i in range(self.topLevelItemCount()):
            if (group := self.topLevelItem(i)) is not None and not group.isExpanded():
                shut.add(group.data(0, GROUP))
        self.names = dict(names)
        self.items.clear()
        self.blockSignals(True)
        self.clear()
        for g in () if o is None else o.groups:
            top = QTreeWidgetItem([f"{g.name} ({len(g.rows)})", ""])
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            top.setData(0, GROUP, g.name)
            top.setFirstColumnSpanned(True)
            font = top.font(0)
            font.setBold(True)
            top.setFont(0, font)
            top.setToolTip(0, "\n".join(f"{x.title}: {x.meaning}" for x in g.lists))
            self.addTopLevelItem(top)
            for r in g.rows:
                item = QTreeWidgetItem(
                    [r.text, f"{FROM}{r.reached_from}" if r.reached_from else ""]
                )
                item.setData(0, ROW, r)
                item.setToolTip(0, tip_of(r))
                if r.reached_from:
                    item.setToolTip(
                        1, f"Reached from {r.reached_from} routines: click to list them"
                    )
                item.setTextAlignment(
                    1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                top.addChild(item)
                self.items.setdefault(r.routine, item)
            top.setExpanded(g.name not in shut)
        self.blockSignals(False)
        self.select(here)
        self.verticalScrollBar().setValue(scroll)

    def select(self, routine: str | None) -> None:
        """Makes the row of `routine` the one picked, quietly."""
        item = None if routine is None else self.items.get(routine)
        self.blockSignals(True)
        try:
            if item is None:
                self.setCurrentIndex(QModelIndex())
            else:
                self.setCurrentItem(item)
                self.scrollToItem(item)
        finally:
            self.blockSignals(False)

    def mark(self, routine: str | None) -> None:
        """Puts the NEXT tag of Follow the game on a routine's row."""
        if routine != self.rows.next:
            self.rows.next = routine
            self.viewport().update()

    def reach_menu(self, row: brain_graph.OutlineRow) -> QMenu:
        """What leads to `row`'s routine, one entry a caller."""
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        for c in row.callers:
            sites = f"{c.sites} call{'' if c.sites == 1 else 's'}"
            act = menu.addAction(f"{brain_graph.named(c.routine, self.names)} ({sites})")
            act.setToolTip(f"Shows the first of the {sites} in {c.routine}")
            act.triggered.connect(
                lambda _=False, who=c.routine: self.reached.emit(who, row.routine)
            )
        return menu

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_F2 and (routine := self.routine()) is not None:
            self.renamed.emit(routine)
            return
        super().keyPressEvent(event)

    def _menu(self, at: QPoint) -> None:
        item = self.itemAt(at)
        row = None if item is None else item.data(0, ROW)
        if not isinstance(row, brain_graph.OutlineRow):
            return
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        act = menu.addAction("Rename\u2026")
        act.setToolTip("A name for the routine, shown wherever the script names it")
        act.triggered.connect(lambda _=False: self.renamed.emit(row.routine))
        menu.popup(self.viewport().mapToGlobal(at))

    def _clicked(self, item: QTreeWidgetItem, column: int) -> None:
        row = item.data(0, ROW)
        if not isinstance(row, brain_graph.OutlineRow):
            return
        if column == 1 and row.callers:
            self.reach_menu(row).popup(QCursor.pos())
        else:
            self.picked.emit(row.routine)

    def _activated(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        row = item.data(0, ROW)
        if isinstance(row, brain_graph.OutlineRow):
            self.picked.emit(row.routine)


class BrainPanel(kit.Panel):
    """The tab: a bar (species, variant, find, history, copy), the script's outline, its code and,
    for a species file, the graph, the operand bar and what to do with the script."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__(scroll=False)
        self.ws, self.studio = ws, studio
        #: whether `sync` has read the species yet, the species and the document shown
        self._loaded = False
        self._species: int | None = None
        self._doc: object | None = None
        #: the header the variant choice picked; None: the one most map types use
        self._header: str | None = None
        self._view: brain.BrainView | None = None
        #: whether the view shown is of a script that takes edits
        self._editing = False
        #: the place (row, routine, scope, node) the graph pane and the bar were last put at
        self._placed: tuple[int, str, str, str | None] | None = None
        self.history = brain.History()
        #: the rows the find text matches, and whether no Return has stepped through them yet
        self._matches: list[int] = []
        self._fresh = True
        self._opening = False
        #: the row Follow the game last took the view to
        self._seen: int | None = None
        #: while above zero, the cursor moves are not the user's: no one is told
        self._quiet = 0
        #: the build, the outline and the focus the panel has taken, and the document version
        self._built: object | None = None
        self._uses: tuple[Built, brain.Uses] | None = None
        self._outline: brain_graph.Outline | None = None
        self._focused = 0
        self._want: tuple[str | None, str | None] | None = None
        self._version = -1
        self._tries = 0
        #: the widgets plugged into the slots, which `sync` keeps in step
        self._parts: list[QWidget] = []

        self.species = kit.choice(
            [],
            tip="Whose AI script to read: any monster the game's overlays serve a script for."
            " With a monster's brain file open, one whose script another overlay holds opens"
            " that monster and its brain file",
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

        self.counting = kit.button(
            f"Count {brain_game.SECONDS:g} s", tip=COUNT_TIP, on=self._start_count
        )
        self.send = kit.button("Send brain", tip=SEND_TIP, on=self._send)
        self.push = kit.button("Push brain", tip=PUSH_TIP, on=self._push)
        self.status = kit.Elided("", None, role="muted")
        self.tools = kit.row(self.counting, self.send, self.push, self.status, grow=self.status)

        self.model = BrainModel()
        self.tree = Code(ws, studio, self.model)
        self.tree.setToolTip(
            "The AI script as code. Double-click or Return on an underlined call, goto or walk"
            " to follow it. Routines start folded. With a brain file open, the context menu"
            " changes the script: insert, delete, replace, move, wrap, note, back to the game's."
        )
        self.delegate = self.tree.delegate
        self.tree.setItemDelegate(self.delegate)
        self.tree.expanded.connect(self._opened)
        self.tree.activated.connect(lambda i: self._follow(i.internalId()))
        self.tree.entered.connect(self._hover)
        self.tree.viewportEntered.connect(lambda: self.tree.viewport().unsetCursor())
        selection = self.tree.selectionModel()
        selection.currentChanged.connect(lambda *_: self._moved())

        self.lists = Lists()
        self.lists.jumped.connect(self.goto)
        self.outline = Routines()
        self.outline.picked.connect(lambda r: self._show_node(None, r, own=False))
        self.outline.reached.connect(self._reached)
        self.outline.renamed.connect(lambda r: self._rename(brain_names.ROUTINE, r))
        self.left = QStackedWidget()
        self.left.addWidget(self.lists)
        self.left.addWidget(self.outline)
        self.names = brain_names.Names()
        self.names.named.connect(self._rename)
        self.side = QSplitter(Qt.Orientation.Vertical)
        self.side.setChildrenCollapsible(False)
        self.side.addWidget(self.left)
        self.side.addWidget(self.names)
        self.side.setSizes([700, 300])
        self.side.setStretchFactor(0, 1)

        self.graph_slot = QWidget()
        self.bar_slot = QWidget()
        self.views = QSplitter(Qt.Orientation.Horizontal)
        self.views.setChildrenCollapsible(False)
        self.views.addWidget(self.graph_slot)
        self.views.addWidget(self.tree)
        self.views.setSizes([600, 500])
        self.views.setStretchFactor(0, 1)
        self.views.setStretchFactor(1, 1)
        centre = QWidget()
        self.lay = QVBoxLayout(centre)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self.lay.addWidget(self.views, 1)
        self.lay.addWidget(self.bar_slot)
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.setChildrenCollapsible(False)
        self.split.addWidget(self.side)
        self.split.addWidget(centre)
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
        lay.addWidget(self.tools)
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
        self._modes(False)
        self._ready(False)
        self.preview = MovePopup(ws, studio, self)
        self.graph = GraphPane(ws, studio, self.preview)
        self.operands = OperandBar(ws, studio, self.preview)
        self.graph.node_picked.connect(self.on_node)
        self.graph.link_followed.connect(self.on_node)
        self.graph.scope_opened.connect(self.on_scope)
        self.plug(self.graph, self.operands)

    # ---- what is shown ----------------------------------------------------------------- #

    def plug(self, graph: QWidget, bar: QWidget) -> None:
        """Puts the graph pane and the operand bar in their slots. `sync` keeps them in step, and
        they show only while the script takes edits."""
        if graph is not self.graph_slot:
            old = self.graph_slot
            self.views.replaceWidget(0, graph)
            old.deleteLater()
        if bar is not self.bar_slot:
            old = self.bar_slot
            self.lay.replaceWidget(old, bar)
            old.deleteLater()
        self.graph_slot, self.bar_slot = graph, bar
        self._parts = [graph, bar]
        self._modes(self._editing)

    def sync(self) -> None:
        """Only notices another species or document, a change of the file and a request to show a
        place; everything slower waits for `_pull`'s timer."""
        ws, sp = self.ws, self.ws.brain_species
        if not self._loaded or sp != self._species or ws.species_doc is not self._doc:
            self._load(sp)
        elif self._editing:
            self._noticed()
        self._place()
        for part in self._parts:
            if callable(step := getattr(part, "sync", None)):
                step()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self.sync()
        self._pull()  # the game may have been set up since, and a decode may be waiting
        self._arm()
        self._tick()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        super().hideEvent(event)
        self.preview.drop()
        self.poll.stop()
        self._arm()

    def _load(self, sp: int | None) -> None:
        self._loaded, self._species, self._header = True, sp, None
        self._doc = self.ws.species_doc
        self._built = self._outline = self._want = None
        self._focused, self._version = 0, -1
        self.ws.follow.reset()
        self._clear()
        self._pull()

    def _clear(self) -> None:
        self._view = None
        self._seen = None
        self._editing = False
        self.tree.editing = False
        self.model.set_view(None)
        self.model.notes, self.model.counts, self.model.changed = {}, {}, frozenset()
        self.history.clear()
        self.lists.show_lists(())
        self.outline.show_outline(None, {})
        self._matches = []
        self._modes(False)
        self._ready(False)

    def _pull(self) -> None:
        """Asks the workspace for what the panel waits for, and shows it; the timer runs until
        it is all there (a decode takes seconds, the scan of the species a few more, a build
        longer on a big overlay)."""
        ws, sp = self.ws, self._species
        view = None if sp is None else ws.brain_view(sp, self._header)
        rows = ws.brain_rows()  # after the decode: the worker takes them in order
        editing = sp is not None and ws.brain_editable(sp)
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
                if view is not self._view or editing != self._editing:
                    self._show(view, editing)
            elif err := ws.brain_error(sp):
                if self._view is not None:
                    self._clear()
                self._say("No AI script to show", err)
            else:
                waiting = True
                if self._view is None:  # a header on its way keeps the one shown
                    self._say(f"Reading {species.label(sp)}'s AI script\u2026", "")
        if sp is not None and self._editing and not ws.brain_note:
            waiting = self._editor() or waiting
        if waiting and self.isVisible():
            self.poll.start()
        else:
            self.poll.stop()

    def _say(self, title: str, hint: str) -> None:
        self.note.say(title, hint)
        self.main.show_page(False)

    def _show(self, view: brain.BrainView, editing: bool) -> None:
        """Shows `view`. Another build of the script being edited keeps the cursor, the routines
        open, the scroll and the history, which follow their lines by node."""
        old = self._view
        swap = editing and self._editing and old is not None and old.header == view.header
        swap = swap and old is not None and old.row == view.row
        kept = self.tree.keep() if swap else None
        trail = self._trail(old, view) if swap and old is not None else None
        self._view, self._editing = view, editing
        self.model.notes = self.ws.brain_notes(view.row) if editing else {}
        self.model.changed = self._changed(view.row) if editing else frozenset()
        self.model.set_words(self.ws.brain_words(view.row))
        with self._silent():
            self.model.set_view(view)
            self.tree.editing = editing
            if kept is not None:
                self.tree.restore(kept)
        if trail is None:
            self.history.clear()
        else:
            self.history.rows[:], self.history.at = trail
        self.lists.show_lists(() if editing else view.outline)
        heads = self.ws.brain_headers(view.row)
        kit.refill(self.variant, [(h.header, h.words) for h in heads], view.header)
        self.variant.setVisible(len(heads) > 1)
        self._modes(editing)
        self._gutter()
        self.main.show_page(True)
        self._ready(True)
        self._rematch()
        self.ws.place_follow(view)
        self._seen = self.ws.follow.next_row if swap else None
        self._marks()
        self._counted()
        self._focus_check()
        self._seek()

    def _changed(self, row: int) -> frozenset[str]:
        """The ids of the game's nodes the edits of the build change."""
        built = self.ws.brain_built(row)
        return frozenset() if built is None else edited_ids(built.edits)

    def _trail(self, old: brain.BrainView, view: brain.BrainView) -> tuple[list[int], int]:
        """The history as the rows of `view`: each stop follows its line, a gone one drops."""
        h = self.history
        rows: list[int] = []
        at = -1
        for i, row in enumerate(h.rows):
            now = row_of(view, key_of(old, row))
            if now is not None and (not rows or rows[-1] != now):
                rows.append(now)
            if i <= h.at:
                at = len(rows) - 1
        return rows, at

    def _modes(self, editing: bool) -> None:
        """What only a script that takes edits has: the outline of its routines, the graph, the
        operand bar and the bar of what to do with the script."""
        self.left.setCurrentWidget(self.outline if editing else self.lists)
        for w in (self.graph_slot, self.bar_slot, self.tools, self.names):
            w.setVisible(editing)

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
        if not cid:
            return
        self._leave()
        model = self.ws.brain_model(int(cid))
        if model is not None:  # another overlay's script: open that monster, as File > Open would
            self.studio.open_later(model)
            self._fill_species(self.ws.brain_rows())  # the list shows the script shown until then
            return
        self.ws.browse_brain(int(cid))
        self.sync()

    def _pick_variant(self, cid: str) -> None:
        self._leave()
        self._switch(cid)

    def _switch(self, header: str) -> None:
        """Shows the script as `header` runs it."""
        if self._view is not None and header != self._view.header:
            self._header = header
            if self._editing:
                self.ws.brain_header = header
            self._pull()

    @contextmanager
    def _silent(self) -> Iterator[None]:
        """The cursor moves inside are the panel's own: they tell no one."""
        self._quiet += 1
        try:
            yield
        finally:
            self._quiet -= 1

    # ---- the editor -------------------------------------------------------------------- #

    def _editor(self) -> bool:
        """What a script that takes edits has beyond a view: the findings, the outline, Count's
        answer, the status and the buttons. True while something is still on its way."""
        ws, sp, v = self.ws, self._species, self._view
        if sp is None or v is None:
            return False
        ws.poll_count()
        built = ws.brain_built(sp)
        if built is not self._built:
            self._built, self._tries = built, 0
            self.studio.findings.stale()
        names = ws.brain_names(sp)
        outline = ws.brain_outline(sp, v.header)
        if outline is not self._outline:
            self._outline = outline
            self.outline.show_outline(outline, names)
            self.outline.select(self._routine())
        self._names(sp, built)
        pending, err = ws.brain_pending(sp), ws.brain_error(sp)
        budget = None if pending or err else ws.brain_budget(sp)
        self._tries += budget is None and not pending and not err
        self._counted()
        self._status(pending, err, budget)
        self._buttons()
        asking = budget is None and not pending and not err and self._tries < BUDGET_TRIES
        return pending or asking or ws.count.busy

    def _names(self, sp: int, built: Built | None) -> None:
        """The file's names and notes on the lines and in the Names list, after they change:
        neither makes a new build."""
        words, notes = self.ws.brain_words(sp), self.ws.brain_notes(sp)
        changed = self.model.set_words(words) or notes != self.model.notes
        self.model.notes = notes
        if changed:
            self.tree.viewport().update()
        if built is not None:
            if self._uses is None or self._uses[0] is not built:
                self._uses = (built, brain.uses(built.program))
            self.names.show_names(words, self._uses[1])

    def _rename(self, table: str, key: str | int) -> None:
        if self._species is not None and self._editing:
            brain_names.rename(self, self.ws, self.studio, self._species, table, key)

    def _routine(self) -> str | None:
        v, row = self._view, self.tree.row()
        return None if v is None or row is None else v.routine_at(row)

    def _status(self, pending: bool, err: str, budget: Budget | None) -> None:
        parts: list[str] = []
        if err:
            parts.append(err)
        elif pending:
            parts.append(BUILDING)
        elif budget is not None:
            parts.append(f"longest run {budget_text(budget)} instructions")
        if said := self.ws.count.status:
            parts.append(said)
        text = f" {brain_game.DOT} ".join(parts)
        self.status.setText(text)
        self.status.setToolTip(text or "The script's longest run, and what building and Count say")
        self.status.set_level("error" if err else None)

    def _buttons(self) -> None:
        """Count when a followed monster can be counted, Send and Push when the script can go."""
        ws, f = self.ws, self.ws.follow
        why = ""
        if ws.count.busy:
            why = "counting\u2026"
        elif not f.on or f.target is None:
            why = brain_game.NOT_FOLLOWING
        self.counting.setEnabled(not why)
        self.counting.setToolTip(COUNT_TIP if not why else f"{COUNT_TIP}. Not now: {why}")
        for button, blocker, tip in (
            (self.send, ws.brain_send_blocker, SEND_TIP),
            (self.push, ws.brain_push_blocker, PUSH_TIP),
        ):
            blocked = blocker()
            button.setEnabled(blocked is None)
            button.setToolTip(tip if blocked is None else blocked)

    def _start_count(self) -> None:
        self.studio.act("count the script", self.ws.count_brain)()
        self._arm_poll()

    def _send(self) -> None:
        self.studio.act("send the brain", self.ws.send_brain)()

    def _push(self) -> None:
        self.studio.act("push the brain", self.ws.push_brain)()

    def _counted(self) -> None:
        """Count's answer on the lines, while it is of the build shown."""
        ws = self.ws
        got = ws.count.result
        nodes = {} if got is None or ws.count.stale or not self._editing else got.nodes
        if nodes != self.model.counts:
            self.model.counts = nodes
            self.tree.viewport().update()

    def _noticed(self) -> None:
        """After a change: a place to show, a new version of the file to wait for a build of, the
        buttons."""
        doc = self.ws.species_doc
        self._focus_check()
        if doc is not None and doc.version != self._version:
            self._version = doc.version
            self._pull()
        self._buttons()
        self._arm_poll()

    def _arm_poll(self) -> None:
        """Polls while a build or a count is on its way."""
        sp = self._species
        if (
            self.isVisible()
            and sp is not None
            and (self.ws.count.busy or self.ws.brain_pending(sp))
        ):
            self.poll.start()

    # ---- where the other views send the cursor ---------------------------------------- #

    def _focus_check(self) -> None:
        """A place was asked for (`ws.show_brain_node`) by another view: go there."""
        ws, v = self.ws, self._view
        if not self._editing or v is None or ws.brain_focused == self._focused:
            return
        self._focused = ws.brain_focused
        self._leave()
        self._want = (ws.brain_focus, ws.brain_routine)
        head = ws.brain_header
        if head not in (None, v.header) and any(h.header == head for h in ws.brain_headers(v.row)):
            self._header = head
            self._pull()  # that header's view, which seeks there
            return
        self._seek()

    def _seek(self) -> None:
        """Goes to the place asked for, once the view has it."""
        v, want = self._view, self._want
        if v is None or want is None:
            return
        node, routine = want
        row = None
        if node:
            row = v.row_for(node, routine)
        elif routine is not None:
            row = v.routine_row.get(routine)
        if row is None:
            return
        self._want = None
        with self._silent():
            self._mark()
            self.history.go(row)
            self.tree.reveal(row)
        self._arrows()
        self.outline.select(v.routine_at(row))

    def _show_node(self, node: str | None, routine: str | None, *, own: bool) -> None:
        """Tells the workspace the place picked, so that every view goes there: `own` when it is
        this panel's cursor, which is there already."""
        ws, v = self.ws, self._view

        def go() -> None:
            if v is not None:
                ws.brain_header = v.header
            if ws.show_brain_node(node, routine) and own:
                self._focused = ws.brain_focused

        self.studio.act("show a place in the script", go)()

    def on_node(self, node: str | None) -> None:
        """The graph pane picked a node (or the id of a routine): the code, the outline and the
        workspace follow."""
        v = self._view
        if node is None or v is None or not self._editing:
            return
        if v.row_for(node) is None and node in v.routine_row:
            self._show_node(None, node, own=False)
        else:
            self._show_node(node, None, own=False)

    def on_scope(self, routine: str, scope: str) -> None:
        """The graph pane opened a scope of a routine: the code goes to the arm or block it is,
        the outline to the routine, and the workspace keeps it for the next place."""
        ws, v = self.ws, self._view
        if v is None or not self._editing:
            return
        rg = ws.brain_routine_graph(v.row, routine, v.header)
        owner = None if rg is None or scope not in rg.scopes else rg.scopes[scope].owner

        def go() -> None:
            ws.brain_routine, ws.brain_scope, ws.brain_header = routine, scope, v.header
            row = v.routine_row.get(routine)
            if owner is not None:
                row = v.row_for(scope if scope in v.row_of else owner)
            if row is not None:
                with self._silent():
                    self._mark()
                    self.history.go(row)
                    self.tree.reveal(row)
                self._arrows()
            self.outline.select(routine)

        self.studio.act("show a scope of the script", go)()

    def _place(self) -> None:
        """The graph pane and the operand bar at the workspace's place, once it moved."""
        ws, v = self.ws, self._view
        if not self._editing or v is None or ws.brain_routine is None:
            return
        at = (v.row, ws.brain_routine, ws.brain_scope, ws.brain_focus)
        if at == self._placed:
            return
        self._placed = at
        self.graph.show_scope(v.row, ws.brain_routine, ws.brain_scope)
        self.graph.select(ws.brain_focus)
        self.operands.show_node(v.row, ws.brain_focus)

    def _reached(self, caller: str, routine: str) -> None:
        """A caller picked in a routine's "reached from" list: its first call of the routine."""
        v = self._view
        site = None
        if v is not None and caller in v.routine_row and routine in v.routine_row:
            kids = v.children[v.routine_row[routine]]
            first = v.lines[kids[0]].node if kids else None
            hits = [
                r for r in v.span(v.routine_row[caller]) if first and v.lines[r].target == first
            ]
            site = v.lines[hits[0]].node if hits else None
        self._show_node(site, caller, own=False)

    # ---- getting around ---------------------------------------------------------------- #

    def _cursor(self) -> int | None:
        return self.tree.row()

    def goto(self, row: int) -> None:
        """Selects line `row`, folding the other routines; Back leads to where the cursor was."""
        self._leave()
        self._mark()
        self.history.go(row)
        self.tree.reveal(row)
        self._arrows()

    def _mark(self) -> None:
        """Puts the cursor in the history, when it got where it is by no step of the history."""
        here = self._cursor()
        if here is not None and here != self.history.current:
            self.history.go(here)

    def _step(self, back: bool) -> None:
        if back:
            self._mark()
        row = self.history.back() if back else self.history.forward()
        if row is not None:
            self._leave()
            self.tree.reveal(row)
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
        """The cursor went somewhere: find goes on from there, and a script that takes edits tells
        the workspace, which tells the other views."""
        self._fresh = False
        self._count()
        if not self._quiet:
            self._announce()

    def _announce(self) -> None:
        v, row = self._view, self._cursor()
        if not self._editing or v is None or row is None or (routine := v.routine_at(row)) is None:
            return
        self.outline.select(routine)
        self._show_node(v.node_at(row), routine, own=True)

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

    def _put(self, rows: Sequence[int]) -> None:
        v = self._view
        if v is None or not rows:
            self.studio.message = "nothing to copy: pick a line first"
            return
        QGuiApplication.clipboard().setText("\n".join(v.lines[r].text for r in rows))
        self.studio.message = f"copied {len(rows)} line{'' if len(rows) == 1 else 's'}"

    def _copy(self) -> None:
        """The lines selected; with one selected, or none, the routine the cursor is in."""
        v, rows, here = self._view, self.tree.picked(), self._cursor()
        if v is not None and len(rows) < 2:
            row = rows[0] if rows else here
            rows = [] if row is None else list(v.span(v.top_of(row)))
        self._put(rows)

    def _copy_lines(self) -> None:
        """The standard Copy key: the lines selected, as in any text view."""
        self._put(self.tree.picked())

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
        if not on and self.ws.count.busy:
            self.ws.count.reset()  # a count in flight ends with Follow; a finished one stays
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
        if self._editing:
            self._editor()

    def _marks(self) -> None:
        """Shows what Follow says: its status and marks, and goes where the monster is."""
        f, v = self.ws.follow, self._view
        self.model.set_marks(f.marks if f.on and v is not None else {})
        if v is not None:
            for row in self.model.marks:  # a routine holding a mark is open
                self.tree.expand(self.model.index_of(v.top_of(row)))
        next_in = None if not f.on or v is None or f.next_row is None else v.routine_at(f.next_row)
        self.outline.mark(next_in)
        text = f.status
        if f.on and f.target is not None and f.target.runs == brain_game.BUILD:
            text += f" {brain_game.DOT} {YOURS}"
        self.live.setVisible(f.on)
        self.live.setText(text)
        self.live.setToolTip(text)
        self.live.set_level(None if f.next_row is not None else "warning")
        if not f.on or v is None:
            return
        if f.header is not None and f.header != v.header:
            self._switch(f.header)
        elif f.next_row is not None and f.next_row != self._seen:
            self._seen = f.next_row
            if self._editing and next_in is not None and next_in != self.graph.routine:
                self.graph.show_scope(v.row, next_in, ROOT)  # the pane then follows inside it
            with self._silent():
                self.tree.reveal(f.next_row)
