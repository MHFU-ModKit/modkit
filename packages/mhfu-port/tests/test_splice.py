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
ONE = {"2,3": (1, 2)}
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


@pytest.mark.parametrize(
    ("weights", "whole", "want"),
    [([1, 2, 3, 4], 10, [3, 6, 10, 13]), ([5, 5], 10, [16, 16]), ([1, 29], 30, [1, 31])],
)
def test_shares_within_a_whole(weights, whole, want):
    assert splice.shares(weights, whole)[0] == want


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


def picks(nodes: Sequence[ir.Node], p: float = 1.0) -> dict[int, float]:
    """The chance of each `enter 1 k` the nodes make, by k, through nested randoms."""
    out: dict[int, float] = {}
    for n in nodes:
        if isinstance(n, ir.Insn) and n.op.name == "enter":
            out[n.args["sub"]] = out.get(n.args["sub"], 0) + p
        elif isinstance(n, ir.Block) and n.op.name == "random":
            for arm in n.arms:
                for k, q in picks(arm.body, p * arm.args["weight"] / splice.TOTAL).items():
                    out[k] = out.get(k, 0) + q
    return out


def wide(weights: Sequence[int], **kw: object) -> tuple[list[ir.Node], splice.Translator]:
    arms = [case(w, enter(2, k)) for k, w in enumerate(weights)]
    sp = spec(pairs={f"2,{k}": (1, k) for k in range(len(weights))}, **kw)
    t = splice.Translator(donor(base_0=blk("random", arms=arms)), sp)
    return t.paste("base_0", "base_3"), t


def test_random_too_wide():
    arms = [case(1, enter(2, 3)) for _ in range(splice.ARMS * splice.GROUP + 1)]
    got, t = translate(base_0=blk("random", arms=arms))
    assert got == [] and found(t) == [("error", "P3_WIDE")]


def test_random_with_thirteen_arms_and_no_padding_fits():
    nodes, t = wide([8, 8, 8, 8, 8, 8, 8, 8, 7, 7, 7, 7, 8])
    assert [n.op.name for n in nodes] == ["random"] and len(nodes[0].arms) == splice.ARMS
    assert not t.findings


def test_random_with_thirteen_arms_and_padding_is_split():
    nodes, t = wide([6] * 13)
    assert found(t) == [("info", "P3_SPLIT")]
    (outer,) = nodes
    assert [a.body[0].op.name for a in outer.arms[:2]] == ["random", "random"]
    assert [sum(a.args["weight"] for a in outer.arms)] == [splice.TOTAL]
    assert [len(a.body[0].arms) for a in outer.arms[:2]] == [7, 6]


def test_a_wide_random_keeps_what_it_draws():
    weights = [10, 10, 10, 10, 10, 10, 10, 10, 5, 5, 5, 5, 3, 2]
    nodes, t = wide(weights)
    assert found(t) == [("info", "P3_SPLIT")] and "14 live arms" in t.findings[0].text
    got = picks(nodes)
    assert sorted(got) == list(range(14))
    assert all(abs(got[k] - w / 100) < 0.02 for k, w in enumerate(weights))
    assert abs(sum(got.values()) - 1) < 0.01
    for n in nodes[0].arms:
        assert len(n.body[0].arms) <= splice.ARMS


def test_a_wide_random_keeps_the_arms_in_order():
    nodes, _ = wide([4] * 16 + [9] * 4)
    order = [
        b.body[0].args["sub"]
        for outer in nodes[0].arms
        if outer.body
        for b in outer.body[0].arms
        if b.body
    ]
    assert order == list(range(20))


def test_a_wide_random_that_falls_short_keeps_its_pad():
    weights = [4] * 14
    nodes, _ = wide(weights)
    got = picks(nodes)
    assert abs(sum(got.values()) - 0.56) < 0.02
    assert nodes[0].arms[-1].body == [] and nodes[0].arms[-1].args["weight"] >= 13


def test_a_wide_random_names_what_it_raises():
    nodes, t = wide([1] * 3 + [29] * 3 + [1] * 8 + [3])
    assert ("info", "P3_WEIGHT") in found(t) and ("info", "P3_SPLIT") in found(t)
    assert picks(nodes)


