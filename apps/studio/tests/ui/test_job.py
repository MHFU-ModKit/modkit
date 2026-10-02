# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import sys
from collections.abc import Callable
from typing import Any

import pytest
from mhfu_studio.shell import studio as studio_module
from mhfu_studio.shell.workspace import Job
from mhfu_studio.ui.job import ProcessRunner
from mhfu_studio.ui.testing import FakeWorkspace
from mhfu_studio.ui.window import Window
from PySide6.QtGui import QGuiApplication

Make = Callable[..., Window]
LONG = 60_000


class Sender(FakeWorkspace):
    def __init__(self, job: Job | None = None, why: str | None = None) -> None:
        super().__init__("map", ".toml")
        self.job, self.why = job, why

    def send_blocker(self) -> str | None:
        return self.why

    def send(self) -> Job | None:
        return self.job


def python(monkeypatch: pytest.MonkeyPatch, code: str) -> None:
    """Every job runs `code` instead of the studio's command line."""
    monkeypatch.setattr(studio_module, "command", lambda job: [sys.executable, "-c", code])


def test_send_runs_a_job(make_window: Make, qtbot: Any) -> None:
    w = make_window(Sender(Job("help", ("--help",))), FakeWorkspace("monster"))
    s = w.studio
    assert isinstance(s.runner, ProcessRunner)
    assert w.bar.send.isEnabled() and w.bar.send.text() == "Send to game"
    w.send_action.trigger()
    w.sync()
    assert s.job is not None and w.bar.send.text() == "Stop" and w.bar.send.property("busy")
    assert w.bar.send.toolTip() == "Stops help" and s.message == "help…"
    qtbot.waitUntil(lambda: s.job is None, timeout=LONG)
    w.sync()
    assert s.message == "help: done" and any("usage" in line for line in s.log)
    assert w.bar.send.text() == "Send to game" and not w.bar.send.property("busy")
    log = w.show_log()
    assert "usage" in log.text.toPlainText() and not log.stop.isEnabled()
    log.copy.click()
    assert QGuiApplication.clipboard().text() == "\n".join(s.log)


def test_send_blocked(make_window: Make) -> None:
    w = make_window(Sender(why="no section loaded"), FakeWorkspace("monster"))
    assert not w.bar.send.isEnabled() and w.bar.send.toolTip() == "no section loaded"
    assert not w.send_action.isEnabled()
    w.studio.switch("monster")
    w.sync()
    assert w.bar.send.toolTip() == "nothing here goes to the game"


def test_stdin_lines(make_window: Make, qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    python(monkeypatch, "import sys; print(sys.stdin.read().upper(), end='')")
    w = make_window()
    w.studio.start(Job("shout", (), b"one\r\ntwo"))
    qtbot.waitUntil(lambda: w.studio.job is None, timeout=LONG)
    assert w.studio.log[-3:] == ["ONE", "TWO", "[exit 0]"]


def test_stop(make_window: Make, qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    python(monkeypatch, "import time; print('up'); time.sleep(60)")
    w = make_window()
    s = w.studio
    s.start(Job("nap", ()))
    qtbot.waitUntil(lambda: "up" in s.log, timeout=LONG)
    log = w.show_log()
    assert log.stop.isEnabled() and "up" in log.text.toPlainText()
    log.stop.click()
    qtbot.waitUntil(lambda: s.job is None, timeout=LONG)
    assert s.message == "nap: stopped"


def test_start_fails(make_window: Make, qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(studio_module, "command", lambda job: ["/nonexistent/studio"])
    w = make_window()
    w.studio.start(Job("ghost", ()))
    qtbot.waitUntil(lambda: w.studio.job is None, timeout=LONG)
    assert w.studio.log[-2].startswith("could not start") and w.studio.log[-1] == "[exit -1]"
    assert w.studio.message == "ghost: failed (-1), see the log"


def test_close_ends_the_job(make_window: Make, monkeypatch: pytest.MonkeyPatch) -> None:
    python(monkeypatch, "import time; time.sleep(60)")
    w = make_window()
    w.studio.start(Job("nap", ()))
    assert w.close() and w.studio.job is None


def test_message_tip(make_window: Make) -> None:
    w = make_window()
    w.studio.message = "x" * 400
    w.sync()
    assert w.message.toolTip() == "x" * 400
