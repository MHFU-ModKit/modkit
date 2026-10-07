# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Moves dock: every move of the port, on a base monster's action or its own; an own move
made from the clip on screen, renamed, deleted, and its fields and steer edited. Its attack
windows are drawn on the Timeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mhfu_port.manifest import MOVE_ATTACKS, TURNS, Move
from PySide6.QtWidgets import QAbstractSpinBox, QSpinBox, QVBoxLayout, QWidget

from mhfu_studio.monster import validate as V
from mhfu_studio.monster.panels.widgets import NoScene
from mhfu_studio.shell.findings import Finding
from mhfu_studio.shell.text import plain
from mhfu_studio.ui import findings, kit

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

ROWS = 8
TURN_WORDS = {
    "clip": ("Clip's", "Turns as the clip does: its turn in Clips, or the ring in the view"),
    "still": ("Still", "Does not turn"),
    "hunter": ("Hunter", "Turns toward the hunter, at most Rate a frame"),
    "away": ("Away", "Turns away from the hunter, at most Rate a frame"),
    "fixed": ("Fixed", "Turns Angle degrees, spread evenly over Frames AI frames"),
}
GAME_NOTE = (
    "Own moves reach the game from the moves module mhfu-port generates from this file with the"
    " port, not through Copy Lua; a mod or a rule plays them by name."
)


def special(box: QSpinBox, text: str) -> QSpinBox:
    """`box` shows `text` at its lowest value, which means "none"."""
    box.setSpecialValueText(text)
    return box


def kind(mv: Move) -> str:
    return "own move" if mv.pair is None else f"({mv.main},{mv.sub})"