def test_a_wide_random_of_nothing_is_dropped():
    nothing = op("set_target", 0, 0, 0)
    got, t = translate(base_0=blk("random", arms=[case(7, nothing) for _ in range(14)]))
    assert got == [] and ("info", "P3_SPLIT") not in found(t)
    assert found(t).count(("info", "P3_DROPPED")) == 15


def test_a_group_with_nothing_to_do_stays_a_wait():
    nothing = op("set_target", 0, 0, 0)
    arms = [case(5, nothing) for _ in range(7)] + [case(5, enter(2, k)) for k in range(7)]
    sp = spec(pairs={f"2,{k}": (1, k) for k in range(7)})
    t = splice.Translator(donor(base_0=blk("random", arms=arms)), sp)
    (outer,) = t.paste("base_0", "base_3")
    assert [bool(a.body) for a in outer.arms[:2]] == [False, True]


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


# ---- distance, side, HP, the global word


def test_dist_cases_keep_their_hundreds_and_the_default():
    arms = [case(5, enter(2, 3)), case(10, ""), other(enter(2, 4))]
    got, t = translate(base_0=blk("switch_dist", arms=arms))
    assert got == ["switch_dist", "case 5", "  enter 1 2 0", "case 10", "default", "  enter 1 3 0"]
    assert not t.findings


def test_dist_without_a_default_adds_none():
    got, _ = translate(base_0=blk("switch_dist", arms=[case(5, enter(2, 3))]))
    assert got == ["switch_dist", "case 5", "  enter 1 2 0"]


def test_a_case_nothing_reaches_goes_before_it_is_translated():
    arms = [case(10, enter(2, 3)), case(10, op("call", 1)), case(4, enter(2, 4))]
    got, t = translate(base_0=blk("switch_dist", arms=arms), call_1=enter(2, 4))
    assert got == ["switch_dist", "case 10", "  enter 1 2 0"]
    assert found(t) == [("info", "P3_DROPPED")] * 2 and not t.routines


def test_dist_with_nothing_to_do():
    arms = [case(5, ""), other("")]
    got, t = translate(base_0=blk("switch_dist", arms=arms))
    assert got == [] and found(t) == [("info", "P3_DROPPED")]


def test_hp_cases_are_one_percent_up():
    arms = [case(25, enter(2, 3)), case(70, enter(2, 4)), other(enter(7, 5))]
    got, t = translate(base_0=blk("switch_hp", arms=arms))
    assert got == [
        "switch_hp",
        "case 26",
        "  enter 1 2 0",
        "case 71",
        "  enter 1 3 0",
        "default",
        "  enter 1 4 0",
    ]
    assert not t.findings


def test_hp_cases_stop_at_a_byte():
    arms = [case(254, enter(2, 3)), case(255, enter(2, 4))]
    got, t = translate(base_0=blk("switch_hp", arms=arms))
    assert got == ["switch_hp", "case 255", "  enter 1 2 0"] and found(t) == [
        ("info", "P3_DROPPED")
    ]


def test_side_is_an_arc_with_the_arms_as_they_are():
    got, t = translate(base_0=blk("if_target_side", head=enter(2, 3), arms=[other(enter(2, 4))]))
    assert got == ["if_target_in_arc 0 127", "  enter 1 2 0", "else", "  enter 1 3 0"]
    assert not t.findings


def test_global_540_is_case_2():
    got, t = translate(base_0=blk("switch_global_540", arms=[case(2, enter(2, 3))]))
    assert got == ["enter 1 2 0"] and found(t) == [("info", "P3_FOLDED")]


def test_global_540_names_the_arms_it_drops():
    arms = [case(1, enter(2, 4)), case(2, enter(2, 3)), other(enter(7, 5))]
    got, t = translate(base_0=blk("switch_global_540", arms=arms))
    assert got == ["enter 1 2 0"] and found(t) == [("warning", "P3_FOLDED")]
    assert "case 1 (1 instructions" in t.findings[0].text and "default" in t.findings[0].text


