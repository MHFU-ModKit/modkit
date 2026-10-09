# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Behaviour dock: the port's behaviour graph (`mhfu_port.behaviour`) on a node canvas.

A node is a move or a block; a wire is a link the manifest stores (`monster/behaviour.py`). The
document is the owner: every gesture of the canvas arrives as intents, is applied as one undo
step, and the canvas is shown again from the document.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from mhfu_port import behaviour as model
from mhfu_port.behaviour import KINDS
from mhfu_port.manifest import Manifest, ManifestError
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from mhfu_studio.monster import behaviour as graph
from mhfu_studio.monster.panels.common import kind
from mhfu_studio.monster.panels.node_graph import GraphView, LinkSpec, NodeSpec
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
        v.link_requested.connect(lambda *w: self._collect(lambda e: graph.link(e, *w)))
        v.unlink_requested.connect(lambda *w: self._collect(lambda e: graph.unlink(e, *w)))
        v.delete_requested.connect(lambda ids: self._collect(lambda e: graph.delete(e, ids)))
        v.param_changed.connect(
            lambda i, n, x: self._collect(lambda e: graph.set_param(e, i, n, x))
        )
        v.label_changed.connect(lambda i, t: self._collect(lambda e: graph.set_label(e, i, t)))
        v.moved.connect(lambda i, x, y: self._dropped.__setitem__(i, (x, y)))
        v.create_requested.connect(self._create)
        v.picked.connect(self._picked)
        v.settled.connect(self._settle)

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        lay.addWidget(kit.row(self.title, self.hint, self.fit, self.spread, stretch=True))
        lay.addWidget(self.view, 1)
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

    # what the canvas tells

    def _collect(self, fn: Callable[[graph.Edits], str]) -> None:
        doc = self.ws.doc
        if doc is None:
            return
        if self._gesture is None:
            self._gesture = graph.Gesture(doc)
        self._gesture.run(fn)

    def _create(self, kind_: str, x: float, y: float) -> None:
        doc = self.ws.doc
        if doc is not None:
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
        self.studio.act("edit behaviour", lambda: self.ws.edit("", done.commit))()
        m = self.ws.manifest
        if made is not None and m is not None and made in m.behaviour.blocks:
            self.view.select([made])

    def _picked(self, node: object) -> None:
        """A move node picks its move, as the Moves table does."""
        name = graph.move_name(node) if isinstance(node, str) else None
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
        if self._drawn is not m:
            self._drawn = m
            self._draw(m)
        self._follow(m)

    def _draw(self, m: Manifest) -> None:
        r = graph.read(m)
        nodes = self._blocks(m, r) + self._moves(m)
        self.view.show(nodes, [LinkSpec(*w) for w in graph.wires(m)])
        loose = f" · {len(r.loose)} loose" if r.loose else ""
        self.title.setText(
            f"{len(m.behaviour.blocks)} blocks · {len(r.paths)} of {model.SEAM_RULES} paths" + loose
        )

    @staticmethod
    def _blocks(m: Manifest, r: graph.Reading) -> list[NodeSpec]:
        out = []
        for i, blk in m.behaviour.blocks.items():
            k = KINDS[blk.kind]
            why = r.refused.get(i)
            marks = [*(f"#{n}" for n in r.priority.get(i, [])), *([WARN] if why else [])]
            values = model.params(blk)
            options: dict[str, list[tuple[str, object]]] = {}
            for p in k.params:
                if p.type == "part":
                    options[p.name] = list(graph.part_options(m, p.optional))
                elif p.type == "mains":
                    options[p.name] = list(graph.main_options())
            out.append(
                NodeSpec(
                    i,
                    k.title,
                    k.role,
                    blk.at,
                    (graph.IN,),
                    (graph.OUT,),
                    k.params,
                    {p.name: values.get(p.name) for p in k.params},
                    options,
                    " ".join(marks),
                    i in r.loose,
                    blk.label,
                    "\n".join([k.tip, *([f"{WARN} {why}"] if why else [])]),
                )
            )
        return out

    @staticmethod
    def _moves(m: Manifest) -> list[NodeSpec]:
        spots = graph.spots(m)
        out = []
        for name, mv in m.moves.items():
            plays = f"plays {mv.clip}" if mv.clip else f"plays anim {mv.anim}"
            out.append(
                NodeSpec(
                    graph.move_id(name),
                    name,
                    MOVE_ROLE,
                    spots[name],
                    (graph.PLAY,),
                    (graph.WHILE, graph.THEN),
                    label=mv.label,
                    tip=f"{kind(mv)} · {plays}",
                )
            )
        return out

    def _follow(self, m: Manifest) -> None:
        """A block a finding named is picked and framed; a move picked elsewhere is selected."""
        ws = self.ws
        block = ws.take_block()
        if block in m.behaviour.blocks:
            self.view.select([block])
            self.view.frame([block])
        elif ws.move != self._move and ws.move is not None and ws.move in m.moves:
            self.view.select([graph.move_id(ws.move)])
        self._move = ws.move
