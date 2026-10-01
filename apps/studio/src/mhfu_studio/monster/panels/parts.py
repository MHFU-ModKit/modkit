# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Parts panel: where he can be hit and for how much, the host's tables or the port's.

`part` (the accumulator that breaks) and `hitzone_row` (the grid row a hit is scaled by) are
different fields of one record: a Tigrex wing is part 6 and row 5."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from mhfu import hitzone
from mhfu.em.intel import HitSphere, PartIntel
from mhfu_port.manifest import Hurtbox
from PySide6.QtWidgets import QVBoxLayout, QWidget

from mhfu_studio.monster.render.hitboxes import PART_COLORS
from mhfu_studio.ui import kit

from .common import bone_span
from .widgets import ExportRow, NoScene, SaveRow, VolumeForm, alert, describe, fly_to, tiles

if TYPE_CHECKING:
    from mhfu_studio.monster.parts import PartSession
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

Vol = HitSphere | Hurtbox
HOST, PORT = "host", "port"
HOST_BONES = (
    "The bone numbers are the HOST's. This port ships its own rig, so a copied volume lands on"
    " whatever joint has that number here: a starting point you can SEE, not a correct answer."
)
PART_VS_ROW = (
    "A part is one of the eight damage counters: what breaks or severs when enough damage"
    " lands on it. The row is a different number: which line of the damage grid scales a hit"
    " there. A Tigrex wing is part 6 but row 5."
)
SOURCE_TIPS = {
    HOST: "The host monster's own tables, as the game has them: read only",
    PORT: "What this port's manifest writes over the host's tables: yours to edit",
}
GRID_NOTE = (
    "Grid writes are proven live (every byte 0xFF gave 411-damage hits); the volumes are still"
    " being tested. A * marks a column whose name is inferred."
)


def _part(v: Vol) -> int:
    return (v.part or 0) & hitzone.PART_MASK


def _row(v: Vol) -> int:
    return v.hitzone_row or 0


def _section(title: str, tip: str) -> tuple[kit.Section, QVBoxLayout]:
    s = kit.Section(title, tip=tip)
    return s, s.body


