# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What a move's miniature shows: every anim its action plays, in turn, on the model of the
script's own species; no toolkit and no GL here (`render.preview` draws it)."""

from __future__ import annotations

from dataclasses import dataclass

from mhfu_port.model import Clip

from mhfu_studio.monster.actions import Plays
from mhfu_studio.monster.core.scene import Scene


@dataclass(frozen=True)
class Reel:
    """A move's clips on `scene`; without any, `why`."""

    title: str
    scene: Scene | None = None
    clips: tuple[Clip, ...] = ()
    why: str = ""
    missing: tuple[int, ...] = ()
    """Anims the action plays that the model has no clip for."""

    def caption(self, at: int = 0) -> str:
        """`anim 47 · 60 frames · 1 of 2`, of the clip `at` playing; else why there is none."""
        if not self.clips:
            return self.why
        c = self.clips[at]
        named = f" {c.names[0]}" if c.names else ""
        parts = [f"anim {c.slot}{named}", f"{c.frames} frames"]
        if len(self.clips) > 1:
            parts.append(f"{at + 1} of {len(self.clips)}")
        if self.missing:
            parts.append(f"no clip for {', '.join(map(str, self.missing))}")
        return " · ".join(parts)


def reel(title: str, scene: Scene, plays: Plays) -> Reel:
    """The clips of the anims `plays` names that `scene` has, the one the 3D view plays first."""
    if plays.slot is None:
        why = (
            "Its code picks the anim while it runs"
            if plays.computed
            else "Names no anim: the clip playing before goes on"
        )
        return Reel(title, scene, why=why)
    have: list[Clip] = []
    missing: list[int] = []
    for slot in (plays.slot, *plays.also):
        try:
            have.append(scene.clip(slot))
        except KeyError:
            missing.append(slot)
    if not have:
        return Reel(title, scene, why=f"{scene.name} has no clip for anim {plays.slot}")
    return Reel(title, scene, tuple(have), missing=tuple(missing))
