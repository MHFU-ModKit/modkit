# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Game panel: what Send to game does, Restore, and the log; sending one part alone and
the reload wait under More. Send and Stop are the toolbar's (and the File menu's).

Each button runs the workspace's `push_job` through the studio, as Send to game does; the log
is the studio's.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from mhfu_studio.shell.studio import LOG_LINES
from mhfu_studio.shell.text import plain
from mhfu_studio.ui import kit

from .common import Gate

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

    from ..workspace import MapWorkspace

HOW = (
    "Send to game (top right) puts this area's edits into the game running in PPSSPP; nothing"
    " on disk changes and you need not save. Stand in this area in the game first. Collision"
    " and textures show at once; the scenery and new climbable walls after the area loads"
    " again: walk out and back in."
)
CATCH_TIP = (
    "How long a send waits for the area to load again, to write the scenery then. A quest area"
    " needs it; the village never reloads, so 0 there."
)
VILLAGE_WARNING = (
    "This is the village: set the wait to 0. The village never reloads its files, so waiting"
    " only slows the game down."
)
#: (label, part, tip) per button that sends one part alone
PARTS: tuple[tuple[str, str, str], ...] = (
    ("Mesh", "mesh", "Sends only the visible scenery; it shows after the area loads again"),
    ("Collision", "collision", "Sends only the floors and walls; they work at once"),
    ("Textures", "textures", "Sends only the textures; they show at once"),
)


class GamePanel(kit.Panel):
    def __init__(self, ws: MapWorkspace, studio: Studio) -> None:
        super().__init__(scroll=False)
        self.ws, self.studio = ws, studio
        self._lines: list[str] = []
        how = kit.label(HOW, role="muted")
        self.running = kit.label(role="muted", wrap=True)
        self.restore = kit.button(
            "Restore",
            tip="Puts the game's own bytes for this area back, undoing every send",
            on=self._push("Restore", (), restore=True),
        )
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(LOG_LINES)
        self.log.setToolTip("What the last send printed: what it planned, wrote and how it ended")
        self.log.setPlaceholderText("Nothing sent yet. What a send prints shows here.")

        more = kit.More(tip="Sending one part alone, and how long a send waits for a reload")
        self.pushes: list[QPushButton] = [
            kit.button(label, tip=tip, on=self._push(label, (part,))) for label, part, tip in PARTS
        ]
        self.catch = kit.number(
            tip=CATCH_TIP,
            value=ws.catch,
            lo=0,
            hi=600,
            suffix=" s",
            on=studio.act("catch", self._set_catch),
        )
        form = kit.Form()
        form.row("Send only", kit.row(*self.pushes, stretch=True))
        form.row("Wait for reload", self.catch)
        self.village = kit.Alert(VILLAGE_WARNING)
        self.clear = kit.button(
            "Clear log",
            tip="Empties the log",
            on=studio.act("clear log", lambda: studio.log.clear()),
        )
        for w in (form, self.village, kit.row(self.clear, stretch=True)):
            more.body.addWidget(w)

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(how)
        lay.addWidget(self.running)
        lay.addWidget(kit.row(self.restore, stretch=True))
        lay.addWidget(self.log, 1)
        lay.addWidget(more)
        self.gate = Gate(page, "send its edits to the game")
        self.body.addWidget(self.gate)

    def _push(
        self, label: str, halves: tuple[str, ...], *, restore: bool = False
    ) -> Callable[..., None]:
        def run() -> None:
            self.studio.start(self.ws.push_job(halves, restore=restore))

        return self.studio.act(f"send {label.lower()}", run)

    def _set_catch(self) -> None:
        self.ws.catch = self.catch.value()

    def _show_log(self) -> None:
        """The studio's log, appended to while it only grew."""
        log = self.studio.log
        if log == self._lines:
            return
        n = len(self._lines)
        if len(log) > n and log[:n] == self._lines:
            for line in log[n:]:
                self.log.appendPlainText(plain(line))
        else:
            self.log.setPlainText("\n".join(plain(line) for line in log))
        self._lines = list(log)

    def sync(self) -> None:
        self._show_log()
        ws, studio = self.ws, self.studio
        if not self.gate.check(ws):
            return
        with QSignalBlocker(self.catch):
            self.catch.setValue(ws.catch)
        self.village.setVisible(ws.in_village() and ws.catch > 0)
        job = studio.job
        why = ws.push_blocker()
        for b in self.pushes:
            b.setEnabled(job is None and why is None)
        self.restore.setEnabled(job is None and ws.push_blocker(restore=True) is None)
        self.clear.setEnabled(bool(studio.log))
        self.running.setText(plain(f"{job.title}… (running)" if job else why or ""))
        self.running.setVisible(bool(self.running.text()))
