# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The View panel: how the area is drawn, its layers and camera presets; the expert layers
and the colour gain under More. Nothing here changes the map. Collision shows from the
toolbar and looks as the Collision panel says."""

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

#: per entry of `MODES`: its name in the list and what it is for
MODE_NAMES = (
    ("As in the game", "texture times vertex colour, as the game draws it"),
    ("Lighting only", "the baked lighting alone, to judge the shading"),
    ("Textures only", "the textures without lighting"),
    ("A colour per group", "each group its own colour, to see where one ends"),
    ("One flat colour", "to read the shape alone"),
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


def modes() -> list[tuple[str, str]]:
    return [(str(i), MODE_NAMES[i][0]) for i in range(len(MODES))]


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

        draw = kit.Section("Draw", tip="How the area's surfaces are drawn")
        self.mode = kit.choice(
            modes(),
            tip="What colours the surfaces.\n" + "\n".join(f"{n}: {t}" for n, t in MODE_NAMES),
            on=lambda m: self._set(mesh, "mode", int(m)),
        )
        form = kit.Form()
        form.row("Colours", self.mode)
        draw.body.addWidget(form)
        draw.body.addWidget(
            self._toggle(
                "Backdrop (sky, far terrain)",
                "Shows the sky dome and the far scenery around the playable area",
                mesh,
                "show_backdrop",
            )
        )
        draw.body.addWidget(
            self._toggle("Wireframe", "Draws the triangle edges over the surfaces", vp, "wireframe")
        )

        layers = kit.Section("Layers", tip="What is drawn besides the area itself")
        for text, tip, attr in (
            ("Mesh", "The visible scenery: what the player sees", "show_mesh"),
            ("Exits", "The invisible cylinders that take the player to another area", "show_exits"),
            ("Arrivals", "Where the player lands coming in from another area", "show_arrivals"),
            ("Labels", "Names over the exits, arrivals and marker spheres", "show_labels"),
        ):
            layers.body.addWidget(self._toggle(text, tip, vp, attr))
        layers.body.addWidget(
            kit.label(
                "Collision: Show collision on the toolbar (C); the Collision panel sets how it"
                " looks.",
                role="muted",
            )
        )

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
                    "Frame area",
                    tip="Fits the whole area into the view (F in the view)",
                    on=self._camera("frame area", lambda v: v.frame_all()),
                ),
                kit.button(
                    "Hunter's eye",
                    tip="Stands the camera where the player arrives, at eye height (which way"
                    " it faces is a guess)",
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

        more = kit.More(tip="Brightness, and the layers for checking the game's own data")
        self.gain = kit.Slider(
            0.5,
            2.5,
            1.0,
            tip="Texture times vertex colour times this; 1.0 is a plain multiply. Not yet"
            " checked against the game.",
            on=lambda v: self._set(mesh, "gain", v),
        )
        gain = kit.Form()
        gain.row("Brightness", self.gain)
        more.body.addWidget(gain)
        for text, tip, owner, attr in (
            (
                "Fog colour as background",
                "Fills the empty background with the area's fog colour, as the game does",
                vp,
                "use_fog_background",
            ),
            (
                "Collision grid",
                "The grid the game looks collision up in; a triangle is solid only where its"
                " cells list it",
                vp,
                "show_lattice",
            ),
            (
                "Marker spheres",
                "Spots the area's code places, such as the supply box's prompt",
                vp,
                "show_spheres",
            ),
            ("Play area box", "The box around the area's playable part", vp, "show_bounds"),
            ("Axes", "The world's x, y and z axes at the origin", vp, "show_axes"),
        ):
            more.body.addWidget(self._toggle(text, tip, owner, attr))

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        for s in (draw, layers, camera, more):
            lay.addWidget(s)
        lay.addStretch(1)
        self.gate = Gate(page, "")
        self.body.addWidget(self.gate)

    def _toggle(self, text: str, tip: str, owner: Owner, attr: str) -> QCheckBox:
        """A checkbox showing `owner().attr`."""
        b = kit.check(text, tip=tip, on=lambda v: self._set(owner, attr, v))
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
        mesh = vp.mesh
        self.mode.setEnabled(mesh is not None)
        self.gain.setEnabled(mesh is not None)
        if mesh is not None:
            kit.refill(self.mode, modes(), str(mesh.mode))
            self.gain.set(mesh.gain)
        self.fov.set(vp.camera.fov)
