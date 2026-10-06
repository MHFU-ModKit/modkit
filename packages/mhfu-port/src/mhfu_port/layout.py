# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Where each source clip of a port goes: the executor entry (the a1 that plays it) the manifest
gives it, else the one the packer picks.

The packer alone puts every clip whose id is under the host's capacity in the entry of its own id
(entry 100 + s plays slot s of streams 1, 3 and 5) and the rest, by id, in the free entries:
first those the host fills, which its own brain asks for, then the others from 0 up. No whole
clip goes to an entry the host plays on some body parts only (the Tigrex's 24 and 25), where it
would move one part. A manifest clip pins MHP3rd clip `source` (default: `slot`) in entry
`slot`; the clip the packer had there takes the entry the pin freed, so a pin moves only the
clips it touches. What fills the entries left over is `motion.build`'s.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from mhfu.entries import ENTRY_BANK, PART_STREAMS
from mhp_formats.anim import AnimPack

from . import manifest
from .fk import entry_slot, part_clip
from .model import clip_key
from .motion import filled

ENTRIES = {75: 123}
"""Executor entries per host species: the rows of its overlay's descriptor table (em75: dumped
live). An unknown host gets one bank, or up to the last entry its own pack fills."""
LIB = "lib"
"""Where `require` finds a library, under the framework's mods directory."""


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
    ids: dict[int, int] = field(init=False, repr=False, compare=False)
    """MHP3rd clip id -> executor entry."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "ids", {cid: e for e, cid in self.entries.items()})


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
    for name, c in sorted(clips.items(), key=lambda kv: kv[1].slot):
        where = f"clips.{name}"
        if c.id not in ids:
            raise LayoutError(f"{where}: the donor has no clip {c.id}")
        if not 0 <= c.slot < cap:
            raise LayoutError(f"{where}: entry {c.slot} is past the host's {cap}")
        if c.slot in part:
            raise LayoutError(f"{where}: the host plays entry {c.slot} on some body parts only")
        pins[c.slot] = c.id
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
        if freed is None:
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
    return Layout(dict(sorted(entries.items())), frozenset(pins), cap, part, tuple(unplaced))


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
    packer had no entry for."""
    for _ in range(len(pins)):
        src = home.get(pins[at])
        if src is None or src not in pins:
            return src
        at = src
    raise AssertionError("a chain of pins that loops through a clip nobody pinned")


def pin(m: manifest.Manifest, name: str, cid: int, entry: int) -> None:
    """Places donor clip `cid` in `entry` as `clips.<name>`, renaming the clip that already
    places it; raises `ManifestError` for a name another clip has."""
    held = next((n for n, c in m.clips.items() if c.id == cid), None)
    if name in m.clips and name != held:
        raise manifest.ManifestError(f"clips.{name} already places clip {m.clips[name].id}")
    if held is None:
        m.clips[name] = manifest.Clip(entry)
    elif held != name:
        m.rename_clip(held, name)
    c = m.clips[name]
    c.slot, c.source = entry, None if cid == entry else cid


def place(m: manifest.Manifest, now: Layout, cid: int, entry: int, name: str) -> None:
    """`pin`s `cid` in `entry`; a named clip there swaps into `cid`'s entry in `now`, an
    unnamed one takes it from the packer."""
    there = now.entries.get(entry)
    named = next((n for n, c in m.clips.items() if c.id == there), None)
    if there is not None and there != cid and named is not None:
        back = now.ids.get(cid)
        if back is None:
            raise LayoutError(f"entry {entry} holds clips.{named}, and clip {cid} has no entry")
        pin(m, named, there, back)
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
    given = {c.slot: n for n, c in m.clips.items() if c.slot in layout.entries}
    taken = set(given.values())
    out = {}
    for e in layout.entries:
        name = given.get(e, clip_key(e))
        if e in given or name not in taken:
            out[e] = name
    return out


def module_name(m: manifest.Manifest) -> str:
    """The Lua module `mhfu_port`'s `P.define` requires for the port's clips."""
    return f"{m.port.name}_clips.lua"


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def lua(m: manifest.Manifest, layout: Layout) -> str:
    """The module: each clip's name -> its executor entry, and the MHP3rd id it holds."""
    src = f"ports/{m.path.name}" if m.path is not None else f"the {m.port.name} manifest"
    named = names(m, layout)
    out = [
        f"-- {module_name(m)}, GENERATED by mhfu-port from {src}: {len(layout.entries)} clips of",
        f"-- MHP3rd file {m.source.anim} by executor entry. Do not edit: rebuild. mhfu_port's",
        "-- P.define reads it as the port's clips (name -> entry).",
        "return {",
    ]
    for e, cid in layout.entries.items():
        name = named.get(e)
        if name is None:
            continue
        key = name if _IDENT.fullmatch(name) else f'["{name}"]'
        out.append(f"  {key} = {e},  -- MHP3rd {cid}")
    return "\n".join([*out, "}", ""])
