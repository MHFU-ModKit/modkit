# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from collections.abc import Iterable, Sequence
from pathlib import Path

import pytest
from mhfu import addresses
from mhfu.em.script import brainfile, budget, build, edit, ir, opcodes, source
from mhfu.em.script import vars as script_vars
from mhfu.em.script.editset import Anchor
from mhfu_port import manifest
from mhfu_port.brain import p3, p3ops, splice
from mhfu_port.cli import main
from mhfu_port.data import Data

PAIRS = {"2,3": (1, 2), "2,4": (1, 3), "7,5": (1, 4)}
TWO = {k: PAIRS[k] for k in ("2,3", "2,4")}
END = "ff"


def op(name: str, *args: int) -> str:
    """A plain MHP3rd instruction as hex."""
    return " ".join(f"{b:02x}" for b in (p3ops.BY_NAME[name].code, *args))


def enter(main_: int, sub: int) -> str:
    return op("enter", main_, sub)


def case(value: int, body: str) -> tuple[int, int | None, str]:
    return 1, value, body


def other(body: str) -> tuple[int, int | None, str]:
    return 2, None, body


def blk(
    name: str, *args: int, head: str = "", arms: Iterable[tuple[int, int | None, str]] = ()
) -> str:
    """A block as hex: its opener and head, then each arm, then the closer."""
    code = f"{p3ops.BY_NAME[name].code:02x}"
    parts = [f"{code} 00", *(f"{a:02x}" for a in args), head]
    for sub, value, body in arms:
        parts += [f"{code} {sub:02x}" + ("" if value is None else f" {value:02x}"), body]
    return " ".join([*parts, f"{code} ff"])


def donor(**routines: str) -> p3.Brain:
    """A donor whose routines are `<list>_<index>=hex`, each given an end."""
    found: dict[str, p3.Routine] = {}
    slots: dict[str, list[str | None]] = {}
    for name, text in routines.items():
        kind, _, index = name.partition("_")
        entries = slots.setdefault(kind, [])
        entries += [None] * (int(index) + 1 - len(entries))
        entries[int(index)] = name
        found[name] = p3.decode(bytes.fromhex(f"{text} {END}"), 0x1000 + 0x100 * len(found), name)
    lists = [p3.List(k, 0, slots[n]) if n in slots else None for k, n in enumerate(p3ops.LISTS)]
    return p3.Brain(23, 0, "em", 0, lists, found)


def spec(**kw: object) -> manifest.Brain:
    return manifest.Brain(
        **{"donor": 23, "splice": "base_3", "entry": "base_0", "pairs": PAIRS} | kw
    )


def lines(nodes: Sequence[ir.Node]) -> list[str]:
    return [line.text for line in source.render_nodes(nodes)]


def translate(
    sp: manifest.Brain | None = None,
    survey: dict[int, script_vars.Var] | None = None,
    slots: dict[str, int] | None = None,
    **routines: str,
) -> tuple[list[str], splice.Translator]:
    t = splice.Translator(donor(**routines), sp or spec(), survey, slots)
    return lines(t.paste("base_0", "base_3")), t


def found(t: splice.Translator) -> list[tuple[str, str]]:
    return [(f.level, f.code) for f in t.findings]


def host(prologue: bool = True) -> ir.Program:
    """The shape of em75's: base_3 is a plain instruction, a test that calls, and an end."""
    test = edit.if_("if_enraged", then=[edit.insn("call", 1)], else_=[edit.insn("call", 2)])
    body = [edit.insn("target_remembered")] if prologue else []
    body += [test, edit.insn("end", 0)]
    items: list[ir.Item] = [
        ir.Header("h0", ["base_l", "call_l"], (0,)),
        ir.Table("base_l", ["base_0", None, None, "base_3"]),
        ir.Table("call_l", ["call_0", "call_0", "call_0"]),
        ir.Routine("base_0", [edit.insn("enter", 0, 1, 0), edit.insn("end", 0)]),
        ir.Routine("base_3", body),
        ir.Routine("call_0", [edit.insn("end", 1)]),
    ]
    program = ir.Program(75, 0, items, 0, "ab" * 20)
    for k, n in enumerate(n for r in program.routines() for n in edit.everything(r.body)):
        n.id = f"75:{k:08X}"
    return program


# ---- weights


