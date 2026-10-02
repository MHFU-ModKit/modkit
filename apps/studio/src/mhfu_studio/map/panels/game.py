# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Game panel: push the loaded section's edits, saved or not, into the running PPSSPP.

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
    "Sends the loaded section's edits into the game running in PPSSPP, through its debugger;"
    " the ISO is never touched and the document need not be saved. Stand in this section in"
    " the game first. Collision and textures land at once. The visible mesh lands on the next"
    " area load: walk out and back in while the catch waits. A climbable wall is read at area"
    " load too, so the push holds it for the catch's seconds: walk out and in once more."
)
CATCH_TIP = (
    "How long the push waits for the game to reload the area, to write the mesh again as it"
    " loads. A quest area re-reads its files on entry and needs it; the village never does,"
    " so there it is 0 and the mesh shows after you step into a house and out. While the"
    " catch waits, a debugger breakpoint is armed and the game runs slower."
)
VILLAGE_WARNING = (
    "This is a village section: set the catch to 0. The village never re-reads its files,"
    " so an armed catch only slows the game down."
)
#: (label, halves, tip) per push button; no halves is all of them
PUSHES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("Push all", (), "Sends the mesh, the collision and the textures together"),
    ("Mesh", ("mesh",), "Sends only the visible mesh; it shows after the next area load"),
    ("Collision", ("collision",), "Sends only the floors and walls; they work at once"),
    ("Textures", ("textures",), "Sends only the texture bank; it shows at once"),
)


class GamePanel(kit.Panel):
    def __init__(self, ws: MapWorkspace, studio: Studio) -> None:
        super().__init__(scroll=False)
        self.ws, self.studio = ws, studio
        self._lines: list[str] = []
        how = kit.label(HOW, role="muted")
        self.catch = kit.number(
            tip=CATCH_TIP,
            value=ws.catch,
            lo=0,
            hi=600,
            suffix=" s",
            on=studio.act("catch", self._set_catch),
        )
        form = kit.Form()
        form.row("Catch", self.catch)
        self.village = kit.Alert(VILLAGE_WARNING)
        self.pushes: list[QPushButton] = [
            kit.button(
                label,
                tip=tip,
                on=self._push(label, halves),
                role="primary" if i == 0 else "normal",
            )
            for i, (label, halves, tip) in enumerate(PUSHES)
        ]
        self.restore = kit.button(
            "Restore",
            tip="Puts the game's own bytes for this section back, undoing every push",
            on=self._push("Restore", (), restore=True),
        )
        self.stop = kit.button(
            "Stop",
            tip="Ends the running push now",
            on=studio.act("stop", studio.stop),
            role="danger",
        )
        self.clear = kit.button(
            "Clear log",
            tip="Empties the log below",
            on=studio.act("clear log", lambda: studio.log.clear()),
        )
        self.running = kit.label(role="muted", wrap=True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(LOG_LINES)
        self.log.setToolTip(
            "What the push printed: the command, what it planned and wrote, and how it ended"
        )
        self.log.setPlaceholderText("Nothing pushed yet. What a push prints shows here.")

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(how)
        lay.addWidget(form)
        lay.addWidget(self.village)
        lay.addWidget(kit.row(*self.pushes[:3], stretch=True))
        lay.addWidget(kit.row(*self.pushes[3:], self.restore, self.stop, self.clear, stretch=True))
        lay.addWidget(self.running)
        lay.addWidget(self.log, 1)
        self.gate = Gate(page, "push its edits into the game")
        self.body.addWidget(self.gate)

    def _push(
        self, label: str, halves: tuple[str, ...], *, restore: bool = False
    ) -> Callable[..., None]:
        def run() -> None:
            self.studio.start(self.ws.push_job(halves, restore=restore))

        return self.studio.act(f"push {label.lower()}", run)

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
        self.stop.setEnabled(job is not None)
        self.clear.setEnabled(bool(studio.log))
        self.running.setText(plain(f"{job.title}… (running)" if job else why or ""))
