# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The map workspace: one section at a time, its edit session, and the map document.

Selecting a section loads it alone, the way the player walks through them; its list in the
document replays into a fresh session. Everything a panel shares (the scene, the session, the
selections, the last message) lives here.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable, Sequence
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from mhfu.files import Extracted

from mhfu_studio.shell.camera import Bounds
from mhfu_studio.shell.input import Key as KeyEvent
from mhfu_studio.shell.input import Pointer
from mhfu_studio.shell.overlay import Overlay
from mhfu_studio.shell.widgets import plain
from mhfu_studio.shell.workspace import Dock, Gesture, ToolGroup, Workspace, register

from .adding import AddForm
from .core.atlas import Atlas
from .core.edit import (
    COLLISION,
    GROUP,
    OBJECT,
    CollisionSelection,
    EditError,
    EditSession,
    Op,
    Selection,
    compose,
    describe_op,
)
from .core.scene import Key, MapScene, SceneError, open_stage
from .document import MANIFEST, MapDocument
from .tools import GROUPS, ViewportTools

if TYPE_CHECKING:
    import moderngl

    from mhfu_studio.shell.studio import Studio

    from .render.viewport import MapViewport

VILLAGE = 139
"""The section shown first when nothing else asks: Pokke village, row 0."""
SMALL = 50.0
"""A selection smaller than this is framed as a box this size around its centre."""