def test_global_540_without_case_2_takes_the_default():
    arms = [case(1, enter(2, 4)), other(enter(2, 3))]
    got, t = translate(base_0=blk("switch_global_540", arms=arms))
    assert got == ["enter 1 2 0"] and found(t) == [("warning", "P3_FOLDED")]


# ---- the species query

CHARGED = [case(1, enter(2, 3)), other(enter(2, 4))]


def test_the_queries_are_the_charge_helpers():
    assert splice.QUERIES == (addresses.CHARGE_QUERY_CHARGED, addresses.CHARGE_QUERY_COOLDOWN)
    assert splice.QUERIES == (0, 6)


@pytest.mark.parametrize("arg", splice.QUERIES)
def test_an_answered_query_stays_a_switch(arg):
    got, t = translate(spec(charge=True), base_0=blk("switch_query", arg, arms=CHARGED))
    want = [f"switch_query {arg}", "case 1", "  enter 1 2 0", "default", "  enter 1 3 0"]
    assert got == want and not t.findings


def test_query_cases_ascend_and_repeats_go():
    arms = [case(3, enter(2, 3)), case(1, enter(2, 4)), case(3, enter(7, 5)), other("")]
    got, t = translate(spec(charge=True), base_0=blk("switch_query", 0, arms=arms))
    assert got == ["switch_query 0", "case 1", "  enter 1 3 0", "case 3", "  enter 1 2 0"]
    assert found(t) == [("info", "P3_DROPPED")]


def test_an_empty_case_still_shields_the_default():
    arms = [case(1, ""), other(enter(2, 3))]
    got, _ = translate(spec(charge=True), base_0=blk("switch_query", 0, arms=arms))
    assert got == ["switch_query 0", "case 1", "default", "  enter 1 2 0"]


def test_a_query_with_nothing_to_do():
    got, t = translate(spec(charge=True), base_0=blk("switch_query", 0, arms=[case(1, "")]))
    assert got == [] and found(t) == [("info", "P3_DROPPED")]


@pytest.mark.parametrize(
    ("arg", "charge"), [(2, True), (3, True), (7, True), (0, False), (6, False)]
)
def test_any_other_query_is_answer_0(arg, charge):
    arms = [case(0, enter(2, 3)), case(1, enter(2, 4)), other(enter(7, 5))]
    got, t = translate(spec(charge=charge), base_0=blk("switch_query", arg, arms=arms))
    assert got == ["enter 1 2 0"] and found(t) == [("warning", "P3_FOLDED")]
    text = t.findings[0].text
    assert f"switch_query {arg} taken as answer 0" in text
    assert "case 1 (1 instructions, first enter 2 4), default (1 instructions" in text
    assert "case 0" not in text


def test_answer_0_without_a_case_0_is_the_default():
    got, t = translate(
        base_0=blk("switch_query", 2, arms=[case(1, enter(2, 4)), other(enter(2, 3))])
    )
    assert got == ["enter 1 2 0"] and found(t) == [("warning", "P3_FOLDED")]
    got, t = translate(base_0=blk("switch_query", 2, arms=[case(1, enter(2, 4))]))
    assert got == [] and found(t) == [("warning", "P3_FOLDED")]


def test_a_fold_that_drops_no_code_is_quiet():
    arms = [case(0, enter(2, 3)), case(1, ""), other("")]
    got, t = translate(base_0=blk("switch_query", 2, arms=arms))
    assert got == ["enter 1 2 0"] and not t.findings


def test_a_folded_arm_makes_no_routine():
    arms = [case(0, enter(2, 3)), case(1, op("call", 1))]
    _, t = translate(base_0=blk("switch_query", 2, arms=arms), call_1=enter(2, 4))
    assert not t.routines


def test_the_splice_builds_a_query():
    sp = spec(charge=True, pairs=TWO)
    h = host()
    s = splice.splice(donor(base_0=blk("switch_query", 0, arms=CHARGED)), h, sp)
    assert not s.findings
    built = build.Built.of(h, s.edits)
    assert built.ok, built.findings
    base_3 = next(r for r in built.program.routines() if r.name == "base_3")
    assert lines(base_3.body)[1:3] == ["switch_query 0", "case 1"]


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


