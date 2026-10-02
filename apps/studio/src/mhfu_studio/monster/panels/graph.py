# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The behaviour pairs as a layered graph: nodes are `(main, sub)`, arrows the hand-offs a handler
makes when its action ends (`PairIntel.next`). No toolkit: the Moves panel draws it.

The engine walks a sequence (the Tigrex charge `(1,4)` hands to the skid `(0,3)`, which hands to
`(0,1)`/`(0,2)` where the brain picks again), so the layout is a DAG from roots (the manifest's
moves, or the selected pair), with the hubs where most hand-offs land drawn once as terminals.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from mhfu.em.intel import PairIntel, SpeciesIntel
from mhfu_port.manifest import Move

Pair = tuple[int, int]
RGBA = tuple[float, float, float, float]

NODE_W, NODE_H = 148.0, 58.0
GAP_X, GAP_Y = 96.0, 22.0
PAD = 18.0
SCOPES = ("moves", "selected", "attacks")
#: a node's colour by `Node.kind`
KINDS: dict[str, RGBA] = {
    "move": (0.35, 0.70, 0.95, 1.0),
    "attacks": (0.95, 0.60, 0.30, 1.0),
    "hub": (0.45, 0.47, 0.50, 1.0),
    "plain": (0.40, 0.42, 0.46, 1.0),
}
NOTES = {
    "moves": "[moves] is empty and nothing is selected: pick a pair in Action, or bind one, "
    "to see its chain",
    "selected": "select a pair in Action",
    "attacks": "no pair in this overlay names an attack id",
}


@dataclass
class Node:
    pair: Pair
    lines: list[str]
    layer: int = 0
    row: int = 0
    x: float = 0.0
    y: float = 0.0
    hub: bool = False
    move: str | None = None
    entry: bool = False
    attacks: bool = False
    #: pairs drawn as this one: same handler, hand-offs and attack ids
    siblings: tuple[Pair, ...] = ()

    @property
    def kind(self) -> str:
        return (
            "hub" if self.hub else "move" if self.move else "attacks" if self.attacks else "plain"
        )


@dataclass
class Arrow:
    src: Pair
    dst: Pair
    label: str
    guards: tuple[str, ...] = ()
    mode: int | None = None


@dataclass
class Layout:
    nodes: dict[Pair, Node] = field(default_factory=dict)
    arrows: list[Arrow] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0
    note: str = ""

    @property
    def empty(self) -> bool:
        return not self.nodes


def _label(p: PairIntel, move: str | None) -> list[str]:
    lines = [f"({p.main},{p.sub})" + (f"  {move}" if move else "")]
    if p.a1:
        lines.append("a1 " + ",".join(map(str, p.a1[:4])) + ("+" if p.a1_computed else ""))
    elif p.a1_computed:
        lines.append("a1 computed")
    bits = []
    if p.attack_ids:
        bits.append("atk " + ",".join(map(str, p.attack_ids[:3])))
    if p.ends_on and p.ends_on != "unknown":
        bits.append(p.ends_on)
    if bits:
        lines.append("  ".join(bits))
    return lines


def build(
    intel: SpeciesIntel | None,
    moves: Mapping[str, Move],
    selected: Pair | None,
    scope: str = "moves",
    depth: int = 6,
) -> Layout:
    """`scope` picks the roots; hubs are never expanded. In the moves scope the selected pair
    joins the roots only when it is not in the picture already: a hub on screen as a terminal
    must not unfold its own hand-offs over the chain being read."""
    lay = _build(intel, moves, selected, scope, depth, extra_root=False)
    if (
        intel is not None
        and scope == "moves"
        and selected is not None
        and not lay.empty
        and selected not in lay.nodes
        and intel.pair(*selected) is not None
    ):
        lay = _build(intel, moves, selected, scope, depth, extra_root=True)
    return lay


def _roots(
    intel: SpeciesIntel,
    bound: Mapping[Pair, str],
    selected: Pair | None,
    scope: str,
    extra_root: bool,
) -> tuple[list[Pair], dict[Pair, tuple[Pair, ...]]]:
    siblings: dict[Pair, tuple[Pair, ...]] = {}
    if scope == "attacks":
        groups: dict[tuple[object, ...], list[Pair]] = {}
        for p in intel:
            if p.attack_ids and p.next is not None:
                sig = (p.handler, tuple(p.successors), p.attack_ids)
                groups.setdefault(sig, []).append((p.main, p.sub))
        roots = []
        for members in groups.values():
            members.sort()
            head = (
                selected
                if selected in members
                else next((m for m in members if m in bound), members[0])
            )
            roots.append(head)
            if len(members) > 1:
                siblings[head] = tuple(m for m in members if m != head)
    elif scope == "selected":
        roots = [selected] if selected else []
    else:
        roots = sorted(bound)
        if extra_root and selected and selected not in roots:
            roots.append(selected)
    return [r for r in roots if intel.pair(*r) is not None], siblings


