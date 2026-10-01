# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A rig at one instant: what `mhfu_port.fk` lacks for a viewer."""

from __future__ import annotations

from dataclasses import dataclass, field

from mhfu_port import fk
from mhfu_port.fk import Floats


@dataclass
class Pose:
    rig: fk.Rig
    frame: float
    world: Floats
    """`(joints, 4, 4)` world matrices."""
    slot: int | None = None
    _deform: Floats | None = field(default=None, repr=False, compare=False)

    @property
    def deform(self) -> Floats:
        """`world @ bind⁻¹`, what a vertex's influences blend."""
        if self._deform is None:
            self._deform = self.rig.deform(self.world)
        return self._deform

    @property
    def joints(self) -> Floats:
        """`(joints, 3)` world positions."""
        return self.world[:, :3, 3]

    def skin(self, skin: fk.Skin) -> Floats:
        """`(vertices, 3)` deformed positions."""
        return skin.apply(self.deform)
