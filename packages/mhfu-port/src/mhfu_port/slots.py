# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What each animation slot of a built port plays, and which (main, sub) pairs reach it.

The animation id `a1` a behaviour handler passes the executor IS the slot index, in every
stream; so a slot is "the clip the engine plays when the brain enters these pairs".
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TextIO

from mhfu.em.moveset import Moveset
from mhp_formats import AnimPack, Clip, Pac, fu

from .motion import donor_slot

Pair = tuple[int, int]
"""(main, sub): what act_set takes."""
Match = Literal["same", "fill", "unknown"]
Fingerprint = tuple[int, int]
"""(frames, loop flag)."""


def frames(clip: Clip) -> int:
    """The last keyframe: a clip's length, which the port's bone remap keeps."""
    return max((k.frame for t in clip.tracks for c in t.channels for k in c.keyframes), default=0)


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
    """Slot -> fingerprint of the clip it plays; MHFU splits one clip across streams, so of its
    longest part."""
    out: dict[int, Fingerprint] = {}
    for stream in anim.streams:
        for slot, clip in enumerate(stream):
            if clip is not None:
                out[slot] = max(out.get(slot, (0, 0)), fingerprint(clip))
    return out


def streams_of(anim: AnimPack, slot: int) -> tuple[int, ...]:
    """The streams with a clip in `slot`."""
    return tuple(i for i, s in enumerate(anim.streams) if slot < len(s) and s[slot] is not None)


def shared(anim: AnimPack) -> dict[int, int]:
    """Slot -> how many slots of its stream play the same stored clip, the most over streams."""
    out: dict[int, int] = {}
    for stream in anim.streams:
        count = Counter(id(c) for c in stream if c is not None)
        for slot, clip in enumerate(stream):
            if clip is not None:
                out[slot] = max(out.get(slot, 0), count[id(clip)])
    return out


@dataclass(frozen=True)
class Source:
    """The donor clip a port slot holds."""

    match: Match
    slot: int | None
    """The donor slot: the port slot itself for "same", the fill's for "fill"."""

    def __str__(self) -> str:
        return self.match if self.slot is None else f"{self.match} {self.slot}"


def correspondence(port: AnimPack, donor: AnimPack, stream: int = 0) -> dict[int, Source]:
    """Port slot -> the clip of donor `stream` it holds: the one the builder's fill rule
    (`motion.donor_slot`) puts there, where the fingerprints agree. An empty or missing donor
    stream is an error, not an empty answer."""
    if not 0 <= stream < len(donor.streams):
        raise ValueError(f"the donor has streams 0-{len(donor.streams) - 1}, not {stream}")
    clips = donor.streams[stream]
    if all(c is None for c in clips):
        full = [i for i, s in enumerate(donor.streams) if any(c is not None for c in s)]
        raise ValueError(f"donor stream {stream} holds no clips; these do: {full}")
    out = {}
    for slot, fp in slot_prints(port).items():
        d = donor_slot(clips, slot)
        clip = clips[d]
        if clip is not None and fingerprint(clip) == fp:
            out[slot] = Source("same" if d == slot else "fill", d)
        else:
            out[slot] = Source("unknown", None)
    return out


@dataclass(frozen=True)
class Row:
    """One slot of the host's tables."""

    slot: int
    pairs: tuple[Pair, ...]
    host_streams: tuple[int, ...]
    port_streams: tuple[int, ...]
    host_frames: int | None
    port_frames: int | None
    shared: int
    """Slots of the port playing this slot's stored clip: many for the fill."""
    source: Source | None
    clip: str = ""
    """The manifest's name for the clip in this slot."""


def catalog(
    host: AnimPack,
    drivers: Mapping[int, Collection[Pair]],
    port: AnimPack | None = None,
    donor: AnimPack | None = None,
    stream: int = 0,
    names: Mapping[int, str] | None = None,
) -> list[Row]:
    """Every slot the host fills, the port fills or a pair drives; `source` needs both packs,
    `names` names clips by slot."""
    source = correspondence(port, donor, stream) if port is not None and donor is not None else {}
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
    """The length of the pack's slot tables; an id past it names no slot."""
    return max((len(s) for s in anim.streams), default=0)


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
    """What each build held at a labelled slot."""

    slot: int
    label: str
    pairs: tuple[Pair, ...]
    builds: Mapping[str, Source | None]
    """Build name -> its clip in the slot; None where the slot is empty."""

    @property
    def transfers(self) -> tuple[str, ...]:
        """The builds where the label describes donor clip `slot`."""
        return tuple(b for b, s in self.builds.items() if s is not None and s.match == "same")


def verdicts(
    labels: Mapping[int, str],
    builds: Mapping[str, AnimPack],
    donor: AnimPack,
    stream: int = 0,
    drivers: Mapping[int, Collection[Pair]] | None = None,
) -> list[Verdict]:
    """A label names what a build played in slot N: donor clip N only where that build put it."""
    held = {name: correspondence(anim, donor, stream) for name, anim in builds.items()}
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
