# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

from mhfu_port import layout, manifest, motion, slots
from mhfu_port.brain import p3

PORT = Path(__file__).parents[3] / "ports" / "brute_tigrex.toml"
CALLS = ("call", "call_base")


def region(brain: p3.Brain, entry: str) -> dict[str, p3.Routine]:
    """The routines `entry` runs, through `call` and `call_base`."""
    seen: dict[str, p3.Routine] = {}
    todo = [entry]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen[name] = brain.routines[name]
        calls = [
            n
            for n in p3.walk(seen[name].body)
            if isinstance(n, p3.Insn) and n.op.name in CALLS and n.op.calls is not None
        ]
        todo += [c for n in calls if (c := brain.entry(n.op.calls, n.args["routine"]))]
    return seen


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


def test_pairs_cover_the_region(data):
    m = manifest.load(PORT)
    assert m.brain is not None
    brain = p3.lift(data.p3rd, m.brain.donor, m.brain.slot)
    entered = {
        f"{n.args['main']},{n.args['sub']}"
        for r in region(brain, m.brain.entry).values()
        for n in p3.walk(r.body)
        if isinstance(n, p3.Insn) and n.op.name == "enter"
    }
    assert set(m.brain.pairs) == entered
