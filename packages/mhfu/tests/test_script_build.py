# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""From a brain file to a container: one build path, findings, and what an error blocks."""

import struct

import pytest
from mhfu import addresses as a
from mhfu.em import script
from mhfu.em.intel import SpeciesIntel
from mhfu.em.script import asm, brainfile, build, edit, ir, lift, parse, stick
from mhfu.em.script.build import Built
from mhfu.em.script.editset import Anchor, EditSet, Insert, set_args
from mhfu.em.script.inject import Head
from mhfu.files import EM_SPECIES

ROW = 0x2A
N = int(a.AI_OVERLAY) + 0x1000  # the synthetic native region


def u32s(*words):
    return struct.pack(f"<{len(words)}I", *words)


NATIVE = (
    bytes.fromhex("0500010005000200ff00")  # r0: two enters and an end
    + bytes(6)
    + u32s(N, 0)  # base_l: r0 and an empty slot
    + u32s(N + 0x10, N + 0x10)  # h0 and h1
)


def program():
    def read(pc):
        return NATIVE[pc - N] if 0 <= pc - N < len(NATIVE) else script.SENTINEL

    body, end = lift.routine(read, N, ROW)
    items = [
        ir.Routine("r0", body, NATIVE[end - N : 0x10], N),
        ir.Table("base_l", ["r0", None], N + 0x10),
        ir.Header("h0", ["base_l"], tuple(range(5)), N + 0x18),
        ir.Header("h1", ["base_l"], tuple(range(5, script.VARIANTS)), N + 0x1C),
    ]
    return ir.Program(ROW, N, items, N + len(NATIVE))


def after_first(p, *nodes, key="u1"):
    """An edit set that puts `nodes` after r0's first instruction."""
    es = EditSet.of(p)
    es.inserts[key] = Insert(Anchor("after", p.routines()[0].body[0].id), list(nodes))
    return es


def codes(built, level=None):
    return [f.code for f in built.findings if level in (None, f.level)]


# ---- a synthetic row


def test_no_edits_build_the_native_container():
    p = program()
    built = Built.of(p, EditSet.of(p))
    assert built.ok and built.findings == [] and built.program is p
    assert built.container == stick.build(p, asm.assemble(p))
    assert built.image is not None and built.image.data == asm.assemble(p).data
    assert built.id == Head.parse(built.container).id


def test_an_edit_builds_a_container_with_its_own_id():
    p = program()
    built = Built.of(p, after_first(p, edit.insn("enter", 3, 0, 0)))
    assert built.ok and codes(built) == []
    assert built.applied.touched == ("r0",)
    assert [n.id for n in built.program.nodes()][1] == "u1.0"
    assert built.container is not None and built.id == Head.parse(built.container).id
    assert built.container != Built.of(p, EditSet.of(p)).container
    assert built.lift is p and len(built.edits.inserts) == 1


def test_the_same_edits_build_the_same_bytes():
    p = program()
    one = Built.of(p, after_first(p, parse.nodes("enter 3 0 0\n")[0]))
    two = Built.of(p, after_first(p, edit.insn("enter", 3, 0, 0)))
    assert one.container == two.container and one.id == two.id
    other = Built.of(p, after_first(p, edit.insn("enter", 3, 1, 0)))
    assert other.id != one.id


def test_an_error_leaves_no_container():
    p = program()
    built = Built.of(p, after_first(p, edit.insn("enter", 3, 0, 1)))
    assert not built.ok and codes(built, "error") == ["GATE_NONZERO"]
    assert built.container is None and built.id is None
    assert built.image is not None  # laid out, so a viewer can still follow it


def test_a_build_that_is_not_the_lifts_is_refused():
    p = program()
    es = after_first(p, edit.insn("enter", 3, 0, 0))
    es.build = "ab" * 20
    built = Built.of(p, es)
    assert codes(built, "error") == ["DOC_BUILD"] and built.container is None
    assert built.program is p
    other = EditSet(7, "")
    other.names["r0"] = "Top"
    assert codes(Built.of(p, other), "error") == ["DOC_BUILD"]


def test_a_label_nothing_names_stops_at_apply_and_assemble():
    p = program()
    built = Built.of(p, after_first(p, edit.insn("call", ir.Sym("nowhere"))))
    assert codes(built, "error") == ["DOC_ANCHOR", "ASSEMBLE"]
    assert built.image is None and built.container is None and built.id is None


