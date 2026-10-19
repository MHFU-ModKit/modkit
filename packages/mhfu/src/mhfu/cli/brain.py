# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu brain`: a big monster's AI script pushed into the running game, and back.

    mhfu brain status                # what is pushed, and which rows and monsters run it
    mhfu brain push 75               # em75's script as lifted, into the game
    mhfu brain push 75 F             # F edited (mhfu script 75 --source): checked, then pushed
    mhfu brain push 75 --row 76      # a sibling row the overlay serves
    mhfu brain restore 75            # the game's own script back

The game must be in a quest (the row's header words are set when it loads) and in PPSSPP's 64 MB
mode. A push lasts until the next quest load. Like the other live commands, it takes the emulator
the options pick: a lane's, the container's, or the one running.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from itertools import groupby
from pathlib import Path
from typing import TYPE_CHECKING

from ..em import script
from ..em.script import inject
from ..files import Extracted
from ..live.session import Session
from .intel import species
from .live import launcher, launcher_args
from .script import vet

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser("brain", help="push an AI script into the running game, and back")
    modes = p.add_subparsers(dest="mode", required=True, metavar="MODE")

    c = modes.add_parser("status", help="what is pushed, and who runs it")
    launcher_args(c, paths=False)
    c.set_defaults(run=status)

    c = modes.add_parser("push", help="a species' script, as lifted or edited, into the game")
    target(c)
    c.add_argument(
        "file", nargs="?", type=Path, help="edited source text (default: the row as lifted)"
    )
    c.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")
    c.set_defaults(run=push)

    c = modes.add_parser("restore", help="the game's own script back")
    target(c)
    c.set_defaults(run=restore)


def target(c: argparse.ArgumentParser) -> None:
    """The overlay, its row, how long to wait for a tick, and where the emulator runs."""
    c.add_argument("species", type=species, help="em overlay (em75, 75)")
    c.add_argument("--row", type=int, help="species row the overlay serves (default its own)")
    c.add_argument(
        "--timeout",
        type=float,
        default=inject.TIMEOUT,
        help="seconds to wait for a monster's tick before pausing the game instead",
    )
    launcher_args(c, paths=False)


def program(args: argparse.Namespace, row: int) -> script.ir.Program | None:
    """The program to push: the row as the game has it, or FILE edited from it and checked;
    None when a finding is an error."""
    sc = script.Scripts(Extracted.find(args.data), args.species)
    like = script.lift.lift(sc, row)
    return like if args.file is None else vet(args.file, like)


def live(args: argparse.Namespace, act: Callable[[Session], int]) -> int:
    """`act` on the game the options name, a failure of the game as an exit status."""
    try:
        with Session.launch(launcher(args), stop_on_exit=False) as s:
            return act(s)
    except (ConnectionError, RuntimeError, TimeoutError) as e:
        print(f"mhfu brain: {e}", file=sys.stderr)
        return 1


def push(args: argparse.Namespace) -> int:
    prog = program(args, args.species if args.row is None else args.row)
    if prog is None:
        return 1
    image = script.asm.assemble(prog)

    def act(s: Session) -> int:
        pushed = inject.push(s, prog, image, timeout=args.timeout)
        print(pushed.describe())
        return int(bool(pushed.mismatched))

    return live(args, act)


def restore(args: argparse.Namespace) -> int:
    def act(s: Session) -> int:
        pushed = inject.restore(
            s, args.species if args.row is None else args.row, timeout=args.timeout
        )
        print(pushed.describe())
        return int(bool(pushed.mismatched))

    return live(args, act)


def status(args: argparse.Namespace) -> int:
    def act(s: Session) -> int:
        print("\n".join(lines(inject.status(s.mem))))
        return 0

    return live(args, act)


def spans(numbers: Sequence[int]) -> str:
    """`0-2, 5`: the numbers as runs."""
    out = []
    for _, group in groupby(enumerate(numbers), lambda t: t[1] - t[0]):
        run = [n for _, n in group]
        out.append(str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}")
    return ", ".join(out)


def lines(now: inject.Status) -> list[str]:
    """The status as text, a line each."""
    if not now.mapped:
        out = [f"SCRIPT_PUSH: {inject.NEEDS_64}"]
    elif now.container is not None:
        c = now.container
        out = [f"SCRIPT_PUSH: row {c.row} id {c.id:08X}, {c.extent} bytes, image at 0x{c.load:08X}"]
    elif now.foreign:
        out = ["SCRIPT_PUSH: holds something that is no script container"]
    else:
        out = ["SCRIPT_PUSH: nothing pushed"]
    out += [f"row {row} runs it on map types {spans(kinds)}" for row, kinds in now.rows.items()]
    for m in now.monsters:
        pc = "-" if m.pc is None else f"0x{m.pc:08X}"
        which = "pushed" if m.pushed else "native"
        out.append(
            f"slot {m.slot:2d}  species {m.species:3d}  0x{m.entity:08X}  {which}  "
            f"context 0x{m.context:02X} pc {pc}"
        )
    return out