def _build(
    intel: SpeciesIntel | None,
    moves: Mapping[str, Move],
    selected: Pair | None,
    scope: str,
    depth: int,
    extra_root: bool,
) -> Layout:
    lay = Layout()
    if intel is None or not intel.has_chain:
        lay.note = "no hand-off intel for this overlay: its species intel has no chain"
        return lay
    bound: dict[Pair, str] = {}
    for name in sorted(moves):
        bound.setdefault((moves[name].main, moves[name].sub), name)
    hubs = set(intel.hubs)
    roots, siblings = _roots(intel, bound, selected, scope, extra_root)
    if not roots:
        lay.note = NOTES[scope]
        return lay

    layer_of = dict.fromkeys(roots, 0)
    order = list(roots)
    frontier = list(roots)
    for d in range(1, depth + 1):
        nxt: list[Pair] = []
        for key in frontier:
            p = intel.pair(*key)
            if p is None or (key in hubs and key not in roots):
                continue
            for t in p.successors:
                if intel.pair(*t) is None:
                    continue
                if t not in layer_of:
                    layer_of[t] = d
                    order.append(t)
                    nxt.append(t)
                elif layer_of[t] < d and t not in hubs:
                    # longest path keeps arrows forward, for a root another root reaches too
                    layer_of[t] = d
                    if t not in nxt:
                        nxt.append(t)
        frontier = nxt
        if not frontier:
            break
    terminal = [k for k in order if k in hubs and k not in roots]
    inner = [layer_of[k] for k in order if k not in terminal]
    last = max(inner) + 1 if inner else 0
    for key in terminal:
        layer_of[key] = last

    if selected and scope == "selected":
        preds = [
            (q.main, q.sub)
            for q in intel.predecessors(*selected)
            if (q.main, q.sub) not in layer_of
        ][:14]
        if preds:
            layer_of = {k: v + 1 for k, v in layer_of.items()} | dict.fromkeys(preds, 0)
            order = preds[::-1] + order

    for key in order:
        p = intel.pair(*key)
        if p is None:
            continue
        lines = _label(p, bound.get(key))
        if key in terminal:
            lines = lines[:2]
        sib = siblings.get(key, ())
        if sib:
            lines[0] += f"  +{len(sib)} alike"
        lay.nodes[key] = Node(
            key,
            lines,
            layer_of[key],
            hub=key in hubs,
            move=bound.get(key),
            entry=key in roots,
            attacks=bool(p.attack_ids),
            siblings=sib,
        )
    seen: dict[tuple[Pair, Pair], Arrow] = {}
    for key in order:
        p = intel.pair(*key)
        if p is None or key in terminal:
            continue
        for e in p.next or ():
            for t in e.to:
                if t not in lay.nodes:
                    continue
                a = seen.get((key, t))
                if a is None:
                    seen[key, t] = a = Arrow(key, t, e.reason, e.guards, e.mode)
                    lay.arrows.append(a)
                elif e.reason and e.reason not in a.label:
                    a.label = f"{a.label} / {e.reason}" if a.label else e.reason
    _place(lay)
    return lay


def _place(lay: Layout) -> None:
    """Rows by the barycentre of the predecessors, one pass; columns by layer."""
    cols: dict[int, list[Node]] = {}
    for n in lay.nodes.values():
        cols.setdefault(n.layer, []).append(n)
    pos_of: dict[Pair, float] = {}
    for layer in sorted(cols):
        col = cols[layer]

        def bary(n: Node) -> tuple[float, Pair]:
            ys = [pos_of[a.src] for a in lay.arrows if a.dst == n.pair and a.src in pos_of]
            return (sum(ys) / len(ys) if ys else 1e9), n.pair

        col.sort(key=(lambda n: (0.0, n.pair)) if layer == 0 else bary)
        for i, n in enumerate(col):
            n.row = i
            pos_of[n.pair] = float(i)
    tallest = max(len(c) for c in cols.values())
    for layer, col in cols.items():
        top = (tallest - len(col)) * (NODE_H + GAP_Y) / 2.0
        for n in col:
            n.x = PAD + layer * (NODE_W + GAP_X)
            n.y = PAD + top + n.row * (NODE_H + GAP_Y)
    lay.width = PAD * 2 + (max(cols) + 1) * (NODE_W + GAP_X) - GAP_X
    lay.height = PAD * 2 + tallest * (NODE_H + GAP_Y) - GAP_Y


