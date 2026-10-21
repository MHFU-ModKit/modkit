# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

import pytest
from mhfu.files import Extracted
from mhfu_port.brain import p3, p3ops
from mhfu_port.brain.p3ops import BY_NAME, OPS, Kind
from mhfu_port.cli import main

ROUTINES = {
    "flat": "00 03 02 2f 05 ff",
    "if": "50 00 00 01 02 50 02 00 01 03 50 ff ff",
    "switch": "07 00 07 01 03 00 01 01 07 02 39 07 ff ff",
    "same-op": "07 00 07 01 01 07 00 07 01 02 39 07 ff 07 ff ff",
    "random": "02 00 02 01 32 00 00 01 02 01 32 39 02 ff ff",
    "loop": "31 00 01 03 00 04 05 31 ff ff",
    "break": "31 00 01 03 50 00 31 03 50 ff 31 ff ff",
    "bracket": "05 00 00 01 01 05 ff ff",
    "alias": "00 01 01 10",
    "end-in-block": "50 00 0d ff 50 ff ff",
    "calls": "14 00 20 00 06 01 02 0d ff",
}


def routine(name: str) -> p3.Routine:
    return p3.decode(bytes.fromhex(ROUTINES[name]), 0x100, name)


def first(r: p3.Routine, kind: type) -> p3.Located:
    return next(n for n in p3.walk(r.body) if isinstance(n, kind))


def test_table_covers_the_dispatch():
    assert sorted(OPS) == [*range(p3ops.NUM_OPS), 0xFF]
    assert len({o.name for o in OPS.values()}) == len(OPS)
    assert all(OPS[c].flow is p3ops.Flow.END for c in p3ops.ENDS)


def test_lengths():
    assert p3ops.length(0x00) == 3
    assert [p3ops.length(0x07, s) for s in (0, 1, 2, 3, 0xFF)] == [2, 3, 2, 2, 2]
    assert [p3ops.length(0x31, s) for s in (0, 1, 3, 0xFF)] == [4, 3, 2, 2]
    assert p3ops.length(0xFF) == p3ops.length(0x10) == 1
    with pytest.raises(ValueError, match="0x70"):
        p3ops.length(0x70)


def test_length_and_entry_follow_the_layout():
    for op in OPS.values():
        assert op.length(0) == (2 if op.block else 1) + len(op.operands)
        if op.calls is not None:
            assert p3ops.LISTS[op.calls]
    assert BY_NAME["call"].entry({"routine": 7}) == (p3ops.CALL, 7)
    assert BY_NAME["goto_base"].entry({}) == (p3ops.BASE, 0)
    assert BY_NAME["enter"].entry({"main": 1, "sub": 2}) is None


@pytest.mark.parametrize("name", ROUTINES)
def test_round_trip(name):
    r = routine(name)
    assert not r.problems
    assert r.ends
    assert p3.encode(r) == bytes.fromhex(ROUTINES[name])


def test_flat_instructions():
    r = routine("flat")
    assert [type(n) for n in r.body] == [p3.Insn] * 3
    assert r.body[0].args == {"main": 3, "sub": 2}
    assert [n.pc for n in r.body] == [0x100, 0x103, 0x105]
    assert r.instructions == 3


def test_if_has_head_and_else():
    block = routine("if").body[0]
    assert isinstance(block, p3.Block) and block.op is BY_NAME["if_enraged"]
    assert [n.args for n in block.head] == [{"main": 1, "sub": 2}]
    [arm] = block.arms
    assert (arm.sub, arm.value) == (2, None)
    assert [n.args for n in arm.body] == [{"main": 1, "sub": 3}]
    assert block.close == 0x100 + 10


def test_switch_arms():
    block = routine("switch").body[0]
    assert block.head == []
    assert [(a.sub, a.value, len(a.body)) for a in block.arms] == [(1, 3, 1), (2, None, 1)]
    assert block.op.kind is Kind.SWITCH


def test_nested_same_op_marker_goes_to_the_inner_block():
    outer = routine("same-op").body[0]
    [case] = outer.arms
    inner = case.body[0]
    assert isinstance(inner, p3.Block)
    assert [a.value for a in inner.arms] == [2]
    assert [a.value for a in outer.arms] == [1]


def test_random_weights():
    block = routine("random").body[0]
    assert [a.value for a in block.arms] == [50, 50]
    assert block.arms[1].body[-1].op is BY_NAME["nop"]


def test_break_inside_an_if_is_a_leave():
    r = routine("break")
    loop = r.body[0]
    assert loop.arms == []
    leave = first(r, p3.Leave)
    assert (leave.op, leave.sub, leave.value) == (loop.op, 3, None)
    assert loop.head[0].head == [leave]


def test_end_inside_a_block_does_not_end_the_routine():
    r = routine("end-in-block")
    assert len(r.body) == 2
    assert r.body[0].head[1].op.code == 0xFF


