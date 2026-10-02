# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The start page, in the view's place while nothing is open (`Studio.on_start`): the setup
checklist, the workspace's lists to open from (`Workspace.start`) and its recent documents."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mhfu_studio.shell import places
from mhfu_studio.shell.studio import doc_name
from mhfu_studio.shell.text import plain
from mhfu_studio.shell.workspace import Choice, Shelf
from mhfu_studio.ui import dialogs, kit, theme

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

Model = tuple[str, str, str, str, tuple[places.Found, ...], tuple[Shelf, ...], tuple[Path, ...]]
#: the column's widest, so a big window does not stretch the lines
WIDTH = 760
COLUMNS = 3
INTRO = {
    "map": "Pick an area, move what you like, and send it into the running game.",
    "monster": "Open a port, tweak its hitboxes, and send them into the running game.",
}


class Tile(QPushButton):
    """An entry: its label, and after it small its detail."""

    def __init__(self, label: str, detail: str, *, tip: str, on: Callable[[], object]) -> None:
        super().__init__()
        self.setObjectName("Tile")
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(lambda *_: on())
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 10, 5)
        lay.setSpacing(8)
        self.label = kit.label(label, wrap=False)
        self.detail = kit.label(detail, role="muted", wrap=False)
        for w in (self.label, self.detail):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lay.addWidget(w)
        lay.insertStretch(1, 1)

    def sizeHint(self) -> QSize:  # noqa: N802
        lay = self.layout()
        return lay.sizeHint() if lay is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()


