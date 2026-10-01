# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Moves tab beside the Viewport: the host's pairs as a graph of hand-offs (`graph.build`).

Click a node to read its hand-offs, double-click to select it in the Action tab; drag a node to
move it, the canvas to pan; the wheel zooms. Edge labels show only for the focused node's
edges: all at once, a five-node chain is unreadable."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from mhfu.em.intel import SpeciesIntel
from mhfu_port.manifest import Move

from . import graph
from .graph import NODE_H, NODE_W, SCOPES, Arrow, Layout, Node, Pair

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace

Point = tuple[float, float]
Rect = tuple[float, float, float, float]
SCOPE_LABELS = ["from [moves]", "from selected", "every attack"]


def panel(ws: MonsterWorkspace) -> None:
    from imgui_bundle import imgui

    if ws.manifest is None:
        imgui.text_wrapped(
            "The graph is the HOST overlay's, and which host needs a manifest. Open a port "
            "manifest."
        )
        return
    if ws.intel is None:
        imgui.text_wrapped(f"no action intel for em{ws.browsing_species or 0:02d}")
        return
    ws.graph.draw(ws)


class MoveGraph:
    """Scope, pan and zoom, the cached layout, the hovered and the picked node."""

    def __init__(self) -> None:
        self.scope = "moves"
        self.pan = [0.0, 0.0]
        self.zoom = 1.0
        self.hover: Pair | None = None
        self.picked: Pair | None = None
        self._layout: Layout | None = None
        self._key: tuple[object, ...] | None = None
        self._fit_pending = True
        self._press: tuple[Pair | None, float, float] | None = None
        self._moved = False

    def layout(
        self, intel: SpeciesIntel | None, moves: Mapping[str, Move], selected: Pair | None
    ) -> Layout:
        key = (
            id(intel),
            self.scope,
            selected,
            tuple(sorted((n, m.main, m.sub) for n, m in moves.items())),
        )
        if self._layout is None or key != self._key:
            self._layout = graph.build(intel, moves, selected, self.scope)
            self._key = key
            self._fit_pending = True
            if self.picked is not None and self.picked not in self._layout.nodes:
                self.picked = None
        return self._layout

    def invalidate(self) -> None:
        self._layout, self._key = None, None

    def fit(self, w: float, h: float) -> None:
        lay = self._layout
        if lay is None or lay.empty:
            return
        self.zoom = max(0.35, min(w / max(lay.width, 1.0), h / max(lay.height, 1.0), 1.25))
        self.pan = [(w - lay.width * self.zoom) / 2.0, (h - lay.height * self.zoom) / 2.0]
        self._fit_pending = False

    def draw(self, ws: MonsterWorkspace) -> None:
        from imgui_bundle import imgui

        from mhfu_studio.shell.widgets import mouse_buttons

        intel, m = ws.intel, ws.manifest
        moves = {} if m is None else dict(m.moves)
        selected = ws.pair
        self._toolbar(ws, intel, selected)
        lay = self.layout(intel, moves, selected)
        avail = imgui.get_content_region_avail()
        w, h = max(float(avail.x), 1.0), max(float(avail.y), 1.0)
        if lay.empty:
            imgui.text_wrapped(lay.note)
            return
        if self._fit_pending:
            self.fit(w, h)
        o = imgui.get_cursor_screen_pos()
        origin = (float(o.x), float(o.y))
        imgui.invisible_button("##movegraph", imgui.ImVec2(w, h), mouse_buttons())
        hovered, active = imgui.is_item_hovered(), imgui.is_item_active()
        mp = imgui.get_io().mouse_pos
        mouse = (float(mp.x), float(mp.y))
        self.hover = None
        if hovered:
            for n in lay.nodes.values():
                if _inside(self._rect(origin, n), mouse):
                    self.hover = n.pair
        self._input(ws, lay, origin, hovered, active, mouse)
        draw = imgui.get_window_draw_list()
        p0, p1 = imgui.ImVec2(*origin), imgui.ImVec2(origin[0] + w, origin[1] + h)
        draw.add_rect_filled(p0, p1, _col(0.09, 0.10, 0.12, 1.0))
        imgui.push_clip_rect(p0, p1, True)
        focus = self.picked if self.picked is not None else self.hover
        hot = [a for a in lay.arrows if focus in (a.src, a.dst)]
        for a in lay.arrows:
            if focus not in (a.src, a.dst):
                self._arrow(draw, origin, lay, a, selected, focus, False)
        for n in lay.nodes.values():
            self._node(draw, origin, n, selected)
        for i, a in enumerate(hot):  # last, so their labels sit on top
            self._arrow(draw, origin, lay, a, selected, focus, True, i)
        if intel is not None:
            _info_box(draw, origin, w, intel, lay, focus, moves)
        imgui.pop_clip_rect()
        _legend(draw, origin, h)

    def _toolbar(
        self, ws: MonsterWorkspace, intel: SpeciesIntel | None, selected: Pair | None
    ) -> None:
        from imgui_bundle import imgui

        imgui.set_next_item_width(110.0)
        idx = SCOPES.index(self.scope) if self.scope in SCOPES else 0
        changed, pick = imgui.combo("##scope", idx, SCOPE_LABELS)
        if changed:
            self.scope = SCOPES[pick]
            self.invalidate()
        imgui.same_line()
        if imgui.small_button("fit"):
            self._fit_pending = True
        imgui.same_line()
        if imgui.small_button("re-layout"):
            self.invalidate()
        imgui.same_line()
        sp = ws.browsing_species or 0
        if intel is None or not intel.has_chain:
            imgui.text_disabled(f"em{sp:02d}: no hand-off intel")
            return
        if selected is not None and intel.pair(*selected) is not None:
            imgui.text_disabled(graph.walk_line(intel, selected))
        else:
            hubs = " ".join(f"({m},{s})" for m, s in intel.hubs)
            imgui.text_disabled(
                f"em{sp:02d}: arrows are what the HANDLER does when its action ends; hubs {hubs} "
                "are where the brain picks again"
            )
        if imgui.is_item_hovered():
            imgui.set_tooltip(
                "Static, read from the overlay. A pair with no arrow out never ends by itself: the "
                "engine only enters it through the translator, which provisions it.\nClick a node "
                "to read its hand-offs; DOUBLE-click to select it in the Action tab. Drag a node "
                "to move it, drag the canvas to pan, wheel to zoom."
            )

    def _input(
        self,
        ws: MonsterWorkspace,
        lay: Layout,
        origin: Point,
        hovered: bool,
        active: bool,
        mouse: Point,
    ) -> None:
        """A press remembers what was under it: a node is dragged, a release without movement
        picks it; empty canvas pans."""
        from imgui_bundle import imgui

        if hovered and imgui.is_mouse_clicked(0):
            self._press, self._moved = (self.hover, *mouse), False
        if active and self._press is not None and imgui.is_mouse_dragging(0):
            d = imgui.get_mouse_drag_delta(0)
            node = lay.nodes.get(self._press[0]) if self._press[0] is not None else None
            if node is not None:
                node.x += float(d.x) / self.zoom
                node.y += float(d.y) / self.zoom
            else:
                self.pan[0] += float(d.x)
                self.pan[1] += float(d.y)
            imgui.reset_mouse_drag_delta(0)
            self._moved = True
        if self._press is not None and imgui.is_mouse_released(0):
            if not self._moved:
                self.picked = self._press[0]
            self._press, self._moved = None, False
        if hovered and self.hover is not None and imgui.is_mouse_double_clicked(0):
            self.picked = self.hover
            ws.select_pair(*self.hover)
        for btn in (1, 2):
            if active and imgui.is_mouse_dragging(btn):
                d = imgui.get_mouse_drag_delta(btn)
                self.pan[0] += float(d.x)
                self.pan[1] += float(d.y)
                imgui.reset_mouse_drag_delta(btn)
        wheel = float(imgui.get_io().mouse_wheel)
        if hovered and wheel:
            mx, my = mouse[0] - origin[0], mouse[1] - origin[1]
            old = self.zoom
            self.zoom = max(0.25, min(2.5, self.zoom * (1.12 if wheel > 0 else 1 / 1.12)))
            k = self.zoom / old
            self.pan = [mx - (mx - self.pan[0]) * k, my - (my - self.pan[1]) * k]

    def _pt(self, origin: Point, x: float, y: float) -> Point:
        return origin[0] + self.pan[0] + x * self.zoom, origin[1] + self.pan[1] + y * self.zoom

    def _rect(self, origin: Point, n: Node) -> Rect:
        x0, y0 = self._pt(origin, n.x, n.y)
        return x0, y0, x0 + NODE_W * self.zoom, y0 + NODE_H * self.zoom

    def _node(self, draw: Any, origin: Point, n: Node, selected: Pair | None) -> None:
        from imgui_bundle import imgui

        x0, y0, x1, y1 = self._rect(origin, n)
        sel, pick, hov = n.pair == selected, n.pair == self.picked, n.pair == self.hover
        if n.hub:
            fill, edge = _col(0.18, 0.19, 0.21, 1.0), _col(0.45, 0.47, 0.50, 1.0)
        elif n.move:
            fill, edge = _col(0.16, 0.27, 0.36, 1.0), _col(0.35, 0.70, 0.95, 1.0)
        elif n.attacks:
            fill, edge = _col(0.32, 0.20, 0.14, 1.0), _col(0.95, 0.60, 0.30, 1.0)
        else:
            fill, edge = _col(0.16, 0.17, 0.20, 1.0), _col(0.40, 0.42, 0.46, 1.0)
        if sel:
            edge = _col(1.0, 0.90, 0.35, 1.0)
        if pick:
            edge = _col(0.95, 0.95, 0.95, 1.0)
        elif hov and not sel:
            edge = _col(0.75, 0.76, 0.78, 1.0)
        r = 6.0 * self.zoom
        a, b = imgui.ImVec2(x0, y0), imgui.ImVec2(x1, y1)
        draw.add_rect_filled(a, b, fill, r)
        draw.add_rect(a, b, edge, r, 2.5 if pick else 2.0 if (sel or hov) else 1.0)
        if self.zoom < 0.5:
            draw.add_text(imgui.ImVec2(x0 + 4, y0 + 3), _col(0.9, 0.9, 0.9, 1.0), n.lines[0])
            return
        text, dim = _col(0.92, 0.93, 0.95, 1.0), _col(0.62, 0.65, 0.70, 1.0)
        step = 16 * min(self.zoom, 1.0)
        for i, line in enumerate(n.lines[:3]):
            draw.add_text(imgui.ImVec2(x0 + 7, y0 + 5 + i * step), text if i == 0 else dim, line)
        if n.hub:
            draw.add_text(
                imgui.ImVec2(x0 + 7, y1 - 15 * min(self.zoom, 1.0)), dim, "brain picks next"
            )

    def _arrow(
        self,
        draw: Any,
        origin: Point,
        lay: Layout,
        a: Arrow,
        selected: Pair | None,
        focus: Pair | None,
        labelled: bool,
        slot: int = 0,
    ) -> None:
        from imgui_bundle import imgui

        s, d = lay.nodes[a.src], lay.nodes[a.dst]
        sx0, sy0, sx1, sy1 = self._rect(origin, s)
        dx0, dy0, dx1, dy1 = self._rect(origin, d)
        hot, sel = focus in (a.src, a.dst), selected in (a.src, a.dst)
        if hot:
            col = _col(0.95, 0.88, 0.45, 1.0)
        elif sel:
            col = _col(0.75, 0.68, 0.35, 0.9)
        else:
            col = _col(0.42, 0.44, 0.48, 0.6)
        th = 2.2 if hot else 1.6 if sel else 1.0
        if a.src == a.dst:
            v = [
                imgui.ImVec2(*q)
                for q in (
                    (sx1 - 10, sy0),
                    (sx1 + 24, sy0 - 26),
                    (sx1 - 44, sy0 - 26),
                    (sx1 - 30, sy0),
                )
            ]
            draw.add_bezier_cubic(v[0], v[1], v[2], v[3], col, th)
            return
        if d.x > s.x + NODE_W * 0.5:
            p1, p4 = (sx1, (sy0 + sy1) / 2), (dx0, (dy0 + dy1) / 2)
            bend = max(30.0, (dx0 - sx1) * 0.5)
            p2, p3 = (sx1 + bend, p1[1]), (dx0 - bend, p4[1])
        elif d.x < s.x - NODE_W * 0.5:
            p1, p4 = (sx0, (sy0 + sy1) / 2), (dx1, (dy0 + dy1) / 2)
            bend = max(30.0, (sx0 - dx1) * 0.5)
            p2, p3 = (sx0 - bend, p1[1]), (dx1 + bend, p4[1])
        else:  # stacked: leave from the bottom or top, arrive at the top or bottom
            down = d.y > s.y
            p1 = ((sx0 + sx1) / 2, sy1 if down else sy0)
            p4 = ((dx0 + dx1) / 2, dy0 if down else dy1)
            lift = (40 if down else -40) * self.zoom
            p2, p3 = (p1[0], p1[1] + lift), (p4[0], p4[1] - lift)
        v = [imgui.ImVec2(*q) for q in (p1, p2, p3, p4)]
        draw.add_bezier_cubic(v[0], v[1], v[2], v[3], col, th)
        _head(draw, v[2], v[3], col, 7.0 * min(self.zoom, 1.2))
        if labelled and a.label:
            t = (0.30, 0.50, 0.70, 0.40, 0.60)[slot % 5]  # staggered: a fan does not pile up
            x, y = _bezier(p1, p2, p3, p4, t)
            lab = a.label if len(a.label) < 44 else a.label[:41] + "..."
            _pill(draw, x, y - 9, lab, _col(0.98, 0.94, 0.70, 1.0))


