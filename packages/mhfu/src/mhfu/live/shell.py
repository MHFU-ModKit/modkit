# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The interactive debug shell on the running game, behind `mhfu shell`.

    shell = Shell(Session.attach())
    shell.onecmd("get mon 1 hp")      # prints to shell.stdout
    shell.loop()                      # the prompt, with history and completion

A command is a word path ("get mon", "anim play") whose arguments an argparse parser reads;
`help` lists them. The commands live in shell_game, shell_anim and shell_hit.
"""

from __future__ import annotations

import argparse
import cmd
import code
import contextlib
import shlex
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, NoReturn, Protocol

from .. import addresses as a
from ..files import Extracted
from ..memory import Memory
from ..structs import Entity, Game
from .session import Session

try:
    import readline
except ImportError:  # no line editing on this platform
    readline = None  # type: ignore[assignment]

HISTORY = Path.home() / ".mhfu_cli_history"

FREEZE_BITS = 0x10100
"""ENTITY.FREEZE_GATE bits that halt the AI tick."""


class CommandError(Exception):
    """A failure shown to the user as one line."""


class _Quit(Exception):
    pass


class Parser(argparse.ArgumentParser):
    """A command's argument parser; raises CommandError instead of exiting."""

    def error(self, message: str) -> NoReturn:
        raise CommandError(f"{message}; usage: {usage(self)}")


def usage(p: argparse.ArgumentParser) -> str:
    return " ".join(p.format_usage().removeprefix("usage: ").split())


Run = Callable[["Shell", argparse.Namespace], "str | None"]


@dataclass
class Command:
    words: tuple[str, ...]
    parser: Parser
    run: Run


SCENES = {int(v): n for n, v in a.table().addresses.items() if n.startswith("SCENE_")}


class Monster(Entity):
    """An entity as the shell reads it."""

    @property
    def frozen(self) -> bool:
        return bool(self.freeze_gate & FREEZE_BITS)


class Task(Protocol):
    def stop(self) -> None: ...


class Poller:
    """Calls `tick` every `period` seconds on a daemon thread until `stop`; the first exception
    ends it and is kept in `error`. The debugger client may be shared between threads."""

    def __init__(self, tick: Callable[[], None], period: float) -> None:
        self.tick = tick
        self.period = period
        self.error: Exception | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:
                self.error = e
                return
            self._stop.wait(self.period)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()


def table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    cells = [list(headers), *([str(c) for c in r] for r in rows)]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip() for r in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def integer(text: str) -> int:
    """An argparse type: decimal, 0x.., 0b.. or negative."""
    return int(text, 0)


def vec(v: Sequence[float]) -> str:
    return "(" + ", ".join(f"{c:.1f}" for c in v) + ")"


class Shell(cmd.Cmd):
    """The shell on one session; `data` is the extracted game the hitbox commands read."""

    prompt = "mhfu> "
    intro = "MHFU debug shell: `help` lists the commands, `py` opens Python, `quit` leaves."

    def __init__(
        self, session: Session, *, data: Path | None = None, stdout: IO[str] | None = None
    ) -> None:
        super().__init__(stdout=stdout)
        self.session = session
        self.data = data
        self.commands: dict[tuple[str, ...], Command] = {}
        self.tasks: dict[str, Task] = {}
        self.sight_radii: dict[int, float] = {}
        """Species sight radii `mon aggro off` zeroed, to restore."""
        from . import shell_anim, shell_game, shell_hit

        for register in (_builtins, shell_game.register, shell_anim.register, shell_hit.register):
            register(self)

    @property
    def game(self) -> Game:
        return self.session.game

    @property
    def mem(self) -> Memory:
        return self.session.mem

    def extracted(self) -> Extracted:
        try:
            return Extracted.find(self.data)
        except FileNotFoundError as e:
            raise CommandError(f"{e}; or start the shell with --data") from e

    def say(self, text: str) -> None:
        """Print now, for commands that report as they go."""
        self.stdout.write(text + "\n")
        self.stdout.flush()

    def command(self, words: str, help: str, run: Run) -> Parser:
        """Add a command; the caller adds its arguments to the parser returned."""
        p = Parser(prog=words, description=help, add_help=False)
        key = tuple(words.split())
        self.commands[key] = Command(key, p, run)
        return p

    def find(self, tokens: Sequence[str]) -> Command:
        """The command with the longest word path that starts `tokens`."""
        given = tuple(tokens)
        found = [c for w, c in self.commands.items() if given[: len(w)] == w]
        if found:
            return max(found, key=lambda c: len(c.words))
        near = sorted(" ".join(w) for w in self.commands if w[: len(given)] == given)
        hint = f"; one of: {', '.join(near)}" if near else "; try `help`"
        raise CommandError(f"unknown command {' '.join(given)!r}{hint}")

    def execute(self, line: str) -> str | None:
        """Run one command line and return its output; raises CommandError."""
        try:
            tokens = shlex.split(line)
        except ValueError as e:
            raise CommandError(str(e)) from e
        c = self.find(tokens)
        return c.run(self, c.parser.parse_args(tokens[len(c.words) :]))

    def onecmd(self, line: str) -> bool:
        line = line.strip()
        if line == "EOF":
            self.say("")
            return True
        if not line or line.startswith("#"):
            return False
        try:
            out = self.execute(line)
        except _Quit:
            return True
        except CommandError as e:
            out = f"! {e}"
        except KeyboardInterrupt:
            out = "! interrupted"
        except Exception as e:  # a failing command must not end the shell
            out = f"! {type(e).__name__}: {e}"
        if out:
            self.say(out)
        return False

    def completenames(self, text: str, *ignored: Any) -> list[str]:
        return sorted({w[0] for w in self.commands if w[0].startswith(text)})

    def completedefault(self, *args: Any) -> list[str]:
        text, line, begidx = args[0], args[1], args[2]
        done = tuple(line[:begidx].split())
        n = len(done)
        words = {w[n] for w in self.commands if len(w) > n and w[:n] == done}
        return sorted(w for w in words if w.startswith(text))

    def monster(self, slot: int) -> Monster:
        """The entity in registry slot `slot`."""
        entity = self.game.monsters().get(slot)
        if entity is None:
            raise CommandError(f"no entity in slot {slot} (`ls mon` lists them)")
        return Monster(entity.mem, entity.base)

    def monsters(self) -> dict[int, Monster]:
        return {k: Monster(e.mem, e.base) for k, e in self.game.monsters().items()}

    def start(self, name: str, task: Task) -> None:
        self.stop(name)
        self.tasks[name] = task

    def stop(self, name: str) -> Task | None:
        task = self.tasks.pop(name, None)
        if task is not None:
            task.stop()
        return task

    def stop_all(self) -> None:
        for name in list(self.tasks):
            self.stop(name)

    def loop(self) -> None:
        """The prompt, until quit, end of input or Ctrl-C, with history kept in HISTORY."""
        if readline is not None:
            with contextlib.suppress(OSError):
                readline.read_history_file(HISTORY)
            readline.set_completer_delims(" ")
            if "libedit" in (readline.__doc__ or ""):
                readline.parse_and_bind("bind ^I rl_complete")
        try:
            self.cmdloop()
        except KeyboardInterrupt:
            self.say("")
        finally:
            self.stop_all()
            if readline is not None:
                with contextlib.suppress(OSError):
                    readline.write_history_file(HISTORY)