def test_alias_end_is_kept():
    r = routine("alias")
    assert r.body[-1].op is OPS[0x10]
    assert r.ends


def test_cut_routine_keeps_what_fits():
    r = p3.decode(bytes.fromhex("00 22 ff 00"), 0x40)
    assert not r.ends
    assert [p.kind for p in r.problems] == [p3.CUT]
    assert p3.encode(r) == bytes.fromhex("00 22 ff")
    assert "0x00000043" in str(r.problems[0])


def test_unknown_opcode():
    r = p3.decode(bytes.fromhex("00 01 01 70 ff"))
    assert [p.kind for p in r.problems] == [p3.UNKNOWN]
    assert len(r.body) == 1


@pytest.mark.parametrize(
    "text",
    [
        "07 01 03 ff",  # a marker with no block
        "31 00 01 03 50 00 31 ff 50 ff ff",  # a close inside another block
        "50 00 50 ff 50 ff ff",  # a close with nothing open
    ],
)
def test_nesting_problems(text):
    r = p3.decode(bytes.fromhex(text))
    assert [p.kind for p in r.problems] == [p3.NESTING]


def test_encode_checks_operands():
    enter = BY_NAME["enter"]
    with pytest.raises(ValueError, match="main sub"):
        p3.encode_nodes([p3.Insn(enter, {"main": 1})])
    with pytest.raises(ValueError, match="needs value"):
        p3.encode_nodes([p3.Block(BY_NAME["switch_mode"], {}, [], [p3.Arm(1, None, [])])])
    with pytest.raises(ValueError, match="takes no value"):
        p3.encode_nodes([p3.Block(BY_NAME["if_enraged"], {}, [], [p3.Arm(2, 5, [])])])


def test_new_nodes_encode():
    enter = p3.Insn(BY_NAME["enter"], {"main": 1, "sub": 2})
    block = p3.Block(BY_NAME["switch_mode"], {}, [], [p3.Arm(1, 4, [enter]), p3.Arm(2, None, [])])
    assert p3.encode_nodes([block, p3.Insn(OPS[0xFF], {})]).hex(" ") == (
        "0c 00 0c 01 04 00 01 02 0c 02 0c ff ff"
    )
    assert p3.count([block]) == 5


def brain_of(*names: str) -> p3.Brain:
    routines = {n: routine(n) for n in names}
    lists = [p3.List(0, 0x300, [*routines]), p3.List(1, 0x400, ["call_0"]), *[None] * 11]
    return p3.Brain(1, 0, "em000m0", 0x200, lists, routines)


def test_render_indents_blocks_and_levels_markers():
    assert p3.render(brain_of("break"), "break") == [
        "; break 0x00000100",
        "repeat mode=1 count=3",
        "  if_enraged",
        "    repeat.break",
        "  if_enraged.end",
        "repeat.end",
        "end",
    ]
    assert p3.render(brain_of("switch"), "switch")[1:] == [
        "switch_area",
        "switch_area.case case=3",
        "  enter main=1 sub=1",
        "switch_area.default",
        "  nop",
        "switch_area.end",
        "end",
    ]


def test_render_whole_brain_and_raw():
    lines = p3.render(brain_of("flat", "if"))
    assert lines[:5] == [
        "; id 1 slot 0 em000m0 0x00000200",
        "; list 0 base 0x00000300 (2)",
        "; list 1 call 0x00000400 (1)",
        "",
        "; flat 0x00000100",
    ]
    assert lines.count("") == 1 + 1
    raw = p3.render(brain_of("flat"), "flat", raw=True)
    assert raw[1] == "00000100  00 03 02      enter main=3 sub=2"


def test_render_names_what_a_call_enters():
    lines = p3.render(brain_of("calls"), "calls")
    assert [line.split("  ; ")[1] for line in lines if "  ; " in line] == [
        "call_0",
        "calls",
        "?",
        "calls",
    ]


def test_render_has_a_line_per_instruction():
    for name in ROUTINES:
        lines = p3.render(brain_of(name), name)
        assert len(lines) - 1 == routine(name).instructions


@pytest.fixture(scope="module")
def extracted(mhp3rd_data: Path) -> Extracted:
    return Extracted.find(mhp3rd_data)


@pytest.fixture(scope="module")
def species(extracted: Extracted) -> p3.Species:
    return p3.Species.read(extracted)


@pytest.fixture(scope="module")
def report(extracted: Extracted) -> p3.Report:
    return p3.check(extracted)


def test_lengths_equal_the_games_table(extracted):
    table = extracted.overlay(p3.GAME_TASK).read(p3ops.LENGTH_TABLE, p3ops.NUM_OPS)
    for code, entry in enumerate(table):
        if entry < 0x80:
            assert [p3ops.length(code, s) for s in (0, 1, 2, 0xFF)] == [1 + entry] * 4
        else:
            opener = 1 + ((entry + 1) & 0xF)
            assert [p3ops.length(code, s) for s in (0, 1, 2, 3, 0xFF)] == [opener, 3, 2, 2, 2]


