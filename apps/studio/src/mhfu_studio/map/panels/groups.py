# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Groups panel: every vertex group of the section, and the material editor of one."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from mhfu_studio.shell.camera import Bounds
from mhfu_studio.ui import kit

from ..core.edit import GROUP, Selection
from ..core.scene import Key, MeshGroup
from .common import Gate

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

    from ..workspace import MapWorkspace

COLUMNS = ("Group", "Faces", "Verts", "Texture", "Budget", "Objects")
BUDGET_TIP = (
    "What the section can be made to draw: a new primitive never draws, so added geometry rides"
    " on the primitives each group already has."
)


def rgba_of(word: int) -> list[int]:
    """A material colour word (red in the low byte) as four bytes."""
    return list(word.to_bytes(4, "little"))


class GroupsPanel(kit.Panel):
    def __init__(self, ws: MapWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        self.show_backdrop = True
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.summary = kit.label(role="muted")
        self.summary.setToolTip(BUDGET_TIP)
        self.backdrop = kit.check(
            "List the far backdrop",
            tip="Also lists the groups of the sky and far terrain, which sit outside the"
            " playable area",
            on=studio.act("list backdrop", self._toggle_backdrop),
            checked=True,
        )
        self.table = kit.Table(
            COLUMNS,
            tip="Every mesh group: click one to select it in the view, click it again to let go,"
            " double-click to point the camera at it. Budget is the triangles its primitives"
            " can draw.",
        )
        self.table.setMinimumHeight(220)
        self.table.picked.connect(self._pick)
        self.table.cellDoubleClicked.connect(self._double)
        self.frame = kit.button(
            "Frame",
            tip="Points the camera at the picked group",
            on=studio.act("frame group", self._frame),
            icon="ph.frame-corners",
        )
        self.hide_button = kit.button(
            "Hide",
            tip="Hides or shows the picked group in the view, to see and click what is behind"
            " it; the map itself does not change",
            on=studio.act("hide group", self._hide),
            icon="ph.eye-slash",
        )
        self.show_all = kit.button(
            "Show all",
            tip="Shows every group you hid",
            on=studio.act("show all groups", self._show_all),
            icon="ph.eye",
        )
        for w in (self.summary, self.backdrop, self.table):
            lay.addWidget(w)
        lay.addWidget(kit.row(self.frame, self.hide_button, self.show_all, stretch=True))
        self.details = kit.label(selectable=True)
        lay.addWidget(self.details)

        self.material = kit.Section(
            "Material",
            tip="The texture and colour the picked group is drawn with; the game reads them when"
            " the area loads",
        )
        form = kit.Form()
        self.slot = kit.choice(
            [],
            tip="The texture bank slot the group draws with",
            on=lambda _k: None,
        )
        self.rgba = [
            kit.integer(tip=f"The material's {name}, 0 to 255", lo=0, hi=255)
            for name in ("red", "green", "blue", "alpha")
        ]
        form.row("Texture", self.slot)
        form.row("Colour", kit.row(*self.rgba, spacing=4))
        self.shared = kit.pill("warning", "")
        self.shared.setWordWrap(True)
        self.apply = kit.button(
            "Apply material",
            tip="Writes the texture slot and colour into the group's material as one edit; Undo"
            " takes it back",
            on=studio.act("material", self._apply),
            role="primary",
        )
        self.unresolved = kit.label(
            "This group's material cannot be found in the model, so it cannot be edited.",
            role="muted",
        )
        for part in (form, self.shared, self.apply, self.unresolved):
            self.material.body.addWidget(part)
        lay.addWidget(self.material)
        lay.addStretch(1)
        self.gate = Gate(page, "see its mesh groups")
        self.body.addWidget(self.gate)
        #: the material fields were filled from (group, texture, colour word)
        self._seed: tuple[Key, int | None, int] | None = None

    def picked(self) -> Key | None:
        """The group the details and the material editor show."""
        ws = self.ws
        if ws.selected_group is not None:
            return ws.selected_group
        if len(ws.selection.vertices) == 1:
            return next(iter(ws.selection.vertices))
        return None

    def _toggle_backdrop(self) -> None:
        self.show_backdrop = self.backdrop.isChecked()

    def _pick(self, key: object) -> None:
        if not isinstance(key, tuple):
            return
        ws = self.ws

        def run() -> None:
            sc = ws.scene
            if sc is not None:
                again = ws.selected_group == key
                ws.tools.select(Selection(GROUP) if again else Selection.group(sc, key))

        self.studio.act("select group", run)()

    def _double(self, row: int, _col: int) -> None:
        item = self.table.item(row, 0)
        key = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(key, tuple):
            self.studio.act("frame group", lambda: self._frame(key))()

    def _frame(self, key: Key | None = None) -> None:
        ws, key = self.ws, key or self.picked()
        if ws.vp is not None and ws.scene is not None and key is not None:
            ws.vp.camera.frame(Bounds.of(ws.scene.group(*key).positions))

    def _hide(self) -> None:
        mesh, key = self.ws.vp.mesh if self.ws.vp else None, self.picked()
        if mesh is not None and key is not None:
            mesh.hide_group(key, not mesh.is_hidden(key))

    def _show_all(self) -> None:
        mesh, sc = self.ws.vp.mesh if self.ws.vp else None, self.ws.scene
        if mesh is not None and sc is not None:
            for g in sc.groups:
                mesh.hide_group(g.key, False)

    def _apply(self) -> None:
        ws, key = self.ws, self.picked()
        sc, sess = ws.scene, ws.session
        if key is None or sc is None or sess is None:
            return
        g = sc.group(*key)
        if g.material is None:
            return
        mat = sc.materials[g.sub][g.material]
        rgba = [b.value() for b in self.rgba]
        slot = self.slot.currentData()
        texture = int(slot) if slot is not None else None
        ws.do(
            "material",
            sess.set_material,
            key,
            texture=texture if texture != g.texture else None,
            rgba=rgba if rgba != rgba_of(mat.color) else None,
        )

    def sync(self) -> None:
        ws = self.ws
        if not self.gate.check(ws):
            return
        sc, vp = ws.scene, ws.vp
        if vp is None or vp.mesh is None or sc is None:
            self.gate.need("No view yet", "The section shows here once the 3D view is up.")
            return
        mesh = vp.mesh
        t, p = sc.terrain, sc.props
        budget = sum(g.budget.triangles for g in sc.groups)
        slots = sum(g.budget.slots for g in sc.groups)
        self.summary.setText(
            f"Terrain: {len(t)} groups, {sum(g.n_faces for g in t)} faces. Props: {len(p)}"
            f" groups, {sum(g.n_faces for g in p)} faces.\nBudget: {budget} triangles"
            f" ({slots} vertex slots)."
        )
        kit.put(self.backdrop, self.show_backdrop)
        shown = [g for g in sc.groups if self.show_backdrop or not g.backdrop]
        self.table.set_rows([_row(g, mesh.is_hidden(g.key)) for g in shown], [g.key for g in shown])
        key = self.picked()
        self.table.select_data(key)
        self.frame.setEnabled(key is not None)
        self.hide_button.setEnabled(key is not None)
        hidden = key is not None and mesh.is_hidden(key)
        self.hide_button.setText("Show" if hidden else "Hide")
        self.show_all.setEnabled(any(mesh.is_hidden(g.key) for g in sc.groups))
        self.material.setVisible(key is not None)
        if key is None:
            self.details.setText("Click a group in the list, or press 1 and click in the view.")
            return
        g = sc.group(*key)
        lo, hi = g.bounds()
        tex = "none" if g.untextured else g.texture
        self.details.setText(
            f"{g.label}: material {g.material}, texture slot {tex}, {g.n_components} objects"
            f" (welded pieces).\nx {lo[0]:.0f}..{hi[0]:.0f}  y {lo[1]:.0f}..{hi[1]:.0f}"
            f"  z {lo[2]:.0f}..{hi[2]:.0f}"
        )
        self._sync_material(g)

    def _sync_material(self, g: MeshGroup) -> None:
        ws, sc = self.ws, self.ws.scene
        assert sc is not None and ws.session is not None
        editable = g.material is not None
        for w in (self.slot, *self.rgba, self.apply):
            w.setEnabled(editable)
        self.unresolved.setVisible(not editable)
        slots = [(str(t.index), f"{t.index}  ({t.width} x {t.height})") for t in sc.textures]
        kit.refill(self.slot, slots)
        if g.material is None:
            self.shared.setVisible(False)
            return
        color = sc.materials[g.sub][g.material].color
        seed = (g.key, g.texture, color)
        if seed != self._seed:
            self._seed = seed
            kit.refill(self.slot, slots, str(g.texture if g.texture is not None else 0))
            for box, v in zip(self.rgba, rgba_of(color), strict=True):
                kit.put(box, v)
        sharers = ws.session.material_sharers(g.key)
        names = ", ".join(f"g{k[1]}" for k in sharers)
        self.shared.setText(
            f"Material {g.material} is shared with {names}: they change too." if sharers else ""
        )
        self.shared.setVisible(bool(sharers))


def _row(g: MeshGroup, hidden: bool) -> list[str]:
    name = g.label + (" (far)" if g.backdrop else "") + (" (hidden)" if hidden else "")
    tex = "none" if g.untextured else ("?" if g.texture is None else str(g.texture))
    return [
        name,
        str(g.n_faces),
        str(g.n_vertices),
        tex,
        str(g.budget.triangles),
        str(g.n_components),
    ]
