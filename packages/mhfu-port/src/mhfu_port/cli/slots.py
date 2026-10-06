# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`slots` and `labels`: what each executor entry of a built port plays."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from mhfu.em.moveset import Moveset
from mhfu.files import monster_pac
from mhp_formats import Clip, fu

from .. import data, layout, manifest, motion, slots

if TYPE_CHECKING:
    from . import Subparsers


def register(sub: Subparsers) -> None:
    p = sub.add_parser(
        "slots",
        help="each host entry's (main, sub) pairs, lengths and donor clip in a port, as CSV",
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
    p.add_argument("-o", "--out", type=Path, help="CSV file (default: stdout)")
    data.add_arguments(p)


def _resolve(args: argparse.Namespace) -> manifest.Manifest | None:
    """Fill `--host` and `--donor` from the manifest where not given; the manifest."""
    m = manifest.load(args.manifest) if args.manifest else None
    if m is not None:
        args.host = m.port.host_species if args.host is None else args.host
        args.donor = m.source.anim if args.donor is None else args.donor
    if args.host is None:
        raise ValueError("give --host or --manifest")
    return m


def _layout(
    m: manifest.Manifest | None, donor: Mapping[int, Clip], host: fu.Anim, species: int
) -> dict[int, int]:
    """The manifest's layout of the donor's clips, else the packer's alone."""
    clips = m.clips if m is not None else {}
    return layout.plan(clips, donor, host, species).entries


def run_slots(args: argparse.Namespace) -> int:
    m = _resolve(args)
    d = data.from_arguments(args)
    host = slots.anim_of(d.fu.read(monster_pac(args.host)))
    drivers = slots.driven(Moveset(d.fu.em(args.host)))
    port = slots.anim_of(args.port.read_bytes()) if args.port else None
    donor = motion.moveset(d.p3rd.read(args.donor)) if args.donor is not None else None
    placed = _layout(m, donor, host, args.host) if donor is not None else None
    ids = {cid: e for e, cid in (placed or {}).items()}
    named = sorted(m.clips.items()) if m is not None else []
    names = {e: n for n, c in named if (e := layout.where(c, ids)) is not None}
    rows = slots.catalog(host, drivers, port, donor, placed, names)
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
                f"  no port entry holds the clip its layout puts there from donor file "
                f"{args.donor}: is that the moveset the port was built from?"
            )
    return 0


def run_labels(args: argparse.Namespace) -> int:
    m = _resolve(args)
    if args.donor is None:
        raise ValueError("give --donor or --manifest")
    d = data.from_arguments(args)
    labels = slots.read_labels(args.labels.read_text())
    builds = {}
    for path in args.ports:
        if path.stem in builds:
            raise ValueError(f"two builds named {path.stem}")
        builds[path.stem] = slots.anim_of(path.read_bytes())
    donor = motion.moveset(d.p3rd.read(args.donor))
    host = slots.anim_of(d.fu.read(monster_pac(args.host)))
    drivers = slots.driven(Moveset(d.fu.em(args.host)))
    rows = slots.verdicts(labels, builds, donor, _layout(m, donor, host, args.host), drivers)
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
