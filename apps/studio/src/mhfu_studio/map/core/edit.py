# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Selecting and editing a section: no GL, no UI.

The op list is the truth and the scene is derived from it: every commit appends an op and
rebuilds the half it touches from the file's bytes through the stage writers, then re-decodes
it into the scene. Undo pops and rebuilds. The gizmo's preview is the one positions-only fast
path, and its commit replaces the preview with the rebuilt truth (s16-quantised, as the game
loads it). Every op names its vertices, primitives or triangles explicitly, so a replay does
not depend on the welding that produced the selection.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from mhfu_studio.shell.findings import Finding
from mhfu_studio.stage import collision, mesh, textures
from mhfu_studio.stage import ops as O

from .scene import CLASS_NAMES, CLIMB_MATERIALS, Array, Key, MapScene, Point, group_name

GROUP, OBJECT, FACE, COLLISION = "group", "object", "face", "collision"
KINDS = (GROUP, OBJECT, FACE, COLLISION)
Op = dict[str, Any]
Pair = tuple[int, int]
"""(chunk, triangle)."""
SHORT_WALL = 100.0
"""A climbable triangle shorter than this is worth a warning."""
KEEPS = ("did-not-fit",)
#: a collision chunk in words: 1 holds the floor, 0 the walls and ceilings
CHUNK_WORDS = {0: "wall", 1: "floor"}
"""Error codes a commit keeps (a partial pack still draws), shown as warnings."""


def compose(
    *,
    pivot: Point = (0.0, 0.0, 0.0),
    by: Point = (0.0, 0.0, 0.0),
    rotate: Point = (0.0, 0.0, 0.0),
    scale: Point = (1.0, 1.0, 1.0),
) -> Array:
    """T(by) T(pivot) R S T(-pivot): the one composition the editor and the writers share."""
    return mesh.transform_matrix(
        {
            "pivot": _floats(pivot),
            "by": _floats(by),
            "rotate": _floats(rotate),
            "scale": _floats(scale),
        }
    )


def decompose(m: Array, pivot: Point) -> dict[str, tuple[float, float, float]]:
    """`by`, `rotate` (degrees) and `scale` of a `compose` matrix about `pivot`; approximate
    under shear, exact for anything the gizmo makes."""
    p = np.asarray(pivot, np.float64)
    a = np.asarray(m, np.float64)[:3, :3]
    sx, sy, sz = (float(np.linalg.norm(a[:, i])) for i in range(3))
    r = a.copy()
    for i, s in enumerate((sx, sy, sz)):
        if s > 1e-12:
            r[:, i] /= s
    if np.linalg.det(r) < 0:
        sx, r[:, 0] = -sx, -r[:, 0]
    ry = math.asin(max(-1.0, min(1.0, -r[2, 0])))
    if abs(math.cos(ry)) > 1e-9:
        rx, rz = math.atan2(r[2, 1], r[2, 2]), math.atan2(r[1, 0], r[0, 0])
    else:
        rx, rz = math.atan2(-r[1, 2], r[1, 1]), 0.0
    by = np.asarray(m, np.float64)[:3, 3] - (p - a @ p)
    return {
        "by": (float(by[0]), float(by[1]), float(by[2])),
        "rotate": (math.degrees(rx), math.degrees(ry), math.degrees(rz)),
        "scale": (sx, sy, sz),
    }


def _floats(v: Point) -> list[float]:
    return [float(x) for x in v]


def apply(m: Array, points: Array) -> Array:
    p = np.asarray(points, np.float64).reshape(-1, 3)
    out: Array = (p @ m[:3, :3].T + m[:3, 3]).astype(np.float32)
    return out