class MapWorkspace(Workspace):
    name = "map"
    filters = ("Map document", MANIFEST)

    def __init__(self, game: Extracted | None = None, atlas: Atlas | None = None) -> None:
        self.data_error = ""
        if game is None:
            try:
                game = Extracted.find()
            except FileNotFoundError as e:
                self.data_error = str(e)
        self.game = game
        if atlas is None and game is not None:
            atlas = Atlas(game)
        self.atlas = atlas
        self.doc = MapDocument.untitled()
        self.doc.game = game
        self.vp: MapViewport | None = None
        self.scene: MapScene | None = None
        self.session: EditSession | None = None
        self.row: int | None = None
        self.selection = Selection()
        self.col_sel = CollisionSelection()
        self.selected_group: Key | None = None
        self.tools = ViewportTools(self)
        #: the last action's outcome, shown in the HUD
        self.message = ""
        self.load_error = ""
        self.load_time = 0.0
        #: the bank slot an import goes into (the Textures panel picks it)
        self.tex_target = 0
        #: an import re-indexes through the slot's shipped palette
        self.tex_keep = False
        self._want: int | None = None
        self._seen = -1
        self._assets: dict[int, MapScene | None] = {}
        #: the Add panel's form; Assets places with it too
        self.add = AddForm(self)

    # the shell's hooks

    @property
    def document(self) -> MapDocument:
        return self.doc

    @property
    def viewport(self) -> MapViewport | None:
        return self.vp

    def can_open(self, path: Path) -> bool:
        return path.name == MANIFEST or (path.is_dir() and (path / MANIFEST).is_file())

    def open(self, path: Path) -> None:
        doc = MapDocument.load(path)
        doc.game = self.game
        self.doc = doc
        self._seen = -1
        self.message = f"opened {doc.path}: {doc.name}, row {doc.row}, {len(doc.stages)} stage(s)"
        if self.scene is not None:
            self.load_stage(self.scene.stage, row=self.row)
        elif doc.stages:
            self._want = doc.stages[0].number
            self.row = doc.row

    def setup(self, ctx: moderngl.Context) -> MapViewport:
        from .render.viewport import MapViewport

        self.vp = MapViewport(ctx)
        if self.atlas is not None:
            want = self._want if self._want is not None else VILLAGE
            self.load_stage(want, row=self.row if self._want is not None else 0)
        return self.vp

    def docks(self) -> Sequence[Dock]:
        def build(module: str, panel: str) -> Callable[[Studio], Any]:
            """The panel class imported when its dock is first built (Qt loads only then)."""
            return lambda studio: getattr(import_module(f"mhfu_studio.map.panels.{module}"), panel)(
                self, studio
            )

        return (
            Dock(
                "Map", "left", build("browser", "BrowserPanel"),
                "Every area of the game and its sections, in walking order. Pick a section to"
                " load it alone, the way the player walks through them.",
                focus=True,
            ),
            Dock(
                "View", "left", build("view", "ViewPanel"),
                "How the section is drawn: textures or flat colours, which layers show, camera"
                " presets and brightness. Changes nothing in the map.",
            ),
            Dock(
                "Document", "left", build("document", "DocumentPanel"),
                "Your map edits as one file: start a new one, open, save, check it for problems"
                " and export it.",
            ),
            Dock(
                "Game", "left", build("game", "GamePanel"),
                "Send this section's edits into the game running in PPSSPP, without touching"
                " the ISO.",
            ),
            Dock(
                "Selection", "right", build("selection", "SelectionPanel"),
                "What you clicked in the view: move, rotate or scale it by typed amounts, remove"
                " it, and see how much of the section's drawing budget is left.",
                focus=True,
            ),
            Dock(
                "Add", "right", build("add", "AddPanel"),
                "Put a new shape, or a copy of the selection, into the section. It reuses free"
                " drawing slots, so the budget decides what fits.",
            ),
            Dock(
                "Assets", "right", build("assets", "AssetsPanel"),
                "Objects and textures from the area's other sections, to copy into this one.",
            ),
            Dock(
                "Groups", "right", build("groups", "GroupsPanel"),
                "Every mesh group of the section, and the material settings of the one you"
                " pick.",
            ),
            Dock(
                "Collision", "right", build("collision", "CollisionPanel"),
                "The invisible floors and walls the player stands on and bumps into: what is"
                " where, what is climbable, and the selected triangles' settings.",
            ),
            Dock(
                "Textures", "bottom", build("textures", "TexturesPanel"),
                "The section's texture bank: who uses each slot, and importing a picture into"
                " one. A texture change shows in the running game at once.",
            ),
        )  # fmt: skip

    def status(self) -> str:
        if self.data_error:
            return self.data_error
        sc = self.scene
        if sc is None:
            return "no section loaded"
        return f"st{sc.stage:03d} {sc.name}   row {self.row}   loaded in {self.load_time:.2f} s"

    def close(self) -> None:
        if self.vp is not None:
            self.vp.release()
            self.vp = None

    def frame(self, dt: float) -> None:
        self.sync_renderer()

    def hud(self) -> str:
        sc = self.scene
        head = f"st{sc.stage:03d}  {sc.name}" if sc else self.data_error or "no section loaded"
        lines = [head]
        if sc is not None:
            lines.append(f"{self.tools.hint()}; F frames, Esc clears")
            sel = self.col_sel if self.tools.kind == COLLISION else self.selection
            hov = ""
            if self.tools.hover is not None:
                hov = f"  hover {sc.group(*self.tools.hover.key).label}"
            elif self.tools.col_hover is not None:
                c, t = self.tools.col_hover
                hov = f"  hover chunk {c} tri {t} ({sc.chunk(c).klass[t]})"
            lines.append(sel.describe(sc) + hov)
        if self.message:
            lines.append(plain(self.message))
        return "\n".join(lines)

    def tool_groups(self) -> Sequence[ToolGroup]:
        return GROUPS

    def tool_on(self, group: str, tool: str) -> bool:
        return self.tools.is_on(group, tool)

    def set_tool(self, group: str, tool: str, on: bool = True) -> None:
        self.tools.choose(group, tool, on)

    def pointer(self, ev: Pointer) -> Gesture:
        return self.tools.pointer(ev)

    def key(self, ev: KeyEvent) -> bool:
        return self.tools.key(ev)

    def paint(self, o: Overlay) -> None:
        self._labels(o)
        self.tools.paint(o)

    def _labels(self, o: Overlay) -> None:
        """The exits', arrivals' and spheres' names at their places, in their colours."""
        vp = self.vp
        labels = vp.labels() if vp is not None else []
        if vp is None or not labels:
            return
        w, h = o.size
        pts = vp.camera.project(np.array([lb.pos for lb in labels]), o.size)
        for lb, (x, y, z) in zip(labels, pts, strict=True):
            if 0.0 <= z <= 1.0 and 0.0 <= x <= w and 0.0 <= y <= h:
                r, g, b, _ = lb.color
                o.text((float(x) + 6.0, float(y) - 8.0), lb.text, (r, g, b, 0.95))

    def reveal(self, target: Hashable) -> None:
        """A finding's (stage, op index): load the stage and select what the op names."""
        if not isinstance(target, tuple) or len(target) != 2:
            return
        stage, i = target
        if not isinstance(stage, int):
            return
        if self.scene is None or self.scene.stage != stage:
            self.load_stage(stage)
        sess, sc = self.session, self.scene
        if sc is None or sc.stage != stage:
            return
        if sess is None or sc is None or not isinstance(i, int) or not 0 <= i < len(sess.ops):
            return
        op = sess.ops[i]
        self.message = f"op {i}: {describe_op(op)}"
        g = op.get("group")
        if isinstance(g, int) and op.get("vertices"):
            try:
                n = sc.group(int(op.get("sub", 0)), g).n_vertices
            except KeyError:
                return
            ids = np.array([v for v in op["vertices"] if 0 <= v < n], np.int64)
            self.tools.select(Selection(OBJECT, {(int(op.get("sub", 0)), g): ids}, {}))
            self.frame_selection()
        elif op.get("op") == "collision" and "tri" in op:
            self.tools.set_kind(COLLISION)
            self.tools.select_collision(CollisionSelection([(int(op.get("chunk", 1)), op["tri"])]))

    def refresh(self) -> None:
        self.message = ""  # it named the edit an undo just took back
        self.tools.reseat()

    # sections

    def load_stage(self, stage: int, *, row: int | None = None) -> bool:
        """Loads one section alone, unloading the previous one."""
        if self.game is None or self.atlas is None:
            self.load_error = self.data_error
            return False
        t0 = time.monotonic()
        try:
            scene = open_stage(self.game, stage)
        except SceneError as e:
            self.load_error = str(e)
            return False
        self.load_error = ""
        self.scene = scene
        if row is not None:
            self.row = row
        elif self.row is None or self.atlas.row(self.row).slot_of(stage) is None:
            rows = self.atlas.rows_of(stage)
            self.row = rows[0][0] if rows else self.row
        self.selected_group = None
        entry = self.doc.stage(stage)
        ops: list[Op] = entry.ops if entry is not None else []
        self.session = EditSession(scene, ops, self.doc.directory)
        self.doc.session = self.session
        self._seen = self.session.revision
        if ops:
            refused = [f for f in self.session.findings if f.level == "error"]
            self.message = f"st{stage:03d}: {len(ops)} op(s) replayed" + (
                f", {len(refused)} refused: {refused[0].message}" if refused else ""
            )
        self.selection = Selection(self.tools.kind if self.tools.kind != COLLISION else OBJECT)
        self.col_sel = CollisionSelection()
        self.tools.reseat()
        if self.vp is not None:
            self.vp.set_scene(scene, self.atlas.arrivals(stage))
            self._clear_flags()
        self.load_time = time.monotonic() - t0
        self.add.at = [float(v) for v in self.vp.framed.center] if self.vp else [0.0] * 3
        return True

    def _clear_flags(self) -> None:
        sess = self.session
        if sess is not None:
            sess.dirty.clear()
            sess.rebuilt.clear()
            sess.textures_changed = sess.collision_changed = sess.collision_preview = False

    def sync_renderer(self) -> None:
        """What the session changed -> the GPU; the selections re-derived over rebuilt groups;
        a list that got its first op joins the document."""
        vp, sess, sc = self.vp, self.session, self.scene
        if sess is None or sc is None:
            return
        if sess.revision != self._seen:
            self._seen = sess.revision
            if sess.ops and self.doc.stage(sc.stage) is None:
                self.doc.ensure_stage(sc.stage, ops=sess.ops)
        if vp is None or vp.mesh is None:
            return
        if sess.rebuilt:
            vp.mesh.rebuild(set(sess.rebuilt))
            sess.rebuilt.clear()
            sess.dirty.clear()
            self.set_selection(self.selection.refresh(sc))
        if sess.dirty:
            vp.mesh.sync(sess.dirty)
        if sess.textures_changed:
            vp.mesh.reload_textures()
            sess.textures_changed = False
        if sess.collision_changed and vp.collision is not None:
            vp.collision.rebuild()
            sess.collision_changed = sess.collision_preview = False
            keep = [
                (c, t)
                for c, t in self.col_sel.tris
                if t < sc.chunk(c).n_triangles and sc.chunk(c).alive[t]
            ]
            self.set_collision_selection(CollisionSelection(keep))
        elif sess.collision_preview and vp.collision is not None:
            vp.collision.refresh_all()
            sess.collision_preview = False

    def asset_scene(self, stage: int) -> MapScene | None:
        """Another section of the row, for the asset browser; loaded once."""
        if stage not in self._assets and self.game is not None:
            try:
                self._assets[stage] = open_stage(self.game, stage)
            except SceneError:
                self._assets[stage] = None
        return self._assets.get(stage)

    # selections and edits

    def set_selection(self, sel: Selection) -> None:
        from .render.stage_mesh import HL_SELECTED

        self.selection = sel
        mesh = self.vp.mesh if self.vp is not None else None
        if mesh is not None:
            mesh.clear_highlight(HL_SELECTED)
            for k, ids in sel.vertices.items():
                mesh.highlight(k, ids, HL_SELECTED, replace=False)
        keys = list(sel.vertices)
        self.selected_group = keys[0] if len(keys) == 1 and sel.kind == GROUP else None
        if mesh is not None:
            mesh.select_group(self.selected_group)
        if sel.kind != self.tools.kind and self.tools.kind != COLLISION:
            self.tools.kind = sel.kind
        if keys and self.scene is not None:
            self.add.group = self.scene.groups.index(self.scene.group(*keys[0]))
            self.add.at_mode = 0
        self.tools.reseat()

    def set_collision_selection(self, sel: CollisionSelection) -> None:
        self.col_sel = sel
        if self.vp is not None and self.vp.collision is not None:
            self.vp.collision.select(sel.tris)
        self.tools.reseat()

    def after_commit(self, ops: Sequence[Op]) -> None:
        if not ops:
            return
        more = f" (+{len(ops) - 3})" if len(ops) > 3 else ""
        self.message = "applied: " + "; ".join(describe_op(o) for o in ops[:3]) + more
        warnings = self.session.warnings() if self.session else []
        if warnings:
            self.message += "   [!] " + warnings[0]
        self._check_range()

    def do(self, what: str, fn: Callable[..., Any], *a: Any, **kw: Any) -> Any:
        """Runs a session edit and reports it in the HUD, or its refusal."""
        try:
            ops = fn(*a, **kw)
        except EditError as e:
            self.message = f"{what} refused: {plain(str(e))}"
            return None
        if isinstance(ops, list):
            self.after_commit(ops)
        else:
            self.message = f"{what}: done"
        self.tools.reseat()
        return ops

    def delete_selected(self) -> None:
        """The collision triangles in collision mode, else the selected objects' primitives."""
        if self.session is None:
            return
        if self.tools.kind == COLLISION:
            if not self.col_sel.empty:
                self.do("delete", self.session.collision_delete, list(self.col_sel.tris))
        elif not self.selection.empty:
            self.remove_selected(solid=False)

    def remove_selected(self, *, solid: bool) -> None:
        if self.session is None or self.selection.empty:
            return
        if self.do("remove", self.session.remove, self.selection, solid=solid):
            self.tools.select(
                Selection(self.tools.kind if self.tools.kind != COLLISION else OBJECT)
            )

    def _check_range(self) -> None:
        bad = self.session.range_check() if self.session else {}
        if bad:
            where = ", ".join(f"sub{k[0]}.g{k[1]}: {n}" for k, n in bad.items())
            self.message += f"   [!] {where} vertices outside the PMO's range: they clamp on export"

    def undo(self) -> None:
        ops = self.session.undo() if self.session else None
        self.message = f"undo: {describe_op(ops[0]) if ops else 'nothing to undo'}"
        self.tools.reseat()

    def redo(self) -> None:
        ops = self.session.redo() if self.session else None
        self.message = f"redo: {describe_op(ops[0]) if ops else 'nothing to redo'}"
        self.tools.reseat()

    def frame_selection(self) -> None:
        """The camera on the selection of the current pick kind, else on the whole section."""
        vp, sc = self.vp, self.scene
        if vp is None or sc is None:
            return
        sel = self.col_sel if self.tools.kind == COLLISION else self.selection
        if sel.empty:
            vp.frame_all()
            return
        lo, hi = sel.bounds(sc)
        b = Bounds(lo.astype(float), hi.astype(float))
        if b.radius < SMALL:
            b = Bounds(b.center - 4 * SMALL, b.center + 4 * SMALL)
        vp.camera.frame(b)

    def apply_numeric(
        self, by: Sequence[float], rotate: Sequence[float], scale: Sequence[float]
    ) -> None:
        sess, sc = self.session, self.scene
        if sess is None or sc is None:
            return
        if self.tools.kind == COLLISION:
            if not self.col_sel.empty:
                m = compose(pivot=self.col_sel.centroid(sc), by=by, rotate=rotate, scale=scale)
                sess.begin_collision(self.col_sel)
                self.do("transform", sess.commit_collision, m)
            return
        if not self.selection.empty:
            m = compose(pivot=self.selection.centroid(sc), by=by, rotate=rotate, scale=scale)
            self.do("transform", sess.apply_now, self.selection, m)

    def select_group(self, key: Key | None) -> None:
        sc = self.scene
        if sc is not None:
            self.tools.select(Selection.group(sc, key) if key is not None else Selection(GROUP))


register("map", MapWorkspace)
