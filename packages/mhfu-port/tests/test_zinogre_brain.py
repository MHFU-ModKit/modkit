# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Zinogre's MHP3rd script spliced into em75, from a scratch [brain] on ports/zinogre.toml (the
port's own manifest is not touched): every own pair is `"own"`, the attacks the donor turns before
(1, 2) are a list. Needs both games' files."""

import copy
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
from mhfu import addresses
from mhfu.em.script import brainfile, build, ir
from mhfu.em.script import vars as script_vars
from mhfu.em.script.budget import LIMIT
from mhfu_port import manifest, moves
from mhfu_port.brain import p3, p3ops, splice
from mhfu_port.data import Data

PORT = Path(__file__).parents[3] / "ports" / "zinogre.toml"
OWN = addresses.OWN_MOVE_MAIN
TURN = (1, 2)
HOST = {
    "0,255": (0, 1),
    "1,1": (1, 2),
    "2,5": (1, 2),
    "2,6": (1, 2),
    "2,12": (1, 0),
    "2,13": (1, 0),
    "2,14": (1, 0),
    "2,16": (1, 0),
    "2,17": (1, 0),
    "2,21": (1, 0),
    "2,23": (1, 0),
    "2,24": (1, 0),
    "2,25": (1, 0),
    "2,26": (1, 2),
}
"""The pairs that play a host pair."""
TURNED = (
    "7,23 7,24 7,27 7,28 7,29 7,30 7,38 7,39 7,43 7,44 7,45 7,46 7,55 7,56 7,57 7,58 7,59 7,60 "
    "7,72 7,73 7,74 7,75 7,83 7,84 7,92 7,93 7,95 7,96 7,97 7,98 7,101 7,102 7,103 7,104 "
    "7,110 7,111 7,120 7,121"
).split()
"""The pairs that turn on the host's (1, 2) before their own move."""


def pair_of(key: str) -> tuple[int, int]:
    main, _, sub = key.partition(",")
    return int(main), int(sub)


def brain(donor: p3.Brain, **kw: object) -> manifest.Brain:
    """Every pair the entry reaches: a host pair, a turn then an own move, or an own move."""
    scan = splice.Translator(donor, manifest.Brain(40, "base_3", "base_3"))
    scan.scan("base_3", "base_3")
    pairs: dict[str, manifest.Plays] = {}
    for m, s in sorted(scan.entered):
        key = f"{m},{s}"
        pairs[key] = HOST[key] if key in HOST else [TURN, manifest.OWN] if key in TURNED else "own"
    when = {"if_target_set": True, "if_target_state": True}
    return manifest.Brain(
        40,
        "base_3",
        "base_3",
        pairs=pairs,
        when=when,
        fatigue=True,
        **kw,  # type: ignore[arg-type]
    )


@pytest.fixture(scope="module")
def zinogre(data: Data) -> SimpleNamespace:
    """The donor, the host row, and `m`, whose `[brain]` maps the 98 pairs the splice enters (the
    roaming code `if_target_set` folds away enters nine more)."""
    donor = p3.lift(data.p3rd, 40, 0)
    host = build.lift_row(data.fu, 75, 75)
    overlay = data.fu.em(75)
    survey = script_vars.survey(host, overlay)
    m = manifest.load(PORT)
    b = m.brain = brain(donor, charge=True)
    names = {t: k for k, t in enumerate(owned(b))}
    used = splice.splice(donor, host, b, survey, names, overlay).uses
    b.pairs = {k: v for k, v in b.pairs.items() if pair_of(k) in {p for p, _ in used}}
    return SimpleNamespace(donor=donor, host=host, overlay=overlay, m=m, survey=survey)


def splice_of(z: SimpleNamespace, b: manifest.Brain, slots: dict[str, int]):
    s = splice.splice(z.donor, z.host, b, z.survey, slots, z.overlay)
    return s, build.Built.of(z.host, s.edits)


def owned(b: manifest.Brain) -> list[str]:
    return sorted({t for k in b.pairs for t in b.targets(pair_of(k)) if isinstance(t, str)})


def every_slot(b: manifest.Brain) -> dict[str, int]:
    return {name: k for k, name in enumerate(owned(b))}


def codes(found: Sequence[object]) -> Counter[str]:
    return Counter(f"{f.level} {f.code}" for f in found)  # type: ignore[attr-defined]


