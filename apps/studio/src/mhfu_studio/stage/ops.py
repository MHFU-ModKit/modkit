# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The edit list: every op a stage edit may hold, its keys, and the checks on them.

An op is a JSON object with an `op` kind. Mesh ops edit one vertex group of PMO `sub` (0
terrain, 2 props); a mesh op whose `group` is null edits only the collision. `collision` ops
name collision triangles by chunk and index (past the shipped count: one the list added, in
order), `texture` ops replace an image of the bank. The mesh, collision and texture writers
each act on their half of one list.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mhfu_studio.shell.findings import Finding, Level

if TYPE_CHECKING:
    from .file import StageFile

Op = Mapping[str, Any]

MESH = ("transform", "move", "pack", "sculpt", "replace", "clear", "material")
COLLISION = ("collision",)
TEXTURE = ("texture",)
KINDS = MESH + COLLISION + TEXTURE
RETIRED = {
    "add": "the editor's Add panel writes `pack`: an OBJ packed into a group's primitives",
    "climb": "a climbable wall is a `collision` op with flags.material 9 or 10",
}
"""Kinds older lists name that no writer acts on, and what to write instead."""
SUBS = (0, 2)
UV_MODES = ("keep", "obj", "planar")
FLAG_LIMITS = {"surface": 0xFF, "material": 0xFF, "exclude": 0xFFFF}
SELECTORS = ("vertices", "sphere", "box")
#: the control a finding on an op lands on: the edit in the Selection panel's list
EDIT = "edit"
COLLISION_ONLY = ("move", "clear")
"""Mesh kinds whose `group` may be null: they then move or unlink collision only."""

Check = Callable[[Any], str | None]
_Said = tuple[Level, str, str]


def _num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def _int(v: Any, lo: int = 0, hi: int | None = None) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v and (hi is None or v <= hi)


def _vec(n: int, what: str) -> Check:
    def check(v: Any) -> str | None:
        ok = isinstance(v, list | tuple) and len(v) == n and all(map(_num, v))
        return None if ok else f"is {what}: {n} numbers"

    return check


def _bytes(*counts: int) -> Check:
    def check(v: Any) -> str | None:
        ok = isinstance(v, list | tuple) and len(v) in counts and all(_int(c, 0, 255) for c in v)
        return None if ok else f"is {' or '.join(map(str, counts))} values 0..255"

    return check


def _ints(v: Any) -> str | None:
    ok = isinstance(v, list | tuple) and all(_int(i) for i in v)
    return None if ok else "is a list of indices"


def _bool(v: Any) -> str | None:
    return None if isinstance(v, bool) else "is true or false"


def _text(v: Any) -> str | None:
    return None if isinstance(v, str) and v else "is a file name"


def _count(v: Any) -> str | None:
    return None if _int(v) else "is an index"


def _chunk(v: Any) -> str | None:
    return None if v is None or _int(v) else "is a chunk number, or null to sort by normal"


def _sphere(v: Any) -> str | None:
    if _vec(4, "")(v) is None and v[3] >= 0:
        return None
    return "is [x, y, z, radius]"


def _matrix(v: Any) -> str | None:
    rows = v if isinstance(v, list | tuple) else []
    flat = [x for r in rows for x in r] if rows and isinstance(rows[0], list | tuple) else rows
    return None if len(flat) == 16 and all(map(_num, flat)) else "is 16 numbers"


def _flags(v: Any) -> str | None:
    if _int(v, 0, 0xFFFFFFFF):
        return None
    if not isinstance(v, Mapping) or set(v) - set(FLAG_LIMITS):
        return "is {surface, material, exclude} or the whole word"
    bad = [k for k, hi in FLAG_LIMITS.items() if k in v and not _int(v[k], 0, hi)]
    return f"has {', '.join(bad)} out of range" if bad else None


def _solid(v: Any) -> str | None:
    return None if isinstance(v, bool) or v == "box" else 'is true, false or "box"'


def _uv(v: Any) -> str | None:
    return None if v in UV_MODES else f"is one of {', '.join(UV_MODES)}"


def _triangles(v: Any) -> str | None:
    ok = isinstance(v, list | tuple) and all(
        isinstance(t, list | tuple) and len(t) == 3 and all(_vec(3, "")(p) is None for p in t)
        for t in v
    )
    return None if ok else "is triangles of three [x, y, z]"


def _source(v: Any) -> str | None:
    ok = isinstance(v, Mapping) and _int(v.get("stage")) and _int(v.get("slot"))
    return None if ok else "is {stage, slot}"


def _volume(v: Any) -> str | None:
    if not isinstance(v, Mapping) or set(v) - {"sphere", "box", "chunks"}:
        return "is {sphere, box, chunks}"
    for key, check in (("sphere", _sphere), ("box", _vec(6, "[x0, y0, z0, x1, y1, z1]"))):
        if key in v and (problem := check(v[key])):
            return f"{key} {problem}"
    return _ints(v["chunks"]) if "chunks" in v else None


