# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What the Add panel adds, and where: a shape and its sizes, or a copy of the selection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

from .core.scene import Array

if TYPE_CHECKING:
    from .workspace import MapWorkspace


class AddForm:
    """The Add panel's values; the workspace keeps them, so a panel can come and go."""

    def __init__(self, ws: MapWorkspace) -> None:
        self.ws = ws
        self.shape = 0
        self.size = [400.0, 300.0, 400.0]
        self.radius = 200.0
        self.height = 400.0
        self.segments = 8
        self.rings = 6
        self.divisions = 1
        self.rotate = 0.0
        self.at = [0.0, 0.0, 0.0]
        self.at_mode = 0
        #: the target group, an index into the scene's groups
        self.group = 0
        self.replace = True
        self.uv = 0
        self.colour = True
        self.solid = 1
        self.copy_scale = 1.0
        self.message = ""

    def placement(self) -> Array:
        """The selection's bottom centre, the last click, or the typed point."""
        ws = self.ws
        if self.at_mode == 0 and not ws.selection.empty and ws.scene is not None:
            lo, hi = ws.selection.bounds(ws.scene)
            return np.array([(lo[0] + hi[0]) / 2, lo[1], (lo[2] + hi[2]) / 2], np.float64)
        if self.at_mode == 1 and ws.tools.last_pick is not None:
            return np.asarray(ws.tools.last_pick.point, np.float64)
        return np.asarray(self.at, np.float64)

    def shape_args(self, kind: str) -> dict[str, Any]:
        if kind == "box" or kind == "ramp":
            return {"size": tuple(self.size)}
        if kind == "plane":
            return {"size": (self.size[0], self.size[2]), "divisions": self.divisions}
        if kind == "cylinder":
            return {"radius": self.radius, "height": self.height, "segments": self.segments}
        if kind == "sphere":
            return {"radius": self.radius, "rings": self.rings, "segments": self.segments}
        return {}
