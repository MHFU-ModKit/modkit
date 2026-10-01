# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The View panel: draw mode, the layers, camera presets, the colour gain."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mhfu_studio.shell.widgets import help_marker

from ..render.stage_mesh import MODES

if TYPE_CHECKING:
    from ..workspace import MapWorkspace

PRESETS = ("iso", "top", "front", "side", "low")


class ViewPanel:
    def __init__(self, ws: MapWorkspace) -> None:
        self.ws = ws

    def draw(self) -> None:
        from imgui_bundle import imgui

        vp = self.ws.vp
        if vp is None:
            imgui.text("attaching...")
            return
        mesh = vp.mesh
        imgui.text("draw")
        if mesh is not None:
            changed, mode = imgui.combo("mode", mesh.mode, list(MODES))
            if changed:
                mesh.mode = mode
            _, mesh.gain = imgui.slider_float("colour gain", mesh.gain, 0.5, 2.5, "%.2f")
            imgui.same_line()
            help_marker(
                "texture x vertex colour. 1.0 = plain modulate; the PSP's 'double' mode would"
                " be 2.0. Not yet checked against a screenshot."
            )
            _, mesh.show_backdrop = imgui.checkbox(
                "backdrop (sky, far terrain)", mesh.show_backdrop
            )
        _, vp.wireframe = imgui.checkbox("wireframe", vp.wireframe)
        _, vp.use_fog_background = imgui.checkbox("fog colour as background", vp.use_fog_background)
        imgui.separator()
        imgui.text("layers")
        _, vp.show_mesh = imgui.checkbox("mesh", vp.show_mesh)
        _, vp.show_collision = imgui.checkbox("collision", vp.show_collision)
        if vp.show_collision:
            imgui.indent()
            _, vp.collision_fill = imgui.checkbox("fill", vp.collision_fill)
            imgui.same_line()
            _, vp.collision_edges = imgui.checkbox("edges", vp.collision_edges)
            imgui.same_line()
            _, vp.collision_xray = imgui.checkbox("x-ray", vp.collision_xray)
            imgui.unindent()
        _, vp.show_lattice = imgui.checkbox("broadphase lattice", vp.show_lattice)
        _, vp.show_exits = imgui.checkbox("exits (trigger cylinders)", vp.show_exits)
        _, vp.show_arrivals = imgui.checkbox("arrivals (landing points)", vp.show_arrivals)
        _, vp.show_spheres = imgui.checkbox("overlay spheres", vp.show_spheres)
        _, vp.show_bounds = imgui.checkbox("bounds", vp.show_bounds)
        _, vp.show_axes = imgui.checkbox("axes", vp.show_axes)
        _, vp.show_labels = imgui.checkbox("labels", vp.show_labels)
        imgui.separator()
        imgui.text("camera")
        for name in PRESETS:
            if imgui.small_button(name):
                vp.camera.look(name)
            imgui.same_line()
        imgui.new_line()
        if imgui.button("frame section (F)"):
            vp.frame_all()
        imgui.same_line()
        if imgui.button("hunter's eye"):
            vp.stand_at_entry()
        imgui.same_line()
        help_marker(
            "the eye 140 units above the first landing point, level, facing the exit table's"
            " yaw (0 = +Z assumed; the convention is unverified)."
        )
        _, vp.camera.fov = imgui.slider_float("fov", vp.camera.fov, 20.0, 90.0, "%.0f")
