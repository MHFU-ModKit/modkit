# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A running game: launch or attach to PPSSPP, read it through typed views, press its buttons.

    with Session.launch(cold=True) as s:      # MHFU_* environment, see Launcher
        s.game.screen_state
        s.press("cross")
        s.wait(lambda: s.game.scene == addresses.SCENE_MAIN_MENU, 30, "the main menu")

Every wait in `mhfu.live` goes through `Session.now`/`Session.sleep`, so tests can swap the clock.
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import TypeVar

import psutil
from ppsspp_debug import (
    AppEmulator,
    Button,
    Client,
    DockerEmulator,
    Lane,
    LocalEmulator,
    emulators,
    find_debuggers,
)

from .. import inject
from ..memory import Live
from ..structs import Game

T = TypeVar("T")
Emulator = LocalEmulator | AppEmulator | DockerEmulator

POLL = 0.1  # seconds between reads; every read stops the CPU, so faster polling stalls the game

# PPSSPP binaries tried, in order, when MHFU_PPSSPP is unset
BINARIES = (
    "~/.cache/modkit/ppsspp/src/build/PPSSPPSDL.app/Contents/MacOS/PPSSPPSDL",  # ppsspp/build.sh
    "PPSSPPSDL",
    "PPSSPPQt",
    "ppsspp",
    "/Applications/PPSSPPSDL.app/Contents/MacOS/PPSSPPSDL",
)


@dataclass(frozen=True)
class Launcher:
    """How to start PPSSPP. Paths are as the emulator sees them: inside the container for docker.

    Environment defaults: MHFU_LAUNCHER (local or docker), MHFU_PPSSPP (binary), MHFU_ISO
    (without it, docker boots the first image in /iso), MHFU_CONTAINER, MHFU_DOCKER (the CLI),
    MHFU_LANE (a local `Lane`: its own hidden PPSSPP, stick and port).
    """

    docker: bool = False
    ppsspp: str | None = None
    iso: str | None = None
    container: str = "ppsspp"
    docker_cli: str = "docker"
    lane: int | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Launcher:
        env = os.environ if env is None else env
        launcher = env.get("MHFU_LAUNCHER", "local")
        if launcher not in ("local", "docker"):
            raise ValueError(f"MHFU_LAUNCHER is {launcher!r}, not local or docker")
        return cls(
            docker=launcher == "docker",
            ppsspp=env.get("MHFU_PPSSPP"),
            iso=env.get("MHFU_ISO"),
            container=env.get("MHFU_CONTAINER", "ppsspp"),
            docker_cli=env.get("MHFU_DOCKER", "docker"),
            lane=ln.number if (ln := inject.lane(env)) else None,
        )

    def binary(self) -> str:
        """The local PPSSPP binary: MHFU_PPSSPP, else the first of BINARIES found."""
        for name in [self.ppsspp] if self.ppsspp else BINARIES:
            name = os.path.expanduser(name)
            found = shutil.which(name) or (name if Path(name).is_file() else None)
            if found:
                return found
        raise FileNotFoundError(f"no PPSSPP binary at {self.ppsspp or BINARIES}; set MHFU_PPSSPP")

    def docker_emulator(self, state: str | os.PathLike[str] | None = None) -> DockerEmulator:
        path = os.fspath(state) if state is not None else None
        return DockerEmulator(self.container, game=self.iso, state=path, docker=self.docker_cli)

    def local_emulator(
        self, state: str | os.PathLike[str] | None = None
    ) -> LocalEmulator | AppEmulator:
        """PPSSPP on this machine, in its lane if it has one (whose own image serves without
        MHFU_ISO); `state` loads at launch and skips the cold boot."""
        lane = Lane(self.lane) if self.lane is not None else None
        iso = self.iso or (lane.game if lane and lane.game.exists() else None)
        if iso is None:
            raise FileNotFoundError("no game image; set MHFU_ISO or pass iso=")
        if lane is not None:
            return lane.prepare(iso).emulator(self.binary(), state=state)
        return LocalEmulator(self.binary(), iso, state=state)

    def stop(self) -> int:
        """Stop the container's emulator, this lane's, or every PPSSPP on this machine outside a
        lane; returns how many."""
        if self.lane is not None:
            return Lane(self.lane).stop()
        if self.docker:
            emulator = DockerEmulator(self.container, docker=self.docker_cli)
            running = emulator.running()
            emulator.stop()
            return int(running)
        return stop_local()


