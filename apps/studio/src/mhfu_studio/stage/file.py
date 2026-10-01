# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A stage PAC as the game ships it, and where each part of it sits."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property

from mhfu import files
from mhfu.files import Extracted
from mhp_formats.fu.stage import TRI_SIZE, Hits, Stage
from mhp_formats.pac import Pac
from mhp_formats.pmo import Pmo

TERRAIN, TEXTURES, PROPS, COLLISION = 0, 1, 2, 5
"""Entries of a stage PAC; an op's `sub` names one of the two PMOs."""
MESHES = (TERRAIN, PROPS)
Vec3 = tuple[float, float, float]


@dataclass(frozen=True)
class StageFile:
    """`st<NNN>.pac`; `game` reads other stages (a texture taken from one) when given."""

    number: int
    data: bytes
    game: Extracted | None = field(default=None, compare=False, repr=False)

    @classmethod
    def read(cls, game: Extracted, number: int) -> StageFile:
        return cls(number, game.read(files.stage_pac(number)), game)

    def sibling(self, number: int) -> StageFile:
        if self.game is None:
            raise ValueError(f"{self.label} was not read from a game, so st{number:03d} is not")
        return StageFile.read(self.game, number)

    @property
    def label(self) -> str:
        return f"st{self.number:03d}"

    @cached_property
    def stage(self) -> Stage:
        return Stage.from_bytes(self.data)

    @cached_property
    def table(self) -> list[tuple[int, int]]:
        """(offset, size) of each entry in the PAC."""
        return Pac.from_bytes(self.data).table()

    def entry(self, k: int) -> bytes:
        off, size = self.table[k]
        return self.data[off : off + size]

    def pmo(self, sub: int) -> Pmo | None:
        """A fresh decode of entry `sub` (edits change it), None when it holds no PMO."""
        data = self.entry(sub) if sub < len(self.table) else b""
        return Pmo.from_bytes(data) if Pmo.sniff(data) else None

    def positions(self, sub: int, group: int) -> tuple[Vec3, ...]:
        """Group `group` of PMO `sub` as shipped, decoded once; empty when there is none."""
        if sub not in self._shipped:
            pmo = self.pmo(sub)
            self._shipped[sub] = [
                tuple((x, y, z) for x, y, z in pmo.positions(g))
                for g in range(len(pmo.groups()) if pmo else 0)
                if pmo
            ]
        rows = self._shipped[sub]
        return rows[group] if 0 <= group < len(rows) else ()

    @cached_property
    def _shipped(self) -> dict[int, list[tuple[Vec3, ...]]]:
        return {}

    @property
    def chunks(self) -> list[Hits]:
        coll = self.stage.collision
        return coll.chunks if coll else []

    @cached_property
    def _chunk_table(self) -> list[tuple[int, int]]:
        coll = self.stage.collision
        return coll.table() if coll else []

    def chunk_offset(self, chunk: int) -> int:
        """Where `HITS` chunk `chunk` starts in the PAC."""
        if not 0 <= chunk < len(self._chunk_table):
            raise IndexError(f"{self.label} has no collision chunk {chunk}")
        return self.table[COLLISION][0] + self._chunk_table[chunk][0]

    def tri_offset(self, chunk: int, tri: int) -> int:
        """Where triangle `tri` of `chunk` starts in the PAC."""
        return self.chunk_offset(chunk) + self.chunks[chunk].tri_offset + TRI_SIZE * tri
