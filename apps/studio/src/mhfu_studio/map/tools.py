# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The viewport's tools: hover, click and box select, the tool keys, and the gizmo.

The gizmo manipulates a pose that starts as T(pivot) at the selection's centre; from the frame
it is first used, the preview is `M = pose_now @ inv(pose_0)`, a world matrix the session
applies to its snapshot, and the same M is committed on release. ImGuizmo will not activate
while an imgui item is active, so while it is hovered or dragging the workspace asks the shell
for the mouse and no capture button is laid down.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from mhfu_studio.shell import gizmo
from mhfu_studio.shell.workspace import Gesture, View

from .core.edit import (
    COLLISION,
    FACE,
    GROUP,
    KINDS,
    OBJECT,
    CollisionSelection,
    EditError,
    Selection,
    compose,
)
from .core.pick import Hit, in_rect, pick, pick_collision
from .core.scene import Array
from .render.stage_mesh import HL_HOVER

if TYPE_CHECKING:
    from .workspace import MapWorkspace

SELECT, MOVE, ROTATE, SCALE = "select", "move", "rotate", "scale"
TOOLS = (SELECT, MOVE, ROTATE, SCALE)
OPERATIONS: dict[str, gizmo.Operation] = {MOVE: "translate", ROTATE: "rotate", SCALE: "scale"}
CLICK_SLOP = 4.0
"""A press and release closer than this is a click, not a box."""
Box = tuple[float, float, float, float]


