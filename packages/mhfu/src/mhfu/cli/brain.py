# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu brain`: a big monster's AI script pushed into the running game, and back, or put on the
memory stick for the framework to load.

    mhfu brain status                # what is pushed, and which rows and monsters run it
    mhfu brain push 75               # em75's script as lifted, into the game
    mhfu brain push 75 F             # F edited (mhfu script 75 --source): checked, then pushed
    mhfu brain push 75 --row 76      # a sibling row the overlay serves
    mhfu brain restore 75            # the game's own script back
    mhfu brain build 75 F -o OUT     # F as the stick container, to place by hand
    mhfu brain deploy 75 F           # the container and the mod that installs it, on the stick
    mhfu brain deploy --doc brains/em75.toml    # the same for a brain file's edits (--row N)

push, build and deploy take the script as lifted, an edited source text F, or a brain file
(`--doc`, which names the overlay itself; docs/formats/brain_file.md). Each prints the findings,
one line each, and refuses while one is an error.

A push needs the game in a quest (the row's header words are set when it loads) and in PPSSPP's
64 MB mode, and lasts until the next quest load; over a stick build its monsters restart. Like
the other live commands, it takes the emulator the options pick: a lane's, the container's, or
the one running. A deploy needs no game: the framework installs the container at its next mod
load, and every quest after runs it. The stick is `--stick`, `--lane`'s, `$MHFU_LANE`'s,
`$MHFU_MEMSTICK` or the one found.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from itertools import groupby
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from ppsspp_debug import Lane

from ..em import script
from ..em.intel import SpeciesIntel
from ..em.script import brainfile, build, inject, stick
from ..files import Extracted
from ..live.session import Session
from .intel import species
from .live import launcher, launcher_args
from .script import report, vet

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser("brain", help="push an AI script into the running game, and back")
    modes = p.add_subparsers(dest="mode", required=True, metavar="MODE")

    c = modes.add_parser("status", help="what is pushed, and who runs it")
    launcher_args(c, paths=False)
    c.set_defaults(run=status)

    c = modes.add_parser("push", help="a species' script, as lifted or edited, into the game")
    source(c)
    live_args(c)
    c.set_defaults(run=push)

    c = modes.add_parser("restore", help="the game's own script back")
    target(c)
    live_args(c)
    c.set_defaults(run=restore)

    c = modes.add_parser("build", help="a species' script as a stick container file")
    source(c)
    c.add_argument("-o", "--out", type=Path, required=True, help="the container to write")
    c.set_defaults(run=build_cmd)

    c = modes.add_parser("deploy", help="a species' script and its mod onto the memory stick")
    source(c)
    where = c.add_mutually_exclusive_group()
    where.add_argument("--stick", type=Path, help="the memory stick's PSP folder (default: found)")
    where.add_argument("--lane", type=int, help="the memory stick of a hidden PPSSPP, numbered")
    c.set_defaults(run=deploy)


def target(c: argparse.ArgumentParser) -> None:
    """The overlay and its row."""
    c.add_argument("species", type=species, help="em overlay (em75, 75)")
    c.add_argument("--row", type=int, help="species row the overlay serves (default its own)")


def source(c: argparse.ArgumentParser) -> None:
    """The overlay and row, and the script to take: the row as lifted, an edited source text, or
    a brain file's edits."""
    c.add_argument("species", nargs="?", type=species, help="em overlay (em75, 75); --doc's own")
    c.add_argument("--row", type=int, help="species row the overlay serves (default its own)")
    what = c.add_mutually_exclusive_group()
    what.add_argument(
        "file", nargs="?", type=Path, help="edited source text (default: the row as lifted)"
    )
    what.add_argument(
        "--doc", type=Path, help="a brain file (brains/em75.toml): its edits to the row"
    )
    c.add_argument(
        "--intel", type=Path, help="--doc: the species' intel (`mhfu intel`), for PAIR_UNKNOWN"
    )
    c.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")


def live_args(c: argparse.ArgumentParser) -> None:
    """How long to wait for a tick, and where the emulator runs."""
    c.add_argument(
        "--timeout",
        type=float,
        default=inject.TIMEOUT,
        help="seconds to wait for a monster's tick before pausing the game instead",
    )
    launcher_args(c, paths=False)


def row_of(args: argparse.Namespace) -> int:
    return int(args.species if args.row is None else args.row)


class Pick(NamedTuple):
    """What a push, build or deploy takes, checked."""

    program: script.ir.Program
    image: script.asm.Image
    container: bytes
    built: build.Built | None
    """The brain file's build; None for the lifted row or a source text."""


def compiled(args: argparse.Namespace) -> build.Built:
    """The row the brain file `--doc` edits, built; its findings printed."""
    bf = brainfile.load(args.doc)
    if args.species is not None and args.species != bf.overlay:
        raise ValueError(f"{args.doc} is em{bf.overlay}'s, not em{args.species}'s")
    intel = None if args.intel is None else SpeciesIntel.load(args.intel)
    row = bf.overlay if args.row is None else args.row
    built = build.compile(bf, Extracted.find(args.data), row, intel=intel)
    report(built.findings)
    return built


def pick(args: argparse.Namespace) -> Pick | None:
    """The script to act on: the brain file's row, the row as the game has it, or a source text
    edited from it and checked; None when a finding is an error."""
    if args.doc is not None:
        built = compiled(args)
        if built.image is None or built.container is None:
            return None
        return Pick(built.program, built.image, built.container, built)
    if args.species is None:
        raise ValueError("name an em overlay (75, em75), or a brain file with --doc")
    sc = script.Scripts(Extracted.find(args.data), args.species)
    like = script.lift.lift(sc, row_of(args))
    prog = like if args.file is None else vet(args.file, like)
    if prog is None:
        return None
    img = script.asm.assemble(prog)
    return Pick(prog, img, stick.build(prog, img), None)


def live(args: argparse.Namespace, act: Callable[[Session], int]) -> int:
    """`act` on the game the options name, a failure of the game as an exit status."""
    try:
        with Session.launch(launcher(args), stop_on_exit=False) as s:
            return act(s)
    except (ConnectionError, RuntimeError, TimeoutError) as e:
        print(f"mhfu brain: {e}", file=sys.stderr)
        return 1


def push(args: argparse.Namespace) -> int:
    got = pick(args)
    if got is None:
        return 1

    def act(s: Session) -> int:
        pushed = inject.push(s, got.program, got.image, timeout=args.timeout)
        print(pushed.describe())
        return int(bool(pushed.mismatched))

    return live(args, act)


def restore(args: argparse.Namespace) -> int:
    def act(s: Session) -> int:
        pushed = inject.restore(s, row_of(args), timeout=args.timeout)
        print(pushed.describe())
        return int(bool(pushed.mismatched))

    return live(args, act)


def build_cmd(args: argparse.Namespace) -> int:
    got = pick(args)
    if got is None:
        return 1
    args.out.write_bytes(got.container)
    head = inject.Head.parse(got.container)
    print(f"{args.out}: row {head.row} id {head.id:08X}, {len(got.container)} bytes")
    return 0


def deploy(args: argparse.Namespace) -> int:
    got = pick(args)
    if got is None:
        return 1
    try:
        root = args.stick if args.lane is None else Lane(args.lane).stick
        if got.built is not None:
            written = stick.ship(got.built, root)
        else:
            written = stick.deploy(got.program, got.image, root)
    except FileNotFoundError as e:
        print(f"mhfu brain: {e}", file=sys.stderr)
        return 1
    print("\n".join(f"wrote {path}" for path in written))
    return 0


def status(args: argparse.Namespace) -> int:
    def act(s: Session) -> int:
        staged: dict[int, inject.Container | None] = {}

        def find(header: int) -> inject.Container | None:
            """The stick container a header is in, looked for once per header."""
            if header not in staged:
                staged[header] = inject.around(s.mem, header)
            return staged[header]

        print("\n".join(lines(inject.status(s.mem, find))))
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
        which = "pushed" if m.pushed else "native" if m.container is None else "stick"
        if m.container is not None and not m.pushed:
            which += f" {m.container.id:08X}"
        out.append(
            f"slot {m.slot:2d}  species {m.species:3d}  0x{m.entity:08X}  {which}  "
            f"context 0x{m.context:02X} pc {pc}"
        )
    return out
