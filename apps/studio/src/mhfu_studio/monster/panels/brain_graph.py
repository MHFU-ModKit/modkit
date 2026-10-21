# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Brain tab's graph pane: one scope of a routine as a tree of nodes (`GraphView(tree=True)`).

`monster/brain_graph.py` plans and places a scope; the pane draws it at the sizes the canvas
measured and tells the user's intents. What the game and the edits add (flags, Follow's marks,
Count's badges, findings) is read from the workspace on every `sync`. Every change is a gesture of
`monster/brain_edit.py` run through `studio.act`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable, Sequence
from functools import partial
from typing import TYPE_CHECKING

from mhfu.em.script import graph as script_graph
from mhfu.em.script.graph import ENTRY, ROOT, Stmt
from mhfu.em.script.opcodes import Shape
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mhfu_studio.monster import brain_edit, brain_game
from mhfu_studio.monster import brain_graph as model
from mhfu_studio.monster.brain import var_of
from mhfu_studio.monster.brain_edit import Gesture, PaletteEntry, Place
from mhfu_studio.monster.panels import brain_names
from mhfu_studio.monster.panels.move_preview import MovePopup
from mhfu_studio.monster.panels.node_graph import FrameSpec, GraphView, LinkSpec, NodeSpec
from mhfu_studio.shell.overlay import RGBA
from mhfu_studio.ui import kit, theme

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

Size = tuple[float, float]
TIMES, ARROW, SEP = "×", "\u25b8", "\u203a"
#: what a box's kind is tinted as: the code colours of `theme` (the catalogue's kinds, and the
#: pane's own entry and join); a test fails on a kind of the catalogue without one
CODE = {
    "move": "action",
    "if": "condition",
    "switch": "condition",
    "pick": "random",
    "call": "flow",
    "jump": "flow",
    "end": "flow",
    "repeat": "flow",
    "target": "state",
    "var": "state",
    "op": "structure",
    "entry": "structure",
    "join": "data",
}
LEVEL_MARK = {"error": "\u2716", "warning": "\u26a0", "info": "\u2139"}
#: a node's flag by what happened to it, the first that holds
INNER = "inner"
LONG_GROUP = 12
"""A palette group with more entries than this is split by the catalogue's theme."""
SLACK = 0.5
"""How much a drawn size may differ from the placed one, in scene units, before a scope is placed
again."""


def floats(c: QColor, alpha: float | None = None) -> RGBA:
    """A colour as the (r, g, b, a) floats the canvas takes."""
    return (c.redF(), c.greenF(), c.blueF(), c.alphaF() if alpha is None else alpha)


def roles() -> dict[str, RGBA]:
    """The tint of each box kind, in the theme."""
    return {kind: floats(theme.code(code)) for kind, code in CODE.items()}


def tags() -> dict[str, RGBA]:
    """The colour of each flag and mark, in the theme."""
    out = {k: floats(theme.edit(k)) for k in theme.EDIT_KINDS}
    out |= {k: floats(theme.mark(k)) for k in theme.MARK_KINDS}
    out[INNER] = floats(theme.edit("edited"), 0.55)
    return out


def split_palette() -> list[tuple[str, list[tuple[str, tuple[PaletteEntry, ...]]]]]:
    """The palette as (group, [(theme, entries)]): a long group by the catalogue's themes, a
    short one under no theme (empty)."""
    out = []
    for group, entries in brain_edit.PALETTE.items():
        if len(entries) <= LONG_GROUP:
            out.append((group, [("", entries)]))
            continue
        by: dict[str, list[PaletteEntry]] = {}
        for e in entries:
            by.setdefault(e.theme, []).append(e)
        out.append((group, [(t, tuple(es)) for t, es in by.items()]))
    return out


def ask_note(parent: QWidget, current: str) -> str | None:
    """The note the user types for a node; None when they cancel. The seam tests replace."""
    text, ok = QInputDialog.getMultiLineText(
        parent, "Note", "A note on this node (blank takes it away):", current
    )
    return text if ok else None