def test_a_row_the_checks_find_nothing_in_has_no_check_cost():
    p = program()
    es = EditSet.of(p)
    es.names["r0"] = "Top"  # a name changes no bytes
    built = Built.of(p, es)
    assert built.program is p and built.findings == []


def test_the_budget_is_made_when_asked_for():
    p = program()
    built = Built.of(p, after_first(p, edit.insn("enter", 3, 0, 0)))
    assert "budget" not in vars(built)
    worst = built.budget.worst
    assert worst is not None and 0 < worst.count < script.budget.LIMIT
    assert built.budget is built.budget


# ---- pairs from species intel


def intel(*pairs, static=True, census=False):
    """A species document with pairs `(main, sub, entered)`; entered None is unmeasured."""
    return SpeciesIntel(
        {
            "host_species": 75,
            "static": {"present": static},
            "census": {"present": census},
            "pairs": [
                {"main": m, "sub": s, "measured": None if n is None else {"entered": n}}
                for m, s, n in pairs
            ],
        }
    )


def pair_findings(si, *pairs):
    p = program()
    built = Built.of(p, after_first(p, *(edit.insn("enter", m, s, 0) for m, s in pairs)), si)
    assert built.ok
    return [(f.code, f.level, f.text) for f in built.findings]


def test_a_pair_the_census_never_saw_warns():
    got = pair_findings(intel((3, 1, 0), census=True), (3, 1))
    assert [(c, level) for c, level, _ in got] == [("PAIR_UNKNOWN", "warning")]
    assert "enter (3, 1)" in got[0][2] and "never enter" in got[0][2]


def test_a_pair_nothing_names_warns():
    got = pair_findings(intel((3, 1, 5)), (3, 9))
    assert [c for c, _, _ in got] == ["PAIR_UNKNOWN"] and "neither the overlay" in got[0][2]


def test_a_known_or_unmeasured_pair_says_nothing():
    assert pair_findings(intel((3, 1, 5), (3, 2, None)), (3, 1), (3, 2)) == []


def test_without_intel_pairs_say_nothing():
    assert pair_findings(None, (3, 9)) == []
    assert pair_findings(intel((3, 1, 0), static=False), (3, 1), (3, 9)) == []


def test_only_new_and_edited_enters_are_looked_at():
    p = program()
    si = intel((0, 1, 0), (0, 2, 0), static=True)
    quiet = Built.of(p, after_first(p, edit.insn("enter", 3, 1, 0)), intel((3, 1, 5)))
    assert codes(quiet) == []  # r0's own enters are the game's, whatever the intel says
    es = EditSet.of(p)
    first = p.routines()[0].body[0].id
    set_args(es, p, first, sub=3)
    built = Built.of(p, es, si)
    assert [(f.code, f.node) for f in built.findings] == [("PAIR_UNKNOWN", first)]


# ---- compile


def test_compile_refuses_a_lift_of_another_row():
    p = program()
    with pytest.raises(ValueError, match="the lift is row 42's, not row 7's"):
        build.compile(brainfile.new(75), None, 7, lift=p)


def test_compile_refuses_another_species_intel():
    with pytest.raises(ValueError, match="the intel is em75's, the file em01's"):
        build.compile(brainfile.new(1), None, 1, lift=program(), intel=intel())


def test_compile_needs_the_game_or_a_lift():
    with pytest.raises(ValueError, match="name the game, or give the row's lift"):
        build.compile(brainfile.new(75), None, 75)


def test_compile_takes_the_files_edits_by_row():
    p = program()
    es = after_first(p, edit.insn("enter", 3, 0, 0))
    bf = brainfile.new(75)
    bf.put(es)
    built = build.compile(bf, None, ROW, lift=p)
    assert built.edits is es and built.ok and built.id != Built.of(p, EditSet.of(p)).id
    assert build.compile(brainfile.new(75), None, ROW, lift=p).edits.is_empty()


# ---- the game


@pytest.fixture(scope="module")
def lifts(game):
    """A function: the lifts of an overlay's rows, lifted once."""
    cache = {}

    def of(em):
        if em not in cache:
            cache[em] = script.lift.lift_all(script.Scripts(game, em))
        return cache[em]

    return of


