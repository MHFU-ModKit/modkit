# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Image statistics and the golden files that hold them: never the image itself.

A golden records the renderer it came from. On the same renderer the RGBA hash must match
exactly; on another (Metal against llvmpipe) only the statistics are compared, within a
`Tolerance`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from mhfu_studio.shell.findings import Finding

BINS = 16
#: coverage is also kept per cell of a GRID x GRID split, so a shape that moved shows
GRID = 4
#: a channel this far from the clear colour counts as drawn
COVER_THRESHOLD = 12
GOLDEN_VERSION = 1


@dataclass(frozen=True)
class Stats:
    """`mean` per RGBA channel in levels; `histogram` per RGB channel, fractions of pixels."""

    size: tuple[int, int]
    renderer: str
    sha256: str
    clear: tuple[float, float, float, float]
    coverage: float
    #: coverage per cell, row-major from the top left
    cells: tuple[float, ...]
    mean: tuple[float, float, float, float]
    histogram: tuple[tuple[float, ...], ...]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> Stats:
        def floats(v: Any) -> tuple[float, ...]:
            return tuple(float(x) for x in v)

        w, h = (int(x) for x in d["size"])
        r, g, b, a = floats(d["clear"])
        mr, mg, mb, ma = floats(d["mean"])
        return cls(
            size=(w, h),
            renderer=str(d["renderer"]),
            sha256=str(d["sha256"]),
            clear=(r, g, b, a),
            coverage=float(d["coverage"]),
            cells=floats(d["cells"]),
            mean=(mr, mg, mb, ma),
            histogram=tuple(floats(c) for c in d["histogram"]),
        )


@dataclass(frozen=True)
class Tolerance:
    """How far statistics may drift between renderers."""

    #: fraction of the image, and of each cell
    coverage: float = 0.01
    cells: float = 0.01
    #: levels, per channel
    mean: float = 2.0
    #: fraction of pixels that moved bins, per channel
    histogram: float = 0.03


def measure(rgba: npt.NDArray[np.uint8], clear: Sequence[float], renderer: str) -> Stats:
    """The statistics of `(h, w, 4)` uint8 drawn over the clear colour `clear` (0..1)."""
    img = np.ascontiguousarray(rgba, dtype=np.uint8)
    h, w = img.shape[:2]
    rgb = img[:, :, :3].astype(np.int16)
    bg = np.round(np.asarray(clear[:3], dtype=np.float64) * 255).astype(np.int16)
    covered = np.abs(rgb - bg).max(axis=2) > COVER_THRESHOLD
    n = float(h * w)
    hist = tuple(
        tuple(
            round(float(c) / n, 6) for c in np.bincount(img[:, :, i].ravel() >> 4, minlength=BINS)
        )
        for i in range(3)
    )
    m = [round(float(v), 4) for v in img.reshape(-1, 4).mean(axis=0)]
    return Stats(
        size=(w, h),
        renderer=renderer,
        sha256=hashlib.sha256(img.tobytes()).hexdigest(),
        clear=(float(clear[0]), float(clear[1]), float(clear[2]), float(clear[3])),
        coverage=round(float(covered.mean()), 6),
        cells=tuple(
            round(float(c.mean()), 6)
            for band in np.array_split(covered, GRID, axis=0)
            for c in np.array_split(band, GRID, axis=1)
        ),
        mean=(m[0], m[1], m[2], m[3]),
        histogram=hist,
    )


def compare(got: Stats, want: Stats, tol: Tolerance | None = None, name: str = "") -> list[Finding]:
    """What differs between a render and its golden; empty when it passes."""
    tol = tol or Tolerance()
    out: list[Finding] = []

    def bad(code: str, message: str) -> None:
        out.append(Finding("error", code, message, where=name, target=name or None))

    if got.size != want.size:
        bad("size", f"rendered {got.size}, the golden is {want.size}")
        return out
    if got.renderer == want.renderer:
        if got.sha256 != want.sha256:
            bad("hash", "the pixels changed on the golden's own renderer")
    else:
        out.append(
            Finding(
                "info",
                "renderer",
                f"statistics only: rendered on {got.renderer}, the golden on {want.renderer}",
                where=name,
            )
        )
    d = abs(got.coverage - want.coverage)
    if d > tol.coverage:
        bad("coverage", f"coverage {got.coverage:.4f}, golden {want.coverage:.4f} (off {d:.4f})")
    cell = max(abs(a - b) for a, b in zip(got.cells, want.cells, strict=True))
    if cell > tol.cells:
        bad("cells", f"a cell's coverage moved by {cell:.4f}: the picture shifted")
    for ch, a, b in zip("RGBA", got.mean, want.mean, strict=True):
        if abs(a - b) > tol.mean:
            bad("mean", f"mean {ch} {a:.2f}, golden {b:.2f}")
    for ch, ha, hb in zip("RGB", got.histogram, want.histogram, strict=True):
        moved = sum(abs(x - y) for x, y in zip(ha, hb, strict=True)) / 2
        if moved > tol.histogram:
            bad("histogram", f"{moved:.4f} of the pixels moved bins in {ch}")
    return out


def compare_all(
    got: Mapping[str, Stats], want: Mapping[str, Stats], tol: Tolerance | None = None
) -> list[Finding]:
    """Every image against its golden, plus the images one side has and the other lacks."""
    out: list[Finding] = []
    for name in want:
        if name not in got:
            out.append(Finding("error", "missing", "in the golden but not rendered", where=name))
    for name, stats in got.items():
        if name in want:
            out += compare(stats, want[name], tol, name)
        else:
            out.append(
                Finding("warning", "new", "not in the golden (--update adds it)", where=name)
            )
    return out


def load_golden(path: Path) -> dict[str, Stats]:
    data = json.loads(Path(path).read_text())
    if data.get("version") != GOLDEN_VERSION:
        raise ValueError(f"{path}: golden version {data.get('version')}, want {GOLDEN_VERSION}")
    return {k: Stats.from_json(v) for k, v in data["images"].items()}


def save_golden(path: Path, stats: Mapping[str, Stats]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    images = [
        f"  {json.dumps(name)}: {{\n"
        + ",\n".join(f"   {json.dumps(k)}: {json.dumps(v)}" for k, v in s.to_json().items())
        + "\n  }"
        for name, s in stats.items()
    ]
    body = ",\n".join(images)
    path.write_text(f'{{\n "version": {GOLDEN_VERSION},\n "images": {{\n{body}\n }}\n}}\n')
    return path
