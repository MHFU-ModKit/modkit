# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Zinogre's MHP3rd script spliced into em75 by ports/zinogre.toml's `[brain]`, and the tables
and moves module that go with it. Needs both games' files."""

import copy
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
from mhfu import addresses
from mhfu.em.script import brainfile, build, draw, edit, ir
from mhfu.em.script import vars as script_vars
from mhfu.em.script.budget import LIMIT
from mhfu_port import behaviour, manifest, moves
from mhfu_port.brain import charge, fatigue, own, p3, p3ops, splice
from mhfu_port.build import build as build_port
from mhfu_port.cli import main
from mhfu_port.cli.build import modules
from mhfu_port.data import Data

PORT = Path(__file__).parents[3] / "ports" / "zinogre.toml"
OWN = addresses.OWN_MOVE_MAIN
TURN = (1, 2)
KEPT = {f"b{i}" for i in [*range(1, 7), *range(12, 22), 36]}
"""The hand-built rules that stay: the lunge's skids, the notice, the combat flag, the part breaks
and the roar signal."""


@pytest.fixture(scope="module")
def zinogre(data: Data) -> SimpleNamespace:
    """The donor, the host row, the manifest with its derived moves and the helpers' tables."""
    m, derived = own.expand(manifest.load(PORT), data)
    assert m.brain is not None
    ch, said = charge.table(m, data)
    return SimpleNamespace(
        donor=p3.lift(data.p3rd, m.brain.donor, m.brain.slot),
        host=build.lift_row(data.fu, 75, 75),
        overlay=data.fu.em(75),
        survey=script_vars.survey(build.lift_row(data.fu, 75, 75), data.fu.em(75)),
        m=m,
        derived=derived,
        slots=moves.slots(m),
        charge=ch,
        said=said,
    )


def splice_of(z: SimpleNamespace, b: manifest.Brain):
    named = z.charge.named if z.charge is not None else ()
    s = splice.splice(z.donor, z.host, b, z.survey, z.slots, z.overlay, named)
    return s, build.Built.of(z.host, s.edits)


def codes(found: Sequence[object]) -> Counter[str]:
    return Counter(f"{f.level} {f.code}" for f in found)  # type: ignore[attr-defined]


def test_the_manifest_carries_its_brain(zinogre):
    m = zinogre.m
    b = m.brain
    assert (b.donor, b.splice, b.entry, b.fatigue, b.charge) == (40, "base_3", "base_3", True, True)
    assert b.when == {"if_target_set": True, "if_target_state": True}
    assert manifest.loads(manifest.dumps(m)) == m
    assert len(b.pairs) == 104 and len(b.derived()) == len(own.pairs(b)) == 34


def test_a_donor_pair_plays_the_authored_move_where_it_is_the_donors(zinogre):
    b = zinogre.m.brain
    assert b.targets((1, 16)) == ["charge_up"] and b.targets((1, 15)) == ["p3_1_15"]
    assert b.targets((1, 2)) == ["break_howl"]
    assert [b.targets(p) for p in [(2, 3), (2, 4)]] == [["dash_stop"], ["dash"]]
    assert b.targets((7, 29)) == b.targets((7, 30)) == [TURN, "dash"]
    assert zinogre.m.moves["dash"].after is None


def test_a_wrapper_turns_and_plays_the_move_of_the_pair_it_leads_to(zinogre):
    b = zinogre.m.brain
    assert b.targets((7, 23)) == [TURN, "p3_7_0"] and b.targets((7, 0)) == ["p3_7_0"]
    assert b.targets((7, 110)) == [TURN, "p3_7_109"] and b.targets((7, 109)) == ["p3_7_109"]
    twins = [b.targets((2, 11)), b.targets((7, 5))]
    assert twins == [["p3_2_10"], ["p3_7_4"]]
    assert set(b.derived().values()) >= {(7, 0), (7, 109), (2, 10), (7, 4), (1, 4), (7, 13)}
    assert b.shared() <= set(b.derived().values()) and (7, 0) in b.shared()
    assert (7, 13) in b.shared() and b.targets((7, 84)) == [TURN, "p3_7_13"]


