# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Behaviour tab: the port's behaviour graph (`mhfu_port.behaviour`) on a node canvas.

A node is a move or a block, drawn compact; a wire is a link the manifest stores
(`monster/behaviour.py`). The outline beside the canvas scopes it: All is the canvas as stored,
a trigger shows only its rules, one framed row each in the order they are checked (`rows`), laid
out for display and never written to a block's `at`. The selected node's values and note are
edited in the bar under the canvas. The document is the owner: every gesture of the canvas and
every edit of the bar arrives as intents, is applied as one undo step, and is shown again from
the document.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from functools import partial
from typing import TYPE_CHECKING

from mhfu_port import behaviour as model
from mhfu_port.behaviour import KINDS, Kind, Path, Trigger
from mhfu_port.manifest import Manifest, ManifestError, Move
from PySide6.QtCore import QTimer
from PySide6.QtGui import QHideEvent, QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QMenu, QSizePolicy, QVBoxLayout, QWidget

from mhfu_studio.monster import behaviour as graph
from mhfu_studio.monster import rule_game
from mhfu_studio.monster.behaviour import ALL, CANVAS, ORDER
from mhfu_studio.monster.panels import rows, triggers
from mhfu_studio.monster.panels.common import kind
from mhfu_studio.monster.panels.node_graph import FrameSpec, GraphView, LinkSpec, NodeSpec
from mhfu_studio.monster.panels.properties import Detail, Play, Plays, PropertyBar
from mhfu_studio.monster.panels.widgets import NoScene
from mhfu_studio.shell.overlay import Color, Ink
from mhfu_studio.ui import kit, theme

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

#: a node's colour by role; a move has a role of its own
ROLES: dict[str, Color] = {
    "event": Ink.AXIS_X,
    "state": Ink.AXIS_Y,
    "condition": Ink.AXIS_Z,
    "modifier": Ink.WARNING,
    "effect": Ink.EFFECT,
    "move": Ink.SELECTION,
}
MOVE_ROLE = "move"
WARN = chr(0x26A0)
HINT = (
    "Right-click adds a block · drag from a port to wire · higher blocks are checked first,"
    " but paths that play nothing run before all of them"
)
#: over a trigger's rows, and what a wire dragged there is told
SCOPED_HINT = (
    "+ Rule adds a rule · right-click a block to insert one · Delete removes · drag a frame to"
    " reorder · wire in All, Canvas"
)
ORDER_HINT = (
    "Right-click a block to insert one · Delete removes · drag a frame to reorder · wire in Canvas"
)
FIRE_TIP = "Raises {} in the running game now; the rules' counts show whether their conditions held"
SCOPED = "Wire by dragging ports in All, Canvas"
UNPLACED = "A rule with no move yet is checked by nothing, so it has no place to move"
CROSSED = "; a rule moves only among its own trigger's rules, the others keep their places"