def walk_line(intel: SpeciesIntel, pair: Pair) -> str:
    """Where a pair hands to, on one line."""
    p = intel.pair(*pair)
    if p is None or p.next is None:
        return f"({pair[0]},{pair[1]}): no hand-off intel"
    if not p.next:
        return (
            f"({p.main},{p.sub}) never ends itself: it stays until the brain or a flinch moves it"
        )
    bits = [
        "/".join(f"({m},{s})" for m, s in e.to) + (f" when {e.reason}" if e.reason else "")
        for e in p.next
    ]
    return f"({p.main},{p.sub}) ends -> " + "  |  ".join(bits)


def info_lines(
    intel: SpeciesIntel, pair: Pair, lay: Layout, moves: Mapping[str, Move]
) -> list[str]:
    """A node's hand-offs as text: the readable form of its arrows."""
    p = intel.pair(*pair)
    if p is None:
        return [f"({pair[0]},{pair[1]})"]
    head = f"({p.main},{p.sub})"
    for name, mv in sorted(moves.items()):
        if (mv.main, mv.sub) == pair:
            head += f"  {name} -> clip {mv.clip or '?'}"
            if mv.after:
                head += f"  (after = {mv.after})"
    lines = [head]
    n = lay.nodes.get(pair)
    if n is not None and n.siblings:
        more = " ..." if len(n.siblings) > 8 else ""
        lines.append(
            f"  + {len(n.siblings)} alike: "
            + " ".join(f"({m},{s})" for m, s in n.siblings[:8])
            + more
        )
    bits = []
    if p.handler:
        bits.append(f"handler 0x{p.handler:08X}")
    if p.a1:
        bits.append("a1 " + ",".join(map(str, p.a1[:5])) + ("+" if p.a1_computed else ""))
    if p.attack_ids:
        bits.append("attack " + ",".join(map(str, p.attack_ids)))
    if p.ends_on and p.ends_on != "unknown":
        bits.append("ends on " + p.ends_on)
    if bits:
        lines.append("  " + "   ".join(bits))
    if p.next:
        lines.append("hands to:")
        for e in p.next[:10]:
            tgt = "/".join(f"({m},{s})" for m, s in e.to) or "(computed)"
            lines.append(f"  -> {tgt:<12} {e.describe() or 'always'}")
        if len(p.next) > 10:
            lines.append(f"  ... {len(p.next) - 10} more")
    elif p.next is not None:
        lines.append("hands to: nothing, it never ends by itself")
    if p.prev:
        more = " ..." if len(p.prev) > 10 else ""
        lines.append("entered from: " + " ".join(f"({m},{s})" for m, s in p.prev[:10]) + more)
    else:
        lines.append("entered from: the brain (no handler hands here)")
    if p.measured:
        lines.append(f"census: entered {p.entered}, dwell {p.dwell_ticks:.1f} ticks")
    lines.append("double-click: select it in Action")
    return lines


class MoveGraph:
    """The graph's per-document state: the scope, the picked node and the layout, whose node
    positions a drag moves (so they outlive the panel)."""

    def __init__(self) -> None:
        self.scope = "moves"
        self.picked: Pair | None = None
        #: the layout is new to the view, which frames it and clears this
        self.fresh = True
        self._layout: Layout | None = None
        self._key: tuple[object, ...] | None = None

    def layout(
        self, intel: SpeciesIntel | None, moves: Mapping[str, Move], selected: Pair | None
    ) -> Layout:
        """Rebuilt when its inputs change; the same nodes keep where they were dragged."""
        key = (
            id(intel),
            self.scope,
            selected,
            tuple(sorted((n, m.main, m.sub) for n, m in moves.items())),
        )
        if self._layout is None or key != self._key:
            old, self._layout, self._key = (
                self._layout,
                build(intel, moves, selected, self.scope),
                key,
            )
            if old is not None and old.nodes.keys() == self._layout.nodes.keys():
                for k, n in self._layout.nodes.items():
                    n.x, n.y = old.nodes[k].x, old.nodes[k].y
            else:
                self.fresh = True
            if self.picked not in self._layout.nodes:
                self.picked = None
        return self._layout

    def set_scope(self, scope: str) -> None:
        if scope in SCOPES:
            self.scope = scope

    def relayout(self) -> None:
        """Forgets the dragged positions: the next layout is placed afresh."""
        self._layout, self._key = None, None
