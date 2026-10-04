# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu.cli import main
from mhfu.live import Launcher, Session, session


@pytest.fixture(autouse=True)
def no_launcher_env(monkeypatch):
    for name in ("MHFU_LAUNCHER", "MHFU_PPSSPP", "MHFU_ISO", "MHFU_CONTAINER", "MHFU_DOCKER"):
        monkeypatch.delenv(name, raising=False)


def test_start(monkeypatch, fake, capsys):
    calls = []

    def launch(launcher, **kw):
        calls.append((launcher, kw))
        return Session.attach(fake.port, timeout=5)

    monkeypatch.setattr(Session, "launch", launch)
    assert main(["start", "--docker", "--container", "rig", "--state", "/config/a.ppst"]) == 0
    assert capsys.readouterr().out == f"{fake.port}\n"
    [(launcher, kw)] = calls
    assert launcher == Launcher(docker=True, container="rig")
    assert kw == {"state": "/config/a.ppst", "cold": False, "timeout": 60.0, "stop_on_exit": False}


def test_start_fails_cleanly(monkeypatch, capsys):
    def launch(launcher, **kw):
        raise TimeoutError("no game")

    monkeypatch.setattr(Session, "launch", launch)
    assert main(["start"]) == 1
    assert "mhfu start: no game" in capsys.readouterr().err


def test_stop_local(monkeypatch, capsys):
    monkeypatch.setattr(session, "stop_local", lambda: 2)
    assert main(["stop"]) == 0
    assert capsys.readouterr().out == "stopped 2 emulator process(es)\n"
