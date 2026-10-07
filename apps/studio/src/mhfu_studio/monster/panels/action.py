# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Actions dock: for each of the base monster's actions, the clip your port plays, what it
hits with and whether the timing fits (`actions.rows`). Pick one to watch it, give it another
clip, and copy the moves into your mod's Lua, which is how they reach the game.

A ported monster has no AI of its own: the engine runs the one MHFU overlay `port.host_species`
names, so every `(main, sub)` here is that base monster's, with the port's animation on it."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Sequence
from functools import partial
from typing import TYPE_CHECKING

from mhfu import files
from mhfu.em.intel import PairIntel, SpeciesIntel
from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QHeaderView, QVBoxLayout, QWidget

from mhfu_studio.monster import actions, clips, species
from mhfu_studio.monster.actions import ActionRow
from mhfu_studio.monster.align import Alignment
from mhfu_studio.monster.panels import graph
from mhfu_studio.monster.panels.common import COVERAGE
from mhfu_studio.monster.panels.handoffs import HandOffs
from mhfu_studio.monster.panels.widgets import NoScene
from mhfu_studio.shell.findings import Level, worst
from mhfu_studio.shell.text import plain
from mhfu_studio.ui import kit

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

#: the background survey is looked at this often (ms) until it is done
POLL = 250
#: rows the tables show before they scroll
ROWS, CLIP_ROWS = 10, 8
#: said once for every action without a census, so not on the picked one's line
EVERYWHERE = {"UNMEASURED"}
LUA_NOTE = (
    "The game reads moves from your mod's Lua file, not from this file. Copy them into the"
    " mod's P.define{…}; Send hitboxes to game carries nothing else."
)
ROWS_TIP = (
    "Every action worth a clip: your moves, then the base monster's attacks, then what the game"
    " goes into by itself (read from its code). Plays now is the clip on screen while the game"
    " is in it; Hits, its hit group; Timing, your clip's impact against the frame the code"
    " checks. Click one to watch it from the start."
)


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


def row_level(r: ActionRow) -> Level | None:
    """An idle copy is loud on every row (forced, it looks like an override that never fired);
    a missing clip or a bad timing only on your own moves."""
    mine = r.group == actions.MOVE
    if mine and (r.timing.level == "error" or r.plays.kind == clips.MISSING):
        return "error"
    if r.plays.kind == clips.FILLER or (mine and r.timing.level == "warning"):
        return "warning"
    return None


def stretch(t: kit.Table, column: int) -> None:
    """`column`, a clip's name, takes the width the dock has left, eliding."""
    head = t.horizontalHeader()
    head.setStretchLastSection(False)
    head.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)


def matches(r: ActionRow, needle: str) -> bool:
    words = [*r.cells(), r.plays.name, f"anim {r.plays.slot}", r.label]
    words += [f"({m},{s})" for m, s in r.alike]
    return needle in " ".join(words).lower()


