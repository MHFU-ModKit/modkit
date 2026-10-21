# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import json
import struct

import pytest
from mhfu import addresses as a
from mhfu.cli import main
from mhfu.em import script
from mhfu.em.script import brainfile, edit, ir
from mhfu.em.script import vars as sv
from mhfu.em.script.opcodes import BY_NAME
from mhfu.em.script.vars import Kind
from mhfu.files import EM_SPECIES
from mhfu.overlay import TEXT, Overlay

LOAD = 0x100000
SB, SH, SW, LBU = 0x28, 0x29, 0x2B, 0x24
OFFSET = {1: 0x4B6, 2: 0x52B, 3: 0x4B4, 4: 0x52E}


def program(*body: ir.Node) -> ir.Program:
    items: list[ir.Item] = [
        ir.Header("h0", ["base_l"], (0,)),
        ir.Table("base_l", ["base_0"]),
        ir.Routine("base_0", [*body, edit.insn("end", 0)]),
    ]
    return ir.Program(75, 0, items, 0, "ab" * 20)


def word(op: int, offset: int) -> int:
    """`op $zero, offset($s0)`."""
    return op << 26 | 16 << 21 | offset & 0xFFFF


def overlay(*words: int) -> Overlay:
    text = struct.pack(f"<{len(words)}I", *words)
    head = struct.pack("<4sIIIIIII", b"MWo3", 1, LOAD, len(text), 0, 0, 0, 0)
    return Overlay(head.ljust(TEXT, b"\0") + text, "em75.ovl")


def kinds(s: dict[int, sv.Var]) -> dict[int, Kind]:
    return {v: x.kind for v, x in s.items()}


# ---- the table


def test_bytes_are_the_entity_fields():
    assert dict(sv.BYTE) == OFFSET
    assert a.ENTITY.SCRIPT_VAR_2 == 0x52B and a.ENTITY.SCRIPT_VAR_3 == 0x4B4


def test_every_variable_is_a_byte_or_species_code():
    assert sorted({*sv.BYTE, *sv.CODE}) == list(range(1, script.catalogue.VARS + 1))


# ---- the script


def test_var_of_names_the_variable_a_node_sets_or_tests():
    pairs = [
        (edit.insn("set_var", 4, 1), 4),
        (edit.insn("reset_var", 2), 2),
        (edit.if_("if_var", 7, 1, then=[]), 7),
        (edit.if_("if_var3_zero", then=[]), 3),
        (edit.insn("set_var", 0, 1), None),
        (edit.insn("wait", 3), None),
    ]
    assert [sv.var_of(n.op, n.args) for n, _ in pairs] == [v for _, v in pairs]


def test_uses_count_the_nodes_that_name_a_variable():
    p = program(
        edit.insn("set_var", 4, 1),
        edit.if_("if_var", 4, 1, then=[edit.insn("reset_var", 4)], else_=[edit.insn("wait", 1)]),
        edit.if_("if_var3_zero", then=[edit.insn("set_var", 6, 2)]),
    )
    s = sv.survey(p)
    assert {v: x.uses for v, x in s.items() if x.uses} == {3: 1, 4: 3, 6: 1}


def test_a_script_with_no_variable_has_two_free_bytes():
    s = sv.survey(program(edit.insn("wait", 1)), overlay(0))
    assert kinds(s) == {
        1: Kind.ENGINE,
        2: Kind.FREE,
        3: Kind.ENGINE,
        4: Kind.FREE,
        **dict.fromkeys(range(5, 10), Kind.CODE),
    }
    assert sv.free(s) == [2, 4]


def test_a_variable_the_script_names_is_the_scripts():
    s = sv.survey(program(edit.insn("set_var", 4, 1)), overlay(0))
    assert s[4].kind is Kind.SCRIPT and sv.free(s) == [2]


