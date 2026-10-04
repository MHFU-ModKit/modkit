# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import sys

import pytest
from ppsspp_debug import DockerEmulator, LocalEmulator

LISTEN = """
import pathlib, socket, sys, time
s = socket.socket()
s.bind(("127.0.0.1", 0))
s.listen()
pathlib.Path(sys.argv[1]).write_text(str(s.getsockname()[1]))
time.sleep(60)
"""


def test_local_port_is_read_from_the_process(tmp_path):
    out = tmp_path / "port"
    with LocalEmulator(sys.executable, args=["-c", LISTEN, str(out)]) as emu:
        assert emu.port(10) == int(out.read_text())
        assert emu.running()
    assert not emu.running()


def test_local_exit_is_reported():
    with LocalEmulator(sys.executable, args=["-c", "raise SystemExit(3)"]) as emu:
        with pytest.raises(RuntimeError, match="code 3"):
            emu.port(10)


def test_local_without_a_port_times_out():
    with LocalEmulator(sys.executable, args=["-c", "import time; time.sleep(60)"]) as emu:
        with pytest.raises(TimeoutError, match="RemoteDebuggerOnStartup"):
            emu.port(0.3)


# stands in for `docker exec <container> ppsspp-ctl <verb> ...`; the first launch dies at once
FAKE_DOCKER = """#!/bin/sh
echo "$@" >> "$FAKE_DIR/calls"
case "$4" in
  status) [ -f "$FAKE_DIR/alive" ] && echo "running  pid=7" || echo stopped ;;
  start|restart)
    n=$(( $(cat "$FAKE_DIR/starts" 2>/dev/null || echo 0) + 1 ))
    echo $n > "$FAKE_DIR/starts"
    [ $n -ge 2 ] && touch "$FAKE_DIR/alive"
    echo started ;;
  stop) rm -f "$FAKE_DIR/alive"; echo stopped ;;
  log) echo "Could not find a graphics and a present queue" ;;
  screenshot) printf '\\211PNG' ;;
esac
"""


@pytest.fixture
def docker(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("the fake docker is a shell script")
    exe = tmp_path / "docker"
    exe.write_text(FAKE_DOCKER)
    exe.chmod(0o755)
    monkeypatch.setenv("FAKE_DIR", str(tmp_path))
    return exe


def test_docker_relaunches_a_dead_start(docker, tmp_path):
    emu = DockerEmulator("rig", game="/iso/game.iso", docker=str(docker), settle=0)
    emu.restart()
    assert emu.running()
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls == [
        "exec rig ppsspp-ctl restart --iso=/iso/game.iso",
        "exec rig ppsspp-ctl status",
        "exec rig ppsspp-ctl log",
        "exec rig ppsspp-ctl stop",
        "exec rig ppsspp-ctl start --iso=/iso/game.iso",
        "exec rig ppsspp-ctl status",
        "exec rig ppsspp-ctl status",
    ]
    assert emu.port() == 12345


def test_docker_gives_up(docker):
    emu = DockerEmulator("rig", docker=str(docker), tries=1, settle=0)
    with pytest.raises(RuntimeError, match="present queue"):
        emu.start()


def test_docker_screenshot(docker):
    assert DockerEmulator(docker=str(docker)).screenshot() == b"\x89PNG"