def _any(v: Any) -> str | None:
    return None


def _nonneg(v: Any) -> str | None:
    return None if _num(v) and v >= 0 else "is a number >= 0"


def _one_triangle(v: Any) -> str | None:
    return None if _triangles([v]) is None else "is three [x, y, z]"


_BOX = _vec(6, "[x0, y0, z0, x1, y1, z1]")
_SOLID_KEYS: dict[str, Check] = {
    "solid": _solid,
    "flags": _flags,
    "chunk": _chunk,
    "inflate": _nonneg,
    "collision": _volume,
    "chunks": _ints,
    "collision_obj": _text,
}
_SELECT: dict[str, Check] = {"vertices": _ints, "sphere": _sphere, "box": _BOX}
KEYS: dict[str, dict[str, Check]] = {
    "transform": {
        **_SELECT,
        **_SOLID_KEYS,
        "pivot": _vec(3, "[x, y, z]"),
        "by": _vec(3, "[x, y, z]"),
        "rotate": _vec(3, "degrees about x, y, z"),
        "scale": _vec(3, "[x, y, z]"),
        "matrix": _matrix,
        "margin": _nonneg,
        "selection": _any,
    },
    "move": {**_SELECT, **_SOLID_KEYS, "by": _vec(3, "[x, y, z]")},
    "pack": {
        **_SOLID_KEYS,
        "obj": _text,
        "prims": _ints,
        "first": _count,
        "count": _count,
        "uv": _uv,
        "colour": _bytes(3, 4),
        "collapse": _bool,
        "reindex": _bool,
    },
    "sculpt": {**_SOLID_KEYS, "obj": _text, "first": _count, "collapse": _bool},
    "replace": {**_SOLID_KEYS, "obj": _text, "uv": _uv},
    "clear": {
        "prims": _ints,
        "solid": _solid,
        "sphere": _sphere,
        "box": _BOX,
        "collision": _volume,
        "chunks": _ints,
    },
    "material": {
        "mat": _count,
        "rgba": _bytes(4),
        "ambient": _bytes(4),
        "texture": lambda v: None if _int(v, 0, 255) else "is a bank slot 0..255",
    },
    "collision": {
        "chunk": _chunk,
        "tri": _count,
        "tris": _ints,
        "verts": _one_triangle,
        "flags": _flags,
        "delete": _bool,
        "add": _triangles,
        "solid_box": _BOX,
        "inflate": _nonneg,
    },
    "texture": {
        "slot": _count,
        "png": _text,
        "from": _source,
        "rgb": _bytes(3),
        "keep_palette": _bool,
    },
}
"""Per kind, every key an op may carry besides `op`, `sub` and `group`."""
ASSETS = ("obj", "collision_obj", "png")
"""Keys naming a file, relative to the edit list's folder."""


def parse(raw: object) -> list[dict[str, Any]]:
    """An edit list from decoded JSON: a list of op objects, or `{"ops": [...]}`."""
    if isinstance(raw, Mapping):
        raw = raw.get("ops", [raw])
    if not isinstance(raw, list) or not all(isinstance(o, dict) for o in raw):
        raise ValueError("an edit list is a JSON array of objects")
    return raw