# ---- fatigue

TIRED = blk("if_tired", head=enter(2, 3), arms=[other(enter(2, 4))])


def test_fatigue_turns_if_tired_into_gauge_a():
    got, t = translate(spec(fatigue=True), base_0=TIRED)
    assert got == ["if_gauge_a_low", "  enter 1 2 0", "else", "  enter 1 3 0"] and not t.findings
    assert t.asked == set()


@pytest.mark.parametrize(("name", "args"), [("if_tired", ()), ("if_stamina_low", (0,))])
def test_fatigue_leaves_nothing_to_when(name, args):
    got, t = translate(spec(fatigue=True), base_0=blk(name, *args, head=enter(2, 3)))
    assert got == ["if_gauge_a_low", "  enter 1 2 0"] and not t.findings


def test_fatigue_takes_stamina_low_only_at_zero():
    code = blk("if_stamina_low", 3, head=enter(2, 3), arms=[other(enter(2, 4))])
    got, t = translate(spec(fatigue=True, when={"if_stamina_low": True}), base_0=code)
    assert got == ["enter 1 2 0"] and found(t) == [("warning", "P3_FOLDED")]
    got, t = translate(spec(fatigue=True), base_0=code)
    assert got == [] and found(t) == [("error", "P3_UNDECIDED")]


def test_fatigue_drops_an_empty_test():
    roll = blk("random", arms=[case(30, blk("if_object_near")), case(70, "")])
    got, t = translate(spec(fatigue=True), base_0=blk("if_stamina_low", 0, head=roll))
    assert got == [] and set(found(t)) == {("info", "P3_DROPPED")}


def test_without_fatigue_if_tired_still_asks():
    got, t = translate(spec(when={"if_tired": False}), base_0=TIRED)
    assert got == ["enter 1 3 0"] and found(t) == [("warning", "P3_FOLDED")]


def test_a_when_the_helper_answers_is_ignored():
    sp = spec(fatigue=True, pairs=TWO, when={"if_tired": True, "if_stamina_low": True})
    s = splice.splice(donor(base_0=TIRED), host(), sp)
    assert [(f.level, f.code) for f in s.findings] == [
        ("warning", "P3_WHEN_IGNORED"),
        ("warning", "P3_WHEN_IGNORED"),
    ]
    assert [f.text.split()[0] for f in s.findings] == ["when.if_tired", "when.if_stamina_low"]
    late = blk("if_stamina_low", 3, head=enter(2, 3), arms=[other(enter(2, 4))])
    s = splice.splice(donor(base_0=f"{TIRED} {late}"), host(), sp)
    assert [f.text.split()[0] for f in s.findings if f.code == "P3_WHEN_IGNORED"] == [
        "when.if_tired"
    ]


def test_when_is_not_ignored_without_fatigue():
    s = splice.splice(donor(base_0=TIRED), host(), spec(pairs=TWO, when={"if_tired": True}))
    assert [f.code for f in s.findings] == ["P3_FOLDED"]


def host_using(*body: ir.Node, where: str = "call_0") -> ir.Program:
    h = host()
    next(r for r in h.routines() if r.name == where).body[:0] = body
    return h


@pytest.mark.parametrize("op_", ["refill_gauge_a", "if_gauge_a_low"])
def test_fatigue_wants_the_host_to_leave_gauge_a(op_):
    use = edit.insn(op_, 0) if op_ == "refill_gauge_a" else edit.if_(op_, then=[], else_=None)
    sp = spec(fatigue=True, pairs=ONE)
    s = splice.splice(donor(base_0=enter(2, 3)), host_using(use), sp)
    assert [(f.level, f.code) for f in s.findings] == [("error", "P3_FATIGUE_HOST")]
    assert op_ in s.findings[0].text and "outside base_3" in s.findings[0].text
    s = splice.splice(donor(base_0=enter(2, 3)), host_using(use), spec(pairs=ONE))
    assert not s.findings
    s = splice.splice(donor(base_0=enter(2, 3)), host_using(use, where="base_3"), sp)
    assert not s.findings


