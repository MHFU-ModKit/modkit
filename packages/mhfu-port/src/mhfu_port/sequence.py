# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Sequences: moves that run one after another through `Move.after`.

A sequence is a chain of moves, each handing to the next. Its head is a move no other move's
`after` names; a ring of moves with no head is headed by its alphabetically first. A move that
two chains reach belongs to the first head, in the manifest's order.
"""

from __future__ import annotations

from .manifest import Manifest, ManifestError


def chain(m: Manifest, head: str) -> list[str]:
    """`head` and the moves it hands to in turn, up to the first repeat."""
    out: list[str] = []
    name: str | None = head
    while name is not None and name in m.moves and name not in out:
        out.append(name)
        name = m.moves[name].after
    return out


def loops(m: Manifest, head: str) -> bool:
    """Whether the chain from `head` ends by handing back to one of its own moves."""
    steps = chain(m, head)
    return bool(steps) and m.moves[steps[-1]].after is not None


def heads(m: Manifest) -> list[str]:
    """Each sequence's head, in the manifest's order."""
    named = {mv.after for mv in m.moves.values() if mv.after is not None}
    out = [n for n in m.moves if n not in named]
    reached = {s for h in out for s in chain(m, h)}
    for n in sorted(m.moves):  # what is left is rings: every move in one is named by another
        if n not in reached:
            out.append(n)
            reached.update(chain(m, n))
    order = list(m.moves)
    return sorted(out, key=order.index)


def head_of(m: Manifest, name: str) -> str:
    """The head of the sequence `name` belongs to."""
    for h in heads(m):
        if name in chain(m, h):
            return h
    raise ManifestError(f"no move {name!r}")


def step_name(m: Manifest, head: str) -> str:
    """The lowest `<head>_<k>` from k = 2 that no move has."""
    k = 2
    while f"{head}_{k}" in m.moves:
        k += 1
    return f"{head}_{k}"
