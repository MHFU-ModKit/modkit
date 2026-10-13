# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A node-graph view over NodeGraphQt that never owns the graph: the owner's document does.

The owner calls `GraphView.show(nodes, links)` after every edit (undo too); the view diffs by
node id and moves only what differs. A gesture comes out as an intent signal, one event-loop turn
late (so the owner may rebuild whatever the gesture touched), and the view holds the gesture's
change only until then: when no `show` came, the view goes back to what it last showed. A
refused edit therefore needs no code.

A node is compact: its title and a line or two of summary, no editors; what a node holds is
edited in the property bar (`properties.py`). NodeGraphQt's own model, commands and undo are
bypassed: its nodes are only items in a scene.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from NodeGraphQt import BackdropNode, BaseNode, NodeGraph, Port
from NodeGraphQt.qgraphics.node_base import NodeItem
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QInputDevice,
    QKeyEvent,
    QNativeGestureEvent,
    QPainter,
    QPen,
    QShowEvent,
    QUndoCommand,
    QUndoStack,
    QWheelEvent,
)
from PySide6.QtWidgets import QApplication, QMenu, QStyleOptionGraphicsItem, QVBoxLayout, QWidget

from mhfu_studio.shell.overlay import Color, Ink
from mhfu_studio.ui import theme

Point = tuple[float, float]
#: a palette group: its title, and its kinds as (kind, title, tip)
PaletteGroup = tuple[str, Sequence[tuple[str, str, str]]]
#: a node's least width, the lines of summary it shows, and the room left of them
NODE_W, SUMMARY_LINES, SUMMARY_X = 168.0, 2, 14.0
#: a frame's room round its nodes: the sides, the title bar with the gap under it, the bottom
FRAME_PAD, FRAME_TOP, FRAME_BOTTOM = 18.0, 38.0, 14.0
#: a scroll without pixels moves this many pixels per angle unit (a notch of 120 is ~3 lines)
_ANGLE_PX = 0.5
#: the zoom per angle unit of a wheel, as NodeGraphQt has it, and the scale the canvas keeps to
_WHEEL = 1.001
_SCALE = (0.05, 3.0)
DIM, STAND_IN, FADED = 0.35, 0.8, 0.2
"""The opacity of a dimmed node, of a stand-in node, and of a wire faded back."""


@dataclass(frozen=True)
class NodeSpec:
    """One node as the owner wants it drawn: a title, a `summary` of a line or two under it, a
    `badge` in its title bar, the `tip` it carries; `dim` draws it faded; `ref` draws a stand-in
    for a node shown elsewhere: dashed and a little faded."""

    id: str
    title: str
    role: str
    at: Point
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    summary: str = ""
    badge: str = ""
    dim: bool = False
    tip: str = ""
    ref: bool = False


@dataclass(frozen=True)
class FrameSpec:
    """A titled frame round the nodes `nodes` (ids), tinted by `role`."""

    id: str
    title: str
    nodes: tuple[str, ...]
    role: str


@dataclass(frozen=True)
class LinkSpec:
    src: str
    src_port: str
    dst: str
    dst_port: str


# ---- NodeGraphQt, cut down to a scene of items -------------------------------------------- #


class _NoUndo(QUndoStack):
    """NodeGraphQt's undo stack, holding nothing: a command runs and is dropped."""

    def push(self, cmd: QUndoCommand) -> None:
        cmd.redo()

    def beginMacro(self, text: str) -> None:  # noqa: N802
        pass

    def endMacro(self) -> None:  # noqa: N802
        pass