def chain_of(m: manifest.Manifest, name: str) -> list[tuple[object, ...]]:
    """What a derived move plays, step by step: clip, attacks, steer, length."""
    out = []
    mv: manifest.Move | None = m.moves[name]
    while mv is not None:
        clip = m.clips[mv.clip].source if mv.clip else mv.anim
        attacks = tuple((a.id, a.frame, a.end) for a in mv.attacks)
        out.append((clip, attacks, mv.steer, mv.length))
        mv = m.moves[mv.after] if mv.after else None
    return out


def test_the_pairs_that_share_a_move_derive_the_same_one(zinogre, data):
    """A pair that names its twin's move would derive that very move itself. (7, 71), (7, 82) and
    (7, 119) are not the script's: only wrappers lead to them, and play (7, 13)'s move."""
    b = zinogre.m.brain
    named = {k: v for k, v in b.pairs.items() if isinstance(v, str)}
    twins = {k: v for k, v in named.items() if v in b.derived()}
    twins |= {"7,71": "p3_7_13", "7,82": "p3_7_13", "7,119": "p3_7_13"}
    assert len(twins) == 17
    apart = manifest.load(PORT)
    apart.brain.pairs |= {k: manifest.OWN for k in twins}
    again, _ = own.expand(apart, data)
    for key, first in twins.items():
        name = manifest.own_name(manifest.pair_of(key))
        assert chain_of(again, name) == chain_of(again, first), key


def test_what_fought_the_brain_is_gone(zinogre):
    m = zinogre.m
    assert not [n for n, mv in m.moves.items() if mv.claim is not None]
    assert set(m.behaviour.blocks) == KEPT
    assert not {"flinches", "in_combat"} - set(
        behaviour.vars(m)
    ) or "flinches" not in behaviour.vars(m)
    plays = {p for blk in m.behaviour.blocks.values() for p in blk.play}
    assert plays == {
        "lunge_stop",
        "notice_howl",
        "flinch_head",
        "topple_left",
        "topple_right",
        "break_howl",
    }
    assert m.moves["topple_left_3"].after == "stamp" and "stamp" in m.moves


def test_zinogre_splices_into_em75(zinogre):
    s, built = splice_of(zinogre, zinogre.m.brain)
    assert not [f for f in s.findings if f.level == "error"], s.findings
    assert built.ok, built.findings
    assert built.budget.worst is not None and built.budget.worst.count <= min(LIMIT, 150)
    grown = len(built.image.data) - (built.lift.end - built.lift.base)
    assert 8000 < grown < 14000 and 50 <= len(s.routines) <= 65
    got = codes(s.findings)
    assert got["info P3_SPLIT"] == 10 and got["warning P3_FOLDED"] == 3
    assert got["info P3_HISTORY"] == 1 and got["warning P3_APPROX"] == 1
    assert not got["warning P3_PAIR_UNUSED"] and not got["warning P3_WHEN_IGNORED"]
    assert {k for k in got if k.startswith(("error", "warning"))} == {
        "warning P3_FOLDED",
        "warning P3_APPROX",
    }
    folded = " ".join(f.text for f in s.findings if f.code == "P3_FOLDED")
    assert "switch_global_540 taken as answer 2" in folded
    assert "switch_query 2 taken as answer 0" in folded and "switch_query 0" not in folded


def inner_of(arm: ir.Arm) -> ir.Block | None:
    """The random an arm opens with."""
    first = arm.body[0] if arm.body else None
    return first if isinstance(first, ir.Block) and first.op.name == "random" else None


def reached(outer: ir.Block) -> int:
    """How many leaf arms of a split random some draw of the 32 takes."""
    hit = set()
    for v in range(draw.DRAW):
        k = draw.take(draw.weights(outer) or [], v)
        inner = inner_of(outer.arms[k])
        hit.add((k, None if inner is None else draw.take(draw.weights(inner) or [], v)))
    return len(hit)


