# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Offline checks of a built port: the invariants the engine enforces, how far its clips pull the
mesh apart, and whether the donor's rig and skin survived.

Rank stretch by absolute growth, never by ratio. Magnitude alone does not prove a defect either:
the native Tigrex stretches an elbow by ~240 units. What separates a torn port is where: an edge
whose ends ride different halves of the body fork (`tear`). Native reads 0 there; a Zinogre with
its location records below the fork read 208, its "L-shaped back".
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import cached_property

import numpy as np
from mhp_formats import fu, pmo
from mhp_formats.anim import Clip, channel_kind
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Skeleton
from numpy.typing import NDArray

from . import constraints, fk, mesh, motion, records
from .fidelity import Fidelity, Weights, compare, expected
from .mesh import Part, Skinned
from .model import ANIMATION, MODEL, SKELETON
from .motion import frames
from .rig import OFFSET_TOL, Tip, tip_of

TEAR_LIMIT = 120.0
"""Units an edge across the body fork may grow before the port counts as torn."""
FRACTIONS = (0.25, 0.5, 0.75)
"""Where in each clip the stretch is sampled."""
MIN_FRAMES = 4
"""Clips shorter than this are not sampled."""
GROWTH_EPS = 1e-3
"""Units of growth below which an edge counts as rigid: what the blend's rounding leaves."""
STILL_SHARE = 0.25
"""The share of vertices that may ride joints the animation does not reach."""
MAX_PAD = 8
"""Joints down the port's lead chain tried as the image of the donor's root."""


class Port:
    """A built model PAC, read for checking."""

    def __init__(self, pac: bytes) -> None:
        entries = Pac.from_bytes(pac).entries
        self.skeleton = Skeleton.from_bytes(entries[SKELETON])
        self.model = pmo.Pmo.from_bytes(entries[MODEL])
        self.anim = fu.Anim.from_bytes(entries[ANIMATION])
        self.rig = fk.Rig.from_skeleton(self.skeleton)
        self.parents = [b.parent for b in self.skeleton.bones]

    @property
    def animated(self) -> int:
        """`constraints.animated`, all joints where that does not fit the skeleton."""
        n = constraints.animated(self.skeleton)
        return n if 0 < n <= len(self.parents) else len(self.parents)

    def clip(self, slot: int) -> Clip | None:
        return fk.rig_clip(self.anim, slot, self.skeleton)

    def slots(self) -> list[int]:
        """Every executor entry some stream fills."""
        return motion.filled(self.anim)

    def distinct(self) -> Iterator[tuple[int, Clip]]:
        """`(entry, clip)` once per distinct clip, at its lowest entry."""
        seen = set()
        parts = sorted({b.stream for b in self.skeleton.bones})
        for slot in self.slots():
            key = tuple(id(fk.part_clip(self.anim, k, slot)) for k in parts)
            clip = self.clip(slot)
            if key not in seen and clip is not None:
                seen.add(key)
                yield slot, clip

    @cached_property
    def surface(self) -> Surface:
        return Surface.of(self.model, self.rig.n)


@dataclass(frozen=True)
class Surface:
    """Every group's vertices as one skin, its triangles, and the edges between them."""

    skin: fk.Skin
    triangles: NDArray[np.intp]
    """`(triangles, 3)` vertex indices."""
    group: NDArray[np.intp]
    """Per triangle."""
    edges: NDArray[np.intp]
    """`(edges, 2)` vertex indices, lower first, each edge once."""
    edge_of: NDArray[np.intp]
    """`(triangles, 3)`: each triangle's edges ab, bc, ca as rows of `edges`."""
    dominant: NDArray[np.intp]
    """Per vertex, its heaviest joint; -1 where none carries it."""

    @classmethod
    def of(cls, model: pmo.Pmo, joints: int) -> Surface:
        positions: list[tuple[float, float, float]] = []
        influences = []
        triangles, group = [], []
        for g in range(len(model.groups())):
            base = len(positions)
            positions += model.positions(g)
            influences += model.influences(g)
            tris = model.triangles(g)
            triangles += [(base + a, base + b, base + c) for a, b, c in tris]
            group += [g] * len(tris)
        tri = np.array(triangles, dtype=np.intp).reshape(-1, 3)
        sides = np.sort(np.stack([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]], axis=1), axis=2)
        edges, inverse = np.unique(sides.reshape(-1, 2), axis=0, return_inverse=True)
        skin = fk.Skin(positions, influences, joints)
        return cls(
            skin,
            tri,
            np.array(group, dtype=np.intp),
            edges,
            inverse.reshape(-1, 3),
            skin.dominant(),
        )

    @cached_property
    def edge_group(self) -> NDArray[np.intp]:
        """Per edge, the group of a triangle it bounds."""
        out = np.zeros(len(self.edges), dtype=np.intp)
        out[self.edge_of.ravel()] = np.repeat(self.group, 3)
        return out

    def riding(self, joints: int) -> NDArray[np.bool_]:
        """Per vertex: whether some of its weight rides a joint at or past `joints`."""
        rides = (self.skin.weights > 0) & (self.skin.joints >= joints)
        return np.asarray(rides.any(axis=1), dtype=np.bool_)

    def growth(self, deform: NDArray[np.float64]) -> NDArray[np.float64]:
        """`(..., edges)`: how much each edge grew, in units, under `(..., joints, 4, 4)`."""
        rest = self.skin.positions
        posed = self.skin.apply(deform)
        a, b = self.edges.T
        before = np.linalg.norm(rest[a] - rest[b], axis=-1)
        grown: NDArray[np.float64] = (
            np.linalg.norm(posed[..., a, :] - posed[..., b, :], axis=-1) - before
        )
        return grown


