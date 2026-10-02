# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Game jobs in the background: the studio's `Runner` on a QProcess, and the log window."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer
from PySide6.QtGui import QFontDatabase, QGuiApplication, QTextCursor
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPlainTextEdit, QVBoxLayout, QWidget

from mhfu_studio.ui import kit

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

#: how long a stopped job may take to end before it is killed
KILL_MS = 3000


class ProcessRunner(QObject):
    """Runs a job's command line; its output, both channels, goes line by line to the studio."""

    def __init__(self, studio: Studio, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.studio = studio
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")  # else a line shows only when the job ends
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(self._failed)
        self._kill = QTimer(self)
        self._kill.setSingleShot(True)
        self._kill.setInterval(KILL_MS)
        self._kill.timeout.connect(self.proc.kill)
        self._rest = b""

    def start(self, argv: list[str], stdin: bytes) -> None:
        self._rest = b""
        self.proc.start(argv[0], argv[1:])
        if stdin:
            self.proc.write(stdin)
        self.proc.closeWriteChannel()

    def stop(self) -> None:
        """Asks the job to end; kills it after `KILL_MS`."""
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.terminate()
            self._kill.start()

    def close(self) -> None:
        """Ends a running job and waits for it, the way `stop` does: the window is going."""
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.terminate()
            if not self.proc.waitForFinished(KILL_MS):
                self.proc.kill()
                self.proc.waitForFinished(KILL_MS)

    def _read(self) -> None:
        *lines, self._rest = (self._rest + self.proc.readAllStandardOutput().data()).split(b"\n")
        self._hear(lines)

    def _hear(self, lines: list[bytes]) -> None:
        for line in lines:
            self.studio.heard(line.decode(errors="replace").rstrip("\r"))
        if lines:
            self.studio.changed()

    def _finished(self, code: int, status: QProcess.ExitStatus) -> None:
        self._kill.stop()
        self._read()
        if self._rest:
            self._hear([self._rest])
            self._rest = b""
        self.studio.ended(code if status == QProcess.ExitStatus.NormalExit else -1)

    def _failed(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:  # no `finished` follows
            self.studio.heard(f"could not start: {self.proc.errorString()}")
            self.studio.ended(-1)


class JobLog(QDialog):
    """What the game jobs printed, as it comes, with Stop and Copy. Not modal."""

    def __init__(self, studio: Studio, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.studio = studio
        self.setWindowTitle("Game jobs")
        self.state = kit.label(role="muted", wrap=False)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.stop = kit.button(
            "Stop", tip="Stops the running job", on=studio.act("stop the job", studio.stop)
        )
        self.copy = kit.button("Copy", tip="Copies the whole log", on=self._copy)
        close = kit.button("Close", tip="Closes this window; a running job goes on", on=self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.state, 1)
        for b in (self.stop, self.copy, close):
            buttons.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addWidget(self.text, 1)
        lay.addLayout(buttons)
        self.resize(720, 400)
        self._shown: list[str] | None = None

    def sync(self) -> None:
        job = self.studio.job
        self.state.setText(f"running: {job.title}" if job is not None else "no job running")
        self.stop.setEnabled(job is not None)
        if self.studio.log != self._shown:
            self._shown = list(self.studio.log)
            self.text.setPlainText("\n".join(self._shown))
            self.text.moveCursor(QTextCursor.MoveOperation.End)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText("\n".join(self.studio.log))
