# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The files a port is checked against: its built PAC, the host's PAC and the donor's moveset."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mhfu_port import build
from mhfu_port.data import Data
from mhfu_port.manifest import Manifest
from mhp_formats import fu, p3rd

from mhfu_studio.monster.clips import build_id
from mhfu_studio.shell import places


@dataclass(frozen=True)
class Built:
    """A built model PAC and the file name it goes by."""

    pac: bytes
    name: str

    @property
    def id(self) -> str:
        return build_id(self.name, self.pac)


def built(m: Manifest, pac: Path | None = None, data: Data | None = None) -> Built:
    """The PAC at `pac`, else the port built in memory from the extracted games."""
    if pac is not None:
        return Built(pac.read_bytes(), pac.name)
    return Built(build.build(m, data or places.games()).pac, m.port.pac)


def host_anim(m: Manifest, data: Data) -> fu.Anim:
    return build.host(m, data).anim


def donor_anim(m: Manifest, data: Data) -> p3rd.Anim:
    return p3rd.Anim.from_bytes(data.p3rd.read(m.source.anim))