def test_fatigue_wants_a_species_that_does_not_drain_gauge_a(monkeypatch):
    latch = int(addresses.GAUGE_A_LATCH)
    monkeypatch.setattr(script_vars, "scan", lambda o: script_vars.Sites({}, {latch: 2}))
    sp = spec(fatigue=True, pairs=ONE)
    lifted = donor(base_0=enter(2, 3))
    s = splice.splice(lifted, host(), sp, overlay=object())  # type: ignore[arg-type]
    assert [(f.level, f.code) for f in s.findings] == [("error", "P3_FATIGUE_HOST")]
    assert "GAUGE_A_LATCH 2x" in s.findings[0].text
    assert not splice.splice(lifted, host(), sp).findings
    other_spec = spec(pairs=ONE)
    assert not splice.splice(lifted, host(), other_spec, overlay=object()).findings  # type: ignore[arg-type]


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


def test_a_list_enters_its_targets_in_order():
    sp = spec(pairs={"7,13": [(1, 2), "slam"], "2,3": [(1, 2), (1, 3)]})
    got, t = translate(sp, slots={"slam": 5}, base_0=f"{enter(7, 13)} {enter(2, 3)}")
    assert got == ["enter 1 2 0", f"enter {OWN} 5 0", "enter 1 2 0", "enter 1 3 0"]
    assert not t.findings
    assert dict(t.uses) == {
        ((7, 13), (1, 2)): 1,
        ((7, 13), "slam"): 1,
        ((2, 3), (1, 2)): 1,
        ((2, 3), (1, 3)): 1,
    }


def test_a_list_names_a_derived_move():
    sp = spec(pairs={"7,13": [(1, 2), manifest.OWN]})
    got, _ = translate(sp, slots={"p3_7_13": 2}, base_0=enter(7, 13))
    assert got == ["enter 1 2 0", f"enter {OWN} 2 0"]


def test_a_list_with_a_move_that_has_no_slot_keeps_the_rest():
    got, t = translate(spec(pairs={"7,13": [(1, 2), "slam"]}), base_0=enter(7, 13))
    assert got == ["enter 1 2 0"] and found(t) == [("error", "P3_OWN_MISSING")]


def test_every_enter_of_a_list_yields_for_the_latch():
    sp = spec(pairs=TWO | {"7,13": [(1, 2), "slam"]})
    got, t = translate(sp, slots={"slam": 5}, base_0=latched(enter(7, 13)))
    assert got == ["enter 1 2 0", f"enter {OWN} 5 0", "enter 1 2 0"] and not t.findings


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


def routine(**routines: str) -> list[str]:
    """The lines of `p3_call_1`, translated with the history in variable 2."""
    _, t = translate(spec(history=2), base_0=op("call", 1), **routines)
    return lines(t.routines["p3_call_1"])


def history_found(**routines: str) -> list[tuple[str, str]]:
    return found(translate(spec(history=2), base_0=op("call", 1), **routines)[1])


READ = prev(enter(2, 3), enter(2, 4))
IF_READ = [
    "if_var 2 1",
    "  set_var 2 1",
    "  enter 1 2 0",
    "else",
    "  set_var 2 1",
    "  enter 1 3 0",
]


def test_history_read_late_notes_in_the_arms_after_it():
    got = routine(call_1=f"{enter(2, 3)} {prev(enter(2, 3), '')}")
    assert got == [
        "enter 1 2 0",
        "if_var 2 1",
        "  set_var 2 1",
        "  enter 1 2 0",
        "else",
        "  set_var 2 1",
        "end return",
    ]
    assert not history_found(call_1=f"{enter(2, 3)} {prev(enter(2, 3), '')}")


def test_history_read_in_a_branch_notes_the_other_path_first():
    code = blk("if_enraged", head=f"{enter(2, 3)} {READ}", arms=[other(enter(2, 4))])
    want = ["if_enraged", "  enter 1 2 0", *(f"  {x}" for x in IF_READ)]
    assert routine(call_1=code) == [*want, "else", "  set_var 2 1", "  enter 1 3 0", "end return"]
    assert not history_found(call_1=code)


