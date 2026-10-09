# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Where each source clip of a port goes: the executor entry (the a1 that plays it) the manifest
gives it, else the one the packer picks.

The packer alone puts every clip whose id is under the host's capacity in the entry of its own id
(entry 100 + s plays slot s of streams 1, 3 and 5) and the rest, by id, in the free entries:
first those the host fills, which its own brain asks for, then the others from 0 up. No whole
clip goes to an entry the host plays on some body parts only (the Tigrex's 24 and 25), where it
would move one part. A manifest clip with a `slot` pins MHP3rd clip `source` (default: `slot`)
in that entry; the clip the packer had there takes the entry the pin freed, so a pin moves only
the clips it touches. A manifest clip without one only names its clip. One with a `start` pins
only those frames of its source, so a source can fill several entries; its pins free its packer
entry once, and the other clips they displace go to free entries. What fills the entries left
over is `motion.build`'s.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from mhfu import inject
from mhfu.entries import ENTRY_BANK, PART_STREAMS
from mhp_formats.anim import AnimPack, Clip

from . import manifest
from .fk import entry_slot, part_clip
from .model import clip_key
from .motion import cut, filled
from .travel import KEY_STEP, Turn

ENTRIES = {75: 123}
"""Executor entries per host species: the rows of its overlay's descriptor table (em75: dumped
live). An unknown host gets one bank, or up to the last entry its own pack fills."""
LIB = inject.LIB


class LayoutError(manifest.ManifestError):
    """A manifest clip that cannot go where it says."""


@dataclass(frozen=True)
class Layout:
    entries: dict[int, int]
    """Executor entry -> MHP3rd clip id."""
    placed: frozenset[int] = frozenset()
    """The entries the manifest names."""
    capacity: int = ENTRY_BANK
    partial: frozenset[int] = frozenset()
    """Entries the host plays on some body parts only."""
    unplaced: tuple[int, ...] = ()
    """Clips no entry was left for."""
    turns: dict[int, Turn] = field(default_factory=dict)
    """Entry -> what YAW turns while it plays; the build sets it."""
    frames: dict[int, int] = field(default_factory=dict)
    """Entry -> its clip's frames; the build sets it."""
    cuts: dict[int, tuple[int, int]] = field(default_factory=dict)
    """Entry -> `(start, frames)` of the source it holds; an entry not here holds all of it."""
    ids: dict[int, int] = field(init=False, repr=False, compare=False)
    """MHP3rd clip id -> executor entry; a cut source's first cut."""

    def __post_init__(self) -> None:
        last_first = sorted(self.entries, key=lambda e: (self.cuts.get(e, (0, 0)), e), reverse=True)
        object.__setattr__(self, "ids", {self.entries[e]: e for e in last_first})

    def entry(self, c: manifest.Clip) -> int | None:
        """Clip `c`'s entry here: a cut's pin, else its source's; None where it holds neither."""
        e = where(c, self.ids)
        return e if e is not None and self.entries.get(e) == c.id else None


def capacity(host: AnimPack, species: int) -> int:
    """The executor entries the host plays: `ENTRIES`, within the slots its pack has."""
    rows = ENTRIES.get(species, max(ENTRY_BANK, max(filled(host), default=-1) + 1))
    return min(rows, _addressable(host))


def _addressable(host: AnimPack) -> int:
    """Entries every part of the host can hold: each one's streams have a slot for it."""
    parts = _parts(host)
    n = 0
    while n < PART_STREAMS * ENTRY_BANK:
        for k in parts:
            si, slot = entry_slot(k, n)
            if si >= len(host.streams) or slot >= len(host.streams[si]):
                return n
        n += 1
    return n


def _parts(host: AnimPack) -> list[int]:
    """The body parts the host animates: those with a clip in their first stream."""
    count = (len(host.streams) + PART_STREAMS - 1) // PART_STREAMS
    return [k for k in range(count) if any(c is not None for c in host.streams[PART_STREAMS * k])]


def partial(host: AnimPack, entries: int) -> frozenset[int]:
    """Entries under `entries` that the host plays on some of its parts but not all."""
    parts = _parts(host)
    out = set()
    for e in range(entries):
        held = sum(part_clip(host, k, e) is not None for k in parts)
        if 0 < held < len(parts):
            out.add(e)
    return frozenset(out)


def plan(
    clips: Mapping[str, manifest.Clip], ids: Collection[int], host: AnimPack, species: int
) -> Layout:
    """The layout of the donor's clips `ids` on `host`: the packer's, with the manifest's
    `clips` pinned. Raises `LayoutError` for a manifest clip the donor or the host cannot take."""
    cap = capacity(host, species)
    part = partial(host, cap)
    pins: dict[int, int] = {}
    cuts: dict[int, tuple[int, int]] = {}
    for name, c in sorted(clips.items()):
        where = f"clips.{name}"
        if c.id not in ids:
            raise LayoutError(f"{where}: the donor has no clip {c.id}")
        if c.slot is None:
            continue
        if not 0 <= c.slot < cap:
            raise LayoutError(f"{where}: entry {c.slot} is past the host's {cap}")
        if c.slot in part:
            raise LayoutError(f"{where}: the host plays entry {c.slot} on some body parts only")
        pins[c.slot] = c.id
        if c.cut is not None:
            cuts[c.slot] = c.cut
    asked = set(filled(host))
    packed, left = _pack(ids, cap, part, asked)
    home = {cid: e for e, cid in packed.items()}
    pinned = set(pins.values())
    entries = dict(pins)
    entries.update((e, cid) for e, cid in packed.items() if e not in pins and cid not in pinned)
    displaced = []
    for e in sorted(pins):
        cid = packed.get(e)
        if cid is None or cid in pinned:
            continue
        freed = _freed(e, pins, home)
        if freed is None or freed in entries:
            displaced.append(cid)
        else:
            entries[freed] = cid
    free = iter(_free(entries.keys() | part, cap, asked))
    unplaced: list[int] = []
    for cid in [*displaced, *(c for c in left if c not in pinned)]:
        to = next(free, None)
        if to is None:
            unplaced.append(cid)
        else:
            entries[to] = cid
    placed = dict(sorted(entries.items()))
    return Layout(placed, frozenset(pins), cap, part, tuple(unplaced), cuts=cuts)


def _free(taken: Collection[int], cap: int, asked: Collection[int]) -> list[int]:
    """Entries under `cap` not `taken`, those the host fills first."""
    return sorted((e for e in range(cap) if e not in taken), key=lambda e: (e not in asked, e))


def _pack(
    ids: Collection[int], cap: int, part: Collection[int], asked: Collection[int]
) -> tuple[dict[int, int], list[int]]:
    """The packer with no manifest: entry -> id, and the ids no entry is left for."""
    entries = {cid: cid for cid in sorted(ids) if cid < cap and cid not in part}
    free = iter(_free(entries.keys() | set(part), cap, asked))
    left: list[int] = []
    for cid in sorted(set(ids) - set(entries.values())):
        to = next(free, None)
        if to is None:
            left.append(cid)
        else:
            entries[to] = cid
    return entries, left


def _freed(at: int, pins: Mapping[int, int], home: Mapping[int, int]) -> int | None:
    """The entry the chain of pins ending at `at` frees: the packer's entry of the clip pinned
    there, followed back while another pin took it; None where the chain starts at a clip the
    packer had no entry for, or loops through a source pinned twice."""
    seen = set()
    while at not in seen:
        seen.add(at)
        src = home.get(pins[at])
        if src is None or src not in pins:
            return src
        at = src
    return None


def pinned(m: manifest.Manifest) -> Layout:
    """The manifest's pins alone: all that is known of a layout without the donor and host."""
    pins = {c.slot: c for c in m.clips.values() if c.slot is not None}
    cuts = {e: c.cut for e, c in pins.items() if c.cut is not None}
    return Layout({e: c.id for e, c in pins.items()}, cuts=cuts)


def where(c: manifest.Clip, ids: Mapping[int, int]) -> int | None:
    """Clip `c`'s entry: a cut's pin, else what a layout's `ids` say, else its pin."""
    return c.slot if c.start is not None else ids.get(c.id, c.slot)


def clips(placed: Layout, donor: Mapping[int, Clip]) -> dict[int, Clip]:
    """Entry -> the donor clip it plays: `donor`'s by id, cut where `placed` cuts it."""
    out = {}
    for e, cid in placed.entries.items():
        whole = donor[cid]
        if e not in placed.cuts:
            out[e] = whole
            continue
        try:
            out[e] = cut(whole, *placed.cuts[e])
        except ValueError as err:
            raise LayoutError(f"entry {e}, clip {cid}: {err}") from None
    return out


def holder(m: manifest.Manifest, cid: int) -> str | None:
    """The name of the manifest clip that names donor clip `cid`."""
    return next((n for n, c in m.clips.items() if c.id == cid), None)


def name_clip(m: manifest.Manifest, name: str, cid: int) -> None:
    """Names donor clip `cid` `clips.<name>`, renaming the clip that already names it and
    keeping its pin; a new name pins nothing. Raises `ManifestError` for a name another clip
    has."""
    held = holder(m, cid)
    if name in m.clips and name != held:
        raise manifest.ManifestError(f"clips.{name} already names clip {m.clips[name].id}")
    if held is None:
        m.clips[name] = manifest.Clip(source=cid)
    elif held != name:
        m.rename_clip(held, name)


def pin(m: manifest.Manifest, name: str, cid: int, entry: int) -> None:
    """Places donor clip `cid` in `entry` as `clips.<name>` (`name_clip`, then the pin)."""
    name_clip(m, name, cid)
    c = m.clips[name]
    c.slot, c.source = entry, None if cid == entry else cid


def place(m: manifest.Manifest, now: Layout, cid: int, entry: int, name: str) -> None:
    """`pin`s `cid` in `entry`. A clip pinned there swaps into `cid`'s entry in `now`; one the
    packer put there takes the entry the pin frees."""
    there = now.entries.get(entry)
    held = None if there is None or there == cid else holder(m, there)
    if there is not None and held is not None and m.clips[held].slot is not None:
        back = now.ids.get(cid)
        if back is None:
            raise LayoutError(f"entry {entry} holds clips.{held}, and clip {cid} has no entry")
        pin(m, held, there, back)
    pin(m, name, cid, entry)


def moved(before: Layout, after: Layout) -> dict[int, tuple[int | None, int | None]]:
    """Clip id -> (entry before, entry after), for the clips whose entry changed."""
    ids = before.ids.keys() | after.ids.keys() | set(before.unplaced) | set(after.unplaced)
    out = {cid: (before.ids.get(cid), after.ids.get(cid)) for cid in sorted(ids)}
    return {cid: ab for cid, ab in out.items() if ab[0] != ab[1]}


def of(m: manifest.Manifest, ids: Collection[int], host: AnimPack) -> Layout:
    return plan(m.clips, ids, host, m.port.host_species)


def names(m: manifest.Manifest, layout: Layout) -> dict[int, str]:
    """Entry -> the clip's name: the manifest's, else `clip_key(entry)` where no clip has it."""
    given = {e: n for n, c in m.clips.items() if (e := layout.entry(c)) is not None}
    taken = set(given.values())
    out = {}
    for e in layout.entries:
        name = given.get(e, clip_key(e))
        if e in given or name not in taken:
            out[e] = name
    return out


def module_name(m: manifest.Manifest) -> str:
    """The Lua module `mhfu_port`'s `P.define` requires for the port's clips."""
    return inject.port_module(m.port.name, "clips")


def turns_module_name(m: manifest.Manifest) -> str:
    """The Lua module with the port's turn curves, which no mod needs: moves carry their own."""
    return inject.port_module(m.port.name, "turns")


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _source(m: manifest.Manifest) -> str:
    return f"ports/{m.path.name}" if m.path is not None else f"the {m.port.name} manifest"


def lua(m: manifest.Manifest, layout: Layout) -> str:
    """The module: each clip's name -> its executor entry, and the MHP3rd id it holds."""
    named = names(m, layout)
    out = [
        f"-- {module_name(m)}, GENERATED by mhfu-port from {_source(m)}: {len(layout.entries)}"
        " clips of",
        f"-- MHP3rd file {m.source.anim} by executor entry. Do not edit: rebuild. mhfu_port's",
        "-- P.define reads it as the port's clips (name -> entry).",
        "return {",
    ]
    for e, cid in layout.entries.items():
        name = named.get(e)
        if name is None:
            continue
        key = name if _IDENT.fullmatch(name) else f'["{name}"]'
        out.append(f"  {key} = {e},  -- MHP3rd {cid}{_frames(layout.cuts.get(e))}")
    return "\n".join([*out, "}", ""])


def _frames(cut: tuple[int, int] | None) -> str:
    return "" if cut is None else f", frames {cut[0]}..{cut[0] + cut[1]}"


def turns_lua(m: manifest.Manifest, layout: Layout) -> str:
    """The turns module: entry -> YAW while it plays. `mhfu move play --curve` reads it."""
    return "\n".join(
        [
            f"-- {turns_module_name(m)}, GENERATED by mhfu-port from {_source(m)}: entry -> YAW"
            " while it plays,",
            f"-- 4 hex digits (0x10000 a turn) every {KEY_STEP} clip frames, the last at its end."
            " Do not edit: rebuild.",
            "return {",
            *(f'  [{e}] = "{t.lua()}",' for e, t in sorted(layout.turns.items())),
            "}",
            "",
        ]
    )
