# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu rig points|goto|walk` against a fake game: the planning is `test_route`'s."""

import pytest
from mhfu.cli import main
from mhfu.cli import rig as cli_rig
from mhfu.live import Session, navigation, rig, route

MAP_TOML = """\
[map]
name = "snow"

[[point]]
name = "wall"
stage = 97
at = [10900.0, 650.0, 9100.0]
note = "the Tigrex sticks here"

[[point]]
name = "ledge"
stage = 99
at = [15400.0, 0.0, 7950.0]
kind = "climb"
heading = 157.0
"""


@pytest.fixture
def doc(tmp_path, monkeypatch):
    (tmp_path / "map.toml").write_text(MAP_TOML)
    monkeypatch.setenv("MHFU_MAP", str(tmp_path))
    return tmp_path


@pytest.fixture
def cli(fake, clock, monkeypatch):
    monkeypatch.setattr(rig, "running", lambda launcher: True)
    monkeypatch.setattr(Session, "launch", lambda *a, **kw: Session.attach(fake.port, timeout=5))
    monkeypatch.setattr(route.Map, "live", classmethod(lambda cls, s, game: cls((97,), {97: []})))
    monkeypatch.setattr(cli_rig.Extracted, "find", lambda path=None: None)
    return lambda *argv: main(["rig", *argv, "--lane", "1"])


def test_points(doc, capsys):
    assert main(["rig", "points"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split()[:3] == ["wall", "st097", "(10900,"]
    assert out[0].endswith("the Tigrex sticks here")
    assert "climb 157" in out[1]


def test_points_without_a_map(monkeypatch, capsys):
    monkeypatch.delenv("MHFU_MAP", raising=False)
    assert main(["rig", "points"]) == 1
    assert "MHFU_MAP" in capsys.readouterr().err


def test_goto(doc, cli, monkeypatch, capsys):
    went = []

    def goto(s, point, plan, log=None):
        went.append(point)
        return point.at

    monkeypatch.setattr(route, "goto", goto)
    assert cli("goto", "wall") == 0
    assert cli("goto", "--at", "98", "1", "2", "3") == 0
    assert [p.name for p in went] == ["wall", "--at"] and went[1].stage == 98
    assert capsys.readouterr().out.splitlines()[0] == "player -> (10900, 650.0, 9100)"
    assert cli("goto", "door") == 1


def test_walk(doc, cli, monkeypatch, capsys):
    calls = []

    def walk(s, point, plan, climbs=(), log=None):
        calls.append((point.name, [c.name for c in climbs]))
        return navigation.Walk(True, point.xz, 0.0, 1.0, "arrived")

    monkeypatch.setattr(route, "walk", walk)
    monkeypatch.setattr(navigation, "pose", lambda s: navigation.Pose(1.0, 2.0, 3.0, 0.0))
    assert cli("walk", "ledge", "wall", "--no-guard") == 0
    assert calls == [("ledge", ["ledge"]), ("wall", ["ledge"])]
    assert capsys.readouterr().out.splitlines()[-1].endswith("at (1, 2, 3)")


def test_walk_stops_short(doc, cli, monkeypatch):
    monkeypatch.setattr(
        route, "walk", lambda *a, **kw: navigation.Walk(False, (0.0, 0.0), 90.0, 1.0, "blocked")
    )
    assert cli("walk", "wall", "--no-guard") == 1