@pytest.mark.parametrize(
    ("weights", "want"),
    [
        ([25] * 4, [8] * 4),
        ([24, 13, 6, 17, 8, 6, 16, 10], [8, 4, 2, 5, 3, 2, 5, 3]),
        ([10], [3]),
        ([0, 100], [0, 32]),
        ([1, 99], [1, 31]),
    ],
)
def test_shares(weights, want):
    assert splice.shares(weights)[0] == want


def test_shares_name_what_is_raised():
    assert splice.shares([1, 99])[1] == [0]


def test_random_scales_and_drops_zero_arms():
    arms = [case(1, enter(2, 3)), case(0, enter(2, 4)), case(99, enter(2, 4))]
    got, t = translate(base_0=blk("random", arms=arms))
    assert got == ["random", "branch 1", "  enter 1 2 0", "branch 31", "  enter 1 3 0"]
    assert found(t) == [("info", "P3_WEIGHT")]


def test_random_pads_when_weights_fall_short():
    got, _ = translate(base_0=blk("random", arms=[case(50, enter(2, 3))]))
    assert got == ["random", "branch 16", "  enter 1 2 0", "branch 16"]


def test_random_too_wide():
    arms = [case(1, enter(2, 3)) for _ in range(splice.ARMS + 1)]
    got, t = translate(base_0=blk("random", arms=arms))
    assert got == [] and found(t) == [("error", "P3_WIDE")]


# ---- buckets, modes, facing


def test_range_cases_become_buckets():
    arms = [case(0, enter(2, 3)), case(2, enter(2, 4)), other(enter(7, 5))]
    got, t = translate(base_0=blk("switch_range", arms=arms))
    buckets = ["enter 1 2 0", "enter 1 4 0", "enter 1 3 0", "enter 1 4 0"]
    assert got == ["switch_range", *(x for b in buckets for x in ("bucket", f"  {b}"))]
    assert found(t) == [("info", "P3_DROPPED")]


def test_range_without_default_ends_empty():
    got, _ = translate(base_0=blk("switch_range", arms=[case(0, enter(2, 3))]))
    assert got == ["switch_range", "bucket", "  enter 1 2 0", "bucket"]


def test_range_too_wide():
    got, t = translate(base_0=blk("switch_range", arms=[case(splice.BUCKETS - 1, enter(2, 3))]))
    assert got == [] and found(t) == [("error", "P3_WIDE")]


def test_mode_one_is_if_mode2():
    arms = [case(1, enter(2, 3)), other(enter(2, 4))]
    got, t = translate(base_0=blk("switch_mode", arms=arms))
    assert got == ["if_mode2", "  enter 1 2 0", "else", "  enter 1 3 0"] and not t.findings
    assert translate(base_0=blk("switch_mode", arms=arms[:1]))[0] == ["if_mode2", "  enter 1 2 0"]


def test_mode_without_host_mode():
    arms = [case(2, enter(2, 3)), other(enter(2, 4))]
    got, t = translate(base_0=blk("switch_mode", arms=arms))
    assert got == ["enter 1 3 0"] and found(t) == [("error", "P3_UNSUPPORTED")]


def test_rage_is_enraged():
    arms = [other(enter(2, 4))]
    got, _ = translate(base_0=blk("if_enraged", head=enter(2, 3), arms=arms))
    assert got == ["if_enraged", "  enter 1 2 0", "else", "  enter 1 3 0"]


@pytest.mark.parametrize(
    ("ang", "name", "deg"),
    [
        (64, "if_pursuit_off_facing", 90),
        (67, "if_pursuit_off_facing", 94),
        (90, "if_pursuit_off_facing", 127),
        (91, "if_off_facing", 128),
        (200, "if_off_facing", 255),
    ],
)
def test_facing_swaps_arms_and_scales(ang, name, deg):
    arms = [other(enter(2, 4))]
    got, t = translate(base_0=blk("if_facing", ang, head=enter(2, 3), arms=arms))
    assert got == [f"{name} {deg}", "  enter 1 3 0", "else", "  enter 1 2 0"]
    assert found(t) == ([("warning", "P3_APPROX")] if name == "if_off_facing" else [])