def test_the_engine_owns_a_variable_whatever_the_script_does():
    s = sv.survey(program(edit.insn("set_var", 1, 1)), overlay(0))
    assert s[1].kind is Kind.ENGINE and s[1].uses == 1 and s[1].why


def test_clear_counters_is_a_reset_not_a_use():
    s = sv.survey(program(edit.insn("clear_counters"), edit.insn("clear_counters")), overlay(0))
    assert s[2].kind is Kind.FREE and s[2].uses == 0
    assert any("(2 in this script)" in line for line in s[2].why)
    assert not any("this script" in line for line in sv.survey(program(), overlay(0))[2].why)


def test_species_code_has_no_byte():
    s = sv.survey(program(edit.insn("set_var", 5, 1)))
    assert s[5].kind is Kind.CODE and s[5].uses == 1 and "vtable +0x94" in s[5].why[0]


def test_reasons_lead_with_what_the_script_does():
    s = sv.survey(program(edit.insn("set_var", 4, 1), edit.insn("set_var", 4, 2)), overlay(0))
    assert s[4].reasons == ("2 script nodes name it",)
    assert s[2].reasons[0] == "no script node names it" and s[2].reasons[1:] == s[2].why
    one = sv.survey(program(edit.insn("set_var", 1, 1)), overlay(0))
    assert one[1].reasons[0] == "1 script node names it"


def test_every_variable_is_in_the_survey():
    assert list(sv.survey(program())) == list(range(1, 10))


# ---- the species overlay


@pytest.mark.parametrize(
    ("words", "hit"),
    [
        ([word(SB, 0x52B)], True),
        ([word(SH, 0x52B)], True),
        ([word(SW, 0x52B)], True),
        ([word(SH, 0x52A)], False),
        ([word(SW, 0x528)], False),
        ([word(SB, 0x52C)], False),
        ([word(LBU, 0x52B)], False),
    ],
)
def test_a_store_at_the_byte_counts(words, hit):
    assert sv.scan(overlay(*words)).stores.get(2, 0) == hit


def test_the_scan_counts_each_variable():
    ovl = overlay(word(SB, 0x4B6), word(SB, 0x4B6), word(SB, 0x4B4), word(SB, 0x52E))
    assert sv.scan(ovl).stores == {1: 2, 3: 1, 4: 1}


def jal(target: int) -> int:
    return 3 << 26 | target >> 2 & 0x3FFFFFF


def test_the_scan_counts_calls_of_a_helper():
    ovl = overlay(
        jal(a.GAUGE_A_LATCH), jal(a.GAUGE_A_LATCH), jal(a.SET_SCRIPT_VAR_1), jal(a.MONSTER_STEP)
    )
    assert sv.scan(ovl).calls == {a.GAUGE_A_LATCH: 2, a.SET_SCRIPT_VAR_1: 1}


def test_a_species_store_makes_a_variable_the_species():
    s = sv.survey(program(), overlay(word(SB, 0x52B)))
    assert s[2].kind is Kind.SPECIES and "1 store in the species overlay" in s[2].why[-1]
    assert sv.free(s) == [4]


def test_a_species_that_calls_the_gauge_latch_owns_variable_2():
    s = sv.survey(program(), overlay(jal(a.GAUGE_A_LATCH), jal(a.GAUGE_A_LATCH)))
    assert s[2].kind is Kind.SPECIES and sv.free(s) == [4]
    assert s[2].why[-1].startswith("2 calls to GAUGE_A_LATCH in the species overlay")


def test_a_call_of_the_var_1_helper_leaves_it_the_engines():
    s = sv.survey(program(), overlay(jal(a.SET_SCRIPT_VAR_1)))
    assert s[1].kind is Kind.ENGINE and "1 call to SET_SCRIPT_VAR_1" in s[1].why[-1]
    assert s[2].kind is Kind.FREE