@pytest.mark.parametrize("em", EM_SPECIES)
def test_an_empty_file_builds_the_games_own_bytes(game, lifts, em):
    bf = brainfile.new(em)
    region = script.Scripts(game, em).ovl
    for row, lf in lifts(em).items():
        built = build.compile(bf, game, row, lift=lf)
        want = stick.build(lf, asm.assemble(lf))
        assert built.ok and built.findings == [], row
        assert built.image.data == region.read(lf.base, lf.end - lf.base), row
        assert built.container == want and built.id == Head.parse(want).id, row


def test_the_rows_of_all_overlays_are_42(lifts):
    assert sum(len(lifts(em)) for em in EM_SPECIES) == 42


def first_enter(lf):
    return next(
        n for n in ir.walk(lf.routines()[0].body) if isinstance(n, ir.Insn) and n.op.name == "enter"
    )


def edited_75(lf):
    """em75's Tigrex: its first enter changed, two weights swapped, an if_enraged put in."""
    es = EditSet.of(lf)
    body = lf.routines()[0].body
    set_args(es, lf, first_enter(lf).id, sub=2)
    pick = next(n for n in ir.walk(body) if isinstance(n, ir.Block) and n.op.name == "random")
    set_args(es, lf, pick.arms[0].id, weight=pick.arms[3].args["weight"])
    set_args(es, lf, pick.arms[3].id, weight=pick.arms[0].args["weight"])
    gate = next(n for n in body if isinstance(n, ir.Block) and n.op.name == "if_counter")
    es.inserts["u1"] = Insert(Anchor("before", gate.id), parse.nodes("if_enraged\n  enter 3 0 0\n"))
    return es


def test_an_edited_tigrex_builds_and_checks_clean(game, lifts):
    lf = lifts(75)[75]
    bf = brainfile.new(75)
    bf.put(edited_75(lf))
    built = build.compile(bf, game, 75, lift=lf)
    assert built.ok and codes(built, "error") == [] and codes(built, "warning") == []
    assert built.id is not None and built.id != build.compile(brainfile.new(75), game, 75).id
    assert {"u1.0", "u1.1"} <= {n.id for n in built.program.nodes()}
    assert built.applied.touched == (lf.routines()[0].name,)
    worst = built.budget.worst
    assert worst is not None and worst.count <= script.budget.LIMIT


def test_weights_that_break_the_total_leave_no_container(game, lifts):
    lf = lifts(75)[75]
    es = EditSet.of(lf)
    pick = next(
        n
        for n in ir.walk(lf.routines()[0].body)
        if isinstance(n, ir.Block) and n.op.name == "random"
    )
    set_args(es, lf, pick.arms[0].id, weight=pick.arms[0].args["weight"] + 1)
    bf = brainfile.new(75)
    bf.put(es)
    built = build.compile(bf, game, 75, lift=lf)
    assert codes(built, "error") == ["RANDOM_WEIGHTS"]
    assert built.container is None and built.id is None and not built.ok


def test_a_stale_build_is_refused(game, lifts):
    lf = lifts(75)[75]
    es = edited_75(lf)
    es.build = "00" * 20
    bf = brainfile.new(75)
    bf.put(es)
    built = build.compile(bf, game, 75, lift=lf)
    assert codes(built, "error") == ["DOC_BUILD"] and built.container is None


def test_edits_reach_the_rows_that_share_a_region(game, lifts):
    lf76, lf88 = lifts(75)[76], lifts(75)[88]
    es = EditSet.of(lf76)
    es.also = [88]
    first = first_enter(lf76)
    set_args(es, lf76, first.id, sub=first.args["sub"] + 1)
    bf = brainfile.new(75)
    bf.put(es)
    one = build.compile(bf, game, 76, lift=lf76)
    two = build.compile(bf, game, 88, lift=lf88)
    assert one.ok and two.ok and (one.row, two.row) == (76, 88)
    assert one.id == two.id != build.compile(brainfile.new(75), game, 76).id
    assert Head.parse(two.container).row == 88
    assert build.compile(bf, game, 81).edits.is_empty()


def test_compile_lifts_when_it_is_not_given_one(game, lifts):
    lf = lifts(75)[75]
    bf = brainfile.new(75)
    bf.put(edited_75(lf))
    assert build.compile(bf, game, 75).container == build.compile(bf, game, 75, lift=lf).container