def _bezier(p1: Point, p2: Point, p3: Point, p4: Point, t: float) -> Point:
    u = 1.0 - t
    return (
        u**3 * p1[0] + 3 * u * u * t * p2[0] + 3 * u * t * t * p3[0] + t**3 * p4[0],
        u**3 * p1[1] + 3 * u * u * t * p2[1] + 3 * u * t * t * p3[1] + t**3 * p4[1],
    )


def _pill(draw: Any, x: float, y: float, text: str, col: int) -> None:
    """Text on a dark rounded backing, readable over arrows and boxes."""
    from imgui_bundle import imgui

    ts = imgui.calc_text_size(text)
    a, b = imgui.ImVec2(x - 4.0, y - 2), imgui.ImVec2(x + float(ts.x) + 4.0, y + float(ts.y) + 2)
    draw.add_rect_filled(a, b, _col(0.05, 0.06, 0.07, 0.92), 4.0)
    draw.add_text(imgui.ImVec2(x, y), col, text)


def _info_box(
    draw: Any,
    origin: Point,
    w: float,
    intel: SpeciesIntel,
    lay: Layout,
    pair: Pair | None,
    moves: Mapping[str, Move],
) -> None:
    """The focused node's hand-offs as text, top right of the canvas."""
    from imgui_bundle import imgui

    dim = pair is None or intel.pair(*pair) is None
    lines = (
        ["click a node to read its hand-offs, double-click to select it"]
        if pair is None or dim
        else graph.info_lines(intel, pair, lay, moves)
    )
    lh = float(imgui.get_text_line_height()) + 3.0
    tw = max(float(imgui.calc_text_size(t).x) for t in lines)
    bw, bh = tw + 20.0, lh * len(lines) + 12.0
    x0, y0 = origin[0] + w - bw - 8.0, origin[1] + 8.0
    a, b = imgui.ImVec2(x0, y0), imgui.ImVec2(x0 + bw, y0 + bh)
    draw.add_rect_filled(a, b, _col(0.05, 0.06, 0.07, 0.90), 6.0)
    draw.add_rect(a, b, _col(0.35, 0.37, 0.40, 1.0), 6.0, 1.0)
    for i, t in enumerate(lines):
        if dim:
            col = _col(0.55, 0.58, 0.62, 1.0)
        elif i == 0:
            col = _col(0.95, 0.95, 0.97, 1.0)
        elif t.startswith("  ->"):
            col = _col(0.98, 0.94, 0.70, 1.0)
        elif t.startswith("double-click"):
            col = _col(0.50, 0.52, 0.56, 1.0)
        else:
            col = _col(0.72, 0.75, 0.80, 1.0)
        draw.add_text(imgui.ImVec2(x0 + 10.0, y0 + 6.0 + i * lh), col, t)


