# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""One trigger's rules as rows, for the Behaviour canvas: display only, nothing here is written
to a block's `at`.

A row is one rule in the order the brain checks them, in a frame titled `#n · note`: its blocks
left to right and the move it plays as a reference at its end. A rule with no move yet comes
after them, ending in a placeholder. A trigger that is a playing move shows that move once, on
the left, wired into every row. `plan` makes the nodes and wires, `place` puts them where the
drawn sizes say.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from mhfu_port.behaviour import Path, Trigger

from mhfu_studio.monster import behaviour as graph
from mhfu_studio.monster.panels.node_graph import (
    FRAME_BOTTOM,
    FRAME_PAD,
    FRAME_TOP,
    FrameSpec,
    LinkSpec,
    NodeSpec,
)

#: a displayed node's id: the reference after block `b` to move `m` is `ref:b:m`, the placeholder
#: after it `open:b`; a second drawing of block `b` is `b~2`, and `b~2`'s reference `ref:b~2:m`
REF, OPEN, COPY = "ref:", "open:", "~"
FRAME, NEW = "frame:", "new"
ARROW = "▸"
TIMES = "×"
NO_NOTE = "(no note)"
UNFINISHED = "unfinished"
PLACEHOLDER = f"{ARROW} plays…"
#: the room between a row's nodes, between its frame and the next, and after a move trigger
GAP_X, GAP_Y, TRIGGER_GAP = 46.0, 14.0, 90.0
Size = tuple[float, float]


def node_of(shown: str) -> str:
    """The node of the document a displayed node stands for: a reference its move, a second
    drawing of a block the block; a placeholder or a frame stands for none (empty)."""
    ref = ref_of(shown)
    if ref is not None:
        return graph.move_id(ref[1]) if ref[1] else ""
    if shown.startswith(FRAME):
        return ""
    return shown.split(COPY)[0]


def ref_of(shown: str) -> tuple[str, str | None] | None:
    """For a reference or a placeholder, the block it follows and the move it stands for (None:
    the placeholder, no move yet); None for any other node."""
    if shown.startswith(REF):
        _, block, move = shown.split(":")
        return block.split(COPY)[0], move
    if shown.startswith(OPEN):
        return shown.removeprefix(OPEN).split(COPY)[0], None
    return None


def players(ps: Sequence[Path]) -> dict[str, tuple[int, ...]]:
    """For each move some rule plays, the places in `ps` of the rules that play it."""
    out: dict[str, list[int]] = {}
    for rank, p in enumerate(ps, 1):
        if p.play:
            out.setdefault(p.play, []).append(rank)
    return {name: tuple(ranks) for name, ranks in out.items()}


@dataclass(frozen=True)
class Row:
    """One rule: its frame `id`, `rank` (None: no move yet), the `path` it is (its `play` empty
    if it has none), its nodes in order and the `role` that tints its frame."""

    id: str
    rank: int | None
    title: str
    nodes: tuple[NodeSpec, ...]
    role: str
    path: Path


@dataclass(frozen=True)
class Plan:
    """A trigger as rows of nodes not yet placed, the wires between them, and the move node a
    while-playing trigger starts from."""

    rows: tuple[Row, ...]
    links: tuple[LinkSpec, ...]
    start: NodeSpec | None = None

    @property
    def nodes(self) -> list[NodeSpec]:
        return [*([self.start] if self.start else []), *(n for r in self.rows for n in r.nodes)]

    def row_of(self, shown: str) -> Row | None:
        """The row a displayed node or frame belongs to."""
        return next(
            (r for r in self.rows if r.id == shown or any(n.id == shown for n in r.nodes)), None
        )


def note_of(blocks: Mapping[str, str], p: Path) -> str:
    """The notes of `p`'s blocks, joined; `NO_NOTE` when they have none."""
    return "; ".join(blocks[i] for i in p.blocks if blocks[i]) or NO_NOTE


