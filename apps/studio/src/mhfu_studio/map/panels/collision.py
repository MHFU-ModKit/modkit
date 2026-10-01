# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Collision panel: chunks and classes with their counts, and the inspector of the
collision selection (pick kind 4): flags, vertices, climbable, delete, new triangles."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from mhfu_studio.shell.camera import Bounds
from mhfu_studio.shell.widgets import help_marker, plain

from ..core.edit import COLLISION, CollisionSelection
from ..core.scene import CLASSES
from ..render.overlays import CLASS_COLORS

if TYPE_CHECKING:
    from ..workspace import MapWorkspace

CLIMB_COLOR = (1.0, 0.82, 0.1, 1.0)
WARN = (1.0, 0.75, 0.3, 1.0)
CHUNKS = ("auto", "0 walls", "1 floor")


def flags_of(fields: list[int]) -> dict[str, int]:
    return {"surface": fields[0], "material": fields[1], "exclude": fields[2]}


class CollisionPanel:
    def __init__(self, ws: MapWorkspace) -> None:
        self.ws = ws
        self.seed: tuple[int, int, int] | None = None
        self.flags = [0, 0, 0]
        self.verts = [[0.0] * 3 for _ in range(3)]
        self.add_flags = [0, 0, 0]
        self.add_chunk = 0
        self.inflate = 0.0

    def draw(self) -> None:
        from imgui_bundle import imgui

        ws = self.ws
        sc, vp = ws.scene, ws.vp
        if sc is None or vp is None or vp.collision is None:
            imgui.text("no section loaded")
            return
        col = vp.collision
        _, vp.show_collision = imgui.checkbox("show collision", vp.show_collision)
        for c in sc.collision:
            what = "walkable floor" if c.index == 1 else "walls / ceilings"
            label = (
                f"chunk {c.index} - {what}, {c.n_triangles} triangles,"
                f" grid {c.grid[0]}x{c.grid[1]} x {c.cell[0]}"
            )
            changed, on = imgui.checkbox(label, col.show_chunk.get(c.index, True))
            if changed:
                col.show_chunk[c.index] = on
        imgui.separator()
        for k in CLASSES:
            n = sum(int((c.klass == k).sum()) for c in sc.collision)
            imgui.color_button(
                f"##c{k}", imgui.ImVec4(*CLASS_COLORS[k], 1.0), 0, imgui.ImVec2(14, 14)
            )
            imgui.same_line()
            changed, on = imgui.checkbox(f"{k}  ({n})", col.show_class.get(k, True))
            if changed:
                col.set_class(k, on)
        imgui.separator()
        _, col.fill_alpha = imgui.slider_float("fill alpha", col.fill_alpha, 0.0, 1.0, "%.2f")
        _, col.edge_alpha = imgui.slider_float("edge alpha", col.edge_alpha, 0.0, 1.0, "%.2f")
        climb = sc.climbable()
        if climb:
            imgui.text_colored(
                imgui.ImVec4(*CLIMB_COLOR),
                f"{len(climb)} climbable triangles (material 9/10, vertical)",
            )
            if imgui.small_button("frame climbable walls"):
                pts = np.concatenate([sc.chunk(c).verts[t] for c, t in climb])
                vp.camera.frame(Bounds.of(pts))
            imgui.same_line()
            if imgui.small_button("select them"):
                ws.tools.set_kind(COLLISION)
                ws.tools.select_collision(CollisionSelection(list(climb)))
        else:
            imgui.text_disabled("no climbable wall in this section")
        if sc.surface_table:
            imgui.text_disabled(
                "surface table: " + " ".join(f"0x{v:02X}" for v in sc.surface_table)
            )
        imgui.separator()
        self._inspector()

    def _inspector(self) -> None:
        from imgui_bundle import imgui

        ws = self.ws
        sc, sess = ws.scene, ws.session
        if sc is None or sess is None:
            return
        sel = ws.col_sel
        if ws.tools.kind != COLLISION:
            imgui.text_disabled("press 4 (or the `collision` kind) to pick collision triangles")
        imgui.text_wrapped(sel.describe(sc))
        if not sel.empty:
            c0, t0 = sel.tris[0]
            ch = sc.chunk(c0)
            if self.seed != (c0, t0, len(sel)):
                self.seed = (c0, t0, len(sel))
                self.flags = [int(ch.surface_id[t0]), int(ch.material[t0]), int(ch.exclude[t0])]
                self.verts = [[float(v) for v in row] for row in ch.verts[t0]]
            if len(sel) == 1:
                n = ch.normals[t0]
                added = "" if t0 < ch.n_shipped else " (added)"
                imgui.text(
                    f"chunk {c0} tri {t0}{added}: {ch.klass[t0]}  normal ({n[0]:.2f} {n[1]:.2f}"
                    f" {n[2]:.2f})  plane_d {ch.plane_d[t0]:.0f}"
                )
                upright = "near-vertical" if ch.vertical(t0) else f"flat (|n.y| {abs(n[1]):.2f})"
                height = float(ch.verts[t0][:, 1].max() - ch.verts[t0][:, 1].min())
                imgui.text(f"{upright}  height {height:.0f}  cells {ch.cells_of(t0)[:8]}")
                for i in range(3):
                    _, self.verts[i] = imgui.input_float3(f"v{i}", self.verts[i], "%.1f")
                if imgui.button("apply vertices"):
                    ws.do("collision", sess.collision_set, c0, [t0], verts=self.verts)
                imgui.same_line()
                if imgui.button("flip"):
                    v = ch.verts[t0]
                    flipped = [v[0].tolist(), v[2].tolist(), v[1].tolist()]
                    ws.do("collision", sess.collision_set, c0, [t0], verts=flipped)
                imgui.same_line()
                help_marker(
                    "swap two corners: the normal points the other way (the engine uses the"
                    " plane's side for floors and walls)."
                )
            else:
                mats = sorted({int(sc.chunk(c).material[t]) for c, t in sel.tris})
                imgui.text(f"{len(sel)} triangles; materials {mats}")
            _, self.flags = imgui.input_int3("surface / material / exclude", self.flags)
            if imgui.button("apply flags"):
                for c, ts in sel.by_chunk().items():
                    ws.do("collision", sess.collision_set, c, ts, flags=flags_of(self.flags))
            imgui.same_line()
            if imgui.button("climbable (10)"):
                self.climb(True, 10)
            imgui.same_line()
            if imgui.button("rock wall (9)"):
                self.climb(True, 9)
            imgui.same_line()
            if imgui.button("not climbable"):
                self.climb(False)
            imgui.same_line()
            if imgui.button("delete"):
                ws.do("delete", sess.collision_delete, list(sel.tris))
            for w in sess.climb_warnings(sel.tris)[:4]:
                imgui.text_colored(imgui.ImVec4(*WARN), plain(w))
        imgui.separator()
        imgui.text("new collision")
        imgui.same_line()
        help_marker(
            "Added triangles live in scratch RAM and are linked into the broadphase by one word"
            " per cell; they need no room in the chunk. Chunk 1 is what the hunter stands on,"
            " chunk 0 what he walks into; `auto` sorts by the normal."
        )
        _, self.add_chunk = imgui.combo("chunk##add", self.add_chunk, list(CHUNKS))
        _, self.add_flags = imgui.input_int3("surface / material / exclude##add", self.add_flags)
        chunk = None if self.add_chunk == 0 else self.add_chunk - 1
        fl = flags_of(self.add_flags)
        mesh_sel = ws.selection
        n_faces = mesh_sel.n_faces(sc) if not mesh_sel.empty else 0
        if imgui.button(f"mesh selection's {n_faces} faces -> collision") and n_faces:
            tris = [
                sc.group(*k).positions[sc.group(*k).triangles[f]].tolist()
                for k, faces in mesh_sel.faces(sc).items()
                for f in faces
            ]
            ws.do("collision", sess.collision_add, tris, flags=fl, chunk=chunk)
        imgui.same_line()
        help_marker(
            "Every selected face becomes a collision triangle with those flags: the way to make a"
            " rock face climbable: select its faces (kind 3), material 10, chunk 0. Only"
            " near-vertical triangles take the material."
        )
        if imgui.button("box collider around the mesh selection") and not mesh_sel.empty:
            lo, hi = mesh_sel.bounds(sc)
            ws.do(
                "collision",
                sess.collision_box,
                lo.tolist(),
                hi.tolist(),
                flags=fl,
                chunk=chunk,
                inflate=self.inflate,
            )
        imgui.same_line()
        imgui.set_next_item_width(80)
        _, self.inflate = imgui.input_float("inflate", self.inflate, 0, 0, "%.0f")

    def climb(self, on: bool, material: int = 10) -> None:
        ws = self.ws
        if ws.session is None or ws.col_sel.empty:
            return
        warnings = ws.session.climb_warnings(ws.col_sel.tris) if on else []
        ws.do("climb", ws.session.set_climbable, list(ws.col_sel.tris), on, material)
        if warnings:
            ws.message += "   [!] " + warnings[0]
