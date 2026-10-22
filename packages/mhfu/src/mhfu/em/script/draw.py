# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A random's pick: which branch a draw takes, and what a random inside a branch can reach.

`random` reads the draw (the entity's counter, low five bits) and does not advance it, so a
`random` that is the first node of a branch of a `random` reads the draw its parent read, and only
the values its parent's branch took can reach it. `nested` is the one place that says which
randoms those are and what they can see.
"""

from __future__ import annotations

from collections.abc import Sequence

from .ir import Block, Node

__all__ = ["DRAW", "nested", "spans", "take", "weights"]

DRAW = 32
"""Values of a pick's draw; a branch's weight is a share of it."""


def weights(b: Block) -> list[int] | None:
    """The weight of each arm, None unless every arm has a number."""
    ws = [a.args.get("weight") for a in b.arms]
    return [w for w in ws if isinstance(w, int)] if all(isinstance(w, int) for w in ws) else None


def take(ws: Sequence[int], d: int) -> int:
    """The branch a draw of `d` takes: a 0 is skipped, a 255 takes the rest, the others take their
    weight in turn and the last branch gets what is left."""
    total = 0
    for k, w in enumerate(ws):
        if w == 0:
            continue
        total += w
        if w == 0xFF or d < total:
            return k
    return len(ws) - 1


def spans(ws: Sequence[int], lo: int = 0, hi: int = DRAW) -> list[range]:
    """The draws in `lo..hi` that each branch takes; empty for a branch none of them reaches."""
    out = [range(0)] * len(ws)
    for d in range(lo, hi):
        k = take(ws, d)
        out[k] = range(out[k].start if out[k] else d, d + 1)
    return out


def nested(nodes: Sequence[Node], sees: range | None = None) -> dict[int, range]:
    """Each `random` that is the first node of a branch of a `random`, by `id()`, with the draws
    that can reach it: its branch's, within its parent's. `sees` is the first node's, when
    `nodes` is such a branch. A branch that no draw reaches has none of them in it."""
    out: dict[int, range] = {}
    for k, n in enumerate(nodes):
        if not isinstance(n, Block):
            continue
        mine = sees if k == 0 and n.op.name == "random" else None
        if mine is not None:
            out[id(n)] = mine
        ws = weights(n) if n.op.name == "random" else None
        within = mine or range(DRAW)
        got = spans(ws, within.start, within.stop) if ws else [range(0)] * len(n.arms)
        out |= nested(n.head)
        for a, span in zip(n.arms, got, strict=True):
            out |= nested(a.body, span or None)
    return out