# ---- tests without a host state


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("if_target_state", (7, 0)),
        ("if_tired", ()),
        ("if_stamina_low", (3,)),
        ("if_target_set", ()),
    ],
)
def test_when_picks_the_arm(name, args):
    code = blk(name, *args, head=enter(2, 3), arms=[other(enter(2, 4))])
    got, t = translate(spec(when={name: True}), base_0=code)
    assert got == ["enter 1 2 0"] and found(t) == [("warning", "P3_FOLDED")]
    got, t = translate(spec(when={name: False}), base_0=code)
    assert got == ["enter 1 3 0"] and found(t) == [("warning", "P3_FOLDED")]
    got, t = translate(base_0=code)
    assert got == [] and found(t) == [("error", "P3_UNDECIDED")]


def test_when_is_not_asked_of_empty_arms():
    roll = blk("random", arms=[case(30, blk("if_object_near")), case(70, "")])
    got, t = translate(base_0=blk("if_stamina_low", 0, head=roll))
    assert got == [] and ("error", "P3_UNDECIDED") not in found(t)
    assert found(t)[-1] == ("info", "P3_DROPPED")


def test_when_drops_nothing_quietly():
    got, t = translate(spec(when={"if_tired": True}), base_0=blk("if_tired", head=enter(2, 3)))
    assert got == ["enter 1 2 0"] and not t.findings


def test_object_near_is_false():
    got, t = translate(base_0=blk("if_object_near", arms=[other(enter(2, 4))]))
    assert got == ["enter 1 3 0"] and not t.findings
    got, t = translate(base_0=blk("if_object_near", head=enter(2, 3)))
    assert got == [] and found(t) == [("warning", "P3_FOLDED")]


def test_set_target_zero_is_dropped():
    got, t = translate(base_0=op("set_target", 0, 0, 0))
    assert got == [] and found(t) == [("info", "P3_DROPPED")]
    got, t = translate(base_0=op("set_target", 1, 0, 0))
    assert got == [] and found(t) == [("error", "P3_UNSUPPORTED")]


# ---- the latch


def latched(between: str) -> str:
    arms = [case(0, enter(2, 3)), other(enter(2, 4))]
    return f"{op('set_latch', 1)} {between} {blk('switch_latch', arms=arms)}"


def test_latch_after_a_yield_takes_case_0():
    got, t = translate(base_0=latched(enter(7, 5)))
    assert got == ["enter 1 4 0", "enter 1 2 0"] and not t.findings


def test_latch_with_no_yield_takes_the_default():
    got, t = translate(base_0=latched(""))
    assert got == ["enter 1 3 0"] and not t.findings


def test_latch_over_a_decided_test():
    between = blk("if_tired", head=enter(7, 5))
    got, _ = translate(spec(when={"if_tired": True}), base_0=latched(between))
    assert got == ["enter 1 4 0", "enter 1 2 0"]
    got, _ = translate(spec(when={"if_tired": False}), base_0=latched(between))
    assert got == ["enter 1 3 0"]


def test_latch_that_may_yield():
    got, t = translate(base_0=latched(blk("if_enraged", head=enter(7, 5))))
    assert got == ["if_enraged", "  enter 1 4 0"] and found(t) == [("error", "P3_LATCH")]


def test_latch_with_no_set():
    arms = [case(0, enter(2, 3))]
    got, t = translate(base_0=blk("switch_latch", arms=arms))
    assert got == [] and found(t) == [("error", "P3_LATCH")]


# ---- own moves

OWN = addresses.OWN_MOVE_MAIN
SLAM = {"2,3": (1, 2), "7,13": "slam"}


def test_an_own_move_is_enter_16_slot():
    got, t = translate(
        spec(pairs=SLAM), slots={"bite": 0, "slam": 5}, base_0=f"{enter(2, 3)} {enter(7, 13)}"
    )
    assert got == ["enter 1 2 0", f"enter {OWN} 5 0"] and not t.findings
    assert dict(t.uses) == {((2, 3), (1, 2)): 1, ((7, 13), "slam"): 1}


def test_own_names_a_derived_move():
    sp = spec(pairs={"7,13": manifest.OWN})
    got, t = translate(sp, slots={"p3_7_13": 2}, base_0=enter(7, 13))
    assert got == [f"enter {OWN} 2 0"] and dict(t.uses) == {((7, 13), "p3_7_13"): 1}


@pytest.mark.parametrize("slots", [None, {}, {"bite": 0}])
def test_an_own_move_with_no_slot(slots):
    got, t = translate(spec(pairs=SLAM), slots=slots, base_0=f"{enter(2, 3)} {enter(7, 13)}")
    assert got == ["enter 1 2 0"] and found(t) == [("error", "P3_OWN_MISSING")]
    assert "7,13" in t.findings[0].text and "slam" in t.findings[0].text
    assert dict(t.uses) == {((2, 3), (1, 2)): 1}


