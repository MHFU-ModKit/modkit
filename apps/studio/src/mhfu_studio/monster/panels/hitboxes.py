# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Hitboxes panel: where he hits YOU. The Parts panel's mirror, with volume sets for parts:
a handler spawns an attack by id, the record names a set, the set is the same sphere record."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mhfu import hitzone
from mhfu.em.intel import AttackIntel, AttackRecord
from mhfu_port.manifest import Hitbox
from PySide6.QtWidgets import QVBoxLayout, QWidget

from mhfu_studio.monster.attacks import LEVERS
from mhfu_studio.monster.render.hitboxes import set_color
from mhfu_studio.shell.findings import Level
from mhfu_studio.ui import kit

from .common import bone_span
from .parts import HOST, HOST_BONES, PORT
from .widgets import (
    ExportRow,
    NoScene,
    SaveRow,
    VolumeForm,
    describe,
    fly_to,
    host_text,
    tiles,
)

if TYPE_CHECKING:
    from mhfu_studio.monster.attacks import AttackSession
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

SOURCE_TIPS = {
    HOST: "The host monster's own attack volumes and records, as the game has them: read only",
    PORT: "What this port's manifest writes over the host's: yours to edit",
}
SET_TIP = (
    "A move spawns an attack by number; the attack record names a volume SET, and the set's"
    " spheres are where the blow lands. Pick a set to light it in the view."
)
SHARED = (
    "The sets are SPECIES data: with the port REPLACING its host they are his alone; beside a"
    " native em{sp:02d} they re-arm the native too. The in-place write and the P.hit() path are"
    " proven live. Deploy syncs mhfu_port.lua as well: a stale library silently drops these"
    " tables."
)
LEVER_HEADS = ("Attack", "Power", "Element", "Set", "From")


def moves_hitting(ws: MonsterWorkspace, set_index: int) -> tuple[list[str], int]:
    """`(declared moves, other pairs)` whose handler hits with this set."""
    si = ws.host_intel()
    if si is None:
        return [], 0
    by_pair: dict[tuple[int, int], list[str]] = {}
    m = ws.manifest
    for name, mv in ({} if m is None else m.moves).items():
        by_pair.setdefault((mv.main, mv.sub), []).append(name)
    names, others = [], 0
    for p in si.pairs_hitting_with(set_index, ws.host_species):
        got = by_pair.get((p.main, p.sub))
        if got:
            names += got
        else:
            others += 1
    return names, others


def _section(title: str, tip: str) -> tuple[kit.Section, QVBoxLayout]:
    s = kit.Section(title, tip=tip)
    return s, s.body


