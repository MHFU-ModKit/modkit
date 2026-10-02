# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Hitboxes panel: where he hits YOU. The Parts panel's mirror, with hit groups for parts:
a move spawns an attack by id, the attack names a hit group, the group's spheres are the hitboxes.
"""

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
from .parts import HOST_BONES
from .widgets import (
    PORT,
    NoScene,
    SendRow,
    VolumeForm,
    base_name,
    describe,
    export_button,
    fly_to,
    place,
    see_through,
    shared_note,
    source_switch,
    sync_see_through,
    sync_source,
    tiles,
)

if TYPE_CHECKING:
    from mhfu_studio.monster.attacks import AttackSession
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

GROUP_TIP = (
    "A move spawns an attack, the attack names a hit group, and the group's hitboxes are where"
    " the blow lands."
)
LEVER_HEADS = ("Attack", "Power", "Element", "Group", "From")


def moves_hitting(ws: MonsterWorkspace, set_index: int) -> tuple[list[str], int]:
    """`(declared moves, other actions)` whose handler hits with this group."""
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


class HitboxesPanel(kit.Panel):
    """Where the monster hits you: show, copy, pick a hit group, a hitbox, change it, send."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        self._records: list[AttackRecord] = []
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        show = kit.Section("Show", tip="Whose hitboxes, and whether the view draws them")
        self.source = source_switch(
            ws, "hitboxes and attacks", lambda k: self._act("hitboxes", lambda: self._source(k))
        )
        self.draw = kit.check(
            "Show in view",
            tip="Draws the hitboxes on the monster, one colour per hit group",
            on=lambda on: self._act("show hitboxes", lambda: self._show(on)),
        )
        self.xray = see_through(ws, studio)
        self.no_intel = kit.Alert(level="info")
        for w in (self.source, kit.row(self.draw, self.xray, stretch=True), self.no_intel):
            show.body.addWidget(w)
        lay.addWidget(show)

        start = kit.Section(
            "Start from the base monster", tip="Copy the base monster's hitboxes into yours"
        )
        self.start_hint = kit.label(
            "You have no hitboxes yet: pick a hit group below and copy it from the base monster.",
            role="muted",
        )
        self.adopt_pair = kit.button(
            "Copy the hit groups this action uses",
            tip="Copies the base monster's hitboxes of every hit group the picked action"
            " spawns, to move onto the right joints of your skeleton. " + HOST_BONES,
            on=self._adopt_pair,
            icon="ph.copy",
        )
        start.body.addWidget(self.start_hint)
        self.start_box, self.start_lay = start, start.body
        lay.addWidget(start)

        sets = kit.Section("1  Pick a hit group", tip=GROUP_TIP)
        self.move_only = kit.check(
            "Only this action's hit groups",
            tip="Lists and draws only the hit groups the action picked in Moves or Action uses",
            on=lambda on: self._act("action's groups", lambda: self._move_only(on)),
        )
        self.no_attack = kit.label(role="muted")
        self.orphans = kit.Alert(level="error")
        self.sets_head = kit.label(role="muted")
        self.sets = kit.Table(
            ["Group", "Hitboxes", "Used by", "Attacks"],
            tip="The hit groups; pick one to light it in the view. Hitboxes reads yours/fit: how"
            " many you have, how many fit.",
        )
        self.sets.picked.connect(self._pick_set)
        for w in (self.move_only, self.no_attack, self.orphans, self.sets_head, self.sets):
            sets.body.addWidget(w)
        self.sets_box = sets
        lay.addWidget(sets)

        vols = kit.Section("2  Pick a hitbox", tip="Pick one here or click it in the view")
        self.vols_box = vols
        self.host_hint = kit.label(role="muted")
        self.to_port = kit.button(
            "Edit yours",
            tip="Switches to your port's hitboxes, the ones you can change",
            on=lambda: self._act("hitboxes", lambda: self._source(PORT)),
            role="primary",
        )
        self.set_hint = kit.label(role="muted")
        self.adopt_set = kit.button(
            "Copy the base monster's hit group",
            tip="Copies the base monster's hitboxes of this hit group into yours, to move. "
            + HOST_BONES,
            on=self._adopt_set,
            icon="ph.copy",
        )
        self.add = kit.button(
            "Add a sphere",
            tip="Adds one sphere on joint 1 to the picked hit group, to move where you want it",
            on=self._add,
            icon="ph.plus",
        )
        self.vols_head = kit.label(role="muted")
        self.over = kit.Alert(level="error")
        self.vols = kit.Table(
            ["#", "Group", "Joint", "Radius"],
            tip="Your hitboxes, the picked group's or all; * marks one changed since the last"
            " save. Click one to change it and light it in the view.",
            swatch_column=1,
        )
        self.vols.picked.connect(self._pick_volume)
        self.pick_hint = kit.label(
            "Pick a hitbox, here or in the view, to change it.", role="muted"
        )
        self.adopt_here = QVBoxLayout()
        self.adopt_here.addWidget(self.adopt_set)
        for w in (self.host_hint, self.to_port, self.set_hint):
            vols.body.addWidget(w)
        vols.body.addLayout(self.adopt_here)
        for w in (
            self.add,
            self.vols_head,
            self.over,
            self.vols,
            self.pick_hint,
        ):
            vols.body.addWidget(w)
        lay.addWidget(vols)

        edit = kit.Section("3  Change it", tip="Each change is one undo step and shows at once")
        self.edit_box = edit
        self.set_box = kit.integer(
            tip="Which of the base monster's hit groups this hitbox belongs to: the attack picks"
            " the group",
            lo=0,
            hi=0,
            on=lambda v: self._stage(set=v),
        )
        self.set_swatch = kit.Swatch(tip="The hit group's colour in the view")
        self.label = kit.text_field(
            tip="Your note for this hitbox: 'tail tip', 'left claw'", placeholder="what it is"
        )
        self.label.editingFinished.connect(self._label)
        self.form = VolumeForm(
            self._stage,
            lambda: None if ws.vp is None else ws.vp.selected_joint,
            bone_tip="A joint of your port's skeleton; 126 and 127 are the attack's own place."
            " Pick a joint in the view to read it.",
            extra=[
                ("Hit group", kit.row(self.set_box, self.set_swatch, stretch=True)),
                ("Note", self.label),
            ],
        )
        self.copy = kit.button(
            "Duplicate", tip="Adds a copy of this hitbox to move", on=self._copy, icon="ph.copy"
        )
        self.look = kit.button(
            "Frame it",
            tip="Points the camera at this hitbox",
            on=self.studio.act("frame hitbox", self._look),
            icon="ph.crosshair",
        )
        self.delete = kit.button(
            "Delete", tip="Removes this hitbox", on=self._delete, role="danger", icon="ph.trash"
        )
        self.shared = kit.Alert()
        edit.body.addWidget(self.form)
        edit.body.addWidget(tiles(self.copy, self.look, self.delete, columns=3))
        edit.body.addWidget(self.shared)
        lay.addWidget(edit)

        ship = kit.Section("4  Send to game", tip="Your hitboxes onto the memory stick")
        self.send = SendRow(ws, studio)
        ship.body.addWidget(self.send)
        self.ship_box = ship
        lay.addWidget(ship)
        self.no_manifest = kit.label(
            "This scene has no manifest, so there is nothing to write hitboxes into.",
            role="muted",
        )
        lay.addWidget(self.no_manifest)

        self.more = kit.More(
            tip="Attack stats, where the attack table was found, test tools, copying again and"
            " export"
        )
        self.join = kit.Alert(level=None, role="muted")
        self.offset_note = kit.Alert(level=None, role="muted")
        self.records_head = kit.label(role="muted")
        self.records = kit.Grid(
            LEVER_HEADS,
            tip="The attacks on the picked hit group or action. On yours, double-click power,"
            " element (0x hex is fine) or group to change it; a value equal to the base"
            " monster's changes nothing.",
        )
        self.records.edited.connect(self._lever)
        self.to_host = kit.button(
            "Back to the base monster's",
            tip="Drops your changes to the picked attack: the base monster's stands",
            on=self._clear_record,
            icon="ph.arrow-counter-clockwise",
        )
        self.keep = kit.button(
            "Keep only the picked hitbox",
            tip="Drops the other hitboxes of its group, so where the blow lands is the whole"
            " answer. A test tool; the other groups stay.",
            on=self._keep,
        )
        self.again = QVBoxLayout()
        self.export = export_button(ws, studio)
        stats = kit.label("Attack stats", role="caps", wrap=False)
        stats.setToolTip("Power, element and hit group of each attack: the decoded bytes")
        for w in (self.join, self.offset_note, stats, self.records_head, self.records):
            self.more.body.addWidget(w)
        self.more.body.addWidget(kit.row(self.to_host, stretch=True))
        self.more.body.addWidget(kit.row(self.keep, stretch=True))
        self.more.body.addLayout(self.again)
        self.more.body.addWidget(kit.row(self.export, stretch=True))
        lay.addWidget(self.more)

        self.pages = kit.Pages(page, NoScene(studio))
        self.body.addWidget(self.pages)
        self.body.addStretch(1)

    # ---- actions --------------------------------------------------------------------- #

    def _act(self, label: str, fn: Callable[[], object]) -> None:
        self.studio.act(label, fn)()

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
                msg = f"hitbox {i}: {describe(fields)}"
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
            if self.ws.edit(f"added a hitbox to group {st}", lambda: sess.add_volume(new)):
                self.ws.select_attack_volume(len(sess.volumes()) - 1)

        self._act("add hitbox", run)

    def _adopt(self, sess: AttackSession, host: AttackIntel, st: int) -> bool:
        hs = host.set(st)
        if hs is None or not hs.spheres:
            return False
        src = f"{base_name(self.ws)} hit group {st}"
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

        self._act("copy hit group", run)

    def _adopt_pair(self) -> None:
        def run() -> None:
            sess, host = self._session(), self.ws.host_attacks()
            if host is None:
                return
            done = sum(self._adopt(sess, host, st) for st in self.ws.pair_sets())
            self.ws.attacks_source = PORT
            self.ws.sync()
            self.ws.message = (
                f"copied {done} hit group(s) from {base_name(self.ws)}: its joint numbers on"
                " your skeleton, a start you can see, not the answer"
            )

        self._act("copy the action's groups", run)

    def _keep(self) -> None:
        i = self.ws.selected_attack_volume

        def run() -> None:
            sess = self._session()
            if i is None:
                return
            st = sess.volumes()[i].set
            gone = len(sess.volumes_of(st)) - 1
            msg = (
                f"kept hitbox {i}, dropped {gone} from group {st}: the attack lands there or"
                " nowhere"
            )
            if self.ws.edit(msg, lambda: sess.keep_only(i)):
                self.ws.select_attack_volume(sess.volumes_of(st)[0][0])

        self._act("keep only", run)

    def _delete(self) -> None:
        i = self.ws.selected_attack_volume

        def run() -> None:
            sess = self._session()
            if i is not None and self.ws.edit(f"deleted hitbox {i}", lambda: sess.remove_volume(i)):
                self.ws.select_attack_volume(None)

        self._act("delete hitbox", run)

    def _copy(self) -> None:
        i = self.ws.selected_attack_volume

        def run() -> None:
            sess = self._session()
            if i is None:
                return
            v = sess.volumes()[i]
            if self.ws.edit(f"hitbox {i} duplicated", lambda: sess.add_volume(v)):
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
            same = ", the base monster's, so nothing changes" if value == host_value else ""
            levers = {key: None if value == host_value else value}
            msg = f"attack {a.id} {key} = {value}{same}"
            self.ws.edit(msg, lambda: sess.set_attack(a.id, **levers))

        self._act("attack lever", run)

    def _clear_record(self) -> None:
        row = self.records.currentRow()

        def run() -> None:
            sess = self._session()
            if not 0 <= row < len(self._records):
                self.ws.message = "pick an attack first"
                return
            aid = self._records[row].id
            self.ws.edit(
                f"attack {aid}: back to the base monster's",
                lambda: sess.clear_attack(aid),
            )

        self._act("attack to base", run)

    # ---- sync ------------------------------------------------------------------------ #

    def sync(self) -> None:
        ws = self.ws
        self.pages.show_page(ws.scene is not None)
        if ws.scene is None:
            return
        host, sess = ws.host_attacks(), ws.attack_session
        sync_source(self.source, ws, ws.attacks_source)
        kit.put(self.draw, ws.show_attacks)
        sync_see_through(self.xray, ws)
        self.no_intel.setText(ws.intel_gap("attack"))
        self.no_intel.setVisible(host is None)
        port = ws.attacks_source == PORT
        has_doc = sess is not None and ws.manifest is not None
        for w in (self.sets_box, self.vols_box):
            w.setVisible(host is not None)
        if host is None:
            for x in (self.join, self.offset_note, self.edit_box, self.start_box):
                x.setVisible(False)
        else:
            self._provenance(host)
            self._sets(host, sess if port else None)
            self._volumes(host, sess, port)
            self._levers(host, sess if port else None)
            self._start(sess if has_doc else None)
        self.shared.setText(shared_note(ws))
        self.ship_box.setVisible(has_doc)
        self.send.sync()
        self.export.setEnabled(ws.exportable())
        self.keep.setEnabled(port and ws.selected_attack_volume is not None)
        self.no_manifest.setVisible(not has_doc)

    def _start(self, sess: AttackSession | None) -> None:
        """Copying the action's groups: on top while you have no hitboxes, else in More."""
        ws = self.ws
        empty = sess is not None and not sess.volumes()
        place(self.adopt_pair, self.start_lay if empty else self.again)
        pair = bool(ws.pair_sets()) and ws.pair is not None
        self.adopt_pair.setVisible(sess is not None and pair)
        if ws.pair is not None:
            m, s = ws.pair
            self.adopt_pair.setText(
                f"Copy the hit groups ({m},{s}) uses" + ("" if empty else " again")
            )
        self.start_box.setVisible(empty)

    def _provenance(self, host: AttackIntel) -> None:
        ws = self.ws
        sp = f"0x{host.spawner:08X}" if host.spawner else "?"
        if host.join == "measured":
            n = len(host.primary.attacks) if host.primary else 0
            self.join.setText(f"Spawner {sp} to the {n}-attack table: measured in the game.")
        else:
            self.join.setText(
                f"Spawner {sp} to its table: inferred ({host.join}). Only em75's was traced in"
                " the game; here the id range was matched to the biggest table."
            )
        self.join.set_level(None if host.join == "measured" else "warning")
        self.join.setToolTip(host.join_provenance)
        self.join.setVisible(True)
        hsp = ws.host_species
        off = None if hsp is None else host.id_offset(hsp)
        if off is None:
            self.offset_note.setText(
                f"Species {hsp} shares this overlay but its id offset is unknown: an action's"
                " attack ids cannot be matched to attacks."
            )
        else:
            self.offset_note.setText(f"Species {hsp} uses attack = handler id + {off}.")
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
                f"Only the groups ({m},{s}) uses: " + ", ".join(map(str, pair_sets))
            )
            kit.put(self.move_only, ws.sets_of_move_only)
        silent = ws.pair is not None and not pair_sets
        if silent and ws.pair is not None:
            self.no_attack.setText(
                f"({ws.pair[0]},{ws.pair[1]}) spawns no attack the code shows: a turn, a roar,"
                " a walk; or an id worked out while it runs."
            )
        self.no_attack.setVisible(silent)
        only = on_pair and ws.sets_of_move_only
        listed = sorted(pair_sets) if only else [st.index for st in host.sets]
        if sess is not None and not only:
            listed = sorted(set(listed) | set(sess.sets()))
        lost = ws.attack_orphans
        self.orphans.setText(
            f"{len(lost)} hitbox(es) name a joint your skeleton does not have"
            f" ({', '.join(str(getattr(o, 'bone', '?')) for o in lost[:6])}), so they are drawn"
            " nowhere. Joint numbers belong to the skeleton they were made for."
        )
        self.orphans.setVisible(bool(lost))
        self.sets_head.setText(
            f"{len(listed)} of {len(host.sets)} hit groups; {len(host.attacks)} attacks"
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
                moves += (" " if moves else "") + f"({others} action{'' if others == 1 else 's'})"
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
                    count,
                    moves,
                    ", ".join(f"{a.id}(p{a.power})" for a in atks[:2]) + more,
                )
            )
            tip = [f"joints {bone_span(bones)}"] if bones else []
            tip += [f"attack {a.id}: {a.describe()}" for a in atks]
            if names:
                tip.append("moves: " + ", ".join(names))
            if unrigged:
                tip.append(
                    "On no joint: every hitbox hangs on 126/127, the attack's own place, like a"
                    " projectile. Nothing here to move onto a joint."
                )
            if over:
                tip.append(f"{over} more than fit: the game drops the rest")
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
        self.vols_box.title.setText(
            "2  Pick a hitbox" + ("" if st is None else f" in hit group {st}")
        )
        self.host_hint.setVisible(not port)
        self.to_port.setVisible(not port and sess is not None)
        if not port:
            self.host_hint.setText(
                "These are the base monster's hitboxes, read only. To change one, edit yours:"
                " copy the base monster's hit group there first."
            )
        editing = port and sess is not None
        vols = [] if sess is None else sess.volumes()
        rows_of = [] if sess is None or st is None else sess.volumes_of(st)
        empty_set = editing and st is not None and not rows_of
        hs = None if st is None else host.set(st)
        self.set_hint.setVisible(editing and (empty_set or not vols))
        if empty_set:
            n = 0 if hs is None else hs.capacity
            self.set_hint.setText(
                f"Hit group {st}: none of yours yet, the base monster's {n} hitbox(es) stand."
            )
        elif editing and not vols:
            self.set_hint.setText("Pick a hit group above to copy the base monster's.")
        can_adopt = editing and st is not None and hs is not None and bool(hs.spheres)
        place(self.adopt_set, self.again if rows_of else self.adopt_here)
        self.adopt_set.setVisible(
            can_adopt and (bool(rows_of) or ws.selected_attack_volume is None)
        )
        self.adopt_set.setText(
            f"Copy the base monster's hit group {st}" + (" again" if rows_of else "")
        )
        self.add.setVisible(editing and st is not None and ws.selected_attack_volume is None)
        self.add.setText(f"Add a sphere to hit group {st}")
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
            head = f"Hit group {st}: {len(rows)} hitbox(es)" + (
                "" if cap is None else f", {cap} fit"
            )
            over = sess.over_capacity(st)
        else:
            head = f"{len(vols)} hitbox(es) in {len(sess.sets())} hit group(s)"
            over = sum(sess.over_capacity_all().values())
        self.vols_head.setText(head)
        self.over.setText(f"{over} more than fit: the game drops the rest of the group")
        self.over.setVisible(over > 0)
        cells, data, colors, tips = [], [], [], []
        for i, v in rows:
            changed = sess.volume_changed(i)
            o = v.offset or (0.0, 0.0, 0.0)
            if v.is_node_space:
                bone, tip = "attack", f"Joint {v.bone}: the attack's own place, its origin."
            elif v.is_marker:
                bone, tip = "0x7D", "A marker the game reads past, with no shape. Drawn nowhere."
            else:
                bone, tip = str(v.bone), ""
            cells.append((f"{i}{' *' if changed else ''}", str(v.set), bone, f"{v.radius:g}"))
            data.append(i)
            colors.append(set_color(v.set))
            bits = (
                f"{v.shape}, offset {o[0]:g} {o[1]:g} {o[2]:g}",
                "Changed, not saved yet." if changed else "",
                tip,
            )
            tips.append(" ".join(t for t in bits if t))
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
            f"A joint of your port's skeleton ({n} joints); 126 and 127 are the attack's own"
            " place. Pick a joint in the view to read it."
        )
        self.set_box.setMaximum(max(len(host.sets) - 1, v.set))
        kit.put(self.set_box, v.set)
        self.set_swatch.set(set_color(v.set))
        kit.put(self.label, v.label)
        self.edit_box.setVisible(True)

    def _levers(self, host: AttackIntel, sess: AttackSession | None) -> None:
        ws = self.ws
        hp = ws.host_pair()
        if ws.selected_set is not None:
            recs = host.attacks_using(ws.selected_set)
            title = f"Attacks using hit group {ws.selected_set}"
        elif hp is not None and ws.pair is not None:
            recs = host.records_for(hp.attack_ids, ws.host_species)
            title = f"Attacks ({ws.pair[0]},{ws.pair[1]}) spawns"
        else:
            recs, title = [], ""
        self._records = list(recs)
        if not title:
            self.records_head.setText("Pick a hit group or an action to see its attacks.")
        elif not recs:
            self.records_head.setText(f"{title}: none.")
        else:
            self.records_head.setText(f"{title}: power, element, group.")
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
                    "yours" if tuned else "base",
                ]
            )
            tips.append(
                f"attack 0x{a.va:08X}: kind {a.kind}, angle {a.angle}, tag 0x{a.tag:02X};"
                " the rest is not decoded"
            )
        editable = [False, True, True, True, False] if sess is not None else False
        self.records.set_cells(cells, editable=editable, tips=tips)
        self.records.fit(6)
