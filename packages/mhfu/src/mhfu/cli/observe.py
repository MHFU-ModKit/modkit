# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu observe trace`, `cost` and `path`: what a big monster's overlay calls in the engine, what
the engine does to the monster per (main, sub) pair, what tracing costs, and where it goes."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import addresses as a
from ..em.abi import Engine
from ..files import Extracted
from ..live import clips, observe, survival
from ..live.session import Session
from ..views import layout
from .live import launcher, launcher_args

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser("observe", help="a big monster's engine calls per (main, sub) pair, live")
    modes = p.add_subparsers(dest="mode", required=True, metavar="MODE")

    t = modes.add_parser("trace", help="callees per pair, and the entity's changes per pair")
    _common(t)
    t.add_argument(
        "--callees",
        default="all",
        help="all, none, or a comma list of addresses and addresses.toml names",
    )
    t.add_argument("--indirect", action="store_true", help="also the overlay's register calls")
    t.add_argument(
        "--engine",
        default="",
        help="functions kept when a register holds the monster: ADDR or NAME, @REG for not a0",
    )
    t.add_argument(
        "--writes", default="", help="ENTITY fields whose writers to log: NAME or OFFSET[:SIZE]"
    )
    t.add_argument("--budget", type=int, help="breakpoints per run; more runs each replay --state")
    t.add_argument("--rate", type=float, default=2.0, help="snapshots a second (0: ends only)")
    t.add_argument("--json", type=Path, help="write the runs and the per-pair table here")
    t.set_defaults(run=trace)

    c = modes.add_parser("cost", help="emulation speed against the number of traced callees")
    _common(c, speed="max")
    c.add_argument("--counts", default="0,50,100,all", help="callee counts, the most-called first")
    c.set_defaults(run=cost)

    w = modes.add_parser("path", help="a big monster's position, walls and clip every AI frame")
    launcher_args(w)
    w.add_argument("--slot", type=int, help="registry slot (default the first big monster)")
    w.add_argument("--seconds", type=float, default=5.0, help="emulated seconds")
    w.add_argument("--csv", type=Path, help="write every frame here")
    w.set_defaults(run=path)


def _common(p: argparse.ArgumentParser, speed: str | None = None) -> None:
    launcher_args(p)
    p.add_argument("--state", help="savestate loaded before each run, as the emulator sees it")
    p.add_argument("--species", type=int, default=75, help="overlay species (em number)")
    p.add_argument("--seconds", type=float, default=30.0, help="emulated seconds a run")
    p.add_argument("--hp", type=int, default=100, help="pin the player's HP here (0: leave it)")
    p.add_argument("--speed", default=speed, help="emulation speed: a percent, or max")
    p.add_argument("--data", type=Path, help="extracted game (default: $MHFU_DATA)")


@contextmanager
def _observer(args: argparse.Namespace) -> Iterator[observe.Observer]:
    engine = Engine.load(Extracted.find(args.data))
    with Session.launch(launcher(args), stop_on_exit=False) as s:
        obs = observe.Observer(s, engine, args.species)
        if args.speed is not None:
            percent = None if args.speed == "max" else int(args.speed)
            s.client.set_speed(percent, fast_forward=args.speed == "max")
        hp = args.hp or None
        try:
            with survival.Guard(s, hp=hp, calm_monsters=False, tick=0.2, log=_err) as guard:
                yield obs
            _err(guard.report())
        finally:
            if args.speed is not None:
                s.client.set_speed()


def _err(line: str) -> None:
    print(line, file=sys.stderr)


def _address(item: str) -> int:
    table = a.table().addresses
    return int(table[item]) if item in table else int(item, 0)


def _items(spec: str) -> list[str]:
    return [item.strip() for item in spec.split(",") if item.strip()]


def _callees(spec: str, obs: observe.Observer) -> list[int]:
    if spec in ("all", "none"):
        return list(obs.callees) if spec == "all" else []
    return [_address(item) for item in _items(spec)]


def _engine(spec: str) -> dict[int, str]:
    """ADDR or NAME, each with an optional @REG (default a0)."""
    out = {}
    for item in _items(spec):
        where, _, reg = item.partition("@")
        out[_address(where)] = reg or "a0"
    return out


def _writes(spec: str) -> list[tuple[int, int]]:
    """ENTITY field names (sized by their type) or OFFSET[:SIZE] (4 by default)."""
    out = []
    for item in _items(spec):
        where, _, size = item.partition(":")
        field = a.ENTITY.fields.get(where)
        if field is not None:
            fmt = layout(field.type)
            out.append((int(field), int(size or 0) or (fmt.size if fmt else 4)))
        else:
            out.append((int(where, 0), int(size or 4)))
    return out


def trace(args: argparse.Namespace) -> int:
    with _observer(args) as obs:
        targets = _callees(args.callees, obs)
        groups = observe.batches(targets, args.budget)
        if len(groups) > 1 and args.state is None:
            _err("no --state: the runs are consecutive windows, not replays of one")
        runs = []
        for n, group in enumerate(groups, 1):
            _err(f"run {n}/{len(groups)}")
            runs.append(
                obs.run(
                    group,
                    seconds=args.seconds,
                    indirect=obs.indirect if args.indirect else (),
                    engine=_engine(args.engine),
                    writes=_writes(args.writes),
                    rate=args.rate,
                    state=args.state,
                    log=_err,
                )
            )
    print(observe.report(runs))
    if any(run.writes for run in runs):
        print(observe.writes_report(runs))
    for run in runs:
        print(observe.changes_report(observe.pair_changes(run.snapshots)))
    for n, run in enumerate(runs, 1):
        print(
            f"run {n}: {len(run.targets)} callee(s), speed {run.speed or 0:.3f}x, "
            f"{run.fps or 0:.0f} fps, {run.dropped} dropped, {run.unparsed} unparsed"
        )
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "runs": [dataclasses.asdict(r) for r in runs],
            "pairs": [dataclasses.asdict(p) for p in observe.by_pair(runs)],
        }
        args.json.write_text(json.dumps(data, default=_json), encoding="utf-8")
    return 0


def _json(value: Any) -> Any:
    return value.hex() if isinstance(value, bytes) else str(value)


def cost(args: argparse.Namespace) -> int:
    with _observer(args) as obs:
        total = len(obs.callees)
        counts = [total if c == "all" else min(int(c), total) for c in args.counts.split(",")]
        results = observe.cost(obs, counts, seconds=args.seconds, state=args.state, log=_err)
    print("breakpoints  speed    fps  calls")
    for r in results:
        print(f"{r.breakpoints:11}  {r.speed or 0:5.2f}x  {r.fps or 0:5.1f}  {r.calls}")
    return 0


def path(args: argparse.Namespace) -> int:
    with Session.launch(launcher(args), stop_on_exit=False) as s:
        _, m = clips.monster(s, args.slot)
        frames = observe.track(s, m.base, args.seconds)
    if args.csv:
        observe.write_frames(frames, args.csv)
    print(observe.legs_report(observe.legs(frames)))
    return 0 if frames else 1