def test_history_read_in_a_branch_with_no_else_adds_one():
    got = routine(call_1=blk("if_enraged", head=READ))
    assert got == [
        "if_enraged",
        *(f"  {x}" for x in IF_READ),
        "else",
        "  set_var 2 1",
        "end return",
    ]


def indented(nodes: list[str]) -> list[str]:
    return [f"  {x}" for x in nodes]


def test_history_read_in_a_pick_notes_the_other_arms():
    random = blk("random", arms=[case(50, READ), case(50, enter(2, 3))])
    assert routine(call_1=random) == [
        "random",
        "branch 16",
        *indented(IF_READ),
        "branch 16",
        "  set_var 2 1",
        "  enter 1 2 0",
        "end return",
    ]
    ranges = blk("switch_range", arms=[case(0, READ), other(enter(2, 3))])
    assert routine(call_1=ranges) == [
        "switch_range",
        "bucket",
        *indented(IF_READ),
        "bucket",
        "  set_var 2 1",
        "  enter 1 2 0",
        "end return",
    ]
    assert not history_found(call_1=random) and not history_found(call_1=ranges)


def test_history_read_in_a_switch_without_a_default_adds_one():
    code = blk("switch_dist", arms=[case(5, READ)])
    want = ["switch_dist", "case 5", *indented(IF_READ), "default", "  set_var 2 1", "end return"]
    assert routine(call_1=code) == want and not history_found(call_1=code)


def test_history_read_nested_in_its_own_default():
    again = blk("switch_prev_call", arms=[case(2, enter(2, 4)), other(enter(7, 5))])
    code = blk("switch_prev_call", arms=[case(1, enter(2, 3)), other(again)])
    assert routine(call_1=code) == [
        "if_var 2 1",
        "  set_var 2 1",
        "  enter 1 2 0",
        "else",
        "  if_var 2 2",
        "    set_var 2 1",
        "    enter 1 3 0",
        "  else",
        "    set_var 2 1",
        "    enter 1 4 0",
        "end return",
    ]
    assert not history_found(call_1=code)


def test_history_read_after_code_in_its_own_default():
    again = blk("switch_prev_call", arms=[case(2, enter(2, 4)), other("")])
    code = blk("switch_prev_call", arms=[case(1, enter(2, 3)), other(f"{enter(7, 5)} {again}")])
    assert routine(call_1=code) == [
        "if_var 2 1",
        "  set_var 2 1",
        "  enter 1 2 0",
        "else",
        "  enter 1 4 0",
        "  if_var 2 2",
        "    set_var 2 1",
        "    enter 1 3 0",
        "  else",
        "    set_var 2 1",
        "end return",
    ]
    assert not history_found(call_1=code)


def test_history_read_after_a_call_warns():
    call = f"{op('call', 2)} {READ}"
    got = routine(call_1=call, call_2=enter(2, 3))
    assert got[:2] == ["set_var 2 2", "enter 1 2 0"]
    assert history_found(call_1=call, call_2=enter(2, 3)) == [("warning", "P3_HISTORY")]


def test_history_read_twice_in_a_row_is_an_error():
    assert history_found(call_1=f"{READ} {READ}") == [("error", "P3_HISTORY")]


def test_history_of_an_inlined_read_is_the_calls_not_the_callers():
    call = f"{op('call', 2)} {enter(2, 3)}"
    assert routine(call_1=call, call_2=READ) == [
        "set_var 2 1",
        "if_var 2 1",
        "  set_var 2 2",
        "  enter 1 2 0",
        "else",
        "  set_var 2 2",
        "  enter 1 3 0",
        "enter 1 2 0",
        "end return",
    ]
    assert not history_found(call_1=call, call_2=READ)


def test_history_reads_copied_into_buckets_stay_noted():
    ranges = blk("switch_range", arms=[case(1, enter(2, 3)), other(op("call", 2))])
    inlined = [
        "if_var 2 1",
        "  set_var 2 2",
        "  enter 1 2 0",
        "else",
        "  set_var 2 2",
        "  enter 1 3 0",
    ]
    assert routine(call_1=ranges, call_2=READ) == [
        "set_var 2 1",
        "switch_range",
        "bucket",
        *indented(inlined),
        "bucket",
        "  enter 1 2 0",
        "bucket",
        *indented(inlined),
        "end return",
    ]