class ActionsPanel(kit.Panel):
    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        act = studio.act
        self._picker_for: object = None
        self._name_for: object = None

        # the base monster, and its model beside yours
        self.host_note = kit.label(role="muted", wrap=False)
        self.beside = kit.check(
            "Base monster beside",
            tip="Stands the base monster's own model beside yours, playing its own anim for the"
            " picked action from the same frame: what the action really looks like",
            on=lambda on: act("base monster beside", lambda: ws.set_show_host(on))(),
        )
        self.no_intel = kit.Alert()

        # every action worth a clip
        self.filter = kit.text_field(
            tip="Shows only the rows with this text: a move or clip name, an anim like 'anim 17',"
            " an action like (1,4), or a kind like 'idle'",
            placeholder="Filter: name, anim, (1,4) or kind",
        )
        self.filter.textChanged.connect(lambda _t: self.sync())
        self.count = kit.label(role="muted", wrap=False)
        self.table = kit.Table(
            ["Action", "Plays now", "Hits", "Timing"], tip=ROWS_TIP, swatch_column=1
        )
        stretch(self.table, 1)
        self.table.picked.connect(self._pick)

        # the picked action
        self.title = kit.label(role="title", wrap=False)
        self.plays = kit.label()
        self.headline = kit.label(role="muted")
        self.preview = kit.Alert(level="info")
        self.change = kit.button(
            "Change clip…",
            tip="Lists your clips: click one to watch it on this action, then use it",
            on=lambda: self._open_picker(not self.picker.isVisible()),
            icon="ph.film-strip",
        )
        self.unbind = kit.button(
            "Remove the move",
            tip="Deletes this move from the manifest: the action plays the base monster's anim"
            " in your port again",
            on=act("remove move", ws.unbind),
            icon="ph.link-break",
        )
        self.name = kit.text_field(
            tip="The move's name: your mod's Lua plays it by this name (m:play(\"lunge\"))",
            placeholder="move name",
        )
        self.name.editingFinished.connect(self._rename)
        self.name_form = kit.Form()
        self.name_form.row("Name", self.name)

        # the clip picker
        self.clip_filter = kit.text_field(
            tip="Shows only the clips whose anim #, name, kind or label has this text",
            placeholder="Filter clips",
        )
        self.clip_filter.textChanged.connect(lambda _t: self.sync())
        self.clips = kit.Table(
            ["Anim", "Clip", "Kind", "Frames"],
            tip="Your clips; click one to watch it on this action from its first frame",
            swatch_column=2,
        )
        stretch(self.clips, 1)
        self.clips.picked.connect(self._preview)
        self.use = kit.button(
            "Use this clip",
            tip="Writes the clip on screen into the manifest as this action's move: the move keeps"
            " its other settings, and an unnamed clip gets a name",
            on=self._use,
            role="primary",
            icon="ph.link",
        )
        self.cancel = kit.button(
            "Close",
            tip="Closes the list; the clip stays as it is in the manifest",
            on=lambda: self._open_picker(False),
        )
        self.picker = QWidget()
        lay = QVBoxLayout(self.picker)
        lay.setContentsMargins(0, 0, 0, 0)
        for w in (self.clip_filter, self.clips, kit.row(self.use, self.cancel, stretch=True)):
            lay.addWidget(w)
        self.picker.setVisible(False)
        self.refused = kit.Alert(role="muted")

        self.hits_text = kit.label(selectable=True)
        self.hits = Strip(column=True)
        self.holds = kit.Alert()
        self.dots = kit.Alert()

        picked = kit.Section("This action", tip="The picked action, and the clip it plays")
        for w in (
            self.title,
            self.plays,
            self.headline,
            self.preview,
            kit.row(self.change, self.unbind, stretch=True),
            self.picker,
            self.name_form,
            self.refused,
            self.hits_text,
            self.hits,
            self.holds,
            self.dots,
        ):
            picked.body.addWidget(w)
        self.picked = picked

        # how moves reach the game
        self.lua_note = kit.label(LUA_NOTE, role="muted")
        self.copy = kit.button(
            "Copy Lua",
            tip="Copies your moves as the clips = {…} and moves = {…} lines of a mod's"
            " P.define{…}, to paste over the old ones",
            on=act("copy lua", self._copy),
            icon="ph.copy",
        )
        self.lua = kit.label(role="mono", selectable=True)
        self.lua.setToolTip("What Copy Lua copies: paste it into your mod's P.define{…}")
        game = kit.Section(
            "How this reaches the game", tip="Moves live in your mod's Lua, not in the manifest"
        )
        for w in (self.lua_note, kit.row(self.copy, stretch=True), self.lua):
            game.body.addWidget(w)

        # expert: every action, the hand-off graph, the code, another monster's actions
        self.more = kit.More(
            tip="Every action of the base monster as a graph and a table, the code behind the"
            " picked one, and another monster's actions to compare"
        )
        self.more.toggle.toggled.connect(lambda _on: self.sync())
        self._build_more()

        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(10)
        for w in (
            kit.row(self.host_note, self.beside, stretch=True),
            self.no_intel,
            kit.row(self.filter, self.count),
            self.table,
            picked,
            game,
            self.more,
        ):
            pl.addWidget(w)
        pl.addStretch(1)
        self.no_scene = NoScene(studio)
        self.no_scene.say(
            "No port manifest",
            "An action plays one of your clips when the base monster enters it, so it needs a"
            " port manifest. Open one (ports/<name>.toml).",
        )
        self.pages = kit.Pages(page, self.no_scene)
        self.body.addWidget(self.pages)
        self.poll = QTimer(self)
        self.poll.setInterval(POLL)
        self.poll.timeout.connect(self.sync)

    def _build_more(self) -> None:
        ws, act = self.ws, self.studio.act
        self.species = kit.choice(
            [],
            tip="Whose actions the graph and the table read. Your base monster's are the ones"
            " the game runs for your port; another one is for comparing, not for moves.",
            on=lambda sp: act("browse", lambda: ws.browse_species(int(sp)))(),
        )
        self._species_key: object = None
        self._choices = species_choices(ws)
        self.browsing = kit.Alert()
        self.compare = kit.check(
            "Compare the monsters",
            tip="Every MHFU monster's actions side by side: which base monster gives your port"
            " the most to work with",
            on=lambda on: self.sync(),
        )
        self.hosts_wait = kit.label(
            "Reading every monster's actions (once, then kept)…", role="muted"
        )
        self.hosts = kit.Table(
            ["Monster", "Actions", "Timed", "Timer", "Effects", "Hidden"],
            tip="Click a monster to read its actions. Timed actions wait for clip frames; timer"
            " ones end on a countdown. Hidden: groups whose actions the studio cannot list.",
        )
        self.hosts.picked.connect(lambda sp: act("browse", lambda: ws.browse_species(sp))())
        self.graph = HandOffs(ws, self.studio)
        self.pairs_filter = kit.text_field(
            tip="Shows only the actions whose row has this text: an action like 1,4, a frame,"
            " where it goes, or 'timer'",
            placeholder="filter actions",
        )
        self.pairs_filter.textChanged.connect(lambda _t: self.sync())
        self.pairs_count = kit.label(role="muted", wrap=False)
        self.pairs = kit.Table(
            ["Action", "Ends", "Checks", "Effects", "Then"],
            tip="Every action the base monster's code runs; click one to pick it. Ends: what"
            " finishes it; Checks: frames its code waits for; Then: where it goes next.",
        )
        self._pairs_key: object = None
        self.pairs.picked.connect(lambda pr: act("pick action", lambda: ws.select_action(*pr))())
        self.handler = kit.label(role="mono", selectable=True)
        self.handler.setToolTip("Where the action's code is in the game, and the anims it names")
        self.then = Strip(column=True)
        self.declared = kit.label(role="muted")
        self.show_findings = kit.check(
            "Every finding",
            tip="Every check on the picked action against its clip, with what it means",
            on=lambda on: self.sync(),
        )
        self.findings = QWidget()
        self.findings_lay = QVBoxLayout(self.findings)
        self.findings_lay.setContentsMargins(0, 0, 0, 0)
        self._findings_key: object = None
        self.show_effects = kit.check(
            "Effects of no action",
            tip="The base monster's timed effects that no action is known to fire; joints are"
            " the base monster's",
            on=lambda on: self.sync(),
        )
        self.effects = kit.Table(
            ["Effect", "Joint", "Frame"], tip="Hover a row: what that joint is on your skeleton"
        )
        browse = kit.Form()
        browse.row("Actions of", self.species)
        for w in (
            browse,
            self.browsing,
            self.compare,
            self.hosts_wait,
            self.hosts,
            kit.label("Where each action goes when it ends", role="caps"),
            self.graph,
            kit.label("Every action", role="caps"),
            kit.row(self.pairs_filter, self.pairs_count),
            self.pairs,
            self.handler,
            self.then,
            self.declared,
            self.show_findings,
            self.findings,
            self.show_effects,
            self.effects,
        ):
            self.more.body.addWidget(w)

    # ---- actions ----------------------------------------------------------------------- #

    def _pick(self, key: object) -> None:
        """A row: back on the base monster's actions if another's were browsed."""
        if not isinstance(key, tuple):
            return
        main, sub, move = key
        ws = self.ws

        def run() -> None:
            if not ws.browsing_the_host and ws.host_species is not None:
                ws.browse_species(ws.host_species)
            ws.select_action(main, sub, move)

        self.studio.act("pick action", run)()

    def _preview(self, slot: object) -> None:
        if isinstance(slot, int):
            self.studio.act(f"try anim {slot}", lambda: self.ws.play_slot(slot))()

    def _use(self) -> None:
        """Binds the clip on screen; the list closes once it is in the manifest."""
        before = self.ws.manifest
        self.studio.act("use clip", lambda: self.ws.bind_move())()
        if self.ws.manifest is not before:
            self._open_picker(False)

    def _open_picker(self, on: bool) -> None:
        self.picker.setVisible(on)
        if on:
            self.sync()

    def _rename(self) -> None:
        new = self.name.text().strip()
        if new and new != self.ws.move:
            self.studio.act("rename move", lambda: self.ws.rename_move(new))()

    def _copy(self) -> None:
        m = self.ws.manifest
        text = "" if m is None else actions.lua_moves(m)
        if not text:
            self.ws.message = "no moves to copy yet: use a clip for an action first"
            return
        QGuiApplication.clipboard().setText(text + "\n")
        n = 0 if m is None else sum(mv.pair is not None for mv in m.moves.values())
        self.ws.message = f"copied {n} moves as Lua: paste them into your mod's P.define{{…}}"

    # ---- sync -------------------------------------------------------------------------- #

    def sync(self) -> None:
        ws = self.ws
        m = ws.manifest
        self.pages.show_page(m is not None)
        if m is None:
            self.poll.stop()
            return
        self.host_note.setText(f"Base monster: {species.label(ws.host_species)}")
        kit.put(self.beside, ws.show_host)
        gap = ws.intel_gap("action")
        self.no_intel.setText(gap)
        self.no_intel.setVisible(bool(gap))
        self._sync_rows()
        self._sync_picked()
        lua = actions.lua_moves(m)
        self.lua.setText(lua or "No moves yet: use a clip for an action and its Lua shows here.")
        self.copy.setEnabled(bool(lua))
        if self.more.toggle.isChecked():
            self._sync_more()
        else:
            self.poll.stop()

    def _sync_rows(self) -> None:
        ws = self.ws
        rows = ws.action_rows()
        needle = self.filter.text().strip().lower()
        shown = [r for r in rows if not needle or matches(r, needle)]
        self.table.set_rows(
            [r.cells() for r in shown],
            [r.key for r in shown],
            colors=[COVERAGE.get(r.plays.kind or "") for r in shown],
            tips=[r.tip() for r in shown],
            levels=[row_level(r) for r in shown],
        )
        self.table.fit(ROWS)
        self.count.setText(f"{len(shown)} of {len(rows)}")
        self.table.select_data(self._selected_key(shown))

    def _selected_key(self, rows: list[ActionRow]) -> object:
        """The row of the picked action: its own, or the one it is listed alike under."""
        ws = self.ws
        if ws.pair is None:
            return None
        for r in rows:
            if r.move == ws.move and (r.pair == ws.pair or ws.pair in r.alike):
                return r.key
        return None

    def _sync_picked(self) -> None:
        ws, m, pair = self.ws, self.ws.manifest, self.ws.pair
        self.picked.setVisible(pair is not None)
        if pair is None or m is None:
            self.picker.setVisible(False)
            return
        if (pair, ws.move) != self._picker_for:  # another action closes the list
            self._picker_for = (pair, ws.move)
            self.picker.setVisible(False)
        host = ws.browsing_the_host
        bound = ws.move is not None and ws.move in m.moves
        now = ws.plays(*pair, ws.move)
        name = f"({pair[0]},{pair[1]})"
        self.title.setText(f"{ws.move} {name}" if bound else name)
        frames = "" if now.frames is None else f", {now.frames} frames"
        anim = "" if now.slot is None or not now.name else f" (anim {now.slot})"
        self.plays.setText(f"Plays {now.text()}{anim}{frames}" if host else "")
        self.plays.setToolTip(now.why)
        self.plays.setVisible(host)
        al = ws.alignment
        self.headline.setText("" if al is None else plain(al.headline))
        self.headline.setVisible(al is not None)
        vp = ws.vp
        on_screen = None if vp is None or vp.clip is None else vp.clip.slot
        trying = host and on_screen is not None and on_screen != now.slot
        self.preview.setVisible(trying)
        if trying:
            found = ws.manifest_clip(on_screen) if on_screen is not None else None
            label = f" {found[0]}" if found else ""
            self.preview.setText(
                f"On screen: anim {on_screen}{label}, not what plays now. Use this clip keeps it."
            )
        refusal = self._refusal(al)
        self.refused.setText(refusal)
        self.refused.setVisible(bool(refusal))
        can = not refusal and ws.label_session is not None
        self.change.setVisible(can)
        self.unbind.setVisible(bound and host)
        self.name_form.setVisible(bound and host)
        if bound and (pair, ws.move) != self._name_for:
            self._name_for = (pair, ws.move)
            kit.put(self.name, ws.move or "")
        if not can:
            self.picker.setVisible(False)
        if self.picker.isVisible():
            self._sync_picker(on_screen, now.slot, bound and not now.claimed)
        self._hits(al)
        self._holds(al)
        self._dots(al)

    def _refusal(self, al: Alignment | None) -> str:
        """Why no clip can be used here: another monster's action, or one the game never
        enters (`allow_unentered` belongs in the file, beside the reason)."""
        ws = self.ws
        if not ws.browsing_the_host:
            return (
                f"These are {species.label(ws.browsing_species)}'s actions, for comparing: the"
                f" game runs {species.label(ws.host_species)}'s for your port."
            )
        if al is not None and any(f.code == "NEVER_ENTERED" for f in al.errors):
            return "No clip here: the game was watched and never enters this action."
        return ""

    def _sync_picker(self, on_screen: int | None, plays: int | None, bound: bool) -> None:
        ws, sc = self.ws, self.ws.scene
        if sc is None:
            return
        cov = ws.vocabulary().coverage
        needle = self.clip_filter.text().strip().lower()
        rows, data, colors = [], [], []
        for c in sc.clips:
            sl = cov.slots.get(c.slot)
            found = ws.manifest_clip(c.slot)
            name = found[0] if found else ""
            kind = "" if sl is None else clips.KIND_WORDS[sl.kind][0]
            label = found[1].label if found else ""
            cells = [str(c.slot), name, kind, f"{c.frames} loop" if c.loop else str(c.frames)]
            if needle and needle not in " ".join([*cells, label]).lower():
                continue
            rows.append(cells)
            data.append(c.slot)
            colors.append(None if sl is None else COVERAGE[sl.kind])
        self.clips.set_rows(rows, data, colors=colors)
        self.clips.fit(CLIP_ROWS)
        self.clips.select_data(on_screen)
        same = on_screen is not None and on_screen == plays and bound
        self.use.setEnabled(on_screen is not None and not same)

    def _hits(self, al: Alignment | None) -> None:
        """What the action hits with: the attack records its code spawns, and their groups."""
        ws = self.ws
        p = None if al is None else al.pair
        host = ws.host_attacks()
        recs = [] if host is None or p is None else host.records_for(p.attack_ids, ws.host_species)
        text = ""
        if host is not None and p is not None and ws.browsing_the_host and p.attack_ids:
            if recs:
                text = "Hits with " + "; ".join(
                    f"attack {a.id} (power {a.power}) using hit group {a.volume}" for a in recs
                )
            else:
                text = (
                    f"Hits with attack {', '.join(map(str, p.attack_ids))}, whose hit group"
                    f" cannot be matched for {species.label(ws.host_species)}."
                )
        self.hits_text.setText(plain(text))
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

    def _holds(self, al: Alignment | None) -> None:
        p = None if al is None else al.pair
        holds = p is not None and p.next is not None and not p.next
        self.holds.setVisible(holds)
        if p is not None:
            self.holds.setText(
                f"({p.main},{p.sub}) never ends by itself: played from your mod, the monster stays"
                " in it. Its move needs an after = in the manifest."
            )

    def _dots(self, al: Alignment | None) -> None:
        """The worst finding on one line; every one in More, codes in the tips."""
        found = [] if al is None else al.findings
        bad = [f for f in found if f.level != "info" and f.code not in EVERYWHERE]
        first = max(bad, key=lambda f: f.level == "error", default=None)
        self.dots.setVisible(first is not None)
        if first is not None and al is not None:
            self.dots.setText(plain(first.message))
            self.dots.setToolTip(" · ".join(f.code for f in bad))
            self.dots.set_level(worst(bad))

    def _sync_more(self) -> None:
        ws = self.ws
        self._sync_species()
        intel = ws.intel
        self.graph.sync()
        self.pairs.setVisible(intel is not None)
        if intel is not None:
            self._sync_pairs(intel)
        al = ws.alignment
        p = None if al is None else al.pair
        a1 = "" if p is None else ",".join(map(str, p.a1)) or "-"
        self.handler.setText(
            f"code at 0x{p.handler:08X}   anims {a1}" if p is not None and p.handler else ""
        )
        self.handler.setVisible(bool(self.handler.text()))
        self._then(al)
        self._findings(al)
        fx = [] if intel is None else intel.framed_effects()
        self.show_effects.setVisible(bool(fx))
        self.show_effects.setText(f"Effects of no action ({len(fx)})")
        self.effects.setVisible(bool(fx) and self.show_effects.isChecked())
        if fx and self.effects.isVisible():
            rig = ws.port_rig()
            self.effects.set_rows(
                [[str(e.id), "?" if e.bone is None else str(e.bone), str(e.frame)] for e in fx],
                tips=[
                    "" if rig is None or e.bone is None else f"on your rig: {rig.describe(e.bone)}"
                    for e in fx
                ],
            )
            self.effects.fit(6)

    def _sync_species(self) -> None:
        ws = self.ws
        ids, cur, hs = self._choices, ws.browsing_species, ws.host_species
        if (tuple(ids), hs) != self._species_key:
            self._species_key = (tuple(ids), hs)
            self.species.blockSignals(True)
            self.species.clear()
            for s in ids:
                self.species.addItem(species.label(s) + (", base" if s == hs else ""), s)
            self.species.blockSignals(False)
        kit.put(self.species, cur)
        self.browsing.setVisible(not ws.browsing_the_host)
        self.browsing.setText(
            f"Reading the actions of {species.label(cur)}: the game runs {species.label(hs)}'s"
            " for your port, not these. This compares; changing the base monster is a rebuild."
        )
        on = self.compare.isChecked()
        hosts = ws.host_options() if on else None
        waiting = on and hosts is None
        self.hosts_wait.setVisible(waiting)
        self.hosts.setVisible(on and not waiting)
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

    def _sync_pairs(self, intel: SpeciesIntel) -> None:
        needle = self.pairs_filter.text().strip().lower()
        if (id(intel), needle) != self._pairs_key:
            self._pairs_key = (id(intel), needle)
            shown = [p for p in intel if not needle or needle in " ".join(pair_row(p)).lower()]
            self.pairs.set_rows(
                [pair_row(p) for p in shown],
                [(p.main, p.sub) for p in shown],
                tips=["\n".join(map(str, p.next or ())) for p in shown],
                levels=["warning" if p.budget.gated else None for p in shown],
            )
            self.pairs_count.setText(
                f"{len(shown)} of {len(intel)} actions of {species.label(self.ws.browsing_species)}"
            )
        self.pairs.fit(ROWS)
        self.pairs.select_data(self.ws.pair)

    def _then(self, al: Alignment | None) -> None:
        """Where the GAME goes after this action, beside what the move DECLARES."""
        ws, act = self.ws, self.studio.act
        p = None if al is None else al.pair
        nxt = () if p is None or not p.next else tuple(p.next)

        def build() -> list[QWidget]:
            out: list[QWidget] = [kit.label("When it ends, the game goes to", role="muted")]
            for e in nxt:
                tgt = "/".join(f"({m},{s})" for m, s in e.to) or "?"
                b = kit.button(
                    tgt,
                    tip=plain(f"Picks {tgt}, the action this one goes to: {e}"),
                    on=act("then", partial(ws.select_action, *e.to[0]) if e.to else lambda: None),
                )
                why = kit.label(f"when {e.reason}" if e.reason else "always", role="muted")
                out.append(kit.row(b, why, stretch=True))
            return out

        self.then.setVisible(bool(nxt))
        self.then.fill(tuple(str(e) for e in nxt), build)
        m = ws.manifest
        mv = None if m is None or al is None or not al.move else m.moves.get(al.move)
        text = ""
        if m is not None and mv is not None and mv.after:
            nm = m.moves.get(mv.after)
            pr = None if nm is None else nm.pair
            to = (
                " (no such move!)"
                if nm is None
                else " (own move)"
                if pr is None
                else f" ({pr[0]},{pr[1]})"
            )
            text = f"your move goes on to {mv.after}{to}"
        self.declared.setText(text)
        self.declared.setVisible(bool(text))

    def _findings(self, al: Alignment | None) -> None:
        found = [] if al is None else al.findings
        self.show_findings.setVisible(bool(found))
        self.show_findings.setText(f"Every finding ({len(found)})")
        self.findings.setVisible(bool(found) and self.show_findings.isChecked())
        key = None if al is None else (al.move, al.main, al.sub, al.clip, tuple(found))
        if key == self._findings_key:
            return
        self._findings_key = key
        while (it := self.findings_lay.takeAt(0)) is not None:
            if (w := it.widget()) is not None:
                w.deleteLater()
        marks = {"error": "x", "warning": "!", "info": "·"}
        for f in found:
            lb = kit.Alert(plain(f"{marks.get(f.level, '·')}  {f.message}"), f.level)
            lb.setToolTip(f.code)
            self.findings_lay.addWidget(lb)