class _Navigation(QObject):
    """The canvas as macOS canvas apps move: two-finger scroll pans; a pinch, Ctrl/Cmd+scroll and
    a mouse wheel zoom, within `_SCALE`. NodeGraphQt's own wheel zoom has no limit."""

    def __init__(self, viewer: Any) -> None:
        super().__init__(viewer)
        self._viewer = viewer
        viewer.viewport().installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if isinstance(event, QWheelEvent):
            if self._pad(event):
                self._pan(event.pixelDelta(), event.angleDelta())
            else:
                angle = event.angleDelta()
                self._zoom(_WHEEL ** (angle.y() or angle.x()), event.position().toPoint())
            return True
        if (
            isinstance(event, QNativeGestureEvent)
            and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture
        ):
            self._zoom(1.0 + event.value(), event.position().toPoint())
            return True
        return False

    @staticmethod
    def _pad(ev: QWheelEvent) -> bool:
        """A scroll from a touchpad (or any device sending pixels or phases), not zooming."""
        if ev.modifiers() & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier
        ):
            return False
        return (
            not ev.pixelDelta().isNull()
            or ev.phase() != Qt.ScrollPhase.NoScrollPhase
            or ev.pointingDevice().type() == QInputDevice.DeviceType.TouchPad
        )

    def _pan(self, pixels: QPoint, angle: QPoint) -> None:
        """The content follows the fingers: the scene rect moves against them."""
        dx, dy = (
            (pixels.x(), pixels.y())
            if not pixels.isNull()
            else (angle.x() * _ANGLE_PX, angle.y() * _ANGLE_PX)
        )
        v = self._viewer
        a, b = v.mapToScene(QPoint(0, 0)), v.mapToScene(QPoint(round(dx), round(dy)))
        x, y, w, h = v.scene_rect()
        v.set_scene_rect([x + a.x() - b.x(), y + a.y() - b.y(), w, h])

    def _zoom(self, factor: float, at: QPoint) -> None:
        """Scales about `at` (view pixels); a canvas past a limit may still come back."""
        v = self._viewer
        now = v.transform().m11()
        lo, hi = min(_SCALE[0], now), max(_SCALE[1], now)
        f = min(max(factor, lo / now), hi / now)
        v.scale(f, f, v.mapToScene(at))


def _tuples(body: QColor, edge: QColor) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """A node's body and edge colours as the (r, g, b, a) tuples NodeGraphQt takes."""
    return (
        (body.red(), body.green(), body.blue(), 255),
        (edge.red(), edge.green(), edge.blue(), 255),
    )


def wrap(text: str, metrics: QFontMetricsF, width: float, lines: int = SUMMARY_LINES) -> list[str]:
    """`text` in at most `lines` lines of `width`: words kept, the last line elided."""
    out: list[str] = []
    line = ""
    words = text.split()
    for k, word in enumerate(words):
        tried = f"{line} {word}".strip()
        if metrics.horizontalAdvance(tried) <= width or not line:
            line = tried
            continue
        out.append(line)
        line = word
        if len(out) == lines - 1:
            line = " ".join(words[k:])
            break
    if line:
        out.append(metrics.elidedText(line, Qt.TextElideMode.ElideRight, width))
    return out[:lines]


