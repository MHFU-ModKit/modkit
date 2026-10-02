# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Action dock: what the base monster's action expects, against the clip on screen.

A ported monster has no AI of its own: the engine runs the one MHFU overlay `port.host_species`
names, so every `(main, sub)` here is that base monster's, with the port's animation on it."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from functools import partial
from typing import TYPE_CHECKING

from mhfu import files
from mhfu.em.intel import PairIntel, SpeciesIntel
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mhfu_studio.monster import species
from mhfu_studio.monster.align import Alignment
from mhfu_studio.monster.panels import graph
from mhfu_studio.monster.panels.widgets import NoScene
from mhfu_studio.shell.findings import worst
from mhfu_studio.shell.text import plain
from mhfu_studio.ui import kit

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

#: wider than this, the action table sits beside the rest; narrower, under it
SIDE_BY_SIDE = 760
#: the background survey is looked at this often (ms) until it is done
POLL = 250


class Strip(QWidget):
    """A row (or a column) of buttons and labels, rebuilt only when its `key` changes."""

    def __init__(self, column: bool = False) -> None:
        super().__init__()
        self.lay = QVBoxLayout(self) if column else QHBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self._key: Hashable = object()

    def fill(self, key: Hashable, build: Callable[[], Sequence[QWidget]]) -> None:
        if key == self._key:
            return
        self._key = key
        while (it := self.lay.takeAt(0)) is not None:
            if (w := it.widget()) is not None:
                w.deleteLater()
        for w in build():
            self.lay.addWidget(w)
        if isinstance(self.lay, QHBoxLayout):
            self.lay.addStretch(1)


def species_choices(ws: MonsterWorkspace) -> list[int]:
    return species.available(ws.intel_root) if ws.intel_root else list(files.EM_SPECIES)


def pair_row(p: PairIntel) -> list[str]:
    """action, ends, checks, effects, then: "stays" never ends itself, "?" is not known."""
    gates = ", ".join(f"{f:g}" for f in p.tested_frames[:4]) or "·"
    nxt = " ".join(f"({m},{s})" for m, s in p.successors[:4])
    after = "?" if p.next is None else "stays" if not p.next else nxt
    if len(p.successors) > 4:
        after += " +"
    ends = graph.ends_text(p) or "?"
    return [f"({p.main},{p.sub})", ends, gates, str(len(p.effects)) if p.effects else "", after]