class StartPage(QScrollArea):
    """Rebuilt when what it shows changes; `sync` is cheap otherwise."""

    def __init__(self, studio: Studio) -> None:
        super().__init__()
        self.studio = studio
        self.setObjectName("Start")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        outer = QWidget()
        outer.setObjectName("Card")
        outer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(outer)
        row.setContentsMargins(24, 20, 24, 20)
        self.column = QWidget()
        self.column.setMaximumWidth(WIDTH)
        self.lay = QVBoxLayout(self.column)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(16)
        row.addWidget(self.column, 1)
        self.setWidget(outer)
        self._shown: Model | None = None
        #: the place rows' Choose buttons, by place key, for tests
        self.choose: dict[str, QPushButton] = {}
        self.tiles: list[Tile] = []
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)  # no end known: Qt animates it
        self.busy.setTextVisible(False)
        self.busy.setFixedHeight(6)

    def model(self) -> Model:
        """Everything the page shows, to compare between syncs."""
        st, ws = self.studio, self.studio.active
        opening = st.opening.warmup.what if st.opening is not None else ""
        back = doc_name(ws) if ws.shown() is not None and not opening else ""
        found = tuple(places.find(p) for p in places.PLACES)
        shelves = tuple(ws.start())
        return ws.name, theme.current().name, back, opening, found, shelves, tuple(st.recent())

    def sync(self) -> None:
        m = self.model()
        if m == self._shown:
            return
        self._shown = m
        name, _, back, opening, found, shelves, recent = m
        self._clear()
        self.choose, self.tiles = {}, []
        lay = self.lay
        head = kit.label(name.capitalize(), role="heading", wrap=False)
        top: list[QWidget] = [head]
        if back:
            top.append(
                kit.button(
                    f"Back to {back}",
                    tip="Leaves the start page for the document you had open",
                    on=self.studio.act("back", lambda: self.studio.show_start(False)),
                    icon="ph.arrow-left",
                )
            )
        lay.addWidget(kit.row(*top, stretch=True, spacing=16))
        lay.addWidget(kit.label(INTRO.get(name, ""), role="muted"))
        if opening:
            lay.addWidget(self.busy)
            self.busy.show()  # leaving the page hid it
            lay.addWidget(kit.label(opening, role="muted"))
        lay.addWidget(self._setup(found))
        for shelf in shelves:
            lay.addWidget(self._shelf(shelf))
        if recent:
            lay.addWidget(self._recent(recent))
        lay.addStretch(1)

    def _clear(self) -> None:
        self.busy.setParent(None)  # kept: it is made once
        while (item := self.lay.takeAt(0)) is not None:
            w = item.widget()
            if w is not None:
                w.deleteLater()

    # ---- the parts --------------------------------------------------------------------- #

    def _setup(self, found: Sequence[places.Found]) -> QWidget:
        sec = kit.Section("Setup", tip="Where the studio finds the games and PPSSPP")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)
        grid.setColumnStretch(1, 1)
        for i, f in enumerate(found):
            p, ok = f.place, f.path is not None
            mark = QLabel()
            mark.setPixmap(theme.status_icon(ok).pixmap(16, 16))
            mark.setToolTip("Found" if ok else "Missing")
            title = kit.label(p.name, role="title", wrap=False)
            says = kit.Alert(f.says(), None if ok else "warning", role="muted")
            says.setToolTip(p.what)
            lines = QVBoxLayout()
            lines.setSpacing(1)
            lines.addWidget(title)
            lines.addWidget(says)
            if not ok:
                lines.addWidget(kit.label(f"{p.needs}.", role="muted"))
            elif f.source == "env":
                wins = f"{p.env} is set: it wins over a choice here."
                lines.addWidget(kit.label(wins, role="muted"))
            b = kit.button(
                "Choose…",
                tip=f"Pick it in a folder dialog; the studio remembers it. {p.what}.",
                on=lambda p=p: self._pick(p),
            )
            self.choose[p.key] = b
            grid.addWidget(mark, 2 * i, 0, Qt.AlignmentFlag.AlignTop)
            grid.addLayout(lines, 2 * i, 1)
            grid.addWidget(b, 2 * i, 2, Qt.AlignmentFlag.AlignTop)
            grid.setRowMinimumHeight(2 * i + 1, 8)
        sec.body.addLayout(grid)
        return sec

    def _pick(self, p: places.Place) -> None:
        path = dialogs.ask_folder(self, p)
        if path is None:
            return
        self.studio.act(f"choose the {p.name}", lambda: self.studio.remember(p, path))()
        if places.find(p).path is None or self.studio.message.startswith("not used"):
            dialogs.warn(self, "Not used", plain(self.studio.message))

    def _shelf(self, shelf: Shelf) -> QWidget:
        sec = kit.Section(shelf.title)
        missing = [p for p in shelf.needs if places.find(p).path is None]
        if missing and shelf.choices:
            names = " and the ".join(p.name for p in missing)
            sec.body.addWidget(kit.Alert(f"Set up the {names} above first."))
        group = None
        grid: QGridLayout | None = None
        n = 0
        for c in shelf.choices:
            if grid is None or c.group != group:
                group, n = c.group, 0
                if group:
                    sec.body.addWidget(kit.label(group, role="muted", wrap=False))
                grid = self._grid()
                sec.body.addLayout(grid)
            t = self._tile(c)
            t.setEnabled(not missing)
            grid.addWidget(t, n // COLUMNS, n % COLUMNS)
            n += 1
        if shelf.note:
            sec.body.addWidget(kit.label(shelf.note, role="muted"))
        if shelf.browse is not None:
            text, tip = shelf.browse
            b = kit.button(text, tip=tip, on=self._browse, icon="ph.folder-open")
            sec.body.addWidget(kit.row(b, stretch=True))
        return sec

    def _recent(self, recent: Sequence[Path]) -> QWidget:
        sec = kit.Section("Recent", tip="What you opened or saved here last, newest first")
        grid = self._grid()
        for n, path in enumerate(recent):
            c = Choice(
                f"{path.parent.name}/{path.name}",
                f"Opens {plain(str(path))}",
                path=path,
                detail=plain(str(path.parent.parent)),
            )
            grid.addWidget(self._tile(c), n // COLUMNS, n % COLUMNS)
        sec.body.addLayout(grid)
        return sec

    @staticmethod
    def _grid() -> QGridLayout:
        g = QGridLayout()
        g.setSpacing(6)
        for col in range(COLUMNS):
            g.setColumnStretch(col, 1)
        return g

    def _tile(self, c: Choice) -> Tile:
        t = Tile(c.label, c.detail, tip=c.tip, on=lambda: self._choose(c))
        self.tiles.append(t)
        return t

    def _choose(self, c: Choice) -> None:
        QGuiApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)  # an area takes a second
        try:
            self.studio.act(f"open {c.label}", lambda: self.studio.choose(c))()
        finally:
            QGuiApplication.restoreOverrideCursor()

    def _browse(self) -> None:
        self.studio.act("open", lambda: dialogs.open_document(self.window(), self.studio))()
