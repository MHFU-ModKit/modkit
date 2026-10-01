# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Action dock: what the HOST action expects, against the clip on screen.

A ported monster has no AI of its own: the engine runs the one MHFU overlay `port.host_species`
names, so every `(main, sub)` here is that host's, with the port's animation painted on it."""

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
from mhfu_studio.monster.panels.widgets import NoScene, Pages, Table, alert, set_level
from mhfu_studio.shell.findings import Level, worst
from mhfu_studio.shell.widgets import plain
from mhfu_studio.ui import kit

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

#: wider than this, the pair table sits beside the rest; narrower, under it
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
    """pair, ends on, tests, fx, after: "holds" never ends itself, "?" has no hand-off intel."""
    gates = ", ".join(f"{f:g}" for f in p.tested_frames[:4]) or "·"
    nxt = " ".join(f"({m},{s})" for m, s in p.successors[:4])
    after = "?" if p.next is None else "holds" if not p.next else nxt
    if len(p.successors) > 4:
        after += " +"
    ends = "budget" if p.budget.gated else p.ends_on or "?"
    return [f"({p.main},{p.sub})", ends, gates, str(len(p.effects)) if p.effects else "", after]


class ActionPanel(kit.Panel):
    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__(scroll=False)
        self.ws, self.studio = ws, studio
        act = studio.act
        self._rows: tuple[object, ...] | None = None

        # the host
        self.species = kit.choice(
            [],
            tip="Whose action table to read. The host is the monster whose code runs your port;"
            " another one is for comparing, not binding.",
            on=lambda sp: act("browse", lambda: ws.browse_species(int(sp)))(),
        )
        self._species_key: object = None
        self._choices = species_choices(ws)
        self.host_note = kit.label(role="muted")
        self.browsing = alert(level="warning")
        self.show_host = kit.check(
            "Show the host beside the port",
            tip="Loads the host's own model and plays the clip its code picks for this action,"
            " beside your port. Both run at the same rate; each loops at its own end.",
            on=lambda on: act("show host", lambda: ws.set_show_host(on))(),
        )
        self.compare = kit.check(
            "Compare the hosts",
            tip="Every MHFU monster's action table side by side: which host gives your port the"
            " most to work with",
            on=lambda on: self._compare(on),
        )
        self.hosts_note = kit.label(
            "Every host runs the same action tick: 8 mains, each fanning into sub-states. What"
            " differs is how much you inherit. Timed pairs wait for fixed clip frames your"
            " animation has to hit; budget ones end on a frame countdown instead of your clip.",
            role="hint",
        )
        self.hosts_wait = kit.label("Surveying the hosts (once, then cached)…", role="muted")
        self.hosts = Table(
            ["host", "pairs", "timed", "budget", "fx", "hidden"],
            tip="Click a host to read its action table. Hidden: mains whose sub-states the scan"
            " cannot see, which is not the same as none.",
        )
        self.hosts.picked.connect(lambda sp: act("browse", lambda: ws.browse_species(sp))())
        host = kit.Section("Host", tip="The monster whose code runs this port")
        w: QWidget
        for w in (
            self.species,
            self.host_note,
            self.browsing,
            self.show_host,
            self.compare,
            self.hosts_note,
            self.hosts_wait,
            self.hosts,
        ):
            host.body.addWidget(w)
        self.no_intel = kit.label(role="muted")

        # the port's moves
        self.no_moves = kit.label(
            "[moves] is empty: pick a pair to try it against the clip on screen, and bind it"
            " when it fits.",
            role="hint",
        )
        self.moves = Table(
            ["move", "pair", "clip"],
            tip="The moves this port binds; click one to play its clip and read its pair",
        )
        self.moves.picked.connect(lambda name: act("move", lambda: self._play_move(name))())
        moves = kit.Section("Moves", tip="The [moves] of the manifest: a host pair and a clip each")
        moves.body.addWidget(self.no_moves)
        moves.body.addWidget(self.moves)

        # the alignment
        self.back = kit.button(
            "All pairs",
            tip="Lets go of this pair, back to the list of every pair",
            on=act("all pairs", ws.clear_pair),
            icon="ph.arrow-left",
        )
        self.title = kit.label(role="title", wrap=False)
        self.clip = kit.label(role="muted")
        self.headline = kit.label()
        self.host_plays = Strip()
        self.host_none = alert(level="warning")
        self.hits = Strip(column=True)
        self.hits_text = kit.label(selectable=True)
        self.then = Strip(column=True)
        self.declared = kit.label(role="muted")
        self.in_moves = kit.button(
            "Show in Moves",
            tip="Brings the Moves graph forward with this pair picked, to read where it leads",
            on=act("show in moves", self._show_in_moves),
            icon="ph.flow-arrow",
        )
        self.then_warn = alert(level="warning")
        self.show_findings = kit.check(
            "Show the findings",
            tip="Every check on this pair against the clip, with what it means",
            on=lambda on: self._fold(on),
        )
        self.dots = alert()
        self.findings = QWidget()
        self.findings_lay = QVBoxLayout(self.findings)
        self.findings_lay.setContentsMargins(0, 0, 0, 0)
        self._findings_key: object = None
        self._findings_pair: object = None
        self.handler = kit.label(role="mono", selectable=True)
        self.show_effects = kit.check(
            "Species-wide effects",
            tip="The host's framed effect spawns. No pair is known to fire them, so they are"
            " listed for the species, where their timing at least is legible.",
            on=lambda on: self.effects_box.setVisible(on),
        )
        self.effects_note = kit.label(
            "Not tied to any pair: they hang off a switch no pair's code calls, so which action"
            " fires them cannot be decided offline. Bones are the host's.",
            role="hint",
        )
        self.effects = Table(
            ["effect", "bone", "frame"],
            tip="Hover a row: what that bone is on your rig",
        )
        self.effects_box = QWidget()
        lay = QVBoxLayout(self.effects_box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.effects_note)
        lay.addWidget(self.effects)
        self.effects_box.setVisible(False)

        # binding
        self.bind_name = kit.text_field(
            tip="The name the move gets in [moves]; empty gives move_<main>_<sub>",
            placeholder="move name",
        )
        self.bind = kit.button(
            "Bind as move",
            tip="Writes this pair and the clip on screen into the manifest as a move: the port"
            " plays that clip whenever the host enters this pair",
            on=act("bind", lambda: ws.bind_move(self.bind_name.text())),
            role="primary",
            icon="ph.link",
        )
        self.bind_note = kit.label(role="muted")
        self.bind_row = kit.row(self.bind_name, self.bind, stretch=True)

        align = kit.Section("This pair", tip="The selected pair against the clip on screen")
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
            self.handler,
            self.bind_row,
            self.bind_note,
            self.show_effects,
            self.effects_box,
        ):
            align.body.addWidget(w)
        self.align = align

        # every pair
        self.filter = kit.text_field(
            tip="Shows only the pairs whose row has this text: a pair like 1,4, a frame, a"
            " hand-off, or 'budget'",
            placeholder="filter pairs",
        )
        self.filter.textChanged.connect(lambda _t: self.sync())
        self.count = kit.label(role="muted", wrap=False)
        self.pairs = Table(
            ["pair", "ends on", "tests", "fx", "after"],
            tip="Every pair the host's code dispatches. Click one to try it against the clip on"
            " screen. Ends on: what finishes it (the clip, or a frame budget). Tests: clip"
            " frames its code waits for. After: the pairs it hands to.",
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
        for w in (host, self.no_intel, moves, align):
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
            "An action binds a host behaviour pair to one of the port's clips, so it needs a"
            " port manifest. Open one (ports/<name>.toml).",
        )
        self.pages = Pages(self.split, self.no_scene)
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
        """The findings open, or folded to one line of their codes."""
        self.findings.setVisible(on)
        self.handler.setVisible(on and bool(self.handler.text()))
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
        self.no_intel.setText(f"No action intel for em{ws.browsing_species or 0:02d}.")
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
                self.species.addItem(f"em{s:02d}" + ("  (host)" if s == hs else ""), s)
            self.species.blockSignals(False)
        kit.put(self.species, cur)
        self.host_note.setText(
            f"the host this port rides (host_species = {hs})" if ws.browsing_the_host else ""
        )
        self.browsing.setVisible(not ws.browsing_the_host)
        self.browsing.setText(
            f"Browsing em{cur or 0:02d}: this port rides em{hs or 0:02d}, so these pairs are not"
            " the ones its engine dispatches. This compares hosts; it does not re-host. The"
            " host also picks the frame PAC the porter files clips into, so changing it means a"
            " rebuild."
        )
        self.show_host.setText(f"Show em{cur or 0:02d} beside the port")
        kit.put(self.show_host, ws.show_host)
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
                        f"em{h.species:02d}" + (" (host)" if h.species == hs else ""),
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
                f"{len(shown)} of {len(intel)} pairs em{self.ws.browsing_species or 0:02d}"
                " dispatches"
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
        self.show_effects.setText(f"Species-wide effects ({len(fx)})")
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
        """Which clip the host plays for this action. An a1 its pack lacks says so rather than
        falling through to another a1: the wrong animal doing the wrong thing."""
        ws, p, act = self.ws, al.pair, self.studio.act
        a1s = () if p is None else tuple(p.a1)
        table = ws.host_clip_table() if ws.show_host and a1s else {}
        sp = ws.browsing_species or 0

        def build() -> list[QWidget]:
            out: list[QWidget] = [kit.label("The host plays clip", role="muted", wrap=False)]
            for a1 in a1s:
                if not ws.show_host:
                    out.append(kit.label(str(a1), wrap=False))
                elif a1 not in table:
                    lb = kit.label(f"{a1}?", role="muted", wrap=False)
                    lb.setToolTip(
                        f"The host's code names clip {a1} and em{sp:02d}'s pack has no such"
                        " slot: either the action is unreachable, or its clip comes from"
                        " somewhere this tool cannot see."
                    )
                    out.append(lb)
                else:
                    frames, loop = table[a1]
                    b = kit.button(
                        f"{a1}" + (" ●" if ws.host_clip == a1 else ""),
                        tip=f"Plays the host's clip {a1} ({frames} frames"
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
            f"None of this action's clips is in em{sp:02d}'s pack, so the host beside you shows"
            " its default pose, not this action."
        )

    def _hits(self, al: Alignment) -> None:
        """What this action hits with: the attack records its code spawns, and their sets."""
        ws, p = self.ws, al.pair
        host = ws.host_attacks()
        recs = [] if host is None or p is None else host.records_for(p.attack_ids, ws.host_species)
        if host is None or p is None or not ws.browsing_the_host:
            text = ""
        elif not p.attack_ids:
            n = p.attack_sites_computed
            more = f" ({n} computed id{'' if n == 1 else 's'})" if n else ""
            text = f"Hits with: nothing the static scan can see{more}."
        elif not recs:
            ids = ",".join(map(str, p.attack_ids))
            text = (
                f"Hits with attack id {ids}, which cannot be resolved for species"
                f" {ws.host_species} (no id offset is known)."
            )
        else:
            bits = []
            for a in recs:
                st = host.set(a.volume)
                desc = "" if st is None else st.describe()
                desc = "" if not desc else f" [{desc[:60]}{'...' if len(desc) > 60 else ''}]"
                bits.append(
                    f"attack {a.id} (power {a.power}, element 0x{a.element:02X}) with set"
                    f" {a.volume}{desc}"
                )
            text = "Hits with " + "; ".join(bits)
        self.hits_text.setText(plain(text))
        self.hits_text.setVisible(bool(text))
        sets = sorted({a.volume for a in recs}) if ws.browsing_the_host else []
        act = self.studio.act
        self.hits.setVisible(bool(sets))
        self.hits.fill(
            tuple(sets),
            lambda: [
                kit.button(
                    f"Edit set {s} in Hitboxes",
                    tip=f"Opens attack set {s} in Hitboxes: your port's copy when it has one,"
                    " else the host's to adopt",
                    on=act("edit set", partial(ws.edit_set, s)),
                    icon="ph.arrow-square-out",
                )
                for s in sets
            ],
        )

    def _then(self, al: Alignment) -> None:
        """What the ENGINE does after this pair, beside what the move DECLARES."""
        ws, p, act = self.ws, al.pair, self.studio.act
        nxt = () if p is None or not p.next else tuple(p.next)

        def build() -> list[QWidget]:
            out: list[QWidget] = [kit.label("When it ends, the engine goes to", role="muted")]
            for e in nxt:
                tgt = "/".join(f"({m},{s})" for m, s in e.to) or "?"
                b = kit.button(
                    tgt,
                    tip=plain(f"Selects {tgt}, the pair this one hands to: {e}"),
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
                f"({p.main},{p.sub}) never ends by itself: forced, it stays until something else"
                " moves the monster. A move here needs `after =`."
            )
        m = ws.manifest
        mv = None if m is None or not al.move else m.moves.get(al.move)
        text = ""
        if m is not None and mv is not None and mv.after:
            nm = m.moves.get(mv.after)
            text = f"declared after = {mv.after}" + (
                f" ({nm.main},{nm.sub})" if nm else " (no such move!)"
            )
        self.declared.setText(text)
        self.declared.setVisible(bool(text))

    def _findings(self, al: Alignment) -> None:
        """Folded to a line of codes unless something is wrong."""
        key = (al.move, al.main, al.sub, al.clip, tuple(al.findings))
        if key != self._findings_key:
            self._findings_key = key
            while (it := self.findings_lay.takeAt(0)) is not None:
                if (w := it.widget()) is not None:
                    w.deleteLater()
            marks = {"error": "x", "warning": "!", "info": "·"}
            for f in al.findings:
                lb = alert(plain(f"{marks.get(f.level, '·')}  {f.message}"), f.level)
                self.findings_lay.addWidget(lb)
            n_err, n_warn = len(al.errors), len(al.warnings)
            s = "" if len(al.findings) == 1 else "s"
            counts = f": {n_err} error, {n_warn} warning" if n_err or n_warn else ""
            self.show_findings.setText(f"Show the {len(al.findings)} finding{s}{counts}")
            if (al.main, al.sub) != self._findings_pair:  # a new pair opens on its errors
                self._findings_pair = (al.main, al.sub)
                kit.put(self.show_findings, bool(al.errors))
            bits: list[tuple[str, Level]] = [
                (f.code.replace("_", " ").lower(), f.level)
                for f in al.findings
                if f.level != "info"
            ]

            self.dots.setText("  ·  ".join(code for code, _ in bits) or "nothing to flag")
            set_level(self.dots, worst(al.findings) if bits else "info")
        p = al.pair
        a1 = "" if p is None else ",".join(map(str, p.a1)) or "-"
        self.handler.setText(
            f"handler 0x{p.handler:08X}   a1 {a1}" if p is not None and p.handler else ""
        )
        self.show_findings.setVisible(bool(al.findings))
        self._fold(bool(al.findings) and self.show_findings.isChecked())

    def _bind(self, al: Alignment) -> None:
        """Refused while browsing another host, and for a pair the census measured as never
        entered (`allow_unentered` belongs in the file, beside the reason)."""
        ws = self.ws
        can = ws.label_session is not None
        note = ""
        if not can:
            pass
        elif not ws.browsing_the_host:
            note = (
                f"Cannot bind: this pair is em{ws.browsing_species or 0:02d}'s, and the engine"
                f" runs em{ws.host_species or 0:02d}'s for this port."
            )
        elif any(f.code == "NEVER_ENTERED" for f in al.errors):
            note = "Cannot bind: the game was watched and never enters this pair."
        else:
            clip = f" with clip {al.clip}" if al.clip else ""
            note = f"Writes [moves] on ({al.main},{al.sub}){clip}."
            self.bind_name.setPlaceholderText(f"move_{al.main}_{al.sub}")
        ok = can and note.startswith("Writes")
        self.bind_row.setVisible(ok)
        self.bind_note.setVisible(bool(note))
        self.bind_note.setText(note)
