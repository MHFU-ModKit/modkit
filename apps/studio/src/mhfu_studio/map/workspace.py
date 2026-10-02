# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The map workspace: one section at a time, its edit session, and the map document.

On screen a row is a "map" and a section an "area" (`core.atlas`). Selecting a section loads it
alone, the way the player walks through them; its list in the document replays into a fresh
session. Everything a panel shares (the scene, the session, the selections, the last message)
lives here.
"""

from __future__ import annotations

import json
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
from mhfu_studio.shell.text import keys, plain
from mhfu_studio.shell.workspace import (
    Dock,
    Gesture,
    Job,
    Shortcut,
    ToolGroup,
    Workspace,
    register,
)
from mhfu_studio.stage import ops as O
from mhfu_studio.stage.live import CLIMB, QUEST_CATCH

from .adding import AddForm
from .core.atlas import Atlas, row_name, stage_name, stage_title
from .core.edit import (
    COLLISION,
    FACE,
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
from .core.scene import Key, MapScene, SceneError, group_name, open_stage
from .document import MANIFEST, MapDocument
from .tools import GROUPS, MOVE, ROTATE, SCALE, SELECT, VERBS, ViewportTools

if TYPE_CHECKING:
    import moderngl

    from mhfu_studio.shell.studio import Studio

    from .render.viewport import MapViewport

VILLAGE = 139
"""The section shown first when nothing else asks: Pokke village, row 0."""
SMALL = 50.0
"""A selection smaller than this is framed as a box this size around its centre."""


CLEAR = Shortcut(("Escape",), "Clears the selection, or drops a drag or a box under way")
REMOVE = Shortcut(
    ("Delete", "Backspace"), "Removes the selection (with Collision picked: its triangles)"
)
FRAME = Shortcut(("F",), "Frames the selection, or the whole area")
#: the view's keys; `MapWorkspace.key` passes on no other
KEYS = (CLEAR, REMOVE, FRAME)
#: what a click picks, per pick kind
NOUNS = {
    GROUP: "a group",
    OBJECT: "an object",
    FACE: "a triangle",
    COLLISION: "a collision triangle",
}
TOOLS = {t.id: t for g in GROUPS for t in g.tools}
TRANSFORMS = (MOVE, ROTATE, SCALE)


def count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def climbs(ops: Sequence[Op]) -> bool:
    """A collision op setting a climbable material: read at area load, so the push holds."""
    for o in ops:
        flags = o.get("flags")
        if o.get("op") == "collision" and isinstance(flags, dict):
            if flags.get("material") in CLIMB:
                return True
    return False


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
        self.message = ""
        self.load_error = ""
        self.load_time = 0.0
        #: the bank slot an import goes into (the Textures panel picks it)
        self.tex_target = 0
        #: an import re-indexes through the slot's shipped palette
        self.tex_keep = False
        #: seconds a push watches for the area reload (0 in the village); the Game panel's
        self.catch = QUEST_CATCH
        self._want: int | None = None
        self._seen = -1
        self._assets: dict[int, MapScene | None] = {}
        #: the Add panel's form; Assets places with it too
        self.add = AddForm(self)
        #: a dock to bring forward, for the window (`take_focus`)
        self._focus: str | None = None

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
        n = len(doc.stages)
        self.message = f"opened {doc.name} ({doc.path}): edits in {count(n, 'area')}"
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
                "Areas", "left", build("browser", "BrowserPanel"),
                "Every map of the game and its areas, in walking order. Click an area to load"
                " it alone, the way the player walks through them.",
            ),
            Dock(
                "View", "left", build("view", "ViewPanel"),
                "How the area is drawn: its colours, which layers show, and camera presets."
                " Changes nothing in the map.",
                shown=False,
            ),
            Dock(
                "Document", "left", build("document", "DocumentPanel"),
                "Your map edits as one document: its name, its folder and the areas it changes."
                " Open and Save are in the File menu.",
                shown=False,
            ),
            Dock(
                "Game", "left", build("game", "GamePanel"),
                "What Send to game does, putting the game's own area back, and what the last"
                " send printed.",
                shown=False,
            ),
            Dock(
                "Selection", "right", build("selection", "SelectionPanel"),
                "What you clicked in the view: move, rotate or scale it by typed amounts, remove"
                " it, and this area's edits.",
            ),
            Dock(
                "Add", "right", build("add", "AddPanel"),
                "Put a new shape, or a copy of the selection, into the area. It reuses free"
                " drawing slots, so the space left decides what fits.",
                shown=False,
            ),
            Dock(
                "Assets", "right", build("assets", "AssetsPanel"),
                "Objects and textures from the map's other areas, to copy into this one.",
                shown=False,
            ),
            Dock(
                "Groups", "right", build("groups", "GroupsPanel"),
                "Every group of the area, and the texture and colour of the one you pick.",
                shown=False,
            ),
            Dock(
                "Collision", "right", build("collision", "CollisionPanel"),
                "The invisible floors and walls the player stands on and bumps into: what is"
                " climbable, and changing the triangles you select.",
                shown=False,
            ),
            Dock(
                "Textures", "bottom", build("textures", "TexturesPanel"),
                "The area's textures: who wears each slot, and replacing one with a picture or a"
                " flat colour. Send to game shows the change in the running game.",
                shown=False,
            ),
        )  # fmt: skip

    def status(self) -> str:
        if self.data_error:
            return self.data_error
        sc = self.scene
        if sc is None:
            return "no area loaded"
        where = "" if self.row is None else f" in {row_name(self.row)}"
        return f"{stage_title(sc.stage)}{where}, loaded in {self.load_time:.2f} s"

    def close(self) -> None:
        if self.vp is not None:
            self.vp.release()
            self.vp = None

    def frame(self, dt: float) -> None:
        self.sync_renderer()

    def hud(self) -> str:
        sc = self.scene
        return stage_title(sc.stage) if sc else plain(self.data_error) or "no area loaded"

    def hint(self) -> str:
        sc, t = self.scene, self.tools
        if sc is None:
            return plain(self.data_error) or "Click an area in Areas to load it"
        sel = self.col_sel if t.kind == COLLISION else self.selection
        if sel.empty:
            if t.tool == SELECT:
                return (
                    f"Click {NOUNS[t.kind]} to select it \u00b7 shift-click adds \u00b7 drag"
                    " draws a box \u00b7 Alt-drag turns the view \u00b7 right-drag pans"
                )
            return (
                f"Click {NOUNS[t.kind]} to {VERBS[t.tool]} it \u00b7 drag turns the view"
                " \u00b7 right-drag pans"
            )
        acts = [f"drag a handle to {VERBS[t.tool]} it"] if t.gizmo_visible else []
        acts += [f"{TOOLS[x].key} {TOOLS[x].label.lower()}" for x in TRANSFORMS if x != t.tool]
        acts += [f"{keys(k.keys[:1])} {w}" for k, w in ((REMOVE, "remove"), (FRAME, "frame"))]
        acts.append(f"{keys(CLEAR.keys)} deselect")
        return f"{self.selected()}: " + " \u00b7 ".join(acts)

    def selected(self) -> str:
        """The selection in words: "2 objects in group 8"."""
        if self.tools.kind == COLLISION:
            return count(len(self.col_sel), "collision triangle")
        sel = self.selection
        ks = list(sel.vertices)
        where = group_name(ks[0]) if len(ks) == 1 else count(len(ks), "group")
        if sel.kind == GROUP:
            return where
        n = sum(len(sel.parts.get(k, [])) for k in ks)
        return f"{count(n, 'object' if sel.kind == OBJECT else 'triangle')} in {where}"

    def tool_groups(self) -> Sequence[ToolGroup]:
        return GROUPS

    def tool_on(self, group: str, tool: str) -> bool:
        return self.tools.is_on(group, tool)

    def set_tool(self, group: str, tool: str, on: bool = True) -> None:
        self.tools.choose(group, tool, on)

    def pointer(self, ev: Pointer) -> Gesture:
        return self.tools.pointer(ev)

    def key(self, ev: KeyEvent) -> bool:
        return any(ev.name in k.keys for k in KEYS) and self.tools.key(ev)

    def shortcuts(self) -> Sequence[Shortcut]:
        return KEYS

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
            self._focus = "Selection"
        elif op.get("op") == "collision" and "tri" in op:
            self.tools.set_kind(COLLISION)
            self.tools.select_collision(CollisionSelection([(int(op.get("chunk", 1)), op["tri"])]))
            self._focus = "Collision"

    def refresh(self) -> None:
        self.message = ""  # it named the edit an undo just took back
        self.tools.reseat()

    def take_focus(self) -> str | None:
        label, self._focus = self._focus, None
        return label

    # the game

    def send_blocker(self) -> str | None:
        return self.push_blocker()

    def send(self) -> Job:
        return self.push_job()

    def push_blocker(self, *, restore: bool = False) -> str | None:
        """Why the loaded section cannot be sent (restored) now; None when it can."""
        if self.game is None:
            return "no game files" + (f": {self.data_error}" if self.data_error else "")
        sc, sess = self.scene, self.session
        if sc is None or sess is None:
            return "no area loaded"
        if restore:
            return None
        if not sess.ops:
            return f"no edits to {stage_name(sc.stage)} yet"
        bad = [f for f in O.check(sess.ops, base_dir=sess.base_dir) if f.level == "error"]
        if bad:
            return f"{bad[0].where} has an error: {bad[0].message}"
        return None

    def push_job(self, halves: Sequence[str] = (), *, restore: bool = False) -> Job:
        """`studio map push` of the loaded section's list, unsaved, on stdin: `halves` of it
        (all when empty), or with `restore` the file's own bytes back. Send and the Game panel
        both run this."""
        why = self.push_blocker(restore=restore)
        if why is not None:
            raise ValueError(why)
        sc, sess, game = self.scene, self.session, self.game
        assert sc is not None and sess is not None and game is not None
        label = stage_name(sc.stage)
        argv = ["map", "push", "--stage", str(sc.stage), "--data", str(game.root)]
        if restore:
            return Job(f"restore {label} in the game", (*argv, "--restore"))
        catch = f"{self.catch:g}"
        argv += ["--ops", "-", "--catch", catch, *(f"--{h}" for h in halves)]
        if sess.base_dir is not None:
            argv += ["--base", str(sess.base_dir)]
        if (not halves or "collision" in halves) and climbs(sess.ops):
            argv += ["--hold", catch]
        what = f"{label}'s {' and '.join(halves)}" if halves else label
        return Job(f"send {what} to the game", tuple(argv), json.dumps(sess.ops).encode())

    def in_village(self) -> bool:
        """The loaded section is in row 0 (the village), whose files the game never re-reads."""
        sc, atlas = self.scene, self.atlas
        return (
            sc is not None and atlas is not None and any(r == 0 for r, _ in atlas.rows_of(sc.stage))
        )

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
        self.catch = 0.0 if self.in_village() else QUEST_CATCH
        self.selected_group = None
        entry = self.doc.stage(stage)
        ops: list[Op] = entry.ops if entry is not None else []
        self.session = EditSession(scene, ops, self.doc.directory)
        self.doc.session = self.session
        self._seen = self.session.revision
        if ops:
            refused = [f for f in self.session.findings if f.level == "error"]
            self.message = f"{stage_name(stage)}: {count(len(ops), 'edit')} applied" + (
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
                self.doc.ensure_stage(sc.stage, ops=sess.ops, row=self.row)
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

        same = sel.same(self.selection)
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
        self._seat(same)

    def set_collision_selection(self, sel: CollisionSelection) -> None:
        same = sel.tris == self.col_sel.tris
        self.col_sel = sel
        if self.vp is not None and self.vp.collision is not None:
            self.vp.collision.select(sel.tris)
        self._seat(same)

    def _seat(self, same: bool) -> None:
        """The gizmo keeps its turn over the same selection (re-derived after a rebuild)."""
        if same:
            self.tools.follow()
        else:
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
        self.tools.follow()
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
            where = ", ".join(f"{group_name(k)}: {n}" for k, n in bad.items())
            self.message += (
                f"   [!] vertices past what the model can store ({where}): export pulls them back"
            )

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
                if self.do("transform", sess.commit_collision, m):
                    self.tools.turned(m)
            return
        if not self.selection.empty:
            m = compose(pivot=self.selection.centroid(sc), by=by, rotate=rotate, scale=scale)
            if self.do("transform", sess.apply_now, self.selection, m):
                self.tools.turned(m)

    def select_group(self, key: Key | None) -> None:
        sc = self.scene
        if sc is not None:
            self.tools.select(Selection.group(sc, key) if key is not None else Selection(GROUP))


register("map", MapWorkspace)
