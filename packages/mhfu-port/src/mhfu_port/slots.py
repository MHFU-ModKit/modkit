# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What each executor entry of a built port plays, and which (main, sub) pairs reach it.

The animation id `a1` a behaviour handler passes the executor IS the entry (`fk.entry_slot`
names its slot in each stream); so an entry is "the clip the engine plays when the brain enters
these pairs".
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TextIO

from mhfu.em.moveset import Moveset
from mhfu.entries import ENTRY_BANK, PART_STREAMS
from mhp_formats import AnimPack, Clip, Pac, fu

from .fk import entry_of
from .layout import Layout, LayoutError, played
from .motion import frames

Pair = tuple[int, int]
"""(main, sub): what act_set takes."""
Match = Literal["same", "fill", "unknown"]
Fingerprint = tuple[int, int]
"""(frames, loop flag)."""


def anim_of(pac: bytes) -> fu.Anim:
    """The animation pack of a monster PAC."""
    found = [e for e in Pac.from_bytes(pac).entries if e and fu.Anim.sniff(e)]
    if len(found) != 1:
        raise ValueError(f"a monster PAC holds one animation pack, this one {len(found)}")
    return fu.Anim.from_bytes(found[0])


def driven(ms: Moveset) -> dict[int, tuple[Pair, ...]]:
    """Animation id -> the pairs whose handler passes it as a literal."""
    out: dict[int, list[Pair]] = {}
    for key, pair in sorted(ms.pairs.items()):
        for a1 in ms.animations(pair).ids:
            out.setdefault(a1, []).append(key)
    return {a1: tuple(pairs) for a1, pairs in sorted(out.items())}


def fingerprint(clip: Clip) -> Fingerprint:
    """What a donor clip keeps through the port's bone remap: its length and loop flag."""
    return frames(clip), clip.loop


def slot_prints(anim: AnimPack) -> dict[int, Fingerprint]:
    """Entry -> fingerprint of the clip it plays; MHFU splits one clip across streams, so of its
    longest part."""
    out: dict[int, Fingerprint] = {}
    for si, stream in enumerate(anim.streams):
        for slot, clip in enumerate(stream):
            if clip is not None:
                e = entry_of(si, slot)
                out[e] = max(out.get(e, (0, 0)), fingerprint(clip))
    return out


def streams_of(anim: AnimPack, entry: int) -> tuple[int, ...]:
    """The streams with a clip for `entry`."""
    bank, slot = divmod(entry, ENTRY_BANK)
    return tuple(
        i
        for i, s in enumerate(anim.streams)
        if i % PART_STREAMS == bank and slot < len(s) and s[slot] is not None
    )


def shared(anim: AnimPack) -> dict[int, int]:
    """Entry -> how many entries of its stream play the same stored clip, the most over
    streams."""
    out: dict[int, int] = {}
    for si, stream in enumerate(anim.streams):
        count = Counter(id(c) for c in stream if c is not None)
        for slot, clip in enumerate(stream):
            if clip is not None:
                e = entry_of(si, slot)
                out[e] = max(out.get(e, 0), count[id(clip)])
    return out


@dataclass(frozen=True)
class Source:
    """The donor clip a port entry holds."""

    match: Match
    clip: int | None
    """The donor's MHP3rd clip id: the layout's for "same", the donor's first for "fill"."""

    def __str__(self) -> str:
        return self.match if self.clip is None else f"{self.match} {self.clip}"


def correspondence(port: AnimPack, donor: Mapping[int, Clip], layout: Layout) -> dict[int, Source]:
    """Port entry -> the donor clip it holds where the fingerprints agree: the one `layout` puts
    there (`layout.played`, so a cut compares cut), else the donor's first clip (an idle copy
    older builds filled with). An empty donor is an error, not an empty answer."""
    if not donor:
        raise ValueError("the donor has no clips")
    first = min(donor)
    out = {}
    for e, fp in slot_prints(port).items():
        cid = layout.entries.get(e)
        try:
            want = played(layout, donor, e)
        except LayoutError:
            want = None
        if cid is not None and want is not None and fingerprint(want) == fp:
            out[e] = Source("same", cid)
        elif cid != first and fingerprint(donor[first]) == fp:
            out[e] = Source("fill", first)
        else:
            out[e] = Source("unknown", None)
    return out


@dataclass(frozen=True)
class Row:
    """One executor entry of the host's tables."""

    slot: int
    pairs: tuple[Pair, ...]
    host_streams: tuple[int, ...]
    port_streams: tuple[int, ...]
    host_frames: int | None
    port_frames: int | None
    shared: int
    """Entries of the port playing this entry's stored clip: many for the fill."""
    source: Source | None
    clip: str = ""
    """The manifest's name for the clip in this entry."""


