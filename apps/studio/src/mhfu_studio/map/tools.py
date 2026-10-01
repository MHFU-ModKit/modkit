# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The viewport's tools: the toolbar's groups, hover, click and box select, the gizmo, and the
keys the window leaves to the view.

The gizmo's pose starts as T(centre of the selection). While a handle is dragged the session
previews `M = pose @ inv(pose0)`, a world matrix applied to its snapshot; the release commits
the same M.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from mhfu_studio.shell.input import Button, Key, Mod, Pointer
from mhfu_studio.shell.manipulator import Manipulation, Manipulator, Operation
from mhfu_studio.shell.overlay import Ink, Overlay
from mhfu_studio.shell.workspace import Gesture, Tool, ToolGroup

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
OPERATIONS: dict[str, Operation] = {MOVE: "translate", ROTATE: "rotate", SCALE: "scale"}
VERBS = {MOVE: "move", ROTATE: "turn", SCALE: "resize"}
SNAP, LOCAL = "snap", "local"
TOOL, PICK, OPTIONS = "tool", "pick", "options"
CLICK_SLOP = 4.0
"""A press and release closer than this is a click, not a box."""
Box = tuple[float, float, float, float]

GROUPS = (
    ToolGroup(
        TOOL,
        "Tool",
        (
            Tool(
                SELECT, "Select", "Q", "ph.cursor",
                "Click picks what is under the pointer, shift-click adds or takes it away, and a"
                " drag selects everything inside a box. Alt-drag turns the camera.",
            ),
            Tool(
                MOVE, "Move", "W", "ph.arrows-out-cardinal",
                "Drag an arrow to move the selection along it, a square to move it flat in that"
                " plane, or the dot to slide it across the view. A click elsewhere still picks.",
            ),
            Tool(
                ROTATE, "Rotate", "E", "ph.arrow-clockwise",
                "Drag a coloured ring to turn the selection about that axis, or the outer ring"
                " to turn it about your line of sight.",
            ),
            Tool(
                SCALE, "Scale", "R", "ph.arrows-out",
                "Drag a box handle to stretch the selection along that axis, or the centre box"
                " to grow it (drag right) or shrink it (drag left) evenly.",
            ),
        ),
    ),
    ToolGroup(
        PICK,
        "Pick",
        (
            Tool(
                GROUP, "Groups", "1", "ph.stack",
                "A click picks a whole mesh group: every piece of the section that shares one"
                " material and texture.",
            ),
            Tool(
                OBJECT, "Objects", "2", "ph.cube",
                "A click picks one object: the connected piece of mesh under the pointer, such"
                " as a crate, a rock or a roof.",
            ),
            Tool(
                FACE, "Faces", "3", "ph.triangle",
                "A click picks single triangles of the mesh, for fine edits or to turn them into"
                " collision in the Collision panel.",
            ),
            Tool(
                COLLISION, "Collision", "4", "ph.wall",
                "Shows the invisible collision the hunter stands on and bumps into, and a click"
                " picks its triangles instead of the mesh.",
            ),
        ),
    ),
    ToolGroup(
        OPTIONS,
        "Options",
        (
            Tool(
                SNAP, "Snap", "", "ph.magnet",
                "Gizmo drags move in whole steps: 50 units, 15 degrees, or a tenth of the size."
                " Turn it off to place things freely.",
            ),
            Tool(
                LOCAL, "Local", "", "ph.compass",
                "The gizmo's arrows follow the selection's own turn during a drag instead of the"
                " world's axes. Scaling always uses the selection's own axes.",
            ),
        ),
        toggles=True,
    ),
)  # fmt: skip


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
        #: the rubber band being dragged, in view points
        self.box: Box | None = None
        self.manipulator = Manipulator()
        self._press: tuple[float, float] | None = None
        self._pose: Array | None = None
        #: the pose a running drag started from; None when no drag runs
        self._pose0: Array | None = None

    @property
    def gizmo_visible(self) -> bool:
        if self.tool == SELECT:
            return False
        if self.kind == COLLISION:
            return not self.ws.col_sel.empty
        return not self.ws.selection.empty

    @property
    def dragging(self) -> bool:
        return self._pose0 is not None

    def pose(self) -> Array:
        """The gizmo's pose: the running drag's, else T(centre of the selection)."""
        if self._pose is None:
            sc = self.ws.scene
            assert sc is not None
            sel = self.ws.col_sel if self.kind == COLLISION else self.ws.selection
            self._pose = compose(by=sel.centroid(sc))
        return self._pose

    # the toolbar

    def is_on(self, group: str, tool: str) -> bool:
        if group == TOOL:
            return self.tool == tool
        if group == PICK:
            return self.kind == tool
        return {SNAP: self.snap, LOCAL: self.space_local}.get(tool, False)

    def choose(self, group: str, tool: str, on: bool = True) -> None:
        if group == TOOL:
            self.set_tool(tool)
        elif group == PICK:
            self.set_kind(tool)
        elif tool == SNAP:
            self.snap = on
        elif tool == LOCAL:
            self.space_local = on

    def set_tool(self, tool: str) -> None:
        if tool in TOOLS and tool != self.tool:
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

    def clear(self) -> None:
        """Selects nothing of the current pick kind."""
        if self.kind == COLLISION:
            self.select_collision(CollisionSelection())
        else:
            self.select(Selection(self.kind))

    def hint(self) -> str:
        """What the mouse does now, for the HUD."""
        if self.tool == SELECT:
            return "click picks, shift adds, drag boxes; Alt-drag orbits, right-drag pans"
        verb = VERBS[self.tool]
        if self.gizmo_visible:
            return f"drag a handle to {verb}; drag elsewhere orbits, right-drag pans"
        return f"click something to {verb} it; drag orbits, right-drag pans"

    # input

    def pointer(self, ev: Pointer) -> Gesture:
        """Hover, clicks, the box and the gizmo; returns the camera gestures they took."""
        ws = self.ws
        if ws.viewport is None or ws.scene is None or ws.session is None:
            return Gesture.NONE
        if ev.kind == "wheel":
            return Gesture.NONE
        if self.gizmo_visible:
            got = self._gizmo(ev)
            if got.consumed or got.done or got.over:
                self._unhover()
                return got.consumed
        if ev.kind == "leave":
            self._unhover()
        elif ev.kind == "move":
            self._move(ev)
        elif ev.kind == "press" and ev.button == Button.LEFT:
            if Mod.ALT in ev.mods:
                return Gesture.NONE
            self._press = ev.pos
            # a drag in the select tool is a box, not an orbit
            return Gesture.ORBIT if self.tool == SELECT else Gesture.NONE
        elif ev.kind == "release" and ev.button == Button.LEFT:
            self._release(ev)
        return Gesture.NONE

    def key(self, ev: Key) -> bool:
        ws = self.ws
        if ws.scene is None or ev.mods & (Mod.CTRL | Mod.ALT):
            return False
        if ev.name == "Escape":
            if self.dragging:
                self.end_gizmo()
            elif self._press is not None:
                self._press = self.box = None
            else:
                self.clear()
            return True
        if ev.name in ("Delete", "Backspace"):
            ws.delete_selected()
            return True
        if ev.name == "F":
            ws.frame_selection()
            return True
        return False

    def paint(self, o: Overlay) -> None:
        """The gizmo and the rubber band."""
        vp = self.ws.viewport
        if vp is None or self.ws.scene is None:
            return
        if self.gizmo_visible:
            op = OPERATIONS[self.tool]
            self.manipulator.paint(o, vp.camera, o.size, self.pose(), op, local=self.space_local)
        if self.box is not None:
            x0, y0, x1, y1 = self.box
            o.rect((x0, y0), (x1, y1), color=Ink.BOX)

    def _move(self, ev: Pointer) -> None:
        if self._press is not None and Button.LEFT in ev.buttons and self.tool == SELECT:
            px, py = self._press
            if abs(ev.x - px) > CLICK_SLOP or abs(ev.y - py) > CLICK_SLOP:
                self.box = (px, py, ev.x, ev.y)
        if self.box is not None:
            self._unhover()
        else:
            self._hover(ev.pos, ev.size)

    def _release(self, ev: Pointer) -> None:
        press, box = self._press, self.box
        self._press = self.box = None
        if press is None:
            return
        shift = Mod.SHIFT in ev.mods
        if box is not None:
            self.box_select(box, ev.size, shift)
        elif abs(ev.x - press[0]) <= CLICK_SLOP and abs(ev.y - press[1]) <= CLICK_SLOP:
            self.click(ev.pos, ev.size, shift)

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
        o, d = vp.camera.ray(mouse[0], mouse[1], size)
        if self.kind == COLLISION:
            self._set_hover(None)
            self._set_col_hover(self._pick_col(o, d))
            return
        self._set_col_hover(None)
        self._set_hover(self._pick_mesh(o, d))

    def _unhover(self) -> None:
        self._set_hover(None)
        self._set_col_hover(None)

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

    def _gizmo(self, ev: Pointer) -> Manipulation:
        ws = self.ws
        vp, sess = ws.viewport, ws.session
        assert vp is not None and sess is not None
        snap = self.snaps[self.tool] if self.snap else None
        got = self.manipulator.pointer(
            ev, vp.camera, self.pose(), OPERATIONS[self.tool], local=self.space_local, snap=snap
        )
        if got.using:
            self._pose = got.matrix
            if self._pose0 is None:  # the press that took a handle
                self._pose0 = got.matrix.copy()
                if self.kind == COLLISION:
                    sess.begin_collision(ws.col_sel)
                else:
                    sess.begin(ws.selection)
            else:
                sess.preview(got.matrix @ np.linalg.inv(self._pose0))
        elif got.done and self._pose0 is not None:
            pose0, self._pose0 = self._pose0, None
            try:
                ops = sess.commit(got.matrix @ np.linalg.inv(pose0), pose0[:3, 3])
            except EditError as e:
                ops, ws.message = [], f"refused: {e}"
            ws.after_commit(ops)
            self.reseat()
        return got

    def end_gizmo(self) -> None:
        """Abandons a running drag; the selection goes back where it was."""
        if self._pose0 is not None:
            self.manipulator.cancel()
            sess = self.ws.session
            if sess is not None and sess.previewing:
                sess.cancel()
            self._pose0 = None
        self.reseat()

    def reseat(self) -> None:
        """The gizmo follows the selection after an edit, an undo or a new selection."""
        if self._pose0 is None:
            self._pose = None