@dataclass
class Selection:
    """Vertex ids per group, and the parts (components or faces) that chose them; `kind`
    says what a click adds, `vertices` what a transform moves."""

    kind: str = OBJECT
    vertices: dict[Key, Array] = field(default_factory=dict)
    parts: dict[Key, list[int]] = field(default_factory=dict)

    @classmethod
    def group(cls, scene: MapScene, key: Key) -> Selection:
        g = scene.group(*key)
        return cls(GROUP, {key: np.arange(g.n_vertices, dtype=np.int64)}, {key: [0]})

    @classmethod
    def object(cls, scene: MapScene, key: Key, component: int) -> Selection:
        g = scene.group(*key)
        ids = g.component_vertices(component).astype(np.int64)
        return cls(OBJECT, {key: ids}, {key: [int(component)]})

    @classmethod
    def face(cls, scene: MapScene, key: Key, face: int) -> Selection:
        g = scene.group(*key)
        return cls(FACE, {key: np.unique(g.triangles[face]).astype(np.int64)}, {key: [int(face)]})

    @classmethod
    def from_pick(cls, scene: MapScene, kind: str, key: Key, face: int) -> Selection:
        if kind == GROUP:
            return cls.group(scene, key)
        if kind == FACE:
            return cls.face(scene, key, face)
        g = scene.group(*key)
        return cls.object(scene, key, int(g.components[g.triangles[face][0]]))

    def _copy(self) -> Selection:
        return Selection(
            self.kind, dict(self.vertices), {k: list(v) for k, v in self.parts.items()}
        )

    def add(self, other: Selection) -> Selection:
        out = self._copy()
        for k, ids in other.vertices.items():
            out.vertices[k] = np.union1d(out.vertices.get(k, np.zeros(0, np.int64)), ids)
            have = out.parts.setdefault(k, [])
            have += [p for p in other.parts.get(k, []) if p not in have]
        return out

    def toggle(self, scene: MapScene, other: Selection) -> Selection:
        """Shift-click: removes the part if it is in, else adds it."""
        k = next(iter(other.vertices))
        part = other.parts.get(k, [None])[0]
        if part is not None and part in self.parts.get(k, []):
            return self.remove(scene, k, part)
        return self.add(other)

    def remove(self, scene: MapScene, key: Key, part: int) -> Selection:
        out = self._copy()
        if part not in out.parts.get(key, []):
            return out
        out.parts[key].remove(part)
        if self.kind == GROUP:
            del out.vertices[key], out.parts[key]
            return out
        g = scene.group(*key)

        def ids_of(p: int) -> Array:
            if self.kind == OBJECT:
                return g.component_vertices(p)
            return np.unique(g.triangles[p])

        keep = np.zeros(g.n_vertices, bool)
        for p in out.parts[key]:
            keep[ids_of(p)] = True  # a vertex a kept part shares stays
        ids = out.vertices[key]
        ids = ids[~np.isin(ids, ids_of(part)) | keep[ids]]
        if len(ids):
            out.vertices[key] = ids
        else:
            del out.vertices[key], out.parts[key]
        return out

    @property
    def empty(self) -> bool:
        return not any(len(v) for v in self.vertices.values())

    def same(self, other: Selection) -> bool:
        """The same vertices, whatever parts chose them."""
        a, b = self.vertices, other.vertices
        return a.keys() == b.keys() and all(np.array_equal(a[k], b[k]) for k in a)

    @property
    def n_vertices(self) -> int:
        return int(sum(len(v) for v in self.vertices.values()))

    def faces(self, scene: MapScene) -> dict[Key, Array]:
        """Per group, the faces whose corners are all selected."""
        out = {}
        for k, ids in self.vertices.items():
            g = scene.group(*k)
            out[k] = np.nonzero(np.isin(g.triangles, ids).all(1))[0]
        return out

    def n_faces(self, scene: MapScene) -> int:
        return int(sum(len(f) for f in self.faces(scene).values()))

    def straddling(self, scene: MapScene) -> int:
        """Faces with some corners selected: moving those stretches them."""
        n = 0
        for k, ids in self.vertices.items():
            m = np.isin(scene.group(*k).triangles, ids)
            n += int((m.any(1) & ~m.all(1)).sum())
        return n

    def positions(self, scene: MapScene) -> Array:
        parts = [scene.group(*k).positions[ids] for k, ids in self.vertices.items() if len(ids)]
        return np.concatenate(parts) if parts else np.zeros((0, 3), np.float32)

    def bounds(self, scene: MapScene) -> tuple[Array, Array]:
        p = self.positions(scene)
        if not len(p):
            z = np.zeros(3, np.float32)
            return z, z
        return p.min(0), p.max(0)

    def centroid(self, scene: MapScene) -> Array:
        lo, hi = self.bounds(scene)
        out: Array = ((lo + hi) * 0.5).astype(np.float64)
        return out

    def refresh(self, scene: MapScene) -> Selection:
        """After a rebuild the groups are new and component ids may be renumbered: keep the
        vertex ids, re-derive the parts, drop vertices no face draws any more."""
        out = Selection(self.kind)
        for k, ids in self.vertices.items():
            try:
                g = scene.group(*k)
            except KeyError:
                continue
            if self.kind == GROUP:
                out.vertices[k], out.parts[k] = np.arange(g.n_vertices, dtype=np.int64), [0]
                continue
            ids = ids[(ids < g.n_vertices) & np.isin(ids, np.unique(g.triangles))]
            if not len(ids):
                continue
            out.vertices[k] = ids
            if self.kind == OBJECT:
                out.parts[k] = sorted(int(c) for c in np.unique(g.components[ids]))
            else:
                out.parts[k] = [int(f) for f in np.nonzero(np.isin(g.triangles, ids).all(1))[0]]
        return out

    def describe(self) -> str:
        """In words: "2 objects in group 8", "group 8", "3 triangles in 2 groups"."""
        if self.empty:
            return "nothing selected"
        ks = list(self.vertices)
        where = group_name(ks[0]) if len(ks) == 1 else count(len(ks), "group")
        if self.kind == GROUP:
            return where
        n = sum(len(self.parts.get(k, [])) for k in ks)
        return f"{count(n, 'object' if self.kind == OBJECT else 'triangle')} in {where}"