@dataclass(frozen=True)
class Stretch:
    """One edge at one frame of one clip."""

    growth: float
    slot: int
    frame: int
    group: int
    joints: tuple[int, int]
    """The dominant joints of its two ends, lower first."""

    def __str__(self) -> str:
        a, b = self.joints
        return (
            f"{self.growth:.0f} units (slot {self.slot} frame {self.frame}, group {self.group}, "
            f"joints {a}-{b})"
        )


def samples(clip: Clip) -> list[int]:
    """The frames a clip is sampled at; none for a clip shorter than `MIN_FRAMES`."""
    last = frames(clip)
    return [int(last * f) for f in FRACTIONS] if last >= MIN_FRAMES else []


def worst(port: Port) -> list[Stretch]:
    """The most grown edge at each sample of each distinct clip, worst first."""
    out = []
    s = port.surface
    for slot, clip in port.distinct():
        at = samples(clip)
        if not at:
            continue
        grown = s.growth(fk.deform_matrices(port.rig, clip, at))
        for i, frame in enumerate(at):
            e = int(grown[i].argmax())
            out.append(_stretch(s, grown[i, e], slot, frame, e))
    return sorted(out, key=lambda x: -x.growth)


def faces(port: Port, slot: int, frame: int) -> list[tuple[Stretch, float]]:
    """Each triangle's most grown edge at one frame, with its length ratio, worst first; rigid
    triangles are left out."""
    clip = port.clip(slot)
    if clip is None:
        raise ValueError(f"slot {slot} is empty in this port")
    s = port.surface
    grown = s.growth(fk.deform_matrices(port.rig, clip, frame))
    rest = s.skin.positions
    out = []
    for sides in s.edge_of:
        e = int(sides[grown[sides].argmax()])
        if grown[e] <= GROWTH_EPS:
            continue
        a, b = s.edges[e]
        before = float(np.linalg.norm(rest[a] - rest[b]))
        ratio = (before + grown[e]) / before if before > GROWTH_EPS else float("inf")
        out.append((_stretch(s, grown[e], slot, frame, e), ratio))
    return sorted(out, key=lambda x: -x[0].growth)


def branches(parents: Sequence[int], fork: int) -> dict[int, int]:
    """Joint -> the child of `fork` whose subtree holds it; joints at or above the fork are
    missing."""
    kids: dict[int, list[int]] = {}
    for i, p in enumerate(parents):
        if p >= 0:
            kids.setdefault(p, []).append(i)
    out = {}
    for child in kids.get(fork, []):
        stack = [child]
        while stack:
            j = stack.pop()
            out[j] = child
            stack += kids.get(j, [])
    return out


def tear(port: Port) -> Stretch | None:
    """The most grown edge whose ends ride different branches of the body fork; None when no
    edge crosses it."""
    s = port.surface
    label = branches(port.parents, records.body_fork(port.parents))
    side = np.array([label.get(int(j), -1) for j in s.dominant], dtype=np.intp)
    a, b = s.edges.T
    cross = np.flatnonzero((side[a] >= 0) & (side[b] >= 0) & (side[a] != side[b]))
    if not len(cross):
        return None
    best: Stretch | None = None
    for slot, clip in port.distinct():
        at = samples(clip)
        if not at:
            continue
        grown = s.growth(fk.deform_matrices(port.rig, clip, at))[:, cross]
        i, k = np.unravel_index(int(grown.argmax()), grown.shape)
        if best is None or grown[i, k] > best.growth:
            best = _stretch(s, grown[i, k], slot, at[i], int(cross[k]))
    return best


def _stretch(s: Surface, growth: float, slot: int, frame: int, edge: int) -> Stretch:
    a, b = sorted(int(s.dominant[v]) for v in s.edges[edge])
    return Stretch(float(growth), slot, frame, int(s.edge_group[edge]), (a, b))