def test_zinogre_splits_reach_every_donor_arm(zinogre):
    s, built = splice_of(zinogre, zinogre.m.brain)
    nodes = [n for i in s.edits.inserts.values() for n in edit.everything(i.nodes)]
    nodes += [n for r in s.edits.routines.values() for n in edit.everything(r)]
    outers = [
        n
        for n in nodes
        if isinstance(n, ir.Block) and n.op.name == "random" and any(map(inner_of, n.arms))
    ]
    live = sorted(int(f.text.split()[2]) for f in s.findings if f.code == "P3_SPLIT")
    assert len(live) == len(outers) == 10
    assert sorted(reached(o) for o in outers) == live
    got = codes(built.findings)
    assert built.ok and got["warning CORRELATED_DRAW"] == 15 and not got["warning ARM_UNREACHABLE"]


def queries(built: build.Built) -> Counter[int]:
    return Counter(
        n.args["arg"]
        for r in built.program.routines()
        for n in ir.walk(r.body)
        if isinstance(n, ir.Block) and n.op.name == "switch_query"
    )


def test_zinogre_keeps_the_charge_queries(zinogre):
    _, built = splice_of(zinogre, zinogre.m.brain)
    got = queries(built)
    assert set(got) == {0, 6} and got[0] > 100 and got[6] > 70


def test_zinogre_reads_tired_from_gauge_a(zinogre):
    _, built = splice_of(zinogre, zinogre.m.brain)
    tests = [
        n for r in built.program.routines() for n in ir.walk(r.body) if isinstance(n, ir.Block)
    ]
    assert any(n.op.name == "if_gauge_a_low" for n in tests)
    assert not any(n.op.name in ("if_tired", "if_stamina_low") for n in tests)


def test_zinogre_enters_its_own_moves(zinogre):
    s, built = splice_of(zinogre, zinogre.m.brain)
    own_enters = {
        n.args["sub"]
        for r in built.program.routines()
        for n in ir.walk(r.body)
        if isinstance(n, ir.Insn) and n.op.name == "enter" and n.args["main"] == OWN
    }
    assert own_enters == {zinogre.slots[n] for _, n in s.uses if isinstance(n, str)}
    assert {"p3_7_0", "p3_7_12", "p3_1_19", "charge_up", "dash", "break_howl"} <= set(s.slots)


def test_zinogre_without_the_charge_helper_is_never_charged(zinogre):
    plain = copy.deepcopy(zinogre.m.brain)
    plain.charge = False
    s, built = splice_of(zinogre, plain)
    assert built.ok and not queries(built)
    assert not [f for f in s.findings if f.level == "error"]
    folded = Counter(f.text.split(" taken")[0] for f in s.findings if f.code == "P3_FOLDED")
    assert folded == {
        "if_target_set": 1,
        "switch_query 0": 4,
        "switch_query 2": 1,
        "switch_query 6": 2,
    }
    assert len(s.routines) < 40  # the charged picker and what only it calls are gone


def expanded(brain_: p3.Brain, nodes: Sequence[p3.Node]) -> list[tuple[int, int]]:
    """The pairs a routine enters, with each call replaced by its routine's."""
    out: list[tuple[int, int]] = []
    for n in p3.walk(nodes):
        if isinstance(n, p3.Insn) and n.op.name == "enter":
            out.append((n.args["main"], n.args["sub"]))
        elif isinstance(n, p3.Insn) and n.op.name == "call":
            name = brain_.entry(p3ops.CALL, n.args["routine"])
            assert name is not None
            out += expanded(brain_, brain_.routines[name].body)
    return out


def test_zinogre_turns_then_moves(zinogre):
    b = zinogre.m.brain
    s, _ = splice_of(zinogre, b)
    turned = [manifest.pair_of(k) for k in b.pairs if len(b.targets(manifest.pair_of(k))) == 2]
    assert len(turned) == 38
    for pair in turned:
        move = b.targets(pair)[-1]
        assert s.uses[pair, TURN] > 0 and s.uses[pair, move] == s.uses[pair, TURN]
    for label, name in s.routines.items():
        got = Counter(
            (n.args["main"], n.args["sub"])
            for n in ir.walk(s.edits.routines[label])
            if isinstance(n, ir.Insn) and n.op.name == "enter"
        )
        want: Counter[tuple[int, int]] = Counter()
        for pair in expanded(zinogre.donor, zinogre.donor.routines[name].body):
            for t in b.targets(pair):
                want[(OWN, zinogre.slots[t]) if isinstance(t, str) else t] += 1
        assert got == want, label


