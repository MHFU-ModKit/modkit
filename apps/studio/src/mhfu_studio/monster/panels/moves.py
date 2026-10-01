# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Moves dock: the host's pairs as a graph of hand-offs (`graph.build`), on a QGraphicsView.

Click a node to read its hand-offs, double-click to select it in Action; drag a node to move it,
the canvas to pan (any button); the wheel zooms. Edge labels show only for the focused node's
edges: all at once, a five-node chain is unreadable."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from mhfu.em.intel import SpeciesIntel
from mhfu_port.manifest import Move
from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPolygonF,
    QResizeEvent,
    QShowEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsSceneHoverEvent,
    QGraphicsView,
    QStackedWidget,
    QStyleOptionGraphicsItem,
    QVBoxLayout,
    QWidget,
)

from mhfu_studio.monster.panels import graph
from mhfu_studio.monster.panels.graph import KINDS, NODE_H, NODE_W, Arrow, Layout, Node, Pair
from mhfu_studio.monster.panels.widgets import NoScene, Pages, put
from mhfu_studio.shell.overlay import Color, Ink
from mhfu_studio.ui import kit, theme

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

Point = tuple[float, float]
#: the wheel's zoom range, and the range a fit may pick: text scales with the canvas, so a fit
#: stops where it stays legible and leaves the rest to a pan
ZOOM, FIT_ZOOM = (0.25, 2.5), (0.6, 1.25)
STEP = 1.12
#: scene room around the layout, so the canvas pans past its edges
MARGIN = 10000.0
PAD = 18.0
SCOPES = (("moves", "From moves"), ("selected", "From selected"), ("attacks", "Every attack"))
SCOPE_TIPS = {
    "moves": "Starts at the pairs your manifest's [moves] bind, and follows what each hands to",
    "selected": "Starts at the pair selected in Action, with the pairs that lead into it",
    "attacks": "Every pair that hits with an attack; pairs that act alike are drawn once",
}
CANVAS_TIP = (
    "The host's behaviour pairs: an arrow is what a pair's code does when its action ends."
    " Click a node to read its hand-offs, double-click to work on it in Action. Drag a node to"
    " move it, drag the canvas to pan, and use the wheel to zoom."
)
WALK_TIP = (
    "Read from the host's code, not watched in the game. A pair with no arrow out never ends by"
    " itself: the engine enters it only through the translator, which provisions it."
)
LEGEND: tuple[tuple[str, Color], ...] = (
    ("move", KINDS["move"]),
    ("attacks", KINDS["attacks"]),
    ("hub", KINDS["hub"]),
    ("picked", Ink.SELECTION),
    ("in Action", Ink.HOT),
)


def _alpha(c: QColor, a: float) -> QColor:
    c.setAlphaF(c.alphaF() * a)
    return c


def bezier(p1: Point, p2: Point, p3: Point, p4: Point, t: float) -> Point:
    u = 1.0 - t
    return (
        u**3 * p1[0] + 3 * u * u * t * p2[0] + 3 * u * t * t * p3[0] + t**3 * p4[0],
        u**3 * p1[1] + 3 * u * u * t * p2[1] + 3 * u * t * t * p3[1] + t**3 * p4[1],
    )


def curve(s: QRectF, d: QRectF) -> tuple[Point, Point, Point, Point]:
    """An arrow's control points from `s` to `d`: sideways between columns, else stacked."""
    if d.left() > s.left() + NODE_W * 0.5:
        p1, p4 = (s.right(), s.center().y()), (d.left(), d.center().y())
        bend = max(30.0, (d.left() - s.right()) * 0.5)
        return p1, (p1[0] + bend, p1[1]), (p4[0] - bend, p4[1]), p4
    if d.left() < s.left() - NODE_W * 0.5:
        p1, p4 = (s.left(), s.center().y()), (d.right(), d.center().y())
        bend = max(30.0, (s.left() - d.right()) * 0.5)
        return p1, (p1[0] - bend, p1[1]), (p4[0] + bend, p4[1]), p4
    down = d.top() > s.top()
    p1 = (s.center().x(), s.bottom() if down else s.top())
    p4 = (d.center().x(), d.top() if down else d.bottom())
    lift = 40.0 if down else -40.0
    return p1, (p1[0], p1[1] + lift), (p4[0], p4[1] - lift), p4