class GraphPane(QWidget):
    """The graph of one scope with its breadcrumb. `show_scope` and `select` are silent; `sync`
    follows the workspace."""

    node_picked = Signal(str)
    """A node or arm was selected on the canvas, by the user."""
    scope_opened = Signal(str, str)
    """A compact block was opened, a breadcrumb clicked or Follow moved the scope: the routine and
    the scope's key, already drawn."""
    link_followed = Signal(str)
    """A link was followed: the routine it leads to; the panel goes there."""

    def __init__(
        self, ws: MonsterWorkspace, studio: Studio, preview: MovePopup | None = None
    ) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        #: a Move node under the mouse in miniature, shared with the operand bar's
        self.preview = preview or MovePopup(ws, studio, self)
        self._at: tuple[int, str, str] | None = None
        self._scene: model.Scene | None = None
        #: what was drawn last (nodes at the origin, frames) and the sizes it was drawn at
        self._key: tuple[object, ...] | None = None
        self._sizes: dict[str, Size] = {}
        self._crumbs: tuple[script_graph.Crumb, ...] = ()
        self._picked: str | None = None
        self._followed: str | None = None
        self._theme = theme.current()
        self._refit = False
        self._palette = False

        self.view = GraphView(tree=True)
        self.view.set_roles(roles())
        self.view.set_tags(tags())
        self.view.picked.connect(self._pick)
        self.view.activated.connect(self._activate)
        self.view.delete_requested.connect(self._delete)
        self.view.create_requested.connect(self._create)
        self.view.menu_requested.connect(self._menu)
        self.view.hovered.connect(self._hover)
        self.trail = QHBoxLayout()
        self.trail.setContentsMargins(0, 0, 0, 0)
        self.trail.setSpacing(2)
        self.status = kit.Elided("", None, role="muted")
        self.fit = kit.button(
            "Fit",
            tip="Frames the whole scope",
            on=lambda: self.view.fit(),
            icon="ph.arrows-out",
        )
        bar = QWidget()
        head = QHBoxLayout(bar)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        head.addLayout(self.trail)
        head.addWidget(self.status, 1)
        head.addWidget(self.fit)
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        col.addWidget(bar)
        col.addWidget(self.view, 1)
        self.empty = kit.Empty(
            "No scope to draw", "Pick a routine in the outline or the code, and a script to read."
        )
        self.pages = kit.Pages(page, self.empty)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.pages)
        self.pages.show_page(False)
        # a scroll area takes the arrow keys, so these are shortcuts, not a key handler
        for keys, make in (("Alt+Up", brain_edit.earlier), ("Alt+Down", brain_edit.later)):
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(partial(self._shift, make))

    # -- what the panel calls -- #

    def show_scope(self, row: int, routine: str, scope: str = "") -> None:
        """Draws `scope` (a routine's own when empty) of `routine`; the canvas is framed when
        it is another scope than the one drawn. Silent."""
        here = (row, routine, scope)
        moved = here != self._at
        self._at = here
        self._draw(refit=moved)

    @property
    def routine(self) -> str | None:
        """The routine drawn."""
        return None if self._at is None else self._at[1]

    def select(self, node: str | None) -> None:
        """Selects `node` where it is drawn (clears the selection when it is not), without
        `node_picked`, and brings it into view."""
        self._picked = node if node is not None and self._drawn(node) else None
        self.view.select([] if self._picked is None else [self._picked])
        if self._picked is not None:
            self._reveal(self._picked)

    def sync(self) -> None:
        """After any change: the theme, then the scope again from what the workspace has now
        (a new build, Follow's marks, Count's badges). A pass that changes nothing draws
        nothing."""
        if theme.current() is not self._theme:
            self._theme = theme.current()
            self.view.set_roles(roles())
            self.view.set_tags(tags())
            self.view.set_canvas(self._theme.view)
            self._key = None
        if self._at is None:
            return
        self._draw()
        self._follow()

    # -- drawing -- #

    def _draw(self, *, refit: bool = False) -> None:
        if self._at is None:
            return
        row, routine, scope = self._at
        header = self.ws.brain_header
        self.ws.brain_built(row)  # takes a build that is done: the one drawn is the newest
        plan = self.ws.brain_plan(row, routine, scope, header)
        lost = None
        if plan is None and scope != ROOT and self.ws.brain_routine_graph(row, routine, header):
            plan = self.ws.brain_plan(row, routine, ROOT, header)  # the scope is gone: its routine
            lost = (routine, ROOT)
            self._at, refit = (row, routine, ROOT), True
        if plan is None:
            self._nothing(row, routine)
            return
        self.pages.show_page(True)
        if refit:
            self._refit, self._key, self._sizes = True, None, {}
        self._place(plan, row)
        if self._picked is not None and not self._drawn(self._picked):
            self._picked = None  # it is in another scope, or gone
        self._crumb(plan.crumbs)
        self._tools(row)
        if self._refit and self.isVisible():
            self._refit = False
            self.view.fit()
        if lost is not None:
            self.scope_opened.emit(*lost)

    def _nothing(self, row: int, routine: str) -> None:
        """No scope to draw: the script is not built yet, failed, or has no such routine. The
        last scope stays up while the next builds."""
        error = self.ws.brain_error(row)
        if self.ws.brain_built(row) is None:
            title, hint = (
                ("The script did not build" if error else "The script is not ready"),
                (error or "It is read in the background; this fills in when it is done."),
            )
        else:
            title, hint = (
                f"No routine {routine}",
                "A change removed it, or it is not in this script.",
            )
        self.empty.say(title, hint)
        if self._scene is None or self.ws.brain_built(row) is not None:
            self._scene, self._key = None, None
            self.pages.show_page(False)

    def _place(self, plan: model.Plan, row: int) -> None:
        """Draws `plan` and, when a node came out another size than it was placed at, places it
        again at the sizes the canvas drew. Draws nothing when nothing differs."""
        scene = plan.place(self._sizes)
        look = _Look(self.ws, row, scene)
        nodes, frames = look.nodes(scene), look.frames(scene)
        key = (tuple(dataclasses.replace(n, at=(0.0, 0.0)) for n in nodes), tuple(frames))
        if key == self._key:
            self._scene = scene
            return
        links = _links(scene)
        self.view.show(nodes, links, frames)
        drawn = self.view.sizes()
        if any(_differs(b.size, drawn[b.id]) for b in scene.nodes):
            scene = plan.place(drawn)
            self.view.show(look.nodes(scene), _links(scene), look.frames(scene))
        self._sizes, self._key, self._scene = drawn, key, scene

    def _crumb(self, crumbs: tuple[script_graph.Crumb, ...]) -> None:
        if crumbs != self._crumbs:
            self._crumbs = crumbs
            while (item := self.trail.takeAt(0)) is not None:
                if (w := item.widget()) is not None:
                    w.deleteLater()
            for k, c in enumerate(crumbs):
                if k:
                    self.trail.addWidget(kit.label(SEP, role="muted", wrap=False))
                if k == len(crumbs) - 1:  # the scope shown now: its name, not a way to it
                    here = kit.label(c.label, role="heading", wrap=False)
                    here.setToolTip("The scope shown now")
                    self.trail.addWidget(here)
                    continue
                b = QToolButton()
                b.setText(c.label)
                b.setAutoRaise(True)
                b.setToolTip(f"Opens {c.label}" + (" and selects its block" if c.node else ""))
                b.clicked.connect(lambda _=False, c=c: self._goto(c.scope, c.node))
                self.trail.addWidget(b)

    def _tools(self, row: int) -> None:
        """The empty canvas's menu offers the palette to a file that takes edits."""
        editable = self.ws.brain_editable(row)
        if editable != self._palette:
            self._palette = editable
            groups = (
                [
                    (f"Add at the end: {group}" + (f" · {t}" if t else ""), _kinds(entries))
                    for group, by in split_palette()
                    for t, entries in by
                ]
                if editable
                else []
            )
            self.view.set_palette(groups)
        pending = self.ws.brain_pending(row)
        self.status.setText("updating…" if pending else "")
        self.status.setToolTip("An edit is being built; this scope shows the last build")

    # -- Follow -- #

    def _follow(self) -> None:
        """With Follow on, the scope follows the monster to its NEXT node when that moves, if
        the node is in the routine shown (the panel goes to another routine itself)."""
        f = self.ws.follow
        node = f.next_node if f.on else None
        if node is None or node == self._followed or self._at is None:
            self._followed = node if f.on else None
            return
        self._followed = node
        row, routine, scope = self._at
        rg = self.ws.brain_routine_graph(row, routine, self.ws.brain_header)
        if rg is None or node not in rg.nodes:
            return
        want = model.scope_for(rg, node, scope)
        if want is not None and want != scope:
            self.show_scope(row, routine, want)
            self.scope_opened.emit(routine, want)
        self.select(node)

    def _reveal(self, node: str) -> None:
        """Scrolls to `node` when it is not in view."""
        rect = self.view.viewer.mapToScene(self.view.viewer.viewport().rect()).boundingRect()
        at = self.view.node_rect(node)
        if at is not None and not rect.contains(at):
            self.view.frame([node])

    # -- the user's intents -- #

    def _pick(self, now: object) -> None:
        """The canvas's selection changed: a node, an arm or nothing. The entry and a join are
        not the script's nodes, so they tell nothing."""
        if not isinstance(now, str):
            self._picked = None
            return
        self._picked = now
        box = self._box(now)
        if box is None or box.kind not in ("entry", "join"):
            self.node_picked.emit(now)

    def _hover(self, node: object) -> None:
        """A Move node under the mouse: its move in miniature, above or below the node."""
        if self._at is not None and isinstance(node, str):
            row = self._at[0]
            pair, near = self.ws.brain_move(row, node), self.view.node_on_screen(node)
            if pair is not None and near is not None:
                self.preview.ask(row, pair, near)
                return
        self.preview.drop()

    def _activate(self, node: str) -> None:
        """Double-click or Return: a compact block opens, a call follows its link."""
        box = self._box(node)
        if box is None:
            return
        if box.compact:
            self._open(box)
            return
        for link in box.links:
            if link.target is not None:
                self.link_followed.emit(link.target)
                return
        if box.links:
            self.status.setText(box.links[0].text)

    def _open(self, box: model.Box) -> None:
        """A compact block's one arm opens; with several the user picks."""
        if len(box.opens) == 1:
            self._goto(box.opens[0].scope)
            return
        menu = QMenu(self)
        self._openers(menu, box)
        menu.popup(QCursor.pos())

    def _openers(self, menu: QMenu, box: model.Box) -> None:
        for o in box.opens:
            a = menu.addAction(f"{o.name} · {_nodes(o.size)}")
            a.setToolTip(f"Opens the {o.name} branch as a scope of its own")
            a.triggered.connect(lambda _=False, s=o.scope: self._goto(s))

    def _goto(self, scope: str, select: str | None = None) -> None:
        if self._at is None:
            return
        row, routine, _ = self._at
        self.show_scope(row, routine, scope)
        self.scope_opened.emit(routine, scope)
        if select is not None:
            self.select(select)
            self.node_picked.emit(select)

    def _shift(self, make: Callable[[str], Gesture]) -> None:
        """Alt+Up and Alt+Down: the picked node one place up or down its list."""
        if self._editable() and self._picked is not None and self._is_stmt(self._picked):
            self._run(make(self._picked))

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if self._refit:
            self._refit = False
            QTimer.singleShot(0, self, self.view.fit)  # once the layout has sized the canvas

    # -- gestures -- #

    def _editable(self) -> bool:
        return self._at is not None and self.ws.brain_editable(self._at[0])

    def _drawn(self, node: str) -> bool:
        """`node` is a node or an arm's frame of the scope shown."""
        scene = self._scene
        return scene is not None and (
            self._box(node) is not None or any(f.id == node for f in scene.frames)
        )

    def _box(self, node: str) -> model.Box | None:
        try:
            return None if self._scene is None else self._scene.box(node)
        except KeyError:
            return None

    def _is_stmt(self, node: str) -> bool:
        scene = self._scene
        return scene is not None and isinstance(scene.plan.rg.nodes.get(node), Stmt)

    def _run(self, g: Gesture) -> None:
        """One gesture, one undo step; a refusal puts its reason in the pane and the status
        bar, and the scope stays as it was."""
        if self._at is None:
            return
        row = self._at[0]
        done: list[bool] = []
        self.studio.act(g.label, lambda: done.append(self.ws.edit_brain(g, row)))()
        refused = not done or not done[0]
        self.status.setText(self.ws.message if refused else "")
        self.status.setToolTip(self.ws.message if refused else "")

    def _end_of(self, scope: str) -> str | None:
        """The place a scope's end is, for `Place.end`: the routine, a block's head (the block's
        id) or an arm."""
        if self._scene is None or self._at is None:
            return None
        rg = self._scene.plan.rg
        if scope == ROOT:
            return rg.name
        s = rg.scopes[scope]
        owner = rg.nodes.get(s.owner) if s.owner is not None else None
        if not isinstance(owner, Stmt) or s.port is None:
            return None
        return owner.ports[s.port].arm or owner.id

    def _create(self, op: str, x: float, y: float) -> None:
        """The empty canvas's menu: a new node at the end of the scope."""
        if self._scene is None or not self._editable():
            return
        end = self._end_of(self._scene.scope)
        entry = next((e for es in brain_edit.PALETTE.values() for e in es if e.op == op), None)
        if end is not None and entry is not None:
            self._run(brain_edit.insert_entry(Place.end(end), entry, self.ws.brain_pairs()))

    def _delete(self, ids: list[str]) -> None:
        """The Delete key: one node, or one arm (a frame selected with what it holds); several
        at once are not one step."""
        if not self._editable():
            return
        scene = self._scene
        if scene is None:
            return
        frames = {f.id: f for f in scene.frames}
        arms = [i for i in ids if i in frames]
        if len(arms) == 1 and set(ids) <= {arms[0], *frames[arms[0]].nodes}:
            ids = arms  # a frame selects what it holds too
        if len(ids) != 1:
            self.status.setText("Select one node to delete")
            return
        node = ids[0]
        if self._is_stmt(node):
            self._run(brain_edit.delete(node))
            return
        for b in scene.nodes:
            if any(p.arm == node for p in b.ports):
                self._run(self._remove(b.id, node))
                return

    def _remove(self, block: str, arm: str) -> Gesture:
        rg = self._scene.plan.rg if self._scene is not None else None
        stmt = None if rg is None else rg.nodes.get(block)
        if isinstance(stmt, Stmt) and stmt.op.shape is Shape.IF:
            return brain_edit.remove_else(arm)
        return brain_edit.remove_arm(arm)

    # -- the node menu -- #

    def _menu(self, node: str, menu: QMenu) -> None:
        """Fills a node's right-click menu: its place in the scope, and with a file that takes
        edits the gestures."""
        scene, box = self._scene, self._box(node)
        if scene is None or box is None or self._at is None:
            return
        rg = scene.plan.rg
        editable = self._editable()
        if box.compact:
            sub = menu.addMenu("Open")
            sub.setToolTipsVisible(True)
            self._openers(sub, box)
        self._links_menu(menu, box)
        if not editable:
            return
        found = rg.nodes.get(node)
        end = self._end_of(scene.scope)
        pairs = self.ws.brain_pairs()

        def insert(place: Place) -> Callable[[PaletteEntry], Gesture]:
            return lambda e: brain_edit.insert_entry(place, e, pairs)

        if isinstance(found, script_graph.Entry):
            self._palette_menu(menu, "Insert at the start", insert(Place.start(rg.name)))
        elif isinstance(found, script_graph.Join):
            self._palette_menu(menu, "Insert after", insert(Place.after(found.owner)))
        elif isinstance(found, Stmt):
            self._palette_menu(menu, "Insert before", insert(Place.before(node)))
            self._palette_menu(menu, "Insert after", insert(Place.after(node)))
        if end is not None:
            self._palette_menu(menu, "Insert at the end of the scope", insert(Place.end(end)))
        if isinstance(found, Stmt):
            self._statement_menu(menu, node, found)
        if box.edited or box.new or box.inner or (node == ENTRY and rg.edited):
            ref = rg.name if node == ENTRY else node
            self._act(
                menu,
                "Back to the game's",
                brain_edit.back_to_game(ref),
                "Undoes the edits to"
                " this node (a routine: to all of it) and what is anchored in it",
            )
        ref = rg.name if node == ENTRY else node
        a = menu.addAction("Note…")
        a.setToolTip("Writes a note on this node, which shows in its tip and the file")
        a.triggered.connect(lambda _=False: self._note(ref))
        self._names_menu(menu, node, found)

    def _names_menu(self, menu: QMenu, node: str, found: object) -> None:
        """Naming what a node names: the routine at the entry, a variable, where a link leads."""
        scene, at = self._scene, self._at
        if scene is None or at is None:
            return
        rg = scene.plan.rg
        asks: list[tuple[str, str, str | int]] = []
        if node == ENTRY:
            asks.append(("Rename routine…", brain_names.ROUTINE, rg.name))
        if isinstance(found, Stmt) and (var := var_of(found.op, found.args)) is not None:
            asks.append((f"Name variable {var}…", brain_names.VARS, var))
        box = self._box(node)
        for link in () if box is None else box.links:
            if link.target is not None and link.target != rg.name:
                asks.append((f"Rename {link.target}…", brain_names.ROUTINE, link.target))
        for title, table, key in dict.fromkeys(asks):
            a = menu.addAction(title)
            a.setToolTip("A name shown wherever the script names it; the brain file keeps it")
            a.triggered.connect(
                lambda _=False, t=table, k=key: brain_names.rename(
                    self, self.ws, self.studio, at[0], t, k
                )
            )

    def _links_menu(self, menu: QMenu, box: model.Box) -> None:
        for link in box.links:
            if link.target is None:
                continue
            a = menu.addAction(f"Follow: {link.text}")
            a.setToolTip("Goes to that routine; Back in the code returns")
            a.triggered.connect(lambda _=False, t=link.target: self.link_followed.emit(t))

    def _statement_menu(self, menu: QMenu, node: str, stmt: Stmt) -> None:
        self._act(menu, "Delete", brain_edit.delete(node), "Takes this node, and all in it, away")
        self._palette_menu(menu, "Replace with…", lambda e: brain_edit.replace(node, e.op))
        self._act(
            menu,
            "Move up (Alt+Up)",
            brain_edit.earlier(node),
            "Swaps this node with the one above it in its list",
        )
        self._act(
            menu,
            "Move down (Alt+Down)",
            brain_edit.later(node),
            "Swaps this node with the one below it in its list",
        )
        picked = self.view.selected()
        refs = picked if node in picked else [node]
        wrap = menu.addMenu("Wrap in")
        wrap.setToolTipsVisible(True)
        self._palette_menu(
            wrap,
            "an if",
            lambda e: brain_edit.wrap_if(refs, e.op),
            groups=("Conditions",),
        )
        self._act(
            wrap,
            "a pick",
            brain_edit.wrap_pick(refs),
            "Puts the selected nodes in the first branch of"
            " a new two-way pick; they must follow each other in one list",
        )
        if stmt.block:
            self._arms_menu(menu, node, stmt)

    def _arms_menu(self, menu: QMenu, node: str, stmt: Stmt) -> None:
        shape = stmt.op.shape
        if shape is Shape.REPEAT:
            return
        if shape is Shape.IF:
            if any(p.scope is None and not p.head for p in stmt.ports):
                self._act(
                    menu, "Add else", brain_edit.add_else(node), "Gives this if an else branch"
                )
        else:
            self._act(menu, "Add arm", brain_edit.add_arm(node), "Adds a branch to this block")
            if shape is Shape.SWITCH:
                self._act(
                    menu,
                    "Add default",
                    brain_edit.add_arm(node, default=True),
                    "Adds the branch taken when no case matches",
                )
        arms = [p for p in stmt.ports if p.arm]
        if arms:
            sub = menu.addMenu("Remove")
            sub.setToolTipsVisible(True)
            for p in arms:
                label = f"{p.name} ({_nodes(p.size)})"
                self._act(
                    sub, label, self._remove(node, p.arm), "Takes this branch, and all in it, away"
                )

    def _act(self, menu: QMenu, title: str, g: Gesture, tip: str) -> None:
        a = menu.addAction(title)
        a.setToolTip(tip)
        a.triggered.connect(lambda _=False: self._run(g))

    def _palette_menu(
        self,
        parent: QMenu,
        title: str,
        make: Callable[[PaletteEntry], Gesture],
        groups: Iterable[str] | None = None,
    ) -> None:
        """A submenu of the palette by group (and the catalogue's theme in a long one); a pick
        runs `make`. `groups` limits it to those groups, and then names none of them."""
        sub = parent.addMenu(title)
        sub.setToolTipsVisible(True)
        for group, by in split_palette():
            if groups is not None and group not in groups:
                continue
            top = sub
            if groups is None:
                top = sub.addMenu(group)
                top.setToolTipsVisible(True)
            for t, entries in by:
                menu = top.addMenu(t) if t else top
                menu.setToolTipsVisible(True)
                for e in entries:
                    a = menu.addAction(e.title)
                    a.setToolTip(e.meaning)
                    a.triggered.connect(lambda _=False, e=e: self._run(make(e)))

    def _note(self, ref: str) -> None:
        if self._at is None:
            return
        row = self._at[0]
        text = ask_note(self, self.ws.brain_notes(row).get(ref, ""))
        if text is not None:
            self._run(brain_edit.set_note(ref, text))


