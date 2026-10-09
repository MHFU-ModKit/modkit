# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The tail tip of the shipped ports: mesh record 1, its map, and the weld the map makes."""

from pathlib import Path

import numpy as np
import pytest
from mhfu_port import build, fk, manifest, motion, verify
from mhfu_port.build import Built

PORTS = Path(__file__).parents[3] / "ports"
WANT = {  # the map, the tip's first group, its cut face's group, seam vertices
    "zinogre": (((46, 42), (47, 42), (48, 43), (49, 44), (50, 45)), 175, 180, 33),
    "brute_tigrex": (((44, 42), (45, 42), (46, 43)), 86, 87, 18),
}
NATIVE_REACH = 408.0
"""Units the native Tigrex's tip reaches from its chain's root: where its dropped tail lies."""


@pytest.fixture(scope="module", params=sorted(WANT))
def port(request: pytest.FixtureRequest, data: object) -> tuple[str, Built, verify.Port]:
    b = build.build(manifest.load(PORTS / f"{request.param}.toml"), data)  # type: ignore[arg-type]
    return request.param, b, verify.Port(b.pac)


def _skin(p: verify.Port, groups: range) -> fk.Skin:
    pos = [v for g in groups for v in p.model.positions(g)]
    return fk.Skin(pos, [vi for g in groups for vi in p.model.influences(g)], p.rig.n)


def test_mesh_one(port):
    name, b, p = port
    pairs, first, cut, _ = WANT[name]
    assert b.tip is not None and b.tip.pairs == pairs
    body, tip = p.model.meshes
    assert len(body.groups) == first and len(p.model.groups()) == cut + 1
    assert [g.material for g in tip.groups][-1] == 1
    assert not set(tip.materials) & set(body.materials)


def test_weld(port):
    """Every chain joint posed as its carrier welds the tip's seam onto the stump within 0.1 u,
    at bind and through the idle; unattached it lies hundreds of units away."""
    name, b, p = port
    assert b.tip is not None
    n = len(p.model.meshes[0].groups)
    body, tip = _skin(p, range(n)), _skin(p, range(n, len(p.model.groups())))
    at = tip.apply(p.rig.deform(fk.attach(p.rig.bind_world, b.tip.pairs)))
    gap = np.linalg.norm(at[:, None] - body.positions[None], axis=-1)
    seam = np.flatnonzero(gap.min(axis=1) < 0.5)
    partner = gap.argmin(axis=1)[seam]
    assert len(seam) == WANT[name][3]
    idle = p.clip(1)
    assert idle is not None
    for f in np.linspace(0, motion.frames(idle), 9):
        world = p.rig.world(*fk.Curves(idle, p.rig).at(f))
        stump = body.apply(p.rig.deform(world))[partner]
        welded = tip.apply(p.rig.deform(fk.attach(world, b.tip.pairs)))[seam]
        assert np.linalg.norm(welded - stump, axis=1).max() < 0.1
        loose = tip.apply(p.rig.deform(world))[seam]
        assert np.linalg.norm(loose - stump, axis=1).max() > 400


def test_drop_lies_at_its_root(port):
    """The dropped tail draws the chain from its root's bind: the tip lies about it, as close as
    the native's (`M·v`; the root sits at the origin, so `M·T(-bind_root)·v` reads the same)."""
    _, b, p = port
    assert b.tip is not None
    n = len(p.model.meshes[0].groups)
    tip = _skin(p, range(n, len(p.model.groups())))
    root = p.rig.bind_joints[b.tip.pairs[0][0]]
    assert np.linalg.norm(root) < 1e-3
    assert np.linalg.norm(tip.positions - root, axis=1).max() < NATIVE_REACH