def test_species_table(species):
    assert len(species.slots) == len(p3.IDS)
    assert [s.file for s in species.slots[23]][0] == [s.file for s in species.slots[58]][0]
    assert len(species.slots[1]) == 2
    assert len(species.slots[10]) == 4
    assert species.slots[65][0].file == p3.GAME_TASK


def sizes(brain: p3.Brain) -> list[int]:
    return [0 if f is None else sum(e is not None for e in f.entries) for f in brain.lists]


def test_brute_tigrex(extracted, species):
    brain = p3.lift(extracted, 23, 0, species)
    assert brain.overlay == "em058m0"
    assert sizes(brain) == [22, 69, 1, 1, 1, 2, 1, 1, 2, 1, 3, 1, 0]
    assert len(brain.routines) == 105
    assert brain.instructions == 2122
    assert brain.lists[1].entries[12] == "call_12"
    assert brain.routines["call_12"].ends


def test_zinogre(extracted, species):
    brain = p3.lift(extracted, 40, 0, species)
    assert len(brain.routines) == 122
    assert brain.instructions == 3313


def test_tigrex_shares_brute_tigrex_lists(extracted, species):
    brute, tigrex = (p3.lift(extracted, i, 0, species) for i in (23, 58))
    assert brute.overlay == tigrex.overlay
    assert brute.descriptor != tigrex.descriptor
    assert [f and f.va for f in brute.lists] == [f and f.va for f in tigrex.lists]


def test_slot_one_is_the_same_code_elsewhere(extracted, species):
    m0, m1 = (p3.lift(extracted, 23, s, species) for s in (0, 1))
    assert m1.overlay == "em058m1"
    assert list(m0.routines) == list(m1.routines)
    assert [p3.encode(r) for r in m0.routines.values()] == [
        p3.encode(r) for r in m1.routines.values()
    ]


def test_every_routine_encodes_to_its_bytes(extracted, species):
    brain = p3.lift(extracted, 23, 0, species)
    overlay = extracted.overlay(species.slots[23][0].file)
    for r in brain.routines.values():
        data = p3.encode(r)
        assert overlay.read(r.va, len(data)) == data


def test_lift_errors(extracted, species):
    for ident, slot in ((65, 0), (66, 0), (99, 0), (23, 2)):
        with pytest.raises(ValueError):
            p3.lift(extracted, ident, slot, species)


def test_em003_runs_into_the_next_item(extracted, species):
    for ident in p3.KNOWN:
        r = p3.lift(extracted, ident, 0, species).routines[p3.KNOWN[ident]]
        assert not r.ends
        assert [p.kind for p in r.problems] == [p3.CUT]


def test_check_passes(report):
    assert report.ok
    assert report.skipped == [65, 66, 67]
    assert report.problems == [] and report.missing == []
    assert len(report.quirks) == 4


def test_check_numbers(report):
    t = report.total
    assert (t.unknown, t.uncovered, t.mismatched) == (0, 0, 0)
    assert report.slots[0] == p3.Totals(2852, 90450)
    assert report.slots[1] == p3.Totals(2852, 90450)
    assert report.slots[2] == report.slots[3] == p3.Totals(547, 11405)
    assert (t.routines, t.instructions) == (6798, 203710)


def test_check_one_id(extracted):
    r = p3.check(extracted, [3])
    assert r.ok and len(r.quirks) == 2
    assert sorted(r.slots) == [0, 1]


def test_render(extracted, species):
    brain = p3.lift(extracted, 23, 0, species)
    lines = p3.render(brain)
    assert lines[0].startswith("; id 23 slot 0 em058m0 ")
    assert "; base_0 " in " ".join(lines)
    one = p3.render(brain, "death_0", raw=True)
    assert one[0].startswith("; death_0 ")
    assert one[1].split()[1] == "00" and "enter main=" in one[1]
    with pytest.raises(ValueError, match="no routine"):
        p3.render(brain, "nope")


def test_render_names_the_routine_a_call_enters(extracted, species):
    brain = p3.lift(extracted, 23, 0, species)
    calls = [line.split("  ; ")[1] for line in p3.render(brain) if "  ; " in line]
    assert calls and set(calls) <= set(brain.routines)


def test_cli(extracted, capsys):
    games = ["--p3rd-data", str(extracted.root)]
    assert main(["brain", "23", "--routine", "death_0", *games]) == 0
    assert "enter main=" in capsys.readouterr().out
    assert main(["brain", "--check", *games]) == 0
    assert capsys.readouterr().out.rstrip().endswith("ok")
    with pytest.raises(SystemExit):
        main(["brain", "x", *games])
    assert main(["brain", *games]) == 1
    assert main(["brain", "65", *games]) == 1