class BehaviourPanel(kit.Panel):
    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__(scroll=False)
        self.ws, self.studio = ws, studio
        #: the manifest and theme the canvas was last shown for
        self._drawn: Manifest | None = None
        self._theme: object = None
        #: the move the canvas last followed
        self._move: str | None = None
        #: the intents of the gesture in flight, and where its nodes were dropped
        self._gesture: graph.Gesture | None = None
        self._dropped: dict[str, graph.Point] = {}
        #: the block the gesture creates
        self._made: str | None = None
        #: what the canvas was last drawn from, for the bar and the outline
        self._read: graph.Reading | None = None
        self._triggers: list[Trigger] = []
        #: the rules with no move yet, by their trigger's key
        self._opens: dict[str, list[Path]] = {}
        #: the rows now drawn (None: All), and what to pick once the next edit is drawn
        self._plan: rows.Plan | None = None
        self._after: tuple[str, str | None] | None = None
        self._frame_after: str | None = None
        self._scope: tuple[str, str] | None = None
        self._outlined: object = None
        #: the rows as last laid out (nodes, wires, frames), for the marks to draw again
        self._laid: tuple[list[NodeSpec], list[LinkSpec], list[FrameSpec]] | None = None
        self._palette = ALL

        self.title = kit.label(role="title", wrap=False)
        self.hint = kit.label(HINT, role="muted", wrap=False)
        self.hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.fit = kit.button(
            "Fit",
            tip="Frames every node",
            on=lambda: self.view.fit(),
            icon="ph.arrows-out",
        )
        self.add_rule = kit.button(
            "+ Rule",
            tip="Adds a rule for this trigger. It is checked after every other rule: drag its"
            " frame up to raise it",
            on=self._add_rule,
        )
        self.fire = kit.button(
            "Fire",
            tip=FIRE_TIP,
            on=studio.act("fire in the game", self._fire),
            icon="ph.lightning",
        )
        self.mode = kit.Segmented(
            [(ORDER, "In order"), (CANVAS, "Canvas")],
            tip="All rules: each as a framed row in the order they are checked, or the free"
            " canvas as stored",
            on=self._mode,
            current=ws.all_view,
            tips={
                ORDER: "Every rule as a row, first checked first; edit them here as in a trigger",
                CANVAS: "The canvas as stored: free blocks, free wires, and how every path crosses",
            },
        )
        self.spread = kit.button(
            "Spread out",
            tip="Moves every node away from the top left so crowded ones stop overlapping; which"
            " block is higher stays so, and so does every path's priority",
            on=studio.act("spread nodes", lambda: ws.edit("", self._spread)),
            icon="ph.arrows-out-simple",
        )
        self.watch = kit.check(
            "Watch the game",
            tip="Marks every rule with how often the running game has fired it, read once a"
            " second while this tab is open. The game must run this port's rules: Play in game"
            " sends them",
            on=lambda on: studio.act("watch the game", lambda: self._watching(on))(),
            checked=ws.watch.on,
        )
        self.live = kit.Elided("", None, role="muted")
        self.ticker = QTimer(self)
        self.ticker.setInterval(round(rule_game.PERIOD * 1000))
        self.ticker.timeout.connect(studio.guard("watch the game", self._tick))
        self.glow = QTimer(self)
        self.glow.setSingleShot(True)
        self.glow.timeout.connect(self._marks)
        self.view = GraphView()
        self.view.set_palette(graph.palette())
        v = self.view
        v.link_requested.connect(lambda *w: self._collect(self._wire(graph.link, *w)))
        v.unlink_requested.connect(lambda *w: self._collect(self._wire(graph.unlink, *w)))
        v.delete_requested.connect(self._delete)
        v.moved.connect(self._moved)
        v.create_requested.connect(self._create)
        v.picked.connect(self._picked)
        v.selection_changed.connect(lambda _: self._focus())
        v.settled.connect(self._settle)
        v.menu_requested.connect(self._menu)
        v.frame_moved.connect(self._frame_moved)
        self.bar = PropertyBar()
        self.bar.param_changed.connect(self._param)
        self.bar.label_changed.connect(self._label)
        self.bar.plays_changed.connect(self._plays)
        self.bar.play_requested.connect(self._play)

        self.outline = triggers.Outline()
        self.outline.picked.connect(self._scoped)
        page = QWidget()
        side = QHBoxLayout(page)
        side.setContentsMargins(10, 8, 10, 8)
        side.setSpacing(10)
        side.addWidget(self.outline)
        main = QWidget()
        side.addWidget(main, 1)
        lay = QVBoxLayout(main)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(
            kit.row(
                self.title,
                self.hint,
                self.mode,
                self.add_rule,
                self.fire,
                self.fit,
                self.spread,
                self.watch,
                self.live,
                grow=self.live,
            )
        )
        lay.addWidget(self.view, 1)
        lay.addWidget(self.bar)
        self.no_scene = NoScene(studio)
        self.no_scene.say(
            "No port manifest",
            "The behaviour graph lives in a port manifest. Open one (ports/<name>.toml).",
        )
        self.pages = kit.Pages(page, self.no_scene)
        self.body.addWidget(self.pages)

    def _spread(self) -> str:
        doc = self.ws.doc
        if doc is None:
            raise ManifestError("no port manifest open")
        return graph.spread(doc)

    def _scoped(self, key: str) -> None:
        """A row of the outline picked: that trigger's rules, framed to fit."""
        self.ws.scope = key
        self.sync()

    def _mode(self, mode: str) -> None:
        """In order or Canvas picked for All."""
        self.ws.all_view = mode
        self.sync()

    # what the bar tells: the same intents as the canvas's, one undo step each

    def _param(self, node: str, name: str, value: object) -> None:
        self._collect(lambda e: graph.set_param(e, node, name, value))
        self._settle()

    def _label(self, node: str, text: str) -> None:
        row = self._plan.row_of(node) if self._plan is not None else None
        if row is not None and node == row.id:  # a frame's note is its first block's
            node = row.path.blocks[0]
        else:
            node = rows.node_of(node)
        self._collect(lambda e: graph.set_label(e, node, text))
        self._settle()

    def _plays(self, block: str, old: str | None, new: str | None) -> None:
        self._collect(lambda e: graph.set_play(e, block, old, new))
        self._after = (block, new)
        self._settle()

    def _play(self, move: str) -> None:
        """Play in game, as the Moves panel does it, for `move`."""

        def play() -> None:
            if self.ws.move != move:
                self.ws.select_move(move)
            self.ws.play_move_in_game()

        self.studio.act("play move in game", play)()

    def _add_rule(self) -> None:
        t, doc = self._current(), self.ws.doc
        if t is None or doc is None:
            return
        self._made = model.new_id(doc.manifest.behaviour)
        self._collect(lambda e: graph.add_rule(e, t))
        self._settle()

    def _current(self) -> Trigger | None:
        """The trigger the canvas is scoped to; None for All."""
        return next((t for t in self._triggers if t.key == self.ws.scope), None)

    # what a node's menu offers, in a trigger's rows

    def _menu(self, node: str, menu: QMenu) -> None:
        plan, m = self._plan, self.ws.manifest
        row = plan.row_of(node) if plan is not None else None
        menu.setToolTipsVisible(True)
        if m is None or row is None:
            move = graph.move_name(node)
            if move is not None and plan is not None:
                self._play_entry(menu, move)
            return
        ref = rows.ref_of(node)
        if ref is not None:  # a reference or the placeholder: before the play
            block, move = ref
            self._insert_menu(menu, "Insert before the play", block, None, move)
            if move is not None:
                menu.addAction("Delete the play").triggered.connect(
                    lambda: self._drop_play(block, move)
                )
                self._play_entry(menu, move)
            return
        if rows.is_start(node):  # a while-playing rule's start: its move
            move = graph.move_name(rows.node_of(node))
            if move is not None:
                self._play_entry(menu, move)
            return
        k, block = row.blocks.index(node), row.path.blocks[row.blocks.index(node)]
        last = k == len(row.path.blocks) - 1
        to = None if last else row.path.blocks[k + 1]
        self._insert_menu(menu, "Insert after", block, to, row.path.play or None if last else None)
        name = "Delete the rule" if k == 0 else "Delete the block"
        menu.addAction(name).triggered.connect(lambda: self._delete_now([node]))

    def _insert_menu(
        self, menu: QMenu, title: str, after: str, to: str | None, play: str | None
    ) -> None:
        sub = menu.addMenu(title)
        sub.setToolTipsVisible(True)
        for group, kinds in graph.insertable():
            inner = sub.addMenu(group) if len(graph.insertable()) > 1 else sub
            inner.setToolTipsVisible(True)
            for kind_, name, tip in kinds:
                act = inner.addAction(name)
                act.setToolTip(tip)
                act.triggered.connect(lambda _=False, k=kind_: self._insert(after, k, to, play))

    def _playable(self, move: str) -> Play:
        return Play(move, self.ws.play_move_blocker(move) or "")

    def _play_entry(self, menu: QMenu, move: str) -> None:
        act = menu.addAction("Play in game")
        off = self.ws.play_move_blocker(move)
        act.setEnabled(off is None)
        act.setToolTip(
            "Plays this move in the running game, as Play in game does in Moves"
            if off is None
            else f"Play in game: {off}"
        )
        act.triggered.connect(lambda: self._play(move))

    def _insert(self, after: str, kind_: str, to: str | None, play: str | None) -> None:
        doc = self.ws.doc
        if doc is None:
            return
        self._made = model.new_id(doc.manifest.behaviour)
        self._collect(lambda e: graph.insert_block(e, after, kind_, to, play))
        self._settle()

    def _drop_play(self, block: str, move: str) -> None:
        self._collect(lambda e: graph.set_play(e, block, move, None))
        self._after = (block, None)
        self._settle()

    # what the canvas tells

    def _collect(self, fn: Callable[[graph.Edits], str]) -> None:
        doc = self.ws.doc
        if doc is None:
            return
        if self._gesture is None:
            self._gesture = graph.Gesture(doc)
        self._gesture.run(fn)

    def _free(self) -> bool:
        """Whether the canvas is the stored one, where blocks and wires are free."""
        return self.ws.scope == ALL and self.ws.all_view == CANVAS

    def _wire(self, edit: Callable[..., str], *wire: str) -> Callable[[graph.Edits], str]:
        """The edit of a wire intent, or the refusal the rows give for it."""
        return (lambda e: edit(e, *wire)) if self._free() else self._refuse(SCOPED)

    def _delete(self, ids: list[str]) -> None:
        plan = self._plan
        if plan is None:
            self._collect(lambda e: graph.delete(e, list(dict.fromkeys(ids))))
            return
        whole = {  # a rule goes with its frame or its first block
            r.id
            for i in ids
            if (r := plan.row_of(i)) is not None and (i == r.id or i == r.blocks[0])
        }
        for r in plan.rows:
            if r.id in whole:
                self._collect(
                    partial(graph.drop_rule, blocks=r.path.blocks, play=r.path.play or None)
                )
        for i in ids:
            row, ref = plan.row_of(i), rows.ref_of(i)
            if row is None:  # a move: refused, as in All
                self._collect(partial(graph.delete, ids=[i]))
            elif row.id in whole or rows.is_start(i):
                continue
            elif ref is not None:
                if ref[1] is not None:
                    self._collect(partial(graph.set_play, block=ref[0], old=ref[1], new=None))
            else:
                self._collect(partial(graph.drop_block, block=rows.node_of(i)))

    def _frame_moved(self, frame: str, x: float, y: float) -> None:
        """A rule's frame dropped: the rule takes the place among its trigger's rules that the
        drop is at, or the drop is refused and the frame goes back."""
        plan = self._plan
        row = next((r for r in plan.rows if r.id == frame), None) if plan is not None else None
        if plan is None or row is None:
            return
        if row.rank is None:
            self._collect(self._refuse(UNPLACED))
            return
        ranked = [r for r in plan.rows if r.rank is not None and r.key == row.key]
        rects = self.view.frame_rects()
        middle = rects[frame].center().y()
        every = [r for r in plan.rows if r.rank is not None]
        to = sum(1 for r in ranked if r is not row and rects[r.id].center().y() < middle)
        above = sum(1 for r in every if r is not row and rects[r.id].center().y() < middle)
        paths = [r.path for r in ranked]
        k = ranked.index(row)
        self._frame_after = f"{rows.FRAME}{ranked[to].rank}"  # the places are handed round
        crossed = every.index(ranked[to]) != above  # the drop was among other triggers' rules

        def move(e: graph.Edits) -> str:
            said = graph.move_rule(e, paths, k, to)
            return said + (CROSSED if crossed else "")

        self._collect(move)

    @staticmethod
    def _refuse(why: str) -> Callable[[graph.Edits], str]:
        def refuse(_: graph.Edits) -> str:
            raise ManifestError(why)

        return refuse

    def _delete_now(self, ids: list[str]) -> None:
        self._delete(ids)
        self._settle()

    def _moved(self, node: str, x: float, y: float) -> None:
        if self._free():  # the rows are laid out for display: a drag snaps back
            self._dropped[node] = (x, y)

    def _create(self, kind_: str, x: float, y: float) -> None:
        doc = self.ws.doc
        if doc is None:
            return
        if not self._free():
            self._collect(self._refuse(SCOPED))
            return
        self._made = model.new_id(doc.manifest.behaviour)
        self._collect(lambda e: graph.add_block(e, kind_, (x, y)))

    def _settle(self) -> None:
        """The gesture is whole: one undo step, or one refusal."""
        doc, gesture, dropped = self.ws.doc, self._gesture, self._dropped
        made, self._gesture, self._dropped, self._made = self._made, None, {}, None
        after, self._after = self._after, None
        frame, self._frame_after = self._frame_after, None
        if doc is None:
            return
        if dropped:
            gesture = gesture or graph.Gesture(doc)
            gesture.run(lambda e: graph.move_nodes(e, dropped))
        if gesture is None:
            return
        done, was = gesture, doc.manifest

        def edit() -> None:
            self.ws.edit("", done.commit)
            self.sync()  # the window syncs a turn on: after the view would have put the canvas back

        self.studio.act("edit behaviour", edit)()
        m = self.ws.manifest
        if made is not None and m is not None and made in m.behaviour.blocks:
            self.view.select([made])
            self._show_bar()
        elif frame is not None and m is not was and frame in self.view.frame_rects():
            self.view.select([frame])
            self._show_bar()
        elif after is not None and self._plan is not None:
            ends = [n.id for r in self._plan.rows for n in r.nodes if rows.ref_of(n.id) == after]
            if ends:
                self.view.select(ends[:1])
                self._show_bar()

    def _picked(self, node: object) -> None:
        """A move node picks its move, as the Moves table does; the bar shows the node."""
        self._show_bar()
        name = graph.move_name(rows.node_of(node)) if isinstance(node, str) else None
        if name is None or name == self.ws.move:
            return

        def pick() -> None:
            self.ws.select_move(name)
            self._move = self.ws.move

        self.studio.act("pick move", pick)()

    # showing

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._show_bar()  # a game may have started while the tab was away
        self._arm()

    def hideEvent(self, event: QHideEvent) -> None:  # noqa: N802
        super().hideEvent(event)
        self._arm()

    # Watch the game: the marks

    def _watching(self, on: bool) -> None:
        self.ws.watch.set(on)
        self._arm()
        if on:
            self.ws.poll_game()
        self._marks()

    def _arm(self) -> None:
        """Polls once a second while the toggle is on and this tab is shown, else not at all."""
        if self.ws.watch.on and self.isVisible():
            if not self.ticker.isActive():
                self.ticker.start()
        else:
            self.ticker.stop()

    def _tick(self) -> None:
        if self.ws.poll_game():
            self._marks()

    def _marks(self) -> None:
        """Draws the status and the marks again, nothing else of the canvas."""
        self._status()
        if self.ws.manifest is None or self._read is None:
            return
        if self._laid is not None:
            nodes, links, frames = self._laid
            self.view.show(nodes, links, self._marked(frames))
        if self.ws.watch.lit():
            self.glow.start(round(rule_game.FLASH * 1000) + 50)

    def _fire(self) -> None:
        """Raises the scoped trigger in the running game; the game is not the document, so there
        is no undo step."""
        t = self._current()
        if t is not None:
            self.ws.fire(t)

    def _status(self) -> None:
        """The header's words on the watch, Fire's state, and the outline's totals."""
        t = self._current()
        self.fire.setVisible(t is not None and t.kind in rule_game.FIRE)
        if t is not None and t.kind in rule_game.FIRE:
            why = self.ws.fire_blocker(t)
            self.fire.setEnabled(why is None)
            self.fire.setToolTip(
                FIRE_TIP.format(triggers.title(t)) if why is None else f"Fire: {why}"
            )
        w = self.ws.watch
        self.live.setVisible(w.on)
        self.live.setText(w.status)
        self.live.setToolTip(w.status)
        self.live.set_level(None if w.fires is not None else "warning")
        fires = w.fires
        totals = (
            {}
            if fires is None
            else {
                t.key: triggers.times(triggers.total(t, fires)) for t in self._triggers if t.rules
            }
        )
        self.outline.set_fired(
            {} if fires is None else {ALL: triggers.times(sum(fires.values())), **totals}
        )

    def _marked(self, frames: list[FrameSpec]) -> list[FrameSpec]:
        """`frames` with how often the game fired each rule after its title, quiet at none and
        outlined when the count just rose."""
        fires, plan = self.ws.fires, self._plan
        if fires is None or plan is None:
            return frames
        lit, place = self.ws.watch.lit(), {r.id: r.rank for r in plan.rows}
        out = []
        for fr in frames:
            rank = place.get(fr.id)
            if rank is None:  # a rule with no move yet is checked by nothing
                out.append(fr)
                continue
            n = fires.get(rank, 0)
            out.append(
                dataclasses.replace(
                    fr, mark=f" {triggers.DOT} {triggers.times(n)}", muted=n == 0, lit=rank in lit
                )
            )
        return out

    def sync(self) -> None:
        m = self.ws.manifest
        self.pages.show_page(m is not None)
        if m is None:
            return
        if theme.current() is not self._theme:
            self._theme = theme.current()
            self.view.set_roles(ROLES)
            self.view.set_canvas(theme.current().view)
            self._outlined = None
        if self._drawn is not m:
            self._drawn = m
            self._read = graph.read(m)
            self._triggers = model.triggers(m)
            self._opens = {}
            for o in graph.open_rules(m):
                self._opens.setdefault(model.trigger_key(m, o), []).append(o)
            self._scope = None
        block = self.ws.take_block()
        if block in m.behaviour.blocks:
            self._reach(block)
        if self.ws.scope not in (ALL, *(t.key for t in self._triggers)):
            self.ws.scope = ALL  # its trigger is gone
        if self._scope != (self.ws.scope, self.ws.all_view) or self._outlined is None:
            self._draw(m)
        self._follow(m, block)
        self._show_bar()

    def _reach(self, block: str) -> None:
        """A block that a finding named and the scope does not draw: its trigger's rows, or All
        for a block on no rule."""
        key = self.ws.scope
        on_rule = triggers.owner(self._triggers, self._opens, block)
        if key != ALL and not triggers.holds(self._triggers, self._opens, key, block):
            self.ws.scope = on_rule or ALL
        if self.ws.scope == ALL and on_rule is None:
            self.ws.all_view = CANVAS  # a block on no rule is not in the rows

    def _show_bar(self) -> None:
        """The bar shows the one frame or node selected, from the document: a refused edit goes
        back."""
        m, ids = self.ws.manifest, self.view.selected()
        frames = self.view.selected_frames()
        pick = frames[0] if len(frames) == 1 else ids[0] if len(ids) == 1 else None
        self.bar.show_detail(None if m is None or pick is None else self._detail(m, pick))

    def _detail(self, m: Manifest, shown: str) -> Detail | None:
        row = self._plan.row_of(shown) if self._plan is not None else None
        if row is not None and shown == row.id:
            root = m.behaviour.blocks.get(row.path.blocks[0])
            title = "Rule " + (f"#{row.rank}" if row.rank else "(no move yet)")
            return (
                None
                if root is None
                else Detail(
                    shown,
                    title,
                    "A rule: its blocks run left to right, then the move it plays",
                    label=root.label,
                    note_tip="The rule's note, which titles its frame: kept on its first block",
                )
            )
        ref = rows.ref_of(shown)
        if ref is not None:
            return self._ref_detail(m, shown, *ref)
        node = rows.node_of(shown)
        name = graph.move_name(node)
        if name is not None:
            mv = m.moves.get(name)
            if mv is None:
                return None
            return Detail(node, name, self._move_tip(mv), label=mv.label, play=self._playable(name))
        blk = m.behaviour.blocks.get(node)
        if blk is None or self._read is None:
            return None
        k = KINDS[blk.kind]
        values = model.params(blk)
        tip = self._block_tip(k, self._read.refused.get(node))
        return Detail(
            node,
            k.title,
            tip,
            k.params,
            {p.name: values.get(p.name) for p in k.params},
            graph.options(m, blk.kind),
            blk.label,
        )

    def _ref_detail(self, m: Manifest, shown: str, block: str, move: str | None) -> Detail | None:
        """A reference or the placeholder: the picker for the move, and the move's own note."""
        if block not in m.behaviour.blocks or (move is not None and move not in m.moves):
            return None
        if move is None:
            plays = Plays(block, None, tuple(m.moves), "pick a move")
            return Detail(
                shown, "Move", "No move yet: pick one and the rule runs", plays=plays, note=False
            )
        mv = m.moves[move]
        return Detail(
            shown,
            "Move",
            self._move_tip(mv),
            label=mv.label,
            note_tip="A note on this move, kept in the port manifest",
            plays=Plays(block, move, tuple(m.moves)),
            play=self._playable(move),
        )

    @staticmethod
    def _block_tip(k: Kind, why: str | None) -> str:
        return "\n".join([k.tip, *([f"{WARN} {why}"] if why else [])])

    @staticmethod
    def _move_tip(mv: Move) -> str:
        plays = f"plays {mv.clip}" if mv.clip else f"plays anim {mv.anim}"
        return "\n".join([f"{kind(mv)} · {plays}", *([f"Note: {mv.label}"] if mv.label else [])])

    def _draw(self, m: Manifest) -> None:
        r = self._read
        assert r is not None
        scope = self.ws.scope
        total = len(r.paths)
        key = (tuple((t.key, t.ranks) for t in self._triggers), total, scope, theme.current())
        if key != self._outlined:
            self._outlined = key
            self.outline.set_triggers(self._triggers, total, scope)
        blocks, moves = self._blocks(m, r), self._moves(m)
        t = self._current()
        free = t is None and self.ws.all_view == CANVAS
        self._laid = None
        if free:
            self._plan = None
            self._canvas(m, r, blocks + moves)
            loose = f" · {len(r.loose)} loose" if r.loose else ""
            self.title.setText(
                f"{len(m.behaviour.blocks)} blocks · {total} of {model.SEAM_RULES} paths" + loose
            )
        else:
            self._draw_rows(m, r, t, blocks, moves)
            self.title.setText(
                triggers.says(t, total) if t is not None else triggers.says_all(total)
            )
        self.hint.setText(HINT if free else SCOPED_HINT if t is not None else ORDER_HINT)
        self.mode.setVisible(t is None)
        self.mode.set(self.ws.all_view)
        self.spread.setVisible(free)
        self.add_rule.setVisible(t is not None)
        if self._palette != (ALL if free else ""):  # rows take no free blocks
            self._palette = ALL if free else ""
            self.view.set_palette(graph.palette() if free else [])
        self._status()
        here = (scope, self.ws.all_view)
        if self._scope is not None and self._scope != here:
            self.view.fit()
        self._scope = here

    def _canvas(self, m: Manifest, r: graph.Reading, nodes: list[NodeSpec]) -> None:
        """The stored canvas; with a node selected, all but the rules through it is dimmed."""
        wires = graph.wires(m)
        rules = [*r.paths, *(o for os in self._opens.values() for o in os)]
        lit = graph.focus(m, rules, self.view.selected())
        if lit is not None:
            nodes = [dataclasses.replace(n, dim=n.id not in lit.nodes) for n in nodes]
        faded = [] if lit is None else [LinkSpec(*w) for w in wires if w not in lit.wires]
        self.view.show(nodes, [LinkSpec(*w) for w in wires], faded=faded)

    def _focus(self) -> None:
        """The selection changed: the stored canvas follows it."""
        m = self.ws.manifest
        if m is not None and self._read is not None and self._free():
            self._canvas(m, self._read, self._blocks(m, self._read) + self._moves(m))

    def _draw_rows(
        self,
        m: Manifest,
        r: graph.Reading,
        t: Trigger | None,
        blocks: list[NodeSpec],
        moves: list[NodeSpec],
    ) -> None:
        """`t`'s rules as rows (every rule if None): drawn once to measure the nodes, then
        placed."""
        notes = {i: b.label for i, b in m.behaviour.blocks.items()}
        specs, played = {n.id: n for n in blocks}, rows.players(r.paths)
        if t is not None:
            by_move = dict(zip(m.moves, moves, strict=True))
            own = self._opens.get(t.key, [])
            plan = rows.plan(t, specs, notes, by_move, played, own, self._tips(m, played))
        else:
            opens = [(model.trigger_key(m, o), o) for o in graph.open_rules(m)]
            labels = {x.key: triggers.label(x) for x in self._triggers}
            tips = self._tips(m, played)
            plan = rows.plan_all(self._triggers, opens, specs, notes, played, labels, tips)
        self._plan = plan
        links = list(plan.links)
        self.view.show([dataclasses.replace(n, at=(0.0, 0.0)) for n in plan.nodes], links)
        placed, frames = rows.place(plan, self.view.sizes())
        self._laid = (placed, links, frames)
        self.view.show(placed, links, self._marked(frames))

    def _tips(self, m: Manifest, played: Mapping[str, tuple[int, ...]]) -> dict[str, str]:
        """Each move's tip: the rules that play it, by rank and trigger, and its sequence."""
        who = {k: triggers.label(t) for t in self._triggers for k, _ in t.rules}
        return {
            name: triggers.plays_tip(
                name, [(k, who[k]) for k in played.get(name, ())], graph.then(m, name)
            )
            for name in m.moves
        }

    @staticmethod
    def _blocks(m: Manifest, r: graph.Reading) -> list[NodeSpec]:
        out = []
        for i, blk in m.behaviour.blocks.items():
            k = KINDS[blk.kind]
            why = r.refused.get(i)
            marks = [*(f"#{n}" for n in r.priority.get(i, [])), *([WARN] if why else [])]
            note = [f"Note: {blk.label}"] if blk.label else []
            out.append(
                NodeSpec(
                    i,
                    k.title,
                    k.role,
                    blk.at,
                    (graph.IN,),
                    (graph.OUT,),
                    model.summary(m, blk),
                    " ".join(marks),
                    i in r.loose,
                    "\n".join([BehaviourPanel._block_tip(k, why), *note]),
                )
            )
        return out

    @staticmethod
    def _moves(m: Manifest) -> list[NodeSpec]:
        spots = graph.spots(m)
        out = []
        for name, mv in m.moves.items():
            out.append(
                NodeSpec(
                    graph.move_id(name),
                    name,
                    MOVE_ROLE,
                    spots[name],
                    (graph.PLAY,),
                    (graph.WHILE, graph.THEN),
                    tip=BehaviourPanel._move_tip(mv),
                )
            )
        return out

    def _follow(self, m: Manifest, block: str | None) -> None:
        """A block a finding named is picked and framed; a move picked elsewhere is selected."""
        ws = self.ws
        if block in m.behaviour.blocks:
            self.view.select([block])
            self.view.frame([block])
        elif ws.move != self._move and ws.move is not None and ws.move in m.moves:
            self.view.select([graph.move_id(ws.move)])
        self._move = ws.move