class MovesPanel(kit.Panel):
    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        act = studio.act
        self._loaded: object = None

        self.count = kit.label(role="title", wrap=False)
        self.table = kit.Table(
            ["Move", "Plays on", "Clip", "Attacks"],
            tip="Your moves: on one of the base monster's actions, or your own, which the move"
            " player plays whole. Click one to watch it.",
        )
        self.table.picked.connect(
            lambda n: act("pick move", lambda: ws.select_move(str(n)))() if n else None
        )
        self.new = kit.button(
            "New from the clip on screen",
            tip="Makes an own move that plays the clip on screen, named after it",
            on=act("new move", ws.new_move),
            role="primary",
            icon="ph.plus",
        )
        self.delete = kit.button(
            "Delete",
            tip="Deletes the picked move; refused while another move, a rule or an effect names it",
            on=act("delete move", ws.delete_move),
            icon="ph.trash",
        )

        self.editor = kit.Section("This move", tip="The picked own move: what the game plays")
        self.title = kit.label(role="title", wrap=False)
        self.clip = kit.label(role="muted")
        self.play_clip = kit.button(
            "Play its clip",
            tip="Puts the move's clip back on screen, turning as the move steers",
            on=act("play move", lambda: ws.select_move(ws.move or "")),
            icon="ph.play",
        )
        self.pair_note = kit.label(role="muted")
        form = kit.Form()
        self.name = kit.text_field(
            tip="The move's name: a mod or a rule plays it by this name; renaming follows them",
            placeholder="move name",
        )
        self.name.editingFinished.connect(self._rename)
        form.row("Name", self.name)
        self.length = special(
            kit.integer(
                tip="AI frames from the clip's start to the move's end; the lowest is until the"
                " clip ends",
                lo=0,
                hi=0xFFFF,
                on=lambda v: act("length", lambda: ws.set_move(length=v or None))(),
            ),
            "until the clip ends",
        )
        self.length.setSuffix(" AI frames")
        form.row("Length", self.length)
        self.hub = kit.check(
            "The base monster's hub",
            tip="Rides the action the base monster's brain picks its next move from, which"
            " hands back to the brain once the clip ends; untick to give another",
            on=lambda on: act("carrier", lambda: self._carrier(None if on else self._a_hub()))(),
        )
        self.main = kit.integer(
            tip="The carrier's main state: the base monster's action the move rides",
            lo=0,
            hi=7,
            on=lambda _v: act("carrier", lambda: self._carrier(self._typed()))(),
        )
        self.sub = kit.integer(
            tip="The carrier's sub state",
            lo=0,
            hi=255,
            on=lambda _v: act("carrier", lambda: self._carrier(self._typed()))(),
        )
        form.row("Carrier", kit.row(self.hub, self.main, self.sub, stretch=True))
        self.host_attacks = kit.check(
            "Keep the base monster's",
            tip="Keeps the attacks and effects the base monster's own clip in this anim spawns;"
            " by default only your attack windows hit",
            on=lambda on: act("host attacks", lambda: ws.set_move(host_attacks=on))(),
        )
        form.row("Base attacks", self.host_attacks)
        self.after = kit.choice(
            [],
            tip="The move played when this one ends; none: the base monster's brain picks",
            on=lambda n: act("after", lambda: ws.set_move(after=n or None))(),
        )
        form.row("Then", self.after)

        steer = kit.Form()
        self.turn = kit.Segmented(
            [(t, TURN_WORDS[t][0]) for t in TURNS],
            tip="How the move turns the monster",
            tips={t: TURN_WORDS[t][1] for t in TURNS},
            on=lambda t: act("turn", lambda: ws.set_steer(turn=t))(),
        )
        steer.row("Turn", self.turn)
        self.rate = special(
            kit.integer(
                tip="Most YAW units a frame it turns (65536 a full turn); the lowest is the base"
                " monster's charge's",
                lo=0,
                hi=0xFFFF,
                on=lambda v: act("rate", lambda: ws.set_steer(rate=v or None))(),
            ),
            "the charge's",
        )
        self.rate_label = steer.row("Rate", self.rate)
        self.angle = kit.number(
            tip="Degrees it turns over the move, + the way YAW grows (to its left); the ring in"
            " the view drags it too",
            lo=-359,
            hi=359,
            suffix="°",
            on=lambda v: act("angle", lambda: ws.set_steer(angle=v))(),
        )
        self.angle_label = steer.row("Angle", self.angle)
        self.frames = kit.integer(
            tip="AI frames the fixed turn is spread over",
            lo=1,
            hi=0x7FFF,
            on=lambda v: act("frames", lambda: ws.set_steer(frames=v))(),
        )
        self.frames_label = steer.row("Over", self.frames)
        self.walls = kit.check(
            "A wall ahead ends it",
            tip="Ends the move when the monster runs into a wall in the way it travels",
            on=lambda on: act("walls", lambda: ws.set_steer(walls=on))(),
        )
        steer.row("Walls", self.walls)
        self.dir = kit.number(
            tip="Which way it travels against its facing, for the walls: 0 forward, 180 back",
            lo=-180,
            hi=180,
            suffix="°",
            on=lambda v: act("travel", lambda: ws.set_steer(dir=v))(),
        )
        steer.row("Travels", self.dir)

        self.attacks = kit.Items(
            tip="The move's attacks: each spawns when the clip reaches its first frame and ends"
            " at its last. Click one to pick it on the Timeline.",
            empty="No attacks yet: drag on the Timeline's attack lanes to add one",
        )
        self.attacks.setMaximumHeight(110)
        self.attacks.picked.connect(
            lambda i: act("pick attack", lambda: setattr(ws, "picked_window", i))()
        )
        self.attacks_note = kit.label(
            f"Drag on the Timeline's attack lanes to add or move one; {MOVE_ATTACKS} at most.",
            role="muted",
        )
        self.found = QWidget()
        self.found_lay = QVBoxLayout(self.found)
        self.found_lay.setContentsMargins(0, 0, 0, 0)
        self._found_key: object = None
        #: the own moves' checks, and the manifest they were made for
        self._checked: tuple[object, list[Finding]] = (None, [])

        self.own_body = QWidget()
        body = QVBoxLayout(self.own_body)
        body.setContentsMargins(0, 0, 0, 0)
        parts: tuple[QWidget, ...] = (
            form,
            kit.label("Turn and walls", role="caps"),
            steer,
            kit.label("Attacks", role="caps"),
            self.attacks,
            self.attacks_note,
        )
        for w in parts:
            body.addWidget(w)
        top: tuple[QWidget, ...] = (
            self.title,
            kit.row(self.clip, self.play_clip, stretch=True),
            self.pair_note,
            self.own_body,
            self.found,
        )
        for w in top:
            self.editor.body.addWidget(w)

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        shown: tuple[QWidget, ...] = (
            self.count,
            self.table,
            kit.row(self.new, self.delete, stretch=True),
            self.editor,
            kit.label(GAME_NOTE, role="muted"),
        )
        for w in shown:
            lay.addWidget(w)
        lay.addStretch(1)
        self.no_scene = NoScene(studio)
        self.no_scene.say(
            "No port manifest",
            "Moves live in a port manifest. Open one (ports/<name>.toml).",
        )
        self.pages = kit.Pages(page, self.no_scene)
        self.body.addWidget(self.pages)
        for box in (self.length, self.main, self.sub, self.rate, self.angle, self.frames, self.dir):
            box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

    # acting

    def _rename(self) -> None:
        new = self.name.text().strip()
        if new and new != self.ws.move:
            self.studio.act("rename move", lambda: self.ws.rename_move(new))()

    def _carrier(self, pair: tuple[int, int] | None) -> None:
        self.ws.set_move(carrier=pair)

    def _typed(self) -> tuple[int, int]:
        return self.main.value(), self.sub.value()

    def _a_hub(self) -> tuple[int, int]:
        """The base monster's first hub, to start an own carrier from."""
        si = self.ws.host_intel()
        hubs = [] if si is None else list(si.hubs)
        return hubs[0] if hubs else (0, 0)

    # showing

    def sync(self) -> None:
        ws, m = self.ws, self.ws.manifest
        self.pages.show_page(m is not None)
        if m is None:
            return
        own = sum(mv.own for mv in m.moves.values())
        self.count.setText(f"{len(m.moves)} moves, {own} of them own")
        rows, data, tips = [], [], []
        for n, mv in m.moves.items():
            rows.append([n, kind(mv), mv.clip or f"anim {mv.anim}", self._attacks(mv)])
            data.append(n)
            tips.append(mv.label or ("" if mv.pair is None else "edit it in Actions"))
        self.table.set_rows(rows, data, tips=tips)
        self.table.fit(ROWS)
        self.table.select_data(ws.move)
        vp = ws.vp
        self.new.setEnabled(vp is not None and vp.clip is not None)
        picked = None if ws.move is None else m.moves.get(ws.move)
        self.delete.setEnabled(picked is not None)
        self.editor.setVisible(picked is not None)
        if picked is None or ws.move is None:
            return
        self._sync_move(ws.move, picked)

    @staticmethod
    def _attacks(mv: Move) -> str:
        if mv.pair is not None:
            return "the action's"
        return ", ".join(str(a.id) for a in mv.attacks) or "·"

    def _sync_move(self, name: str, mv: Move) -> None:
        ws, m = self.ws, self.ws.manifest
        assert m is not None
        self.title.setText(f"{name} · {kind(mv)}")
        own = mv.pair is None
        self.own_body.setVisible(own)
        self.pair_note.setVisible(not own)
        self.pair_note.setText(
            f"Rides the base monster's ({mv.main},{mv.sub}): its clip and timing are in Actions."
        )
        slot = ws.move_slot(mv)
        on_screen = ws.own_move_on_screen() is not None
        where = "" if slot is None else f" (anim {slot})" if slot >= 0 else " (in no anim)"
        self.clip.setText(f"Plays {mv.clip or 'anim'}{where}")
        self.play_clip.setVisible(own and not on_screen)
        if name != self._loaded:
            self._loaded = name
            kit.put(self.name, name)
        if own:
            self._sync_own(name, mv)
        self._findings(name)

    def _sync_own(self, name: str, mv: Move) -> None:
        ws, m = self.ws, self.ws.manifest
        assert m is not None
        kit.put(self.length, mv.length or 0)
        kit.put(self.hub, mv.carrier is None)
        main, sub = mv.carrier if mv.carrier is not None else self._a_hub()
        kit.put(self.main, main)
        kit.put(self.sub, sub)
        self.main.setEnabled(mv.carrier is not None)
        self.sub.setEnabled(mv.carrier is not None)
        kit.put(self.host_attacks, mv.host_attacks)
        others = [(n, f"{n} \u00b7 {kind(o)}") for n, o in m.moves.items() if n != name]
        kit.refill(self.after, [("", "the brain picks"), *others], mv.after or "")
        s = mv.steer
        kit.put(self.turn, s.turn)
        steers = s.turn in ("hunter", "away")
        self.rate.setVisible(steers)
        self.rate_label.setVisible(steers)
        fixed = s.turn == "fixed"
        for label in (self.angle_label, self.frames_label):
            label.setVisible(fixed)
        self.angle.setVisible(fixed)
        self.frames.setVisible(fixed)
        kit.put(self.rate, s.rate or 0)
        kit.put(self.angle, s.angle or 0.0)
        kit.put(self.frames, s.frames or 1)
        kit.put(self.walls, s.walls)
        kit.put(self.dir, s.dir)
        items = []
        for i, a in enumerate(mv.attacks):
            rec = ws.attack_record(a.id)
            what = "no record" if rec is None else f"power {rec.power}, hit group {rec.volume}"
            span = f"frame {a.frame} on" if a.end is None else f"frames {a.frame}-{a.end}"
            items.append(
                kit.Item(
                    f"attack {a.id}: {span} ({what})",
                    i,
                    a.label or "",
                    "error" if rec is None and ws.host_attacks() is not None else None,
                )
            )
        self.attacks.set_items(items)
        if ws.picked_window is not None and ws.picked_window < self.attacks.count():
            self.attacks.setCurrentRow(ws.picked_window)

    def _findings(self, name: str) -> None:
        """The checks on this move: what the game will refuse or never reach."""
        ws, doc = self.ws, self.ws.doc
        if doc is None:
            return
        m = doc.manifest
        if self._checked[0] is not m:
            self._checked = (m, V.own_moves(m, doc.pac, doc.intel, doc.sources))
        found = [f for f in self._checked[1] if f.target == ("moves", name)]
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
        findings.take(ws, {V.MOVE_WINDOWS: self.attacks, V.MOVE_CLIP: self.play_clip})