def plan(
    t: Trigger,
    blocks: Mapping[str, NodeSpec],
    notes: Mapping[str, str],
    moves: Mapping[str, NodeSpec],
    played: Mapping[str, tuple[int, ...]],
    opens: Sequence[Path] = (),
) -> Plan:
    """`t`'s rules as rows, a frame tinted as the trigger is, then the `opens`: rules of `t` with
    no move yet. `blocks` and `moves` are the specs of the document's nodes, `notes` each
    block's note, `played` the places of the rules that play each move (`players`)."""
    start = moves.get(t.name) if t.section == "move" else None
    seen: Counter[str] = Counter()
    news: Counter[str] = Counter()
    rows: list[Row] = []
    links: list[LinkSpec] = []
    for rank, p in [*t.rules, *((None, o) for o in opens)]:
        ids: list[str] = []
        nodes: list[NodeSpec] = []
        for b in p.blocks:  # a block on two rules is drawn twice, the copy faded
            seen[b] += 1
            shown = b if seen[b] == 1 else f"{b}{COPY}{seen[b]}"
            marks = [m for m in blocks[b].badge.split() if not m.startswith("#")]
            nodes.append(
                dataclasses.replace(blocks[b], id=shown, dim=seen[b] > 1, badge=" ".join(marks))
            )
            ids.append(shown)
        links += [LinkSpec(a, graph.OUT, b, graph.IN) for a, b in zip(ids, ids[1:], strict=False)]
        if start is not None:
            links.append(LinkSpec(start.id, graph.WHILE, ids[0], graph.IN))
        end: NodeSpec | None = None
        if p.play:
            by = played.get(p.play, ())
            end = NodeSpec(
                f"{REF}{ids[-1]}:{p.play}",
                f"{ARROW} {p.play}",
                "move",
                (0.0, 0.0),
                (graph.PLAY,),
                badge=f"{TIMES}{len(by)}" if len(by) > 1 else "",
                tip=f"The move {p.play}, played by {len(by)} "
                f"rule{'' if len(by) == 1 else 's'}: " + ", ".join(f"#{k}" for k in by),
                ref=True,
            )
        elif rank is None:  # a rule that plays nothing by design ends at its last block
            end = NodeSpec(
                f"{OPEN}{ids[-1]}",
                PLACEHOLDER,
                "move",
                (0.0, 0.0),
                (graph.PLAY,),
                tip="This rule plays no move yet, so it does not run: select this and pick one"
                " in the bar",
                ref=True,
            )
        if end is not None:
            nodes.append(end)
            links.append(LinkSpec(ids[-1], graph.OUT, end.id, graph.PLAY))
        if rank is None:
            news[p.blocks[0]] += 1
            frame, title = f"{FRAME}{NEW}:{p.blocks[0]}:{news[p.blocks[0]]}", UNFINISHED
        else:
            frame, title = f"{FRAME}{rank}", f"#{rank}"
        rows.append(Row(frame, rank, f"{title} · {note_of(notes, p)}", tuple(nodes), t.section, p))
    return Plan(tuple(rows), tuple(links), start)


def place(plan_: Plan, sizes: Mapping[str, Size]) -> tuple[list[NodeSpec], list[FrameSpec]]:
    """Positions from the drawn `sizes`: a row per rule from the top, its nodes left to right, a
    frame round each; a move trigger on the left, level with the middle of the rows."""
    x0 = 0.0 if plan_.start is None else sizes[plan_.start.id][0] + TRIGGER_GAP + FRAME_PAD
    out: list[NodeSpec] = []
    frames: list[FrameSpec] = []
    y = 0.0
    for row in plan_.rows:
        x, tall = x0, 0.0
        for spec in row.nodes:
            w, h = sizes[spec.id]
            out.append(dataclasses.replace(spec, at=(x, y)))
            x += w + GAP_X
            tall = max(tall, h)
        ids = tuple(n.id for n in row.nodes)
        frames.append(FrameSpec(row.id, row.title, ids, row.role))
        y += tall + FRAME_TOP + FRAME_BOTTOM + GAP_Y
    if plan_.start is not None:
        used = y - FRAME_TOP - FRAME_BOTTOM - GAP_Y if plan_.rows else 0.0
        mid = (used - sizes[plan_.start.id][1]) / 2
        out.insert(0, dataclasses.replace(plan_.start, at=(0.0, max(0.0, mid))))
    return out, frames
