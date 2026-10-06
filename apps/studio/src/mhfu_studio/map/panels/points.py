# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Points panel: the document's named points, and the one picked: its name, kind, place and
note. The Point tool sets new ones; `mhfu rig goto|walk` reads them from the saved map.toml.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QVBoxLayout, QWidget

from mhfu_studio.shell.text import plain
from mhfu_studio.ui import kit

from ..core.atlas import stage_title
from ..tools import POINT, TOOL
from .common import Gate

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

    from ..workspace import MapWorkspace

KINDS = (
    ("point", "Point: a place to go to"),
    ("waypoint", "Waypoint: a stop on the way"),
    ("climb", "Climb: the foot of a ledge"),
)
CLIMB_TIP = (
    "Degrees the hunter faces to climb, square on to the face: 0 is +z, 90 is +x. A walk may"
    " climb here on its way, and climbs on arriving."
)


def command(name: str, folder: object) -> str:
    """What takes the hunter to the point in the running game."""
    where = f" --map {folder}" if folder else ""
    return f"mhfu rig goto {name}{where}"


class PointsPanel(kit.Panel):
    def __init__(self, ws: MapWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        self.items = kit.Items(
            tip="The document's named points; this area's first. Click one to edit it (and"
            " load its area).",
            empty="No point yet: pick the Point tool (P) and click the ground.",
        )
        self.items.picked.connect(self._pick)
        add = kit.button(
            "Set a point",
            tip="Turns on the Point tool: the next click on the ground sets a point there",
            on=studio.act("point tool", lambda: ws.set_tool(TOOL, POINT)),
            icon="ph.map-pin",
        )
        self.name = kit.text_field(
            tip="The point's name, what `mhfu rig goto` takes; unique in the document",
            on=self._rename,
        )
        self.kind = kit.choice(
            KINDS,
            tip="A point is somewhere to go; a waypoint a stop on the way; a climb the foot of"
            " a ledge, which a walk may climb",
            on=lambda k: self._edit("kind", kind=k),
        )
        self.heading = kit.number(
            tip=CLIMB_TIP, lo=-180, hi=360, step=5, suffix=" deg",
            on=lambda v: self._edit("heading", heading=float(v)),
        )  # fmt: skip
        self.at = kit.Vec3(
            tip="Where the point is, in the area's world units; y is the ground",
            decimals=0,
            step=10,
            on=lambda v: self._edit("place", at=(v[0], v[1], v[2])),
        )
        self.note = kit.text_field(
            tip="Anything to remember about the point", on=lambda t: self._edit("note", note=t)
        )
        self.where = kit.label(role="muted", selectable=True)
        copy = kit.button(
            "Copy goto command",
            tip="Copies the command that takes the hunter here in the running game",
            on=self._copy,
            icon="ph.copy",
        )
        remove = kit.button(
            "Remove",
            tip="Takes the point out of the document",
            on=self._remove,
            icon="ph.trash",
        )
        form = kit.Form()
        form.row("Name", self.name)
        form.row("Kind", self.kind)
        form.row("Climb", self.heading)
        form.row("At", self.at)
        form.row("Note", self.note)
        form.row("Area", self.where)
        self.form = QWidget()
        lay = QVBoxLayout(self.form)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(form)
        lay.addWidget(kit.row(copy, remove, stretch=True))

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.items)
        lay.addWidget(kit.row(add, stretch=True))
        lay.addWidget(self.form)
        lay.addStretch(1)
        self.gate = Gate(page, "set points on it")
        self.body.addWidget(self.gate)

    # ---- actions ---------------------------------------------------------------------- #

    def _pick(self, name: object) -> None:
        if isinstance(name, str):
            self.studio.act(f"pick {name}", lambda: self.pick(name))()

    def pick(self, name: str) -> None:
        """Edit the point `name`, loading its area when another is loaded."""
        ws, p = self.ws, self.ws.doc.point(name)
        if p is None:
            return
        ws.point = name
        if p not in ws.points_here():
            ws.load_stage(p.stage)

    def _edit(self, what: str, **changes: Any) -> None:
        name = self.ws.point
        if name is not None:
            self.studio.act(
                f"point {what}", lambda: self._say(self.ws.edit_point(name, **changes))
            )()

    def _rename(self, text: str) -> None:
        if self.ws.point is not None and text.strip() != self.ws.point:
            self._edit("name", name=text.strip())

    def _say(self, refusal: str | None) -> None:
        if refusal:
            self.studio.message = f"point not changed: {refusal}"

    def _remove(self) -> None:
        name = self.ws.point
        if name is not None:
            self.studio.act(f"remove {name}", lambda: self.ws.remove_point(name))()

    def _copy(self) -> None:
        if self.ws.point is not None:
            text = command(self.ws.point, self.ws.doc.directory)
            QGuiApplication.clipboard().setText(text)
            self.studio.message = f"copied: {text}"

    # ---- sync ------------------------------------------------------------------------- #

    def sync(self) -> None:
        ws = self.ws
        if not self.gate.check(ws, section=False):
            return
        here = {p.name for p in ws.points_here()}
        ordered = sorted(ws.doc.points, key=lambda p: p.name not in here)
        self.items.set_items(
            [
                kit.Item(
                    f"{p.name}  ({stage_title(p.stage)})"
                    + ("" if p.kind == "point" else f", {p.kind}"),
                    p.name,
                    plain(p.note) or command(p.name, ws.doc.directory),
                )
                for p in ordered
            ]
        )
        p = ws.doc.point(ws.point) if ws.point else None
        self.form.setVisible(p is not None)
        if p is None:
            return
        kit.put(self.name, p.name)
        kit.put(self.kind, p.kind)
        kit.put(self.heading, p.heading or 0.0)
        self.heading.setEnabled(p.kind == "climb")
        kit.put(self.at, p.at)
        kit.put(self.note, p.note)
        self.where.setText(f"{stage_title(p.stage)} (st{p.stage:03d})")
