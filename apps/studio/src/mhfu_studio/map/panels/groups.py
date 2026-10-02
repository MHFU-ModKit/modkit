# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Groups panel: every group of the area, and the texture and colour of the picked one;
the totals, the far backdrop and a group's exact place under More."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from mhfu_studio.shell.camera import Bounds
from mhfu_studio.ui import kit

from ..core.edit import GROUP, Selection, count
from ..core.scene import Key, MeshGroup, group_name
from .common import Gate

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

    from ..workspace import MapWorkspace

COLUMNS = ("Group", "Texture", "Triangles", "Objects")
BUDGET_TIP = (
    "What the area can be made to draw: added shapes ride on the drawing slots each group"
    " already has."
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
            tip="Every group: click one to select it in the view, click it again to let go,"
            " double-click to point the camera at it.",
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
        self.hint = kit.label(role="muted")
        lay.addWidget(self.table)
        lay.addWidget(kit.row(self.frame, self.hide_button, self.show_all, stretch=True))
        lay.addWidget(self.hint)

        self.material = kit.Section(
            "Texture and colour",
            tip="What the picked group is drawn with; the game reads it when the area loads",
        )
        form = kit.Form()
        self.slot = kit.choice(
            [],
            tip="The texture slot the group draws with",
            on=lambda _k: None,
        )
        self.rgba = [
            kit.integer(tip=f"The colour's {name}, 0 to 255", lo=0, hi=255)
            for name in ("red", "green", "blue", "alpha")
        ]
        form.row("Texture", self.slot)
        form.row("Colour", kit.row(*self.rgba, spacing=4))
        self.shared = kit.Alert()
        self.apply = kit.button(
            "Apply material",
            tip="Writes the texture slot and colour into the group's material as one edit",
            on=studio.act("material", self._apply),
            role="primary",
        )
        self.unresolved = kit.label(
            "This group's material cannot be found in the model, so it cannot be edited.",
            role="muted",
        )
        for part in (form, self.shared, kit.row(self.apply, stretch=True), self.unresolved):
            self.material.body.addWidget(part)
        lay.addWidget(self.material)

        more = kit.More(tip="The area's totals, the backdrop's groups and the picked one's place")
        self.details = kit.label(role="muted", selectable=True)
        for w in (self.summary, self.backdrop, self.details):
            more.body.addWidget(w)
        lay.addWidget(more)
        lay.addStretch(1)
        self.gate = Gate(page, "see its groups")
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
            self.gate.need("No view yet", "The area shows here once the 3D view is up.")
            return
        mesh = vp.mesh
        t, p = sc.terrain, sc.props
        budget = sum(g.budget.triangles for g in sc.groups)
        self.summary.setText(
            f"Model: {count(len(t), 'group')}, {count(sum(g.n_faces for g in t), 'triangle')}."
            f" Second model: {count(len(p), 'group')},"
            f" {count(sum(g.n_faces for g in p), 'triangle')}.\nCan draw {budget} triangles."
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
        self.hint.setVisible(key is None)
        self.details.setVisible(key is not None)
        if key is None:
            self.hint.setText("Click a group in the list, or pick Groups (1) and click the view.")
            return
        g = sc.group(*key)
        lo, hi = g.bounds()
        tex = "none" if g.untextured else g.texture
        self.details.setText(
            f"{g.label}: material {g.material}, texture slot {tex},"
            f" {count(g.n_components, 'object')}.\nx {lo[0]:.0f}..{hi[0]:.0f}"
            f"  y {lo[1]:.0f}..{hi[1]:.0f}  z {lo[2]:.0f}..{hi[2]:.0f}"
        )
        self._sync_material(g)

    def _sync_material(self, g: MeshGroup) -> None:
        ws, sc = self.ws, self.ws.scene
        assert sc is not None and ws.session is not None
        editable = g.material is not None
        for w in (self.slot, *self.rgba, self.apply):
            w.setEnabled(editable)
        self.unresolved.setVisible(not editable)
        slots = [(str(t.index), f"slot {t.index} ({t.width} x {t.height})") for t in sc.textures]
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
        names = ", ".join(group_name(k) for k in sharers)
        self.shared.setText(f"Shared with {names}: they change too." if sharers else "")
        self.shared.setVisible(bool(sharers))


def _row(g: MeshGroup, hidden: bool) -> list[str]:
    name = g.label + (" (far)" if g.backdrop else "") + (" (hidden)" if hidden else "")
    tex = "none" if g.untextured else ("?" if g.texture is None else str(g.texture))
    return [name, tex, str(g.n_faces), str(g.n_components)]
