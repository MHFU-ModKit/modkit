# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Game panel: push the loaded section's edits into the running PPSSPP.

It runs `studio map inject` in a QProcess and shows its output as it comes, so the window
stays responsive while a catch waits for the area reload.
"""

from __future__ import annotations

import sys
from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtCore import QByteArray, QProcess, QProcessEnvironment, QSignalBlocker, QTimer
from PySide6.QtWidgets import QPlainTextEdit, QPushButton, QVBoxLayout, QWidget
from shiboken6 import isValid

from mhfu_studio.shell.text import plain
from mhfu_studio.ui import dialogs, kit

from ...stage.live import CLIMB, QUEST_CATCH
from .common import Gate

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

    from ..core.edit import Op
    from ..workspace import MapWorkspace

LOG_LINES = 2000
KILL_AFTER = 3000
"""Milliseconds Stop waits for the push to end before killing it."""
#: the studio's own command line, run by the interpreter running the window
MAIN = "import sys; from mhfu_studio.cli import main; sys.exit(main())"
HOW = (
    "Sends the loaded section's edits into the game running in PPSSPP, through its debugger;"
    " the ISO is never touched. Stand in this section in the game first. Collision and"
    " textures land at once. The visible mesh lands on the next area load: walk out and back"
    " in while the catch waits. A climbable wall is read at area load too, so the push holds"
    " it for the catch's seconds: walk out and in once more."
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
#: (label, flags, tip) per push button
PUSHES = (
    (
        "Push all",
        ["--mesh", "--collision", "--textures"],
        "Sends the mesh, the collision and the textures together",
    ),
    ("Mesh", ["--mesh"], "Sends only the visible mesh; it shows after the next area load"),
    ("Collision", ["--collision"], "Sends only the floors and walls; they work at once"),
    ("Textures", ["--textures"], "Sends only the texture bank; it shows at once"),
    (
        "Restore",
        ["--restore"],
        "Puts the game's own bytes for this section back, undoing every push",
    ),
)


def text(data: QByteArray) -> str:
    return bytes(data.data()).decode("utf-8", "replace")


def in_village(ws: MapWorkspace) -> bool:
    """The loaded section is in row 0, whose files the game never re-reads."""
    sc, atlas = ws.scene, ws.atlas
    return sc is not None and atlas is not None and any(r == 0 for r, _ in atlas.rows_of(sc.stage))


def climbs(ops: list[Op]) -> bool:
    """A collision op setting a climbable material: read at area load, so the push holds."""
    for o in ops:
        flags = o.get("flags")
        if o.get("op") == "collision" and isinstance(flags, dict):
            if flags.get("material") in CLIMB:
                return True
    return False


class GamePanel(kit.Panel):
    def __init__(self, ws: MapWorkspace, studio: Studio) -> None:
        super().__init__(scroll=False)
        self.ws, self.studio = ws, studio
        self.proc: QProcess | None = None
        self.what = ""
        self._stage: int | None = None
        how = kit.label(HOW, role="muted")
        self.unsaved = kit.pill(
            "warning", "Save the document first: the push reads the edits from its folder."
        )
        self.unsaved.setWordWrap(True)
        self.save_as = kit.button(
            "Save As…",
            tip="Picks a folder for the document and saves it there, so the push can read it",
            on=self._save_as,
        )
        self.catch = kit.number(tip=CATCH_TIP, value=QUEST_CATCH, lo=0, hi=600, suffix=" s")
        self.catch.valueChanged.connect(lambda _v: self._warn())
        form = kit.Form()
        form.row("Catch", self.catch)
        self.village = kit.pill("warning", VILLAGE_WARNING)
        self.village.setWordWrap(True)
        self.pushes: list[QPushButton] = []
        for i, (label, flags, tip) in enumerate(PUSHES):
            b = kit.button(
                label,
                tip=tip,
                on=studio.act(f"push {label.lower()}", partial(self.run, flags)),
                role="primary" if i == 0 else "normal",
            )
            self.pushes.append(b)
        self.stop = kit.button(
            "Stop", tip="Ends the running push now", on=self._stop, role="danger"
        )
        self.clear = kit.button("Clear log", tip="Empties the log below", on=self._clear)
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
        lay.addWidget(kit.row(self.unsaved, self.save_as))
        lay.addWidget(form)
        lay.addWidget(self.village)
        lay.addWidget(kit.row(*self.pushes[:3], stretch=True))
        lay.addWidget(kit.row(*self.pushes[3:], self.stop, self.clear, stretch=True))
        lay.addWidget(self.running)
        lay.addWidget(self.log, 1)
        self.gate = Gate(page, "push its edits into the game")
        self.body.addWidget(self.gate)

    @property
    def busy(self) -> bool:
        p = self.proc
        return p is not None and isValid(p) and p.state() != QProcess.ProcessState.NotRunning

    def command(self, flags: list[str]) -> list[str]:
        """The inject command line for the loaded section, the interpreter first."""
        ws = self.ws
        if ws.doc.directory is None or ws.scene is None:
            raise ValueError("save the document and load a section first")
        catch = f"{self.catch.value():g}"
        cmd = [sys.executable, "-c", MAIN, "map", "inject", str(ws.doc.directory)]
        cmd += ["--stage", str(ws.scene.stage), "--catch", catch]
        if ws.game is not None:
            cmd += ["--data", str(ws.game.root)]
        cmd += flags
        if "--collision" in flags and ws.session is not None and climbs(ws.session.ops):
            cmd += ["--hold", catch]
        return cmd

    def run(self, flags: list[str]) -> None:
        """Saves the document, then starts the push."""
        if self.busy or self.ws.session is None:
            return
        if not self.studio.save():
            self._say(f"not saved: {self.studio.message}")
            return
        cmd = self.command(flags)
        self.start(cmd[0], cmd[1:])

    def start(self, program: str, args: list[str]) -> None:
        studio = args[:2] == ["-c", MAIN]
        self.what = " ".join(["studio", *args[2:]] if studio else [program, *args])
        self._say("$ " + self.what)
        if self.proc is not None:
            self.proc.deleteLater()
        p = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")  # lines as they are printed, not at exit
        p.setProcessEnvironment(env)
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        p.readyReadStandardOutput.connect(self._read)
        p.finished.connect(self._finished)
        p.errorOccurred.connect(self._failed)
        self.proc = p
        p.start(program, args)
        self.studio.changed()

    def _read(self, *, rest: bool = False) -> None:
        """Whole lines as they come; with `rest`, what is left after the last one."""
        p = self.proc
        if p is None:
            return
        while p.canReadLine():
            self._say(text(p.readLine()).rstrip("\r\n"))
        if rest and p.bytesAvailable():
            self._say(text(p.readAll()))

    def _finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        self._read(rest=True)
        self._say(f"[exit {code}]")
        self.studio.message = f"push {'done' if code == 0 else f'failed ({code})'}"
        self.studio.changed()

    def _failed(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._say(f"could not start: {self.proc.errorString() if self.proc else error}")
            self.studio.changed()

    def _say(self, line: str) -> None:
        self.log.appendPlainText(plain(line))

    def _stop(self) -> None:
        p = self.proc
        if p is not None and self.busy:
            p.terminate()
            # the process is the timer's context: gone with it, the timer never fires
            QTimer.singleShot(KILL_AFTER, p, lambda: self._kill(p))

    def _kill(self, p: QProcess) -> None:
        if p is self.proc and self.busy:
            p.kill()

    def _clear(self) -> None:
        self.log.clear()

    def _save_as(self) -> None:
        path = dialogs.ask_save_as(self, self.ws)
        if path is not None:
            self.studio.save(path)

    def _warn(self) -> None:
        self.village.setVisible(in_village(self.ws) and self.catch.value() > 0)

    def sync(self) -> None:
        ws = self.ws
        if not self.gate.check(ws):
            return
        stage = ws.scene.stage if ws.scene else None
        if stage != self._stage:  # a section loaded: the catch's default follows it
            self._stage = stage
            with QSignalBlocker(self.catch):
                self.catch.setValue(0.0 if in_village(ws) else QUEST_CATCH)
        self._warn()
        saved = ws.doc.directory is not None
        self.unsaved.setVisible(not saved)
        self.save_as.setVisible(not saved)
        busy = self.busy
        for b in self.pushes:
            b.setEnabled(saved and not busy and ws.session is not None)
        self.stop.setEnabled(busy)
        self.running.setText(f"{plain(self.what)}  (running…)" if busy else plain(self.what))