def _legend(draw: Any, origin: Point, h: float) -> None:
    from imgui_bundle import imgui

    x, y = origin[0] + 8, origin[1] + h - 18
    for text, (r, g, b) in (
        ("move", (0.35, 0.70, 0.95)),
        ("attacks", (0.95, 0.60, 0.30)),
        ("hub", (0.45, 0.47, 0.50)),
        ("picked", (0.95, 0.95, 0.95)),
        ("Action tab", (1.0, 0.90, 0.35)),
    ):
        draw.add_rect_filled(
            imgui.ImVec2(x, y + 3), imgui.ImVec2(x + 10, y + 13), _col(r, g, b, 1.0), 2.0
        )
        draw.add_text(imgui.ImVec2(x + 14, y), _col(0.7, 0.72, 0.76, 1.0), text)
        x += 14 + 8 * (len(text) + 2)


def _head(draw: Any, frm: Any, to: Any, col: int, size: float) -> None:
    from imgui_bundle import imgui

    dx, dy = float(to.x) - float(frm.x), float(to.y) - float(frm.y)
    n = (dx * dx + dy * dy) ** 0.5 or 1.0
    ux, uy = dx / n, dy / n
    bx, by = float(to.x) - ux * size, float(to.y) - uy * size
    half = size * 0.5
    draw.add_triangle_filled(
        to,
        imgui.ImVec2(bx - uy * half, by + ux * half),
        imgui.ImVec2(bx + uy * half, by - ux * half),
        col,
    )


def _inside(rect: Rect, pos: Point) -> bool:
    x0, y0, x1, y1 = rect
    return x0 <= pos[0] <= x1 and y0 <= pos[1] <= y1


def _col(r: float, g: float, b: float, a: float) -> int:
    from imgui_bundle import imgui

    return int(imgui.get_color_u32(imgui.ImVec4(r, g, b, a)))