class NodeItem(QGraphicsItem):
    """One pair; dragging it writes the layout's node, so the position outlives the panel."""

    def __init__(self, canvas: GraphView, node: Node) -> None:
        super().__init__()
        self.canvas, self.node = canvas, node
        self.edges: list[EdgeItem] = []
        flag = QGraphicsItem.GraphicsItemFlag
        self.setFlags(flag.ItemIsMovable | flag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(1)
        self.setPos(node.x, node.y)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(-2, -2, NODE_W + 4, NODE_H + 4)

    def rect(self) -> QRectF:
        """In the scene."""
        return QRectF(self.pos().x(), self.pos().y(), NODE_W, NODE_H)

    def paint(self, p: QPainter, opt: QStyleOptionGraphicsItem, w: QWidget | None = None) -> None:
        c, n = self.canvas, self.node
        edge, width = theme.color(KINDS[n.kind]), 1.0
        if n.pair == c.selected:
            edge, width = theme.color(Ink.HOT), 2.0
        if n.pair == c.picked:
            edge, width = theme.color(Ink.SELECTION), 2.5
        elif n.pair == c.hover and n.pair != c.selected:
            edge, width = theme.color(Ink.HOVER), 2.0
        box = QRectF(0, 0, NODE_W, NODE_H)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(c.palette().color(QPalette.ColorRole.Base))
        p.drawRoundedRect(box, 6, 6)
        p.setBrush(_alpha(theme.color(KINDS[n.kind]), 0.22))
        p.setPen(QPen(edge, width))
        p.drawRoundedRect(box, 6, 6)
        text, dim = c.palette().color(QPalette.ColorRole.Text), c.dim()
        p.setFont(c.small)
        lod = QStyleOptionGraphicsItem.levelOfDetailFromTransform(p.worldTransform())
        lines = n.lines[:1] if lod < 0.5 else n.lines[:3]
        for i, line in enumerate(lines):
            p.setPen(text if i == 0 else dim)
            p.drawText(QRectF(7, 4 + i * 16, NODE_W - 12, 16), Qt.AlignmentFlag.AlignLeft, line)
        if n.hub and lod >= 0.5:
            p.setPen(dim)
            p.drawText(QRectF(7, NODE_H - 17, NODE_W - 12, 16), 0, "brain picks next")

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: Any) -> Any:  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.node.x, self.node.y = self.pos().x(), self.pos().y()
            for e in self.edges:
                e.adjust()
        return super().itemChange(change, value)

    def hoverEnterEvent(self, e: QGraphicsSceneHoverEvent) -> None:  # noqa: N802
        self.canvas.set_hover(self.node.pair)

    def hoverLeaveEvent(self, e: QGraphicsSceneHoverEvent) -> None:  # noqa: N802
        if self.canvas.hover == self.node.pair:
            self.canvas.set_hover(None)


class EdgeItem(QGraphicsItem):
    """A hand-off; labelled only while one of its ends is focused."""

    def __init__(self, canvas: GraphView, arrow: Arrow, src: NodeItem, dst: NodeItem) -> None:
        super().__init__()
        self.canvas, self.arrow, self.src, self.dst = canvas, arrow, src, dst
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        #: where the label sits along the curve, staggered so a fan does not pile up
        self.t = 0.5
        self._ends: tuple[Point, Point, Point, Point] | None = None
        self._path = QPainterPath()
        src.edges.append(self)
        dst.edges.append(self)
        self.adjust()

    def adjust(self) -> None:
        self.prepareGeometryChange()
        s, path = self.src.rect(), QPainterPath()
        if self.src is self.dst:
            path.moveTo(s.right() - 10, s.top())
            path.cubicTo(
                s.right() + 24, s.top() - 26, s.right() - 44, s.top() - 26, s.right() - 30, s.top()
            )
            self._ends = None
        else:
            self._ends = p1, p2, p3, p4 = curve(s, self.dst.rect())
            path.moveTo(*p1)
            path.cubicTo(QPointF(*p2), QPointF(*p3), QPointF(*p4))
        self._path = path

    @property
    def hot(self) -> bool:
        return self.canvas.focus in (self.arrow.src, self.arrow.dst)

    def boundingRect(self) -> QRectF:  # noqa: N802
        # room for the arrow head and a label beside the curve
        return self._path.boundingRect().adjusted(-12, -24, 300, 24)

    def paint(self, p: QPainter, opt: QStyleOptionGraphicsItem, w: QWidget | None = None) -> None:
        c, a = self.canvas, self.arrow
        if self.hot:
            col, width = theme.color(Ink.SELECTION), 2.2
        elif c.selected in (a.src, a.dst):
            col, width = _alpha(theme.color(Ink.HOT), 0.85), 1.6
        else:
            col, width = _alpha(c.dim(), 0.6), 1.0
        pen = QPen(col, width)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(self._path)
        if self._ends is None:
            return
        p1, p2, p3, p4 = self._ends
        self._head(p, p3, p4, col)
        if self.hot and a.label:
            x, y = bezier(p1, p2, p3, p4, self.t)
            self._pill(p, x, y - 9, a.label if len(a.label) < 44 else a.label[:41] + "...")

    def _head(self, p: QPainter, frm: Point, to: Point, col: QColor, size: float = 7.0) -> None:
        dx, dy = to[0] - frm[0], to[1] - frm[1]
        n = (dx * dx + dy * dy) ** 0.5 or 1.0
        ux, uy = dx / n, dy / n
        bx, by, half = to[0] - ux * size, to[1] - uy * size, size * 0.5
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        tri = [
            QPointF(*to),
            QPointF(bx - uy * half, by + ux * half),
            QPointF(bx + uy * half, by - ux * half),
        ]
        p.drawPolygon(QPolygonF(tri))

    def _pill(self, p: QPainter, x: float, y: float, text: str) -> None:
        """Text on a backing of its own, readable over arrows and boxes."""
        c = self.canvas
        p.setFont(c.small)
        r = p.fontMetrics().boundingRect(text)
        box = QRectF(x - 4, y - 2, r.width() + 8, r.height() + 4)
        p.setPen(QPen(_alpha(theme.color(Ink.SELECTION), 0.6), 1.0))
        p.setBrush(_alpha(c.palette().color(QPalette.ColorRole.Base), 0.94))
        p.drawRoundedRect(box, 4, 4)
        p.setPen(c.palette().color(QPalette.ColorRole.Text))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, text)