def test_a_late_read_builds():
    h = host()
    code = donor(base_0=op("call", 1), call_1=f"{enter(2, 3)} {READ}")
    s = splice.splice(code, h, spec(pairs=TWO, history=2))
    assert not s.findings
    built = build.Built.of(h, s.edits)
    assert built.ok, built.findings
    mine = next(r for r in built.program.routines() if r.name == "p3_call_1")
    assert lines(mine.body)[:2] == ["enter 1 2 0", "if_var 2 1"]


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


def test_the_inner_draws_of_a_split_random_are_stepped():
    arms = [case(7, enter(2, 3)) for _ in range(14)]
    s = splice.splice(donor(base_0=blk("random", arms=arms)), host(), spec(pairs=TWO))
    (outer,) = s.edits.inserts["u1"].nodes
    inner = [a.body for a in outer.arms if a.body]
    assert inner and all([n.op.name for n in b] == ["rng_step2", "random"] for b in inner)
    assert build.Built.of(host(), s.edits).ok


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


def test_brute_with_fatigue(brute, data):
    brain, h = brute
    sp = brute_spec(brain)
    sp.fatigue = True
    del sp.when["if_tired"], sp.when["if_stamina_low"]
    base = splice.splice(brain, h, brute_spec(brain))
    s = splice.splice(brain, h, sp, overlay=data.fu.em(75))
    assert not [f for f in s.findings if f.level == "error"], s.findings
    assert "P3_WHEN_IGNORED" not in {f.code for f in s.findings}
    built = build.Built.of(h, s.edits)
    assert built.ok, built.findings
    assert built.budget.worst is not None and built.budget.worst.count <= budget.LIMIT
    tired = [
        n
        for n in p3.walk(brain.routines["base_13"].body)
        if isinstance(n, p3.Block) and n.op.name == "if_tired"
    ]
    assert len(tired) == 1 and brain.routines["base_13"].body[1] is tired[0]
    gauge = [
        n
        for nodes in (i.nodes for i in s.edits.inserts.values())
        for n in ir.walk(nodes)
        if isinstance(n, ir.Block) and n.op.name == "if_gauge_a_low"
    ]
    assert len(gauge) == 1
    assert not any(
        n.op.name == "if_gauge_a_low"
        for i in base.edits.inserts.values()
        for n in ir.walk(i.nodes)
        if isinstance(n, ir.Block)
    )
    assert set(s.routines) - set(base.routines) == {"p3_call_24", "p3_call_37"}
    assert set(base.routines) <= set(s.routines)


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


def test_brute_cli_fatigue(brute, data, tmp_path, capsys):
    brain, _ = brute
    sp = brute_spec(brain)
    pairs = "".join(f'"{k}" = [{v[0]}, {v[1]}]\n' for k, v in sp.pairs.items() if k != "7,13")
    when = "".join(f"{k} = {str(v).lower()}\n" for k, v in sp.when.items())
    path = tmp_path / "m.toml"
    path.write_text(
        '[port]\nname = "t"\nhost_species = 75\npac = "t.bin"\n[source]\nmodel = 5248\n'
        + f'{BRAIN}fatigue = true\n[brain.pairs]\n{pairs}"7,13" = "own"\n[brain.when]\n{when}'
    )
    games = ["--data", str(data.fu.root), "--p3rd-data", str(data.p3rd.root)]
    assert main(["splice", str(path), *games]) == 0
    text = capsys.readouterr().out
    assert "own moves; stamina 400 or 600 of 1000, tired 2250 frames" in text
    assert "1 own moves" in text and "P3_FATIGUE_SHARED" in text
    ignored = [line.split(":")[0] for line in text.splitlines() if line.startswith("warning")]
    assert ignored.count("warning P3_WHEN_IGNORED base_3") == 2
    assert "error" not in text


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