def _nodes(n: int) -> str:
    return "empty" if n == 0 else f"{n} node" + "s" * (n != 1)


def _kinds(entries: Sequence[PaletteEntry]) -> list[tuple[str, str, str]]:
    return [(e.op, e.title, e.meaning) for e in entries]


def _differs(a: Size, b: Size) -> bool:
    return abs(a[0] - b[0]) > SLACK or abs(a[1] - b[1]) > SLACK


def _links(scene: model.Scene) -> list[LinkSpec]:
    return [LinkSpec(w.src, w.src_port, w.dst, w.dst_port) for w in scene.wires]


class _Look:
    """What the workspace adds to a scope as it is now: Follow's marks, Count's badges and what
    never ran, the file's notes. It turns a scene into the specs the canvas draws."""

    def __init__(self, ws: MonsterWorkspace, row: int, scene: model.Scene) -> None:
        self.rg = scene.plan.rg
        self.notes = ws.brain_notes(row)
        counted = ws.count.result
        self.count = None if ws.count.stale else counted
        self.marks = _marks(ws, row, scene)
        self.ports = _port_keys(scene)

    def nodes(self, scene: model.Scene) -> list[NodeSpec]:
        return [self.node(b) for b in scene.nodes]

    def node(self, b: model.Box) -> NodeSpec:
        n = self.count
        ran = None if n is None else (n.nodes.get(b.id) if b.id != ENTRY else self._starts(n))
        never = n is not None and b.id in n.never
        badge = " ".join(
            [
                *([f"{TIMES}{ran}"] if ran is not None else []),
                *(
                    [f"{LEVEL_MARK.get(b.level or '', LEVEL_MARK['warning'])}{b.findings}"]
                    if b.findings
                    else []
                ),
            ]
        )
        kinds = self.marks.get(b.id, ())
        link = b.badge
        if b.compact:
            link = f"{ARROW} " + " · ".join(f"{o.name} {o.size}" for o in b.opens)
        return NodeSpec(
            b.id,
            b.title,
            b.kind,
            b.at,
            b.inputs,
            b.outputs,
            b.summary,
            badge,
            b.dead or never,
            self._tip(b, ran, never),
            captions=self._captions(b),
            link=link,
            flag=_flag(b),
            marks=tuple(k for k, _ in kinds),
        )

    def _starts(self, n: brain_game.Counted) -> int | None:
        total = n.routines.get(self.rg.name)
        return None if total is None else total.starts

    def _captions(self, b: model.Box) -> tuple[str, ...]:
        if not b.ports:
            return ()
        got = self.count
        out = []
        for p in b.ports:
            taken = None if got is None else got.taken.get(self.ports[b.id][p.key])
            out.append(p.name + (f" {TIMES}{taken}" if taken is not None else ""))
        return (*out, *([""] if b.exit else []))

    def _tip(self, b: model.Box, ran: int | None, never: bool) -> str:
        lines = [b.tip]
        lines += [link.text for link in b.links]
        if b.compact:
            lines.append("Double-click to open its branches")
        elif any(link.target for link in b.links):
            lines.append("Double-click or Return follows the link")
        if note := self.notes.get(b.id):
            lines.append(f"Note: {note}")
        if ran is not None:
            lines.append(f"Ran {ran} times in the count")
        elif never:
            lines.append("Did not run in the count")
        lines += [label for _, label in self.marks.get(b.id, ()) if label]
        return "\n".join(lines)

    def frames(self, scene: model.Scene) -> list[FrameSpec]:
        got = self.count
        out = []
        for f in scene.frames:
            taken = None if got is None else got.taken.get(f.id)
            mark = (f" {brain_game.DOT} {TIMES}{taken}" if taken is not None else "") + (
                f" {brain_game.DOT} edited" if f.edited else ""
            )
            out.append(
                FrameSpec(
                    f.id, f.title, f.nodes, f.role, mark=mark, flag="edited" if f.edited else ""
                )
            )
        return out