def test_the_species_outranks_the_script_and_the_engine_the_species():
    s = sv.survey(program(edit.insn("set_var", 4, 1)), overlay(word(SB, 0x52E), word(SB, 0x4B6)))
    assert s[4].kind is Kind.SPECIES and s[4].uses == 1
    assert s[1].kind is Kind.ENGINE and "1 store" in s[1].why[-1]


def test_without_an_overlay_the_species_is_not_known():
    s = sv.survey(program())
    assert s[2].kind is Kind.FREE and s[2].why[-1] == "the species overlay is not scanned"
    assert all("not scanned" not in line for line in s[1].why)


def test_the_scan_is_kept_per_overlay_content():
    first = sv.scan(overlay(word(SB, 0x52B)))
    assert sv.scan(overlay(word(SB, 0x52B))) is first
    assert first.stores == {2: 1} and sv.scan(overlay(word(SB, 0x52E))).stores == {4: 1}


# ---- with the game


def test_em75(game):
    sc = script.Scripts(game, 75)
    s = sv.survey(script.lift.lift(sc), sc.ovl)
    assert kinds(s) == {
        1: Kind.ENGINE,
        2: Kind.FREE,
        3: Kind.ENGINE,
        4: Kind.SCRIPT,
        **dict.fromkeys(range(5, 10), Kind.CODE),
    }
    assert sv.free(s) == [2]
    found = sv.scan(sc.ovl)
    assert found.stores.get(1, 0) > 0 and found.stores.get(3, 0) > 0
    assert 2 not in found.stores and 4 not in found.stores
    assert found.calls == {a.SET_SCRIPT_VAR_1: 2}
    assert s[4].uses > 0 and s[2].uses == 0


def test_em01_owns_variable_2(game):
    sc = script.Scripts(game, 1)
    s = sv.survey(script.lift.lift(sc), sc.ovl)
    assert s[2].kind is Kind.SPECIES and sv.free(s) == []
    assert sv.scan(sc.ovl).calls[a.GAUGE_A_LATCH] > 0


SPECIES_OWN_2 = (1, 15, 20, 54, 59, 82)
SCRIPT_NAMES_4 = (1, 15, 17, 20, 21, 40, 54, 59, 75, 82)


def test_variable_2_is_free_unless_the_species_calls_the_gauge_latch(game):
    for em in EM_SPECIES:
        sc = script.Scripts(game, em)
        s = sv.survey(script.lift.lift(sc), sc.ovl)
        assert (s[2].kind is Kind.SPECIES) == (em in SPECIES_OWN_2), em
        assert (s[4].kind is Kind.SCRIPT) == (em in SCRIPT_NAMES_4), em
        assert s[1].kind is s[3].kind is Kind.ENGINE, em


def test_cli_vars(game, capsys):
    assert main(["script", "75", "--vars", "--data", str(game.root)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[:3] for line in lines] == [
        ["var", str(v), k]
        for v, k in enumerate("engine free engine script".split() + ["code"] * 5, 1)
    ]
    assert "MONSTER_STEP" in lines[0] and "CLEAR_COUNTERS" in lines[1]
    assert main(["script", "75", "--vars", "--json", "--data", str(game.root)]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["kind"] for r in rows][:4] == ["engine", "free", "engine", "script"]
    assert all(set(r) == {"var", "kind", "uses", "why"} for r in rows)


def test_cli_vars_of_a_brain_file(game, tmp_path, capsys):
    doc = brainfile.save(brainfile.new(75), tmp_path / "em75.toml")
    argv = ["script", "75", "--doc", str(doc), "--vars", "--data", str(game.root)]
    assert main(argv) == 0
    assert capsys.readouterr().out.splitlines()[1].split()[:3] == ["var", "2", "free"]


def test_the_ops_with_a_var_operand():
    named = {"set_var", "reset_var", "if_var", "if_var3_zero"}
    seen = {
        name
        for name, op in BY_NAME.items()
        if any(i.fmt is script.catalogue.Fmt.VAR for i in script.catalogue.entry(op).operands)
    }
    assert seen | {"if_var3_zero"} == named