def catalog(
    host: AnimPack,
    drivers: Mapping[int, Collection[Pair]],
    port: AnimPack | None = None,
    donor: Mapping[int, Clip] | None = None,
    layout: Layout | None = None,
    names: Mapping[int, str] | None = None,
) -> list[Row]:
    """Every entry the host fills, the port fills or a pair drives; `source` needs the port,
    the donor and the layout, `names` names clips by entry."""
    source = (
        correspondence(port, donor, layout)
        if port is not None and donor is not None and layout is not None
        else {}
    )
    host_fp = slot_prints(host)
    port_fp = slot_prints(port) if port is not None else {}
    port_shared = shared(port) if port is not None else {}
    rows = []
    for slot in range(slot_count(host)):
        pairs = tuple(sorted(set(drivers.get(slot, ()))))
        in_port = streams_of(port, slot) if port is not None else ()
        in_host = streams_of(host, slot)
        if pairs or in_host or in_port:
            rows.append(
                Row(
                    slot,
                    pairs,
                    in_host,
                    in_port,
                    _frames(host_fp.get(slot)),
                    _frames(port_fp.get(slot)),
                    port_shared.get(slot, 0),
                    source.get(slot),
                    (names or {}).get(slot, ""),
                )
            )
    return rows


def slot_count(anim: AnimPack) -> int:
    """One past the last entry the pack's slot tables hold; an id past it names no slot."""
    return max((entry_of(si, len(s) - 1) + 1 for si, s in enumerate(anim.streams) if s), default=0)


CATALOG = (
    "slot",
    "driven_by",
    "pairs",
    "host_streams",
    "port_streams",
    "host_frames",
    "port_frames",
    "shared",
    "source",
    "clip",
)


def write_catalog(rows: Iterable[Row], out: TextIO) -> None:
    w = csv.writer(out, lineterminator="\n")
    w.writerow(CATALOG)
    for r in rows:
        w.writerow(
            (
                r.slot,
                _pairs(r.pairs),
                len(r.pairs),
                _join(r.host_streams),
                _join(r.port_streams),
                _opt(r.host_frames),
                _opt(r.port_frames),
                r.shared or "",
                _opt(r.source),
                r.clip,
            )
        )


_LABEL = re.compile(r"\s*(\d+)\s*->\s*(.*)")


def read_labels(text: str) -> dict[int, str]:
    """`N -> what the eye saw` lines, N the a1 forced; other lines are ignored."""
    return {int(m[1]): m[2].strip() for m in map(_LABEL.match, text.splitlines()) if m}


@dataclass(frozen=True)
class Verdict:
    """What each build held at a labelled entry."""

    slot: int
    label: str
    pairs: tuple[Pair, ...]
    builds: Mapping[str, Source | None]
    """Build name -> its clip in the entry; None where the entry is empty."""

    @property
    def transfers(self) -> tuple[str, ...]:
        """The builds where the label describes the donor clip the layout puts there."""
        return tuple(b for b, s in self.builds.items() if s is not None and s.match == "same")


def verdicts(
    labels: Mapping[int, str],
    builds: Mapping[str, AnimPack],
    donor: Mapping[int, Clip],
    layout: Layout,
    drivers: Mapping[int, Collection[Pair]] | None = None,
) -> list[Verdict]:
    """A label names what a build played in entry N: the layout's clip only where that build
    put it there."""
    held = {name: correspondence(anim, donor, layout) for name, anim in builds.items()}
    return [
        Verdict(
            slot,
            text,
            tuple(sorted(set((drivers or {}).get(slot, ())))),
            {name: h.get(slot) for name, h in held.items()},
        )
        for slot, text in sorted(labels.items())
    ]


def write_verdicts(rows: Iterable[Verdict], builds: Sequence[str], out: TextIO) -> None:
    w = csv.writer(out, lineterminator="\n")
    w.writerow(("slot", "label", "driven_by", *builds, "transfers"))
    for v in rows:
        cells = [str(v.builds[b] or "empty") for b in builds]
        w.writerow((v.slot, v.label, _pairs(v.pairs), *cells, _join(v.transfers)))


def _pairs(pairs: Iterable[Pair]) -> str:
    return " ".join(f"({m},{s})" for m, s in pairs)


def _join(items: Iterable[object]) -> str:
    return " ".join(map(str, items))


def _opt(v: object | None) -> str:
    return "" if v is None else str(v)


def _frames(fp: Fingerprint | None) -> int | None:
    return None if fp is None else fp[0]
