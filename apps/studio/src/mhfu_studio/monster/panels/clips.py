# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Clips panel: every clip of the original by MHP3rd id, the anim it plays in, what is
really in that anim (an idle copy plays idle, and forcing it looks exactly like a failed
override), and its name; naming, placing and Play in game."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from mhfu_studio.monster import clips
from mhfu_studio.monster import validate as V
from mhfu_studio.monster.clip_browser import SourceClip
from mhfu_studio.monster.clips import SlotCoverage, Vocabulary
from mhfu_studio.monster.render.playback import DEFAULT_SPEED, wall_clock
from mhfu_studio.shell.findings import Level
from mhfu_studio.ui import findings, kit

from .common import COVERAGE
from .widgets import NoScene

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

KINDS = (clips.CARRIED, clips.FILLER, clips.HOST, clips.ALTERED)
KIND_TIPS = {
    clips.CARRIED: "The original's own clip, intact: forcing this anim plays the move you ported",
    clips.FILLER: "A copy of the idle clip where the original had nothing: forcing it plays"
    " idle, which looks exactly like an override that never fired",
    clips.HOST: "The base monster's own clip, left in place: its motion on your skeleton",
    clips.ALTERED: "Matches neither the original's clip nor the base monster's",
}
FILLER_WARNING = (
    "{n} anims are idle copies. Forcing one plays idle, which looks just like an override that"
    " never fired: check the kind before blaming the script."
)
BUILD_TIP = (
    "The build a name typed here is keyed to. Anim numbers are per build: rebuild the port and"
    " they can shift, so every name remembers the build and its clip's length."
)
#: a name's health in words
STATUS = {
    clips.MOVED: "moved",
    clips.AMBIGUOUS: "unclear",
    clips.LOST: "gone",
    clips.UNCHECKABLE: "unchecked",
}
UNPLACED = "in no anim: the base monster has no anim left for it"


def _page() -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    return w, lay