def _flag(b: model.Box) -> str:
    """How a node was changed: dead code, new code, an edited node, or a block with edits in it."""
    if b.dead:
        return "dead"
    if b.new:
        return "new"
    if b.edited:
        return "edited"
    return INNER if b.inner else ""


def _port_keys(scene: model.Scene) -> dict[str, dict[str, str]]:
    """For each block drawn with ports, the key `Counted.taken` has for each port."""
    rg = scene.plan.rg
    out: dict[str, dict[str, str]] = {}
    for b in scene.nodes:
        stmt = rg.nodes.get(b.id)
        if b.ports and isinstance(stmt, Stmt):
            out[b.id] = {
                p.key: brain_game.port_key(b.id, gp)
                for p, gp in zip(b.ports, stmt.ports, strict=True)
            }
    return out


def _marks(
    ws: MonsterWorkspace, row: int, scene: model.Scene
) -> dict[str, tuple[tuple[str, str], ...]]:
    """Follow's marks by node: (kind, label) in the order a line shows them. A node inside a
    compact block marks the block."""
    f = ws.follow
    if not f.on:
        return {}
    view = ws.brain_view(row, ws.brain_header)
    by: dict[str, list[tuple[str, str]]] = {}
    if view is not None:
        for at, here in f.marks.items():
            node = view.lines[at].node if 0 <= at < len(view.lines) else None
            if node is not None:
                by.setdefault(node, []).extend((m.kind, m.label) for m in here)
    if f.next_node is not None and not any(
        k == brain_game.NEXT for k, _ in by.get(f.next_node, ())
    ):
        by.setdefault(f.next_node, []).append((brain_game.NEXT, ""))
    drawn = {b.id: b for b in scene.nodes}
    out: dict[str, list[tuple[str, str]]] = {}
    covers = _covers(scene)
    for node, marks in by.items():
        for target in [node] if node in drawn else [covers[node]] if node in covers else []:
            out.setdefault(target, []).extend(marks)
    order = {k: i for i, k in enumerate(brain_game.KINDS)}
    return {
        n: tuple(sorted(dict.fromkeys(ms), key=lambda m: order.get(m[0], len(order))))
        for n, ms in out.items()
    }


def _covers(scene: model.Scene) -> dict[str, str]:
    """The compact block that stands for each node inside it."""
    rg = scene.plan.rg
    out: dict[str, str] = {}

    def inside(block: Stmt) -> Iterable[str]:
        for p in block.ports:
            if p.scope is None:
                continue
            for sid in rg.scopes[p.scope].body:
                yield sid
                n = rg.nodes[sid]
                if isinstance(n, Stmt):
                    yield from inside(n)
                    yield from (p.arm for p in n.ports if p.arm)

    for b in scene.nodes:
        stmt = rg.nodes.get(b.id)
        if b.compact and isinstance(stmt, Stmt):
            for nid in inside(stmt):
                out[nid] = b.id
            out.update({p.arm: b.id for p in stmt.ports if p.arm})
    return out
