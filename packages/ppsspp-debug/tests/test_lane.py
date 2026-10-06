# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import subprocess
import sys
import time

import pytest
from ppsspp_debug import Lane, emulators
from ppsspp_debug._lane import settings


@pytest.fixture
def stick(tmp_path):
    src = tmp_path / "stick"
    (src / "SYSTEM").mkdir(parents=True)
    (src / "SYSTEM/ppsspp.ini").write_text(
        "[General]\nRemoteISOPort = 62517\nPauseOnLostFocus = True\n[Graphics]\nFrameSkip = 0\n"
    )
    (src / "SAVEDATA/ULES01213").mkdir(parents=True)
    (src / "SAVEDATA/ULES01213/DATA.BIN").write_bytes(b"save")
    return src


def test_settings_set_and_add_under_general():
    out = settings("[General]\nA = 1\n[Other]\nB = 2\n", {"A": "9", "C": "3"})
    assert out == "[General]\nC = 3\nA = 9\n[Other]\nB = 2\n"


def test_prepare_clones_and_owns_the_port(tmp_path, stick):
    game = tmp_path / "game.iso"
    game.write_bytes(b"iso")
    lane = Lane(3, tmp_path / "lanes").prepare(game, stick)
    ini = (lane.stick / "SYSTEM/ppsspp.ini").read_text()
    assert "RemoteISOPort = 45103" in ini
    assert "PauseOnLostFocus = False" in ini
    assert "RemoteDebuggerOnStartup = True" in ini
    assert "FrameSkip = 0" in ini
    assert (lane.stick / "SAVEDATA/ULES01213/DATA.BIN").read_bytes() == b"save"
    assert lane.game.read_bytes() == b"iso"


def test_prepare_keeps_the_lane_stick_unless_fresh(tmp_path, stick):
    game = tmp_path / "game.iso"
    game.write_bytes(b"iso")
    lane = Lane(1, tmp_path / "lanes").prepare(game, stick)
    (lane.stick / "SAVEDATA/ULES01213/DATA.BIN").write_bytes(b"played")
    lane.prepare(game, stick)
    assert (lane.stick / "SAVEDATA/ULES01213/DATA.BIN").read_bytes() == b"played"
    lane.prepare(game, stick, fresh=True)
    assert (lane.stick / "SAVEDATA/ULES01213/DATA.BIN").read_bytes() == b"save"


def test_processes_are_found_by_their_game_image(tmp_path):
    lane = Lane(2, tmp_path / "lanes")
    sleeper = [sys.executable, "-c", "import time; time.sleep(30)", str(lane.game)]
    proc = subprocess.Popen(sleeper)
    try:
        deadline = time.monotonic() + 5
        while not lane.processes() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert [p.pid for p in lane.processes()] == [proc.pid]
        assert lane.stop() == 1
    finally:
        proc.kill()
        proc.wait()
    assert not lane.processes()


def test_lane_processes_are_not_free_emulators(monkeypatch, tmp_path):
    import ppsspp_debug._launch as launch

    monkeypatch.setattr(launch, "LANES", tmp_path / "lanes")
    assert launch._in_lane(["PPSSPPSDL", str(tmp_path / "lanes/1/game.iso")])
    assert not launch._in_lane(["PPSSPPSDL", str(tmp_path / "game.iso")])
    assert all(p.pid for p in emulators())