def test_zinogre_brain_file_round_trip(zinogre, data, tmp_path):
    s, built = splice_of(zinogre, zinogre.m.brain)
    bf = brainfile.new(75)
    bf.put(s.edits)
    path = brainfile.save(bf, tmp_path / "em75.toml")
    again = build.compile(brainfile.load(path), data.fu, 75, lift=zinogre.host)
    assert again.id == built.id


def test_the_charge_table(zinogre):
    ch = zinogre.charge
    assert ch is not None
    assert ch.ups == {
        "p3_1_6": (10, 15, 15, 20),
        "p3_1_15": (25, 35, 40, 55),
        "charge_up": (40, 55, 65, 100),
        "p3_1_17": (55, 75, 100, 100),
        "p3_1_3": (50, 50, 50, 50),
        "p3_1_20": (50, 50, 50, 50),
    }
    assert ch.transform == "p3_1_4" and (ch.chip_gauge, ch.chip_durability) == (5, 5)
    assert (ch.cooldown_up, ch.cooldown_out) == (420, 900)
    assert ch.discharge == ((4, 16), (4, 29), (4, 30), (4, 31))
    assert zinogre.said == [], "every charge-up pair has its own move"


def test_the_fatigue_table(zinogre, data):
    fat, found = fatigue.table(zinogre.m, data)
    assert fat is not None and not [f for f in found if f.level == "error"]
    assert fat.pairs[4, 16] == 200 and {"p3_7_12", "p3_7_94", "p3_7_85"} <= set(fat.own)


@pytest.fixture(scope="module")
def module_text(zinogre, data, tmp_path_factory):
    where = tmp_path_factory.mktemp("zinogre")
    m = manifest.load(PORT)
    built = build_port(m, data)
    modules(m, built, where, data)
    return (where / moves.module_name(m)).read_text(), built


def test_the_moves_module_carries_fatigue_and_charge(module_text):
    text, _ = module_text
    lines = {line.split(" = ")[0].strip(): line for line in text.splitlines()}
    assert "fatigue" in lines and "calm = true" in lines["fatigue"]
    assert lines["charge"].strip() == (
        "charge = { ups = { charge_up = { 40, 55, 65, 100 }, p3_1_15 = { 25, 35, 40, 55 }, "
        "p3_1_17 = { 55, 75, 100, 100 }, p3_1_20 = { 50, 50, 50, 50 }, "
        "p3_1_3 = { 50, 50, 50, 50 }, p3_1_6 = { 10, 15, 15, 20 } }, "
        'transform = "p3_1_4", chip_gauge = 5, chip_durability = 5, cooldown_up = 420, '
        "cooldown_out = 900, discharge = { { 4, 16 }, { 4, 29 }, { 4, 30 }, { 4, 31 } } },"
    )


def test_the_own_moves_fit_the_framework(zinogre, module_text):
    """The slots and the turn keys the registry holds: the keys are what bounds the port."""
    _, built = module_text
    names = [n for n, mv in zinogre.m.moves.items() if mv.own]
    keys = sum(len(moves.curve(zinogre.m, n, built.layout) or "") // 4 for n in names)
    assert len(names) == 83 and keys == 4367
    assert len(names) <= moves.SLOTS and keys <= moves.POOL
    assert not moves.problems(zinogre.m, built.layout, moves.records(Data.find().fu, 75))


def test_the_cli_splices_the_port(data, capsys):
    args = ["--data", str(data.fu.root), "--p3rd-data", str(data.p3rd.root)]
    assert main(["splice", str(PORT), *args]) == 0
    out = capsys.readouterr().out
    assert "routines added" in out and "worst run" in out
    assert "  charge: charge_up 40/55/65/100, p3_1_15 25/35/40/55, " in out
    assert "  pair 7,23 -> 1,2  x" in out and "  pair 7,23 -> own p3_7_0 (slot " in out
