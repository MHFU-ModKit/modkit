# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Where each source clip of a port goes: the executor entry (the a1 that plays it) the manifest
gives it, else the one the packer picks.

A manifest clip places MHP3rd clip `source` (default: `slot`) in entry `slot`. The packer then
puts every other stream-0 clip in the entry of its own id, and the rest, by id, in the free
entries: first those the host fills, which its own brain asks for, then the others from 0 up. No
whole clip goes to an entry the host plays on some body parts only (the Tigrex's 24 and 25),
where it would move one part. What fills the entries left over is `motion.build`'s.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

from mhp_formats.anim import AnimPack

from . import manifest
from .fk import ENTRY_BANK, FU_PART_STREAM, entry_slot, part_clip
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
    while n < FU_PART_STREAM * ENTRY_BANK:
        for k in parts:
            si, slot = entry_slot(k, n)
            if si >= len(host.streams) or slot >= len(host.streams[si]):
                return n
        n += 1
    return n


def _parts(host: AnimPack) -> list[int]:
    """The body parts the host animates: those with a clip in their first stream."""
    count = (len(host.streams) + FU_PART_STREAM - 1) // FU_PART_STREAM
    return [k for k in range(count) if any(c is not None for c in host.streams[FU_PART_STREAM * k])]


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
    """The layout of the donor's clips `ids` on `host`: the manifest's `clips` first, then the
    packer. Raises `LayoutError` for a manifest clip the donor or the host cannot take."""
    cap = capacity(host, species)
    part = partial(host, cap)
    entries: dict[int, int] = {}
    for name, c in sorted(clips.items(), key=lambda kv: kv[1].slot):
        cid = c.id
        where = f"clips.{name}"
        if cid not in ids:
            raise LayoutError(f"{where}: the donor has no clip {cid}")
        if not 0 <= c.slot < cap:
            raise LayoutError(f"{where}: entry {c.slot} is past the host's {cap}")
        if c.slot in part:
            raise LayoutError(f"{where}: the host plays entry {c.slot} on some body parts only")
        entries[c.slot] = cid
    placed = frozenset(entries)
    left = sorted(set(ids) - set(entries.values()))
    own = [cid for cid in left if cid < min(ENTRY_BANK, cap) and cid not in entries.keys() | part]
    entries.update((cid, cid) for cid in own)
    asked = set(filled(host))
    free = iter(
        sorted(
            (e for e in range(cap) if e not in entries.keys() | part),
            key=lambda e: (e not in asked, e),
        )
    )
    unplaced = []
    for cid in sorted(set(left) - set(own)):
        e = next(free, None)
        if e is None:
            unplaced.append(cid)
        else:
            entries[e] = cid
    return Layout(dict(sorted(entries.items())), placed, cap, part, tuple(unplaced))


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
