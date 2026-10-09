# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The skeleton a port ships, how the donor's bones land on its joints, and its tail tip.

MHFU's animation FK walks the animated joints in streams (the Tigrex: 31 body, 9 head, 5 tail),
each a contiguous run of joint indices named by `Bone.stream`; the joints past the animated count
take one more stream id. A parent keeps a lower index than its children.

A severable tail's tip is a second root chain at `params[1]`, past the animated joints: the
engine poses it from its own binds (`roots[1]`) and its dropped-tail object draws it as PMO mesh
record 1. While the tail is whole, each chain joint copies a live tail joint's pose (`Tip`).
"""

from __future__ import annotations

import copy
import math
from collections.abc import Sequence
from dataclasses import dataclass

from mhp_formats.skeleton import FU_MAGIC, Bone, Skeleton, Vec3

from .fk import bind_world

APPENDAGE_SHARE = 0.4
"""The largest share of the animated joints a head or tail stream may take."""

ORIGIN_EPS = 1.0
"""A joint this close to the model origin belongs to the leading origin chain."""

OFFSET_TOL = 0.5
"""Units two bind positions may differ by and still be the same joint."""


@dataclass(frozen=True)
class Tip:
    """A severable tail's tip chain and the live joints it rides while the tail is whole."""

    pairs: tuple[tuple[int, int], ...]
    """`(joint, carrier)`: the root, then the chain in index order. A joint that copies its
    carrier's pose skins as `carrier_skin · T(offset)`, which lays the tip on the stump."""
    offset: Vec3
    """`bind(carrier) - bind(joint)`, one for the whole chain; the root (no vertices) takes its
    first child's carrier."""

    @property
    def joints(self) -> list[int]:
        return [j for j, _ in self.pairs]


def tip_of(skeleton: Skeleton) -> Tip | None:
    """The chain rooted at `params[1]` and its carriers: the joints of the body tree at each
    chain joint's bind plus one offset. None without a second root there or with a bare root.

    Of several such offsets, the one whose carriers keep the chain's parent links and end the
    body's tree (a neck can mirror a short chain too); raises when none, or a tie, is left."""
    n = len(skeleton.bones)
    root = skeleton.params[1] if len(skeleton.params) > 1 else 0
    if not 0 < root < n or skeleton.bones[root].parent >= 0:
        return None
    parents = [b.parent for b in skeleton.bones]
    kids = _children(parents)
    chain = sorted(_subtree(kids, root))[1:]
    if not chain:
        return None
    bind = _bind_of(skeleton)
    live = sorted(_subtree(kids, 0))
    found: dict[tuple[int, ...], Vec3] = {}
    for c in live:
        d = _minus(bind[c], bind[chain[0]])
        at = [_at(bind, live, _plus(bind[j], d)) for j in chain]
        hit = tuple(k for k in at if k is not None)
        if len(hit) == len(chain):
            found[hit] = d

    def fit(carriers: tuple[int, ...]) -> tuple[bool, bool]:
        of = dict(zip(chain, carriers, strict=True))
        linked = all(parents[of[j]] == of[parents[j]] for j in chain if parents[j] in of)
        ends = all(k in carriers for c in carriers for k in kids.get(c, ()))
        return linked, ends

    best = max(map(fit, found), default=None)
    picks = [c for c in found if fit(c) == best]
    if len(picks) != 1:
        why = "no one offset pairs" if not picks else f"{len(picks)} offsets pair"
        raise ValueError(f"{why} the tip chain {root}..{chain[-1]} with the body's joints")
    (pick,) = picks
    return Tip(((root, pick[0]), *zip(chain, pick, strict=True)), found[pick])


def _at(bind: Sequence[Vec3], joints: Sequence[int], at: Vec3) -> int | None:
    """The joint of `joints` nearest `at` within `OFFSET_TOL`."""
    gap, j = min(((math.dist(bind[j], at), j) for j in joints), default=(math.inf, -1))
    return j if gap < OFFSET_TOL else None


