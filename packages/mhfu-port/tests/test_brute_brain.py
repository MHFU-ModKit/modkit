# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

from mhfu.em.script import build
from mhfu_port import layout, manifest, motion, slots
from mhfu_port.brain import p3, splice

PORT = Path(__file__).parents[3] / "ports" / "brute_tigrex.toml"


def test_loads():
    m = manifest.load(PORT)
    assert manifest.loads(manifest.dumps(m)) == m
    assert m.brain is not None
    assert (m.brain.donor, m.brain.splice, m.brain.entry) == (23, "base_3", "base_12")


def test_charge_is_the_host_pair_of_the_donor_charge():
    m = manifest.load(PORT)
    assert m.brain is not None
    assert m.moves["charge"].pair == m.brain.host_pair((7, 13))


def test_pins_land(data):
    m = manifest.load(PORT)
    donor = motion.moveset(data.p3rd.read(m.source.anim))
    host = slots.anim_of(data.fu.read(m.port.host_frame))
    got = layout.of(m, donor, host)
    pinned = [c for c in m.clips.values() if c.slot is not None]
    assert pinned and got.unplaced == ()
    assert all(got.entries[c.slot] == c.id for c in pinned if c.slot is not None)
    assert set(got.entries.values()) == set(donor)
    assert not got.partial & got.placed


def test_brain_splices_into_its_host(data):
    m = manifest.load(PORT)
    assert m.brain is not None
    host = build.lift_row(data.fu, m.port.host_species, m.port.host_species)
    s = splice.splice(p3.lift(data.p3rd, m.brain.donor, m.brain.slot), host, m.brain)
    assert not [f for f in s.findings if f.level == "error" or f.code == "P3_PAIR_UNUSED"]
    assert build.Built.of(host, s.edits).ok
