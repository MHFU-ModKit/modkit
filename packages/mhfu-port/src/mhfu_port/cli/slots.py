# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`slots` and `labels`: what each animation slot of a built port plays."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from mhfu.em.moveset import Moveset
from mhfu.files import monster_pac
from mhp_formats import p3rd

from .. import data, manifest, slots

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser(
        "slots", help="each host slot's (main, sub) pairs, lengths and donor clip in a port, as CSV"
    )
    p.add_argument("port", type=Path, nargs="?", help="a built model PAC")
    _common(p)
    p.set_defaults(run=run_slots)

    q = sub.add_parser("labels", help="which hand labels describe a donor clip, per build, as CSV")
    q.add_argument("labels", type=Path, help="`N -> text` lines, N the a1 forced")
    q.add_argument("ports", type=Path, nargs="+", help="the builds the labels may come from")
    _common(q)
    q.set_defaults(run=run_labels)


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--manifest", type=Path, help="the port's: host, donor and clip names")
    p.add_argument("--host", type=int, metavar="SPECIES", help="the MHFU host")
    p.add_argument("--donor", type=int, metavar="FILE", help="MHP3rd moveset file")
    p.add_argument("--stream", type=int, default=0, help="the donor stream (default: 0)")
    p.add_argument("-o", "--out", type=Path, help="CSV file (default: stdout)")
    data.add_arguments(p)


def _resolve(args: argparse.Namespace) -> dict[int, str]:
    """Fill `--host` and `--donor` from the manifest where not given; the manifest's clip
    names by slot."""
    names: dict[int, str] = {}
    if args.manifest:
        m = manifest.load(args.manifest)
        args.host = m.port.host_species if args.host is None else args.host
        args.donor = m.source.anim if args.donor is None else args.donor
        for name, clip in sorted(m.clips.items()):
            names[clip.slot] = f"{names[clip.slot]} {name}" if clip.slot in names else name
    if args.host is None:
        raise ValueError("give --host or --manifest")
    return names


def run_slots(args: argparse.Namespace) -> int:
    names = _resolve(args)
    d = data.from_arguments(args)
    host = slots.anim_of(d.fu.read(monster_pac(args.host)))
    drivers = slots.driven(Moveset(d.fu.em(args.host)))
    port = slots.anim_of(args.port.read_bytes()) if args.port else None
    donor = p3rd.Anim.from_bytes(d.p3rd.read(args.donor)) if args.donor is not None else None
    rows = slots.catalog(host, drivers, port, donor, args.stream, names)
    with _output(args.out) as out:
        slots.write_catalog(rows, out)

    driven = [r for r in rows if r.pairs]
    silent = [r.slot for r in driven if not r.host_streams]
    say(f"{len(rows)} slots, {len(driven)} driven by a (main, sub) pair")
    if silent:
        say(f"  {len(silent)} driven slots have no host clip, so their pairs cannot animate:")
        say(f"    {_ids(silent)}")
    size = slots.slot_count(host)
    past = [a1 for a1 in drivers if a1 >= size]
    if past:
        say(f"  {len(past)} driven ids name no slot of the {size}: {_ids(past)}")
    if port is not None:
        most = max((r.shared for r in rows), default=0)
        say(f"  {sum(r.shared > 1 for r in rows)} port slots share their clip (at most {most})")
    held = [r.source for r in rows if r.source is not None]
    if held:
        n = {m: sum(s.match == m for s in held) for m in ("same", "fill", "unknown")}
        say(f"  port: {n['same']} own donor clip, {n['fill']} the fill, {n['unknown']} unknown")
        if not n["same"]:
            say(
                f"  no port slot holds its own clip of donor file {args.donor} stream "
                f"{args.stream}: is that the moveset the port was built from?"
            )
    return 0


def run_labels(args: argparse.Namespace) -> int:
    _resolve(args)
    if args.donor is None:
        raise ValueError("give --donor or --manifest")
    d = data.from_arguments(args)
    labels = slots.read_labels(args.labels.read_text())
    builds = {}
    for path in args.ports:
        if path.stem in builds:
            raise ValueError(f"two builds named {path.stem}")
        builds[path.stem] = slots.anim_of(path.read_bytes())
    donor = p3rd.Anim.from_bytes(d.p3rd.read(args.donor))
    drivers = slots.driven(Moveset(d.fu.em(args.host)))
    rows = slots.verdicts(labels, builds, donor, args.stream, drivers)
    with _output(args.out) as out:
        slots.write_verdicts(rows, list(builds), out)

    say(f"{len(rows)} labels over {len(builds)} builds")
    for name in builds:
        say(f"  {name}: {sum(name in v.transfers for v in rows)} hold their own donor clip")
    if len(builds) > 1:
        say(f"  {sum(len(v.transfers) == len(builds) for v in rows)} under every build")
    return 0


@contextmanager
def _output(path: Path | None) -> Iterator[TextIO]:
    if path is None:
        yield sys.stdout
        return
    with path.open("w", newline="") as f:
        yield f


def say(line: str) -> None:
    print(line, file=sys.stderr)


def _ids(ids: list[int]) -> str:
    return " ".join(map(str, ids))