# --- built-in commands ---


def _builtins(shell: Shell) -> None:
    p = shell.command("help", "list the commands, or show one", _help)
    p.add_argument("command", nargs="*")
    shell.command("py", "Python with s, game, player, monsters and run(line) preloaded", _py)
    p = shell.command("watch", "re-run a command every few seconds", _watch)
    p.add_argument("-n", type=int, default=15, help="times to run it")
    p.add_argument("-i", type=float, default=1.0, metavar="SEC", help="seconds between runs")
    p.add_argument("line", nargs=argparse.REMAINDER, metavar="command")
    p = shell.command("connect", "attach to the game again, as after an emulator restart", _connect)
    p.add_argument("--port", type=int, help="debugger port (default: the PPSSPP on this machine)")
    p.add_argument("--host", help="debugger host (default: the last one)")
    shell.command("quit", "leave", _quit)
    shell.command("exit", "leave", _quit)


def _help(shell: Shell, args: argparse.Namespace) -> str:
    if args.command:
        return shell.find(args.command).parser.format_help().rstrip()
    rows = [
        (usage(c.parser), c.parser.description or "") for _, c in sorted(shell.commands.items())
    ]
    width = max(len(u) for u, _ in rows)
    return "\n".join(f"  {u.ljust(width)}  {h}" for u, h in rows)


def _py(shell: Shell, args: argparse.Namespace) -> None:
    namespace = {
        "s": shell.session,
        "game": shell.game,
        "player": shell.game.player,
        "monsters": shell.monsters(),
        "a": a,
        "shell": shell,
        "run": shell.onecmd,
    }
    banner = "s, game, player, monsters, a (addresses), run('sys status'); Ctrl-D returns"
    code.interact(banner=banner, local=namespace, exitmsg="")


CLEAR_SCREEN = "\033[2J\033[H"


def _watch(shell: Shell, args: argparse.Namespace) -> None:
    if not args.line:
        raise CommandError(f"which command? usage: {usage(shell.commands['watch',].parser)}")
    line = shlex.join(args.line)
    for i in range(args.n):
        try:
            out = shell.execute(line)
        except CommandError as e:
            out = f"! {e}"
        clear = CLEAR_SCREEN if shell.stdout.isatty() else ""
        shell.say(f"{clear}watch [{i + 1}/{args.n}] {line}\n{out or ''}")
        if i + 1 < args.n:
            shell.session.sleep(args.i)


def _connect(shell: Shell, args: argparse.Namespace) -> str:
    host = args.host or shell.session.client.host
    shell.stop_all()
    with contextlib.suppress(Exception):
        shell.session.close()
    shell.session = Session.attach(args.port, host)
    return f"attached to port {shell.session.port}"


def _quit(shell: Shell, args: argparse.Namespace) -> NoReturn:
    raise _Quit
