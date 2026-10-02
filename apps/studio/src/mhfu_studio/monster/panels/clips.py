# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Clips panel: every anim, what is really in it (an idle copy plays idle, and forcing it
looks exactly like a failed override), and the names it carries, keyed to this build."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QVBoxLayout, QWidget

from mhfu_studio.monster import clips
from mhfu_studio.monster.clips import SlotCoverage, Vocabulary
from mhfu_studio.monster.render.playback import DEFAULT_SPEED, wall_clock
from mhfu_studio.shell.findings import Level
from mhfu_studio.ui import kit

from .common import COVERAGE
from .widgets import NoScene

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

KINDS = (clips.CARRIED, clips.FILLER, clips.HOST, clips.ALTERED)
#: a kind in words: one, many
KIND_WORDS = {
    clips.CARRIED: ("own clip", "own clips"),
    clips.FILLER: ("idle copy", "idle copies"),
    clips.HOST: ("base monster's", "base monster's"),
    clips.ALTERED: ("changed", "changed"),
    clips.UNKNOWN: ("unknown", "unknown"),
}
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


def _page() -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    return w, lay


class ClipsPanel(kit.Panel):
    """Every anim: play one, see what it really holds, and name it."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__(scroll=False)
        self.ws, self.studio = ws, studio
        self._slot: int | None = None
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
            "The base monster has no anim of that number, so the builder had nowhere to put"
            " these clips of the original: they are not in this build at all."
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
            tip="Shows only the clips whose anim #, name, kind or label contains this text",
            placeholder="Filter: anim #, name, kind or label",
        )
        self.filter.textChanged.connect(self._filter)
        lay.addWidget(self.filter)
        self.table = kit.Table(
            ["Anim #", "Kind", "Frames", "Loop", "Travel", "Name"],
            tip="Every anim in the build. Click one to play it from the start. The anim # is"
            " the number a script passes to force it; Travel is how far the clip carries the"
            " body.",
            swatch_column=1,
        )
        self.table.picked.connect(self._play)
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
            " by it; renaming follows them.",
            placeholder="clip_07",
        )
        self.label = kit.text_field(
            tip="What the clip shows, in your words: 'tail sweep', 'roar'",
            placeholder="tail sweep",
        )
        for field in (self.name, self.label):
            field.returnPressed.connect(self._apply)
        form.row("Name", self.name)
        form.row("Shows", self.label)
        self.editor.body.addWidget(form)
        self.apply = kit.button(
            "Apply",
            tip="Writes the name and label into the manifest, keyed to this build",
            on=self._apply,
            icon="ph.check",
        )
        self.editor.body.addWidget(kit.row(self.apply, stretch=True))
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

    def _filter(self, text: str) -> None:
        self.studio.act("filter clips", lambda: setattr(self.ws, "clip_filter", text))()

    def _play(self, slot: object) -> None:
        if isinstance(slot, int):
            self.studio.act(f"play anim {slot}", lambda: self.ws.play_slot(slot))()

    def _apply(self) -> None:
        def run() -> None:
            self.ws.name_buf, self.ws.label_buf = self.name.text(), self.label.text()
            self.ws.label()

        self.studio.act("name clip", run)()

    def sync(self) -> None:
        ws, sc = self.ws, self.ws.scene
        has = sc is not None and bool(sc.clips)
        self.pages.show_page(has)
        if sc is None or not has:
            if sc is not None:
                self.empty.say("No clips", "This PAC has no animation in it.")
            return
        vocab = ws.vocabulary()
        self.count.setText(f"{len(sc.clips)} clips, {sum(c.loop for c in sc.clips)} looping")
        self.build.setText(f"build {vocab.build}" if vocab.build else "build not identified")
        self._coverage(vocab)
        self._table(vocab)
        self._editor(vocab)

    def _coverage(self, vocab: Vocabulary) -> None:
        cov = vocab.coverage
        n = cov.counts()
        self.kind_row.setVisible(cov.has_source)
        for kind, (sw, text) in self.kinds.items():
            text.setText(f"{n[kind]} {KIND_WORDS[kind][n[kind] != 1]}")
            text.set_level("warning" if kind == clips.FILLER and n[kind] else None)
            text.setVisible(bool(n[kind]))
            sw.setVisible(bool(n[kind]))
        self.filler.setText(FILLER_WARNING.format(n=n[clips.FILLER]))
        self.filler.setVisible(cov.has_source and n[clips.FILLER] > 0)
        self.dropped.setText(
            f"{len(cov.dropped)} of the original's clips are not in this build: "
            + ", ".join(map(str, sorted(cov.dropped)))
        )
        self.dropped.setVisible(bool(cov.dropped))
        self.notes.setText("\n".join(f"• {n}" for n in vocab.notes))
        self.notes.setVisible(bool(vocab.notes))
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
        ws, sc, vp = self.ws, self.ws.scene, self.ws.vp
        assert sc is not None
        kit.put(self.filter, ws.clip_filter)
        needle = ws.clip_filter.strip().lower()
        speed = DEFAULT_SPEED if vp is None or vp.actor is None else vp.playback.speed
        rows, data, colors, tips, levels = [], [], [], [], []
        for c in sc.clips:
            cov = vocab.coverage.slots.get(c.slot)
            found = ws.manifest_clip(c.slot)
            label = found[1].label if found else ""
            text = f"{c.slot} {' '.join(c.names)} {kind_text(cov)} {label}"
            if needle and needle not in text.lower():
                continue
            net, _ = ws.travel(c.slot)
            rows.append(
                (
                    str(c.slot),
                    kind_text(cov),
                    str(c.frames),
                    "loop" if c.loop else "",
                    f"{net:.0f}" if net >= 1.0 else "",
                    c.name if c.names else "",
                )
            )
            data.append(c.slot)
            colors.append(None if cov is None else COVERAGE[cov.kind])
            tip = [cov.why() if cov else "Kind unknown"]
            tip.append(f"{wall_clock(c.frames, speed):.2f} s at speed {speed:.2f}")
            if label:
                tip.append(label)
            if not c.whole_rig:
                tip.append("Partial: it moves only some of the joints")
            tips.append("\n".join(tip))
            levels.append(level_of(cov))
        self.table.set_rows(rows, data, colors=colors, tips=tips, levels=levels)
        playing = None if vp is None or vp.clip is None else vp.clip.slot
        if playing is None:
            self.table.clearSelection()
        else:
            self.table.select_data(playing)

    def _editor(self, vocab: Vocabulary) -> None:
        ws = self.ws
        self.editor.setVisible(True)
        editing = ws.manifest is not None and ws.edit_slot is not None
        for w in (self.slot, self.why, self.name, self.label, self.apply):
            w.setVisible(editing)
        self.slot_swatch.setVisible(False)
        self.slot_kind.setVisible(editing)
        if ws.manifest is None:
            self.edit_hint.setText("Names and labels live in a manifest: open a port manifest.")
        elif ws.edit_slot is None:
            self.edit_hint.setText("Click a clip above to name it.")
        self.edit_hint.setVisible(not editing)
        if not editing or ws.edit_slot is None:
            return
        cov = vocab.coverage.slots.get(ws.edit_slot)
        self.slot.setText(f"Anim {ws.edit_slot}")
        self.slot_swatch.set(None if cov is None else COVERAGE[cov.kind])
        self.slot_kind.setText(kind_text(cov))
        self.why.setText(cov.why() if cov else "Its kind is unknown.")
        self.why.set_level(level_of(cov))
        if ws.edit_slot != self._slot:  # a new pick loads its name; typing is left alone
            self._slot = ws.edit_slot
            kit.put(self.name, ws.name_buf)
            kit.put(self.label, ws.label_buf)


def kind_text(cov: SlotCoverage | None) -> str:
    return "" if cov is None else KIND_WORDS[cov.kind][0]


def level_of(cov: SlotCoverage | None) -> Level | None:
    """An idle copy is the loud one: a forced idle looks like an override that never fired."""
    return "warning" if cov is not None and cov.kind == clips.FILLER else None
