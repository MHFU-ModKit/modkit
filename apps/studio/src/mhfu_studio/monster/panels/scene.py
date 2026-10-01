# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Scene, View and Joints panels: what is in the file, what is drawn, and the bones."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QVBoxLayout, QWidget

from mhfu_studio.monster.render import skeleton
from mhfu_studio.monster.render.mesh import MODES
from mhfu_studio.ui import kit, theme

from .widgets import NoScene, Pages, Table, alert, put

if TYPE_CHECKING:
    from mhfu_studio.monster.render.skeleton import SkeletonOverlay
    from mhfu_studio.monster.render.viewport import MonsterViewport
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

VIEWS = {
    "front": "Front",
    "back": "Back",
    "side": "Side",
    "other_side": "Other side",
    "three": "Three-quarter",
    "top": "Top",
}
SHADING = {
    "textured": ("Textured", "The model with its own textures, as the game draws it"),
    "flat": ("Flat", "One plain colour with light: the shape alone, easier to judge"),
    "vgroup": ("Groups", "Each mesh group in its own colour: which piece is which"),
}
ISOLATE = (
    ("all", "Show all", "Draw the whole model"),
    ("only", "Only tagged", "Draw only the geometry the tagged joints carry"),
    ("hide", "Hide tagged", "Draw everything except what the tagged joints carry"),
)
FORK_TIP = (
    "FORK is the joint where the body splits front from rear; LEAD is the chain from the root"
    " down to it. A clip's location records must land on the lead chain, or half the animal"
    " lifts and the waist tears."
)


def _look(name: str) -> Callable[[MonsterViewport], object]:
    return lambda vp: vp.camera.look(name)


def _page() -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    return w, lay