def test_own_enters_are_in_a_called_routine():
    _, t = translate(spec(pairs=SLAM), slots={"slam": 5}, base_0=op("call", 1), call_1=enter(7, 13))
    assert lines(t.routines["p3_call_1"]) == [f"enter {OWN} 5 0", "end return"]


def test_an_own_enter_yields_for_the_latch():
    got, t = translate(spec(pairs=SLAM), slots={"slam": 5}, base_0=latched(enter(7, 13)))
    assert got == [f"enter {OWN} 5 0", "enter 1 2 0"] and not t.findings


def test_the_splice_names_the_slots_it_used():
    h = host()
    s = splice.splice(
        donor(base_0=f"{enter(2, 3)} {enter(7, 13)}"), h, spec(pairs=SLAM), slots={"slam": 5}
    )
    assert not s.findings and s.slots == {"slam": 5}
    assert dict(s.uses) == {((2, 3), (1, 2)): 1, ((7, 13), "slam"): 1}
    built = build.Built.of(h, s.edits)
    assert built.ok, built.findings
    base_3 = next(r for r in built.program.routines() if r.name == "base_3")
    assert lines(base_3.body)[1:3] == ["enter 1 2 0", f"enter {OWN} 5 0"]
    s = splice.splice(donor(base_0=enter(2, 3)), host(), spec(pairs=SLAM), slots={"slam": 5})
    assert s.slots == {}  # the donor enters none


def test_the_splice_without_slots_fails_an_own_pair():
    s = splice.splice(donor(base_0=f"{enter(2, 3)} {enter(7, 13)}"), host(), spec(pairs=SLAM))
    assert [f.code for f in s.findings] == ["P3_OWN_MISSING"] and s.slots == {}


# ---- the call history


def prev(then: str, default: str) -> str:
    return blk("switch_prev_call", arms=[case(1, then), other(default)])


def test_history_leads_the_routine_that_reads_it():
    got, t = translate(
        spec(history=2),
        base_0=op("call", 1),
        call_1=prev(enter(2, 3), enter(2, 4)),
    )
    assert got == ["call @p3_call_1"] and not t.findings
    assert lines(t.routines["p3_call_1"]) == [
        "if_var 2 1",
        "  set_var 2 1",
        "  enter 1 2 0",
        "else",
        "  set_var 2 1",
        "  enter 1 3 0",
        "end return",
    ]


def test_history_is_noted_by_every_call():
    _, t = translate(
        spec(history=2),
        base_0=f"{op('call', 1)} {op('call', 2)}",
        call_1=f"{op('call', 2)} {enter(2, 3)}",
        call_2=prev(enter(2, 4), ""),
    )
    assert lines(t.routines["p3_call_1"])[:2] == ["set_var 2 1", "if_var 2 1"]
    assert lines(t.routines["p3_call_2"])[0] == "if_var 2 1"


def test_history_unused_notes_nothing():
    _, t = translate(spec(history=2), base_0=op("call", 1), call_1=enter(2, 3))
    assert lines(t.routines["p3_call_1"]) == ["enter 1 2 0", "end return"]


def test_history_read_late():
    _, t = translate(spec(history=2), base_0=op("call", 1), call_1=f"{enter(2, 3)} {prev('', '')}")
    assert ("error", "P3_HISTORY") in found(t)


def test_history_read_in_a_pasted_routine():
    got, t = translate(spec(history=2), base_0=prev(enter(2, 3), ""))
    assert got == [] and found(t) == [("error", "P3_HISTORY")]


def test_history_needs_a_var():
    _, t = translate(spec(), base_0=op("call", 1), call_1=prev(enter(2, 3), ""))
    assert found(t) == [("error", "P3_HISTORY")]
    assert lines(t.routines["p3_call_1"]) == ["end return"]


def survey_of(*body: ir.Node) -> dict[int, script_vars.Var]:
    """The variables of a host script with `body` in its routine, no overlay."""
    h = host()
    h.routines()[1].body[:0] = body
    return script_vars.survey(h)


def reads(sp: manifest.Brain, survey: dict[int, script_vars.Var] | None):
    return translate(sp, survey, base_0=op("call", 1), call_1=prev(enter(2, 3), ""))


