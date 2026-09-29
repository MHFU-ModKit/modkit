# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Where a built port's joints go under its clips: against the donor's own pose, and where its
rest pose puts its feet."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from mhp_formats.anim import Clip
from mhp_formats.skeleton import Skeleton

from . import fk
from .verify import Port, correspondence

TOLERANCE = 1.0
"""Units a joint may sit from the donor's and still play the donor's motion."""
LINED_UP = 0.8
"""The share of donor bones the correspondence must place before a comparison means anything."""
IDLE = 1
"""The slot a big monster idles in."""


@dataclass(frozen=True)
class SlotPose:
    """One donor clip against the port's, at the donor's middle key."""

    slot: int
    frame: float
    joints: int
    median: float
    p90: float
    worst: float
    lift: float
    """How much higher the port stands, taken out before comparing (the manifest's lift)."""


@dataclass(frozen=True)
class Poses:
    matched: int
    bones: int
    pad: int
    unmatched: list[int]
    slots: list[SlotPose] = field(default_factory=list)
    absent: list[int] = field(default_factory=list)
    """Donor slots the port does not fill."""
    partial: list[tuple[int, int, int]] = field(default_factory=list)
    """`(slot, port joints, donor joints)` where the host fills the slot in only some parts."""
    compared: int = 0
    """Donor bones compared: those of its root tree."""

    @property
    def worst(self) -> float:
        return max((s.worst for s in self.slots), default=0.0)

    @property
    def ok(self) -> bool:
        return bool(self.slots) and self.worst <= TOLERANCE


def compare(
    port: Port, donor: Skeleton, clips: Sequence[Clip | None], record_of: Mapping[int, int]
) -> Poses:
    """Each donor clip posed on the donor's rig (bone -> record `record_of`) against the port's
    clip in the same slot on the port's rig, joint by joint under the correspondence.

    Raises ValueError when the correspondence places too few donor bones to judge."""
    c = correspondence(donor, port.skeleton)
    n = len(donor.bones)
    if len(c.joint_of) < LINED_UP * n:
        raise ValueError(
            f"only {len(c.joint_of)} of {n} donor bones line up with the port's rig: "
            "is this the donor the port was built from?"
        )
    out = Poses(
        len(c.joint_of), n, c.pad, sorted(set(range(n)) - set(c.joint_of)), compared=len(c.tree)
    )
    rig = fk.Rig.from_skeleton(donor)
    tree = c.tree
    bones = np.array(list(tree), dtype=np.intp)
    joints = np.array(list(tree.values()), dtype=np.intp)
    root = 0  # where the correspondence's walk starts
    for slot, clip in enumerate(clips):
        if clip is None:
            continue
        built = port.clip(slot)
        if built is None:
            out.absent.append(slot)
            continue
        want = fk.Curves(clip, rig, record_of)
        have = fk.Curves(built, port.rig)
        if 2 * len(have.driven) < len(want.driven):
            out.partial.append((slot, len(have.driven), len(want.driven)))
            continue
        keys = want.keys()
        if len(keys) < 2:
            continue
        frame = float(keys[len(keys) // 2])
        ws = rig.world(*want.at(frame))[:, :3, 3]
        wb = port.rig.world(*have.at(frame))[:, :3, 3]
        lift = float(wb[c.joint_of[root], 1] - ws[root, 1])
        d = np.sort(np.linalg.norm(ws[bones] + (0.0, lift, 0.0) - wb[joints], axis=1))
        out.slots.append(
            SlotPose(
                slot,
                frame,
                len(d),
                float(d[len(d) // 2]),
                float(d[int(len(d) * 0.9)]),
                float(d[-1]),
                lift,
            )
        )
    return out


@dataclass(frozen=True)
class Floor:
    """Where the idle's first frame puts the mesh's lowest point, by the animation alone; a native
    reads near 0. The game places a port otherwise: the Zinogre reads +105 at his manifest's lift
    and stands slightly below the ground, so this is not yet a ground lift."""

    height: float
    vertex: int
    joint: int
    """The vertex's heaviest joint."""


def rest_floor(port: Port, slot: int = IDLE) -> Floor:
    """The lowest vertex the animation moves (none riding a joint it does not reach), posed at
    the first frame of `slot`."""
    clip = port.clip(slot)
    if clip is None:
        raise ValueError(f"slot {slot}, the idle, is empty in this port")
    s = port.surface
    moving = ~s.riding(port.animated)
    if not moving.any():
        raise ValueError("no vertex rides an animated joint")
    posed = s.skin.apply(fk.deform_matrices(port.rig, clip, 0))
    index = np.flatnonzero(moving)
    v = int(index[posed[index, 1].argmin()])
    return Floor(float(posed[v, 1]), v, int(s.dominant[v]))
