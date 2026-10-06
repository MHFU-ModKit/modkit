# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The files a port is checked against: its built PAC, the host's PAC and the donor's moveset."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mhfu_port import build, layout, motion
from mhfu_port.data import Data
from mhfu_port.layout import Layout
from mhfu_port.manifest import Manifest
from mhp_formats import fu
from mhp_formats.anim import Clip

from mhfu_studio.monster.clips import build_id
from mhfu_studio.shell import places


@dataclass(frozen=True)
class Built:
    """A built model PAC and the file name it goes by."""

    pac: bytes
    name: str
    layout: Layout | None = None
    """Where the build put each clip; None for a PAC read from a file."""

    @property
    def id(self) -> str:
        return build_id(self.name, self.pac)


def built(m: Manifest, pac: Path | None = None, data: Data | None = None) -> Built:
    """The PAC at `pac`, else the port built in memory from the extracted games."""
    if pac is not None:
        return Built(pac.read_bytes(), pac.name)
    b = build.build(m, data or places.games())
    return Built(b.pac, m.port.pac, b.layout)


def host_anim(m: Manifest, data: Data) -> fu.Anim:
    return build.host(m, data).anim


def donor_clips(m: Manifest, data: Data) -> dict[int, Clip]:
    """The donor's clips by MHP3rd id."""
    return motion.moveset(data.p3rd.read(m.source.anim))


def placed(m: Manifest, data: Data | None) -> Layout:
    """The manifest's layout on its donor and host; its pins alone without the games."""
    try:
        games = data or places.games()
        return layout.of(m, donor_clips(m, games), host_anim(m, games))
    except (OSError, ValueError):
        return layout.pinned(m)