@dataclass
class CollisionSelection:
    """Collision triangles as (chunk, tri) pairs."""

    tris: list[Pair] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.tris

    def __len__(self) -> int:
        return len(self.tris)

    def toggle(self, ct: Pair) -> CollisionSelection:
        out = list(self.tris)
        if ct in out:
            out.remove(ct)
        else:
            out.append(ct)
        return CollisionSelection(out)

    def by_chunk(self) -> dict[int, list[int]]:
        return by_chunk(self.tris)

    def positions(self, scene: MapScene) -> Array:
        rows = [scene.chunk(c).verts[t] for c, t in self.tris]
        return np.concatenate(rows).reshape(-1, 3) if rows else np.zeros((0, 3), np.float32)

    def bounds(self, scene: MapScene) -> tuple[Array, Array]:
        p = self.positions(scene)
        if not len(p):
            z = np.zeros(3, np.float32)
            return z, z
        return p.min(0), p.max(0)

    def centroid(self, scene: MapScene) -> Array:
        lo, hi = self.bounds(scene)
        out: Array = ((lo + hi) * 0.5).astype(np.float64)
        return out

    def describe(self, scene: MapScene) -> str:
        """In words, with the kinds: "3 collision triangles: 2 wall, 1 climb"."""
        if self.empty:
            return "no collision triangle selected"
        kinds: dict[str, int] = {}
        for c, t in self.tris:
            k = CLASS_NAMES[str(scene.chunk(c).klass[t])]
            kinds[k] = kinds.get(k, 0) + 1
        what = ", ".join(f"{n} {k}" for k, n in kinds.items())
        return f"{count(len(self), 'collision triangle')}: {what}"


def count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def triangle_name(chunk: int, tri: int) -> str:
    """`wall triangle 12`: a collision triangle as a person points at it."""
    return f"{CHUNK_WORDS.get(chunk, f'chunk {chunk}')} triangle {tri}"


def by_chunk(tris: Iterable[Pair]) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for c, t in tris:
        out.setdefault(int(c), []).append(int(t))
    return out


class EditError(RuntimeError):
    """An op the writers refused; the session is back where it was."""


def transform_op(
    key: Key,
    vertices: Iterable[int],
    matrix: Array,
    pivot: Point,
    kind: str = OBJECT,
    parts: Sequence[int] = (),
) -> Op:
    """One group's vertices through one matrix: stored whole, and decomposed for a reader."""
    d = decompose(np.asarray(matrix, np.float64), pivot)
    return {
        "op": "transform",
        "sub": int(key[0]),
        "group": int(key[1]),
        "vertices": [int(i) for i in vertices],
        "pivot": [round(float(v), 3) for v in pivot],
        "by": [round(v, 3) for v in d["by"]],
        "rotate": [round(v, 4) for v in d["rotate"]],
        "scale": [round(v, 5) for v in d["scale"]],
        "matrix": [[round(float(v), 8) for v in row] for row in np.asarray(matrix)],
        "selection": {"kind": kind, "parts": [int(p) for p in parts]},
    }


