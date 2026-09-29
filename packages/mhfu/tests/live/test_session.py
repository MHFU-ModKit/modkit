import pytest
from mhfu import addresses as a
from mhfu.live import Launcher, Session, session


def test_reads_through_views(s, fake):
    fake.poke("B", a.SCREEN_STATE, 17)
    assert s.game.screen_state == 17
    s.game.player.hp = 55
    assert fake.peek("H", a.PLAYER_ENTITY + a.ENTITY.HP) == (55,)


def test_wait(s, clock):
    calls = iter([0, 0, 7])
    assert s.wait(lambda: next(calls), 5, "seven") == 7
    with pytest.raises(TimeoutError, match="no nothing within 2s"):
        s.wait(lambda: False, 2, "nothing")
    assert clock.now > 2


def test_holds(s):
    reads = iter([True, True, False])
    assert not s.holds(lambda: next(reads), 1.0)
    assert s.holds(lambda: True, 1.0)


def test_press_until(s, fake):
    fake.on_press.append(lambda b: len(fake.presses) == 3 and fake.poke("B", a.SCREEN_STATE, 1))
    assert s.press_until(lambda: s.game.screen_state, "menu", "start") == 1
    assert fake.presses == ["start"] * 3
    with pytest.raises(TimeoutError, match="after 2 cross presses"):
        s.press_until(lambda: False, "nothing", attempts=2)


def test_launcher_env(tmp_path):
    assert Launcher.from_env({}) == Launcher()
    env = {"MHFU_LAUNCHER": "docker", "MHFU_CONTAINER": "rig", "MHFU_ISO": "/iso/game.iso"}
    assert Launcher.from_env(env) == Launcher(docker=True, container="rig", iso="/iso/game.iso")
    with pytest.raises(ValueError, match="MHFU_LAUNCHER"):
        Launcher.from_env({"MHFU_LAUNCHER": "cloud"})
    binary = tmp_path / "PPSSPPSDL"
    binary.write_text("")
    assert Launcher(ppsspp=str(binary)).binary() == str(binary)
    with pytest.raises(FileNotFoundError, match="MHFU_PPSSPP"):
        Launcher(ppsspp=str(tmp_path / "missing")).binary()


def test_local_command(tmp_path):
    binary = tmp_path / "PPSSPPSDL"
    binary.write_text("")
    emu = Launcher(ppsspp=str(binary), iso="game.iso").local_emulator("x.ppst")
    assert emu.command == [str(binary), "--state=x.ppst", "game.iso"]
    with pytest.raises(FileNotFoundError, match="MHFU_ISO"):
        Launcher(ppsspp=str(binary)).local_emulator()


class Stub:
    """An emulator that is already listening on a port."""

    host = "127.0.0.1"

    def __init__(self, port: int, running: bool = False) -> None:
        self._port, self.is_running, self.calls = port, running, []

    def port(self, timeout: float = 0) -> int:
        return self._port

    def running(self) -> bool:
        return self.is_running

    def __getattr__(self, name):
        if name in ("start", "restart", "stop"):
            return lambda: self.calls.append(name)
        raise AttributeError(name)


@pytest.fixture
def local(monkeypatch, fake):
    stopped, stub = [], Stub(fake.port)
    monkeypatch.setattr(session, "find_debuggers", lambda: [fake.port])
    monkeypatch.setattr(session, "stop_local", lambda: stopped.append(True))
    monkeypatch.setattr(Launcher, "local_emulator", lambda self, state=None: stub)
    return stub, stopped


def test_launch_attaches_to_a_running_emulator(local):
    stub, stopped = local
    with Session.launch(Launcher(), timeout=5) as s:
        assert s.emulator is None
    assert (stub.calls, stopped) == ([], [])


def test_cold_launch_restarts(local):
    stub, stopped = local
    with Session.launch(Launcher(), cold=True, timeout=5) as s:
        assert s.emulator is stub
    assert (stub.calls, stopped) == (["start", "stop"], [True])


def test_state_launch_restarts_docker(monkeypatch, fake):
    stub = Stub(fake.port, running=True)
    monkeypatch.setattr(Launcher, "docker_emulator", lambda self, state=None: stub)
    with Session.launch(Launcher(docker=True), state="/config/x.ppst", timeout=5):
        pass
    assert stub.calls == ["restart", "stop"]


def test_docker_attach_leaves_the_emulator_running(monkeypatch, fake):
    stub = Stub(fake.port, running=True)
    monkeypatch.setattr(Launcher, "docker_emulator", lambda self, state=None: stub)
    with Session.launch(Launcher(docker=True), timeout=5) as s:
        assert s.emulator is None
    assert stub.calls == ["start"]
