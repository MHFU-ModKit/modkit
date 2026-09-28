from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from types import TracebackType

import psutil

StrPath = str | os.PathLike[str]


def find_debuggers() -> list[int]:
    """Debugger ports of the PPSSPP processes on this machine, read from their own sockets.

    PPSSPP silently takes a random port when its configured one is busy and saves that port
    back to ppsspp.ini, so the process is the only reliable source.
    """
    ports: set[int] = set()
    for proc in psutil.process_iter(["name"]):
        if "ppsspp" in (proc.info["name"] or "").lower():
            ports.update(_listening(proc))
    return sorted(ports)


def _listening(proc: psutil.Process) -> set[int]:
    try:
        conns = proc.net_connections("tcp")
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return set()
    return {c.laddr.port for c in conns if c.status == psutil.CONN_LISTEN}


class LocalEmulator:
    """PPSSPP as a child process of this one.

    PPSSPP opens the debugger at startup only with `RemoteDebuggerOnStartup = True` in its
    ppsspp.ini. A `state` launch skips the cold boot, so PRX plugins do not load.
    """

    host = "127.0.0.1"

    def __init__(
        self,
        binary: StrPath,
        game: StrPath | None = None,
        *,
        state: StrPath | None = None,
        args: Sequence[str] = (),
        log_file: StrPath | None = None,
    ) -> None:
        self.command = [
            os.fspath(binary),
            *(["-v", f"--log={os.fspath(log_file)}"] if log_file else []),
            *([f"--state={os.fspath(state)}"] if state else []),
            *args,
            *([os.fspath(game)] if game else []),
        ]
        self._proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if self.running():
            return
        self._proc = subprocess.Popen(
            self.command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def port(self, timeout: float = 30.0) -> int:
        """The debugger port, once the emulator listens on one."""
        if self._proc is None:
            raise RuntimeError("the emulator was not started")
        deadline = time.monotonic() + timeout
        while True:
            if (code := self._proc.poll()) is not None:
                raise RuntimeError(f"PPSSPP exited with code {code}")
            try:
                proc = psutil.Process(self._proc.pid)
                ports = sorted(set().union(*map(_listening, [proc, *proc.children(True)])))
            except psutil.NoSuchProcess:
                ports = []
            if ports:
                return ports[0]
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"PPSSPP opened no debugger port within {timeout:g}s; "
                    "is RemoteDebuggerOnStartup = True in ppsspp.ini?"
                )
            time.sleep(0.1)

    def stop(self, grace: float = 3.0) -> None:
        """Terminate the emulator and its helpers, killing them after `grace` seconds."""
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        _signal(proc, signal.SIGTERM)
        try:
            proc.wait(grace)
        except subprocess.TimeoutExpired:
            _signal(proc, signal.SIGKILL if sys.platform != "win32" else signal.SIGTERM)
            proc.wait(grace)

    def __enter__(self) -> LocalEmulator:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()


def _signal(proc: subprocess.Popen[bytes], sig: int) -> None:
    if sys.platform == "win32":
        proc.terminate()
        return
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


class DockerEmulator:
    """PPSSPP in the modkit's container (ppsspp/docker), driven through its `ppsspp-ctl`.

    `game` and `state` are paths inside the container. `ppsspp-ctl` pins the debugger port and
    reports ready only once the new process owns it.
    """

    def __init__(
        self,
        container: str = "ppsspp",
        *,
        game: str | None = None,
        state: str | None = None,
        host: str = "127.0.0.1",
        port: int = 12345,
        docker: str = "docker",
        tries: int = 4,
        settle: float = 2.5,
    ) -> None:
        """`settle` is how long a launch must survive to count, and the pause between tries."""
        self.container = container
        self.host = host
        self._port = port
        self._docker = docker
        self._launch = [
            *([f"--state={state}"] if state else []),
            *([f"--iso={game}"] if game else []),
        ]
        self._tries = tries
        self._settle = settle

    def _ctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self._docker, "exec", self.container, "ppsspp-ctl", *args],
            capture_output=True,
            text=True,
        )

    def start(self) -> None:
        """Start the emulator unless it already runs; that one may be anywhere in the game."""
        self._start("start")

    def restart(self) -> None:
        """Stop any running emulator and cold boot a new one."""
        self._start("restart")

    def _start(self, verb: str) -> None:
        # a Vulkan device pick on a virtual display can kill PPSSPP a second after it reports
        # ready, so check that it survived and relaunch if not
        last = ""
        for attempt in range(self._tries):
            r = self._ctl(verb, *self._launch)
            if r.returncode == 0:
                time.sleep(self._settle)
                if self.running():
                    return
                last = self.log()[-2000:]
            else:
                last = r.stdout + r.stderr
            self._ctl("stop")
            verb = "start"
            if attempt + 1 < self._tries:
                time.sleep(self._settle)
        raise RuntimeError(
            f"no live emulator in {self.container} after {self._tries} tries:\n{last}"
        )

    def running(self) -> bool:
        return self._ctl("status").stdout.startswith("running")

    def port(self, timeout: float = 30.0) -> int:
        """The pinned debugger port; `start()` returns only once it is open."""
        return self._port

    def stop(self) -> None:
        self._ctl("stop")

    def log(self) -> str:
        """The emulator's recent output."""
        return self._ctl("log").stdout

    def screenshot(self) -> bytes:
        """The container's display as a PNG; works where the debugger's screenshot does not."""
        command = [self._docker, "exec", self.container, "ppsspp-ctl", "screenshot"]
        return subprocess.run(command, capture_output=True, check=True).stdout

    def __enter__(self) -> DockerEmulator:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()
