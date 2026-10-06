# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu.cli import main
from mhfu.live import Launcher, Session, boot, hall, quests


@pytest.fixture
def run(monkeypatch, fake):
    """`mhfu go-on-quest` on the fake, with the flows replaced by a log of what ran."""
    calls = []
    launched = []

    def launch(launcher, **kw):
        launched.append((launcher, kw))
        return Session.attach(fake.port, timeout=5)

    card = quests.Card(
        3, "Hunt the Velocidrome", "Hunt a Velocidrome", "", "", ("Velocidrome",), ""
    )
    monkeypatch.setattr(Session, "launch", launch)
    monkeypatch.setattr(boot, "to_village", lambda s, language: calls.append(("village", language)))
    monkeypatch.setattr(hall, "in_hall", lambda s: False)
    monkeypatch.setattr(hall, "enter", lambda s: calls.append(("enter hall",)) or True)
    monkeypatch.setattr(
        quests, "open_board", lambda s, rank, **kw: calls.append(("board", rank, kw))
    )
    monkeypatch.setattr(quests, "list_quests", lambda s: [card])

    def take(s, rank, name, **kw):
        kw.pop("log")
        calls.append(("take", rank, name, kw))
        return quests.Taken(card, 98 if kw["leave"] else None)

    monkeypatch.setattr(quests, "take", take)
    for name in (
        "MHFU_LAUNCHER",
        "MHFU_PPSSPP",
        "MHFU_ISO",
        "MHFU_CONTAINER",
        "MHFU_DOCKER",
        "MHFU_LANE",
    ):
        monkeypatch.delenv(name, raising=False)

    def go(*argv):
        calls.clear()
        return main(["go-on-quest", *argv]), calls, launched

    return go


def test_to_the_quest(run, capsys):
    code, calls, launched = run("--rank", "1", "--quest", "Velocidrome", "--docker")
    assert code == 0
    assert calls == [
        ("village", "english"),
        ("take", 1, "Velocidrome", {"board": "elder", "counter": "counter_low", "leave": True}),
    ]
    [(launcher, kw)] = launched
    assert launcher == Launcher(docker=True)
    assert kw == {"cold": True, "stop_on_exit": False}
    assert "area index 98" in capsys.readouterr().out


def test_until_village(run):
    code, calls, _ = run("--until", "village", "--language", "deutsch", "--stop")
    assert code == 0 and calls == [("village", "deutsch")]


def test_list_at_the_hall(run, capsys):
    code, calls, _ = run("--board", "hall", "--counter", "counter_high", "--list")
    assert code == 0
    assert calls[1:] == [
        ("enter hall",),
        ("board", 0, {"board": "hall", "counter": "counter_high"}),
    ]
    assert " 3  Hunt the Velocidrome" in capsys.readouterr().out


def test_no_depart(run):
    code, calls, _ = run("--quest", "velo", "--no-depart")
    assert code == 0
    assert calls[-1][3]["leave"] is False


@pytest.mark.parametrize("argv", [[], ["--slot", "1", "--list"]])
def test_refuses_before_booting(run, argv, capsys):
    code, calls, launched = run(*argv)
    assert code == 2 and not calls and not launched
    assert "mhfu go-on-quest:" in capsys.readouterr().err


def test_a_failure_is_one_line(run, monkeypatch, capsys):
    def take(s, rank, name, **kw):
        raise quests.NoContractError("no contract for 'velo' in 3 attempts")

    monkeypatch.setattr(quests, "take", take)
    code, _, _ = run("--quest", "velo")
    assert code == 1
    assert capsys.readouterr().err == "mhfu go-on-quest: no contract for 'velo' in 3 attempts\n"