class _Item(NodeItem):  # type: ignore[misc]
    """A node's drawing: a summary under the title, a badge in the title bar, a dashed edge for a
    reference, and no renaming. It overrides `calc_size` (room for the summary, at least
    `NODE_W` wide), `paint` (draws those) and `mouseDoubleClickEvent`."""

    badge = ""
    summary = ""
    ref = False
    #: the node's body and edge colours, for a reference's dashed edge
    body: QColor | None = None
    edge: QColor | None = None

    @staticmethod
    def _font() -> QFont:
        font = QFont(QApplication.font())
        font.setPointSizeF(9.0)
        return font

    def _lines(self, width: float) -> list[str]:
        return wrap(self.summary, QFontMetricsF(self._font()), width - 2 * SUMMARY_X)

    def _top(self) -> float:
        return float(self.text_item.boundingRect().height()) + 6.0

    def calc_size(self, add_w: float = 0.0, add_h: float = 0.0) -> tuple[float, float]:
        width, height = super().calc_size(add_w, add_h)
        width = max(float(width), NODE_W)
        if not self.summary:
            return width, float(height)
        body = len(self._lines(width)) * QFontMetricsF(self._font()).height()
        return width, max(float(height), self._top() + body + 8.0)

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None
    ) -> None:
        super().paint(painter, option, widget)
        if self.ref and self.body is not None and self.edge is not None:
            area = QRectF(self.boundingRect()).adjusted(1.0, 1.0, -1.0, -1.0)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(self.body, 3.0))  # over the solid edge, then dashed
            painter.drawRoundedRect(area, 3.0, 3.0)
            painter.setPen(QPen(self.edge, 1.6, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(area, 3.0, 3.0)
        if self.summary:
            painter.setFont(self._font())
            painter.setPen(theme.color(Ink.TEXT))
            metrics = painter.fontMetrics()
            for k, text in enumerate(self._lines(self.width)):
                y = self._top() + (k + 1) * metrics.height() - metrics.descent()
                painter.drawText(QPointF(SUMMARY_X, y), text)
        if self.badge:
            font = QFont(painter.font())
            font.setBold(True)
            font.setPointSizeF(8.0)
            painter.setFont(font)
            painter.setPen(theme.color(Ink.TEXT))
            area = QRectF(self.boundingRect()).adjusted(0.0, 3.0, -8.0, 0.0)
            painter.drawText(
                area, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, self.badge
            )

    def mouseDoubleClickEvent(self, event: Any) -> None:  # noqa: N802
        event.accept()  # NodeGraphQt would edit the title here; a title is the owner's


class _Node(BaseNode):  # type: ignore[misc]
    __identifier__ = "mhfu.studio"
    NODE_NAME = "node"

    def __init__(self) -> None:
        super().__init__(qgraphics_item=_Item)


#: the type NodeGraphQt files a `_Node` under, for its context menu
NODE_TYPE = f"{_Node.__identifier__}.{_Node.__name__}"


# ---- the view ----------------------------------------------------------------------------- #


@dataclass
class _Shown:
    """One node on the canvas, and what it was built from."""

    node: Any
    sig: tuple[object, ...]
    ins: dict[str, Any]
    outs: dict[str, Any]
    #: NodeGraphQt's own body and border colours, for a role with none
    base: tuple[Any, Any]
    tint: tuple[Any, Any] | None = None
    title: str | None = None

    @property
    def view(self) -> Any:
        return self.node.view


def _signature(spec: NodeSpec) -> tuple[object, ...]:
    """What a node is built from: a change of it rebuilds the node, any other updates it."""
    return spec.inputs, spec.outputs


class GraphView(QWidget):
    """The canvas. Signals are the user's intents; none fires from `show`."""

    link_requested = Signal(str, str, str, str)
    unlink_requested = Signal(str, str, str, str)
    delete_requested = Signal(list)
    #: once per node at a drag's end, and once per frame (its top left)
    moved = Signal(str, float, float)
    frame_moved = Signal(str, float, float)
    create_requested = Signal(str, float, float)
    #: the id of the one frame selected, else of the one node, else `None`
    picked = Signal(object)
    #: the ids of the nodes selected, after any change of them
    selection_changed = Signal(list)
    #: a node was right-clicked: its id, and its (emptied) menu for the owner to fill
    menu_requested = Signal(str, QMenu)
    #: after the intents of one gesture, all of them: the owner applies what it collected and
    #: shows, still before the view would put the canvas back
    settled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._graph = NodeGraph(undo_stack=_NoUndo())
        self._shown: dict[str, _Shown] = {}
        self._nodes: Sequence[NodeSpec] = ()
        self._links: Sequence[LinkSpec] = ()
        self._frames: Sequence[FrameSpec] = ()
        self._backdrops: dict[str, Any] = {}
        self._kept: set[str] = set()
        self._roles: Mapping[str, Color] = {}
        self._menus: list[QMenu] = []
        self._pending: list[tuple[Callable[[], None], bool]] = []
        self._wires: list[tuple[str, LinkSpec]] = []
        self._renders = 0
        self._busy = 0
        self._pick: str | None = None
        self._chosen: list[str] = []
        self._faded: Collection[LinkSpec] = frozenset()
        self._framed = False  # the first non-empty `show` frames its nodes

        viewer = self._graph.viewer()
        self._navigation = _Navigation(viewer)
        viewer.setMinimumSize(240, 160)
        for act in (viewer.qaction_for_undo(), viewer.qaction_for_redo()):
            act.setShortcuts([])  # else Ctrl+Z stops here, at an empty stack, not at the studio
            viewer.context_menus()["graph"].removeAction(act)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._graph.widget)
        viewer.scene().selectionChanged.connect(self._selection)
        self._graph.port_connected.connect(lambda inp, out: self._wired("link", inp, out))
        self._graph.port_disconnected.connect(lambda inp, out: self._wired("unlink", inp, out))
        viewer.moved_nodes.connect(self._dragged)
        # a command makes NodeGraphQt show a node's menu; `_prompt` empties it for the owner
        self._graph.context_nodes_menu().add_command("-", node_type=NODE_TYPE)
        self._graph.context_menu_prompt.connect(self._prompt)
        self.set_canvas(theme.current().view)

    @property
    def viewer(self) -> Any:
        return self._graph.viewer()

    # -- what the owner calls -- #

    def show(
        self,
        nodes: Sequence[NodeSpec] | None = None,
        links: Sequence[LinkSpec] = (),
        frames: Sequence[FrameSpec] = (),
        faded: Collection[LinkSpec] = (),
    ) -> None:
        """Draws `nodes`, `links` (`faded` ones dim) and `frames`, and no more than differs from
        what is drawn. Called bare it is `QWidget.show`."""
        if nodes is None:
            super().show()
            return
        self._apply(nodes, links, frames, faded)
        if nodes and self.isVisible():
            self._frame_once()

    def set_roles(self, colors: Mapping[str, Color]) -> None:
        """A node's colour by `NodeSpec.role`; a role not named keeps NodeGraphQt's grey."""
        self._roles = colors
        self._apply(self._nodes, self._links, self._frames, self._faded)

    def set_canvas(self, background: Color) -> None:
        """The canvas's fill, and its grid a shade off it."""
        bg = theme.color(background)
        grid = bg.lighter(150) if bg.lightness() < 128 else bg.darker(112)
        self._graph.set_background_color(bg.red(), bg.green(), bg.blue())
        self._graph.set_grid_color(grid.red(), grid.green(), grid.blue())

    def set_palette(self, groups: Sequence[PaletteGroup]) -> None:
        """What the right-click menu offers to create: a submenu per group."""
        menu = self.viewer.context_menus()["graph"]
        menu.clear()
        for old in self._menus:
            old.deleteLater()
        self._menus = []
        for title, kinds in groups:
            sub = menu.addMenu(title)
            sub.setToolTipsVisible(True)
            self._menus.append(sub)
            for kind, name, tip in kinds:
                act = sub.addAction(name)
                act.setToolTip(tip)
                act.triggered.connect(lambda _=False, k=kind: self._create(k))

    def select(self, ids: Sequence[str]) -> None:
        """Selects exactly these nodes and frames, without a `picked`."""
        self._busy += 1
        try:
            self.viewer.scene().clearSelection()
            for i in ids:
                if i in self._shown:
                    self._shown[i].view.setSelected(True)
                elif i in self._backdrops:
                    self._backdrops[i].view.setSelected(True)
        finally:
            self._busy -= 1
        self._pick = self._picked()
        self._chosen = self.selected()

    def selected(self) -> list[str]:
        """The selected nodes' ids, in the order of the last `show`."""
        picked = {n.id for n in self.viewer.selected_nodes()}
        return [n.id for n in self._nodes if n.id in picked]

    def frame_rects(self) -> dict[str, QRectF]:
        """Where each frame is now, in scene units."""
        return {i: bd.view.sceneBoundingRect() for i, bd in self._backdrops.items()}

    def selected_frames(self) -> list[str]:
        """The selected frames' ids, in the order of the last `show`. Clicking a frame selects
        the nodes it holds as well."""
        return [i for i, bd in self._backdrops.items() if bd.view.isSelected()]

    def frame(self, ids: Sequence[str]) -> None:
        """Centres the canvas on these nodes."""
        nodes = [self._shown[i].node for i in ids if i in self._shown]
        if nodes:
            self._graph.center_on(nodes)

    def fit(self) -> None:
        """Frames every node and frame."""
        views = [s.view for s in self._shown.values()] + [b.view for b in self._backdrops.values()]
        if views:
            self.viewer.zoom_to_nodes(views)

    def sizes(self) -> dict[str, tuple[float, float]]:
        """Each shown node's drawn width and height, in scene units."""
        out = {}
        for i, s in self._shown.items():
            r = s.view.sceneBoundingRect()
            out[i] = (r.width(), r.height())
        return out

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if self._shown:
            QTimer.singleShot(0, self, self._frame_once)

    def _frame_once(self) -> None:
        if not self._framed:
            self._framed = True
            self.fit()

    # -- drawing -- #

    def _apply(
        self,
        nodes: Sequence[NodeSpec],
        links: Sequence[LinkSpec],
        frames: Sequence[FrameSpec] = (),
        faded: Collection[LinkSpec] = (),
    ) -> None:
        self._check(nodes, links, frames)
        self._nodes, self._links, self._frames, self._faded = nodes, links, frames, faded
        self._renders += 1
        self._busy += 1
        try:
            keep = set(self.selected())
            want = {n.id for n in nodes}
            for gone in [i for i in self._shown if i not in want]:
                self._drop(gone)
            for spec in nodes:
                have = self._shown.get(spec.id)
                if have is not None and have.sig != _signature(spec):
                    self._drop(spec.id)
                    have = None
                self._sync(have or self._make(spec), spec)
            self._relink(links)
            for ln, pipe in self._pipes().items():
                pipe.setOpacity(FADED if ln in faded else 1.0)
            self._frame(frames)
            for i in keep & want:
                if not self._shown[i].view.isSelected():
                    self._shown[i].view.setSelected(True)
        finally:
            self._busy -= 1
        self._pick = self._picked()
        self._chosen = self.selected()

    @staticmethod
    def _check(
        nodes: Sequence[NodeSpec], links: Sequence[LinkSpec], frames: Sequence[FrameSpec] = ()
    ) -> None:
        """Everything that could fail half way, before anything is drawn."""
        ports = {n.id: (set(n.inputs), set(n.outputs)) for n in nodes}
        if len(ports) != len(nodes):
            raise ValueError("two nodes share an id")
        for n in nodes:
            if len(ports[n.id][0]) != len(n.inputs) or len(ports[n.id][1]) != len(n.outputs):
                raise ValueError(f"node {n.id}: a port name twice")
        for fr in frames:
            if not all(i in ports for i in fr.nodes):
                raise ValueError(f"frame {fr.id}: a node it holds is not listed")
        for ln in links:
            if ln.src not in ports or ln.src_port not in ports[ln.src][1]:
                raise ValueError(f"link from {ln.src}.{ln.src_port}: no such output")
            if ln.dst not in ports or ln.dst_port not in ports[ln.dst][0]:
                raise ValueError(f"link to {ln.dst}.{ln.dst_port}: no such input")

    def _make(self, spec: NodeSpec) -> _Shown:
        node = _Node()
        node.NODE_NAME = spec.id  # unique: the graph then never renames a node
        node.model.id = node.view.id = spec.id
        # a side with one port needs no name on it
        named_in, named_out = len(spec.inputs) > 1, len(spec.outputs) > 1
        ins = {
            p: node.add_input(p, multi_input=True, display_name=named_in).view for p in spec.inputs
        }
        outs = {
            p: node.add_output(p, multi_output=True, display_name=named_out).view
            for p in spec.outputs
        }
        self._graph.add_node(node, pos=spec.at, selected=False, push_undo=False)
        base = (node.view.color, node.view.border_color)
        shown = _Shown(node, _signature(spec), ins, outs, base)
        self._shown[spec.id] = shown
        return shown

    def _drop(self, node_id: str) -> None:
        shown = self._shown.pop(node_id)
        for port in shown.node.input_ports() + shown.node.output_ports():
            port.clear_connections(push_undo=False, emit_signal=False)
        for port in shown.view.inputs + shown.view.outputs:  # a wire only the canvas had
            for pipe in list(port.connected_pipes):
                pipe.delete()
        self._graph.remove_node(shown.node, push_undo=False)

    def _sync(self, s: _Shown, spec: NodeSpec) -> None:
        """Brings one node to `spec`, redrawing it when its title or summary changed."""
        view = s.view
        resized = s.title != spec.title or view.summary != spec.summary
        if s.title != spec.title:
            s.title = view.name = spec.title
        ink = self._tint(spec.role)
        tint = s.base if ink is None else _tuples(*ink)
        if tint != s.tint:
            s.tint = tint
            view.color, view.border_color = tint
            view.body, view.edge = (None, None) if ink is None else ink
        if view.badge != spec.badge:
            view.badge = spec.badge
            view.update()
        view.ref = spec.ref
        view.setOpacity(DIM if spec.dim else STAND_IN if spec.ref else 1.0)
        x, y = view.xy_pos
        if abs(x - spec.at[0]) > 1e-3 or abs(y - spec.at[1]) > 1e-3:
            view.xy_pos = spec.at
        view.summary = spec.summary
        if resized:
            view.draw_node()
        view.setToolTip(spec.tip)  # after `draw_node`, which sets NodeGraphQt's own

    def _tint(self, role: str) -> tuple[QColor, QColor] | None:
        """A role's body and edge colours."""
        ink = self._roles.get(role)
        if ink is None:
            return None
        edge = theme.color(ink)
        return edge.darker(320), edge

    def _pipes(self) -> dict[LinkSpec, Any]:
        out: dict[LinkSpec, Any] = {}
        for node_id, s in self._shown.items():
            for name, port in s.outs.items():
                for pipe in list(port.connected_pipes):
                    to = pipe.input_port
                    key = LinkSpec(node_id, name, to.node.id, to.name)
                    if key in out:  # twice drawn
                        pipe.delete()
                    out[key] = pipe
        return out

    def _ports(self, ln: LinkSpec) -> tuple[Port, Port]:
        return (
            self._shown[ln.src].node.get_output(ln.src_port),
            self._shown[ln.dst].node.get_input(ln.dst_port),
        )

    def _relink(self, links: Sequence[LinkSpec]) -> None:
        """Cuts and makes wires through the ports, so NodeGraphQt's model follows the canvas;
        `emit_signal=False` keeps them from reaching `_wired`."""
        have = self._pipes()
        want = set(links)
        for ln in have.keys() - want:
            src, dst = self._ports(ln)
            src.disconnect_from(dst, push_undo=False, emit_signal=False)
        for ln in want - have.keys():
            src, dst = self._ports(ln)
            if dst in src.connected_ports():  # the model holds a wire the canvas lost
                src.disconnect_from(dst, push_undo=False, emit_signal=False)
            src.connect_to(dst, push_undo=False, emit_signal=False)

    def _frame(self, frames: Sequence[FrameSpec]) -> None:
        """The frames, made again each time: titled backdrops round their nodes. A title is cut
        to the frame's width."""
        chosen = {*self.selected_frames(), *self._kept}
        self._kept = chosen if not frames else set()  # a pass with no frames keeps the choice
        for old in self._backdrops.values():
            self._graph.remove_node(old, push_undo=False)
        self._backdrops = {}
        metrics = QFontMetricsF(QApplication.font())
        for fr in frames:
            node = BackdropNode()
            node.NODE_NAME = fr.id
            node.model.id = node.view.id = fr.id
            self._graph.add_node(node, pos=(0.0, 0.0), selected=False, push_undo=False)
            ink = self._roles.get(fr.role)
            if ink is not None:
                edge = theme.color(ink).darker(220)
                node.set_color(edge.red(), edge.green(), edge.blue())
            held = QRectF()
            for i in fr.nodes:
                held = held.united(self._shown[i].view.sceneBoundingRect())
            node.set_pos(held.left() - FRAME_PAD, held.top() - FRAME_TOP)
            node.set_size(held.width() + 2 * FRAME_PAD, held.height() + FRAME_TOP + FRAME_BOTTOM)
            node.view.name = metrics.elidedText(
                fr.title, Qt.TextElideMode.ElideRight, node.view.width - 20.0
            )
            if fr.id in chosen:
                node.view.setSelected(True)
            self._backdrops[fr.id] = node

    # -- what the canvas tells -- #

    def _raise(self, fn: Callable[[], None], *, revert: bool = True) -> None:
        """Runs `fn` after the event that raised it; if it changed the canvas and `show` did
        not follow, the canvas goes back."""
        self._pending.append((fn, revert))
        if len(self._pending) == 1:
            QTimer.singleShot(0, self, self._flush)

    def _flush(self) -> None:
        pending, self._pending = self._pending, []
        seen = self._renders
        for fn, _ in pending:
            fn()
        self.settled.emit()
        if self._renders == seen and any(revert for _, revert in pending):
            self._apply(self._nodes, self._links, self._frames, self._faded)

    def _wired(self, kind: str, into: Port, out: Port) -> None:
        """NodeGraphQt applied a connect or a disconnect (a re-route is both, in one turn):
        they come out in order as the one intent each."""
        if self._busy:
            return
        self._wires.append((kind, LinkSpec(out.node().id, out.name(), into.node().id, into.name())))
        if len(self._wires) == 1:
            self._raise(self._tell_wires)

    def _tell_wires(self) -> None:
        wires, self._wires = self._wires, []
        for kind, ln in wires:
            signal = self.link_requested if kind == "link" else self.unlink_requested
            signal.emit(ln.src, ln.src_port, ln.dst, ln.dst_port)

    def _dragged(self, moved: Mapping[Any, Any]) -> None:
        """`moved` is NodeGraphQt's {node item: where it was}, once per drag end."""
        at = [(i.id, *i.xy_pos) for i in moved if i.id in self._shown]
        framed = set(self._backdrops)
        held = [(i.id, *i.xy_pos) for i in moved if i.id in framed]

        def emit() -> None:
            for i, x, y in at:
                self.moved.emit(i, x, y)
            for i, x, y in held:
                self.frame_moved.emit(i, x, y)

        self._raise(emit)

    def _prompt(self, menu: Any, node: Any) -> None:
        """Just before a node's menu shows: it is emptied and the owner fills it."""
        if node is None or node.id not in self._shown:
            return
        sub = self._graph.context_nodes_menu().qmenu.get_menu(node.type_)
        if sub is not None:
            sub.clear()
            self.menu_requested.emit(node.id, sub)

    def _create(self, kind: str) -> None:
        x, y = self._graph.cursor_pos()
        self._raise(lambda: self.create_requested.emit(kind, x, y), revert=False)

    def _picked(self) -> str | None:
        frames, ids = self.selected_frames(), self.selected()
        if len(frames) == 1:
            return frames[0]
        return ids[0] if len(ids) == 1 else None

    def _selection(self) -> None:
        if not self._busy:
            self._raise(self._emit_picked, revert=False)

    def _emit_picked(self) -> None:
        now, ids = self._picked(), self.selected()
        if now != self._pick:
            self._pick = now
            self.picked.emit(now)
        if ids != self._chosen:
            self._chosen = ids
            self.selection_changed.emit(ids)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not event.modifiers():
            self._delete_selected()
            event.accept()
            return
        super().keyPressEvent(event)

    def _delete_selected(self) -> None:
        ids = [*self.selected(), *self.selected_frames()]
        cuts = [
            LinkSpec(
                p.output_port.node.id, p.output_port.name, p.input_port.node.id, p.input_port.name
            )
            for p in self.viewer.selected_pipes()
        ]

        def emit() -> None:
            for ln in cuts:
                self.unlink_requested.emit(ln.src, ln.src_port, ln.dst, ln.dst_port)
            if ids:
                self.delete_requested.emit(ids)

        if ids or cuts:
            self._raise(emit, revert=False)