def test_history_is_the_first_free_variable():
    _, t = reads(spec(), survey_of())
    assert t.var == 2 and found(t) == [("info", "P3_HISTORY")]
    assert lines(t.routines["p3_call_1"])[:2] == ["if_var 2 1", "  set_var 2 1"]
    assert "variable 2" in t.findings[0].text


def test_history_skips_a_variable_the_host_uses():
    _, t = reads(spec(), survey_of(edit.insn("set_var", 2, 1)))
    assert t.var == 4 and found(t) == [("info", "P3_HISTORY")]


def test_history_with_no_free_variable_is_an_error():
    used = [edit.insn("set_var", 2, 1), edit.insn("set_var", 4, 1)]
    _, t = reads(spec(), survey_of(*used))
    assert t.var is None and found(t) == [("error", "P3_HISTORY")]
    assert "1 engine, 2 script" in t.findings[0].text
    assert lines(t.routines["p3_call_1"]) == ["end return"]


def test_history_named_and_free_is_quiet():
    _, t = reads(spec(history=2), survey_of())
    assert t.var == 2 and not t.findings


@pytest.mark.parametrize(("var", "why"), [(1, "MONSTER_STEP"), (4, "1 script node names it")])
def test_history_named_but_not_free_is_a_warning(var, why):
    _, t = reads(spec(history=var), survey_of(edit.insn("set_var", 4, 1)))
    assert t.var == var and found(t) == [("warning", "P3_HISTORY")]
    assert f"brain.history {var} is not free" in t.findings[0].text and why in t.findings[0].text


def test_history_unread_asks_nothing_of_the_host():
    _, t = translate(spec(), survey_of(), base_0=enter(2, 3))
    assert t.var is None and not t.findings


def test_splice_takes_the_survey():
    s = splice.splice(
        donor(base_0=op("call", 1), call_1=prev(enter(2, 3), "")),
        host(),
        spec(pairs={"2,3": (1, 2)}),
        survey_of(),
    )
    assert [(f.level, f.code) for f in s.findings] == [("info", "P3_HISTORY")]
    assert any(
        n.op.name == "if_var" and n.args["var"] == 2
        for r in s.edits.routines.values()
        for n in ir.walk(r)
        if isinstance(n, ir.Block)
    )


# ---- calls and pastes


def test_call_from_pasted_code_is_a_routine():
    got, t = translate(
        base_0=op("call", 1), call_1=f"{enter(2, 3)} {op('call', 2)}", call_2=enter(2, 4)
    )
    assert got == ["call @p3_call_1"]
    assert t.sources == {"p3_call_1": "call_1"}
    assert lines(t.routines["p3_call_1"]) == ["enter 1 2 0", "enter 1 3 0", "end return"]


def test_a_routine_is_made_once():
    _, t = translate(base_0=f"{op('call', 1)} {op('call', 1)}", call_1=enter(2, 3))
    assert list(t.routines) == ["p3_call_1"]


def test_call_to_an_empty_slot():
    got, t = translate(base_0=op("call", 3), call_1=enter(2, 3))
    assert got == [] and found(t) == [("error", "P3_UNSUPPORTED")]


def test_call_cycle():
    _, t = translate(base_0=op("call", 1), call_1=op("call", 1))
    assert ("error", "P3_UNSUPPORTED") in found(t)


def test_tail_call_base_is_pasted():
    got, t = translate(base_0=f"{enter(2, 3)} {op('call_base', 1)}", base_1=enter(2, 4))
    assert got == ["enter 1 2 0", "enter 1 3 0"] and not t.findings


def test_tail_call_base_in_arms():
    arms = [other(op("call_base", 2))]
    got, _ = translate(
        base_0=blk("if_enraged", head=op("call_base", 1), arms=arms),
        base_1=enter(2, 3),
        base_2=enter(2, 4),
    )
    assert got == ["if_enraged", "  enter 1 2 0", "else", "  enter 1 3 0"]


def test_call_base_before_more_code():
    got, t = translate(base_0=f"{op('call_base', 1)} {enter(2, 3)}", base_1=enter(2, 4))
    assert got == ["enter 1 2 0"] and found(t) == [("error", "P3_UNSUPPORTED")]


def test_call_base_from_a_call():
    _, t = translate(base_0=op("call", 1), call_1=op("call_base", 1), base_1=enter(2, 4))
    assert found(t) == [("error", "P3_UNSUPPORTED")]


