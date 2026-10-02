"""World -> map-image projection per map, the map-section anchors, and the tunables the window
edits.

The packaged calibration.json carries the hand-measured anchors (world x/z -> pixel on the map
image) and the known area_index -> section pairs. The user's copy, written by `save`, lives in
`${XDG_CONFIG_HOME:-~/.config}/mhfu-hud/calibration.json` and wins over the package default.
Only the window thread owns a Calibration; the reader gets a frozen `SectionMap`.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import pygame

Anchor = tuple[float, float, float, float]
"""World x, world z, image x, image y."""
Affine = tuple[list[float], list[float]]

USER_KEYS = ("quest_map", "village_marker", "area_index_map")
"""What the user copy holds, so anchors keep coming from the package."""
FALLBACK_SECTION_SCALE = 0.030
"""Image px per world unit for a section with one anchor: the median of the snowy anchors."""


def user_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "mhfu-hud" / "calibration.json"


def _package_default() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        files(__package__).joinpath("calibration.json").read_text("utf-8")
    )
    return data


def _merge(base: dict[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


# --- thin-plate spline ---


def _tps_u(r2: float) -> float:
    """The thin-plate basis U(r) = r^2 log r, from r^2."""
    if r2 <= 1e-12:
        return 0.0
    return r2 * 0.5 * math.log(r2)


class _TPS:
    """A 2D -> 2D thin-plate spline through N anchors, one spline per output axis."""

    def __init__(self, anchors: Sequence[Anchor]) -> None:
        self.anchors = list(anchors)
        n = len(anchors)
        size = n + 3
        k = [[0.0] * size for _ in range(size)]  # [K P; P^T 0]
        for i, (xi, zi, _, _) in enumerate(anchors):
            for j, (xj, zj, _, _) in enumerate(anchors):
                k[i][j] = _tps_u((xi - xj) ** 2 + (zi - zj) ** 2)
            k[i][n : n + 3] = [1.0, xi, zi]
            k[n][i], k[n + 1][i], k[n + 2][i] = 1.0, xi, zi
        self.wpx = _solve(k, [a[2] for a in anchors] + [0.0] * 3)
        self.wpy = _solve(k, [a[3] for a in anchors] + [0.0] * 3)

    def evaluate(self, x: float, z: float) -> tuple[float, float]:
        n = len(self.anchors)
        sx = self.wpx[n] + self.wpx[n + 1] * x + self.wpx[n + 2] * z
        sy = self.wpy[n] + self.wpy[n + 1] * x + self.wpy[n + 2] * z
        for i, (xi, zi, _, _) in enumerate(self.anchors):
            u = _tps_u((x - xi) ** 2 + (z - zi) ** 2)
            sx += self.wpx[i] * u
            sy += self.wpy[i] * u
        return sx, sy


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gauss-Jordan with partial pivoting on a copy; zeros for a singular system."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return [0.0] * n
        m[col], m[pivot] = m[pivot], m[col]
        inv = 1.0 / m[col][col]
        m[col] = [v * inv for v in m[col]]
        for r in range(n):
            f = m[r][col]
            if r != col and f != 0.0:
                m[r] = [vr - f * vc for vr, vc in zip(m[r], m[col], strict=True)]
    return [m[i][n] for i in range(n)]


# --- the reader's copy ---


@dataclass(frozen=True)
class SectionMap:
    """One map's anchors and known area_index pairs, frozen for the reader thread."""

    anchors: tuple[tuple[float, float, int | None], ...]
    """World x, world z and the section the anchor lies in (None when unlabelled)."""
    known: Mapping[int, int | None]
    """area_index -> section; None is the base camp."""

    def lookup(self, area_index: int) -> tuple[int | None, bool]:
        """(section, found); a found None is the base camp."""
        if area_index in self.known:
            return self.known[area_index], True
        return None, False

    def snap(self, x: float, z: float, max_dist: float) -> tuple[int | None, float]:
        """The section of the nearest anchor and its distance; None past `max_dist`.

        Each section has its own world frame, so this holds only on an entry point, right
        after a gate transition.
        """
        best: tuple[float, int | None] = (math.inf, None)
        for ax, az, section in self.anchors:
            d = math.hypot(x - ax, z - az)
            if d < best[0]:
                best = (d, section)
        dist, section = best
        return (section if dist <= max_dist else None), dist


# --- the window's calibration ---