# the joint correspondence


@dataclass(frozen=True)
class Correspondence:
    """Donor bone -> port joint, rebuilt from the two skeletons alone."""

    joint_of: dict[int, int]
    pad: int
    """Port joints above the image of the donor's root."""
    by_position: frozenset[int]
    """Bones outside the donor's root tree (the tail tip's chain): they survive, but their
    motion is not the donor's to compare."""

    @property
    def tree(self) -> dict[int, int]:
        """`joint_of` less the bones placed by position."""
        return {b: j for b, j in self.joint_of.items() if b not in self.by_position}


def correspondence(donor: Skeleton, port: Skeleton) -> Correspondence:
    """The donor's tree matched down the port's from the root, children paired by bind offset;
    then each other donor root's tree from the port's root at its bind position, and any bone
    left over by bind position.

    The builder may put origin joints above the donor's root, so each joint down the port's
    lead chain is tried as the root's image and the best kept."""
    dk, pk = _children(donor), _children(port)
    d_off = np.array([b.position for b in donor.bones], dtype=np.float64).reshape(-1, 3)
    p_off = np.array([b.position for b in port.bones], dtype=np.float64).reshape(-1, 3)

    def walk(start: int, root: int = 0) -> dict[int, int]:
        out: dict[int, int] = {}
        todo = [(root, start)]
        while todo:
            d, p = todo.pop()
            out[d] = p
            used: set[int] = set()
            for c in dk.get(d, []):
                free = [j for j in pk.get(p, []) if j not in used]
                if not free:
                    continue
                gap = [float(np.abs(d_off[c] - p_off[j]).max()) for j in free]
                j = free[int(np.argmin(gap))]
                if min(gap) < OFFSET_TOL:
                    used.add(j)
                    todo.append((c, j))
        return out

    best: dict[int, int] = {}
    pad = 0
    node = 0
    for depth in range(MAX_PAD if donor.bones and port.bones else 0):
        found = walk(node)
        if len(found) > len(best):
            best, pad = found, depth
        kids = pk.get(node, [])
        if len(kids) != 1:
            break
        node = kids[0]
    d_world = fk.Rig.from_skeleton(donor).bind_joints
    p_world = fk.Rig.from_skeleton(port).bind_joints
    taken = set(best.values())
    tree = set(best)
    for r in dk.get(-1, []):
        free = [j for j in pk.get(-1, []) if j and j not in taken]  # joint 0 holds the pad
        gap = [float(np.linalg.norm(p_world[j] - d_world[r])) for j in free]
        if r not in best and gap and min(gap) < OFFSET_TOL:
            found = walk(free[int(np.argmin(gap))], r)
            best |= found
            taken |= set(found.values())
    for d in range(len(donor.bones)):
        if d in best:
            continue
        gap = np.linalg.norm(p_world - d_world[d], axis=1)
        gap[list(taken)] = np.inf
        j = int(gap.argmin()) if len(gap) else -1
        if j >= 0 and gap[j] < OFFSET_TOL:
            best[d] = j
            taken.add(j)
    return Correspondence(dict(sorted(best.items())), pad, frozenset(set(best) - tree))


def _children(skeleton: Skeleton) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for i, b in enumerate(skeleton.bones):
        out.setdefault(b.parent, []).append(i)
    return out


# the audit


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


def audit(
    port: Port,
    donor: Skeleton | None = None,
    parts: Sequence[Part] = (),
    host: Port | None = None,
) -> list[Check]:
    """The engine's rules (`constraints`), then the port's own checks; `donor` adds whether its
    rig survived, `parts` (its groups as the port was built from them) whether every vertex
    kept its weights, and `host` (the species' own PAC) whether its dropped tail finds a tip. A
    warning passes and says why."""
    results = constraints.validate(port.model, port.skeleton, port.anim)
    out = []
    for code, (level, holds, _) in constraints.RULES.items():
        if code != "TEMPLATE":
            hits = [r.message for r in results if r.code == code]
            out.append(Check(holds, level == "warn" or not hits, "; ".join(hits)))

    parents, n = port.parents, len(port.parents)
    covered = sum(c for _, c in constraints.runs(port.skeleton))
    out.append(
        Check(
            "animation partition vs FK walk (report)",
            True,
            f"partition {covered}, walk {n}"
            + (f": joints {covered}..{n - 1} get no bone section" if covered < n else ""),
        )
    )
    loc = _loc_joints(port)
    below = records.loc_below_fork(parents, loc)
    out.append(
        Check(
            "location channels vs the body fork (report)",
            True,
            f"fork {records.body_fork(parents)}, location on {_few(sorted(loc))}"
            + (f"; below it: {_few(below)}" if below else ""),
        )
    )
    s = port.surface
    riding = s.riding(port.animated)
    still = sorted({int(j) for j in s.skin.joints[riding].ravel() if j >= port.animated})
    out.append(
        Check(
            "little geometry on a joint the animation does not reach",
            riding.sum() <= STILL_SHARE * len(riding),
            f"{riding.sum()} of {len(riding)} vertices ride {_few(still)}",
        )
    )
    worst_tear = tear(port)
    out.append(
        Check(
            "clips do not tear the mesh across the body fork",
            worst_tear is None or worst_tear.growth < TEAR_LIMIT,
            "no edge crosses the fork" if worst_tear is None else str(worst_tear),
        )
    )
    if host is not None:
        out.append(tip_check(port, host))
    if donor is not None:
        out += _against(port, donor, parts)
    return out


