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
from collections.abc import Callable
from typing import TYPE_CHECKING

from mhfu_port import behaviour as model
from mhfu_port.behaviour import KINDS, Kind, Trigger
from mhfu_port.manifest import Manifest, ManifestError, Move
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from mhfu_studio.monster import behaviour as graph
from mhfu_studio.monster.behaviour import ALL
from mhfu_studio.monster.panels import rows, triggers
from mhfu_studio.monster.panels.common import kind
from mhfu_studio.monster.panels.node_graph import GraphView, LinkSpec, NodeSpec
from mhfu_studio.monster.panels.properties import Detail, PropertyBar
from mhfu_studio.monster.panels.widgets import NoScene
from mhfu_studio.shell.overlay import Ink
from mhfu_studio.ui import kit, theme

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

#: a node's colour by role; a move has a role of its own
ROLES = {
    "event": Ink.AXIS_X,
    "state": Ink.AXIS_Y,
    "condition": Ink.AXIS_Z,
    "modifier": Ink.WARNING,
    "move": Ink.SELECTION,
}
MOVE_ROLE = "move"
WARN = chr(0x26A0)
HINT = "Right-click adds a block · drag from a port to wire · higher blocks are checked first"
#: said for the intents a trigger's rows do not take yet, and over the rows
SCOPED = "Create and wire in All for now"


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
        self._scope: str | None = None
        self._outlined: object = None

        self.title = kit.label(role="title", wrap=False)
        self.hint = kit.label(HINT, role="muted", wrap=False)
        self.hint.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.fit = kit.button(
            "Fit",
            tip="Frames every node",
            on=lambda: self.view.fit(),
            icon="ph.arrows-out",
        )
        self.spread = kit.button(
            "Spread out",
            tip="Moves every node away from the top left so crowded ones stop overlapping; which"
            " block is higher stays so, and so does every path's priority",
            on=studio.act("spread nodes", lambda: ws.edit("", self._spread)),
            icon="ph.arrows-out-simple",
        )
        self.view = GraphView()
        self.view.set_palette(graph.palette())
        v = self.view
        v.link_requested.connect(lambda *w: self._collect(self._wire(graph.link, *w)))
        v.unlink_requested.connect(lambda *w: self._collect(self._wire(graph.unlink, *w)))
        v.delete_requested.connect(self._delete)
        v.moved.connect(self._moved)
        v.create_requested.connect(self._create)
        v.picked.connect(self._picked)
        v.settled.connect(self._settle)
        self.bar = PropertyBar()
        self.bar.param_changed.connect(self._param)
        self.bar.label_changed.connect(self._label)

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
        lay.addWidget(kit.row(self.title, self.hint, self.fit, self.spread, stretch=True))
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

    # what the bar tells: the same intents as the canvas's, one undo step each

    def _param(self, node: str, name: str, value: object) -> None:
        self._collect(lambda e: graph.set_param(e, node, name, value))
        self._settle()

    def _label(self, node: str, text: str) -> None:
        self._collect(lambda e: graph.set_label(e, node, text))
        self._settle()

    # what the canvas tells

    def _collect(self, fn: Callable[[graph.Edits], str]) -> None:
        doc = self.ws.doc
        if doc is None:
            return
        if self._gesture is None:
            self._gesture = graph.Gesture(doc)
        self._gesture.run(fn)

    def _scope_all(self) -> bool:
        return self.ws.scope == ALL

    def _wire(self, edit: Callable[..., str], *wire: str) -> Callable[[graph.Edits], str]:
        """The edit of a wire intent, or the refusal a trigger's rows give for it."""
        return (lambda e: edit(e, *wire)) if self._scope_all() else self._not_here

    @staticmethod
    def _not_here(_: graph.Edits) -> str:
        raise ManifestError(SCOPED)

    def _delete(self, ids: list[str]) -> None:
        nodes = list(dict.fromkeys(rows.node_of(i) for i in ids))  # a reference is its move
        self._collect(lambda e: graph.delete(e, nodes))

    def _moved(self, node: str, x: float, y: float) -> None:
        if self._scope_all():  # the rows are laid out for display: a drag snaps back
            self._dropped[node] = (x, y)

    def _create(self, kind_: str, x: float, y: float) -> None:
        doc = self.ws.doc
        if doc is None:
            return
        if not self._scope_all():
            self._collect(self._not_here)
            return
        self._made = model.new_id(doc.manifest.behaviour)
        self._collect(lambda e: graph.add_block(e, kind_, (x, y)))

    def _settle(self) -> None:
        """The gesture is whole: one undo step, or one refusal."""
        doc, gesture, dropped = self.ws.doc, self._gesture, self._dropped
        made, self._gesture, self._dropped, self._made = self._made, None, {}, None
        if doc is None:
            return
        if dropped:
            gesture = gesture or graph.Gesture(doc)
            gesture.run(lambda e: graph.move_nodes(e, dropped))
        if gesture is None:
            return
        done = gesture

        def edit() -> None:
            self.ws.edit("", done.commit)
            self.sync()  # the window syncs a turn on: after the view would have put the canvas back

        self.studio.act("edit behaviour", edit)()
        m = self.ws.manifest
        if made is not None and m is not None and made in m.behaviour.blocks:
            self.view.select([made])
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
            self._scope = None
        block = self.ws.take_block()
        if block in m.behaviour.blocks:
            self._reach(block)
        if self.ws.scope not in (ALL, *(t.key for t in self._triggers)):
            self.ws.scope = ALL  # its trigger is gone
        if self._scope != self.ws.scope or self._outlined is None:
            self._draw(m)
        self._follow(m, block)
        self._show_bar()

    def _reach(self, block: str) -> None:
        """A block that a finding named and the scope does not draw: its trigger's rows, or All
        for a block on no rule."""
        scope = next((t for t in self._triggers if t.key == self.ws.scope), None)
        if scope is not None and not any(block in p.blocks for _, p in scope.rules):
            self.ws.scope = triggers.owner(self._triggers, block) or ALL

    def _show_bar(self) -> None:
        """The bar shows the one node selected, from the document: a refused edit goes back."""
        m, ids = self.ws.manifest, self.view.selected()
        self.bar.show_detail(self._detail(m, ids[0]) if m is not None and len(ids) == 1 else None)

    def _detail(self, m: Manifest, node: str) -> Detail | None:
        node = rows.node_of(node)
        name = graph.move_name(node)
        if name is not None:
            mv = m.moves.get(name)
            return None if mv is None else Detail(node, name, self._move_tip(mv), label=mv.label)
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
        t = next((t for t in self._triggers if t.key == scope), None)
        if t is None:
            self.view.show(blocks + moves, [LinkSpec(*w) for w in graph.wires(m)])
            loose = f" · {len(r.loose)} loose" if r.loose else ""
            self.title.setText(
                f"{len(m.behaviour.blocks)} blocks · {total} of {model.SEAM_RULES} paths" + loose
            )
        else:
            self._draw_rows(m, r, t, blocks, moves)
            self.title.setText(triggers.says(t, total))
        self.hint.setText(HINT if t is None else SCOPED)
        self.spread.setEnabled(t is None)
        if self._scope is not None and self._scope != scope:
            self.view.fit()
        self._scope = scope

    def _draw_rows(
        self,
        m: Manifest,
        r: graph.Reading,
        t: Trigger,
        blocks: list[NodeSpec],
        moves: list[NodeSpec],
    ) -> None:
        """`t`'s rules as rows: drawn once to measure the nodes, then placed."""
        notes = {i: b.label for i, b in m.behaviour.blocks.items()}
        plan = rows.plan(
            t,
            {n.id: n for n in blocks},
            notes,
            dict(zip(m.moves, moves, strict=True)),
            rows.players(r.paths),
        )
        links = list(plan.links)
        self.view.show([dataclasses.replace(n, at=(0.0, 0.0)) for n in plan.nodes], links)
        placed, frames = rows.place(plan, self.view.sizes())
        self.view.show(placed, links, frames)

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
