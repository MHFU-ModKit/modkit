# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Every donor clip by MHP3rd id (`stream * 100 + slot`): the executor entry the document's
layout gives it, its name and what it shows; placing one is a document edit.

A name pins nothing, so it moves no clip (`name`, `clips.LabelSession`); placing pins and swaps
(`mhfu_port.layout.place`). A clip the layout leaves out plays from a build of its own
(`preview`), on the port's rig.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from mhfu_port import build, fk, layout, motion
from mhfu_port.data import Data
from mhfu_port.layout import Layout
from mhfu_port.manifest import Manifest, ManifestError
from mhfu_port.model import Clip as SceneClip
from mhp_formats import anim
from mhp_formats.anim import AnimPack

from mhfu_studio.monster import clips
from mhfu_studio.monster.document import PortDocument


@dataclass(frozen=True)
class SourceClip:
    """A donor clip, or (`id` None) an entry of the build that holds none of them."""

    id: int | None
    entry: int | None
    """Its executor entry in the document's layout; None: the layout leaves it out."""
    frames: int
    loop: bool
    name: str = ""
    label: str = ""

    @property
    def stream(self) -> int | None:
        return None if self.id is None else self.id // motion.SOURCE_BANK

    @property
    def key(self) -> tuple[str, int]:
        """What a table row carries: the id, else the entry."""
        if self.id is not None:
            return "clip", self.id
        assert self.entry is not None
        return "anim", self.entry


def preview_slot(cid: int) -> int:
    """A preview's key in a scene's caches: negative, so never an entry's."""
    return -1 - cid


class ClipBrowser:
    """The donor's clips (`donor`, by MHP3rd id) on `host` against the document's layout."""

    def __init__(self, doc: PortDocument, donor: Mapping[int, anim.Clip], host: AnimPack) -> None:
        self.doc = doc
        self.donor = dict(donor)
        self.host = host
        self.prints = {cid: clips.fingerprint(c) for cid, c in self.donor.items()}
        self._layout: tuple[Manifest, Layout] | None = None

    def layout(self) -> Layout:
        """The document's, once per manifest; `LayoutError` for one the host cannot take."""
        m = self.doc.manifest
        if self._layout is None or self._layout[0] is not m:
            self._layout = (m, layout.of(m, self.donor, self.host))
        return self._layout[1]

    def rows(self, others: Iterable[SourceClip] = ()) -> list[SourceClip]:
        """Every donor clip by id, then `others`."""
        ids, m = self.layout().ids, self.doc.manifest
        named = {c.id: (n, c.label) for n, c in m.clips.items()}
        out = []
        for cid in sorted(self.donor):
            frames, loop = self.prints[cid]
            name, label = named.get(cid, ("", ""))
            out.append(SourceClip(cid, ids.get(cid), frames, loop, name, label))
        return [*out, *others]

    def name(self, cid: int, name: str, label: str, build: str | None = None) -> str:
        """Names clip `cid` without pinning it, its fingerprint the original's."""
        if cid not in self.donor:
            raise ManifestError(f"the original has no clip {cid}")
        name = clips.check_name(name)

        def change(m: Manifest) -> None:
            layout.name_clip(m, name, cid)
            c = m.clips[name]
            c.frames, c.loop = self.prints[cid]
            c.label, c.labelled_build = label, build

        self.doc.edit(change)
        return f"clips.{name}"

    def place(self, cid: int, entry: int, name: str) -> str:
        """Pins clip `cid` in `entry`, under its own name or else `name`: a clip pinned there
        swaps into its entry, one the packer put there takes the entry the pin frees. Says what
        moved."""
        if cid not in self.donor:
            raise ManifestError(f"the original has no clip {cid}")
        before = self.layout()
        held = next((n for n, c in self.doc.manifest.clips.items() if c.id == cid), None)
        to = held if held is not None else clips.check_name(name)
        fp = self.prints[cid]

        def change(m: Manifest) -> None:
            layout.place(m, before, cid, entry, to)
            c = m.clips[to]
            if c.frames is None:
                c.frames, c.loop = fp
            layout.of(m, self.donor, self.host)

        self.doc.edit(change)
        return moves_text(layout.moved(before, self.layout()))


def moves_text(moved: Mapping[int, tuple[int | None, int | None]]) -> str:
    def at(e: int | None) -> str:
        return "no anim" if e is None else f"anim {e}"

    if not moved:
        return "nothing moved"
    return "; ".join(f"clip {cid} {at(a)} -> {at(b)}" for cid, (a, b) in moved.items())


def preview(m: Manifest, data: Data, cid: int) -> SceneClip:
    """Donor clip `cid` as a build puts it in an entry, on the port's rig, under
    `preview_slot(cid)`."""
    d, h = build.donor(m, data), build.host(m, data)
    if cid not in d.clips:
        raise KeyError(f"the original has no clip {cid}")
    record_of = build.record_map(d, m.build)
    bind = build.binding(m.build, d, h, build.animated(m.build, record_of, len(d.skeleton.bones)))
    pack = build.animation(d, h, bind, record_of, Layout({0: cid}), m.build.ground_lift)
    c = fk.rig_clip(pack, 0, bind.rig.skeleton)
    if c is None:
        raise ValueError(f"clip {cid} builds to no motion")
    keyed = tuple(j for j, t in enumerate(c.tracks) if any(ch.keyframes for ch in t.channels))
    return SceneClip(preview_slot(cid), motion.frames(c), bool(c.loop), len(c.tracks), keyed, c)
