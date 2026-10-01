# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What the Add panel adds, and where: a shape and its sizes, or a copy of the selection,
`pack`ed into a group's free primitives and those of the objects it replaces."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from .core import shapes
from .core.edit import OBJECT, EditError, Selection
from .core.scene import Array, MeshGroup

if TYPE_CHECKING:
    from .workspace import MapWorkspace

COPY = "copy"
KINDS = (*shapes.SHAPES, COPY)
#: where it goes: the selection's bottom centre, the last click, or the typed point
PLACES = ("selection", "click", "typed")
UV_MODES = ("planar", "keep", "obj")
SOLIDS: tuple[bool | str | None, ...] = (None, "box", True)


class AddForm:
    """The Add panel's values; the workspace keeps them, so a panel can come and go."""

    def __init__(self, ws: MapWorkspace) -> None:
        self.ws = ws
        #: indices into KINDS, PLACES, UV_MODES and SOLIDS
        self.shape = 0
        self.at_mode = 0
        self.uv = 0
        self.solid = 1
        self.size = [400.0, 300.0, 400.0]
        self.radius = 200.0
        self.height = 400.0
        self.segments = 8
        self.rings = 6
        self.divisions = 1
        self.rotate = 0.0
        self.at = [0.0, 0.0, 0.0]
        #: the target group, an index into the scene's groups
        self.group = 0
        self.replace = True
        self.colour = True
        self.copy_scale = 1.0
        #: the last add's outcome
        self.message = ""

    @property
    def kind(self) -> str:
        return KINDS[self.shape]

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

    def target(self) -> MeshGroup | None:
        """The group it goes into."""
        sc = self.ws.scene
        if sc is None or not sc.groups:
            return None
        return sc.groups[max(0, min(self.group, len(sc.groups) - 1))]

    def sacrifice(self) -> Selection | None:
        """The selected objects whose primitives the shape may take."""
        g, sel = self.target(), self.ws.selection
        if g is None or not self.replace or self.kind == COPY or g.key not in sel.vertices:
            return None
        return sel

    def needs(self) -> int:
        """Triangles the shape or the copy has."""
        sc = self.ws.scene
        if self.kind == COPY:
            return self.ws.selection.n_faces(sc) if sc is not None else 0
        return shapes.triangle_count(self.kind, **self.shape_args(self.kind))

    def capacity(self) -> tuple[int, int]:
        """Triangles that fit: with the sacrifice, and in free primitives alone."""
        g, sess = self.target(), self.ws.session
        if g is None or sess is None:
            return 0, 0
        return sess.capacity(g.key, self.sacrifice()), sess.capacity(g.key, None)

    def add(self) -> Selection | None:
        """Writes the shape into the document's assets/ and packs it; the new object, or None
        with `message` saying why not."""
        ws = self.ws
        sc, sess, g = ws.scene, ws.session, self.target()
        if sc is None or sess is None or g is None:
            return None
        if sess.base_dir is None:
            self.message = "save the document first: the shape is written into its assets/"
            return None
        kind, at = self.kind, tuple(self.placement())
        try:
            if kind in ("cylinder", "sphere"):
                v, t, uv = shapes.make(kind, at=at, **self.shape_args(kind))
                stem = kind
            elif kind != COPY:
                v, t, uv = shapes.make(kind, at=at, rotate_y=self.rotate, **self.shape_args(kind))
                stem = kind
            else:
                if ws.selection.empty:
                    self.message = "nothing selected to copy"
                    return None
                k = next(iter(ws.selection.vertices))
                ids = ws.selection.vertices[k]
                v, t, uv = shapes.from_group(
                    sc, k, ids, at=at, scale=self.copy_scale, rotate_y=self.rotate
                )
                stem = f"copy_g{k[1]}"
        except (KeyError, ValueError) as e:
            self.message = f"shape: {e}"
            return None
        name = shapes.next_asset_name(sess.base_dir, stem)
        shapes.write_obj(sess.base_dir / name, v, t, uv, stem)
        try:
            new = sess.add_mesh(
                g.key,
                name,
                len(t),
                sacrifice=self.sacrifice(),
                uv=UV_MODES[self.uv],
                colour=self._colour(),
                solid=SOLIDS[self.solid],
            )
        except EditError as e:
            self.message = f"refused: {e}"
            (sess.base_dir / name).unlink(missing_ok=True)
            return None
        self.message = f"added {Path(name).name} ({len(t)} triangles) into {g.label} as {name}"
        warnings = sess.warnings()
        if warnings:
            self.message += "   [!] " + warnings[0]
        ws.after_commit(sess.ops[-1:])
        ws.tools.kind = OBJECT
        ws.tools.select(new)
        return new

    def _colour(self) -> list[int] | None:
        """The selection's mean vertex colour, when asked for and there is one."""
        ws = self.ws
        if not self.colour or ws.selection.empty or ws.scene is None:
            return None
        sc = ws.scene
        cols = [
            cs[ids]
            for k, ids in ws.selection.vertices.items()
            if (cs := sc.group(*k).colours) is not None
        ]
        return [int(x) for x in np.concatenate(cols).mean(0).round()] if cols else None