def test_end_inside_a_routine():
    code = f"{blk('if_enraged', head=f'{enter(2, 3)} {END}')} {enter(2, 4)}"
    _, t = translate(base_0=code)
    assert found(t) == [("error", "P3_UNSUPPORTED")]


# ---- what has no counterpart


def test_host_ops():
    assert set(splice.HOST_OPS) <= set(p3ops.BY_NAME)
    got, t = translate(base_0=op("set_dest", 1, 2))
    assert got == [] and found(t) == [("error", "P3_HOST_OP")]


def test_unsupported_op():
    got, t = translate(base_0=op("wait", 3))
    assert got == [] and found(t) == [("error", "P3_UNSUPPORTED")]


def test_tables_name_real_ops():
    assert set(splice.OPS) <= set(p3ops.BY_NAME)
    assert set(splice.WHEN) <= set(splice.OPS)
    assert set(splice.MODES.values()) <= set(opcodes.BY_NAME)
    assert set(splice.TRANSFERS) <= set(splice.OPS)


def test_empty_blocks_are_dropped():
    nothing = op("set_target", 0, 0, 0)
    arms = [case(50, nothing), case(50, nothing)]
    got, t = translate(base_0=blk("random", arms=arms))
    assert got == [] and found(t).count(("info", "P3_DROPPED")) == 3
    got, t = translate(base_0=blk("if_enraged", head=nothing))
    assert got == []


def test_empty_then_keeps_the_else():
    got, _ = translate(base_0=blk("if_enraged", arms=[other(enter(2, 3))]))
    assert got == ["if_enraged", "else", "  enter 1 2 0"]


# ---- the edit set


def test_splice_replaces_the_body_after_the_prologue():
    donor_ = donor(base_0=f"{enter(2, 3)} {op('call', 1)}", call_1=enter(2, 4))
    h = host()
    s = splice.splice(donor_, h, spec(pairs=TWO))
    base_3 = next(r for r in h.routines() if r.name == "base_3")
    assert [k for k, ne in s.edits.nodes.items() if ne.delete] == [base_3.body[1].id]
    assert s.edits.inserts["u1"].anchor == Anchor("after", base_3.body[0].id)
    assert s.edits.notes["u1"].startswith("MHP3rd 23 base_0 0x")
    assert s.edits.notes["p3_call_1"].startswith("MHP3rd 23 call_1 0x")
    assert s.routines == {"p3_call_1": "call_1"} and not s.findings
    assert dict(s.uses) == {((2, 3), (1, 2)): 1, ((2, 4), (1, 3)): 1}
    built = build.Built.of(h, s.edits)
    assert built.ok, built.findings
    out = lines(next(r for r in built.program.routines() if r.name == "base_3").body)
    assert out == ["target_remembered", "enter 1 2 0", "call 3", "end base"]


def test_splice_without_a_prologue_goes_into_the_routine():
    s = splice.splice(donor(base_0=enter(2, 3)), host(prologue=False), spec(pairs=TWO))
    assert s.edits.inserts["u1"].anchor == Anchor("into", "base_3", 0)


@pytest.mark.parametrize(
    ("kw", "text"),
    [
        ({"splice": "base_9"}, "no routine base_9"),
        ({"entry": "base_9"}, "no routine base_9"),
    ],
)
def test_splice_needs_both_routines(kw, text):
    s = splice.splice(donor(base_0=enter(2, 3)), host(), spec(pairs=TWO, **kw))
    assert [(f.code, text in f.text) for f in s.findings] == [("P3_ROUTINE", True)]
    assert s.edits.is_empty()


def test_splice_keeps_an_end():
    h = host()
    base_3 = next(r for r in h.routines() if r.name == "base_3")
    base_3.body[-1] = edit.insn("goto", 0)
    s = splice.splice(donor(base_0=enter(2, 3)), h, spec(pairs=TWO))
    assert [f.code for f in s.findings] == ["P3_ROUTINE"]


def test_splice_flags_manifest_entries_nothing_reads():
    sp = spec(pairs=PAIRS | {"5,5": (1, 5)}, when={"if_enraged": True})
    s = splice.splice(donor(base_0=enter(2, 3)), host(), sp)
    assert [(f.level, f.code) for f in s.findings] == [
        ("warning", "P3_PAIR_UNUSED"),
        ("warning", "P3_PAIR_UNUSED"),
        ("warning", "P3_PAIR_UNUSED"),
        ("warning", "P3_WHEN"),
    ]
    assert not [f for f in s.findings if "2,3" in f.text]