class GraphView(QGraphicsView):
    """The canvas: nodes, arrows, the info box and the legend."""

    def __init__(self, panel: MovesPanel) -> None:
        super().__init__()
        self.panel = panel
        self.setScene(QGraphicsScene(self))
        self.setToolTip(CANVAS_TIP)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.small = QFont(self.font())
        self.small.setPixelSize(11)
        self.mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.mono.setPixelSize(11)
        self.lay: Layout | None = None
        self.nodes: dict[Pair, NodeItem] = {}
        self.edges: list[EdgeItem] = []
        self.selected: Pair | None = None
        self.picked: Pair | None = None
        self.hover: Pair | None = None
        self.info: tuple[SpeciesIntel, Mapping[str, Move]] | None = None
        self._press: tuple[Pair | None, QPointF] | None = None
        self._pan: QPointF | None = None
        self._fit_pending = False

    @property
    def focus(self) -> Pair | None:
        return self.picked if self.picked is not None else self.hover

    def dim(self) -> QColor:
        return self.palette().color(QPalette.ColorRole.PlaceholderText)

    def zoom(self) -> float:
        return self.transform().m11()

    # ---- content ----------------------------------------------------------------------- #

    def show_layout(self, lay: Layout, fit: bool) -> None:
        """Builds the items when `lay` is new; `fit` frames it once the view has a size, as
        does the first layout a view gets."""
        fit = fit or self.lay is None
        if lay is not self.lay:
            self.lay, self.hover = lay, None
            sc = self.scene()
            sc.clear()
            self.nodes = {k: NodeItem(self, n) for k, n in lay.nodes.items()}
            for item in self.nodes.values():
                sc.addItem(item)
            self.edges = [
                EdgeItem(self, a, self.nodes[a.src], self.nodes[a.dst]) for a in lay.arrows
            ]
            for e in self.edges:
                sc.addItem(e)
            sc.setSceneRect(
                QRectF(0, 0, lay.width, lay.height).adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN)
            )
        if fit:
            self._fit_pending = True
            self._fit_if_sized()

    def set_state(self, selected: Pair | None, picked: Pair | None) -> None:
        if (selected, picked) != (self.selected, self.picked):
            self.selected, self.picked = selected, picked
            self.restyle()

    def set_hover(self, pair: Pair | None) -> None:
        if pair != self.hover:
            self.hover = pair
            self.restyle()

    def restyle(self) -> None:
        """The focused node's arrows on top, their labels staggered along the curves."""
        hot = 0
        for e in self.edges:
            if e.hot:
                e.setZValue(3)
                e.t = (0.30, 0.50, 0.70, 0.40, 0.60)[hot % 5]
                hot += 1
            else:
                e.setZValue(0)
        self.scene().update()
        self.viewport().update()

    def nodes_rect(self) -> QRectF:
        lay = self.lay
        return QRectF() if lay is None else QRectF(0, 0, lay.width, lay.height)

    def fit(self) -> None:
        """Frames the whole layout at a zoom that keeps the text readable; what does not fit
        then is cut on the right and the bottom, so the roots stay in view."""
        rect = self.nodes_rect()
        if rect.isEmpty():
            return
        self.resetTransform()
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        z = min(max(self.zoom(), FIT_ZOOM[0]), FIT_ZOOM[1])
        self.scale(z / self.zoom(), z / self.zoom())
        w, h = self.viewport().width() / z, self.viewport().height() / z
        x = rect.center().x() if rect.width() <= w else rect.left() + w / 2
        y = rect.center().y() if rect.height() <= h else rect.top() + h / 2
        self.centerOn(x, y)
        self._fit_pending = False

    def _fit_if_sized(self) -> None:
        if self._fit_pending and self.isVisible() and self.viewport().width() > 1:
            self.fit()

    def node_at(self, pos: QPointF) -> Pair | None:
        for it in self.items(pos.toPoint()):
            if isinstance(it, NodeItem):
                return it.node.pair
        return None

    # ---- input ------------------------------------------------------------------------- #

    def wheelEvent(self, e: QWheelEvent) -> None:  # noqa: N802
        notches = e.angleDelta().y() / 120.0
        if notches:
            want = min(max(self.zoom() * STEP**notches, ZOOM[0]), ZOOM[1])
            k = want / self.zoom()
            self.scale(k, k)
        e.accept()

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton):
            self._pan = e.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = (self.node_at(e.position()), e.position())
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._pan is not None:
            d = e.position() - self._pan
            self._pan = e.position()
            h, v = self.horizontalScrollBar(), self.verticalScrollBar()
            h.setValue(h.value() - round(d.x()))
            v.setValue(v.value() - round(d.y()))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._pan is not None and e.button() != Qt.MouseButton.LeftButton:
            self._pan = None
            self.viewport().unsetCursor()
            e.accept()
            return
        super().mouseReleaseEvent(e)
        if e.button() == Qt.MouseButton.LeftButton and self._press is not None:
            pair, at = self._press
            self._press = None
            if (e.position() - at).manhattanLength() < QApplication.startDragDistance():
                self.panel.pick(pair)

    def mouseDoubleClickEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        pair = self.node_at(e.position())
        if pair is not None and e.button() == Qt.MouseButton.LeftButton:
            self.panel.select(pair)
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def resizeEvent(self, e: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._fit_if_sized()

    def showEvent(self, e: QShowEvent) -> None:  # noqa: N802
        super().showEvent(e)
        self._fit_if_sized()

    # ---- painting ---------------------------------------------------------------------- #

    def drawBackground(self, p: QPainter, rect: QRectF | QRect) -> None:  # noqa: N802
        p.fillRect(rect, self.palette().color(QPalette.ColorRole.Base))

    def drawForeground(self, p: QPainter, rect: QRectF | QRect) -> None:  # noqa: N802
        """The info box and the legend, in view coordinates."""
        p.save()
        p.resetTransform()
        self._info_box(p)
        self._legend(p)
        p.restore()

    def info_lines(self) -> tuple[list[str], bool]:
        """The focused node's hand-offs, or how to get them; and whether they are the hint."""
        pair, lay = self.focus, self.lay
        if self.info is None or lay is None or pair is None or self.info[0].pair(*pair) is None:
            return ["click a node to read its hand-offs, double-click to select it"], True
        intel, moves = self.info
        return graph.info_lines(intel, pair, lay, moves), False

    def _info_box(self, p: QPainter) -> None:
        lines, hint = self.info_lines()
        p.setFont(self.mono)
        fm = p.fontMetrics()
        most = self.viewport().width() * 0.6
        lines = [fm.elidedText(t, Qt.TextElideMode.ElideRight, int(most)) for t in lines]
        lh = fm.height() + 2
        bw = max(fm.horizontalAdvance(t) for t in lines) + 20.0
        box = QRectF(self.viewport().width() - bw - 8, 8, bw, lh * len(lines) + 12.0)
        p.setPen(QPen(_alpha(self.dim(), 0.5), 1.0))
        p.setBrush(_alpha(self.palette().color(QPalette.ColorRole.Base), 0.92))
        p.drawRoundedRect(box, 6, 6)
        text = self.palette().color(QPalette.ColorRole.Text)
        for i, t in enumerate(lines):
            if hint or t.startswith("double-click"):
                p.setPen(self.dim())
            elif t.startswith("  ->"):
                p.setPen(theme.color(Ink.HOT))
            else:
                p.setPen(text)
            p.drawText(QPointF(box.left() + 10, box.top() + 6 + fm.ascent() + i * lh), t)

    def _legend(self, p: QPainter) -> None:
        p.setFont(self.small)
        fm = p.fontMetrics()
        x, y = 8.0, self.viewport().height() - 18.0
        for name, color in LEGEND:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color(color))
            p.drawRoundedRect(QRectF(x, y + 3, 10, 10), 2, 2)
            p.setPen(self.dim())
            p.drawText(QPointF(x + 14, y + 3 + fm.ascent() - 1), name)
            x += 14 + fm.horizontalAdvance(name) + 12


