# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Named points on stages: the `[[point]]` tables of a map document's `map.toml`, which the
studio's map editor writes and `mhfu rig walk|goto` reads.

    [[point]]
    name = "tigrex_wall"
    stage = 97                    # the stage it was set on; its day/night twin shares the frame
    at = [11000.0, 360.0, 5000.0] # world x, y (the floor), z
    kind = "point"                # or "waypoint", or "climb" with a heading
    heading = 157.0               # climb only: degrees in the game's atan2(dx, dz), into the face
    note = "where the Tigrex gets stuck"
"""

from __future__ import annotations

import math
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import files

MANIFEST = "map.toml"
KINDS = ("point", "waypoint", "climb")
"""A destination; a stop on the way; a stop at the foot of a ledge, climbed facing `heading`."""
Vec3 = tuple[float, float, float]


class PointError(ValueError):
    """A `[[point]]` table that does not describe a point."""


@dataclass(frozen=True)
class Point:
    name: str
    stage: int
    at: Vec3
    kind: str = "point"
    heading: float | None = None
    note: str = ""

    @property
    def xz(self) -> tuple[float, float]:
        return self.at[0], self.at[2]

    def check(self) -> None:
        """Raise PointError unless every field is in range."""
        if not self.name or self.name != self.name.strip():
            raise PointError(f"point name {self.name!r} is empty or padded")
        if self.stage not in files.STAGES:
            raise PointError(f"{self.name}: no stage {self.stage}")
        if len(self.at) != 3 or not all(math.isfinite(v) for v in self.at):
            raise PointError(f"{self.name}: `at` needs three finite numbers")
        if self.kind not in KINDS:
            raise PointError(f"{self.name}: kind {self.kind!r} is not one of {', '.join(KINDS)}")
        if (self.kind == "climb") != (self.heading is not None):
            raise PointError(f"{self.name}: a heading is for a climb, and a climb needs one")
        if self.heading is not None and not math.isfinite(self.heading):
            raise PointError(f"{self.name}: heading is not a number")


def _number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def parse(tables: object) -> list[Point]:
    """The points of a `point` array as TOML parsed it; raises PointError naming the table."""
    if not isinstance(tables, list):
        raise PointError("`point` must be an array of tables")
    out: list[Point] = []
    for i, t in enumerate(tables):
        if not isinstance(t, Mapping):
            raise PointError(f"[[point]] {i} is not a table")
        unknown = set(t) - {"name", "stage", "at", "kind", "heading", "note"}
        stage, at, heading = t.get("stage"), t.get("at"), t.get("heading")
        if unknown:
            raise PointError(f"[[point]] {i}: unknown keys {sorted(unknown)}")
        if not isinstance(t.get("name"), str):
            raise PointError(f"[[point]] {i} needs a string `name`")
        if not isinstance(stage, int) or isinstance(stage, bool):
            raise PointError(f"[[point]] {i} needs an integer `stage`")
        if not isinstance(at, list) or len(at) != 3 or not all(map(_number, at)):
            raise PointError(f"[[point]] {i} needs `at = [x, y, z]`")
        if heading is not None and not _number(heading):
            raise PointError(f"[[point]] {i}: heading must be a number")
        p = Point(
            t["name"],
            stage,
            (float(at[0]), float(at[1]), float(at[2])),
            str(t.get("kind", "point")),
            None if heading is None else float(heading),
            str(t.get("note", "")),
        )
        p.check()
        out.append(p)
    names = [p.name for p in out]
    if dup := sorted({n for n in names if names.count(n) > 1}):
        raise PointError(f"point names used twice: {', '.join(dup)}")
    return out


def dump(points: Iterable[Point]) -> list[dict[str, Any]]:
    """The `point` array for TOML, the inverse of `parse`."""
    out = []
    for p in points:
        t: dict[str, Any] = {"name": p.name, "stage": p.stage, "at": list(p.at)}
        if p.kind != "point":
            t["kind"] = p.kind
        if p.heading is not None:
            t["heading"] = p.heading
        if p.note:
            t["note"] = p.note
        out.append(t)
    return out


def load(path: str | Path) -> list[Point]:
    """The points of a `map.toml`, or of the map document folder holding one."""
    p = Path(path).expanduser()
    if p.is_dir():
        p = p / MANIFEST
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise PointError(f"{p}: {e}") from None
    try:
        return parse(data.get("point", []))
    except PointError as e:
        raise PointError(f"{p}: {e}") from None


def find(points: Iterable[Point], name: str) -> Point:
    points = list(points)
    for p in points:
        if p.name == name:
            return p
    known = ", ".join(p.name for p in points) or "none"
    raise KeyError(f"no point {name!r} (known: {known})")