def test_a_draw_after_a_draw_is_stepped():
    pick = blk("random", arms=[case(50, ""), case(50, enter(2, 3))])
    both = f"{pick} {blk('random', arms=[case(50, enter(2, 4)), case(50, enter(2, 3))])}"
    s = splice.splice(donor(base_0=both), host(), spec(pairs=TWO))
    assert [n.op.name for n in s.edits.inserts["u1"].nodes] == ["random", "rng_step2", "random"]


def test_a_draw_after_a_yield_is_not_stepped():
    pick = blk("random", arms=[case(50, enter(2, 3)), case(50, enter(2, 3))])
    s = splice.splice(donor(base_0=f"{pick} {pick}"), host(), spec(pairs=TWO))
    assert [n.op.name for n in s.edits.inserts["u1"].nodes] == ["random", "random"]


# ---- the command


BRAIN = """
[brain]
donor = 23
splice = "base_3"
entry = "base_12"
"""


def test_cli_wants_a_brain_table(tmp_path, capsys):
    path = tmp_path / "m.toml"
    path.write_text(
        '[port]\nname = "t"\nhost_species = 75\npac = "t.bin"\n[source]\nmodel = 5248\n'
    )
    assert main(["splice", str(path)]) == 1
    assert "no [brain] table" in capsys.readouterr().err


# ---- with the games


def region_pairs(brain: p3.Brain) -> set[tuple[int, int]]:
    t = splice.Translator(brain, manifest.Brain(23, "base_3", "base_12", history=2))
    t.scan("base_12", "base_3")
    return t.entered


def expanded(brain: p3.Brain, nodes: Sequence[p3.Node]) -> list[tuple[int, int]]:
    """The pairs a routine enters, in order, with each call replaced by its routine's."""
    out: list[tuple[int, int]] = []
    for n in p3.walk(nodes):
        if isinstance(n, p3.Insn) and n.op.name == "enter":
            out.append((n.args["main"], n.args["sub"]))
        elif isinstance(n, p3.Insn) and n.op.name == "call":
            name = brain.entry(p3ops.CALL, n.args["routine"])
            assert name is not None
            out += expanded(brain, brain.routines[name].body)
    return out


@pytest.fixture(scope="module")
def brute(data: Data) -> tuple[p3.Brain, ir.Program]:
    return p3.lift(data.p3rd, 23, 0), build.lift_row(data.fu, 75, 75)


def brute_spec(brain: p3.Brain, distinct: bool = False) -> manifest.Brain:
    """Every pair of the region mapped to (1, 2), or each to a host pair of its own."""
    to = {p: (1, p[0] * 32 + p[1]) if distinct else (1, 2) for p in region_pairs(brain)}
    when = {"if_target_state": True, "if_tired": False, "if_stamina_low": False}
    return manifest.Brain(
        23,
        "base_3",
        "base_12",
        history=2,
        pairs={f"{m},{s}": v for (m, s), v in to.items()},
        when=when,
    )


def test_brute_into_em75(brute, data, tmp_path):
    brain, h = brute
    s = splice.splice(brain, h, brute_spec(brain))
    assert not [f for f in s.findings if f.level == "error"], s.findings
    built = build.Built.of(h, s.edits)
    assert built.ok, built.findings
    assert built.budget.worst is not None and built.budget.worst.count <= budget.LIMIT
    bf = brainfile.new(75)
    bf.put(s.edits)
    path = brainfile.save(bf, tmp_path / "em75.toml")
    again = build.compile(brainfile.load(path), data.fu, 75, lift=h)
    assert again.id == built.id


def test_brute_routines_enter_what_the_donor_does(brute):
    brain, h = brute
    s = splice.splice(brain, h, brute_spec(brain, distinct=True))
    assert s.routines
    for label, name in s.routines.items():
        got = [
            divmod(n.args["sub"], 32)
            for n in ir.walk(s.edits.routines[label])
            if isinstance(n, ir.Insn) and n.op.name == "enter"
        ]
        assert got == expanded(brain, brain.routines[name].body), name


