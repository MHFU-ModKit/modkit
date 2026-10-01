# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Add panel: a shape, or a copy of the selection, `pack`ed into a group's free primitives
and those of the objects it replaces, with the budget before and after."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from mhfu_studio.shell.widgets import help_marker, plain

from ..core import shapes
from ..core.edit import OBJECT, EditError, Selection
from ..core.scene import Array, MeshGroup

if TYPE_CHECKING:
    from ..workspace import MapWorkspace

COPY = "copy of the selection"
KINDS = (*shapes.SHAPES, COPY)
PLACES = ("the selection's bottom centre", "the last click", "typed")
UV_MODES = ("planar", "keep", "obj")
UV_LABELS = ("planar (the group's own texels)", "keep the slots' uv", "from the shape")
SOLIDS: tuple[bool | str | None, ...] = (None, "box", True)
OK, SHORT = (0.5, 0.9, 0.5, 1.0), (1.0, 0.5, 0.4, 1.0)


class AddPanel:
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

    def draw(self) -> None:
        from imgui_bundle import imgui

        ws = self.ws
        sc, sess = ws.scene, ws.session
        if sc is None or sess is None or not sc.groups:
            imgui.text("no section loaded")
            return
        _, self.shape = imgui.combo("shape", self.shape, list(KINDS))
        kind = KINDS[self.shape]
        if kind == "box":
            _, self.size = imgui.input_float3("size x y z", self.size, "%.0f")
        elif kind == "plane":
            _, self.size = imgui.input_float3("size x _ z", self.size, "%.0f")
            _, self.divisions = imgui.slider_int("divisions", self.divisions, 1, 8)
        elif kind == "ramp":
            _, self.size = imgui.input_float3("width height length", self.size, "%.0f")
        elif kind in ("cylinder", "sphere"):
            _, self.radius = imgui.input_float("radius", self.radius, 0, 0, "%.0f")
            if kind == "cylinder":
                _, self.height = imgui.input_float("height", self.height, 0, 0, "%.0f")
            else:
                _, self.rings = imgui.slider_int("rings", self.rings, 2, 16)
            _, self.segments = imgui.slider_int("segments", self.segments, 3, 24)
        else:
            _, self.copy_scale = imgui.input_float("scale", self.copy_scale, 0, 0, "%.2f")
        if kind not in ("cylinder", "sphere"):
            _, self.rotate = imgui.slider_float(
                "rotate about Y", self.rotate, -180.0, 180.0, "%.0f"
            )
        kw = self.shape_args(kind)
        n_tris = shapes.triangle_count(kind, **kw) if kind != COPY else ws.selection.n_faces(sc)
        imgui.separator()
        _, self.at_mode = imgui.combo("place at", self.at_mode, list(PLACES))
        if self.at_mode == 2:
            _, self.at = imgui.input_float3("x y z", self.at, "%.0f")
        else:
            x, y, z = self.placement()
            imgui.text_disabled(f"  ({x:.0f}, {y:.0f}, {z:.0f})")
        names = [g.label + ("  (far)" if g.backdrop else "") for g in sc.groups]
        self.group = max(0, min(self.group, len(names) - 1))
        _, self.group = imgui.combo("into group", self.group, names)
        g = sc.groups[self.group]
        _, self.replace = imgui.checkbox(
            "replace the selected objects (their primitives)", self.replace
        )
        same = g.key in ws.selection.vertices
        sacrifice = ws.selection if self.replace and same and kind != COPY else None
        cap, free = sess.capacity(g.key, sacrifice), sess.capacity(g.key, None)
        extra = f", +{cap - free} from the selection" if cap > free else ""
        imgui.text_colored(
            imgui.ImVec4(*(OK if n_tris <= cap else SHORT)),
            f"needs {n_tris} triangles; {cap} available ({free} free{extra})",
        )
        imgui.same_line()
        help_marker(
            "The group's primitives are the budget: free ones (cleared, or degenerate as shipped)"
            " and, if ticked, those of the objects selected in this group. The strip count is an"
            " estimate; the pack says what did not fit."
        )
        _, self.uv = imgui.combo("uv", self.uv, list(UV_LABELS))
        _, self.colour = imgui.checkbox(
            "vertex colour from the selection (else white)", self.colour
        )
        _, self.solid = imgui.combo("collision", self.solid, ["none", "box", "every triangle"])
        if imgui.button("add"):
            self.add(kind, kw, g, sacrifice)
        imgui.same_line()
        if imgui.button("remove the selection"):
            ws.remove_selected(solid=False)
        if self.message:
            imgui.text_wrapped(plain(self.message))

    def add(self, kind: str, kw: dict[str, Any], g: MeshGroup, sacrifice: Selection | None) -> None:
        """Writes the shape into the document's assets/ and packs it into `g`."""
        ws = self.ws
        sc, sess = ws.scene, ws.session
        if sc is None or sess is None:
            return
        if sess.base_dir is None:
            self.message = "save the document first: the shape is written into its assets/"
            return
        at = tuple(self.placement())
        try:
            if kind in ("cylinder", "sphere"):
                v, t, uv = shapes.make(kind, at=at, **kw)
                stem = kind
            elif kind in shapes.SHAPES:
                v, t, uv = shapes.make(kind, at=at, rotate_y=self.rotate, **kw)
                stem = kind
            else:
                if ws.selection.empty:
                    self.message = "nothing selected to copy"
                    return
                k = next(iter(ws.selection.vertices))
                v, t, uv = shapes.from_group(
                    sc,
                    k,
                    ws.selection.vertices[k],
                    at=at,
                    scale=self.copy_scale,
                    rotate_y=self.rotate,
                )
                stem = f"copy_g{k[1]}"
        except (KeyError, ValueError) as e:
            self.message = f"shape: {e}"
            return
        name = shapes.next_asset_name(sess.base_dir, stem)
        shapes.write_obj(sess.base_dir / name, v, t, uv, stem)
        colour = None
        if self.colour and not ws.selection.empty:
            cols = [
                cs[ids]
                for k, ids in ws.selection.vertices.items()
                if (cs := sc.group(*k).colours) is not None
            ]
            if cols:
                colour = [int(x) for x in np.concatenate(cols).mean(0).round()]
        try:
            new = sess.add_mesh(
                g.key,
                name,
                len(t),
                sacrifice=sacrifice,
                uv=UV_MODES[self.uv],
                colour=colour,
                solid=SOLIDS[self.solid],
            )
        except EditError as e:
            self.message = f"refused: {e}"
            (sess.base_dir / name).unlink(missing_ok=True)
            return
        self.message = f"added {Path(name).name} ({len(t)} triangles) into {g.label} as {name}"
        warnings = sess.warnings()
        if warnings:
            self.message += "   [!] " + warnings[0]
        ws.after_commit(sess.ops[-1:])
        ws.tools.kind = OBJECT
        ws.tools.select(new)