def op_key(op: Mapping[str, Any]) -> Key | None:
    g = op.get("group", 0)
    return None if g is None else (int(op.get("sub", 0)), int(g))


def describe_op(op: Mapping[str, Any]) -> str:
    """One line per op, in plain words: "group 8: moved by 0, 100, 0"."""
    k = str(op.get("op"))
    key = op_key(op)
    where = group_name(key) if key else "collision"
    if k in ("transform", "move"):
        by, rot = op.get("by", (0, 0, 0)), op.get("rotate", (0, 0, 0))
        sc = op.get("scale", (1, 1, 1))
        bits = [f"moved by {_xyz(by)}"] if any(round(v) for v in by) else []
        if any(round(v) for v in rot):
            bits.append(f"turned {_xyz(rot)}\N{DEGREE SIGN}")
        if any(round(v, 2) != 1 for v in sc):
            bits.append(f"scaled \N{MULTIPLICATION SIGN}{_xyz(sc, 2)}")
        return f"{where}: {'; '.join(bits) or 'no change'}"
    if k == "pack":
        name = Path(str(op.get("obj", "?"))).name
        return f"added {name} into {where}" + (", solid" if op.get("solid") else "")
    if k == "clear":
        n = len(op.get("prims") or [])
        return f"removed from {where} ({count(n, 'drawing slot')})" if n else f"removed {where}"
    if k == "material":
        bits = [f"texture slot {op['texture']}"] if "texture" in op else []
        if "rgba" in op:
            bits.append(f"colour {_xyz(op['rgba'])}")
        return f"{where}: {', '.join(bits) or 'material'}"
    if k == "texture":
        if "png" in op:
            src = Path(str(op["png"])).name
        elif "from" in op:
            src = f"slot {op['from']['slot']} of st{op['from']['stage']:03d}"
        else:
            src = f"flat colour {_xyz(op.get('rgb', ()))}"
        return f"texture slot {op.get('slot')}: {src}"
    if k == "collision":
        if "solid_box" in op:
            return "added a box collider"
        if op.get("add"):
            return f"added {count(len(op['add']), 'collision triangle')}{_flags_text(op)}"
        n = ("tri" in op) + len(op.get("tris", []))
        tris = count(n, f"{CHUNK_WORDS.get(op.get('chunk', 1), 'collision')} triangle")
        if op.get("delete"):
            return f"deleted {tris}"
        if "verts" in op:
            return f"moved {tris}"
        return f"changed {tris}{_flags_text(op)}"
    return f"{k} {where}"


def _xyz(v: Sequence[float], digits: int = 0) -> str:
    return ", ".join(f"{round(float(x), digits):g}" for x in v)


def _flags_text(op: Mapping[str, Any]) -> str:
    """The flags an op sets, climbing named."""
    fl = op.get("flags") or {}
    if not isinstance(fl, Mapping) or not fl:
        return ""
    if set(fl) == {"material"}:
        if fl["material"] in CLIMB_MATERIALS:
            return ": climbable"
        if fl["material"] == 0:
            return ": not climbable"
    return ": " + ", ".join(f"{k} {v}" for k, v in fl.items())