def _minus(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _plus(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


@dataclass(frozen=True)
class Rig:
    skeleton: Skeleton
    """PAC entry 0 of the port; `params[1]` is the animated joint count."""
    parents: list[int]
    """Per output joint."""
    bind: list[Vec3]
    """Each output joint's bind position in model space (the FK of `skeleton`)."""
    streams: list[int]
    """Joints per MHFU animation stream, in joint order; the sum is the animated count."""
    joint_of: dict[int, int]
    """Donor bone -> output joint; empty on the host's rig."""
    lead_pad: int
    """Origin joints put in front of the donor's so its hip lands on the host's hip joint."""
    tip: Tip | None

    @property
    def animated(self) -> int:
        return sum(self.streams)


def from_donor(donor: Skeleton, host: Skeleton, animated: int) -> Rig:
    """Ship the donor's own skeleton: `animated` donor bones (clamped to the rig) are split into
    streams read off its bone tree and reordered so each stream is a contiguous run.

    The host's overlay pins the hip to a joint index, the end of its leading origin chain, so
    the donor's shorter chain is padded to the same length. A second root (the tail tip's chain)
    stays unparented at `params[1]`, as on a native rig; raises for one anywhere else.
    """
    n = len(donor.bones)
    if not 0 < animated <= n:
        animated = n
    parents = [b.parent for b in donor.bones]
    if any(p >= i for i, p in enumerate(parents)):
        raise ValueError("a donor bone's parent does not come before it")
    roots = [i for i, p in enumerate(parents) if i and p < 0]
    if roots and roots != [animated]:
        raise ValueError(
            f"donor roots at bones {roots}: only one, at the animated count {animated}, can be "
            "the tail tip's"
        )
    bind = bind_world(parents, [b.position for b in donor.bones])
    lead_pad = max(0, lead_origin(_bind_of(host)) - lead_origin(bind))

    streams, order = partition(parents, bind, animated)
    streams[0] += lead_pad
    joint_of = {old: new + lead_pad for new, old in enumerate(order)}
    out_parents = [j - 1 for j in range(lead_pad)]
    # the donor's root hangs off the pad; the tip's root stays a root
    out_parents += [
        p + lead_pad if p >= 0 else lead_pad - 1 if not new else -1
        for new, p in enumerate(reorder(parents, order))
    ]

    shape = [Bone() for _ in range(lead_pad)]
    for old in order:
        b = donor.bones[old]
        shape.append(Bone(scale=b.scale, rotation=b.rotation, position=b.position))
    bones = _linked(shape, out_parents, streams)
    skeleton = Skeleton(bones, [0, sum(streams)], magic=FU_MAGIC)
    out_bind = bind_world(out_parents, [b.position for b in bones])
    return Rig(skeleton, out_parents, out_bind, streams, joint_of, lead_pad, tip_of(skeleton))


def from_host(host: Skeleton) -> Rig:
    """Ship the host's skeleton unchanged, for a moveset retargeted onto it; the streams are
    counted from its bones."""
    if len(host.params) < 2 or not 0 < host.params[1] <= len(host.bones):
        raise ValueError("the host skeleton names no animated joint count")
    streams = _streams_of(host.bones[: host.params[1]])
    if streams is None:
        raise ValueError("the host's animated joints are not contiguous stream runs")
    parents = [b.parent for b in host.bones]
    return Rig(copy.deepcopy(host), parents, _bind_of(host), streams, {}, 0, tip_of(host))


def partition(
    parents: Sequence[int], bind: Sequence[Vec3], animated: int
) -> tuple[list[int], list[int]]:
    """`(streams, order)`: joints per stream and the new -> old bone permutation that makes each
    stream a contiguous run: body, then head, then tail.

    The tail is the subtree holding the most -Z animated bone, the head the most +Z; a native
    rig comes back unchanged, a donor's head in mid order moves behind the body. Only whole
    subtrees move behind their parent, so a parent stays ahead of its children.
    """
    n = len(parents)
    animated = max(0, min(animated, n))
    identity = list(range(n))
    if animated < 3:
        return [animated or n], identity
    kids = _children(parents)
    limit = max(1, int(animated * APPENDAGE_SHARE))
    tail = _pick_appendage(parents, kids, bind, limit, set(), animated, head=False)
    head = _pick_appendage(parents, kids, bind, limit, tail, animated, head=True)
    if head & tail or len(head) + len(tail) >= animated:
        head = set()
    body = [i for i in range(animated) if i not in head and i not in tail]
    if not body:
        return [animated], identity
    streams = [len(body)] + [len(x) for x in (head, tail) if x]
    return streams, body + sorted(head) + sorted(tail) + identity[animated:]


def reorder(parents: Sequence[int], order: Sequence[int]) -> list[int]:
    """The parent array after the new -> old permutation `order`."""
    pos = {old: new for new, old in enumerate(order)}
    return [pos.get(parents[old], -1) for old in order]


def lead_origin(bind: Sequence[Vec3]) -> int:
    """How many leading joints sit at the model origin: the structural root chain."""
    count = 0
    for p in bind:
        if math.hypot(*p) >= ORIGIN_EPS:
            break
        count += 1
    return count


def _bind_of(skeleton: Skeleton) -> list[Vec3]:
    return bind_world([b.parent for b in skeleton.bones], [b.position for b in skeleton.bones])


def _linked(bones: list[Bone], parents: list[int], streams: list[int]) -> list[Bone]:
    """`bones` with parent, first child, next sibling and stream set; roots are no siblings."""
    stream = [s for s, count in enumerate(streams) for _ in range(count)]
    stream += [len(streams)] * (len(bones) - len(stream))
    child = [-1] * len(bones)
    sibling = [-1] * len(bones)
    last: dict[int, int] = {}
    for i, p in enumerate(parents):
        if p < 0:
            continue
        if p in last:
            sibling[last[p]] = i
        else:
            child[p] = i
        last[p] = i
    for i, b in enumerate(bones):
        b.parent, b.child, b.sibling, b.stream = parents[i], child[i], sibling[i], stream[i]
    return bones


def _streams_of(bones: Sequence[Bone]) -> list[int] | None:
    """Joints per stream when the stream ids run 0, 1, 2... in joint order, else None."""
    counts: list[int] = []
    for b in bones:
        if b.stream == len(counts) - 1:
            counts[-1] += 1
        elif b.stream == len(counts):
            counts.append(1)
        else:
            return None
    return counts or None


def _children(parents: Sequence[int]) -> dict[int, list[int]]:
    kids: dict[int, list[int]] = {}
    for i, p in enumerate(parents):
        kids.setdefault(p, []).append(i)
    return kids


def _subtree(kids: dict[int, list[int]], root: int) -> set[int]:
    out, stack = {root}, [root]
    while stack:
        for c in kids.get(stack.pop(), ()):
            out.add(c)
            stack.append(c)
    return out


def _appendage(
    parents: Sequence[int],
    kids: dict[int, list[int]],
    leaf: int,
    limit: int,
    taken: set[int],
    ceiling: int,
) -> set[int]:
    """The largest complete subtree above `leaf` that fits `limit`, stays below `ceiling` and
    misses `taken`. A partial one would leave the leaf's children ahead of it."""
    best = _subtree(kids, leaf)
    if len(best) > limit or max(best) >= ceiling:
        return set()
    at = leaf
    while parents[at] > 0:
        up = _subtree(kids, parents[at])
        if len(up) > limit or up & taken or max(up) >= ceiling:
            break
        at, best = parents[at], up
    return best


def _pick_appendage(
    parents: Sequence[int],
    kids: dict[int, list[int]],
    bind: Sequence[Vec3],
    limit: int,
    taken: set[int],
    ceiling: int,
    head: bool,
) -> set[int]:
    """The stream at one end of the body (the head at +Z, the tail at -Z), falling back to the
    most extreme multi-joint subtree when the extreme joint's own is a bare leaf."""
    pick = max if head else min
    live = [i for i in range(ceiling) if i not in taken]
    if not live:
        return set()
    best = _appendage(parents, kids, pick(live, key=lambda i: bind[i][2]), limit, taken, ceiling)
    if len(best) >= 2:
        return best
    whole = (_subtree(kids, a) for a in range(1, ceiling) if parents[a] >= 0)
    cands = [st for st in whole if 2 <= len(st) <= limit and max(st) < ceiling and not st & taken]
    if not cands:
        return best
    return pick(cands, key=lambda st: pick(bind[i][2] for i in st))
