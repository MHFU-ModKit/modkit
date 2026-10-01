# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Groups panel: every vertex group of the section, and the material editor of one."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mhfu_studio.shell.camera import Bounds
from mhfu_studio.shell.widgets import help_marker

from ..core.edit import GROUP, Selection
from ..core.scene import Key, MeshGroup

if TYPE_CHECKING:
    from ..workspace import MapWorkspace

SHARED = (1.0, 0.8, 0.3, 1.0)
COLUMNS = (
    ("", 24.0),
    ("group", 96.0),
    ("faces", 50.0),
    ("verts", 50.0),
    ("tex", 36.0),
    ("budget", 52.0),
    ("objects", 52.0),
)


def rgba_of(word: int) -> list[int]:
    """A material colour word (red in the low byte) as four bytes."""
    return list(word.to_bytes(4, "little"))


class GroupsPanel:
    def __init__(self, ws: MapWorkspace) -> None:
        self.ws = ws
        self.show_backdrop = True
        #: the material editor's fields, seeded from the group they were opened on
        self.seed: Key | None = None
        self.slot = 0
        self.rgba = [255, 255, 255, 255]

    def draw(self) -> None:
        from imgui_bundle import imgui

        ws = self.ws
        sc, vp = ws.scene, ws.vp
        if sc is None or vp is None or vp.mesh is None:
            imgui.text("no section loaded")
            return
        t, p = sc.terrain, sc.props
        imgui.text(
            f"terrain {len(t)} groups  {sum(g.n_faces for g in t)} faces  props {len(p)} groups"
            f"  {sum(g.n_faces for g in p)} faces"
        )
        budget = sum(g.budget.triangles for g in sc.groups)
        slots = sum(g.budget.slots for g in sc.groups)
        imgui.text(f"pack budget {budget} triangles  ({slots} vertex slots)")
        imgui.same_line()
        help_marker(
            "what the section can be made to DRAW: a new PRIM does not draw, so added geometry"
            " rides on the primitives each group already has."
        )
        _, self.show_backdrop = imgui.checkbox("list backdrop groups", self.show_backdrop)
        tf = imgui.TableFlags_
        flags = (
            tf.row_bg.value
            | tf.borders_inner_v.value
            | tf.scroll_y.value
            | tf.sizing_fixed_fit.value
            | tf.scroll_x.value
        )
        if imgui.begin_table("groups", len(COLUMNS), flags):
            for name, width in COLUMNS:
                imgui.table_setup_column(name, imgui.TableColumnFlags_.width_fixed.value, width)
            imgui.table_setup_scroll_freeze(0, 1)
            imgui.table_headers_row()
            for g in sc.groups:
                if g.backdrop and not self.show_backdrop:
                    continue
                self._row(g)
            imgui.end_table()
        key = ws.selected_group
        if key is None and len(ws.selection.vertices) == 1:
            key = next(iter(ws.selection.vertices))
        if key is not None:
            g = sc.group(*key)
            lo, hi = g.bounds()
            imgui.separator()
            imgui.text_wrapped(
                f"{g.label}: material {g.material}, texture slot"
                f" {'none' if g.untextured else g.texture}, {g.n_components} objects (welded"
                f" components), x {lo[0]:.0f}..{hi[0]:.0f} y {lo[1]:.0f}..{hi[1]:.0f}"
                f" z {lo[2]:.0f}..{hi[2]:.0f}"
            )
            if imgui.button("frame group"):
                vp.camera.frame(Bounds.of(g.positions))
            self._material(g)

    def _row(self, g: MeshGroup) -> None:
        from imgui_bundle import imgui

        ws = self.ws
        assert ws.vp is not None and ws.vp.mesh is not None and ws.scene is not None
        imgui.table_next_row()
        imgui.table_next_column()
        shown = not ws.vp.mesh.is_hidden(g.key)
        changed, shown = imgui.checkbox(f"##show{g.label}", shown)
        if changed:
            ws.vp.mesh.hide_group(g.key, not shown)
        imgui.table_next_column()
        selected = ws.selected_group == g.key
        label = f"{g.label}{' (far)' if g.backdrop else ''}##sel"
        if imgui.selectable(label, selected, imgui.SelectableFlags_.span_all_columns.value)[0]:
            ws.tools.select(Selection(GROUP) if selected else Selection.group(ws.scene, g.key))
            if not selected and imgui.is_mouse_double_clicked(0):
                ws.vp.camera.frame(Bounds.of(g.positions))
        tex = "none" if g.untextured else ("?" if g.texture is None else str(g.texture))
        for text in (g.n_faces, g.n_vertices, tex, g.budget.triangles, g.n_components):
            imgui.table_next_column()
            imgui.text(str(text))

    def _material(self, g: MeshGroup) -> None:
        """Repoints the group's material record (a `material` op, read at area load)."""
        from imgui_bundle import imgui

        ws = self.ws
        sc, sess = ws.scene, ws.session
        assert sc is not None
        if sess is None or g.material is None:
            imgui.text_disabled("material unresolved; cannot edit")
            return
        mat = sc.materials[g.sub][g.material]
        if self.seed != g.key:
            self.seed = g.key
            self.slot = g.texture if g.texture is not None else 0
            self.rgba = rgba_of(mat.color)
        slots = [f"{t.index}  ({t.width}x{t.height})" for t in sc.textures]
        idx = next((i for i, t in enumerate(sc.textures) if t.index == self.slot), 0)
        changed, idx = imgui.combo("texture slot", idx, slots)
        if changed and sc.textures:
            self.slot = sc.textures[idx].index
        _, self.rgba = imgui.input_int4("diffuse rgba", self.rgba)
        sharers = sess.material_sharers(g.key)
        if sharers:
            names = ", ".join(f"g{k[1]}" for k in sharers)
            imgui.text_colored(
                imgui.ImVec4(*SHARED),
                f"record {g.material} is shared with {names}: they change too",
            )
        if imgui.button("apply material"):
            rgba = [max(0, min(255, int(v))) for v in self.rgba]
            current = g.texture if g.texture is not None else -1
            ws.do(
                "material",
                sess.set_material,
                g.key,
                texture=self.slot if self.slot != current else None,
                rgba=rgba if rgba != rgba_of(mat.color) else None,
            )
