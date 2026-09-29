# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Whether a port keeps the donor's own skin: every vertex on the same joints, each weight within
one step of the u8 the PMO stores it in."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from mhp_formats import pmo
from mhp_formats.pmo import Influence

from . import build, mesh
from .data import Data
from .fk import WEIGHT_EPS
from .manifest import Manifest, Skin

WEIGHT_STEP = 1 / 128
"""One step of the port's u8 weights."""

Weights = dict[int, float]
"""Joint -> weight, summing to 1."""


def normalised(influences: Sequence[Influence]) -> Weights:
    """The influences a vertex blends: repeated joints summed, empty slots dropped."""
    out: Weights = {}
    for b, w in influences:
        if w > WEIGHT_EPS and b >= 0:
            out[b] = out.get(b, 0.0) + w
    total = sum(out.values())
    return {b: w / total for b, w in out.items()}


def expected(
    influences: Sequence[Sequence[Influence]], joint_of: Mapping[int, int]
) -> list[Weights]:
    """Each donor vertex's weights on the port's joints; a bone with no joint drops and the
    vertex's other weights make up for it."""
    return [normalised([(joint_of[b], w) for b, w in vi if b in joint_of]) for vi in influences]


@dataclass(frozen=True)
class Fidelity:
    expected: int
    """Donor vertices."""
    vertices: int
    """Port vertices; the rest compares the two in order."""
    wrong_set: int
    """Vertices on other joints than the donor's."""
    max_error: float
    mean_error: float
    """Over every weight of the vertices on the right joints."""

    @property
    def within_step(self) -> bool:
        return self.max_error <= WEIGHT_STEP

    @property
    def ok(self) -> bool:
        return self.vertices == self.expected and not self.wrong_set and self.within_step


def compare(want: Sequence[Weights], got: Sequence[Sequence[Influence]]) -> Fidelity:
    """The port's influences, vertex by vertex, against `want`."""
    wrong = 0
    errors: list[float] = []
    for w, g in zip(want, got, strict=False):
        have = normalised(g)
        if set(have) != set(w):
            wrong += 1
            continue
        errors += [abs(w[b] - have[b]) for b in w]
    return Fidelity(
        len(want),
        len(got),
        wrong,
        max(errors, default=0.0),
        sum(errors) / len(errors) if errors else 0.0,
    )


@dataclass(frozen=True)
class Report:
    skin: Skin
    """The skin the porter built, after its fallbacks: only "source" keeps the donor's."""
    fidelity: Fidelity


def of_build(m: Manifest, data: Data) -> Report:
    """The porter's own steps up to the PMO, read back, against the donor's weights through the
    binding's joint map."""
    d, h = build.donor(m, data), build.host(m, data)
    record_of = build.record_map(d, m.build)
    bind = build.binding(m.build, d, h, build.animated(m.build, record_of, len(d.skeleton.bones)))
    parts = build.parts(d, m.build)
    skinned, used = build.skin(parts, m.build, bind, h)
    model = pmo.Pmo.from_bytes(mesh.build(skinned, d.model.scale).to_bytes())
    want = expected([vi for p in parts for vi in p.influences], bind.joint_of)
    got = [vi for g in range(len(model.groups())) for vi in model.influences(g)]
    return Report(used, compare(want, got))