def test_the_scratch_brain_is_a_manifest(zinogre):
    b = zinogre.m.brain
    assert len(b.pairs) == 98 and len(owned(b)) == 84
    assert sum(isinstance(v, list) for v in b.pairs.values()) == len(TURNED) == 38
    assert b.targets((7, 23)) == [TURN, "p3_7_23"] and b.targets((2, 5)) == [(1, 2)]
    assert manifest.loads(manifest.dumps(zinogre.m)) == zinogre.m


def test_zinogre_splices_into_em75(zinogre):
    b = zinogre.m.brain
    s, built = splice_of(zinogre, b, every_slot(b))
    assert not [f for f in s.findings if f.level == "error"], s.findings
    assert built.ok, built.findings
    assert built.budget.worst is not None and built.budget.worst.count <= min(LIMIT, 150)
    grown = len(built.image.data) - (built.lift.end - built.lift.base)
    assert 8000 < grown < 14000 and 50 <= len(s.routines) <= 65
    got = codes(s.findings)
    assert got["info P3_SPLIT"] == 10 and got["warning P3_FOLDED"] == 3
    assert got["info P3_HISTORY"] == 1 and got["warning P3_APPROX"] == 1
    assert {k for k in got if k.startswith(("error", "warning"))} == {
        "warning P3_FOLDED",
        "warning P3_APPROX",
    }
    folded = " ".join(f.text for f in s.findings if f.code == "P3_FOLDED")
    assert "switch_global_540 taken as answer 2" in folded
    assert "switch_query 2 taken as answer 0" in folded and "switch_query 0" not in folded


def queries(built: build.Built) -> Counter[int]:
    return Counter(
        n.args["arg"]
        for r in built.program.routines()
        for n in ir.walk(r.body)
        if isinstance(n, ir.Block) and n.op.name == "switch_query"
    )


def test_zinogre_keeps_the_charge_queries(zinogre):
    b = zinogre.m.brain
    _, built = splice_of(zinogre, b, every_slot(b))
    got = queries(built)
    assert set(got) == {0, 6} and got[0] > 100 and got[6] > 70


def test_zinogre_without_the_charge_helper_is_never_charged(zinogre):
    plain = copy.deepcopy(zinogre.m.brain)
    plain.charge = False
    s, built = splice_of(zinogre, plain, every_slot(plain))
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


def test_zinogre_turns_then_moves(zinogre):
    b = zinogre.m.brain
    slots = every_slot(b)
    s, _ = splice_of(zinogre, b, slots)
    for key in TURNED:
        pair = pair_of(key)
        own = b.targets(pair)[-1]
        assert s.uses[pair, TURN] > 0 and s.uses[pair, own] == s.uses[pair, TURN]
    for label, name in s.routines.items():
        got = Counter(
            (n.args["main"], n.args["sub"])
            for n in ir.walk(s.edits.routines[label])
            if isinstance(n, ir.Insn) and n.op.name == "enter"
        )
        want: Counter[tuple[int, int]] = Counter()
        for pair in expanded(zinogre.donor, zinogre.donor.routines[name].body):
            for t in b.targets(pair):
                want[(OWN, slots[t]) if isinstance(t, str) else t] += 1
        assert got == want, label


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


def test_zinogre_with_the_registry_as_it_is(zinogre):
    """The slots `moves.slots` gives while the registry is smaller than the donor's own moves:
    the moves past it are the only error."""
    b = zinogre.m.brain
    m = copy.deepcopy(zinogre.m)
    m.moves |= {name: manifest.Move(anim=1) for name in owned(b)}
    s, built = splice_of(zinogre, b, moves.slots(m))
    errors = {f.code for f in s.findings if f.level == "error"}
    if moves.SLOTS < len(owned(b)):
        assert errors == {"P3_OWN_MISSING"}
    else:
        assert not errors and built.ok


def test_zinogre_brain_file_round_trip(zinogre, data, tmp_path):
    b = zinogre.m.brain
    s, built = splice_of(zinogre, b, every_slot(b))
    bf = brainfile.new(75)
    bf.put(s.edits)
    path = brainfile.save(bf, tmp_path / "em75.toml")
    again = build.compile(brainfile.load(path), data.fu, 75, lift=zinogre.host)
    assert again.id == built.id
