# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Moves dock's rules: each read as a sentence, added, deleted, and every field of the
picked one edited (`monster/rules.py`)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from mhfu_port.manifest import EVENTS, MAIN_STATES, PARTS, SEAM_RULES, UNLIMITED_DIST, Rule
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractSpinBox, QListView, QVBoxLayout, QWidget

from mhfu_studio.monster import rules
from mhfu_studio.monster.panels.common import kind
from mhfu_studio.shell.findings import Finding
from mhfu_studio.shell.text import plain
from mhfu_studio.ui import kit

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

FAR = 1.0e6
"""The farthest a distance box takes; its lowest, 0, means any distance."""
HUNTER = {
    "either": ("Either way", "Whichever way the hunter moves"),
    "receding": ("Moving away", "Only while the hunter moves away from the monster"),
    "closing": ("Closing in", "Only while the hunter closes in on the monster"),
}


class RulesSection(QWidget):
    """Shown under the moves; `sync` takes the Moves dock's checks."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws = ws
        act = studio.act

        def setter(what: str, **conv: object) -> None:
            act(f"rule {what}", lambda: ws.set_rule(**conv))()

        self.title = kit.label(role="caps", wrap=False)
        self.list = kit.Items(
            tip="The port's rules: each plays a move when what it waits for happens. Click one"
            " to change it.",
            empty="No rules yet: New rule plays the picked move when the monster notices you",
        )
        self.list.setWordWrap(True)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMaximumHeight(170)
        self.list.picked.connect(lambda i: act("pick rule", lambda: ws.pick_rule(int(i)))())
        self.new = kit.button(
            "New rule",
            tip="A rule that plays the picked move once, when the monster notices the hunter;"
            " change what it waits for below",
            on=act("new rule", ws.new_rule),
            icon="ph.plus",
        )
        self.delete = kit.button(
            "Delete rule",
            tip="Deletes the picked rule",
            on=act("delete rule", ws.delete_rule),
            icon="ph.trash",
        )

        self.editor = kit.Section("This rule", tip="The picked rule, field by field")
        self.sentence = kit.label()
        form = kit.Form()
        self.play = kit.choice(
            [],
            tip="The move it plays: one of your own, or one on the base monster's actions",
            on=lambda n: setter("play", play=n),
        )
        form.row("Plays", self.play)
        self.on = kit.choice(
            [("", "no event"), *((e, rules.event_words(e)) for e in EVENTS)],
            tip="The monster's event it fires on; a flinch rule plays its move in place of the"
            " base monster's flinch",
            on=lambda e: setter("event", on=e or None),
        )
        form.row("On", self.on)
        self.part = kit.choice(
            [],
            tip="Only the flinch or break of this part",
            on=lambda p: setter("part", part=int(p) if p else None),
        )
        self.part_label = form.row("Of", self.part)
        self.after = kit.choice(
            [],
            tip="Fires while this move plays, once it has held for Held frames",
            on=lambda n: setter("after", from_move=n or None),
        )
        form.row("During", self.after)
        self.mains = [
            kit.check(
                str(k),
                tip=f"Fires while the base monster is in main state {k}, once it has held for"
                " Held frames",
                on=lambda _on: setter("main states", from_main=self._mains()),
            )
            for k in MAIN_STATES
        ]
        form.row("Or main", kit.row(*self.mains, stretch=True, spacing=2))
        self.min_frames = kit.integer(
            tip="Frames the move or main state must have played before the rule fires",
            lo=0,
            hi=0xFFFF,
            on=lambda v: setter("held", min_frames=v),
        )
        self.min_frames.setSuffix(" frames")
        form.row("Held", self.min_frames)
        self.near = kit.number(
            tip="The nearest the hunter may be, in world units",
            lo=0,
            hi=FAR,
            on=lambda _v: setter("distance", dist=self._dist()),
        )
        self.far = kit.number(
            tip="The farthest the hunter may be, in world units; the lowest is any distance",
            lo=0,
            hi=FAR,
            on=lambda _v: setter("distance", dist=self._dist()),
        )
        self.far.setSpecialValueText("any distance")
        form.row("Hunter at", kit.row(self.near, kit.label("to", role="muted"), self.far))
        self.hunter = kit.Segmented(
            [(k, w) for k, (w, _) in HUNTER.items()],
            tip="Which way the hunter must be moving",
            tips={k: t for k, (_, t) in HUNTER.items()},
            on=lambda w: setter("hunter", receding=w == "receding", closing=w == "closing"),
        )
        form.row("Hunter", self.hunter)
        self.mode = kit.integer(
            tip="The mode the move's action is entered with; an own move enters its carrier and"
            " has none",
            lo=0,
            hi=0xFF,
            on=lambda v: setter("mode", mode=v),
        )
        self.mode_label = form.row("Mode", self.mode)
        self.cooldown = kit.integer(
            tip="Frames the rule waits after it fired before it may fire again",
            lo=0,
            hi=0xFFFF,
            on=lambda v: setter("cooldown", cooldown=v),
        )
        self.cooldown.setSuffix(" frames")
        form.row("Then waits", self.cooldown)
        self.times = kit.integer(
            tip="How often it fires at most; the lowest is every time",
            lo=0,
            hi=0xFFFF,
            on=lambda v: setter("times", count=v or None),
        )
        self.times.setSpecialValueText("every time")
        form.row("Times", self.times)
        self.force = kit.check(
            "Right away", tip=rules.FORCE_TIP, on=lambda on: setter("force", force=on)
        )
        self.force_label = form.row("Force", self.force)
        self.label = kit.text_field(
            tip="A note for people; the game does not read it", placeholder="what it is for"
        )
        self.label.editingFinished.connect(lambda: setter("note", label=self.label.text().strip()))
        form.row("Note", self.label)
        for box in (self.min_frames, self.near, self.far, self.mode, self.cooldown, self.times):
            box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.found = QWidget()
        self.found_lay = QVBoxLayout(self.found)
        self.found_lay.setContentsMargins(0, 0, 0, 0)
        self._found_key: object = None
        for w in (self.sentence, form, self.found):
            self.editor.body.addWidget(w)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        for w in (self.title, self.list, kit.row(self.new, self.delete, stretch=True)):
            lay.addWidget(w)
        lay.addWidget(self.editor)

    def _mains(self) -> list[int]:
        return [k for k, box in zip(MAIN_STATES, self.mains, strict=True) if box.isChecked()]

    def _dist(self) -> tuple[float, float]:
        far = self.far.value()
        return self.near.value(), far if far > 0 else UNLIMITED_DIST

    def sync(self, checks: Sequence[Finding]) -> None:
        ws, m = self.ws, self.ws.manifest
        if m is None:
            return
        self.title.setText(f"Rules · {len(m.rules)} of {SEAM_RULES}")
        mine = {i: [f for f in checks if f.target == ("rule", i)] for i in range(len(m.rules))}
        items = [
            kit.Item(rules.sentence(r, m), i, r.label, "error" if mine[i] else None)
            for i, r in enumerate(m.rules)
        ]
        self.list.set_items(items)
        i = ws.picked_rule
        self.list.setCurrentRow(-1 if i is None else i)
        self.delete.setEnabled(i is not None)
        self.editor.setVisible(i is not None)
        if i is not None:
            self._sync_rule(m.rules[i], mine[i])

    def _sync_rule(self, r: Rule, found: list[Finding]) -> None:
        m = self.ws.manifest
        assert m is not None
        self.sentence.setText(rules.sentence(r, m).capitalize() + ".")
        kit.refill(self.play, [(n, f"{n} · {kind(mv)}") for n, mv in m.moves.items()], r.play)
        kit.put(self.on, r.on or "")
        takes = rules.takes_part(r.on)
        self.part.setVisible(takes)
        self.part_label.setVisible(takes)
        parts = [(str(k), rules.part_name(m, k).removeprefix("the ")) for k in PARTS]
        kit.refill(self.part, [("", "any part"), *parts], "" if r.part is None else str(r.part))
        others = [(n, n) for n in m.moves if n != r.play]
        kit.refill(self.after, [("", "no move"), *others], r.from_move or "")
        for k, box in zip(MAIN_STATES, self.mains, strict=True):
            kit.put(box, k in r.from_main)
        lo, hi = r.dist
        kit.put(self.min_frames, r.min_frames)
        kit.put(self.near, lo)
        kit.put(self.far, 0.0 if hi >= UNLIMITED_DIST else hi)
        kit.put(self.hunter, "receding" if r.receding else "closing" if r.closing else "either")
        played = m.moves.get(r.play)
        pair = played is not None and not played.own
        self.mode.setVisible(pair)
        self.mode_label.setVisible(pair)
        kit.put(self.mode, r.mode)
        kit.put(self.cooldown, r.cooldown)
        kit.put(self.times, r.count or 0)
        kit.put(self.label, r.label)
        self.force.setVisible(rules.FORCE)
        self.force_label.setVisible(rules.FORCE)
        kit.put(self.force, rules.forced(r))
        key = tuple(found)
        if key != self._found_key:
            self._found_key = key
            while (it := self.found_lay.takeAt(0)) is not None:
                if (w := it.widget()) is not None:
                    w.deleteLater()
            for f in found:
                a = kit.Alert(plain(f.message), f.level)
                a.setToolTip(f.code)
                self.found_lay.addWidget(a)
        self.found.setVisible(bool(found))