class PartsPanel(kit.Panel):
    """Where the monster can be hit: parts, hurtbox volumes and the damage grid."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        show, body = _section("Show", "Which tables, and whether the view draws their volumes")
        self.source = kit.Segmented(
            [(HOST, "Host"), (PORT, "This port")],
            tip="Whose hurtboxes and grid to show",
            tips=SOURCE_TIPS,
            on=lambda k: self._act("parts source", lambda: self._source(k)),
        )
        self.draw = kit.check(
            "Show in view",
            tip="Draws the hurtbox volumes on the monster, coloured by part",
            on=lambda on: self._act("show parts", lambda: self._show(on)),
        )
        self.xray = kit.check(
            "Through the mesh",
            tip="Draws the volumes on top, even where the body hides them",
            on=lambda on: self._act("x-ray", lambda: self._vp("hitboxes_xray", on)),
        )
        body.addWidget(self.source)
        body.addWidget(kit.row(self.draw, self.xray, stretch=True))
        self.no_intel = alert(level="info")
        body.addWidget(self.no_intel)
        lay.addWidget(show)

        parts, body = _section("Parts", PART_VS_ROW)
        self.summary = kit.label(role="muted")
        self.provenance = kit.label(role="muted")
        self.orphans = alert(level="error")
        self.parts = kit.Table(
            ["Part", "Name", "Vols", "Bones", "Row"],
            tip="The eight damage counters and the volumes that feed each. Click one to light"
            " only its volumes in the view. " + PART_VS_ROW,
        )
        self.parts.picked.connect(self._pick_part)
        self.part_name = kit.text_field(
            tip="A name for the picked part, written into the manifest's [parts]",
            placeholder="head",
        )
        self.part_name.returnPressed.connect(self._name_part)
        self.part_name.textEdited.connect(lambda t: setattr(ws, "part_name_buf", t))
        self.name_button = kit.button(
            "Name", tip="Names the picked part in the manifest (unsaved)", on=self._name_part
        )
        self.name_row = kit.row(self.part_name, self.name_button)
        for w in (self.summary, self.provenance, self.orphans, self.parts, self.name_row):
            body.addWidget(w)
        lay.addWidget(parts)

        vols, body = _section(
            "Volumes", "The port's hurtboxes: pick one here or click it in the view, then edit"
        )
        self.host_hint = kit.label(role="muted")
        self.to_port = kit.button(
            "Edit on this port",
            tip="Switches to the port's own volumes, the ones you can change",
            on=lambda: self._act("parts source", lambda: self._source(PORT)),
            role="primary",
        )
        self.add = kit.button(
            "Add a sphere",
            tip="Adds one sphere on bone 1 to the port's hurtboxes, to move where you want it",
            on=self._add,
            icon="ph.plus",
        )
        self.vols_head = kit.label(role="muted")
        self.over = alert(level="error")
        self.only = kit.check(
            "Only the picked part",
            tip="Lists only the volumes of the part picked above",
            on=lambda on: self._act("only part", lambda: setattr(ws, "only_selected_part", on)),
        )
        self.vols = kit.Table(
            ["Vol", "Bone", "Shape", "Radius", "Part", "Row", "Offset"],
            tip="The port's hurtboxes, one per row; * marks one changed since the last save."
            " Click one to edit it and light it in the view.",
            swatch_column=4,
        )
        self.vols.picked.connect(self._pick_volume)
        self.pick_hint = kit.label("Pick a volume, here or in the view, to edit it.", role="muted")
        self.part_box = kit.integer(
            tip="The damage counter this volume feeds (0 to 7). " + PART_VS_ROW,
            lo=0,
            hi=hitzone.PART_MASK,
            on=lambda v: self._stage(part=v),
        )
        self.row_box = kit.integer(
            tip="Which grid row's percentages this volume takes (0 to 6). NOT the part.",
            lo=0,
            hi=hitzone.MAX_ROW,
            on=lambda v: self._stage(hitzone_row=v),
        )
        self.form = VolumeForm(
            self._stage,
            lambda: None if ws.vp is None else ws.vp.selected_joint,
            bone_tip="A joint number of THIS port's rig. Pick a joint in the view to read it.",
            extra=[("Part", self.part_box), ("Grid row", self.row_box)],
        )
        self.keep = kit.button(
            "Keep only this",
            tip="Drops every other volume: with one left, where a hit lands is the whole answer",
            on=self._keep,
        )
        self.delete = kit.button(
            "Delete", tip="Removes this volume", on=self._delete, role="danger", icon="ph.trash"
        )
        self.copy = kit.button(
            "Duplicate", tip="Adds a copy of this volume to move", on=self._copy, icon="ph.copy"
        )
        self.look = kit.button(
            "Show in view",
            tip="Points the camera at this volume",
            on=self._act_slot("look at volume", self._look),
            icon="ph.eye",
        )
        self.form_box = QWidget()
        fl = QVBoxLayout(self.form_box)
        fl.setContentsMargins(0, 4, 0, 0)
        fl.addWidget(self.form)
        fl.addWidget(tiles(self.keep, self.copy, self.look, self.delete))
        for w in (
            self.host_hint,
            self.to_port,
            self.vols_head,
            self.over,
            self.only,
            self.vols,
            self.add,
            self.pick_hint,
            self.form_box,
        ):
            body.addWidget(w)
        self.vols_box = vols
        lay.addWidget(vols)

        grid, body = _section(
            "Damage grid",
            "How much of each kind of damage a hit takes, in percent, per row and per state",
        )
        self.grid_head = kit.label(role="muted")
        self.states = kit.Segmented(
            [("0", "state 0")],
            tip="The monster's states: normal, enraged and so on, each with its own grid",
            on=lambda k: self._act("grid state", lambda: setattr(ws, "grid_state", int(k))),
        )
        self.state_row = QWidget()
        self.state_lay = QVBoxLayout(self.state_row)
        self.state_lay.setContentsMargins(0, 0, 0, 0)
        self.state_lay.addWidget(self.states)
        self._state_names: tuple[str, ...] = ()
        self._heads: tuple[str, ...] = ()
        self.grid = kit.Grid(
            hitzone.COLUMNS,
            tip="Percent of each damage kind a hit on a row's volumes takes (0 to 255);"
            " double-click a cell to type once the port owns a grid",
            rows=[str(r) for r in range(hitzone.MAX_ROW + 1)],
        )
        self.grid.edited.connect(self._grid_edit)
        for c, col in enumerate(hitzone.COLUMNS):
            head = self.grid.horizontalHeaderItem(c)
            if head is not None:
                head.setToolTip(hitzone.COLUMN_PROVENANCE.get(col, ""))
        self.max_row = kit.button(
            "Max the picked row",
            tip="Cut, impact and shot to 255% on the picked row: every weapon does the most the"
            " grid can say there. A quick way to prove which row a spot uses.",
            on=self._max_row,
        )
        self.grid_note = alert()
        self.grid_text = kit.label(GRID_NOTE, role="muted")
        for w in (
            self.grid_head,
            self.state_row,
            self.grid,
            self.max_row,
            self.grid_note,
            self.grid_text,
        ):
            body.addWidget(w)
        lay.addWidget(grid)

        adopt, body = _section("Start from the host", "Copy the host's tables into the port")
        self.adopt_grid = kit.button(
            "Adopt the host's grid",
            tip="Copies the host's damage grid into the manifest to edit. The port inherits it"
            " at runtime either way.",
            on=self._adopt_grid,
            icon="ph.download-simple",
        )
        self.adopt_vols = kit.button(
            "Adopt the host's volumes",
            tip="Copies the host's hurtboxes into the port. " + HOST_BONES,
            on=self._adopt_volumes,
            icon="ph.download-simple",
        )
        body.addWidget(tiles(self.adopt_grid, self.adopt_vols, columns=1))
        self.adopt_box = adopt
        lay.addWidget(adopt)

        ship, body = _section("Save and ship", "Write the manifest, then send it to the game")
        self.save = SaveRow(ws, studio)
        self.export = ExportRow(ws, studio)
        body.addWidget(self.save)
        body.addWidget(self.export)
        self.ship_box = ship
        lay.addWidget(ship)
        self.no_manifest = kit.label(
            "This scene has no manifest, so there is nothing to write parts into.", role="muted"
        )
        lay.addWidget(self.no_manifest)

        self.pages = kit.Pages(page, NoScene(studio))
        self.body.addWidget(self.pages)
        self.body.addStretch(1)

    # ---- actions --------------------------------------------------------------------- #

    def _act(self, label: str, fn: Callable[[], object]) -> None:
        self.studio.act(label, fn)()

    def _act_slot(self, label: str, fn: Callable[[], object]) -> Callable[..., None]:
        return self.studio.act(label, fn)

    def _vp(self, attr: str, value: object) -> None:
        if self.ws.vp is not None:
            setattr(self.ws.vp, attr, value)

    def _source(self, key: str) -> None:
        self.ws.parts_source = key
        self.ws.sync_hitboxes()

    def _show(self, on: bool) -> None:
        self.ws.show_parts = on
        self.ws.sync_hitboxes()

    def _session(self) -> PartSession:
        sess = self.ws.part_session
        if sess is None:
            raise RuntimeError("no manifest to edit")
        return sess

    def _pick_part(self, part: object) -> None:
        if isinstance(part, int):
            pick = None if self.ws.selected_part == part else part
            self._act("pick part", lambda: self.ws.select_part(pick))

    def _name_part(self) -> None:
        part, name = self.ws.selected_part, self.part_name.text()

        def run() -> None:
            sess = self._session()
            if part is not None:
                self.ws.edit(f"part {part} = {name} (unsaved)", lambda: sess.name_part(part, name))

        self._act("name part", run)

    def _pick_volume(self, index: object) -> None:
        if isinstance(index, int):
            pick = None if self.ws.selected_volume == index else index
            self._act("pick volume", lambda: self.ws.select_volume(pick))

    def _stage(self, **fields: Any) -> None:
        i = self.ws.selected_volume

        def run() -> None:
            sess = self._session()
            if i is not None:
                msg = f"volume {i}: {describe(fields)} (unsaved)"
                self.ws.edit(msg, lambda: sess.edit_volume(i, **fields))

        self._act("edit volume", run)

    def _add(self) -> None:
        def run() -> None:
            sess = self._session()
            new = Hurtbox(bone=1, radius=150.0, part=1, hitzone_row=0, offset=[0.0, 0.0, 0.0])
            if self.ws.edit("added a sphere (unsaved)", lambda: sess.add_volume(new)):
                self.ws.select_volume(len(sess.volumes()) - 1)

        self._act("add volume", run)

    def _keep(self) -> None:
        i = self.ws.selected_volume

        def run() -> None:
            sess = self._session()
            if i is None:
                return
            gone = len(sess.volumes()) - 1
            msg = f"kept volume {i}, dropped {gone}: a hit lands there or nowhere (unsaved)"
            if self.ws.edit(msg, lambda: sess.keep_only(i)):
                self.ws.select_volume(0)

        self._act("keep only", run)

    def _delete(self) -> None:
        i = self.ws.selected_volume

        def run() -> None:
            sess = self._session()
            if i is not None and self.ws.edit(
                f"deleted volume {i} (unsaved)", lambda: sess.remove_volume(i)
            ):
                self.ws.select_volume(None)

        self._act("delete volume", run)

    def _copy(self) -> None:
        i = self.ws.selected_volume

        def run() -> None:
            sess = self._session()
            if i is None:
                return
            v = sess.volumes()[i]
            if self.ws.edit(f"volume {i} duplicated (unsaved)", lambda: sess.add_volume(v)):
                self.ws.select_volume(len(sess.volumes()) - 1)

        self._act("duplicate volume", run)

    def _look(self) -> None:
        vp = self.ws.vp
        fly_to(vp, None if vp is None else vp.hitboxes, self.ws.selected_volume)

    def _grid_edit(self, row: int, column: int, value: int) -> None:
        state = self.ws.grid_state

        def run() -> None:
            sess = self._session()
            name = sess.states()[state].name
            msg = f"{name} row {row} {hitzone.COLUMNS[column]} = {value} (unsaved)"
            self.ws.edit(msg, lambda: sess.set_hitzone(state, row, column, value))

        self._act("grid", run)

    def _max_row(self) -> None:
        state, row = self.ws.grid_state, self.grid.currentRow()

        def run() -> None:
            sess = self._session()
            if row < 0:
                self.ws.message = "pick a grid row first"
                return
            name = sess.states()[state].name
            msg = f"{name} row {row}: cut, impact, shot = 255, the most a byte says (unsaved)"
            self.ws.edit(msg, lambda: sess.fill_row(state, row, 255))

        self._act("max row", run)

    def _adopt_grid(self) -> None:
        def run() -> None:
            sess, host = self._session(), self.ws.host_parts()
            if host is None:
                return
            states = host.states
            msg = "adopted the host's grid: the port inherits it at runtime either way (unsaved)"
            if self.ws.edit(msg, lambda: sess.adopt_grid(states)):
                self._source(PORT)

        self._act("adopt grid", run)

    def _adopt_volumes(self) -> None:
        def run() -> None:
            sess, host = self._session(), self.ws.host_parts()
            if host is None:
                return
            got = sess.adopt_volumes(host.spheres(), source=f"em{self.ws.host_species or 0:02d}")
            self.ws.parts_source = PORT
            self.ws.sync()
            self.ws.message = got.describe()

        self._act("adopt volumes", run)

    # ---- sync ------------------------------------------------------------------------ #

    def volumes_now(self) -> Sequence[Vol]:
        """Whichever source the panel shows."""
        ws = self.ws
        if ws.parts_source == PORT:
            sess = ws.part_session
            return [] if sess is None else sess.volumes()
        host = ws.host_parts()
        return [] if host is None else host.spheres()

    def sync(self) -> None:
        ws, vp = self.ws, self.ws.vp
        self.pages.show_page(ws.scene is not None)
        if ws.scene is None:
            return
        host, sess = ws.host_parts(), ws.part_session
        hs = f"em{ws.host_species or 0:02d}"
        self.source.buttons[HOST].setText(f"Host {hs}")
        kit.put(self.source, ws.parts_source)
        kit.put(self.draw, ws.show_parts)
        kit.put(self.xray, vp is not None and vp.hitboxes_xray)
        self.xray.setEnabled(vp is not None)
        port = ws.parts_source == PORT
        missing = not port and host is None
        self.no_intel.setText(f"No part intel for {hs}: switch to This port, or survey the host.")
        self.no_intel.setVisible(missing)
        vols = self.volumes_now()
        self._parts(vols, host, sess, missing)
        self._volumes(sess, port)
        self._grid(host, sess)
        has_doc = sess is not None and ws.manifest is not None
        self.adopt_box.setVisible(has_doc and host is not None)
        if host is not None:
            self.adopt_grid.setVisible(host.has_grid)
            self.adopt_vols.setVisible(bool(host.spheres()))
        self.ship_box.setVisible(has_doc)
        self.save.sync()
        self.export.sync()
        self.no_manifest.setVisible(not has_doc)

    def _parts(
        self, vols: Sequence[Vol], host: PartIntel | None, sess: PartSession | None, missing: bool
    ) -> None:
        ws = self.ws
        nb = 0 if ws.scene is None else ws.scene.rig.n
        real = [v for v in vols if not v.is_marker]
        markers = "" if len(real) == len(vols) else f", +{len(vols) - len(real)} walker marker(s)"
        self.summary.setText(
            f"{len(real)} volume(s) on {len({v.bone for v in real})} bone(s) of {nb}{markers}"
        )
        prov = ""
        if ws.parts_source == HOST and host is not None:
            if host.active is not None:
                sp = ",".join(map(str, host.active.species)) or "?"
                prov = (
                    f"The set species {sp} walks: 0x{host.active.va:08X} ({host.capacity}"
                    f" records). The overlay holds {len(host.hurtboxes)}; the rest are other"
                    " species ids'."
                )
            else:
                prov = (
                    "Every hurtbox set in the overlay: this intel does not say which one the"
                    " species walks."
                )
        self.provenance.setText(prov)
        self.provenance.setVisible(bool(prov))
        lost = ws.part_orphans
        self.orphans.setText(
            f"{len(lost)} volume(s) name a bone this rig does not have"
            f" ({', '.join(str(getattr(o, 'bone', '?')) for o in lost[:6])}) and are drawn"
            " NOWHERE. Bone numbers belong to the rig that ships them."
        )
        self.orphans.setVisible(bool(lost))
        self.parts.setVisible(not missing)
        by_part: dict[int, list[Vol]] = {}
        for v in vols:
            by_part.setdefault(_part(v), []).append(v)
        rows, tips = [], []
        for i in range(hitzone.PART_MASK + 1):
            mine = by_part.get(i, [])
            name = "" if sess is None else sess.name_of(i)
            bones = sorted({v.bone for v in mine})
            rws = sorted({_row(v) for v in mine})
            rows.append(
                (
                    str(i),
                    name or ("nobody" if i == 0 else ""),
                    str(len(mine)) if mine else "",
                    bone_span(bones) if bones else "",
                    ",".join(map(str, rws)),
                )
            )
            tip = [f"bones {', '.join(map(str, bones))}"] if bones else []
            if len(rws) > 1:
                tip.append(
                    "Its volumes use DIFFERENT grid rows, so they take different percentages."
                    " Not an error: the Tigrex's wings do it."
                )
            tips.append("\n".join(tip))
        n = hitzone.PART_MASK + 1
        self.parts.set_rows(rows, list(range(n)), colors=list(PART_COLORS[:n]), tips=tips)
        self.parts.fit(n)
        if ws.selected_part is None:
            self.parts.clearSelection()
        else:
            self.parts.select_data(ws.selected_part)
        naming = ws.selected_part is not None and sess is not None
        self.name_row.setVisible(naming)
        if naming and ws.selected_part is not None:
            self.name_button.setText(f"Name part {ws.selected_part}")
            kit.put(self.part_name, ws.part_name_buf)

    def _volumes(self, sess: PartSession | None, port: bool) -> None:
        ws = self.ws
        vols = [] if sess is None else sess.volumes()
        editing = port and sess is not None
        self.vols_box.setVisible(sess is not None)
        self.host_hint.setVisible(not port)
        self.to_port.setVisible(not port)
        if not port:
            self.host_hint.setText(
                "These are the host's volumes, read only. To change them, edit them on this"
                " port" + (": adopt the host's first, below." if not vols else ".")
            )
        for w in (self.vols_head, self.vols):
            w.setVisible(editing and bool(vols))
        self.add.setVisible(editing and not vols)
        if not editing or sess is None:
            self.over.setVisible(False)
            self.only.setVisible(False)
            self.pick_hint.setVisible(False)
            self.form_box.setVisible(False)
            return
        head = f"{len(vols)} volume(s)"
        if sess.capacity is not None:
            head += f", {sess.capacity} fit in place"
        self.vols_head.setText(head if vols else "No [[hurtbox]] yet: adopt the host's, or add one")
        self.vols_head.setVisible(True)
        self.over.setText(f"{sess.over_capacity} more than fit: the runtime truncates the list")
        self.over.setVisible(sess.over_capacity > 0)
        self.only.setVisible(bool(vols) and ws.selected_part is not None)
        self.only.setText(f"Only part {ws.selected_part}")
        kit.put(self.only, ws.only_selected_part)
        rows, data, colors, tips = [], [], [], []
        for i, v in enumerate(vols):
            part = _part(v)
            if ws.only_selected_part and ws.selected_part not in (None, part):
                continue
            o = v.offset or (0.0, 0.0, 0.0)
            changed = sess.volume_changed(i)
            rows.append(
                (
                    f"{i}{' *' if changed else ''}",
                    f"0x{v.bone:X}" if v.is_marker else str(v.bone),
                    "mark" if v.is_marker else v.shape,
                    f"{v.radius:g}",
                    str(part),
                    str(_row(v)),
                    f"{o[0]:g} {o[1]:g} {o[2]:g}",
                )
            )
            data.append(i)
            colors.append(PART_COLORS[part])
            tip = ["Changed, not saved yet."] if changed else []
            if v.is_marker:
                tip.append("A walker MARKER, not a joint: 0x7D is the tail-sever skip.")
            tips.append(" ".join(tip))
        self.vols.set_rows(rows, data, colors=colors, tips=tips)
        self.vols.fit(8)
        pick = ws.selected_volume
        picked = pick is not None and 0 <= pick < len(vols)
        if picked and pick is not None:
            self.vols.select_data(pick)
            n = 0 if ws.scene is None else ws.scene.rig.n
            v = vols[pick]
            self.form.show_volume(f"Volume {pick}", v, n)
            kit.put(self.part_box, v.part or 0)
            kit.put(self.row_box, v.hitzone_row or 0)
            self.form.bone.setToolTip(
                f"A joint number of THIS port's rig ({n} joints). Pick a joint in the view to"
                " read it."
            )
        else:
            self.vols.clearSelection()
        self.pick_hint.setVisible(bool(vols) and not picked)
        self.form_box.setVisible(picked)

    def _grid(self, host: PartIntel | None, sess: PartSession | None) -> None:
        ws = self.ws
        own = [] if sess is None else sess.states()
        states: Sequence[Any] = own if own else (host.states if host is not None else ())
        editable = bool(own)
        if ws.show_state is not None:  # a finding asked for this state
            ws.grid_state, ws.show_state = ws.show_state, None
        if not states:
            self.grid_head.setText("No damage grid: adopt the host's below.")
            for w in (self.state_row, self.grid, self.max_row, self.grid_text):
                w.setVisible(False)
        else:
            tail = "" if editable else ", the HOST's (read only)"
            self.grid_head.setText(f"{len(states)} state(s){tail}")
            for w in (self.state_row, self.grid, self.grid_text):
                w.setVisible(True)
            self.max_row.setVisible(editable)
            names = tuple(getattr(st, "name", None) or f"state {i}" for i, st in enumerate(states))
            if names != self._state_names:
                self._state_names = names
                self.state_lay.removeWidget(self.states)
                self.states.deleteLater()
                self.states = kit.Segmented(
                    [(str(i), n) for i, n in enumerate(names)],
                    tip="The monster's states: normal, enraged and so on, each with its own grid",
                    on=lambda k: self._act("grid state", lambda: setattr(ws, "grid_state", int(k))),
                )
                self.state_lay.addWidget(self.states)
            state = min(max(ws.grid_state, 0), len(states) - 1)
            kit.put(self.states, str(state))
            inferred = set() if host is None else set(host.inferred_columns())
            heads = tuple(c + ("*" if c in inferred else "") for c in hitzone.COLUMNS)
            if heads != self._heads:
                self._heads = heads
                for c, text in enumerate(heads):
                    head = self.grid.horizontalHeaderItem(c)
                    if head is not None:
                        head.setText(text)
            rows = states[state].rows
            cells = [[str(v) for v in row] for row in rows]
            tips = [f"used by {o}" if (o := self.row_owner(r)) else "" for r in range(len(rows))]
            self.grid.set_cells(cells, editable=editable, tips=tips)
            self.grid.fit(len(rows))
        note = host.grid_note if host is not None else ""
        self.grid_note.setText(note)
        self.grid_note.setVisible(bool(note))

    def row_owner(self, row: int) -> str:
        """Which named parts read this grid row: what makes the grid legible."""
        sess = self.ws.part_session
        if sess is None:
            return ""
        names: list[str] = []
        for v in self.volumes_now():
            if _row(v) == row:
                n = sess.name_of(_part(v))
                if n and n not in names:
                    names.append(n)
        return ", ".join(names)
