# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Parts panel: where he can be hit and for how much, the base monster's tables or yours.

`part` (the counter that breaks) and `hitzone_row` (the grid row a hit is scaled by) are
different fields of one record: a Tigrex wing is part 6 and row 5."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from mhfu import hitzone
from mhfu.em.intel import HitSphere, PartIntel
from mhfu_port.manifest import Hurtbox
from PySide6.QtWidgets import QVBoxLayout, QWidget

from mhfu_studio.monster import validate as V
from mhfu_studio.monster.render.hitboxes import PART_COLORS
from mhfu_studio.monster.tools import HURT
from mhfu_studio.monster.workspace import HOST, PORT
from mhfu_studio.ui import findings, kit

from .widgets import (
    NoScene,
    SendRow,
    VolumeForm,
    base_name,
    export_button,
    place,
    see_through,
    shared_note,
    source_switch,
    sync_see_through,
    sync_source,
    tiles,
)

if TYPE_CHECKING:
    from mhfu_studio.monster.parts import PartSession
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

Vol = HitSphere | Hurtbox
HOST_BONES = (
    "They keep the base monster's joint numbers: on your skeleton that is a start you can see,"
    " not the answer."
)
PART_VS_ROW = (
    "A breakable part is one of eight damage counters: what breaks when enough damage lands."
    " The damage row is which grid row scales a hit: a Tigrex wing is part 6 but row 5."
)
GRID_NOTE = (
    "Export writes the damage grid and the hurtboxes into the game. Grid writes change the damage"
    " a hit does; whether hurtbox writes move where hits land is unverified in game."
)


def _part(v: Vol) -> int:
    return (v.part or 0) & hitzone.PART_MASK


def _row(v: Vol) -> int:
    return v.hitzone_row or 0