class MovesPanel(kit.Panel):
    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__(scroll=False)
        self.ws, self.studio = ws, studio
        act = studio.act
        self.scope = kit.Segmented(
            SCOPES,
            tip="Which pairs the graph starts from",
            tips=SCOPE_TIPS,
            on=lambda s: act("graph scope", lambda: ws.graph.set_scope(s))(),
        )
        self.fit = kit.icon_button(
            "ph.arrows-in", tip="Fits the whole graph in view", on=lambda: self.canvas.fit()
        )
        self.relayout = kit.icon_button(
            "ph.arrows-clockwise",
            tip="Puts every node back where the layout places it, undoing your drags",
            on=act("re-layout", lambda: ws.graph.relayout()),
        )
        self.walk = kit.label(role="muted")
        self.walk.setToolTip(WALK_TIP)
        self.canvas = GraphView(self)
        self.no_scene = NoScene(studio)
        self.no_scene.say(
            "No port manifest",
            "The graph is the host monster's moves, and the port manifest names the host."
            " Open one (ports/<name>.toml).",
        )
        self.note = kit.Empty("Nothing to draw", "")
        self.empty = QStackedWidget()
        self.empty.addWidget(self.no_scene)
        self.empty.addWidget(self.note)
        self.pages = Pages(self.canvas, self.empty)
        self.tools = kit.row(self.scope, self.fit, self.relayout, stretch=True)
        top = QWidget()
        lay = QVBoxLayout(top)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.tools)
        lay.addWidget(self.walk)
        self.body.addWidget(top)
        self.body.addWidget(self.pages, 1)

    def pick(self, pair: Pair | None) -> None:
        """A click: the node's hand-offs in the info box; empty canvas clears it."""
        self.studio.act("pick node", lambda: setattr(self.ws.graph, "picked", pair))()

    def select(self, pair: Pair) -> None:
        """A double-click: the pair goes to Action, which comes to the front."""

        def run() -> None:
            self.ws.graph.picked = pair
            self.ws.select_pair(*pair)
            self.ws.focus("Action")

        self.studio.act("select pair", run)()

    def sync(self) -> None:
        ws = self.ws
        m, intel = ws.manifest, ws.intel
        self.tools.setVisible(m is not None)
        self.walk.setVisible(m is not None)
        if m is None:
            self._empty(self.no_scene)
            return
        put(self.scope, ws.graph.scope)
        self.walk.setText(self._walk_line(intel))
        sp = ws.browsing_species or 0
        if intel is None:
            self.note.say("No action intel", f"There is no action intel for em{sp:02d}.")
            self._empty(self.note)
            return
        moves = dict(m.moves)
        lay = ws.graph.layout(intel, moves, ws.pair)
        if lay.empty:
            self.note.say("Nothing to draw", lay.note)
            self._empty(self.note)
            return
        self.pages.show_page(True)
        self.canvas.info = (intel, moves)
        self.canvas.show_layout(lay, ws.graph.fresh)
        ws.graph.fresh = False
        self.canvas.set_state(ws.pair, ws.graph.picked)
        self.canvas.viewport().update()

    def _empty(self, which: QWidget) -> None:
        self.empty.setCurrentWidget(which)
        self.pages.show_page(False)

    def _walk_line(self, intel: SpeciesIntel | None) -> str:
        sp, sel = self.ws.browsing_species or 0, self.ws.pair
        if intel is None or not intel.has_chain:
            return f"em{sp:02d}: no hand-off intel"
        if sel is not None and intel.pair(*sel) is not None:
            return graph.walk_line(intel, sel)
        hubs = " ".join(f"({m},{s})" for m, s in intel.hubs)
        return (
            f"em{sp:02d}: an arrow is what a pair's code does when its action ends; at the hubs"
            f" {hubs} the brain picks again"
        )
