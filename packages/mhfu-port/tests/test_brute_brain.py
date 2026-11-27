# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from collections import Counter
from pathlib import Path

from mhfu import addresses
from mhfu.em.script import build, ir
from mhfu.em.script import vars as script_vars
from mhfu_port import layout, manifest, motion, moves, slots
from mhfu_port.brain import own, p3, splice

PORT = Path(__file__).parents[3] / "ports" / "brute_tigrex.toml"


def test_loads():
    m = manifest.load(PORT)
    assert manifest.loads(manifest.dumps(m)) == m
    assert m.brain is not None
    assert (m.brain.donor, m.brain.splice, m.brain.entry) == (23, "base_3", "base_12")


OWN = [(1, 15), (2, 19), (2, 20), (2, 23), (7, 13), (7, 14), (7, 15)]


def test_moves_with_no_host_pair_are_derived():
    m = manifest.load(PORT)
    assert m.brain is not None and m.brain.fatigue
    assert [p for p in own.pairs(m.brain)] == OWN


def spliced(data):
    m, derived = own.expand(manifest.load(PORT), data)
    assert m.brain is not None
    host = build.lift_row(data.fu, m.port.host_species, m.port.host_species)
    survey = script_vars.survey(host, data.fu.em(m.port.host_species))
    donor = p3.lift(data.p3rd, m.brain.donor, m.brain.slot)
    s = splice.splice(
        donor, host, m.brain, survey, moves.slots(m), None, (), moves.picks(m), moves.combo_ids(m)
    )
    return m, derived, host, s


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
    m, derived, host, s = spliced(data)
    found = [*derived, *s.findings]
    assert not [f for f in found if f.level == "error" or f.code == "P3_PAIR_UNUSED"]
    assert build.Built.of(host, s.edits).ok
    heads = {own.own_name(p) for p in OWN}
    pickers = {n for n, mv in m.moves.items() if mv.picker}
    assert pickers == {"p3_2_19", "p3_7_14"}, "the handlers that play other clips by the bearing"
    variants = {v for n in pickers for _, _, v in m.moves[n].pick}
    assert (heads - pickers) | variants <= set(s.slots)
    assert {n for _, n in s.uses if isinstance(n, str)} == (heads - pickers) | variants


def test_brain_has_no_split_and_one_draw_after_a_draw(data):
    _, _, host, s = spliced(data)
    found = Counter(f.code for f in [*s.findings, *build.Built.of(host, s.edits).findings])
    assert not found["P3_SPLIT"] and not found["ARM_UNREACHABLE"]
    assert found["CORRELATED_DRAW"] == 1


def test_brain_enters_own_moves_and_reads_tired_from_gauge_a(data):
    _, _, host, s = spliced(data)
    p = build.Built.of(host, s.edits).program
    insns = [n for r in p.routines() for n in ir.walk(r.body) if isinstance(n, ir.Insn)]
    own_enters = {
        n.args["sub"]
        for n in insns
        if n.op.name == "enter" and n.args["main"] == addresses.OWN_MOVE_MAIN
    }
    assert own_enters == set(s.slots.values())
    tests = [n for r in p.routines() for n in ir.walk(r.body) if isinstance(n, ir.Block)]
    assert any(n.op.name == "if_gauge_a_low" for n in tests)


def test_brain_keeps_its_call_history_in_the_first_free_variable(data):
    m, _, _, s = spliced(data)
    assert m.brain is not None and m.brain.history is None
    (note,) = [f for f in s.findings if f.code == "P3_HISTORY"]
    assert note.level == "info" and "script variable 2" in note.text