class ActionPanel(kit.Panel):
    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__(scroll=False)
        self.ws, self.studio = ws, studio
        act = studio.act
        self._rows: tuple[object, ...] | None = None

        # the base monster
        self.host_note = kit.label(role="muted")
        self.browsing = kit.Alert()
        self.to_view = kit.button(
            "Show it beside yours…",
            tip="Opens View, where Base monster beside stands its model next to yours, playing"
            " the anim it plays for the picked action",
            on=act("view", lambda: ws.focus("View")),
            icon="ph.arrow-square-out",
        )
        host = kit.Section("Base monster", tip="The monster whose code runs your port")
        for w in (self.host_note, self.browsing, kit.row(self.to_view, stretch=True)):
            host.body.addWidget(w)
        self.no_intel = kit.label(role="muted")

        # the port's moves
        self.no_moves = kit.label(
            "No moves yet: pick an action on the right to try it with the clip on screen, then"
            " use the clip for it when it fits.",
            role="hint",
        )
        self.moves = kit.Table(
            ["Move", "Action", "Clip"],
            tip="Your moves; click one to play its clip and read its action",
        )
        self.moves.picked.connect(lambda name: act("move", lambda: self._play_move(name))())
        moves = kit.Section(
            "Your moves",
            tip="The manifest's [moves]: each plays one of your clips when the base monster"
            " enters an action",
        )
        moves.body.addWidget(self.no_moves)
        moves.body.addWidget(self.moves)

        # the alignment
        self.back = kit.button(
            "All actions",
            tip="Lets go of this action, back to the list of every action",
            on=act("all actions", ws.clear_pair),
            icon="ph.arrow-left",
        )
        self.title = kit.label(role="title", wrap=False)
        self.clip = kit.label(role="muted")
        self.headline = kit.label()
        self.host_plays = Strip()
        self.host_none = kit.Alert()
        self.hits = Strip(column=True)
        self.hits_text = kit.label(selectable=True)
        self.then = Strip(column=True)
        self.declared = kit.label(role="muted")
        self.in_moves = kit.button(
            "Show in Moves",
            tip="Brings the Moves graph forward with this action picked, to read where it leads",
            on=act("show in moves", self._show_in_moves),
            icon="ph.flow-arrow",
        )
        self.then_warn = kit.Alert()
        self.show_findings = kit.check(
            "Show the findings",
            tip="Every check on this action against the clip, with what it means",
            on=lambda on: self._fold(on),
        )
        self.dots = kit.Alert()
        self.findings = QWidget()
        self.findings_lay = QVBoxLayout(self.findings)
        self.findings_lay.setContentsMargins(0, 0, 0, 0)
        self._findings_key: object = None
        self._findings_pair: object = None

        # binding
        self.bind_name = kit.text_field(
            tip="The name the move gets in [moves]; empty gives move_<main>_<sub>",
            placeholder="move name",
        )
        self.bind = kit.button(
            "Use this clip for this action",
            tip="Writes this action and the clip on screen into the manifest as a move: your port"
            " plays that clip whenever the base monster enters this action",
            on=act("bind", lambda: ws.bind_move(self.bind_name.text())),
            role="primary",
            icon="ph.link",
        )
        self.bind_note = kit.label(role="muted")
        self.bind_row = kit.row(self.bind_name, self.bind, stretch=True)
        #: the (pair, move) the name field was last filled for
        self._bind_for: object = None

        align = kit.Section("This action", tip="The picked action against the clip on screen")
        for w in (
            kit.row(self.back, self.title, stretch=True),
            self.clip,
            self.headline,
            self.host_plays,
            self.host_none,
            self.hits_text,
            self.hits,
            self.then,
            self.then_warn,
            kit.row(self.declared, self.in_moves, stretch=True),
            self.show_findings,
            self.dots,
            self.findings,
            self.bind_row,
            self.bind_note,
        ):
            align.body.addWidget(w)
        self.align = align

        # expert: another monster's actions, the handler, effects tied to no action
        self.more = kit.More(
            tip="Another monster's actions to compare, the code's address, and effects no action"
            " is known to fire"
        )
        self.species = kit.choice(
            [],
            tip="Whose actions to read. Your base monster's are the ones the game runs for your"
            " port; another one is for comparing, not for moves.",
            on=lambda sp: act("browse", lambda: ws.browse_species(int(sp)))(),
        )
        self._species_key: object = None
        self._choices = species_choices(ws)
        self.compare = kit.check(
            "Compare the monsters",
            tip="Every MHFU monster's actions side by side: which base monster gives your port"
            " the most to work with",
            on=lambda on: self._compare(on),
        )
        self.hosts_note = kit.label(
            "Every monster runs the same action tick: 8 groups of actions, each fanning out."
            " What differs is how much you get. Timed actions wait for clip frames your"
            " animation has to hit; timer ones end on a countdown instead of your clip.",
            role="hint",
        )
        self.hosts_wait = kit.label(
            "Reading every monster's actions (once, then kept)…", role="muted"
        )
        self.hosts = kit.Table(
            ["Monster", "Actions", "Timed", "Timer", "Effects", "Hidden"],
            tip="Click a monster to read its actions. Hidden: groups whose actions the studio"
            " cannot list, which is not the same as none.",
        )
        self.hosts.picked.connect(lambda sp: act("browse", lambda: ws.browse_species(sp))())
        self.handler = kit.label(role="mono", selectable=True)
        self.handler.setToolTip("Where the action's code is in the game, and the anims it names")
        self.show_effects = kit.check(
            "Effects of no action",
            tip="The base monster's timed effects that no action is known to fire, listed for"
            " the monster, where their timing at least is readable",
            on=lambda on: self.effects_box.setVisible(on),
        )
        self.effects_note = kit.label(
            "No action's code calls the switch they hang off, so which action fires them cannot"
            " be told from the code. Joints are the base monster's.",
            role="hint",
        )
        self.effects = kit.Table(
            ["Effect", "Joint", "Frame"],
            tip="Hover a row: what that joint is on your skeleton",
        )
        self.effects_box = QWidget()
        lay = QVBoxLayout(self.effects_box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.effects_note)
        lay.addWidget(self.effects)
        self.effects_box.setVisible(False)
        browse = kit.Form()
        browse.row("Actions of", self.species)
        for w in (
            browse,
            self.compare,
            self.hosts_note,
            self.hosts_wait,
            self.hosts,
            self.handler,
            self.show_effects,
            self.effects_box,
        ):
            self.more.body.addWidget(w)

        # every action
        self.filter = kit.text_field(
            tip="Shows only the actions whose row has this text: an action like 1,4, a frame,"
            " where it goes, or 'timer'",
            placeholder="filter actions",
        )
        self.filter.textChanged.connect(lambda _t: self.sync())
        self.count = kit.label(role="muted", wrap=False)
        self.pairs = kit.Table(
            ["Action", "Ends", "Checks", "Effects", "Then"],
            tip="Every action the base monster's code runs. Click one to try it with the clip on"
            " screen. Ends: what finishes it (the clip, or a timer). Checks: clip frames its code"
            " waits for. Then: where it goes next.",
        )
        self.pairs.picked.connect(lambda pr: act("pair", lambda: ws.select_pair(*pr))())
        table = QWidget()
        tl = QVBoxLayout(table)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(kit.row(self.filter, self.count))
        tl.addWidget(self.pairs, 1)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        for w in (host, self.no_intel, moves, align, self.more):
            ll.addWidget(w)
        ll.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(left)
        self.split = QSplitter()
        self.split.addWidget(scroll)
        self.split.addWidget(table)
        self.split.setChildrenCollapsible(False)
        self.table_side = table

        self.no_scene = NoScene(studio)
        self.no_scene.say(
            "No port manifest",
            "An action plays one of your clips when the base monster enters it, so it needs a"
            " port manifest. Open one (ports/<name>.toml).",
        )
        self.pages = kit.Pages(self.split, self.no_scene)
        self.body.addWidget(self.pages)
        self.poll = QTimer(self)
        self.poll.setInterval(POLL)
        self.poll.timeout.connect(self.sync)
        self._compare(False)
        self._fold(False)

    # ---- actions ----------------------------------------------------------------------- #

    def _compare(self, on: bool) -> None:
        for w in (self.hosts_note, self.hosts_wait, self.hosts):
            w.setVisible(on)
        if on:
            self.sync()

    def _fold(self, on: bool) -> None:
        """The findings open, or folded to one line: the worst one."""
        self.findings.setVisible(on)
        self.dots.setVisible(not on and not self.show_findings.isHidden())

    def _play_move(self, name: str) -> None:
        m = self.ws.manifest
        if m is None or name not in m.moves:
            return
        mv = m.moves[name]
        if mv.clip in m.clips:
            self.ws.play_slot(m.clips[mv.clip].slot)
        self.ws.select_pair(mv.main, mv.sub, name)

    def _show_in_moves(self) -> None:
        self.ws.graph.picked = self.ws.pair
        self.ws.focus("Moves")

    def resizeEvent(self, e: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(e)
        wide = e.size().width() >= SIDE_BY_SIDE
        o = Qt.Orientation.Horizontal if wide else Qt.Orientation.Vertical
        if self.split.orientation() != o:
            self.split.setOrientation(o)

    # ---- sync -------------------------------------------------------------------------- #

    def sync(self) -> None:
        ws = self.ws
        m = ws.manifest
        self.pages.show_page(m is not None)
        if m is None:
            self.poll.stop()
            return
        self._sync_host()
        intel = ws.intel
        self.no_intel.setVisible(intel is None)
        self.no_intel.setText(ws.intel_gap("action", ws.browsing_species))
        self.table_side.setVisible(intel is not None)
        al = ws.alignment if intel is not None else None
        self.align.setVisible(al is not None)
        self._sync_moves()
        if intel is None:
            return
        if al is not None:
            self._sync_alignment(al)
        self._sync_pairs(intel)

    def _sync_host(self) -> None:
        ws = self.ws
        ids, cur, hs = self._choices, ws.browsing_species, ws.host_species
        key = (tuple(ids), hs)
        if key != self._species_key:
            self._species_key = key
            self.species.blockSignals(True)
            self.species.clear()
            for s in ids:
                self.species.addItem(species.label(s) + (", base" if s == hs else ""), s)
            self.species.blockSignals(False)
        kit.put(self.species, cur)
        self.host_note.setText(f"{species.label(hs)}: the monster whose code runs your port")
        self.browsing.setVisible(not ws.browsing_the_host)
        self.browsing.setText(
            f"Showing the actions of {species.label(cur)}: the game runs {species.label(hs)}'s"
            " for your port, not these. This compares; changing the base monster is a rebuild."
        )
        hosts = ws.host_options() if self.compare.isChecked() else None
        waiting = self.compare.isChecked() and hosts is None
        self.hosts_wait.setVisible(waiting)
        self.hosts.setVisible(self.compare.isChecked() and not waiting)
        if waiting:
            self.poll.start()
        else:
            self.poll.stop()
        if hosts is not None:
            self.hosts.set_rows(
                [
                    [
                        species.label(h.species) + (", base" if h.species == hs else ""),
                        str(h.pairs),
                        str(h.timed),
                        str(h.budget),
                        str(h.effects),
                        f"{h.opaque_mains}/8",
                    ]
                    for h in hosts
                ],
                [h.species for h in hosts],
            )
            self.hosts.fit()
            self.hosts.select_data(cur)

    def _sync_moves(self) -> None:
        m = self.ws.manifest
        assert m is not None
        names = sorted(m.moves)
        self.no_moves.setVisible(not names)
        self.moves.setVisible(bool(names))
        self.moves.set_rows(
            [[n, f"({m.moves[n].main},{m.moves[n].sub})", m.moves[n].clip or "·"] for n in names],
            names,
        )
        self.moves.fit(5)
        self.moves.select_data(self.ws.move)

    def _sync_pairs(self, intel: SpeciesIntel) -> None:
        needle = self.filter.text().strip().lower()
        key = (id(intel), needle)
        if key != self._rows:
            self._rows = key
            shown = [p for p in intel if not needle or needle in " ".join(pair_row(p)).lower()]
            self.pairs.set_rows(
                [pair_row(p) for p in shown],
                [(p.main, p.sub) for p in shown],
                tips=["\n".join(map(str, p.next or ())) for p in shown],
                levels=["warning" if p.budget.gated else None for p in shown],
            )
            self.count.setText(
                f"{len(shown)} of {len(intel)} actions of {species.label(self.ws.browsing_species)}"
            )
        self.pairs.select_data(self.ws.pair)

    def _sync_alignment(self, al: Alignment) -> None:
        ws = self.ws
        self.title.setText(f"{al.move}  →  ({al.main},{al.sub})")
        self.clip.setText(
            f"clip {al.clip}" + (f", {al.frames} frames" if al.frames else "") if al.clip else ""
        )
        self.headline.setText(plain(al.headline))
        self._host_clips(al)
        self._hits(al)
        self._then(al)
        self._findings(al)
        self._bind(al)
        fx = [] if ws.intel is None else ws.intel.framed_effects()
        self.show_effects.setVisible(bool(fx))
        self.show_effects.setText(f"Effects of no action ({len(fx)})")
        if fx and self.effects_box.isVisible():
            rig = ws.port_rig()
            self.effects.set_rows(
                [[str(e.id), "?" if e.bone is None else str(e.bone), str(e.frame)] for e in fx],
                tips=[
                    "" if rig is None or e.bone is None else f"on your rig: {rig.describe(e.bone)}"
                    for e in fx
                ],
            )
            self.effects.fit(6)

    def _host_clips(self, al: Alignment) -> None:
        """Which anim the base monster plays for this action. An anim its pack lacks says so
        rather than falling through to another: the wrong animal doing the wrong thing."""
        ws, p, act = self.ws, al.pair, self.studio.act
        a1s = () if p is None else tuple(p.a1)
        table = ws.host_clip_table() if ws.show_host and a1s else {}
        name = species.label(ws.browsing_species)

        def build() -> list[QWidget]:
            out: list[QWidget] = [
                kit.label("The base monster plays anim", role="muted", wrap=False)
            ]
            for a1 in a1s:
                if not ws.show_host:
                    out.append(kit.label(str(a1), wrap=False))
                elif a1 not in table:
                    lb = kit.label(f"{a1}?", role="muted", wrap=False)
                    lb.setToolTip(
                        f"The code names anim {a1} and {name}'s pack has no such anim: either"
                        " the action is never reached, or its clip comes from somewhere the"
                        " studio cannot see."
                    )
                    out.append(lb)
                else:
                    frames, loop = table[a1]
                    b = kit.button(
                        f"{a1}" + (" ●" if ws.host_clip == a1 else ""),
                        tip=f"Plays the base monster's anim {a1} ({frames} frames"
                        + (", loops" if loop else "")
                        + f"). Which of the {len(a1s)} runs depends on the game's state.",
                        on=act("host clip", partial(ws.play_host_clip, a1)),
                    )
                    out.append(b)
            return out

        self.host_plays.setVisible(bool(a1s))
        self.host_plays.fill((a1s, ws.show_host, tuple(table), ws.host_clip), build)
        none = ws.show_host and bool(table) and not any(a in table for a in a1s)
        self.host_none.setVisible(none)
        self.host_none.setText(
            f"None of this action's anims is in {name}'s pack, so the base monster beside yours"
            " shows its default pose, not this action."
        )

    def _hits(self, al: Alignment) -> None:
        """What this action hits with: the attack records its code spawns, and their sets."""
        ws, p = self.ws, al.pair
        host = ws.host_attacks()
        recs = [] if host is None or p is None else host.records_for(p.attack_ids, ws.host_species)
        shapes = []
        if host is None or p is None or not ws.browsing_the_host:
            text = ""
        elif not p.attack_ids:
            n = p.attack_sites_computed
            more = f" ({n} worked out while it runs)" if n else ""
            text = f"Hits with: nothing the code shows{more}."
        elif not recs:
            ids = ",".join(map(str, p.attack_ids))
            text = (
                f"Hits with attack {ids}, which cannot be matched for"
                f" {species.label(ws.host_species)}: its id offset is unknown."
            )
        else:
            bits = []
            for a in recs:
                st = host.set(a.volume)
                if st is not None and st.describe():
                    shapes.append(f"hit group {a.volume}: {st.describe()}")
                bits.append(
                    f"attack {a.id} (power {a.power}, element 0x{a.element:02X}) using hit group"
                    f" {a.volume}"
                )
            text = "Hits with " + "; ".join(bits)
        self.hits_text.setText(plain(text))
        self.hits_text.setToolTip("\n".join(shapes))
        self.hits_text.setVisible(bool(text))
        sets = sorted({a.volume for a in recs}) if ws.browsing_the_host else []
        act = self.studio.act
        self.hits.setVisible(bool(sets))
        self.hits.fill(
            tuple(sets),
            lambda: [
                kit.button(
                    f"Edit hit group {s} in Hitboxes",
                    tip=f"Opens hit group {s} in Hitboxes: yours when you have it, else the"
                    " base monster's to copy",
                    on=act("edit set", partial(ws.edit_set, s)),
                    icon="ph.arrow-square-out",
                )
                for s in sets
            ],
        )

    def _then(self, al: Alignment) -> None:
        """Where the GAME goes after this action, beside what the move DECLARES."""
        ws, p, act = self.ws, al.pair, self.studio.act
        nxt = () if p is None or not p.next else tuple(p.next)

        def build() -> list[QWidget]:
            out: list[QWidget] = [kit.label("When it ends, the game goes to", role="muted")]
            for e in nxt:
                tgt = "/".join(f"({m},{s})" for m, s in e.to) or "?"
                b = kit.button(
                    tgt,
                    tip=plain(f"Picks {tgt}, the action this one goes to: {e}"),
                    on=act("then", partial(ws.select_pair, *e.to[0]) if e.to else lambda: None),
                )
                why = kit.label(f"when {e.reason}" if e.reason else "always", role="muted")
                row = QWidget()
                lay = QHBoxLayout(row)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.addWidget(b)
                lay.addWidget(why, 1)  # the reason takes the width, not a wrap
                out.append(row)
            return out

        self.then.setVisible(bool(nxt))
        self.then.fill(tuple(str(e) for e in nxt), build)
        holds = p is not None and p.next is not None and not p.next
        self.then_warn.setVisible(holds)
        if p is not None:
            self.then_warn.setText(
                f"({p.main},{p.sub}) never ends by itself: forced from a script, the monster"
                " stays in it. A move here needs `after =` in the manifest."
            )
        m = ws.manifest
        mv = None if m is None or not al.move else m.moves.get(al.move)
        text = ""
        if m is not None and mv is not None and mv.after:
            nm = m.moves.get(mv.after)
            text = f"your move goes on to {mv.after}" + (
                f" ({nm.main},{nm.sub})" if nm else " (no such move!)"
            )
        self.declared.setText(text)
        self.declared.setVisible(bool(text))

    def _findings(self, al: Alignment) -> None:
        """Folded to the worst one unless something is wrong; codes in the tips."""
        key = (al.move, al.main, al.sub, al.clip, tuple(al.findings))
        if key != self._findings_key:
            self._findings_key = key
            while (it := self.findings_lay.takeAt(0)) is not None:
                if (w := it.widget()) is not None:
                    w.deleteLater()
            marks = {"error": "x", "warning": "!", "info": "·"}
            for f in al.findings:
                lb = kit.Alert(plain(f"{marks.get(f.level, '·')}  {f.message}"), f.level)
                lb.setToolTip(f.code)
                self.findings_lay.addWidget(lb)
            n_err, n_warn = len(al.errors), len(al.warnings)
            counts = ", ".join(
                f"{n} {word}{'' if n == 1 else 's'}"
                for n, word in ((n_err, "error"), (n_warn, "warning"))
                if n
            )
            self.show_findings.setText(
                f"Show the {len(al.findings)} finding{'' if len(al.findings) == 1 else 's'}"
                + (f": {counts}" if counts else "")
            )
            if (al.main, al.sub) != self._findings_pair:  # a new action opens on its errors
                self._findings_pair = (al.main, al.sub)
                kit.put(self.show_findings, bool(al.errors))
            bad = [f for f in al.findings if f.level != "info"]
            first = max(bad, key=lambda f: f.level == "error", default=None)
            self.dots.setText("nothing to flag" if first is None else plain(first.message))
            self.dots.setToolTip(" · ".join(f.code for f in bad) or "no warnings or errors")
            self.dots.set_level(worst(al.findings) if bad else "info")
        p = al.pair
        a1 = "" if p is None else ",".join(map(str, p.a1)) or "-"
        self.handler.setText(
            f"code at 0x{p.handler:08X}   anims {a1}" if p is not None and p.handler else ""
        )
        self.handler.setVisible(bool(self.handler.text()))
        self.show_findings.setVisible(bool(al.findings))
        self._fold(bool(al.findings) and self.show_findings.isChecked())

    def _bind(self, al: Alignment) -> None:
        """Refused while browsing another monster, and for an action the census measured as
        never entered (`allow_unentered` belongs in the file, beside the reason)."""
        ws = self.ws
        can = ws.label_session is not None
        note, ok = "", False
        if not can:
            pass
        elif not ws.browsing_the_host:
            note = (
                f"Cannot use a clip here: these are {species.label(ws.browsing_species)}'s"
                f" actions, and the game runs {species.label(ws.host_species)}'s for your port."
            )
        elif any(f.code == "NEVER_ENTERED" for f in al.errors):
            note = "Cannot use a clip here: the game was watched and never enters this action."
        else:
            ok = True
            clip = f"clip {al.clip}" if al.clip else "no clip yet (pick one in Clips)"
            note = (
                f"Saves a move: ({al.main},{al.sub}) plays {clip}. A move of the same name keeps"
                " its other settings."
            )
            self.bind_name.setPlaceholderText(f"move_{al.main}_{al.sub}")
            if (ws.pair, ws.move) != self._bind_for:
                self._bind_for = (ws.pair, ws.move)
                self.bind_name.setText(ws.move or "")
        self.bind_row.setVisible(ok)
        self.bind_note.setVisible(bool(note))
        self.bind_note.setText(note)