class EditSession:
    """A section's ops with undo and redo, a live preview, and the writers.

    `ops` is the document's own list for the stage (edited in place); the ops it holds when the
    session starts are replayed once and are the baseline undo stops at.
    """

    def __init__(
        self, scene: MapScene, ops: list[Op] | None = None, base_dir: Path | None = None
    ) -> None:
        self.scene = scene
        #: where an op's OBJ or PNG resolves (the document's folder); None before a save
        self.base_dir = base_dir
        self.ops: list[Op] = ops if ops is not None else []
        self._steps: list[int] = []
        self._undone: list[list[Op]] = []
        self._base: dict[Key, tuple[Array, Array]] = {}
        self._sel: Selection | None = None
        self._cbase: dict[Pair, Array] = {}
        #: groups whose positions changed since the renderer looked
        self.dirty: set[Key] = set()
        #: subs re-decoded since the renderer looked
        self.rebuilt: set[int] = set()
        self.textures_changed = False
        self.collision_changed = False
        self.collision_preview = False
        #: what the writers said in the last rebuild
        self.findings: list[Finding] = []
        self.log: list[str] = []
        #: bumped by every change of `ops`
        self.revision = 0
        self._ever_subs: set[int] = set()
        self._ever_collision = False
        self._ever_textures = False
        if self.ops:
            self._rebuild(self.ops)

    # the preview: positions only

    def begin(self, sel: Selection) -> None:
        """Snapshots the selection's positions; `preview` is relative to them."""
        self._sel = sel
        self._base = {}
        for k, ids in sel.vertices.items():
            self._base[k] = (ids.copy(), self.scene.group(*k).positions[ids].copy())

    def begin_collision(self, sel: CollisionSelection) -> None:
        self._cbase = {ct: self.scene.chunk(ct[0]).verts[ct[1]].copy() for ct in sel.tris}

    @property
    def previewing(self) -> bool:
        return bool(self._base) or bool(self._cbase)

    def preview(self, matrix: Array) -> None:
        for k, (ids, before) in self._base.items():
            self.scene.group(*k).positions[ids] = apply(matrix, before)
            self.dirty.add(k)
        for (c, t), before in self._cbase.items():
            self.scene.chunk(c).verts[t] = apply(matrix, before).reshape(3, 3)
            self.collision_preview = True

    def _restore(self) -> None:
        for k, (ids, before) in self._base.items():
            self.scene.group(*k).positions[ids] = before
            self.dirty.add(k)
        for (c, t), before in self._cbase.items():
            self.scene.chunk(c).verts[t] = before
            self.collision_preview = True
        self._base, self._sel, self._cbase = {}, None, {}

    def cancel(self) -> None:
        self._restore()

    def commit(self, matrix: Array, pivot: Point | None = None) -> list[Op]:
        """Ends the preview as ops (one per group); an identity commits nothing."""
        if self._cbase:
            return self.commit_collision(matrix)
        if not self._base or self._sel is None:
            return []
        sel = self._sel
        at = sel.centroid(self.scene) if pivot is None else np.asarray(pivot, np.float64)
        ops = []
        if not np.allclose(matrix, np.eye(4), atol=1e-9):
            for k, (ids, _) in self._base.items():
                ops.append(transform_op(k, ids, matrix, at, sel.kind, sel.parts.get(k, [])))
        self._restore()  # the rebuild puts the truth where the preview was
        return self.push(ops)

    def apply_now(self, sel: Selection, matrix: Array, pivot: Point | None = None) -> list[Op]:
        """Numeric entry: begin and commit in one call."""
        self.begin(sel)
        return self.commit(matrix, pivot)

    def commit_collision(self, matrix: Array) -> list[Op]:
        ops = []
        if not np.allclose(matrix, np.eye(4), atol=1e-9):
            for (c, t), before in self._cbase.items():
                vs = apply(matrix, before).reshape(3, 3)
                ops.append(
                    {
                        "op": "collision",
                        "group": None,
                        "chunk": int(c),
                        "tri": int(t),
                        "verts": [[round(float(x), 2) for x in v] for v in vs],
                    }
                )
        self._restore()
        self.collision_changed = True
        return self.push(ops)

    # the op list

    def push(self, ops: Sequence[Mapping[str, Any]]) -> list[Op]:
        """Appends one undo step and rebuilds what it touches; a step a writer refuses is
        dropped, the scene rebuilt without it, and `EditError` raised."""
        new = [dict(o) for o in ops]
        if not new:
            return []
        bad = [f for f in O.check(new, base_dir=self.base_dir) if f.level == "error"]
        if bad:
            raise EditError(bad[0].message)
        before = {f for f in self.findings if _refuses(f)}
        self.ops.extend(new)
        self._rebuild(new)
        refused = [f for f in self.findings if _refuses(f) and f not in before]
        if refused:
            del self.ops[-len(new) :]
            self._rebuild(new)
            raise EditError(refused[0].message)
        self._steps.append(len(new))
        self._undone.clear()
        return new

    def undo(self) -> list[Op] | None:
        if not self._steps:
            return None
        n = self._steps.pop()
        ops = self.ops[-n:]
        del self.ops[-n:]
        self._undone.append(ops)
        self._rebuild(ops)
        return ops

    def redo(self) -> list[Op] | None:
        if not self._undone:
            return None
        ops = self._undone.pop()
        self.ops.extend(ops)
        self._steps.append(len(ops))
        self._rebuild(ops)
        return ops

    def can_undo(self) -> bool:
        return bool(self._steps)

    def can_redo(self) -> bool:
        return bool(self._undone)

    @property
    def n_steps(self) -> int:
        return len(self._steps)

    def _rebuild(self, changed: Sequence[Op]) -> None:
        """Re-derives every half any op ever touched: an undo must put the file's bytes back
        even when no op left in the list names that half."""
        self.revision += 1
        self._ever_subs |= {s for o in changed if (s := O.sub_of(o)) is not None}
        self._ever_collision |= any(O.touches_collision(o) for o in changed)
        self._ever_textures |= any(O.touches_textures(o) for o in changed)
        sc, base = self.scene, self.base_dir or Path(".")
        found: list[Finding] = []
        self.log = []
        for sub in sorted(self._ever_subs):
            e = mesh.edit(sc.file, sub, self.ops, base)
            found += e.findings
            self.log += e.log
            sc.replace_sub(sub, e.data)
            self.rebuilt.add(sub)
            self.dirty |= {g.key for g in sc.groups if g.sub == sub}
        if self._ever_collision:
            plan = None
            if any(O.touches_collision(o) for o in self.ops):
                plan = collision.plan(sc.file, self.ops, base)
                found += plan.findings
                self.log += plan.log
            sc.replace_collision(plan)
            self.collision_changed = True
        if self._ever_textures:
            bank = textures.build(sc.file, self.ops, base)
            found += bank.findings
            self.log += bank.log
            sc.replace_textures(bank.data)
            self.textures_changed = True
        self.findings = found

    # geometry: add, remove

    def candidate_prims(self, key: Key, sacrifice: Selection | None = None) -> Array:
        """Where a shape can go: the group's free primitives and the sacrificed objects'."""
        g = self.scene.group(*key)
        prims = {int(p) for p in g.free_prims()}
        if sacrifice is not None and key in sacrifice.vertices:
            prims |= {int(p) for p in g.prims_of_vertices(sacrifice.vertices[key])}
        return np.array(sorted(prims), np.int64)

    def capacity(self, key: Key, sacrifice: Selection | None = None) -> int:
        return self.scene.group(*key).prim_capacity(self.candidate_prims(key, sacrifice))

    def add_mesh(
        self,
        key: Key,
        obj: str,
        n_triangles: int,
        *,
        sacrifice: Selection | None = None,
        uv: str = "planar",
        colour: Sequence[int] | None = None,
        solid: bool | str | None = None,
    ) -> Selection:
        """`pack` an OBJ (relative to `base_dir`) into the group's free primitives and the
        sacrificed objects'; refuses over budget. Returns the new object as a selection."""
        if self.base_dir is None:
            raise EditError("no document folder to hold the asset: save the document first")
        g = self.scene.group(*key)
        prims = self.candidate_prims(key, sacrifice)
        cap = g.prim_capacity(prims)
        if not len(prims):
            raise EditError(f"{g.label} has no free primitive: select an object in it to replace")
        if n_triangles > cap:
            raise EditError(
                f"{Path(obj).name} needs {n_triangles} triangles; {g.label} can draw {cap} in"
                " those primitives: select more objects to replace, or a simpler shape"
            )
        op: Op = {
            "op": "pack",
            "sub": int(key[0]),
            "group": int(key[1]),
            "obj": str(obj),
            "prims": [int(p) for p in prims],
            "uv": uv,
            "collapse": True,
            "reindex": True,
        }
        if colour is not None:
            op["colour"] = [int(c) for c in colour]
        if solid:
            op["solid"] = solid
        self.push([op])
        g = self.scene.group(*key)
        found = g.prim_faces(prims)
        ids = np.unique(g.triangles[found]) if len(found) else np.zeros(0, np.int64)
        parts = sorted(int(c) for c in np.unique(g.components[ids])) if len(ids) else []
        return Selection(OBJECT, {key: ids.astype(np.int64)}, {key: parts})

    def remove(self, sel: Selection, *, solid: bool = False) -> list[Op]:
        """`clear` the selected objects' whole primitives; with `solid`, the collision inside
        their box goes too."""
        ops = []
        for k, ids in sel.vertices.items():
            g = self.scene.group(*k)
            prims = g.prims_of_vertices(ids)
            if not len(prims):
                continue
            op: Op = {"op": "clear", "sub": int(k[0]), "group": int(k[1])}
            op["prims"] = [int(p) for p in prims]
            if solid:
                lo, hi = g.positions[ids].min(0), g.positions[ids].max(0)
                op["solid"] = True
                op["box"] = [round(float(v), 1) for v in (*(lo - 1.0), *(hi + 1.0))]
            ops.append(op)
        if not ops:
            raise EditError(
                "the selection holds no whole primitive to clear (select objects, not faces)"
            )
        return self.push(ops)

    # materials and textures

    def set_material(
        self,
        key: Key,
        *,
        texture: int | None = None,
        rgba: Sequence[int] | None = None,
        ambient: Sequence[int] | None = None,
    ) -> list[Op]:
        g = self.scene.group(*key)
        if g.material is None:
            raise EditError(f"{g.label}'s material record cannot be resolved")
        op: Op = {"op": "material", "sub": int(key[0]), "group": int(key[1]), "mat": g.material}
        if texture is not None:
            op["texture"] = int(texture)
        if rgba is not None:
            op["rgba"] = [int(c) for c in rgba]
        if ambient is not None:
            op["ambient"] = [int(c) for c in ambient] + [0] * (4 - len(ambient))
        if len(op) == 4:
            raise EditError("nothing to change")
        return self.push([op])

    def material_sharers(self, key: Key) -> list[Key]:
        """Other groups drawing with the same record: a `material` op changes them too."""
        g = self.scene.group(*key)
        return [
            x.key
            for x in self.scene.groups
            if x.sub == g.sub and x.key != key and x.material == g.material
        ]

    def import_texture(
        self,
        slot: int,
        *,
        png: str | None = None,
        from_: tuple[int, int] | None = None,
        rgb: Sequence[int] | None = None,
        keep_palette: bool = False,
    ) -> list[Op]:
        op: Op = {"op": "texture", "slot": int(slot)}
        if png is not None:
            if self.base_dir is None:
                raise EditError("no document folder to hold the image: save the document first")
            op["png"] = str(png)
        elif from_ is not None:
            op["from"] = {"stage": int(from_[0]), "slot": int(from_[1])}
        elif rgb is not None:
            op["rgb"] = [int(c) for c in rgb]
        else:
            raise EditError("a texture op needs a png, a source slot or a colour")
        if keep_palette:
            op["keep_palette"] = True
        return self.push([op])

    # collision

    def collision_set(
        self,
        chunk: int,
        tris: Sequence[int],
        *,
        flags: Mapping[str, int] | None = None,
        verts: Sequence[Sequence[float]] | None = None,
    ) -> list[Op]:
        ts = [int(t) for t in tris]
        if not ts:
            raise EditError("no collision triangle selected")
        if verts is not None and len(ts) != 1:
            raise EditError("vertices can be set on one triangle at a time")
        op: Op = {"op": "collision", "group": None, "chunk": int(chunk)}
        if len(ts) == 1:
            op["tri"] = ts[0]
        else:
            op["tris"] = ts
        if flags:
            op["flags"] = {k: int(v) for k, v in flags.items()}
        if verts is not None:
            op["verts"] = [[round(float(x), 2) for x in v] for v in verts]
        if "flags" not in op and "verts" not in op:
            raise EditError("nothing to change")
        return self.push([op])

    def collision_delete(self, tris: Iterable[Pair]) -> list[Op]:
        ops: list[Op] = [
            {"op": "collision", "group": None, "chunk": c, "tris": ts, "delete": True}
            for c, ts in sorted(by_chunk(tris).items())
        ]
        if not ops:
            raise EditError("no collision triangle selected")
        return self.push(ops)

    def collision_add(
        self,
        triangles: Sequence[Sequence[Sequence[float]]],
        *,
        flags: Mapping[str, int] | None = None,
        chunk: int | None = None,
    ) -> list[Op]:
        """Triangles of three xyz each; `chunk` None sorts each by its normal."""
        tv = [[[round(float(x), 2) for x in v] for v in t] for t in triangles]
        if not tv:
            raise EditError("no triangles to add")
        op: Op = {"op": "collision", "group": None, "chunk": chunk, "add": tv}
        if flags:
            op["flags"] = {k: int(v) for k, v in flags.items()}
        return self.push([op])

    def collision_box(
        self,
        lo: Sequence[float],
        hi: Sequence[float],
        *,
        flags: Mapping[str, int] | None = None,
        chunk: int | None = None,
        inflate: float = 0.0,
    ) -> list[Op]:
        box = [round(float(v), 1) for v in (*lo, *hi)]
        op: Op = {"op": "collision", "group": None, "chunk": chunk, "solid_box": box}
        if flags:
            op["flags"] = {k: int(v) for k, v in flags.items()}
        if inflate:
            op["inflate"] = float(inflate)
        return self.push([op])

    def set_climbable(self, tris: Iterable[Pair], on: bool = True, material: int = 10) -> list[Op]:
        """Material 9 or 10 makes a near-vertical triangle climbable; on a flat one it is just
        bulk material."""
        ops: list[Op] = []
        for c, ts in sorted(by_chunk(tris).items()):
            op: Op = {"op": "collision", "group": None, "chunk": c}
            if len(ts) > 1:
                op["tris"] = ts
            else:
                op["tri"] = ts[0]
            op["flags"] = {"material": int(material) if on else 0}
            ops.append(op)
        if not ops:
            raise EditError("no collision triangle selected")
        return self.push(ops)

    def climb_warnings(self, tris: Iterable[Pair]) -> list[str]:
        """Why a climbable patch would fail: flat, short, mixed materials."""
        out, mats = [], set()
        for c, t in tris:
            ch = self.scene.chunk(c)
            if not ch.vertical(t):
                out.append(f"{triangle_name(c, t)} is too flat to climb: only a steep wall is")
            h = float(ch.verts[t][:, 1].max() - ch.verts[t][:, 1].min())
            if h < SHORT_WALL:
                out.append(f"{triangle_name(c, t)} is only {h:.0f} units tall")
            mats.add(int(ch.material[t]))
        if len(mats) > 1:
            out.append(f"the selected triangles mix materials {sorted(mats)}")
        return out

    # checks

    def range_check(self, key: Key | None = None) -> dict[Key, int]:
        """Per group, vertices past +-scale: positions the PMO cannot store."""
        out = {}
        for g in self.scene.groups:
            if (key is not None and g.key != key) or not g.n_vertices:
                continue
            # a shipped vertex at -32768 lands a hair past -scale
            s = np.asarray(self.scene.pmo_scale.get(g.sub, (1, 1, 1)), np.float32) * 1.0001
            n = int((np.abs(g.positions) > s).any(1).sum())
            if n:
                out[g.key] = n
        return out

    def budget(self) -> dict[str, Any]:
        """Per group: `strip` what its primitives can draw, `drawn` the faces now, `free` what
        fits the primitives that draw nothing. A transform spends none of it."""
        per = {
            g.key: {
                "strip": g.budget.triangles,
                "drawn": g.n_faces,
                "free": g.prim_capacity(g.free_prims()),
                "slots": g.budget.slots,
            }
            for g in self.scene.groups
        }
        total = {k: sum(v[k] for v in per.values()) for k in ("strip", "drawn", "free", "slots")}
        spent = sum(o.get("op") == "pack" for o in self.ops)
        return {"per_group": per, "total": total["strip"], **total, "spent": spent}

    def warnings(self) -> list[str]:
        """The writers' complaints from the last rebuild."""
        return [f"{f.where}: {f.message}" for f in self.findings if f.level != "info"]


def _refuses(f: Finding) -> bool:
    return f.level == "error" and f.code not in KEEPS