class HitboxesPanel(kit.Panel):
    """Where the monster hits you: pick a set, pick a hitbox, change it, save and deploy."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        self._records: list[AttackRecord] = []
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        show, body = _section("Show", "Whose hitboxes, and whether the view draws them")
        self.source = kit.Segmented(
            [(HOST, "Host"), (PORT, "This port")],
            tip="Whose attack volumes and records to show",
            tips=SOURCE_TIPS,
            on=lambda k: self._act("hitbox source", lambda: self._source(k)),
        )
        self.draw = kit.check(
            "Show in view",
            tip="Draws the attack volumes on the monster, one colour per set",
            on=lambda on: self._act("show hitboxes", lambda: self._show(on)),
        )
        self.xray = kit.check(
            "Through the mesh",
            tip="Draws the volumes on top, even where the body hides them",
            on=lambda on: self._act("x-ray", lambda: self._vp("hitboxes_xray", on)),
        )
        self.no_intel = kit.Alert(level="info")
        self.join = kit.Alert(level=None, role="muted")
        self.offset_note = kit.Alert(level=None, role="muted")
        for w in (
            self.source,
            kit.row(self.draw, self.xray, stretch=True),
            self.no_intel,
            self.join,
            self.offset_note,
        ):
            body.addWidget(w)
        lay.addWidget(show)

        sets, body = _section("1  Pick a set", SET_TIP)
        self.move_only = kit.check(
            "Only the move's sets",
            tip="Lists and draws only the sets the move picked in Moves or Action hits with",
            on=lambda on: self._act("move's sets", lambda: self._move_only(on)),
        )
        self.no_attack = kit.label(role="muted")
        self.orphans = kit.Alert(level="error")
        self.sets_head = kit.label(role="muted")
        self.sets = kit.Table(
            ["Set", "Attacks", "Vols", "Bones", "Moves"],
            tip=SET_TIP + " Vols reads port/host: how many the port authors, how many fit.",
        )
        self.sets.picked.connect(self._pick_set)
        for w in (self.move_only, self.no_attack, self.orphans, self.sets_head, self.sets):
            body.addWidget(w)
        self.sets_box = sets
        lay.addWidget(sets)

        vols, body = _section(
            "2  Pick a hitbox", "The port's volumes: pick one here or click it in the view"
        )
        self.vols_box = vols
        self.host_hint = kit.label(role="muted")
        self.to_port = kit.button(
            "Edit on this port",
            tip="Switches to the port's own hitboxes, the ones you can change",
            on=lambda: self._act("hitbox source", lambda: self._source(PORT)),
            role="primary",
        )
        self.set_hint = kit.label(role="muted")
        self.adopt_set = kit.button(
            "Adopt the host's set",
            tip="Copies the host's volumes of this set into the port, to move. " + HOST_BONES,
            on=self._adopt_set,
            icon="ph.download-simple",
        )
        self.add = kit.button(
            "Add a sphere",
            tip="Adds one sphere on bone 1 to the picked set, to move where you want it",
            on=self._add,
            icon="ph.plus",
        )
        self.vols_head = kit.label(role="muted")
        self.over = kit.Alert(level="error")
        self.vols = kit.Table(
            ["Vol", "Set", "Bone", "Shape", "Radius", "Offset"],
            tip="The port's hitboxes, the picked set's or all; * marks one changed since the"
            " last save. Click one to edit it and light it in the view.",
            swatch_column=1,
        )
        self.vols.picked.connect(self._pick_volume)
        self.pick_hint = kit.label(
            "Pick a hitbox, here or in the view, to change it.", role="muted"
        )
        for w in (
            self.host_hint,
            self.to_port,
            self.set_hint,
            self.vols_head,
            self.over,
            self.vols,
            self.pick_hint,
            tiles(self.adopt_set, self.add, columns=1),
        ):
            body.addWidget(w)
        lay.addWidget(vols)

        edit, body = _section("3  Change it", "Each change is one undo step and shows at once")
        self.edit_box = edit
        self.set_box = kit.integer(
            tip="Which of the host's volume sets this record ships in: the attack record picks"
            " the set",
            lo=0,
            hi=0,
            on=lambda v: self._stage(set=v),
        )
        self.set_swatch = kit.Swatch(tip="The set's colour in the view")
        self.label = kit.text_field(
            tip="Your note for this hitbox: 'tail tip', 'left claw'", placeholder="what it is"
        )
        self.label.editingFinished.connect(self._label)
        self.form = VolumeForm(
            self._stage,
            lambda: None if ws.vp is None else ws.vp.selected_joint,
            bone_tip="A joint number of THIS port's rig; 126 and 127 are the node's own place."
            " Pick a joint in the view to read it.",
            extra=[
                ("Set", kit.row(self.set_box, self.set_swatch, stretch=True)),
                ("Label", self.label),
            ],
        )
        self.keep = kit.button(
            "Keep only this",
            tip="Drops the other volumes of this set: with one left, where the blow lands is"
            " the whole answer. The other sets are other attacks and stay.",
            on=self._keep,
        )
        self.copy = kit.button(
            "Duplicate", tip="Adds a copy of this hitbox to move", on=self._copy, icon="ph.copy"
        )
        self.look = kit.button(
            "Show in view",
            tip="Points the camera at this hitbox",
            on=self.studio.act("look at hitbox", self._look),
            icon="ph.eye",
        )
        self.delete = kit.button(
            "Delete", tip="Removes this hitbox", on=self._delete, role="danger", icon="ph.trash"
        )
        body.addWidget(self.form)
        body.addWidget(tiles(self.keep, self.copy, self.look, self.delete))
        lay.addWidget(edit)

        rec, body = _section(
            "Attack records",
            "What each attack does: power, element and which set it hits with. Only these"
            " three bytes are decoded.",
        )
        self.records_head = kit.label(role="muted")
        self.records = kit.Grid(
            LEVER_HEADS,
            tip="The attack records on the picked set or move. On the port, double-click power,"
            " element (0x hex is fine) or set to change it; a value equal to the host's says"
            " nothing.",
        )
        self.records.edited.connect(self._lever)
        self.to_host = kit.button(
            "Back to the host's bytes",
            tip="Drops the port's changes to the picked record: the host's stands",
            on=self._clear_record,
            icon="ph.arrow-counter-clockwise",
        )
        for w in (self.records_head, self.records, self.to_host):
            body.addWidget(w)
        self.records_box = rec
        lay.addWidget(rec)

        adopt, body = _section("Start from the host", "Copy the host's sets into the port")
        self.adopt_pair = kit.button(
            "Adopt the move's sets",
            tip="Copies the host's volumes of every set the picked move spawns, so you can move"
            " them onto the right joints of THIS rig. " + HOST_BONES,
            on=self._adopt_pair,
            icon="ph.download-simple",
        )
        self.shared = kit.Alert()
        body.addWidget(self.adopt_pair)
        body.addWidget(self.shared)
        self.adopt_box = adopt
        lay.addWidget(adopt)

        ship, body = _section("4  Save and deploy", "Write the manifest, then send it to the game")
        self.save = SaveRow(ws, studio)
        self.export = ExportRow(ws, studio)
        body.addWidget(self.save)
        body.addWidget(self.export)
        self.ship_box = ship
        lay.addWidget(ship)
        self.no_manifest = kit.label(
            "This scene has no manifest, so there is nothing to write hitboxes into.",
            role="muted",
        )
        lay.addWidget(self.no_manifest)

        self.pages = kit.Pages(page, NoScene(studio))
        self.body.addWidget(self.pages)
        self.body.addStretch(1)

    # ---- actions --------------------------------------------------------------------- #

    def _act(self, label: str, fn: Callable[[], object]) -> None:
        self.studio.act(label, fn)()

    def _vp(self, attr: str, value: object) -> None:
        if self.ws.vp is not None:
            setattr(self.ws.vp, attr, value)

    def _source(self, key: str) -> None:
        self.ws.attacks_source = key
        self.ws.selected_attack_volume = None
        self.ws.sync_attacks()

    def _show(self, on: bool) -> None:
        self.ws.show_attacks = on
        self.ws.sync_attacks()

    def _move_only(self, on: bool) -> None:
        self.ws.sets_of_move_only = on
        self.ws.sync_attacks()

    def _session(self) -> AttackSession:
        sess = self.ws.attack_session
        if sess is None:
            raise RuntimeError("no manifest to edit")
        return sess

    def _pick_set(self, index: object) -> None:
        if isinstance(index, int):
            pick = None if self.ws.selected_set == index else index
            self._act("pick set", lambda: self.ws.select_set(pick))

    def _pick_volume(self, index: object) -> None:
        if isinstance(index, int):
            pick = None if self.ws.selected_attack_volume == index else index
            self._act("pick hitbox", lambda: self.ws.select_attack_volume(pick))

    def _stage(self, **fields: Any) -> None:
        i = self.ws.selected_attack_volume

        def run() -> None:
            sess = self._session()
            if i is not None:
                msg = f"hitbox {i}: {describe(fields)} (unsaved)"
                self.ws.edit(msg, lambda: sess.edit_volume(i, **fields))

        self._act("edit hitbox", run)

    def _label(self) -> None:
        sess, i = self.ws.attack_session, self.ws.selected_attack_volume
        if sess is None or i is None or not 0 <= i < len(sess.volumes()):
            return
        if self.label.text() != sess.volumes()[i].label:
            self._stage(label=self.label.text())

    def _add(self) -> None:
        st = self.ws.selected_set

        def run() -> None:
            sess = self._session()
            if st is None:
                return
            new = Hitbox(bone=1, radius=150.0, set=st, offset=[0.0, 0.0, 0.0])
            if self.ws.edit(f"added a sphere to set {st} (unsaved)", lambda: sess.add_volume(new)):
                self.ws.select_attack_volume(len(sess.volumes()) - 1)

        self._act("add hitbox", run)

    def _adopt(self, sess: AttackSession, host: AttackIntel, st: int) -> bool:
        hs = host.set(st)
        if hs is None or not hs.spheres:
            return False
        src = f"em{self.ws.host_species or 0:02d} set {st}"
        try:
            got = sess.adopt_set(st, hs.spheres, source=src)
        except ValueError as e:
            self.ws.message = str(e)
            return False
        self.ws.message = got.describe()
        return True

    def _adopt_set(self) -> None:
        st = self.ws.selected_set

        def run() -> None:
            sess, host = self._session(), self.ws.host_attacks()
            if st is not None and host is not None and self._adopt(sess, host, st):
                msg = self.ws.message
                self.ws.sync()
                self.ws.message = msg

        self._act("adopt set", run)

    def _adopt_pair(self) -> None:
        def run() -> None:
            sess, host = self._session(), self.ws.host_attacks()
            if host is None:
                return
            done = sum(self._adopt(sess, host, st) for st in self.ws.pair_sets())
            self.ws.attacks_source = PORT
            self.ws.sync()
            self.ws.message = (
                f"adopted {done} set(s) from em{self.ws.host_species or 0:02d}: the HOST's bone"
                " numbers on this rig, a starting point you can see, not a correct answer"
                " (unsaved)"
            )

        self._act("adopt the move's sets", run)

    def _keep(self) -> None:
        i = self.ws.selected_attack_volume

        def run() -> None:
            sess = self._session()
            if i is None:
                return
            st = sess.volumes()[i].set
            gone = len(sess.volumes_of(st)) - 1
            msg = (
                f"kept hitbox {i}, dropped {gone} from set {st}: the attack lands there or"
                " nowhere (unsaved)"
            )
            if self.ws.edit(msg, lambda: sess.keep_only(i)):
                self.ws.select_attack_volume(sess.volumes_of(st)[0][0])

        self._act("keep only", run)

    def _delete(self) -> None:
        i = self.ws.selected_attack_volume

        def run() -> None:
            sess = self._session()
            if i is not None and self.ws.edit(
                f"deleted hitbox {i} (unsaved)", lambda: sess.remove_volume(i)
            ):
                self.ws.select_attack_volume(None)

        self._act("delete hitbox", run)

    def _copy(self) -> None:
        i = self.ws.selected_attack_volume

        def run() -> None:
            sess = self._session()
            if i is None:
                return
            v = sess.volumes()[i]
            if self.ws.edit(f"hitbox {i} duplicated (unsaved)", lambda: sess.add_volume(v)):
                self.ws.select_attack_volume(len(sess.volumes()) - 1)

        self._act("duplicate hitbox", run)

    def _look(self) -> None:
        vp = self.ws.vp
        fly_to(vp, None if vp is None else vp.attacks, self.ws.selected_attack_volume)

    def _lever(self, row: int, column: int, value: int) -> None:
        if not 0 <= row < len(self._records) or not 1 <= column <= len(LEVERS):
            return
        a, key = self._records[row], LEVERS[column - 1]

        def run() -> None:
            sess = self._session()
            host_value = int(getattr(a, key))
            same = " = the host's, so the block says nothing" if value == host_value else ""
            levers = {key: None if value == host_value else value}
            msg = f"attack {a.id} {key} = {value}{same} (unsaved)"
            self.ws.edit(msg, lambda: sess.set_attack(a.id, **levers))

        self._act("attack lever", run)

    def _clear_record(self) -> None:
        row = self.records.currentRow()

        def run() -> None:
            sess = self._session()
            if not 0 <= row < len(self._records):
                self.ws.message = "pick a record first"
                return
            aid = self._records[row].id
            self.ws.edit(
                f"attack {aid}: back to the host's bytes (unsaved)",
                lambda: sess.clear_attack(aid),
            )

        self._act("record to host", run)

    # ---- sync ------------------------------------------------------------------------ #

    def sync(self) -> None:
        ws, vp = self.ws, self.ws.vp
        self.pages.show_page(ws.scene is not None)
        if ws.scene is None:
            return
        host, sess = ws.host_attacks(), ws.attack_session
        self.source.buttons[HOST].setText(host_text(ws))
        kit.put(self.source, ws.attacks_source)
        kit.put(self.draw, ws.show_attacks)
        kit.put(self.xray, vp is not None and vp.hitboxes_xray)
        self.xray.setEnabled(vp is not None)
        self.no_intel.setText(ws.intel_gap("attack"))
        self.no_intel.setVisible(host is None)
        port = ws.attacks_source == PORT
        has_doc = sess is not None and ws.manifest is not None
        for w in (self.sets_box, self.vols_box, self.records_box, self.adopt_box):
            w.setVisible(host is not None)
        if host is None:
            for x in (self.join, self.offset_note, self.edit_box):
                x.setVisible(False)
        else:
            self._provenance(host)
            self._sets(host, sess if port else None)
            self._volumes(host, sess, port)
            self._levers(host, sess if port else None)
            self.adopt_box.setVisible(has_doc)
            ps = ws.pair_sets()
            self.adopt_pair.setVisible(bool(ps) and ws.pair is not None)
            if ws.pair is not None:
                self.adopt_pair.setText(f"Adopt the sets ({ws.pair[0]},{ws.pair[1]}) hits with")
            self.shared.setText(SHARED.format(sp=ws.host_species or 0))
        self.ship_box.setVisible(has_doc)
        self.save.sync()
        self.export.sync()
        self.no_manifest.setVisible(not has_doc)

    def _provenance(self, host: AttackIntel) -> None:
        ws = self.ws
        sp = f"0x{host.spawner:08X}" if host.spawner else "?"
        if host.join == "measured":
            n = len(host.primary.attacks) if host.primary else 0
            self.join.setText(f"Spawner {sp} to the {n}-record table: MEASURED live.")
        else:
            self.join.setText(
                f"Spawner {sp} to table: INFERRED ({host.join}). Only em75's join was walked to"
                " the HP write; here the id range was matched to the biggest table."
            )
        self.join.set_level(None if host.join == "measured" else "warning")
        self.join.setToolTip(host.join_provenance)
        self.join.setVisible(True)
        hsp = ws.host_species
        off = None if hsp is None else host.id_offset(hsp)
        if off is None:
            self.offset_note.setText(
                f"Species {hsp} shares this overlay but its id offset is unknown: a move's"
                " attack ids cannot be resolved to records."
            )
        else:
            self.offset_note.setText(f"Species {hsp} uses record = handler id + {off}.")
        self.offset_note.set_level("error" if off is None else None)
        self.offset_note.setVisible(off is None or bool(off))

    def _sets(self, host: AttackIntel, sess: AttackSession | None) -> None:
        ws = self.ws
        pair_sets = ws.pair_sets()
        on_pair = bool(pair_sets) and ws.pair is not None
        self.move_only.setVisible(on_pair)
        if on_pair and ws.pair is not None:
            m, s = ws.pair
            self.move_only.setText(
                f"Only the sets ({m},{s}) hits with: " + ", ".join(map(str, pair_sets))
            )
            kit.put(self.move_only, ws.sets_of_move_only)
        silent = ws.pair is not None and not pair_sets
        if silent and ws.pair is not None:
            self.no_attack.setText(
                f"({ws.pair[0]},{ws.pair[1]}) spawns no attack the static scan can see: a turn,"
                " a roar, a walk; or a computed id."
            )
        self.no_attack.setVisible(silent)
        only = on_pair and ws.sets_of_move_only
        listed = sorted(pair_sets) if only else [st.index for st in host.sets]
        if sess is not None and not only:
            listed = sorted(set(listed) | set(sess.sets()))
        lost = ws.attack_orphans
        self.orphans.setText(
            f"{len(lost)} volume(s) name a bone this rig does not have"
            f" ({', '.join(str(getattr(o, 'bone', '?')) for o in lost[:6])}) and are drawn"
            " NOWHERE. Bone numbers belong to the rig that ships them."
        )
        self.orphans.setVisible(bool(lost))
        self.sets_head.setText(
            f"{len(listed)} set(s) listed of {len(host.sets)}; {len(host.attacks)} attack record(s)"
        )
        rows, tips, colors = [], [], []
        levels: list[Level | None] = []
        for idx in listed:
            st = host.set(idx)
            atks = host.attacks_using(idx)
            more = f" +{len(atks) - 2}" if len(atks) > 2 else ""
            names, others = moves_hitting(ws, idx)
            moves = ", ".join(names[:1]) + (f" +{len(names) - 1}" if len(names) > 1 else "")
            if others:
                moves += (" " if moves else "") + f"({others} pair{'' if others == 1 else 's'})"
            n_host = 0 if st is None else st.capacity
            over = 0
            if sess is not None:
                n_port, over = len(sess.volumes_of(idx)), sess.over_capacity(idx)
                count = f"{n_port or '·'}/{n_host}"
            else:
                count = str(n_host)
            unrigged = st is not None and not st.rigged
            if sess is not None and sess.volumes_of(idx):
                mine = {h.bone for _, h in sess.volumes_of(idx)}
                bones = sorted(b for b in mine if b not in hitzone.MARKER_BONES)
            else:
                bones = [] if st is None else st.bones
            rows.append(
                (
                    str(idx),
                    ", ".join(f"{a.id}(p{a.power})" for a in atks[:2]) + more,
                    count,
                    bone_span(bones) if bones else ("node" if unrigged else ""),
                    moves,
                )
            )
            tip = [f"attack {a.id}: {a.describe()}" for a in atks]
            if names:
                tip.append("moves: " + ", ".join(names))
            if unrigged:
                tip.append(
                    "Un-rigged: every record hangs on bone 126/127, the node's own position,"
                    " projectile-shaped. Nothing here to re-align to a joint."
                )
            if over:
                tip.append(f"{over} more than fit in place: the runtime truncates")
            tips.append("\n".join(tip))
            colors.append(set_color(idx))
            levels.append("error" if over else None)
        self.sets.set_rows(rows, listed, colors=colors, tips=tips, levels=levels)
        self.sets.fit(8)
        if ws.selected_set is None:
            self.sets.clearSelection()
        else:
            self.sets.select_data(ws.selected_set)

    def _volumes(self, host: AttackIntel, sess: AttackSession | None, port: bool) -> None:
        ws = self.ws
        st = ws.selected_set
        self.vols_box.title.setText("2  Pick a hitbox" + ("" if st is None else f" in set {st}"))
        self.host_hint.setVisible(not port)
        self.to_port.setVisible(not port and sess is not None)
        if not port:
            self.host_hint.setText(
                "These are the host's hitboxes, read only. To change one, edit it on this"
                " port: adopt the host's set there first."
            )
        editing = port and sess is not None
        vols = [] if sess is None else sess.volumes()
        rows_of = [] if sess is None or st is None else sess.volumes_of(st)
        empty_set = editing and st is not None and not rows_of
        hs = None if st is None else host.set(st)
        self.set_hint.setVisible(editing and (empty_set or not vols))
        if empty_set:
            n = 0 if hs is None else hs.capacity
            self.set_hint.setText(f"Set {st}: nothing authored, the host's {n} record(s) stand.")
        elif editing and not vols:
            self.set_hint.setText(
                "No [[hitbox]] yet: pick a set above to adopt the host's, or add a sphere."
            )
        can_adopt = editing and st is not None and hs is not None and bool(hs.spheres)
        self.adopt_set.setVisible(can_adopt and ws.selected_attack_volume is None)
        self.adopt_set.setText(
            f"{'Re-adopt' if rows_of else 'Adopt'} the host's set {st}" if st is not None else ""
        )
        self.add.setVisible(editing and st is not None and ws.selected_attack_volume is None)
        self.add.setText(f"Add a sphere to set {st}")
        listing = editing and bool(rows_of if st is not None else vols)
        for w in (self.vols_head, self.vols):
            w.setVisible(listing)
        self.over.setVisible(False)
        self.pick_hint.setVisible(False)
        self.edit_box.setVisible(False)
        if not listing or sess is None:
            return
        rows = rows_of if st is not None else list(enumerate(vols))
        if st is not None:
            cap = sess.capacities.get(st)
            head = f"Set {st}: {len(rows)} hitbox(es)" + ("" if cap is None else f", {cap} fit")
            over = sess.over_capacity(st)
        else:
            head = f"{len(vols)} hitbox(es) over {len(sess.sets())} set(s)"
            over = sum(sess.over_capacity_all().values())
        self.vols_head.setText(head)
        self.over.setText(f"{over} more than fit in place: the runtime truncates the set")
        self.over.setVisible(over > 0)
        cells, data, colors, tips = [], [], [], []
        for i, v in rows:
            changed = sess.volume_changed(i)
            o = v.offset or (0.0, 0.0, 0.0)
            if v.is_node_space:
                bone, tip = "node", f"Bone {v.bone}: the node's own place, the attack's origin."
            elif v.is_marker:
                bone, tip = "0x7D", "A JOINER the walker hands on, with no geometry. Drawn nowhere."
            else:
                bone, tip = str(v.bone), ""
            cells.append(
                (
                    f"{i}{' *' if changed else ''}",
                    str(v.set),
                    bone,
                    v.shape,
                    f"{v.radius:g}",
                    f"{o[0]:g} {o[1]:g} {o[2]:g}",
                )
            )
            data.append(i)
            colors.append(set_color(v.set))
            tips.append(
                " ".join(t for t in ("Changed, not saved yet." if changed else "", tip) if t)
            )
        self.vols.set_rows(cells, data, colors=colors, tips=tips)
        self.vols.fit(8)
        pick = ws.selected_attack_volume
        if pick is None or not 0 <= pick < len(vols):
            self.vols.clearSelection()
            self.pick_hint.setVisible(True)
            return
        self.vols.select_data(pick)
        v = vols[pick]
        n = 0 if ws.scene is None else ws.scene.rig.n
        self.form.show_volume(f"Hitbox {pick}", v, n)
        self.form.bone.setToolTip(
            f"A joint number of THIS port's rig ({n} joints); 126 and 127 are the node's own"
            " place. Pick a joint in the view to read it."
        )
        self.set_box.setMaximum(max(len(host.sets) - 1, v.set))
        kit.put(self.set_box, v.set)
        self.set_swatch.set(set_color(v.set))
        kit.put(self.label, v.label)
        self.keep.setText(f"Keep only this in set {v.set}")
        self.edit_box.setVisible(True)

    def _levers(self, host: AttackIntel, sess: AttackSession | None) -> None:
        ws = self.ws
        hp = ws.host_pair()
        if ws.selected_set is not None:
            recs = host.attacks_using(ws.selected_set)
            title = f"Records using set {ws.selected_set}"
        elif hp is not None and ws.pair is not None:
            recs = host.records_for(hp.attack_ids, ws.host_species)
            title = f"Records ({ws.pair[0]},{ws.pair[1]}) spawns"
        else:
            recs, title = [], ""
        self._records = list(recs)
        if not title:
            self.records_head.setText("Pick a set or a move to see its attack records.")
        elif not recs:
            self.records_head.setText(f"{title}: none.")
        else:
            self.records_head.setText(f"{title}: power, element, set.")
        self.records.setVisible(bool(recs))
        self.to_host.setVisible(bool(recs) and sess is not None)
        cells, tips = [], []
        for a in recs:
            mine = None if sess is None else sess.attack(a.id)
            vals = []
            for key in LEVERS:
                val = None if mine is None else getattr(mine, key)
                vals.append(int(getattr(a, key)) if val is None else int(val))
            tuned = mine is not None and not mine.is_empty
            cells.append(
                [
                    str(a.id),
                    str(vals[0]),
                    f"0x{vals[1]:02X}",
                    str(vals[2]),
                    "port" if tuned else "host",
                ]
            )
            tips.append(
                f"record 0x{a.va:08X}: kind {a.kind}, angle {a.angle}, tag 0x{a.tag:02X};"
                " the rest is not decoded"
            )
        editable = [False, True, True, True, False] if sess is not None else False
        self.records.set_cells(cells, editable=editable, tips=tips)
        self.records.fit(6)
