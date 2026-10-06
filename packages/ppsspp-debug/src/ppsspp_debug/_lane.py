# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Several PPSSPPs on one machine, each with its own memory stick, debugger port and game image.

PPSSPP keeps its memory stick in `$HOME/.config/ppsspp` on macOS and Linux, so a lane is a HOME
of its own. On macOS a lane starts hidden through LaunchServices and never takes focus. A process
started that way cannot read ~/Desktop, ~/Documents or ~/Downloads without a permission prompt
nobody sees (PPSSPP reports the game as an empty file), so the lane boots its own clone of the
game image.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import psutil

from ._launch import LANES, LocalEmulator, StrPath, _listening

PORT = 45100
"""Lane n listens on PORT + n."""
STICK = Path("~/.config/ppsspp/PSP").expanduser()
"""The memory stick a lane is cloned from."""
CLONED = ("SAVEDATA", "PLUGINS", "PPSSPP_STATE")
SETTINGS = {
    "RemoteDebuggerOnStartup": "True",
    "PauseOnLostFocus": "False",
    "PauseWhenMinimized": "False",
}


@dataclass(frozen=True)
class Lane:
    number: int
    root: Path = LANES

    @property
    def home(self) -> Path:
        return self.root / str(self.number)

    @property
    def stick(self) -> Path:
        return self.home / ".config/ppsspp/PSP"

    @property
    def port(self) -> int:
        return PORT + self.number

    @property
    def game(self) -> Path:
        return self.home / "game.iso"

    def prepare(self, game: StrPath, stick: StrPath = STICK, *, fresh: bool = False) -> Lane:
        """Clone in the game image and the stick's saves, plugins and states, once (again with
        `fresh`); set the ini every time, since PPSSPP rewrites it on exit."""
        src = Path(stick).expanduser()
        (self.stick / "SYSTEM").mkdir(parents=True, exist_ok=True)
        if fresh or not self.game.exists():
            _clone(Path(game), self.game)
        for name in CLONED:
            if (src / name).exists() and (fresh or not (self.stick / name).exists()):
                _clone(src / name, self.stick / name)
        controls = self.stick / "SYSTEM/controls.ini"
        if (src / "SYSTEM/controls.ini").exists() and (fresh or not controls.exists()):
            _clone(src / "SYSTEM/controls.ini", controls)
        ini = self.stick / "SYSTEM/ppsspp.ini"
        base = ini if ini.exists() and not fresh else src / "SYSTEM/ppsspp.ini"
        text = base.read_text(encoding="utf-8") if base.exists() else "[General]\n"
        ini.write_text(settings(text, {**SETTINGS, "RemoteISOPort": str(self.port)}), "utf-8")
        return self

    def emulator(
        self, binary: StrPath, *, state: StrPath | None = None, args: Sequence[str] = ()
    ) -> LocalEmulator | AppEmulator:
        """Hidden through LaunchServices where `binary` sits in a macOS app, else a child
        process with this lane's HOME."""
        if sys.platform == "darwin" and _bundle(binary) is not None:
            return AppEmulator(binary, self.game, home=self.home, state=state, args=args)
        env = {"HOME": os.fspath(self.home)}
        return LocalEmulator(binary, self.game, state=state, args=args, env=env)

    def processes(self) -> list[psutil.Process]:
        """The processes booting this lane's game image."""
        game = os.fspath(self.game)
        found = []
        for proc in psutil.process_iter(["cmdline"]):
            if game in (proc.info["cmdline"] or ()):
                found.append(proc)
        return found

    def debuggers(self) -> list[int]:
        """The ports this lane's emulator listens on."""
        return sorted(set().union(*map(_listening, self.processes())))

    def stop(self, grace: float = 3.0) -> int:
        """Stop this lane's emulator; returns how many processes."""
        return _terminate(self.processes(), grace)


class AppEmulator:
    """PPSSPP started hidden through LaunchServices (`open -j -g -n`), found again by its
    arguments since `open` returns before the app runs."""

    host = "127.0.0.1"

    def __init__(
        self,
        binary: StrPath,
        game: StrPath,
        *,
        home: StrPath,
        state: StrPath | None = None,
        args: Sequence[str] = (),
    ) -> None:
        bundle = _bundle(binary)
        if bundle is None:
            raise ValueError(f"{binary} is not inside a macOS app")
        self.bundle, self.home = bundle, Path(home)
        self.args = [*([f"--state={os.fspath(state)}"] if state else []), *args, os.fspath(game)]
        self._proc: psutil.Process | None = None

    def start(self, timeout: float = 15.0) -> None:
        if self.running():
            return
        env = f"HOME={self.home}"
        subprocess.run(
            ["open", "-j", "-g", "-n", "--env", env, "-a", self.bundle, "--args", *self.args],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + timeout
        while (proc := self._find()) is None:
            if time.monotonic() > deadline:
                raise RuntimeError(f"PPSSPP did not start within {timeout:g}s")
            time.sleep(0.1)
        self._proc = proc

    def _find(self) -> psutil.Process | None:
        for proc in psutil.process_iter(["cmdline"]):
            if (proc.info["cmdline"] or [])[1:] == self.args:
                return proc
        return None

    def running(self) -> bool:
        try:
            return self._proc is not None and self._proc.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    def port(self, timeout: float = 30.0) -> int:
        """The debugger port, once the emulator listens on one."""
        if self._proc is None:
            raise RuntimeError("the emulator was not started")
        deadline = time.monotonic() + timeout
        while True:
            if not self.running():
                raise RuntimeError("PPSSPP exited")
            if ports := sorted(_listening(self._proc)):
                return ports[0]
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"PPSSPP opened no debugger port within {timeout:g}s; "
                    "is RemoteDebuggerOnStartup = True in ppsspp.ini?"
                )
            time.sleep(0.1)

    def stop(self, grace: float = 3.0) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            _terminate([proc], grace)

    def __enter__(self) -> AppEmulator:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()


def settings(ini: str, values: Mapping[str, str]) -> str:
    """`ini` with each key set to its value; a key it lacks goes under [General]."""
    missing = dict(values)
    lines = []
    for line in ini.splitlines():
        key = line.partition("=")[0].strip()
        if key in missing:
            line = f"{key} = {missing.pop(key)}"
        lines.append(line)
    if missing:
        at = next((i + 1 for i, s in enumerate(lines) if s.strip() == "[General]"), None)
        if at is None:
            lines[:0], at = ["[General]"], 1
        lines[at:at] = [f"{k} = {v}" for k, v in missing.items()]
    return "\n".join(lines) + "\n"


def _bundle(binary: StrPath) -> Path | None:
    return next((p for p in Path(binary).parents if p.suffix == ".app"), None)


def _clone(src: Path, dst: Path) -> None:
    """Copy, as an APFS clone where the system can: free in space and time."""
    if dst.is_dir():
        shutil.rmtree(dst)
    elif dst.exists():
        dst.unlink()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.run(["cp", "-cR", os.fspath(src), os.fspath(dst)], check=True)
    elif src.is_dir():
        shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)


def _terminate(procs: list[psutil.Process], grace: float) -> int:
    for proc in procs:
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(procs, timeout=grace)
    for proc in alive:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
    return len(procs)