def stop_local(grace: float = 3.0) -> int:
    """Terminate every PPSSPP process of this user outside a lane, killing those alive after
    `grace` s."""
    stopped = []
    for p in emulators():
        try:
            p.terminate()
            stopped.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _, alive = psutil.wait_procs(stopped, timeout=grace)
    for p in alive:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    return len(stopped)


class Session:
    """A connected game: `game` reads and writes memory, `press` sends input.

    With `stop_on_exit`, closing also stops the emulator this session started.
    """

    def __init__(
        self, client: Client, emulator: Emulator | None = None, *, stop_on_exit: bool = False
    ) -> None:
        self.client = client
        self.emulator = emulator
        self.stop_on_exit = stop_on_exit
        self.mem = Live(client)
        self.game = Game(self.mem)

    @classmethod
    def attach(
        cls,
        port: int | None = None,
        host: str = "127.0.0.1",
        *,
        timeout: float = 30.0,
        wait_for_game: bool = True,
    ) -> Session:
        """Connect to a running PPSSPP; without a port, the one on this machine."""
        client = Client.connect(host, port, timeout=timeout)
        try:
            if wait_for_game:
                # the debugger answers before the game has booted, and RAM is garbage until then
                client.wait_for_game(timeout)
        except BaseException:
            client.close()
            raise
        return cls(client)

    @classmethod
    def launch(
        cls,
        launcher: Launcher | None = None,
        *,
        state: str | os.PathLike[str] | None = None,
        cold: bool = False,
        timeout: float = 60.0,
        stop_on_exit: bool = True,
    ) -> Session:
        """Start the game, or attach to the one running.

        `cold` restarts a running emulator, since plugins load on a cold boot only. A `state`
        launch always restarts, because PPSSPP reads it at startup only; it skips the cold boot,
        so plugins do not load.
        """
        launcher = launcher or Launcher.from_env()
        fresh = cold or state is not None
        owned = True
        emulator: Emulator
        if launcher.docker:
            docker = launcher.docker_emulator(state)
            if fresh:
                docker.restart()
            else:
                owned = not docker.running()
                docker.start()
            emulator = docker
        else:
            lane = Lane(launcher.lane) if launcher.lane is not None else None
            if ports := lane.debuggers() if lane else find_debuggers():
                if not fresh:
                    one = lane is not None or len(ports) == 1
                    return cls.attach(ports[0] if one else None, timeout=timeout)
                launcher.stop()
            emulator = launcher.local_emulator(state)
            emulator.start()
        try:
            session = cls.attach(emulator.port(timeout), emulator.host, timeout=timeout)
        except BaseException:
            if owned:
                emulator.stop()
            raise
        session.emulator = emulator if owned else None
        session.stop_on_exit = stop_on_exit
        return session

    def close(self) -> None:
        """Disconnect, and stop the emulator if this session started it and `stop_on_exit`."""
        self.client.close()
        if self.emulator is not None and self.stop_on_exit:
            self.emulator.stop()

    def __enter__(self) -> Session:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def port(self) -> int:
        return self.client.port

    def press(self, button: Button, frames: int = 5) -> None:
        """Hold `button` for `frames` emulated frames; returns once it is released."""
        self.client.press(button, frames)

    @staticmethod
    def now() -> float:
        return time.monotonic()

    @staticmethod
    def sleep(seconds: float) -> None:
        time.sleep(seconds)

    def wait(self, done: Callable[[], T], timeout: float, what: str, poll: float = POLL) -> T:
        """Poll `done` until it returns something truthy, and return that."""
        deadline = self.now() + timeout
        while True:
            if value := done():
                return value
            if self.now() >= deadline:
                raise TimeoutError(f"no {what} within {timeout:g}s")
            self.sleep(poll)

    def holds(self, done: Callable[[], object], seconds: float, poll: float = POLL) -> bool:
        """True if `done` stays truthy for `seconds`; False at the first read that is not."""
        end = self.now() + seconds
        while True:
            if not done():
                return False
            if self.now() >= end:
                return True
            self.sleep(poll)

    def press_until(
        self,
        done: Callable[[], T],
        what: str,
        button: Button = "cross",
        *,
        frames: int = 5,
        attempts: int = 12,
        window: float = 0.9,
    ) -> T:
        """Press `button` until `done` returns something truthy, and return that.

        Menus swallow a press sent while they animate or debounce, so a single press is never
        enough: each press gets `window` seconds to take before the next.
        """
        for _ in range(attempts):
            if value := done():
                return value
            self.press(button, frames)
            try:
                return self.wait(done, window, what)
            except TimeoutError:
                continue
        if value := done():
            return value
        raise TimeoutError(f"no {what} after {attempts} {button} presses")
