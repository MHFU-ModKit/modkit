# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Which donor bone drives each host joint when a moveset is retargeted onto the host's rig."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from mhp_formats.skeleton import Skeleton

from .fk import bind_world
from .rig import lead_origin

DEPTH_PENALTY = 50.0
"""Bind distance a pair pays per level of tree depth the two joints differ by."""

REACH = 0.25
"""The farthest a pair may sit apart, as a share of the donor's bounding-box diagonal: past it a
host joint has no counterpart and stays at rest."""


def match(donor: Skeleton, host: Skeleton) -> dict[int, int | None]:
    """`{host joint: donor bone}`, None where no donor bone corresponds (the joint stays at
    rest). Greedy on bind distance plus `DEPTH_PENALTY`, each donor bone used once."""
    dp = [b.parent for b in donor.bones]
    hp = [b.parent for b in host.bones]
    donor_bind = bind_world(dp, [b.position for b in donor.bones])
    host_bind = bind_world(hp, [b.position for b in host.bones])
    sw = np.array(donor_bind, dtype=np.float64)
    dw = np.array(host_bind, dtype=np.float64)
    out: dict[int, int | None] = dict.fromkeys(range(len(dw)))
    if not len(sw) or not len(dw):
        return out
    reach = REACH * float(np.sqrt(np.sum(np.ptp(sw, axis=0) ** 2)))
    dist = np.sqrt(np.sum((dw[:, None, :] - sw[None, :, :]) ** 2, axis=2))
    gap = np.abs(np.subtract.outer(depth_of(hp), depth_of(dp)))
    cost = dist + DEPTH_PENALTY * gap
    hi, di = np.nonzero(dist <= reach)
    used: set[int] = set()
    for k in np.lexsort((di, hi, cost[hi, di])):
        h, d = int(hi[k]), int(di[k])
        if out[h] is None and d not in used:
            out[h] = d
            used.add(d)
    _align_origin_chains(out, lead_origin(donor_bind), lead_origin(host_bind))
    return out


def depth_of(parents: Sequence[int]) -> list[int]:
    """Tree depth per joint, a root 0; a joint in a parent cycle counts as a root."""
    out = [-1] * len(parents)

    def depth(i: int, chain: set[int]) -> int:
        if out[i] < 0:
            chain.add(i)
            p = parents[i]
            ok = 0 <= p < len(parents) and p not in chain
            out[i] = depth(p, chain) + 1 if ok else 0
        return out[i]

    return [depth(i, set()) for i in range(len(parents))]


def _align_origin_chains(out: dict[int, int | None], donor_lead: int, host_lead: int) -> None:
    """Pair the two leading origin chains from their ends, where the hip is.

    Every origin joint sits at one point, so distance pairs them from the front, and a host
    chain longer than the donor's then starves its last joint, the hip, which carries the
    height: the port never lifts. Surplus leading host joints stay at rest.
    """
    if donor_lead <= 0 or host_lead <= 0:
        return
    for h in range(host_lead):
        out[h] = None
    for j in range(min(host_lead, donor_lead)):
        d = donor_lead - 1 - j
        for h, bone in out.items():
            if bone == d:
                out[h] = None
        out[host_lead - 1 - j] = d