class Calibration:
    """The calibration the window edits; `save` writes the user copy at `path`."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_path()
        self.data: dict[str, Any] = {}
        self._cache: dict[tuple[str, int | None], Affine | None] = {}
        self._tps: dict[str, _TPS | None] = {}
        self.load()

    def load(self) -> None:
        try:
            user = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            user = {}
        self.data = _merge(_package_default(), {k: v for k, v in user.items() if k[:1] != "_"})
        self._cache.clear()
        self._tps.clear()

    def save(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            user = {k: self.data[k] for k in USER_KEYS if k in self.data}
            self.path.write_text(json.dumps(user, indent=2) + "\n", "utf-8")
        except OSError:
            return False
        return True

    @property
    def quest(self) -> dict[str, Any]:
        q: dict[str, Any] = self.data["quest_map"]
        return q

    @property
    def village(self) -> dict[str, Any]:
        v: dict[str, Any] = self.data["village_marker"]
        return v

    def _anchors(self, slug: str) -> list[Anchor]:
        rows = self.data.get("map_anchors", {}).get(slug) or []
        return [(float(x), float(z), float(px), float(py)) for x, z, px, py in rows]

    def _sections(self, slug: str) -> dict[tuple[int, int], int | None]:
        rows = self.data.get("anchor_sections", {}).get(slug) or []
        return {(round(x), round(z)): s for x, z, s in rows}

    def section_map(self, slug: str) -> SectionMap:
        sections = self._sections(slug)
        anchors = tuple(
            (x, z, sections.get((round(x), round(z)))) for x, z, _, _ in self._anchors(slug)
        )
        known = self.data.get("area_index_map", {}).get(slug) or {}
        return SectionMap(anchors, {int(k): v for k, v in known.items()})

    def learn(self, slug: str, pairs: Mapping[int, int | None]) -> bool:
        """Add the area_index pairs not known yet; True when any was new."""
        table = self.data.setdefault("area_index_map", {}).setdefault(slug, {})
        new = {str(k): v for k, v in pairs.items() if str(k) not in table}
        table.update(new)
        return bool(new)

    # --- projection ---

    def has_anchors(self, slug: str) -> bool:
        return len(self._anchors(slug)) >= 4

    def map_native_size(self, slug: str) -> tuple[int, int] | None:
        size = self.data.get("map_native", {}).get(slug)
        return (int(size[0]), int(size[1])) if size and len(size) == 2 else None

    def world_to_image(self, slug: str, x: float, z: float) -> tuple[float, float] | None:
        """World x/z -> native image pixels through the whole map's spline."""
        if slug not in self._tps:
            anchors = self._anchors(slug)
            self._tps[slug] = _TPS(anchors) if len(anchors) >= 4 else None
        tps = self._tps[slug]
        return tps.evaluate(x, z) if tps else None

    def section_anchors(self, slug: str, section: int | None) -> list[Anchor]:
        sections = self._sections(slug)
        return [
            a for a in self._anchors(slug) if sections.get((round(a[0]), round(a[1]))) == section
        ]

    def _affine(self, slug: str, section: int) -> Affine | None:
        """Least-squares affine pixel = a*x + b*z + t over a section's anchors."""
        key = (slug, section)
        if key not in self._cache:
            anchors = self.section_anchors(slug, section)
            fit: Affine | None = None
            if len(anchors) >= 3:
                rows = [(x, z, 1.0, px, py) for x, z, px, py in anchors]
                ata = [[sum(r[i] * r[j] for r in rows) for j in range(3)] for i in range(3)]
                atx = [sum(r[i] * r[3] for r in rows) for i in range(3)]
                aty = [sum(r[i] * r[4] for r in rows) for i in range(3)]
                fit = (_solve(ata, atx), _solve(ata, aty))
            self._cache[key] = fit
        return self._cache[key]

    def world_to_image_section(
        self, slug: str, section: int | None, x: float, z: float
    ) -> tuple[float, float] | None:
        """World x/z -> image pixels through `section`'s own anchors (sections have their own
        world frames): an affine fit from 3 anchors, a scaled offset from 1 or 2, else None."""
        if section is None:
            return None
        anchors = self.section_anchors(slug, section)
        if len(anchors) >= 3:
            fit = self._affine(slug, section)
            assert fit is not None
            mx, my = fit
            return mx[0] * x + mx[1] * z + mx[2], my[0] * x + my[1] * z + my[2]
        if len(anchors) == 2:
            (ax, az, apx, apy), (bx, bz, bpx, bpy) = anchors
            dw2 = (bx - ax) ** 2 + (bz - az) ** 2
            if dw2 == 0:
                return apx, apy
            scale = math.sqrt(((bpx - apx) ** 2 + (bpy - apy) ** 2) / dw2)
            return apx + (x - ax) * scale, apy + (z - az) * scale
        if len(anchors) == 1:
            ax, az, apx, apy = anchors[0]
            s = FALLBACK_SECTION_SCALE
            return apx + (x - ax) * s, apy + (z - az) * s
        return None

    def section_centroid_pixel(self, slug: str, section: int | None) -> tuple[float, float] | None:
        anchors = self.section_anchors(slug, section) if section is not None else []
        if not anchors:
            return None
        n = len(anchors)
        return sum(a[2] for a in anchors) / n, sum(a[3] for a in anchors) / n

    def world_to_map(
        self, rect: pygame.Rect, x: float, z: float, player_x: float, player_z: float
    ) -> tuple[float, float]:
        """World x/z -> a pixel in `rect`, for a map without anchors: centred on the player
        (`player_centered`) or on a fixed origin, scaled and rotated by the tunables."""
        q = self.quest
        ox, oz = (
            (player_x, player_z)
            if q["mode"] == "player_centered"
            else (
                q["origin_x"],
                q["origin_z"],
            )
        )
        dx, dz = x - ox, z - oz
        r = math.radians(q["rot_deg"])
        c, s, k = math.cos(r), math.sin(r), q["scale"]
        return rect.centerx + (dx * c - dz * s) * k, rect.centery + (dx * s + dz * c) * k