def load(path: Path) -> list[dict[str, Any]]:
    try:
        return parse(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: {e}") from None


def sub_of(op: Op) -> int | None:
    """The PMO a mesh op edits; None for the other halves' ops and for collision-only ones."""
    if op.get("op") not in MESH or op.get("group") is None:
        return None
    sub = op.get("sub", 0)
    return sub if sub in SUBS else None


def touches_collision(op: Op) -> bool:
    """`collision` ops; `move` and `transform`, whose volume carries the collision inside it
    along; and the mesh ops that give an object a collider (`solid`)."""
    kind = op.get("op")
    return kind in (*COLLISION, "move", "transform") or (kind in MESH and bool(op.get("solid")))


def touches_textures(op: Op) -> bool:
    return op.get("op") in TEXTURE


def edit_number(index: int) -> int:
    """Op `index` as an edit list numbers it: from 1."""
    return index + 1


def place(area: str, index: int) -> str:
    """Where op `index` is: `Pokke village (st139), edit 2`, or `Edit 2` with no `area`."""
    n = edit_number(index)
    return f"{area}, edit {n}" if area else f"Edit {n}"


def check(
    ops: Sequence[Op],
    *,
    base_dir: Path | None = None,
    stage: StageFile | None = None,
    where: str = "",
) -> list[Finding]:
    """Grade an edit list. With `base_dir` the files it names must exist; with `stage` the
    writers run over it and report what they would refuse or could not fit. `where` names
    the area in every finding's place, the writers' too."""
    area = where or (stage.label if stage else "")
    out: list[Finding] = []
    for i, op in enumerate(ops):
        at, target = place(area, i), (stage.number if stage else None, i)
        out += [Finding(lv, code, m, at, target, EDIT) for lv, code, m in _check_op(op, base_dir)]
    if stage is not None and not any(f.level == "error" for f in out):
        out += [_placed(f, area) for f in evidence(stage, ops, base_dir or Path("."))]
    return out


def _placed(f: Finding, area: str) -> Finding:
    """A writer's finding on an op, its place named for `area`, landing on the edit."""
    t = f.target
    if isinstance(t, tuple) and len(t) == 2 and isinstance(t[1], int):
        return replace(f, where=place(area, t[1]), focus=EDIT)
    return f


def evidence(stage: StageFile, ops: Sequence[Op], base_dir: Path) -> list[Finding]:
    """What the writers say about the list on this stage."""
    from .collision import plan
    from .mesh import edits
    from .textures import build

    out: list[Finding] = []
    for edit in edits(stage, ops, base_dir).values():
        out += edit.findings
    if any(touches_collision(o) for o in ops):
        out += plan(stage, ops, base_dir).findings
    if any(touches_textures(o) for o in ops):
        out += build(stage, ops, base_dir).findings
    return out


def _check_op(op: Op, base_dir: Path | None) -> list[_Said]:
    kind = op.get("op")
    if kind in RETIRED:
        return [("error", "retired-op", f"no writer acts on `{kind}`: {RETIRED[kind]}")]
    if kind not in KEYS:
        return [("error", "unknown-op", f"`{kind}` is not an op ({', '.join(KINDS)})")]
    out: list[_Said] = []
    keys = KEYS[kind]
    for key, value in op.items():
        if key == "op":
            continue
        if key in keys:
            problem = keys[key](value)
            if problem:
                out.append(("error", "bad-value", f"{key} {problem}"))
        elif kind == "texture" or key not in ("sub", "group"):
            out.append(("warning", "unknown-key", f"`{key}` means nothing to `{kind}`"))
    if kind in MESH:
        out += _mesh(kind, op)
    elif kind == "collision":
        out += _collision(op)
    else:
        out += _texture(op)
    for key in ASSETS:
        name = op.get(key)
        if base_dir is not None and isinstance(name, str) and name:
            if not (base_dir / name).is_file():
                out.append(("error", "missing-asset", f"{key} names {name}, which is not there"))
    return out


def _mesh(kind: str, op: Op) -> list[_Said]:
    out: list[_Said] = []
    if op.get("sub", 0) not in SUBS:
        out.append(("error", "bad-sub", f"sub is 0 (terrain) or 2 (props), not {op['sub']!r}"))
    if "group" not in op:
        out.append(("error", "no-group", f"{kind} needs a group (null for collision only)"))
    elif op["group"] is None:
        if kind not in COLLISION_ONLY:
            out.append(
                ("error", "no-group", f"{kind} edits a group; only move and clear take null")
            )
    elif not _int(op["group"]):
        out.append(("error", "bad-value", "group is a vertex group index"))
    if kind in ("transform", "move"):
        volume = op.get("collision") or {}
        if not any(k in op or k in volume for k in SELECTORS):
            out.append(("error", "no-selector", f"{kind} needs vertices, sphere or box"))
    if kind == "move" and "by" not in op:
        out.append(("error", "no-offset", "move needs `by`"))
    if kind in ("pack", "sculpt", "replace") and "obj" not in op:
        out.append(("error", "no-obj", f"{kind} needs an `obj`"))
    if kind == "pack" and "prims" in op and ("first" in op or "count" in op):
        out.append(("warning", "prims-and-range", "`prims` overrides `first` and `count`"))
    if kind == "material" and not any(k in op for k in ("rgba", "ambient", "texture")):
        out.append(
            ("warning", "no-change", "material changes nothing without rgba, ambient or texture")
        )
    return out


def _collision(op: Op) -> list[_Said]:
    out: list[_Said] = []
    if op.get("group") is not None:
        out.append(("error", "not-collision-only", "a collision op carries `group: null` or none"))
    if "sub" in op:
        out.append(("warning", "unknown-key", "`sub` means nothing to `collision`"))
    names = ("tri" in op) + bool(op.get("tris"))
    if not names and not op.get("add") and "solid_box" not in op:
        out.append(("error", "no-selector", "collision needs tri, tris, add or solid_box"))
    if "verts" in op and (names != 1 or op.get("tris")):
        out.append(("error", "bad-value", "verts moves one triangle: give `tri`, not `tris`"))
    if op.get("delete") and not names:
        out.append(("error", "no-selector", "delete needs tri or tris"))
    if names and op.get("chunk", 1) is None:
        out.append(("error", "bad-value", "a triangle is named within a chunk, not null"))
    return out


def _texture(op: Op) -> list[_Said]:
    out: list[_Said] = []
    if "slot" not in op:
        out.append(("error", "no-slot", "texture needs a `slot`"))
    sources = [k for k in ("png", "from", "rgb") if k in op]
    if len(sources) != 1:
        out.append(("error", "no-source", "texture takes exactly one of png, from, rgb"))
    return out