class ViewportTools:
    def __init__(self, ws: MapWorkspace) -> None:
        self.ws = ws
        self.tool = SELECT
        self.kind = OBJECT
        self.space_local = False
        self.snap = False
        self.snaps = {MOVE: 50.0, ROTATE: 15.0, SCALE: 0.1}
        self.hover: Hit | None = None
        self.col_hover: tuple[int, int] | None = None
        self.last_pick: Hit | None = None
        self.box: Box | None = None
        self._press: tuple[float, float] | None = None
        self._pose: Array | None = None
        self._pose0: Array | None = None
        self._using = False

    @property
    def gizmo_visible(self) -> bool:
        if self.tool == SELECT:
            return False
        if self.kind == COLLISION:
            return not self.ws.col_sel.empty
        return not self.ws.selection.empty

    def _centroid(self) -> Array:
        sc = self.ws.scene
        assert sc is not None
        if self.kind == COLLISION:
            return self.ws.col_sel.centroid(sc)
        return self.ws.selection.centroid(sc)

    # per frame

    def input(self, view: View) -> Gesture:
        """Keys, the gizmo, hover and clicks; returns the camera gestures they took."""
        from imgui_bundle import imgui

        ws = self.ws
        if ws.viewport is None or ws.scene is None or ws.session is None:
            return Gesture.NONE
        io = imgui.get_io()
        mouse = view.mouse(io.mouse_pos.x, io.mouse_pos.y)
        self._keys(view.hovered)
        owns = self._gizmo(view) if self.gizmo_visible else False
        if not self.gizmo_visible:
            self.end_gizmo()
        if view.hovered and not owns:
            self._hover(mouse, view.size)
        else:
            self._set_hover(None)
            self._set_col_hover(None)
        if not owns:
            self._clicks(mouse, view, io.key_shift)
        if owns:
            return Gesture.ORBIT | Gesture.PAN
        # in the select tool a left-drag is a box; ALT+left-drag orbits there
        return Gesture.ORBIT if self.tool == SELECT and not io.key_alt else Gesture.NONE

    def overlay(self, view: View) -> None:
        """The selection box being dragged."""
        from imgui_bundle import imgui

        if self.box is None:
            return
        x0, y0, x1, y1 = self.box
        ox, oy = view.origin
        dl = imgui.get_window_draw_list()
        a = imgui.ImVec2(ox + min(x0, x1), oy + min(y0, y1))
        b = imgui.ImVec2(ox + max(x0, x1), oy + max(y0, y1))
        dl.add_rect_filled(a, b, imgui.get_color_u32(imgui.ImVec4(0.4, 0.7, 1.0, 0.15)))
        dl.add_rect(a, b, imgui.get_color_u32(imgui.ImVec4(0.5, 0.8, 1.0, 0.9)))

    def _keys(self, hovered: bool) -> None:
        from imgui_bundle import imgui

        if imgui.get_io().want_text_input:
            return
        k = imgui.Key
        for key, tool in ((k.q, SELECT), (k.w, MOVE), (k.e, ROTATE), (k.r, SCALE)):
            if imgui.is_key_pressed(key):
                self.set_tool(tool)
        for key, kind in ((k._1, GROUP), (k._2, OBJECT), (k._3, FACE), (k._4, COLLISION)):
            if imgui.is_key_pressed(key):
                self.set_kind(kind)
        if imgui.is_key_pressed(k.escape):
            if self.kind == COLLISION:
                self.select_collision(CollisionSelection())
            else:
                self.select(Selection(self.kind))
        if hovered and (imgui.is_key_pressed(k.delete) or imgui.is_key_pressed(k.backspace)):
            self.ws.delete_selected()
        if hovered and imgui.is_key_pressed(k.f):
            self.ws.frame_selection()

    def set_tool(self, tool: str) -> None:
        if tool != self.tool:
            self.end_gizmo()
            self.tool = tool

    def set_kind(self, kind: str) -> None:
        if kind not in KINDS or kind == self.kind:
            return
        self.end_gizmo()
        self.kind = kind
        if kind == COLLISION:
            # the collision layer is what gets picked; the mesh selection is dropped
            if self.ws.viewport is not None:
                self.ws.viewport.show_collision = True
            self.ws.set_selection(Selection(OBJECT))
        else:
            self.select_collision(CollisionSelection())
            self.select(Selection(kind))

    def select(self, sel: Selection) -> None:
        self.end_gizmo()
        self.ws.set_selection(sel)

    def select_collision(self, sel: CollisionSelection) -> None:
        self.end_gizmo()
        self.ws.set_collision_selection(sel)

    # picking

    def _visible_chunks(self) -> list[int]:
        vp, sc = self.ws.viewport, self.ws.scene
        assert vp is not None and sc is not None
        col = vp.collision
        return [c.index for c in sc.collision if col is None or col.show_chunk.get(c.index, True)]

    def _pick_col(self, o: Array, d: Array) -> tuple[int, int] | None:
        """The nearest visible collision triangle (chunk and class toggles honoured)."""
        vp, sc = self.ws.viewport, self.ws.scene
        assert vp is not None and sc is not None
        hit = pick_collision(sc, o, d, chunks=self._visible_chunks())
        if hit is None:
            return None
        klass = str(sc.chunk(hit[0]).klass[hit[1]])
        if vp.collision is not None and not vp.collision.show_class.get(klass, True):
            return None
        return hit[0], hit[1]

    def _pick_mesh(self, o: Array, d: Array) -> Hit | None:
        vp, sc = self.ws.viewport, self.ws.scene
        assert vp is not None and sc is not None and vp.mesh is not None
        hidden = [g.key for g in sc.groups if vp.mesh.is_hidden(g.key)]
        return pick(sc, o, d, hidden=hidden, backdrop=vp.mesh.show_backdrop)

    def _hover(self, mouse: tuple[float, float], size: tuple[int, int]) -> None:
        vp = self.ws.viewport
        assert vp is not None
        if self.box is not None:
            self._set_hover(None)
            self._set_col_hover(None)
            return
        o, d = vp.camera.ray(mouse[0], mouse[1], size)
        if self.kind == COLLISION:
            self._set_hover(None)
            self._set_col_hover(self._pick_col(o, d))
            return
        self._set_col_hover(None)
        self._set_hover(self._pick_mesh(o, d))

    def _set_col_hover(self, pair: tuple[int, int] | None) -> None:
        if pair != self.col_hover:
            self.col_hover = pair
            vp = self.ws.viewport
            if vp is not None and vp.collision is not None:
                vp.collision.hover(pair)

    def _part_of(self, hit: Hit) -> int:
        if self.kind == GROUP:
            return 0
        sc = self.ws.scene
        assert sc is not None
        g = sc.group(*hit.key)
        if self.kind == FACE:
            return hit.face
        return int(g.components[g.triangles[hit.face][0]])

    def _set_hover(self, hit: Hit | None) -> None:
        old, self.hover = self.hover, hit
        same = (hit is None and old is None) or (
            hit is not None
            and old is not None
            and hit.key == old.key
            and self._part_of(hit) == self._part_of(old)
        )
        vp, sc = self.ws.viewport, self.ws.scene
        if same or vp is None or vp.mesh is None or sc is None:
            return
        vp.mesh.clear_highlight(HL_HOVER)
        if hit is not None:
            sel = Selection.from_pick(sc, self.kind, hit.key, hit.face)
            for k, ids in sel.vertices.items():
                vp.mesh.highlight(k, ids, HL_HOVER, replace=False)

    def _clicks(self, mouse: tuple[float, float], view: View, shift: bool) -> None:
        from imgui_bundle import imgui

        if view.hovered and imgui.is_mouse_clicked(0):
            self._press = mouse
        if self._press is not None and imgui.is_mouse_down(0):
            dx, dy = mouse[0] - self._press[0], mouse[1] - self._press[1]
            if self.tool == SELECT and (abs(dx) > CLICK_SLOP or abs(dy) > CLICK_SLOP):
                self.box = (self._press[0], self._press[1], mouse[0], mouse[1])
        if self._press is not None and imgui.is_mouse_released(0):
            press, self._press = self._press, None
            if self.box is not None:
                box, self.box = self.box, None
                self.box_select(box, view.size, shift)
            elif abs(mouse[0] - press[0]) <= CLICK_SLOP and abs(mouse[1] - press[1]) <= CLICK_SLOP:
                self.click(mouse, view.size, shift)

    def click(self, mouse: tuple[float, float], size: tuple[int, int], shift: bool) -> None:
        ws = self.ws
        vp, sc = ws.viewport, ws.scene
        assert vp is not None and sc is not None
        o, d = vp.camera.ray(mouse[0], mouse[1], size)
        if self.kind == COLLISION:
            pair = self._pick_col(o, d)
            if pair is None:
                if not shift:
                    self.select_collision(CollisionSelection())
            elif shift:
                self.select_collision(ws.col_sel.toggle(pair))
            else:
                self.select_collision(CollisionSelection([pair]))
            return
        hit = self._pick_mesh(o, d)
        self.last_pick = hit
        if hit is None:
            if not shift:
                self.select(Selection(self.kind))
            return
        picked = Selection.from_pick(sc, self.kind, hit.key, hit.face)
        if shift and ws.selection.kind == self.kind:
            self.select(ws.selection.toggle(sc, picked))
        else:
            self.select(picked)

    def box_select(self, box: Box, size: tuple[int, int], shift: bool) -> None:
        ws = self.ws
        vp, sc = ws.viewport, ws.scene
        assert vp is not None and sc is not None and vp.mesh is not None
        x0, y0, x1, y1 = box
        if self.kind == COLLISION:
            got = list(ws.col_sel.tris) if shift else []
            col = vp.collision
            off = [k for k, on in col.show_class.items() if not on] if col is not None else []
            for ci in self._visible_chunks():
                c = sc.chunk(ci)
                if not c.n_triangles:
                    continue
                proj = vp.camera.project(c.verts.reshape(-1, 3), size)
                inside = in_rect(proj, x0, y0, x1, y1).reshape(-1, 3).all(1) & c.alive
                inside &= ~np.isin(c.klass, off)
                got += [(ci, int(t)) for t in np.nonzero(inside)[0] if (ci, int(t)) not in got]
            self.select_collision(CollisionSelection(got))
            return
        keep = shift and ws.selection.kind == self.kind
        out = ws.selection if keep else Selection(self.kind)
        for g in sc.groups:
            if not g.n_vertices or vp.mesh.is_hidden(g.key):
                continue
            if g.backdrop and not vp.mesh.show_backdrop:
                continue
            inside = in_rect(vp.camera.project(g.positions, size), x0, y0, x1, y1)
            if not inside.any():
                continue
            if self.kind == GROUP:
                out = out.add(Selection.group(sc, g.key))
            elif self.kind == OBJECT:
                for cid in np.unique(g.components[inside]):
                    out = out.add(Selection.object(sc, g.key, int(cid)))
            else:
                for f in np.nonzero(inside[g.triangles].all(1))[0]:
                    out = out.add(Selection.face(sc, g.key, int(f)))
        self.select(out)

    # the gizmo

    def _gizmo(self, view: View) -> bool:
        ws = self.ws
        vp, sess = ws.viewport, ws.session
        assert vp is not None and sess is not None
        if self._pose is None:
            self._pose = compose(by=self._centroid())
        snap = self.snaps[self.tool] if self.snap else None
        local = self.space_local or self.tool == SCALE
        got = gizmo.manipulate(
            vp.camera, view, self._pose, OPERATIONS[self.tool], local=local, snap=snap
        )
        self._pose = got.matrix
        if got.using and not self._using:
            self._pose0 = got.matrix.copy()
            if self.kind == COLLISION:
                sess.begin_collision(ws.col_sel)
            else:
                sess.begin(ws.selection)
        if self._pose0 is not None and (got.using or self._using):
            m = got.matrix @ np.linalg.inv(self._pose0)
            if got.using:
                sess.preview(m)
            else:
                try:
                    ops = sess.commit(m, self._pose0[:3, 3])
                except EditError as e:
                    ops, ws.message = [], f"refused: {e}"
                ws.after_commit(ops)
                self.reseat()
        self._using = got.using
        return got.using or got.over

    def end_gizmo(self) -> None:
        sess = self.ws.session
        if self._using and sess is not None and sess.previewing:
            sess.cancel()
        self._using = False
        self.reseat()

    def reseat(self) -> None:
        """The gizmo follows the selection after an undo or a numeric apply."""
        self._pose = self._pose0 = None