class PartsPanel(kit.Panel):
    """Where the monster can be hit: show, copy the base monster's, pick, change, send."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        show = kit.Section("Show", tip="Whose tables, and whether the view draws them")
        self.source = source_switch(
            ws, "hurtboxes and damage grid", lambda k: self._act("parts", lambda: self._source(k))
        )
        self.draw = kit.check(
            "Show in view",
            tip="Draws the hurtboxes on the monster, coloured by breakable part",
            on=lambda on: self._act("show parts", lambda: self._show(on)),
        )
        self.xray = see_through(ws, studio)
        self.no_intel = kit.Alert(level="info")
        for w in (self.source, kit.row(self.draw, self.xray, stretch=True), self.no_intel):
            show.body.addWidget(w)
        lay.addWidget(show)

        start = kit.Section(
            "Start from the base monster", tip="Copy the base monster's tables into yours"
        )
        self.adopt_grid = kit.button(
            "Copy the base monster's damage grid",
            tip="Copies the base monster's damage grid into yours to change. Without one,"
            " your port takes the base monster's anyway.",
            on=self._adopt_grid,
            icon="ph.copy",
        )
        self.adopt_vols = kit.button(
            "Copy the base monster's hurtboxes",
            tip="Copies the base monster's hurtboxes into yours. " + HOST_BONES,
            on=self._adopt_volumes,
            icon="ph.copy",
        )
        self.vols_top, self.grid_top = QVBoxLayout(), QVBoxLayout()
        start.body.addLayout(self.vols_top)
        start.body.addLayout(self.grid_top)
        self.start_box = start
        lay.addWidget(start)

        parts = kit.Section("Breakable parts", tip=PART_VS_ROW)
        self.summary = kit.label(role="muted")
        self.orphans = kit.Alert(level="error")
        self.parts = kit.Table(
            ["Part", "Name", "Hurtboxes", "Damage row"],
            tip="The eight breakable parts and the hurtboxes that feed each. Click one to light"
            " only its hurtboxes in the view.",
        )
        self.parts.picked.connect(self._pick_part)
        self.part_name = kit.text_field(
            tip="A name for the picked part, written into the manifest's [parts]",
            placeholder="head",
        )
        self.part_name.returnPressed.connect(self._name_part)
        self.part_name.textEdited.connect(lambda t: setattr(ws, "part_name_buf", t))
        self.name_button = kit.button(
            "Name", tip="Names the picked part in your manifest", on=self._name_part
        )
        self.name_row = kit.row(self.part_name, self.name_button)
        for w in (self.summary, self.orphans, self.parts, self.name_row):
            parts.body.addWidget(w)
        lay.addWidget(parts)

        vols = kit.Section("Hurtboxes", tip="Pick one here or click it in the view, then change it")
        self.host_hint = kit.label(role="muted")
        self.to_port = kit.button(
            "Edit yours",
            tip="Switches to your port's hurtboxes, the ones you can change",
            on=lambda: self._act("parts", lambda: self._source(PORT)),
            role="primary",
        )
        self.add = kit.button(
            "Add a sphere",
            tip="Adds one sphere on joint 1 to your hurtboxes, to move where you want it",
            on=self._add,
            icon="ph.plus",
        )
        self.vols_head = kit.label(role="muted")
        self.over = kit.Alert(level="error")
        self.only = kit.check(
            "Only the picked part",
            tip="Lists only the hurtboxes of the part picked above",
            on=lambda on: self._act("only part", lambda: setattr(ws, "only_selected_part", on)),
        )
        self.vols = kit.Table(
            ["#", "Joint", "Radius", "Part", "Damage row"],
            tip="Your hurtboxes, one per row; * marks one changed since the last save. Click"
            " one to change it and light it in the view.",
            swatch_column=3,
        )
        self.vols.picked.connect(self._pick_volume)
        self.pick_hint = kit.label(
            "Pick a hurtbox, here or in the view, to change it.", role="muted"
        )
        self.part_box = kit.integer(
            tip="The breakable part this hurtbox feeds (0 to 7): what breaks when enough damage"
            " lands. Not the damage row.",
            lo=0,
            hi=hitzone.PART_MASK,
            on=lambda v: self._stage(part=v),
        )
        self.row_box = kit.integer(
            tip="Which damage row's percentages this hurtbox takes (0 to 6). Not the part.",
            lo=0,
            hi=hitzone.MAX_ROW,
            on=lambda v: self._stage(hitzone_row=v),
        )
        self.form = VolumeForm(
            self._stage,
            lambda: None if ws.vp is None else ws.vp.selected_joint,
            bone_tip="A joint of your port's skeleton. Pick a joint in the view to read it.",
            extra=[("Part", self.part_box), ("Damage row", self.row_box)],
        )
        self.copy = kit.button(
            "Duplicate", tip="Adds a copy of this hurtbox to move", on=self._copy, icon="ph.copy"
        )
        self.look = kit.button(
            "Frame it",
            tip="Points the camera at this hurtbox",
            on=self._act_slot("frame hurtbox", self._look),
            icon="ph.crosshair",
        )
        self.delete = kit.button(
            "Delete", tip="Removes this hurtbox", on=self._delete, role="danger", icon="ph.trash"
        )
        self.form_box = QWidget()
        fl = QVBoxLayout(self.form_box)
        fl.setContentsMargins(0, 4, 0, 0)
        fl.addWidget(self.form)
        fl.addWidget(tiles(self.copy, self.look, self.delete, columns=3))
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
            vols.body.addWidget(w)
        self.vols_box = vols
        lay.addWidget(vols)

        grid = kit.Section(
            "Damage grid",
            tip="How much of each kind of damage a hit takes, in percent, per row and per state",
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
            tip="Percent of each damage kind a hit on a row's hurtboxes takes (0 to 255);"
            " double-click a cell to type once you have a grid of your own. A * marks a"
            " column whose name is a guess.",
            rows=[str(r) for r in range(hitzone.MAX_ROW + 1)],
        )
        self.grid.edited.connect(self._grid_edit)
        for c, col in enumerate(hitzone.COLUMNS):
            head = self.grid.horizontalHeaderItem(c)
            if head is not None:
                head.setToolTip(hitzone.COLUMN_PROVENANCE.get(col, ""))
        self.shared = kit.Alert()
        for w in (self.grid_head, self.state_row, self.grid, self.shared):
            grid.body.addWidget(w)
        lay.addWidget(grid)

        self.send = SendRow(ws, studio)
        lay.addWidget(self.send)
        self.no_manifest = kit.label(
            "This scene has no manifest, so there is nothing to write parts into.", role="muted"
        )
        lay.addWidget(self.no_manifest)

        self.more = kit.More(
            tip="The base monster's table address, test tools, copying again and export"
        )
        self.provenance = kit.label(role="muted", selectable=True)
        self.keep = kit.button(
            "Keep only the picked hurtbox",
            tip="Drops every other hurtbox: with one left, where a hit lands is the whole answer."
            " A test tool.",
            on=self._keep,
        )
        self.max_row = kit.button(
            "Max the picked damage row",
            tip="Cut, impact and shot to 255% on the grid row picked: every weapon does the most"
            " the grid can say there. A quick way to prove which row a spot uses.",
            on=self._max_row,
        )
        self.again = QVBoxLayout()
        self.grid_text = kit.label(GRID_NOTE, role="muted")
        self.export = export_button(ws, studio)
        self.more.body.addWidget(self.provenance)
        self.more.body.addWidget(tiles(self.keep, self.max_row, columns=1))
        self.more.body.addLayout(self.again)
        self.more.body.addWidget(self.grid_text)
        self.more.body.addWidget(kit.row(self.export, stretch=True))
        lay.addWidget(self.more)

        self.pages = kit.Pages(page, NoScene(studio))
        self.body.addWidget(self.pages)
        self.body.addStretch(1)
        #: where a finding lands (`validate.FOCUS`)
        self.lands = {
            V.PART_NAME: self.part_name,
            V.HURT_PART: self.part_box,
            V.HURT_JOINT: self.form.bone,
            V.HURT_RADIUS: self.form.radius,
            V.HURT_END: self.form.to,
            V.HURTBOXES: self.vols,
            V.GRID: self.grid,
        }

    # ---- actions --------------------------------------------------------------------- #

    def _act(self, label: str, fn: Callable[[], object]) -> None:
        self.studio.act(label, fn)()

    def _act_slot(self, label: str, fn: Callable[[], object]) -> Callable[..., None]:
        return self.studio.act(label, fn)

    def _source(self, key: str) -> None:
        self.ws.parts_source = key
        self.ws.selected_volume = None  # an index into the other source's list
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
                self.ws.edit(f"part {part} = {name}", lambda: sess.name_part(part, name))

        self._act("name part", run)

    def _pick_volume(self, index: object) -> None:
        if isinstance(index, int):
            pick = None if self.ws.selected_volume == index else index
            self._act("pick volume", lambda: self.ws.select_volume(pick))

    def _stage(self, **fields: Any) -> None:
        i = self.ws.selected_volume

        if i is not None:
            self._act("edit volume", lambda: self.ws.edit_volume(HURT, i, **fields))

    def _add(self) -> None:
        def run() -> None:
            sess = self._session()
            new = Hurtbox(bone=1, radius=150.0, part=1, hitzone_row=0, offset=[0.0, 0.0, 0.0])
            if self.ws.edit("added a hurtbox", lambda: sess.add_volume(new)):
                self.ws.select_volume(len(sess.volumes()) - 1)

        self._act("add volume", run)

    def _keep(self) -> None:
        i = self.ws.selected_volume

        def run() -> None:
            sess = self._session()
            if i is None:
                return
            gone = len(sess.volumes()) - 1
            msg = f"kept hurtbox {i}, dropped {gone}: a hit lands there or nowhere"
            if self.ws.edit(msg, lambda: sess.keep_only(i)):
                self.ws.select_volume(0)

        self._act("keep only", run)

    def _delete(self) -> None:
        i = self.ws.selected_volume

        def run() -> None:
            sess = self._session()
            if i is not None and self.ws.edit(
                f"deleted hurtbox {i}", lambda: sess.remove_volume(i)
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
            if self.ws.edit(f"hurtbox {i} duplicated", lambda: sess.add_volume(v)):
                self.ws.select_volume(len(sess.volumes()) - 1)

        self._act("duplicate volume", run)

    def _look(self) -> None:
        self.ws.tools.frame(HURT, self.ws.selected_volume)

    def _grid_edit(self, row: int, column: int, value: int) -> None:
        state = self.ws.grid_state

        def run() -> None:
            sess = self._session()
            name = sess.states()[state].name
            msg = f"{name} row {row} {hitzone.COLUMNS[column]} = {value}"
            self.ws.edit(msg, lambda: sess.set_hitzone(state, row, column, value))

        self._act("grid", run)

    def _max_row(self) -> None:
        state, row = self.ws.grid_state, self.grid.currentRow()

        def run() -> None:
            sess = self._session()
            if row < 0:
                self.ws.message = "pick a damage row in the grid first"
                return
            name = sess.states()[state].name
            msg = f"{name} row {row}: cut, impact, shot = 255, the most a byte says"
            self.ws.edit(msg, lambda: sess.fill_row(state, row, 255))

        self._act("max row", run)

    def _adopt_grid(self) -> None:
        def run() -> None:
            sess, host = self._session(), self.ws.host_parts()
            if host is None:
                return
            states = host.states
            msg = f"copied the damage grid of {base_name(self.ws)} into yours"
            if self.ws.edit(msg, lambda: sess.adopt_grid(states)):
                self._source(PORT)

        self._act("adopt grid", run)

    def _adopt_volumes(self) -> None:
        def run() -> None:
            sess, host = self._session(), self.ws.host_parts()
            if host is None:
                return
            got = sess.adopt_volumes(host.spheres(), source=base_name(self.ws))
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
        ws = self.ws
        self.pages.show_page(ws.scene is not None)
        if ws.scene is None:
            return
        host, sess = ws.host_parts(), ws.part_session
        sync_source(self.source, ws, ws.parts_source)
        kit.put(self.draw, ws.show_parts)
        sync_see_through(self.xray, ws)
        port = ws.parts_source == PORT
        missing = not port and host is None
        gap = ws.intel_gap("part")
        if gap and ws.manifest is not None:
            gap += " Your port still takes hurtboxes of its own."
        self.no_intel.setText(gap)
        self.no_intel.setVisible(missing)
        vols = self.volumes_now()
        self._parts(vols, host, sess, missing)
        self._volumes(sess, port)
        self._grid(host, sess)
        has_doc = sess is not None and ws.manifest is not None
        self._start(host, sess if has_doc else None)
        self.send.setVisible(has_doc)
        self.send.sync()
        self.export.setEnabled(ws.exportable())
        self.no_manifest.setVisible(not has_doc)
        self.keep.setEnabled(port and ws.selected_volume is not None)
        findings.take(ws, self.lands)

    def _start(self, host: PartIntel | None, sess: PartSession | None) -> None:
        """A copy of the base monster's tables: on top while yours lacks one, else in More."""
        shown = False
        for b, top, has, what in (
            (
                self.adopt_vols,
                self.vols_top,
                host is not None and bool(host.spheres()),
                "hurtboxes",
            ),
            (self.adopt_grid, self.grid_top, host is not None and host.has_grid, "damage grid"),
        ):
            mine = sess is not None and bool(
                sess.volumes() if b is self.adopt_vols else sess.states()
            )
            place(b, self.again if mine else top)
            b.setText(f"Copy the base monster's {what}" + (" again" if mine else ""))
            b.setVisible(sess is not None and has)
            shown |= sess is not None and has and not mine
        self.start_box.setVisible(shown)

    def _parts(
        self, vols: Sequence[Vol], host: PartIntel | None, sess: PartSession | None, missing: bool
    ) -> None:
        ws = self.ws
        nb = 0 if ws.scene is None else ws.scene.rig.n
        real = [v for v in vols if not v.is_marker]
        markers = "" if len(real) == len(vols) else f", +{len(vols) - len(real)} marker(s)"
        self.summary.setText(
            f"{len(real)} hurtbox(es) on {len({v.bone for v in real})} of {nb} joints{markers}"
        )
        prov = ""
        if ws.parts_source == HOST and host is not None:
            if host.active is not None:
                sp = ",".join(map(str, host.active.species)) or "?"
                prov = (
                    f"The hurtbox set species {sp} walks: 0x{host.active.va:08X}"
                    f" ({host.capacity} records). The overlay holds {len(host.hurtboxes)}; the"
                    " rest are other species ids'."
                )
            else:
                prov = (
                    "Every hurtbox set in the overlay: this data does not say which one the"
                    " species walks."
                )
        self.provenance.setText(prov)
        self.provenance.setVisible(bool(prov))
        lost = ws.part_orphans
        self.orphans.setText(
            f"{len(lost)} hurtbox(es) name a joint your skeleton does not have"
            f" ({', '.join(str(getattr(o, 'bone', '?')) for o in lost[:6])}), so they are drawn"
            " nowhere. Joint numbers belong to the skeleton they were made for."
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
                    name or ("none" if i == 0 else ""),
                    str(len(mine)) if mine else "",
                    ",".join(map(str, rws)),
                )
            )
            tip = [f"joints {', '.join(map(str, bones))}"] if bones else []
            if len(rws) > 1:
                tip.append(
                    "Its hurtboxes use different damage rows, so they take different"
                    " percentages. Not an error: the Tigrex's wings do it."
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
                "These are the base monster's hurtboxes, read only. To change them, edit yours"
                + (": copy the base monster's first, above." if not vols else ".")
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
        head = f"{len(vols)} hurtbox(es)"
        if sess.capacity is not None:
            head += f", {sess.capacity} fit"
        self.vols_head.setText(head)
        self.over.setText(f"{sess.over_capacity} more than fit: the game drops the rest")
        self.over.setVisible(sess.over_capacity > 0)
        self.only.setVisible(bool(vols) and ws.selected_part is not None)
        self.only.setText(f"Only part {ws.selected_part}")
        kit.put(self.only, ws.only_selected_part)
        rows, data, colors, tips = [], [], [], []
        for i, v in enumerate(vols):
            part = _part(v)
            if ws.only_selected_part and ws.selected_part not in (None, part):
                continue
            changed = sess.volume_changed(i)
            o = v.offset or (0.0, 0.0, 0.0)
            rows.append(
                (
                    f"{i}{' *' if changed else ''}",
                    f"0x{v.bone:X} marker" if v.is_marker else str(v.bone),
                    f"{v.radius:g}",
                    str(part),
                    str(_row(v)),
                )
            )
            data.append(i)
            colors.append(PART_COLORS[part])
            tip = [f"{v.shape}, offset {o[0]:g} {o[1]:g} {o[2]:g}"]
            if changed:
                tip.append("Changed, not saved yet.")
            if v.is_marker:
                tip.append("A marker for the game, not a joint: 0x7D is the tail-sever skip.")
            tips.append(" ".join(tip))
        self.vols.set_rows(rows, data, colors=colors, tips=tips)
        self.vols.fit(8)
        pick = ws.selected_volume
        picked = pick is not None and 0 <= pick < len(vols)
        if picked and pick is not None:
            self.vols.select_data(pick)
            n = 0 if ws.scene is None else ws.scene.rig.n
            v = vols[pick]
            self.form.show_volume(f"Hurtbox {pick}", v, n)
            kit.put(self.part_box, v.part or 0)
            kit.put(self.row_box, v.hitzone_row or 0)
            self.form.bone.setToolTip(
                f"A joint of your port's skeleton ({n} joints). Pick a joint in the view to"
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
            self.grid_head.setText("No damage grid: copy the base monster's, above.")
            for w in (self.state_row, self.grid):
                w.setVisible(False)
        else:
            tail = "yours" if editable else "the base monster's, read only"
            self.grid_head.setText(f"{len(states)} state(s), {tail}")
            for w in (self.state_row, self.grid):
                w.setVisible(True)
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
        self.max_row.setEnabled(editable)
        self.shared.setText(shared_note(ws))
        self.shared.setVisible(editable)

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