class ScenePanel(kit.Panel):
    """What the opened port holds."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws = ws
        page, lay = _page()
        self.name = kit.label(role="title")
        self.game = kit.label(role="muted")
        form = kit.Form()
        self.values: dict[str, tuple[QLabel, QLabel]] = {}
        for key, text, tip in (
            ("host", "Host", "The MHFU monster this port rides: its AI, moves and hit tables"),
            ("bones", "Bones", "Joints in the skeleton; clips and hitboxes name them by number"),
            ("groups", "Mesh groups", "Pieces of the model, each drawn with one texture"),
            ("vertices", "Vertices", "Points of the model's mesh"),
            ("textures", "Textures", "Images the model is painted with"),
            ("clips", "Clips", "Animations in the file, one per slot"),
        ):
            value = kit.label(role="mono", wrap=False)
            value.setToolTip(tip)
            self.values[key] = (form.row(text, value), value)
        self.notes_box = kit.Section("Notes", tip="What the loader noticed reading the file")
        self.notes = kit.label(role="muted", selectable=True)
        self.notes_box.body.addWidget(self.notes)
        for w in (self.name, self.game, form, self.notes_box):
            lay.addWidget(w)
        self.pages = Pages(page, NoScene(studio))
        self.body.addWidget(self.pages)
        self.body.addStretch(1)

    def sync(self) -> None:
        sc = self.ws.scene
        self.pages.show_page(sc is not None)
        if sc is None:
            return
        self.name.setText(sc.name)
        self.game.setText(sc.game)
        hs = self.ws.host_species
        for key, value in (
            ("host", "" if hs is None else f"em{hs:02d}"),
            ("bones", sc.rig.n),
            ("groups", len(sc.groups)),
            ("vertices", sc.n_vertices),
            ("textures", len(sc.textures)),
            ("clips", len(sc.clips)),
        ):
            label, field = self.values[key]
            field.setText(str(value))
            label.setVisible(value != "")
            field.setVisible(value != "")
        self.notes_box.setVisible(bool(sc.notes))
        self.notes.setText("\n".join(f"• {n}" for n in sc.notes))


class ViewPanel(kit.Panel):
    """What the view shows and from where; nothing here changes the port."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        page, lay = _page()

        cam = kit.Section("Camera", tip="Where you look from; drag in the view to orbit")
        grid = QGridLayout()
        grid.setSpacing(4)
        for i, (key, text) in enumerate(VIEWS.items()):
            b = kit.button(
                text,
                tip=f"Looks at the monster from the {text.lower()} view",
                on=self._vp_act(f"look {key}", _look(key)),
            )
            grid.addWidget(b, i // 3, i % 3)
        frame = kit.button(
            "Frame",
            tip="Fits the camera to what is on screen, the host beside included",
            on=self._vp_act("frame", lambda vp: vp.camera.frame(vp.bounds())),
            icon="ph.frame-corners",
        )
        grid.addWidget(frame, 2, 0, 1, 3)
        cam.body.addLayout(grid)
        self.fov = kit.Slider(
            15.0,
            90.0,
            40.0,
            tip="The lens: narrow flattens the monster like a telephoto, wide exaggerates depth",
            decimals=0,
            on=lambda v: self._vp_act("field of view", lambda vp: setattr(vp.camera, "fov", v))(),
        )
        fov = kit.Form()
        fov.row("Field of view", self.fov)
        cam.body.addWidget(fov)

        model = kit.Section("Model", tip="How the monster's mesh is drawn")
        self.shading = kit.Segmented(
            [(k, label) for k, (label, _) in SHADING.items()],
            tip="How the mesh is coloured",
            tips={k: tip for k, (_, tip) in SHADING.items()},
            on=lambda m: self._vp_act(
                "shading", lambda vp: setattr(vp.mesh, "mode", MODES.index(m))
            )(),
        )
        self.mesh = self._toggle("Mesh", "show_mesh", "Draws the model; off leaves the bones")
        self.wire = self._toggle(
            "Wireframe", "wireframe", "Draws the mesh's triangle edges instead of its surface"
        )
        model.body.addWidget(self.shading)
        model.body.addWidget(kit.row(self.mesh, self.wire, stretch=True))

        bones = kit.Section("Skeleton", tip="The joints the animation moves")
        self.bones = self._toggle(
            "Bones",
            "show_skeleton",
            "Draws the joints and bones: amber the fork, green the lead chain, red the picked",
        )
        self.xray = self._toggle(
            "Through the mesh", "skeleton_xray", "Draws the bones on top, even inside the model"
        )
        self.ids = kit.check(
            "Joint numbers",
            tip="Writes every joint's number beside it; off shows only the picked joint's",
            on=lambda on: studio.act("joint numbers", lambda: setattr(ws, "show_joint_ids", on))(),
        )
        bones.body.addWidget(kit.row(self.bones, self.xray, stretch=True))
        bones.body.addWidget(self.ids)

        around = kit.Section("Around it", tip="What the view draws beside the monster")
        self.host = kit.check(
            "Host beside",
            tip="Loads the host species' own model and stands it beside the port, playing the"
            " clip the selected move would play: what the move really looks like",
            on=lambda on: studio.act("host beside", lambda: self._host(on))(),
        )
        self.ground = self._toggle("Ground", "show_ground", "Draws a floor grid under the feet")
        self.axes = self._toggle("Axes", "show_axes", "Draws the X, Y and Z axes at the origin")
        self.box = self._toggle(
            "Bounds", "show_bounds", "Draws the box around what is on screen; Frame fits to it"
        )
        self.points = self._toggle(
            "Bind points", "show_points", "Draws every vertex where it sits before any clip"
        )
        around.body.addWidget(self.host)
        around.body.addWidget(kit.row(self.ground, self.axes, stretch=True))
        around.body.addWidget(kit.row(self.box, self.points, stretch=True))

        for w in (cam, model, bones, around):
            lay.addWidget(w)
        self.empty = NoScene(studio)
        self.pages = Pages(page, self.empty)
        self.body.addWidget(self.pages)
        self.body.addStretch(1)

    def _vp_act(self, label: str, fn: Callable[[MonsterViewport], object]) -> Callable[..., None]:
        def run() -> None:
            if self.ws.vp is not None:
                fn(self.ws.vp)

        return self.studio.act(label, run)

    def _toggle(self, text: str, attr: str, tip: str) -> QCheckBox:
        return kit.check(
            text, tip=tip, on=lambda on: self._vp_act(text, lambda vp: setattr(vp, attr, on))()
        )

    def _host(self, on: bool) -> None:
        self.ws.show_host = on
        self.ws.sync_reference()

    def sync(self) -> None:
        ws, vp = self.ws, self.ws.vp
        ready = vp is not None and vp.mesh is not None
        self.pages.show_page(ready)
        if vp is None or vp.mesh is None:
            if ws.scene is not None:
                self.empty.say("No 3D view", self.studio.error or "The view starts in a moment.")
            return
        self.fov.set(vp.camera.fov)
        put(self.shading, MODES[vp.mesh.mode])
        for box, attr in (
            (self.mesh, "show_mesh"),
            (self.wire, "wireframe"),
            (self.bones, "show_skeleton"),
            (self.xray, "skeleton_xray"),
            (self.ground, "show_ground"),
            (self.axes, "show_axes"),
            (self.box, "show_bounds"),
            (self.points, "show_points"),
        ):
            put(box, getattr(vp, attr))
        put(self.ids, ws.show_joint_ids)
        put(self.host, ws.show_host)
        sp = ws.browsing_species
        self.host.setText("Host beside" if sp is None else f"Host em{sp:02d} beside")


class JointsPanel(kit.Panel):
    """The skeleton's joints: pick one to find it, tag some to isolate their geometry."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__(scroll=False)
        self.ws, self.studio = ws, studio
        self._picked: int | None = None
        page, lay = _page()
        self.head = kit.label(role="mono")
        self.head.setToolTip(FORK_TIP)
        self.undriven = alert()
        self.isolate = kit.Segmented(
            [(k, text) for k, text, _ in ISOLATE],
            tip="Which geometry the view draws, by the tagged joints",
            tips={k: tip for k, _, tip in ISOLATE},
            on=lambda k: self._mesh_act("isolate", [i for i, *_ in ISOLATE].index(k)),
        )
        clear = kit.button(
            "Clear tags",
            tip="Untags every joint",
            on=self._vp_act("clear tags", lambda vp: vp.tag_joints(())),
        )
        lead = kit.button(
            "Tag the lead chain",
            tip="Tags the root-to-fork chain: the joints a clip's travel may ride",
            on=self._vp_act(
                "tag lead", lambda vp: vp.tag_joints(vp.skeleton.lead if vp.skeleton else ())
            ),
        )
        self.table = Table(
            ["Tag", "Joint", "Role", "Verts"],
            tip="Every joint. Click one to pick it (red in the view); click its Tag box to paint"
            " the geometry it carries red, then isolate it above.",
            swatch_column=1,
        )
        self.table.cellClicked.connect(self._clicked)
        self._tags: tuple[object, frozenset[int]] | None = None
        for w in (self.head, self.undriven, self.isolate, kit.row(clear, lead, stretch=True)):
            lay.addWidget(w)
        lay.addWidget(self.table, 1)
        self.pages = Pages(page, NoScene(studio))
        self.body.addWidget(self.pages)

    def _vp_act(self, label: str, fn: Callable[[MonsterViewport], object]) -> Callable[..., None]:
        def run() -> None:
            vp = self.ws.vp
            if vp is not None and vp.skeleton is not None:
                fn(vp)

        return self.studio.act(label, run)

    def _mesh_act(self, label: str, isolate: int) -> None:
        self._vp_act(label, lambda vp: setattr(vp.mesh, "isolate", isolate))()

    def _clicked(self, row: int, column: int) -> None:
        vp = self.ws.vp
        if vp is None or vp.mesh is None:
            return
        if column == 0:
            tags = set(vp.mesh.tagged) ^ {row}
            self.studio.act("tag joint", lambda: vp.tag_joints(sorted(tags)))()
        else:
            j = None if vp.selected_joint == row else row
            self.studio.act("pick joint", lambda: vp.select_joint(j))()

    def sync(self) -> None:
        ws, vp = self.ws, self.ws.vp
        sk = None if vp is None else vp.skeleton
        self.pages.show_page(sk is not None)
        if vp is None or sk is None or vp.mesh is None:
            return
        self.head.setText(f"fork {sk.fork}   lead {', '.join(map(str, sk.lead)) or '-'}")
        if ws.undriven:
            joints = ", ".join(map(str, sorted(ws.undriven)))
            self.undriven.setText(
                f"{sum(ws.undriven.values())} vertices hang on joints no clip drives"
                f" ({joints}): they stay at bind, which is why they sit apart from the animal."
            )
        self.undriven.setVisible(bool(ws.undriven))
        put(self.isolate, ISOLATE[vp.mesh.isolate][0])
        counts = ws.joint_counts()
        n = len(sk.positions)
        rows = [("", str(j), role(sk, j), str(counts.get(j, 0) or "")) for j in range(n)]
        colors = [joint_color(sk, j) for j in range(n)]
        rebuilt = self.table.set_rows(rows, list(range(n)), colors=colors)
        tags = (theme.current(), frozenset(vp.mesh.tagged))
        if rebuilt or tags != self._tags:
            self._tags = tags
            on, off = theme.icon("ph.check-square-fill", accent=True), theme.icon("ph.square")
            for j in range(n):
                it = self.table.item(j, 0)
                if it is not None:
                    it.setIcon(on if j in tags[1] else off)
                    it.setToolTip("Tagged: click to untag" if j in tags[1] else "Click to tag")
        if sk.selected is None:
            self.table.clearSelection()
        elif self._picked != sk.selected:
            self.table.select_data(sk.selected)
            it = self.table.item(sk.selected, 1)
            if it is not None:
                self.table.scrollToItem(it)
        self._picked = sk.selected


def role(sk: SkeletonOverlay, j: int) -> str:
    """A joint's part in the rig: fork, lead chain, undriven."""
    bits = []
    if j == sk.fork:
        bits.append("FORK")
    elif j in sk.lead:
        bits.append("lead")
    if sk.driven is not None and j not in sk.driven:
        bits.append("undriven")
    return " ".join(bits)


def joint_color(sk: SkeletonOverlay, j: int) -> tuple[float, float, float, float]:
    """The joint's colour in the view."""
    if j == sk.selected:
        return skeleton.C_SELECTED
    if j == sk.fork:
        return skeleton.C_FORK
    if j in sk.lead:
        return skeleton.C_LEAD
    return skeleton.C_JOINT