def test_brute_with_an_own_move(brute, data, tmp_path):
    brain, h = brute
    sp = brute_spec(brain, distinct=True)
    sp.pairs["7,13"] = "slam"
    s = splice.splice(brain, h, sp, slots={"slam": 5})
    assert not [f for f in s.findings if f.level == "error"], s.findings
    assert s.slots == {"slam": 5} and s.uses[(7, 13), "slam"] == 2
    built = build.Built.of(h, s.edits)
    assert built.ok, built.findings
    found = set()
    for label, name in s.routines.items():
        got = [
            (n.args["main"], n.args["sub"])
            for n in ir.walk(s.edits.routines[label])
            if isinstance(n, ir.Insn) and n.op.name == "enter"
        ]
        want = [
            (OWN, 5) if p == (7, 13) else (1, p[0] * 32 + p[1])
            for p in expanded(brain, brain.routines[name].body)
        ]
        assert got == want, name
        if (OWN, 5) in got:
            found.add(label)
    assert found == {"p3_call_33", "p3_call_36"}
    applied = {r.name: lines(r.body) for r in built.program.routines() if r.name in found}
    assert all("enter 16 5 0" in text for text in applied.values()) and len(applied) == 2
    bf = brainfile.new(75)
    bf.put(s.edits)
    path = brainfile.save(bf, tmp_path / "em75.toml")
    assert build.compile(brainfile.load(path), data.fu, 75, lift=h).id == built.id


def test_brute_cli(brute, data, tmp_path, capsys):
    brain, _ = brute
    sp = brute_spec(brain)
    pairs = "".join(f'"{k}" = [{v[0]}, {v[1]}]\n' for k, v in sp.pairs.items())
    when = "".join(f"{k} = {str(v).lower()}\n" for k, v in sp.when.items())
    path = tmp_path / "m.toml"
    path.write_text(
        '[port]\nname = "t"\nhost_species = 75\npac = "t.bin"\n[source]\nmodel = 5248\n'
        + f"{BRAIN}[brain.pairs]\n{pairs}[brain.when]\n{when}"
    )
    games = ["--data", str(data.fu.root), "--p3rd-data", str(data.p3rd.root)]
    out = tmp_path / "em75.toml"
    assert main(["splice", str(path), "--source", "-o", str(out), *games]) == 0
    text = capsys.readouterr().out
    assert "routines added" in text and "routine p3_call_11" in text and out.is_file()
    assert "info    P3_HISTORY base_3: the call history is script variable 2" in text
    assert brainfile.load(out).edits(75) is not None
    bad = tmp_path / "bad.toml"
    bad.write_text(path.read_text().replace('"2,3" = [1, 2]\n', ""))
    out.unlink()
    assert main(["splice", str(bad), "-o", str(out), *games]) == 1
    assert "P3_PAIR_UNMAPPED" in capsys.readouterr().out and not Path(out).exists()


def test_brute_cli_own_moves(brute, data, tmp_path, capsys):
    brain, _ = brute
    sp = brute_spec(brain)
    pairs = "".join(f'"{k}" = [{v[0]}, {v[1]}]\n' for k, v in sp.pairs.items() if k != "7,13")
    when = "".join(f"{k} = {str(v).lower()}\n" for k, v in sp.when.items())
    path = tmp_path / "m.toml"
    path.write_text(
        '[port]\nname = "t"\nhost_species = 75\npac = "t.bin"\n[source]\nmodel = 5248\n'
        + f'[moves.slam]\nanim = 40\n{BRAIN}[brain.pairs]\n{pairs}"7,13" = "slam"\n'
        + f"[brain.when]\n{when}"
    )
    games = ["--data", str(data.fu.root), "--p3rd-data", str(data.p3rd.root)]
    out = tmp_path / "em75.toml"
    assert main(["splice", str(path), "-o", str(out), *games]) == 0
    assert "pair 7,13 -> own slam (slot 0)  x2" in capsys.readouterr().out
    bf = brainfile.load(out)
    assert bf.pairs == {(OWN, 0): "slam"}
    built = build.compile(bf, data.fu, 75)
    assert built.ok and "enter 16 0 0" in [
        line for r in built.program.routines() for line in lines(r.body)
    ]
    derived = tmp_path / "derived.toml"
    derived.write_text(path.read_text().replace('"7,13" = "slam"', '"7,13" = "own"'))
    out.unlink()
    assert main(["splice", str(derived), "-o", str(out), *games]) == 0  # derived from the donor
    assert "pair 7,13 -> own p3_7_13 (slot 0)" in capsys.readouterr().out
    assert brainfile.load(out).pairs == {(OWN, 0): "p3_7_13"}
