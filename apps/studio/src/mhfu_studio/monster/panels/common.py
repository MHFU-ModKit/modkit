# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What the monster panels share, toolkit-free: data colours, the bone-span format and a
move's kind."""

from __future__ import annotations

from collections.abc import Sequence

from mhfu_port.manifest import Move

RGBA = tuple[float, float, float, float]
#: FILLER is the loud one: a successful override onto idle looks exactly like a failed one
COVERAGE: dict[str, RGBA] = {
    "CARRIED": (0.55, 0.82, 0.55, 1.0),
    "FILLER": (0.98, 0.70, 0.20, 1.0),
    "HOST": (0.60, 0.72, 0.98, 1.0),
    "ALTERED": (0.90, 0.55, 0.95, 1.0),
    "UNKNOWN": (0.55, 0.58, 0.64, 1.0),
}
MARKERS: dict[str, RGBA] = {
    "gate": (0.98, 0.70, 0.20, 0.95),
    "window": (0.55, 0.82, 0.98, 0.95),
    "effect": (0.85, 0.55, 0.98, 0.95),
    "ours": (0.55, 0.90, 0.60, 0.95),
    "impact": (0.98, 0.35, 0.38, 1.0),
}
#: an own move's attack window on the Timeline
ATTACK: RGBA = (0.94, 0.42, 0.36, 0.85)


def bone_span(bones: Sequence[int]) -> str:
    """`10-14, 18`: a bone list at a table column's width."""
    if not bones:
        return "·"
    out, start, prev = [], bones[0], bones[0]
    for b in [*bones[1:], None]:
        if b is not None and b == prev + 1:
            prev = b
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        if b is not None:
            start = prev = b
    return ", ".join(out)


def kind(mv: Move) -> str:
    """`own move`, or the base monster's pair it rides."""
    return "own move" if mv.pair is None else f"({mv.main},{mv.sub})"
