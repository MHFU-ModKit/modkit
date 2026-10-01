# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Add panel: edits the workspace's `AddForm` and adds what it describes."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QVBoxLayout, QWidget

from mhfu_studio.shell.text import plain
from mhfu_studio.ui import kit

from ..adding import COPY, KINDS, PLACES
from .common import Gate

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

    from ..workspace import MapWorkspace

SHAPES = {
    "box": "Box",
    "plane": "Flat plane",
    "ramp": "Ramp",
    "cylinder": "Cylinder",
    "sphere": "Sphere",
    COPY: "Copy of the selection",
}
WHERE = {
    "selection": "The selection's bottom centre",
    "click": "Where you last clicked",
    "typed": "A typed position",
}
UVS = ("Planar: the group's own texture", "Keep the slots' UVs", "The shape's own UVs")
SOLIDS = ("None", "A box around it", "Every triangle")
SIZE_LABELS = {"box": "Size", "plane": "Size (y unused)", "ramp": "Width, height, length"}
BUDGET_TIP = (
    "The group's primitives are the budget: the free ones (cleared, or empty as shipped) and,"
    " when ticked, those of the objects selected in this group. The count is an estimate; the"
    " add says what did not fit."
)


class AddPanel(kit.Panel):
    def __init__(self, ws: MapWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        f = ws.add
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        what = kit.Section("What", tip="The shape to add, and its size")
        self.what = kit.Form()
        self.shape = kit.choice(
            list(SHAPES.items()),
            tip="A simple shape to build, or a copy of what is selected in the view",
            on=self._set("shape", lambda k: KINDS.index(k)),
        )
        self.dims = kit.Vec3(
            f.size, tip="The shape's size in map units", step=50.0, decimals=0, on=self._set("size")
        )
        self.divisions = kit.integer(
            tip="How many rows and columns of squares the plane is cut into; more bend better"
            " but cost more triangles",
            lo=1,
            hi=8,
            on=self._set("divisions"),
        )
        self.radius = kit.number(
            tip="The shape's radius in map units", lo=1.0, step=25.0, on=self._set("radius")
        )
        self.tall = kit.number(
            tip="The cylinder's height in map units", lo=1.0, step=25.0, on=self._set("height")
        )
        self.rings = kit.integer(
            tip="Bands from pole to pole; more look rounder but cost more triangles",
            lo=2,
            hi=16,
            on=self._set("rings"),
        )
        self.segments = kit.integer(
            tip="Sides around the shape; more look rounder but cost more triangles",
            lo=3,
            hi=24,
            on=self._set("segments"),
        )
        self.copy_scale = kit.number(
            tip="Size of the copy against the original; 1 keeps it",
            lo=0.01,
            step=0.1,
            decimals=2,
            on=self._set("copy_scale"),
        )
        self.rotate = kit.Slider(
            -180.0,
            180.0,
            f.rotate,
            tip="Turns the shape about the vertical axis, in degrees",
            decimals=0,
            on=self._set("rotate"),
        )
        self.what.row("Shape", self.shape)
        self.size_label = self.what.row("Size", self.dims)
        for text, w in (
            ("Divisions", self.divisions),
            ("Radius", self.radius),
            ("Height", self.tall),
            ("Rings", self.rings),
            ("Segments", self.segments),
            ("Copy scale", self.copy_scale),
            ("Rotate", self.rotate),
        ):
            self.what.row(text, w)
        what.body.addWidget(self.what)
        lay.addWidget(what)

        where = kit.Section("Where", tip="Where it goes and which group draws it")
        self.where = kit.Form()
        self.place = kit.choice(
            list(WHERE.items()),
            tip="Where the shape's bottom centre goes",
            on=self._set("at_mode", lambda k: PLACES.index(k)),
        )
        self.at = kit.Vec3(
            f.at, tip="The position to add at, in map units", decimals=0, on=self._set("at")
        )
        self.at_shown = kit.label(role="muted")
        self.group = kit.choice(
            [],
            tip="The mesh group that draws the shape: it takes that group's texture and"
            " material, and spends that group's budget",
            on=self._set("group", lambda k: int(str(k))),
        )
        self.replace = kit.check(
            "Replace the selected objects",
            tip="Lets the shape take the primitives of the objects selected in this group; they"
            " stop drawing and the shape draws in their place",
            on=self._set("replace"),
        )
        self.where.row("Place at", self.place)
        self.where.row("Position", self.at)
        self.where.row("", self.at_shown)
        self.where.row("Into group", self.group)
        where.body.addWidget(self.where)
        where.body.addWidget(self.replace)
        self.fits = kit.Alert(level="info")
        self.fits.setToolTip(BUDGET_TIP)
        where.body.addWidget(self.fits)
        lay.addWidget(where)

        how = kit.Section("Finish", tip="How the shape is textured, coloured and made solid")
        form = kit.Form()
        self.uv = kit.choice(
            [(str(i), t) for i, t in enumerate(UVS)],
            tip="How the texture lies on the shape: projected flat from above, the old"
            " triangles' coordinates, or the shape's own",
            on=self._set("uv", lambda k: int(str(k))),
        )
        self.solid = kit.choice(
            [(str(i), t) for i, t in enumerate(SOLIDS)],
            tip="Invisible collision for the new shape, so the hunter bumps into it or stands"
            " on it: none, one box around it, or every triangle of it",
            on=self._set("solid", lambda k: int(str(k))),
        )
        form.row("Texture", self.uv)
        form.row("Collision", self.solid)
        self.colour = kit.check(
            "Colour it like the selection",
            tip="Gives the shape the selection's average vertex colour (the baked lighting),"
            " so it is not lit brighter than its neighbours; else it is white",
            on=self._set("colour"),
        )
        self.add = kit.button(
            "Add",
            tip="Builds the shape, saves it into the document's assets folder and draws it in"
            " the chosen group; Undo takes it back",
            on=studio.act("add", self._add),
            role="primary",
            icon="ph.plus",
        )
        self.remove = kit.button(
            "Remove the selection",
            tip="Clears the selected objects so their primitives are free for what you add",
            on=studio.act("remove", lambda: ws.remove_selected(solid=False)),
            role="danger",
        )
        self.message = kit.label(role="muted", selectable=True)
        how.body.addWidget(form)
        how.body.addWidget(self.colour)
        how.body.addWidget(kit.row(self.add, self.remove, stretch=True))
        how.body.addWidget(self.message)
        lay.addWidget(how)
        lay.addStretch(1)
        self.gate = Gate(page, "add shapes to it")
        self.body.addWidget(self.gate)

    def _set(
        self, name: str, convert: Callable[[object], object] | None = None
    ) -> Callable[[object], None]:
        """A control's slot that writes `name` of the form."""

        def run(value: object) -> None:
            v = value if convert is None else convert(value)
            self.studio.act(f"add {name}", lambda: setattr(self.ws.add, name, v))()

        return run

    def _add(self) -> None:
        self.ws.add.add()

    def sync(self) -> None:
        ws = self.ws
        if not self.gate.check(ws):
            return
        sc = ws.scene
        assert sc is not None
        f = ws.add
        kind = f.kind
        kit.refill(self.shape, list(SHAPES.items()), kind)
        kit.refill(self.place, list(WHERE.items()), PLACES[f.at_mode])
        names = [
            (str(i), g.label + ("  (far)" if g.backdrop else "")) for i, g in enumerate(sc.groups)
        ]
        target = f.target()
        kit.refill(self.group, names, str(sc.groups.index(target)) if target else None)
        kit.refill(self.uv, [(str(i), t) for i, t in enumerate(UVS)], str(f.uv))
        kit.refill(self.solid, [(str(i), t) for i, t in enumerate(SOLIDS)], str(f.solid))
        self.dims.set(f.size)
        self.at.set(f.at)
        self.rotate.set(f.rotate)
        for box, v in (
            (self.divisions, f.divisions),
            (self.radius, f.radius),
            (self.tall, f.height),
            (self.rings, f.rings),
            (self.segments, f.segments),
            (self.copy_scale, f.copy_scale),
        ):
            kit.put(box, v)
        for check, on in ((self.replace, f.replace), (self.colour, f.colour)):
            kit.put(check, on)
        shown: dict[QWidget, bool] = {
            self.dims: kind in SIZE_LABELS,
            self.divisions: kind == "plane",
            self.radius: kind in ("cylinder", "sphere"),
            self.tall: kind == "cylinder",
            self.rings: kind == "sphere",
            self.segments: kind in ("cylinder", "sphere"),
            self.copy_scale: kind == COPY,
            self.rotate: kind not in ("cylinder", "sphere"),
        }
        for row, on in shown.items():
            self.what.layout_.setRowVisible(row, on)
        self.size_label.setText(SIZE_LABELS.get(kind, "Size"))
        typed = PLACES[f.at_mode] == "typed"
        self.where.layout_.setRowVisible(self.at, typed)
        self.where.layout_.setRowVisible(self.at_shown, not typed)
        x, y, z = f.placement()
        self.at_shown.setText(f"({x:.0f}, {y:.0f}, {z:.0f})")
        self.replace.setEnabled(kind != COPY)
        need, (cap, free) = f.needs(), f.capacity()
        extra = f", +{cap - free} from the selection" if cap > free else ""
        self.fits.setText(f"Needs {need} triangles; {cap} fit here ({free} free{extra})")
        self.fits.set_level("info" if need <= cap else "error")
        self.add.setEnabled(need > 0 and (kind != COPY or not ws.selection.empty))
        self.remove.setEnabled(not ws.selection.empty)
        self.message.setText(plain(f.message))
        self.message.setVisible(bool(f.message))
