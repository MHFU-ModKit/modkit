# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The outline beside the Behaviour canvas: every trigger of the port, with how many rules it
starts and where they stand in the order the brain checks them. Picking one scopes the canvas.

The triggers are the package's (`mhfu_port.behaviour.triggers`); this module words them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from mhfu_port.behaviour import KINDS, Path, Trigger
from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetricsF, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from mhfu_studio.monster.behaviour import ALL
from mhfu_studio.ui import theme

SECTIONS = {"event": "When", "state": "Between moves", "move": "While a move plays"}
DASH = "—"
RANGE = "–"
SEP = " › "
ROW_H, COUNT_W, SPAN_W, PAD_X = 24, 24, 66, 8
#: the item roles: what the delegate draws, and the key a row stands for
CELL = Qt.ItemDataRole.UserRole + 1
KEY = Qt.ItemDataRole.UserRole


def span(t: Trigger) -> str:
    """`#first` or `#first–#last`: where a trigger's rules stand among all of them."""
    if not t.rules:
        return ""
    lo, hi = min(t.ranks), max(t.ranks)
    return f"#{lo}" if lo == hi else f"#{lo}{RANGE}#{hi}"


def short(kind: str) -> str:
    """A kind's title as the outline says it: the section already says "When" and "Between
    moves", and the base monster is the only one a state belongs to."""
    title = KINDS[kind].title.removeprefix("On ").removeprefix("Base monster ")
    title = title.replace(" (between moves)", "")
    return title[:1].upper() + title[1:]


def title(t: Trigger) -> str:
    """The outline's row for `t`."""
    if t.section == "move":
        return t.name
    return f"{short(t.kind)} · {t.name}" if t.name else short(t.kind)


def says_all(total: int) -> str:
    """The header over All's rows."""
    return f"All rules {DASH} {total} in the order they are checked"


def label(t: Trigger) -> str:
    """`t` in a frame's title: its row, a move's worded as the while-playing port it is."""
    return f"while {t.name}" if t.section == "move" else title(t)


def tip(t: Trigger) -> str:
    if t.section == "move":
        return f"The rules that start from the while-playing port of {t.name}"
    return KINDS[t.kind].tip


def says(t: Trigger, total: int) -> str:
    """The header over a scoped canvas: `All › Part break — 4 rules, #first–#last of n`."""
    name = f"While {t.name} plays" if t.section == "move" else title(t)
    n = len(t.rules)
    tail = f"{n} rule{'' if n == 1 else 's'}, {span(t)} of {total}" if n else "no rules"
    return f"All{SEP}{name} {DASH} {tail}"


def holds(ts: Sequence[Trigger], opens: Mapping[str, Sequence[Path]], key: str, block: str) -> bool:
    """Whether trigger `key` has a rule, with a move or not, that runs through `block`."""
    t = next((t for t in ts if t.key == key), None)
    rules = [p for _, p in t.rules] if t is not None else []
    return any(block in p.blocks for p in [*rules, *opens.get(key, [])])


def owner(ts: Sequence[Trigger], opens: Mapping[str, Sequence[Path]], block: str) -> str | None:
    """The key of the first trigger one of whose rules runs through `block`; None: it is on no
    rule."""
    return next((t.key for t in ts if holds(ts, opens, t.key, block)), None)


class _Row(QStyledItemDelegate):
    """A row: its title left, its rule count and rank range right. The stylesheet draws the
    hover and selection fill across the whole row."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text, count, ranks, kind = index.data(CELL)
        opt.text = ""
        widget = option.widget
        (widget.style() if widget else QApplication.style()).drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget
        )
        quiet = theme.text(muted=True)
        font = QFont(opt.font)
        room = QRectF(opt.rect).adjusted(PAD_X, 0, -PAD_X, 0)
        if kind == "head":
            font.setBold(True)
            font.setPointSizeF(font.pointSizeF() * 0.85)
            painter.setFont(font)
            painter.setPen(quiet)
            painter.drawText(room, Qt.AlignmentFlag.AlignVCenter, text.upper())
            return
        right = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight
        painter.setFont(font)
        painter.setPen(quiet)
        painter.drawText(room, right, ranks)
        room.setRight(room.right() - SPAN_W)
        painter.drawText(room, right, count)
        room.setRight(room.right() - COUNT_W)
        if kind == "all":
            font.setBold(True)
            painter.setFont(font)
        painter.setPen(quiet if kind == "none" else theme.text())
        shown = QFontMetricsF(font).elidedText(text, Qt.TextElideMode.ElideRight, room.width())
        painter.drawText(room, Qt.AlignmentFlag.AlignVCenter, shown)

    def sizeHint(  # noqa: N802
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        return QSize(0, ROW_H)


class Outline(QListWidget):
    """The triggers as a list under "All rules"; `picked` carries a row's key."""

    picked = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setToolTip(
            "What starts a rule: pick one to see only its rules, in the order they are checked."
            " Greyed: nothing starts from it."
        )
        self.setFixedWidth(224)
        self.setItemDelegate(_Row(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self._now = ALL
        self.currentItemChanged.connect(self._changed)

    def set_triggers(self, ts: Sequence[Trigger], total: int, now: str) -> None:
        """The rows for `ts` and `total` rules, `now` picked, no `picked` for any of it."""
        self._now = now
        self.blockSignals(True)
        self.clear()
        self._add(ALL, (f"All rules ({total})", "", "", "all"), "Every rule, as stored")
        section = ""
        for t in ts:
            if t.section != section:
                section = t.section
                head = QListWidgetItem()
                head.setData(CELL, (SECTIONS[section], "", "", "head"))
                head.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.addItem(head)
            count = str(len(t.rules)) if t.rules else DASH
            self._add(t.key, (title(t), count, span(t), "row" if t.rules else "none"), tip(t))
        self.setCurrentItem(self.item_of(now))
        self.blockSignals(False)

    def _add(self, key: str, cell: tuple[str, str, str, str], tip_: str) -> None:
        item = QListWidgetItem()
        item.setData(CELL, cell)
        item.setData(KEY, key)
        item.setToolTip(tip_)
        self.addItem(item)

    def item_of(self, key: str) -> QListWidgetItem:
        """The row for `key`; the first row, All, if none."""
        found = (self.item(i) for i in range(self.count()))
        return next((i for i in found if i is not None and i.data(KEY) == key), self.item(0))

    def _changed(self, item: QListWidgetItem | None, _old: QListWidgetItem | None = None) -> None:
        key = item.data(KEY) if item is not None else None
        if isinstance(key, str) and key != self._now:
            self._now = key
            self.picked.emit(key)
