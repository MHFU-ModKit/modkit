# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Greedy stripifier that hands out strips of a requested length.

A mesh edited into an existing display list has to fit the PRIMs already there, each drawing a
fixed count, so the caller asks for "a strip of at most N vertices" (or N loose triangles for a
list) rather than for the stripifier's own decomposition.
"""

from collections.abc import Iterable

Triangle = tuple[int, int, int]


class Stripper:
    """Hands out each triangle once, in strips or loose, wound as `ge.triangles` reads them."""

    def __init__(self, triangles: Iterable[Triangle]) -> None:
        self._tris = list(triangles)
        self._alive = [True] * len(self._tris)
        self._left = len(self._tris)
        self._next = 0
        self._by_edge: dict[tuple[int, int], list[int]] = {}
        for i, (a, b, c) in enumerate(self._tris):
            if len({a, b, c}) == 3:  # a degenerate one only ever starts a strip, drawn as given
                for edge in ((a, b), (b, c), (c, a)):
                    self._by_edge.setdefault(edge, []).append(i)

    def __len__(self) -> int:
        """Triangles not handed out yet."""
        return self._left

    def strip(self, max_vertices: int, face_order: int = 0) -> list[int]:
        """A strip of at most `max_vertices` indices, `[]` when none is left or it is under 3.

        The first triangle starts in whichever of its rotations has a neighbour to go on to,
        so a quad given as (a, b, c), (a, c, d) still makes one strip; winding is kept."""
        if max_vertices < 3 or not self._left:
            return []
        a, b, c = self._tris[self._take(self._first())]
        rotations = [[y, x, z] if face_order & 1 else [x, y, z] for x, y, z in _turns(a, b, c)]
        out = next(
            (r for r in rotations if self._follow(r, 1, face_order) is not None), rotations[0]
        )
        k = 1
        while len(out) < max_vertices:
            nxt = self._follow(out, k, face_order)
            if nxt is None:
                break
            out.append(self._third(self._take(nxt), out[-2], out[-1]))
            k += 1
        return out

    def _follow(self, out: list[int], k: int, face_order: int) -> int | None:
        """A live triangle that can be triangle `k` of the strip `out`."""
        p, q = out[-2], out[-1]
        # triangle k of a strip is (p, q, x), or (q, p, x) when its winding flips
        edge = (q, p) if (k + face_order) & 1 else (p, q)
        return next((t for t in self._by_edge.get(edge, ()) if self._alive[t]), None)

    def loose(self, count: int, face_order: int = 0) -> list[Triangle]:
        """Up to `count` triangles for a triangle-list PRIM."""
        out: list[Triangle] = []
        while self._left and len(out) < count:
            a, b, c = self._tris[self._take(self._first())]
            out.append((b, a, c) if face_order & 1 else (a, b, c))
        return out

    def _first(self) -> int:
        while not self._alive[self._next]:
            self._next += 1
        return self._next

    def _take(self, i: int) -> int:
        self._alive[i] = False
        self._left -= 1
        return i

    def _third(self, i: int, p: int, q: int) -> int:
        return next(x for x in self._tris[i] if x != p and x != q)


def _turns(a: int, b: int, c: int) -> list[Triangle]:
    """The three rotations of a triangle, the given one first: all wound alike."""
    return [(a, b, c), (b, c, a), (c, a, b)]
