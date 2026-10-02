# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The View panel: draw mode, the layers, camera presets, the colour gain. Nothing here
changes the map."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QCheckBox, QVBoxLayout, QWidget

from mhfu_studio.ui import kit

from ..render.stage_mesh import MODES
from ..render.viewport import LENS
from .common import Gate

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

    from ..render.viewport import MapViewport
    from ..workspace import MapWorkspace

MODE_TIPS = (
    "textured: texture times vertex colour, as the game draws it",
    "vertex colour: the baked lighting alone, to judge the shading",
    "texture only: the textures without lighting, to see what a slot looks like",
    "by group: each mesh group its own colour, to see where one ends",
    "flat: one colour, to read the shape alone",
)
VIEW_TIPS = {
    "iso": "From above at an angle: the overview",
    "top": "Straight down, like a map",
    "front": "Level from the front",
    "back": "Level from the back",
    "side": "Level from the side",
    "low": "Close to the ground, looking across",
}
Owner = Callable[[], object | None]


class ViewPanel(kit.Panel):
    def __init__(self, ws: MapWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        self._toggles: list[tuple[QCheckBox, Owner, str]] = []

        def mesh() -> object | None:
            return ws.vp.mesh if ws.vp is not None else None

        def vp() -> object | None:
            return ws.vp

        def lens() -> object | None:
            return ws.vp.camera if ws.vp is not None else None

        draw = kit.Section("Draw", tip="How the section's surfaces are drawn")
        self.mode = kit.choice(
            [(str(i), m) for i, m in enumerate(MODES)],
            tip="What colours the surfaces.\n" + "\n".join(MODE_TIPS),
            on=lambda m: self._set(mesh, "mode", int(m)),
        )
        self.gain = kit.Slider(
            0.5,
            2.5,
            1.0,
            tip="Brightness: texture times vertex colour times this. 1.0 is a plain multiply;"
            " the PSP's 'double' mode would be 2.0. Not yet checked against a screenshot.",
            on=lambda v: self._set(mesh, "gain", v),
        )
        form = kit.Form()
        form.row("Colours", self.mode)
        form.row("Gain", self.gain)
        draw.body.addWidget(form)
        for text, tip, owner, attr in (
            (
                "Backdrop (sky, far terrain)",
                "Shows the sky dome and the far scenery around the playable area",
                mesh,
                "show_backdrop",
            ),
            ("Wireframe", "Draws the triangle edges over the surfaces", vp, "wireframe"),
            (
                "Fog colour as background",
                "Fills the empty background with the section's own fog colour, as in the"
                " game; off, it is a neutral grey",
                vp,
                "use_fog_background",
            ),
        ):
            draw.body.addWidget(self._toggle(text, tip, owner, attr))

        layers = kit.Section("Layers", tip="What is drawn besides the section itself")
        layers.body.addWidget(
            self._toggle("Mesh", "The visible scenery: what the player sees", vp, "show_mesh")
        )
        layers.body.addWidget(
            self._toggle(
                "Collision",
                "The invisible floors and walls the player stands on and bumps into, coloured"
                " by kind: floor, wall, climbable, sinking, wading",
                vp,
                "show_collision",
            )
        )
        self.collision = [
            self._toggle("Fill", "Draws the collision triangles filled", vp, "collision_fill"),
            self._toggle("Edges", "Draws the collision triangles' edges", vp, "collision_edges"),
            self._toggle(
                "X-ray",
                "Shows the collision through the scenery, so walls behind a hill show too",
                vp,
                "collision_xray",
            ),
        ]
        sub = kit.row(*self.collision, stretch=True, spacing=12)
        sub.setContentsMargins(22, 0, 0, 0)
        layers.body.addWidget(sub)
        for text, tip, attr in (
            (
                "Broadphase lattice",
                "The grid the game uses to find nearby collision quickly; a triangle is only"
                " solid where its cells list it",
                "show_lattice",
            ),
            (
                "Exits",
                "The invisible cylinders that take the player to another section",
                "show_exits",
            ),
            (
                "Arrivals",
                "Where the player lands when coming in from another section",
                "show_arrivals",
            ),
            (
                "Overlay spheres",
                "Spots the section's code places, such as the supply box's prompt",
                "show_spheres",
            ),
            ("Bounds", "The box around the section's playable area", "show_bounds"),
            ("Axes", "The world's x, y and z axes at the origin", "show_axes"),
            ("Labels", "Names over the exits, arrivals and spheres", "show_labels"),
        ):
            layers.body.addWidget(self._toggle(text, tip, vp, attr))

        camera = kit.Section("Camera", tip="Where the view looks from")
        names = list(LENS.views)
        half = (len(names) + 1) // 2
        for part in (names[:half], names[half:]):
            camera.body.addWidget(
                kit.row(
                    *(
                        kit.button(
                            n.capitalize(),
                            tip=VIEW_TIPS.get(n, f"The {n} view"),
                            on=self._look(n),
                        )
                        for n in part
                    ),
                    stretch=True,
                )
            )
        camera.body.addWidget(
            kit.row(
                kit.button(
                    "Frame section",
                    tip="Fits the whole section into the view (F in the view)",
                    on=self._camera("frame section", lambda v: v.frame_all()),
                ),
                kit.button(
                    "Hunter's eye",
                    tip="Stands the camera where the player arrives, at eye height, facing the"
                    " way the exit table says (0 is assumed to face +Z; unverified)",
                    on=self._camera("hunter's eye", lambda v: v.stand_at_entry()),
                ),
                stretch=True,
            )
        )
        self.fov = kit.Slider(
            20.0,
            90.0,
            LENS.fov,
            tip="Field of view in degrees: wider shows more, narrower looks like a telephoto",
            decimals=0,
            on=lambda v: self._set(lens, "fov", v),
        )
        fov = kit.Form()
        fov.row("Field of view", self.fov)
        camera.body.addWidget(fov)

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        for s in (draw, layers, camera):
            lay.addWidget(s)
        lay.addStretch(1)
        self.gate = Gate(page, "")
        self.body.addWidget(self.gate)

    def _toggle(self, text: str, tip: str, owner: Owner, attr: str) -> QCheckBox:
        b = kit.check(text, tip=tip, on=lambda on: self._set(owner, attr, on))
        self._toggles.append((b, owner, attr))
        return b

    def _set(self, owner: Owner, attr: str, value: object) -> None:
        def run() -> None:
            o = owner()
            if o is not None:
                setattr(o, attr, value)

        self.studio.act(f"view {attr}", run)()

    def _look(self, view: str) -> Callable[[], None]:
        return self._camera(f"{view} view", lambda v: v.look(view))

    def _camera(self, label: str, fn: Callable[[MapViewport], object]) -> Callable[[], None]:
        def run() -> None:
            if self.ws.vp is not None:
                fn(self.ws.vp)

        return self.studio.act(label, run)

    def sync(self) -> None:
        ws, vp = self.ws, self.ws.vp
        if not self.gate.check(ws, section=False):
            return
        if vp is None:
            why = self.studio.error
            self.gate.need(
                "No 3D view" if why else "The view is starting",
                f"The 3D view could not start: {why}"
                if why
                else "The view sets itself up when it first draws; its settings show here then.",
            )
            return
        for b, owner, attr in self._toggles:
            o = owner()
            b.setEnabled(o is not None)
            if o is not None:
                with QSignalBlocker(b):
                    b.setChecked(bool(getattr(o, attr)))
        for b in self.collision:
            b.setEnabled(vp.show_collision)
        mesh = vp.mesh
        self.mode.setEnabled(mesh is not None)
        self.gain.setEnabled(mesh is not None)
        if mesh is not None:
            kit.refill(self.mode, [(str(i), m) for i, m in enumerate(MODES)], str(mesh.mode))
            self.gain.set(mesh.gain)
        self.fov.set(vp.camera.fov)