def tip_check(port: Port, host: Port) -> Check:
    """A host whose model has a tail tip (a chain at `params[1]` and mesh record 1) draws the
    port's mesh record 1 at the cut, and crashes the game without one: the port needs its own
    chain, the map that carries it (`rig.tip_of`) and a mesh record 1 on that chain alone."""
    name = "the host's dropped tail finds the port's tip (mesh record 1 and its map)"
    if len(host.model.meshes) < 2 or _tip(host)[0] is None:
        return Check(name, True, "the host drops no tail")
    tip, why = _tip(port)
    if tip is None:
        return Check(name, False, why)
    if len(port.model.meshes) < 2:
        return Check(name, False, "one mesh record: the dropped tail draws record 1")
    first = len(port.model.meshes[0].groups)
    joints = set(tip.joints)
    stray = [
        g
        for g in range(first, first + len(port.model.meshes[1].groups))
        if {j for vi in port.model.influences(g) for j, w in vi if w > 0} - joints
    ]
    if stray:
        return Check(name, False, f"mesh 1 groups {_few(stray)} ride joints off the tip")
    return Check(
        name, True, f"chain {_few(tip.joints)}, carriers {_few([c for _, c in tip.pairs])}"
    )


def _tip(port: Port) -> tuple[Tip | None, str]:
    """`rig.tip_of` of the port's skeleton, and why there is none."""
    try:
        tip = tip_of(port.skeleton)
    except ValueError as e:
        return None, str(e)
    return tip, "no tip chain at params[1]" if tip is None else ""


def _against(port: Port, donor: Skeleton, parts: Sequence[Part]) -> list[Check]:
    c = correspondence(donor, port.skeleton)
    missing = [b for b in range(len(donor.bones)) if b not in c.joint_of]
    images = set(c.joint_of.values())
    moved = [
        b
        for b, j in c.joint_of.items()
        if c.joint_of.get(donor.bones[b].parent, -1) != port.parents[j]
        and (donor.bones[b].parent >= 0 or port.parents[j] in images)
    ]
    out = [
        Check(
            "every donor bone survives in the port's rig",
            not missing,
            f"missing {_few(missing)}" if missing else f"pad {c.pad}, re-parented {_few(moved)}",
        )
    ]
    if not parts:
        return out
    per = [expected(p.influences, c.joint_of) for p in parts]
    want = [w for i in _built_order(port, parts, per) for w in per[i]]
    got = [vi for g in range(len(port.model.groups())) for vi in port.model.influences(g)]
    f: Fidelity = compare(want, got)
    out.append(
        Check("vertex count kept", f.vertices == f.expected, f"{f.vertices}, donor {f.expected}")
    )
    if f.vertices == f.expected:
        out.append(
            Check("every vertex keeps the donor's joints", not f.wrong_set, f"{f.wrong_set} differ")
        )
        out.append(Check("weights within one u8 step", f.within_step, f"worst {f.max_error:.6f}"))
    return out


def _built_order(port: Port, parts: Sequence[Part], per: Sequence[list[Weights]]) -> list[int]:
    """The parts in the port's group order: the body's, then the tip's (`mesh.build`)."""
    tip = _tip(port)[0]
    if tip is None:
        return list(range(len(parts)))
    skinned = [Skinned(p, [list(w.items()) for w in ws]) for p, ws in zip(parts, per, strict=True)]
    try:
        last = mesh.tip_parts(skinned, tip.joints)
    except ValueError:
        return list(range(len(parts)))
    return [i for i in range(len(parts)) if i not in last] + last


def _loc_joints(port: Port) -> set[int]:
    out: set[int] = set()
    for _, clip in port.distinct():
        for j, track in enumerate(clip.tracks):
            for c in track.channels:
                kind = channel_kind(c.bit)
                if kind is not None and kind[0] == "loc" and c.keyframes:
                    out.add(j)
    return out


def _few(items: Sequence[int], limit: int = 8) -> str:
    shown = " ".join(map(str, items[:limit]))
    return shown + (f" (+{len(items) - limit})" if len(items) > limit else "") if items else "none"