class ClipsPanel(kit.Panel):
    """Every clip: play one, see what it really holds, name it, place it."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__(scroll=False)
        self.ws, self.studio = ws, studio
        self._picked: tuple[str, int] | None = None
        #: the row whose name and label the boxes were loaded with
        self._loaded: tuple[str, int] | None = None
        #: the row the arrow keys just played, so the click that follows does not replay it
        self._stepped_to: object = None
        self._rows: list[SourceClip] = []
        page, lay = _page()

        self.count = kit.label(role="title", wrap=False)
        lay.addWidget(self.count)

        self.kinds: dict[str, tuple[kit.Swatch, kit.Alert]] = {}
        row: list[QWidget] = []
        for kind in KINDS:
            sw = kit.Swatch(COVERAGE[kind], KIND_TIPS[kind])
            text = kit.Alert(level=None, wrap=False)
            text.setToolTip(KIND_TIPS[kind])
            self.kinds[kind] = (sw, text)
            row += [sw, text]
        self.kind_row = kit.row(*row, stretch=True)
        lay.addWidget(self.kind_row)
        self.filler = kit.Alert()
        self.filler.setToolTip(KIND_TIPS[clips.FILLER])
        self.dropped = kit.label(role="muted")
        self.dropped.setToolTip(
            "The base monster has no anim left for these clips of the original: they are not"
            " in this build. Place one over another clip to put it in."
        )
        self.notes = kit.label(role="muted")
        self.health = kit.Alert()
        self.suspect = kit.Items(
            tip="Names that stopped meaning what they say in this build; hover one for why"
        )
        self.suspect.setMaximumHeight(110)
        for w in (self.filler, self.dropped, self.notes, self.health, self.suspect):
            lay.addWidget(w)

        self.filter = kit.text_field(
            tip="Shows only the clips whose id, stream, anim #, name, kind or label contains this"
            " text ('stream 2', 'unnamed')",
            placeholder="Filter: id, stream 2, anim #, name, kind, label",
        )
        self.filter.textChanged.connect(self._filter)
        lay.addWidget(self.filter)
        self.table = kit.Table(
            ["Id", "Anim", "Frames", "Travel", "Name"],
            tip="Every clip of the original by its MHP3rd id (stream x 100 + slot), and the anim"
            " that plays it: what a script passes to force it. Click one, or step with the arrow"
            " keys, to play it from the start; a clip that repeats says loop after its frames;"
            " Travel is how far the clip carries the body.",
            swatch_column=1,
        )
        self.table.picked.connect(self._clicked)
        self.table.currentCellChanged.connect(self._stepped)
        lay.addWidget(self.table, 1)

        self.editor = kit.Section("Name the clip", tip="Write a name and a label into the manifest")
        self.slot = kit.label(role="title", wrap=False)
        self.slot_swatch = kit.Swatch()
        self.slot_kind = kit.label(wrap=False)
        self.editor.body.addWidget(
            kit.row(self.slot, self.slot_swatch, self.slot_kind, stretch=True)
        )
        self.why = kit.Alert(level=None, role="muted")
        self.editor.body.addWidget(self.why)
        form = kit.Form()
        self.name = kit.text_field(
            tip="The clip's key in the manifest (letters, digits, - and _). Moves name their clip"
            " by it; renaming follows them. Naming moves no clip. Return goes on to Shows.",
            placeholder="clip_07",
        )
        self.label = kit.text_field(
            tip="What the clip shows, in your words: 'tail sweep', 'roar'. Return applies and"
            " plays the next clip.",
            placeholder="tail sweep",
        )
        self.name.returnPressed.connect(self.label.setFocus)
        self.label.returnPressed.connect(self._apply_next)
        form.row("Name", self.name)
        form.row("Shows", self.label)
        self.anim = kit.integer(
            tip="The anim (executor entry) to play this clip in. Place pins it there; a clip"
            " pinned there swaps into this one's anim, any other takes the anim this frees.",
            lo=0,
            hi=0,
        )
        self.place = kit.button(
            "Place",
            tip="Moves the clip to the anim beside; the port is built again. Undo takes it back.",
            on=self._place,
            icon="ph.swap",
        )
        form.row("Anim", kit.row(self.anim, self.place, stretch=True))
        self.turn = kit.number(
            tip="Degrees the monster turns over the clip, + the way YAW grows (to its left):"
            " clips like a turn on the spot carry none of their own. The ring in the view"
            " drags it too.",
            lo=-359,
            hi=359,
            suffix="\u00b0",
            on=lambda v: self.studio.act("clip turn", lambda: self.ws.set_clip_turn(v))(),
        )
        self.own_turn = kit.button(
            "Its own",
            tip="Drops the turn set here: the clip turns as its own body does",
            on=self.studio.act("clip turn", lambda: self.ws.set_clip_turn(None)),
            icon="ph.arrow-counter-clockwise",
        )
        self.gizmo = kit.check(
            "Ring",
            tip="Shows the turn gizmo on the floor of the view (T)",
            on=lambda on: self.studio.act("turn gizmo", lambda: self._gizmo(on))(),
            checked=True,
        )
        self.turn_label = form.row("Turn", kit.row(self.turn, self.own_turn, self.gizmo))
        self.editor.body.addWidget(form)
        self.apply = kit.button(
            "Apply",
            tip="Writes the name and label into the manifest, keyed to this build",
            on=self._apply,
            icon="ph.check",
        )
        self.in_game = kit.button(
            "Play in game",
            tip="Holds this clip's anim on the running game's big monster through the"
            " framework's cli_bridge.lua (MHFU_LANE's PPSSPP, else the one running)",
            on=self.studio.act("play in game", self.ws.play_in_game),
            icon="ph.play",
        )
        self.release = kit.button(
            "Release",
            tip="Lets the big monster's own brain pick its moves again",
            on=self.studio.act("release", self.ws.release_in_game),
            icon="ph.square",
        )
        self.new_move = kit.button(
            "New move",
            tip="Makes an own move that plays this clip, in Moves: its attacks, turn and what"
            " follows it",
            on=self.studio.act("new move", self._new_move),
            icon="ph.plus",
        )
        self.editor.body.addWidget(
            kit.row(self.apply, self.in_game, self.release, self.new_move, stretch=True)
        )
        self.edit_hint = kit.label(role="muted")
        self.editor.body.addWidget(self.edit_hint)
        lay.addWidget(self.editor)

        self.more = kit.More(tip="Which build the names are keyed to")
        self.build = kit.label(role="mono", selectable=True)
        self.build.setToolTip(BUILD_TIP)
        self.more.body.addWidget(self.build)
        lay.addWidget(self.more)

        self.empty = NoScene(studio)
        self.pages = kit.Pages(page, self.empty)
        self.body.addWidget(self.pages)

    # acting

    def _filter(self, text: str) -> None:
        self.studio.act("filter clips", lambda: setattr(self.ws, "clip_filter", text))()

    def _play(self, key: object) -> None:
        if not isinstance(key, tuple):
            return
        what, n = key
        if what == "clip":
            self.studio.act(f"play clip {n}", lambda: self.ws.play_source(n))()
        else:
            self.studio.act(f"play anim {n}", lambda: self.ws.play_slot(n))()

    def _stepped(self, row: int, _col: int, before: int, _before_col: int) -> None:
        """A new current row plays: the arrow keys, or the press of a click."""
        it = self.table.item(row, 0)
        if row != before and it is not None:
            self._stepped_to = it.data(Qt.ItemDataRole.UserRole)
            self._play(self._stepped_to)

    def _clicked(self, key: object) -> None:
        """A click replays from the start, unless its own press just played the row."""
        if key != self._stepped_to:
            self._play(key)
        self._stepped_to = None

    def _apply(self) -> None:
        def run() -> None:
            self.ws.name_buf, self.ws.label_buf = self.name.text(), self.label.text()
            self.ws.label()

        self.studio.act("name clip", run)()

    def _apply_next(self) -> None:
        """Apply, then the next row plays, the name box ready for it."""
        name, label = self.name.text().strip(), self.label.text()
        self._apply()
        c = None if self.ws.manifest is None else self.ws.manifest.clips.get(name)
        if c is None or c.label != label:
            return  # refused: stay on the clip
        keys = [r.key for r in self._rows]
        if self._picked in keys and keys.index(self._picked) + 1 < len(keys):
            self._play(keys[keys.index(self._picked) + 1])
        self.name.setText(self.ws.name_buf)
        self.label.setText(self.ws.label_buf)
        self.name.setFocus()
        self.name.selectAll()

    def _gizmo(self, on: bool) -> None:
        self.ws.turn.shown = on

    def _new_move(self) -> None:
        before = self.ws.manifest
        self.ws.new_move()
        if self.ws.manifest is not before:
            self.ws.focus("Moves")

    def _place(self) -> None:
        def run() -> None:
            self.ws.name_buf = self.name.text()
            self.ws.place_clip(self.anim.value())

        self.studio.act("place clip", run)()

    # showing

    def sync(self) -> None:
        ws, sc = self.ws, self.ws.scene
        has = sc is not None and bool(sc.clips)
        self.pages.show_page(has)
        if sc is None or not has:
            if sc is not None:
                self.empty.say("No clips", "This PAC has no animation in it.")
            return
        vocab = ws.vocabulary()
        self._rows = ws.source_rows()
        donor = ws.browser() is not None and any(r.id is not None for r in self._rows)
        self._picked = self._editing()
        if donor:
            ids = [r for r in self._rows if r.id is not None]
            streams = len({r.stream for r in ids})
            named = sum(bool(r.name) for r in ids)
            self.count.setText(f"{len(ids)} clips in {streams} streams, {named} named")
        else:
            self.count.setText(f"{len(sc.clips)} clips, {sum(c.loop for c in sc.clips)} looping")
        self.build.setText(f"build {vocab.build}" if vocab.build else "build not identified")
        self._coverage(vocab)
        self.table.setColumnHidden(0, not donor)
        self._table(vocab)
        self._editor(vocab)
        findings.take(ws, {V.CLIP_NAME: self.name})

    def _editing(self) -> tuple[str, int] | None:
        """The row picked for naming: the one in the picked anim, else a clip in none."""
        ws = self.ws
        if ws.edit_slot is not None:
            return next((r.key for r in self._rows if r.entry == ws.edit_slot), None)
        if ws.edit_clip is not None and any(r.id == ws.edit_clip for r in self._rows):
            return "clip", ws.edit_clip
        return None

    def _playing(self) -> tuple[str, int] | None:
        vp = self.ws.vp
        clip = None if vp is None else vp.clip
        if clip is None:
            return None
        cid = self.ws.playing_clip()
        if cid is not None and any(r.id == cid for r in self._rows):
            return "clip", cid
        return "anim", clip.slot

    def _coverage(self, vocab: Vocabulary) -> None:
        cov = vocab.coverage
        n = cov.counts()
        self.kind_row.setVisible(cov.has_source)
        for kind, (sw, text) in self.kinds.items():
            text.setText(f"{n[kind]} {clips.KIND_WORDS[kind][n[kind] != 1]}")
            text.set_level("warning" if kind == clips.FILLER and n[kind] else None)
            text.setVisible(bool(n[kind]))
            sw.setVisible(bool(n[kind]))
        self.filler.setText(FILLER_WARNING.format(n=n[clips.FILLER]))
        self.filler.setVisible(cov.has_source and n[clips.FILLER] > 0)
        out = [r.id for r in self._rows if r.id is not None and r.entry is None]
        self.dropped.setText(
            f"{len(out)} of the original's clips are in no anim: " + ", ".join(map(str, out))
        )
        self.dropped.setVisible(bool(out))
        notes = [*vocab.notes, *([self.ws.browser_note] if self.ws.browser_note else [])]
        self.notes.setText("\n".join(f"• {n}" for n in notes))
        self.notes.setVisible(bool(notes))
        bad = vocab.suspect
        self.health.setText(f"{len(bad)} name(s) do not match this build")
        self.health.setVisible(bool(bad))
        self.suspect.setVisible(bool(bad))
        self.suspect.set_items(
            [
                kit.Item(
                    f"{t.name} (anim {t.slot}): {STATUS[t.status]}", t.slot, t.message, "warning"
                )
                for t in bad
            ]
        )

    def _table(self, vocab: Vocabulary) -> None:
        ws, vp = self.ws, self.ws.vp
        kit.put(self.filter, ws.clip_filter)
        needle = ws.clip_filter.strip().lower()
        speed = DEFAULT_SPEED if vp is None or vp.actor is None else vp.playback.speed
        rows, data, colors, tips, levels = [], [], [], [], []
        for r in self._rows:
            cov = None if r.entry is None else vocab.coverage.slots.get(r.entry)
            text = " ".join(
                [
                    "" if r.id is None else f"{r.id} stream {r.stream}",
                    f"anim {r.entry}" if r.entry is not None else "unplaced",
                    r.name or "unnamed",
                    kind_text(cov),
                    r.label,
                ]
            )
            if needle and needle not in text.lower():
                continue
            net = 0.0 if r.entry is None else ws.travel(r.entry)[0]
            rows.append(
                (
                    "" if r.id is None else str(r.id),
                    "·" if r.entry is None else str(r.entry),
                    f"{r.frames} loop" if r.loop else str(r.frames),
                    f"{net:.0f}" if net >= 1.0 else "",
                    r.name,
                )
            )
            data.append(r.key)
            colors.append(None if cov is None else COVERAGE[cov.kind])
            tips.append(row_tip(r, cov, speed))
            levels.append(level_of(cov))
        playing = self._playing()
        with QSignalBlocker(self.table):
            self.table.set_rows(rows, data, colors=colors, tips=tips, levels=levels)
            if playing is None:
                self.table.clearSelection()
            else:
                self.table.select_data(playing)

    def _editor(self, vocab: Vocabulary) -> None:
        ws = self.ws
        row = next((r for r in self._rows if r.key == self._picked), None)
        editing = ws.manifest is not None and row is not None
        for w in (self.slot, self.why, self.name, self.label, self.apply, self.slot_kind):
            w.setVisible(editing)
        placeable = editing and row is not None and row.id is not None
        self.anim.setVisible(placeable)
        self.place.setVisible(placeable)
        self.slot_swatch.setVisible(False)
        if ws.manifest is None:
            self.edit_hint.setText("Names and labels live in a manifest: open a port manifest.")
        elif row is None:
            self.edit_hint.setText("Click a clip above to play it and name it.")
        elif row.entry is None:
            self.edit_hint.setText("Place it in an anim to play it in the game.")
        self.edit_hint.setVisible(not editing or (row is not None and row.entry is None))
        if not editing or row is None:
            return
        cov = None if row.entry is None else vocab.coverage.slots.get(row.entry)
        head = f"Clip {row.id}" if row.id is not None else f"Anim {row.entry}"
        where = (
            ""
            if row.id is None
            else f"  stream {row.stream}, "
            + (f"anim {row.entry}" if row.entry is not None else "no anim")
        )
        self.slot.setText(head + where)
        self.slot_swatch.set(None if cov is None else COVERAGE[cov.kind])
        self.slot_kind.setText(kind_text(cov))
        self.why.setText(cov.why() if cov else UNPLACED if row.entry is None else "Kind unknown.")
        self.why.set_level(level_of(cov))
        self.in_game.setEnabled(row.entry is not None)
        self._turn()
        br = ws.browser()
        if placeable and br is not None:
            self.anim.setMaximum(max(br.layout().capacity - 1, 0))
            kit.put(self.anim, row.entry if row.entry is not None else 0)
        if row.key != self._loaded:  # a new pick loads its name; typing stays
            self._loaded = row.key
            kit.put(self.name, ws.name_buf)
            kit.put(self.label, ws.label_buf)

    def _turn(self) -> None:
        """The clip on screen's turn: the one set here, else its own body's."""
        got = self.ws.clip_turn()
        for w in (self.turn, self.own_turn, self.gizmo, self.turn_label):
            w.setVisible(got is not None)
        if got is None:
            return
        deg, given = got
        kit.put(self.turn, deg)
        kit.put(self.gizmo, self.ws.turn.shown)
        self.own_turn.setEnabled(given)
        self.turn_label.setText("Turn" if given else "Turn (own)")


def kind_text(cov: SlotCoverage | None) -> str:
    return "" if cov is None else clips.KIND_WORDS[cov.kind][0]


def level_of(cov: SlotCoverage | None) -> Level | None:
    """An idle copy is the loud one: a forced idle looks like an override that never fired."""
    return "warning" if cov is not None and cov.kind == clips.FILLER else None


def row_tip(r: SourceClip, cov: SlotCoverage | None, speed: float) -> str:
    lines = []
    if r.id is not None:
        lines.append(f"MHP3rd clip {r.id}: stream {r.stream}, slot {r.id % 100}")
    lines.append(cov.why() if cov else UNPLACED if r.entry is None else "Kind unknown")
    lines.append(f"{wall_clock(r.frames, speed):.2f} s at speed {speed:.2f}")
    if r.label:
        lines.append(r.label)
    return "\n".join(lines)
