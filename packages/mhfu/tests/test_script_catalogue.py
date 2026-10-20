# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import dataclasses
import inspect
import re
import string
from collections import Counter

import pytest
from mhfu.em import script
from mhfu.em.script import catalogue as cat
from mhfu.em.script import edit, ir, lift
from mhfu.em.script.catalogue import Confidence, Kind
from mhfu.em.script.opcodes import (
    BY_NAME,
    END_KINDS,
    FIRST_DEEP,
    OPCODES,
    SLOTS,
    Op,
    Shape,
    operands,
    slot,
)
from mhfu.files import EM_SPECIES

NAMES = [o.name for o in OPCODES.values()]


def sample(op: Op) -> list[ir.Node]:
    """Nodes of the opcode with every operand 0: one per layout of a plain opcode."""
    if op.shape is Shape.PLAIN:
        out = []
        for key in (None, *op.by):
            args = dict.fromkeys((o.name for o in operands(op.layout(key))), 0)
            if key is not None:
                args[next(iter(args))] = key
            out.append(ir.Insn(op, args))
        return out
    case = dict.fromkeys((o.name for o in operands(op.label)), 16)
    match op.shape:
        case Shape.SWITCH:
            arms = [ir.Arm(1, dict.fromkeys(case, 0), []), ir.Arm(2, {}, [])]
        case Shape.RANDOM:
            arms = [ir.Arm(1, case, []), ir.Arm(1, case, [])]
        case Shape.BUCKET | Shape.ZONE:
            arms = [ir.Arm(1, {}, []) for _ in range(3)]
        case Shape.IF:
            arms = [ir.Arm(1, {}, [])]
        case _:
            arms = []
    return [ir.Block(op, dict.fromkeys((o.name for o in op.opener), 0), [], arms)]


def layout_names(op: Op) -> set[str]:
    if op.shape is Shape.PLAIN:
        return {o.name for lay in (op.args, *op.by.values()) for o in operands(lay)}
    return {o.name for o in (*op.opener, *operands(op.label))}


def insn(name: str, *args: ir.Value) -> ir.Insn:
    return edit.insn(name, *args)


# ---- the table


def test_every_opcode():
    assert sorted(cat.ENTRIES) == sorted(NAMES)
    assert [e.op for e in cat.entries()] == list(OPCODES.values())
    assert cat.entry(BY_NAME["enter"]) is cat.entry("enter")


@pytest.mark.parametrize("name", NAMES)
def test_operand_names(name):
    e = cat.entry(name)
    assert {i.name for i in e.operands} == layout_names(e.op)
    assert len(e.operands) == len({i.name for i in e.operands})


def test_no_layout():
    for cls in (cat.Entry, cat.OperandInfo, cat.EndKind, cat.Field):
        names = {f.name for f in dataclasses.fields(cls)}
        assert not names & {"args", "layout", "length", "lengths", "size"}, cls
    assert not re.search(r"\.lengths\b|length_of|\.size\b", inspect.getsource(cat))
    nodes = sample(BY_NAME["set_target"])
    assert [[f.name for f in cat.operands(n)] for n in nodes] == [
        ["kind", "a", "b"],
        ["kind", "a", "section"],
    ]


@pytest.mark.parametrize("name", NAMES)
def test_wording(name):
    e = cat.entry(name)
    assert e.title and e.group and e.meaning and isinstance(e.confidence, Confidence)
    for i in e.operands:
        assert i.label and i.meaning
        assert (i.fmt is cat.Fmt.WORD) == (i.words is not None)
    slots = {f for _, f, *_ in string.Formatter().parse(e.text) if f}
    pair = {"pair"} if {"main", "sub"} <= layout_names(e.op) else set()
    assert slots <= layout_names(e.op) | pair, name
    assert bool(e.case) == (e.op.shape is Shape.SWITCH and e.op.name != "switch_zone_relation")
    for n in sample(e.op):
        assert cat.kind_of(n) is e.kind and cat.confidence(n) in Confidence
        assert cat.summary(n) and "{" not in cat.summary(n)
        assert cat.title(n)
        for f in cat.operands(n):
            assert f.text and f.label and f.meaning
        if isinstance(n, ir.Block):
            p = cat.ports(n)
            assert len(p.arms) == len(n.arms) and all(p.arms)
            for a in n.arms:
                assert [f.name for f in cat.arm_operands(n, a)] == [
                    o.name for o in operands(n.op.layout(a.sub))
                ]


def test_kinds():
    by: dict[Kind, set[str]] = {k: set() for k in Kind}
    for e in cat.entries():
        by[e.kind].add(e.name)
    assert by[Kind.MOVE] == {"enter"}
    assert by[Kind.PICK] == {"random", "random_shifted"}
    assert by[Kind.CALL] == {"call_deep", "call_ret", "call", "walk", "call_spot", "route_run"}
    assert by[Kind.JUMP] == {"goto", "goto_base"}
    assert by[Kind.END] == {"end"}
    assert by[Kind.REPEAT] == {"repeat"}
    assert by[Kind.VAR] == {"set_var", "reset_var", "clear_counters"}
    assert by[Kind.TARGET] >= {"set_target", "target_remembered", "pick_target", "pick_target_role"}
    assert all(
        n == "set_target" or n.startswith(("target_", "pick_target")) for n in by[Kind.TARGET]
    )
    shapes = {Kind.IF: {Shape.IF}, Kind.SWITCH: {Shape.SWITCH, Shape.BUCKET, Shape.ZONE}}
    for kind, want in shapes.items():
        assert {BY_NAME[n].shape for n in by[kind]} == want
    assert {n for n in NAMES if BY_NAME[n].shape in {Shape.IF, Shape.SWITCH}} <= (
        by[Kind.IF] | by[Kind.SWITCH]
    )
    assert cat.kind_of(BY_NAME["enter"]) is Kind.MOVE
    assert cat.kind_of(insn("rng_step")) is Kind.OP and by[Kind.OP] >= {"rng_step", "wait", "face"}
    assert [e.name for e in cat.entries(Kind.JUMP)] == ["goto_base", "goto"]


def test_pickers():
    for kind in (Kind.IF, Kind.SWITCH):
        titles = [e.title for e in cat.entries(kind)]
        assert len(titles) == len(set(titles)), kind
    assert len(cat.entries(Kind.IF)) == sum(o.shape is Shape.IF for o in OPCODES.values())
    assert {e.group for e in cat.entries(Kind.IF)} >= {"Hunter", "Distance", "Angle", "State"}


def test_confidence():
    assert cat.entry("if_aware").confidence is Confidence.HIGH
    assert cat.entry("if_goal_elsewhere").confidence is Confidence.MEDIUM
    assert cat.entry("if_hunter_special").confidence is Confidence.LOW
    assert Confidence.LOW < Confidence.MEDIUM < Confidence.HIGH
    assert Confidence.MEDIUM.label == "medium"
    assert cat.confidence(insn("end", 0)) is Confidence.HIGH
    assert cat.confidence(insn("end", 0xFE)) is Confidence.LOW
    assert cat.confidence(insn("end", 0x77)) is Confidence.HIGH


# ---- operands


def test_values():
    def text(node, name):
        return next(f.text for f in cat.operands(node) if f.name == name)

    assert text(edit.if_("if_dist_flat", 5, then=[]), "dist") == "500 units"
    assert text(insn("face", 64), "yaw") == "90 deg (64/256)"
    assert text(insn("call_spot", 0xFFFF, 10), "section") == "any area"
    assert text(insn("call_spot", 40, 10), "radius") == "100 units"
    assert text(insn("set_target", 3, 0, 112), "section") == "st112"
    assert text(insn("set_target", 3, 0, 99), "section") == "Snowy area 1"
    assert text(insn("set_target", 3, 0, 112), "kind") == "3 (area)"
    assert text(insn("set_target", 6, 0, 1), "kind") == "6"
    assert text(insn("set_combat", 0), "on") == "0 (leave)"
    assert text(insn("refill_gauge_a", 0), "apply") == "apply"
    assert text(insn("refill_gauge_a", 1), "apply") == "no-op"
    assert text(edit.if_("if_off_facing", 30, then=[]), "deg") == "30 deg"
    assert text(insn("end", 0xFC), "kind") == "Notice done"
    assert text(insn("end", 0x77), "kind") == "Ignored (119)"
    assert text(insn("call_deep", 3, 7), "routine") == "deep3[7]"
    assert text(insn("call", 2), "routine") == "call[2]"
    assert text(insn("goto", 4), "routine") == "base[4]"
    assert text(insn("walk", 1, 3, 5, 0, 1), "stages") == "walk[5]"
    assert text(insn("route_arm", 2, 4, 0, 0), "list") == "stages[2]"
    assert text(insn("call", ir.Sym("spin")), "routine") == "@spin"
    assert text(insn("pick_target_role", 0x30), "mask") == "0x30"


def test_summary():
    pairs = cat.Words({(3, 27): "Roar"})
    assert cat.summary(insn("enter", 3, 27, 0)) == "Move (3, 27)"
    assert cat.summary(insn("enter", 3, 27, 0), pairs) == "Move (3, 27) Roar"
    assert cat.summary(insn("enter", 2, 1, 0), pairs) == "Move (2, 1)"
    assert cat.summary(insn("enter", ir.Sym("a"), ir.Sym("b"), 0)) == "Move (@a, @b)"
    assert cat.summary(insn("set_var", 4, 1)) == "var 4 = 1"
    assert cat.summary(insn("call_deep", 3, 7)) == "Call deep3[7]"
    assert cat.summary(insn("end", 0)) == "End: Restart"
    assert cat.summary(insn("set_target", 3, 0, 112)) == (
        "Set target: Kind 3 (area), A 0, Area st112"
    )
    assert cat.summary(insn("clear_counters")) == "Clear counters"
    block = edit.if_("if_in_pair", 0, 18, then=[])
    assert cat.summary(block, pairs) == "Own move is (0, 18)"
    assert cat.summary(edit.if_("if_dist_flat", 5, then=[])) == "Within 500 units (flat)"


# ---- blocks


def test_if_ports():
    both = edit.if_("if_aware", then=[insn("wait", 1)], else_=[])
    assert cat.ports(both) == cat.Ports("then", ("else",))
    assert cat.ports(edit.if_("if_aware", then=[])) == cat.Ports("then", ())
    assert cat.ports(edit.repeat(3, [])) == cat.Ports("body", ())
    assert cat.summary(edit.repeat(3, [])) == "Repeat 3 times"


def test_switch_ports():
    hp = edit.switch("switch_hp", cases=[(50, []), (75, [])], default=[])
    assert cat.ports(hp) == cat.Ports(None, ("HP below 50 %", "HP below 75 %", "otherwise"))
    sec = edit.switch("switch_section", cases=[(98, []), (0xFFFF, [])])
    assert cat.ports(sec).arms == ("Snowy base camp", "any area")
    dist = edit.switch("switch_dist", cases=[(10, [])])
    assert cat.ports(dist).arms == ("within 1000 units",)
    yaw = edit.switch("switch_yaw", cases=[(64, [])])
    assert cat.ports(yaw).arms == ("up to 90 deg (64/256)",)
    assert cat.title(hp) == "HP percent" and cat.kind_of(hp) is Kind.SWITCH


def test_bucket_and_zone_ports():
    bucket = edit.switch("switch_range", cases=[([],), ([],), ([],)])
    assert cat.ports(bucket).arms == ("range 1", "range 2", "beyond")
    w = cat.Words(ranges={1: "melee", 5: "out"}, limits=((450.0, -1.0, 2700.0, 5000.0),) * 2)
    assert cat.ports(bucket, w).arms == ("melee \u2264 450", "skipped", "out beyond -1")[:2] + (
        "out beyond",
    )
    zone = edit.switch("switch_zone_relation", cases=[([],), ([],), ([],)])
    assert cat.ports(zone).arms == ("relation 1", "relation 2", "relation 3")


@pytest.mark.parametrize(
    ("weights", "want"),
    [
        ([8, 24], (8, 24)),
        ([8, 8], (8, 24)),
        ([16, 16], (16, 16)),
        ([0, 32], (0, 32)),
        ([0, 255], (0, 32)),
        ([8, 255, 8], (8, 24, 0)),
        ([255], (32,)),
        ([40, 0], (32, 0)),
    ],
)
def test_shares(weights, want):
    assert cat.shares(weights) == want
    assert sum(want) == 32


def test_pick_ports():
    body = [insn("wait", 1)]
    pick = edit.random([(8, body), (8, body), (16, body)])
    assert cat.chance(pick) is None and cat.title(pick) == "Pick one"
    assert cat.ports(pick).arms == ("8/32", "8/32", "16/32")
    c = edit.random([(8, body)])
    assert cat.chance(c) == cat.Chance(0, 8)
    assert cat.title(c) == "Chance 8/32"
    assert cat.ports(c) == cat.Ports(None, ("8/32", "otherwise"))
    lead = ir.Block(
        BY_NAME["random"], {}, [], [ir.Arm(1, {"weight": 24}, []), ir.Arm(1, {"weight": 8}, body)]
    )
    assert cat.chance(lead) == cat.Chance(1, 8)
    assert cat.ports(lead).arms == ("otherwise", "8/32")
    shifted = edit.random([(5, body)], shift=5)
    assert cat.chance(shifted) == cat.Chance(0, 5) and cat.title(shifted) == "Chance 5/32"
    both = edit.random([(16, body), (16, body)])
    assert cat.chance(both) is None
    empty = edit.random([(16, [])])
    assert cat.chance(empty) is None
    never = edit.random([(0, body), (32, [])])
    assert cat.ports(never).arms == ("never", "otherwise")
    assert cat.confidence(c) is Confidence.HIGH and cat.confidence(shifted) is Confidence.MEDIUM


def test_steps_draw():
    assert cat.steps_draw(insn("rng_step")) and cat.steps_draw(insn("rng_step2"))
    assert not cat.steps_draw(insn("wait", 1)) and not cat.steps_draw(edit.random([(8, [])]))
    assert cat.title(insn("rng_step2")) == "Step the draw"


# ---- the header


def test_end_kinds():
    assert set(cat.END_KIND_INFO) == set(END_KINDS)
    for code, k in cat.END_KIND_INFO.items():
        assert k.code == code and k.name == END_KINDS[code] and k.title and k.meaning
        if k.name.startswith("int_0x"):
            assert k.closes == int(k.name[4:], 16)
    assert cat.end_kind(0xFC).closes == 0x20 and cat.end_kind(0xF7).closes == 0
    assert cat.end_kind(0x77).title == "Ignored (119)" and cat.end_kind(0x77).name == "119"
    assert {k.closes for k in cat.END_KIND_INFO.values()} - {0} == set(cat.INTERRUPTS)


def test_interrupts():
    assert list(cat.INTERRUPTS) == [0x80, 0x40, 0x20, 0x10, 0x08, 0x04, 0x02]
    assert {i.list_name for i in cat.INTERRUPTS.values()} == {s.name for s in SLOTS if s.interrupt}
    for bit, i in cat.INTERRUPTS.items():
        assert (
            i.bit == bit
            and slot(next(f for f, s in enumerate(SLOTS) if s.name == i.list_name)).interrupt == bit
        )
        assert i.title and i.entry and i.meaning
        end = cat.end_of(bit)
        assert end is not None and end.closes == bit
    assert (
        cat.interrupt(0x20).entry == "On notice"
        and cat.interrupt(0x20).confidence is Confidence.HIGH
    )
    assert (
        cat.interrupt(0x08).entry == "On enrage"
        and cat.interrupt(0x08).confidence is Confidence.HIGH
    )
    assert cat.interrupt(0x10).confidence is Confidence.LOW
    assert cat.interrupt(0x02).confidence is Confidence.LOW
    assert cat.interrupt(0x40).confidence is Confidence.LOW
    assert "empty" in cat.interrupt(0x04).meaning
    assert cat.end_of(0x20).name == "notice"  # type: ignore[union-attr]
    assert cat.end_of(0x01) is None


def test_lists():
    for f, s in enumerate(SLOTS):
        info = cat.list_info(f)
        assert info.name == s.name and cat.list_info(s.name) == info
        assert info.group and info.title and info.meaning
        assert info.group in cat.GROUPS
        assert (info.group == "Events") == bool(s.interrupt)
        assert (info.group == "Data") == (s.kind == "data")
    assert cat.list_info("base").title == "Base routines"
    assert cat.list_info("base").group == "Decisions"
    assert cat.list_info("ret").title == "Calls with return"
    assert cat.list_info("loop").group == "Loops"
    assert cat.list_info("walk").group == "Routes"
    assert cat.list_info("int_0x20").title == "On notice"
    deep = cat.list_info(FIRST_DEEP + 3)
    assert (deep.name, deep.group, deep.title) == ("deep3", "Deep calls", "Deep calls 3")
    assert cat.list_info("deep3") == deep
    assert deep.group in cat.GROUPS


def test_entry_points():
    pts = {p.title: (p.list_name, p.index) for p in cat.ENTRY_POINTS}
    assert pts["Top"] == ("base", 0)
    assert pts["On notice"] == ("int_0x20", 0) and pts["On enrage"] == ("int_0x08", 0)
    assert len(cat.ENTRY_POINTS) == 1 + len(cat.INTERRUPTS)
    top = cat.entry_point("base", 0)
    assert top is not None and top.title == "Top" and top.confidence is Confidence.HIGH
    assert cat.entry_point("base", 1) is None and cat.entry_point("call", 0) is None
    notice = cat.entry_point("int_0x20", 0)
    assert notice is not None and notice.title == "On notice"


# ---- the game


@pytest.fixture(scope="module")
def progs(game):
    """Every species row of every overlay, lifted."""
    out: dict[int, ir.Program] = {}
    for em in EM_SPECIES:
        out.update(lift.lift_all(script.Scripts(game, em)))
    return out


def test_game_nodes(progs):
    assert len(progs) == 42
    used: Counter[Kind] = Counter()
    for p in progs.values():
        for r in p.routines():
            todo = list(r.body)
            while todo:
                n = todo.pop()
                used[cat.kind_of(n)] += 1
                assert cat.title(n) and cat.confidence(n) in Confidence
                assert cat.summary(n) and "{" not in cat.summary(n)
                for f in cat.operands(n):
                    assert f.text
                if isinstance(n, ir.Block):
                    ps = cat.ports(n)
                    assert len(ps.arms) == len(n.arms) and all(ps.arms)
                    assert (ps.head is not None) == (n.op.shape in {Shape.IF, Shape.REPEAT})
                    for a in n.arms:
                        assert all(f.text for f in cat.arm_operands(n, a))
                        todo += a.body
                    todo += n.head
    assert used.keys() == set(Kind)
    assert used[Kind.MOVE] > 0 and used[Kind.PICK] > 0 and used[Kind.END] > 0


def test_game_ops(progs):
    ops = {
        n.op.name for p in progs.values() for n in p.nodes() if isinstance(n, ir.Insn | ir.Block)
    }
    assert ops <= set(cat.ENTRIES) and len(ops) > 100
    for p in progs.values():
        for n in p.nodes():
            if isinstance(n, ir.Block) and n.op.shape is Shape.RANDOM:
                ws = [a.args["weight"] for a in n.arms]
                assert cat.shares(ws) == tuple(ws) and cat.chance(n) is None
    for p in progs.values():
        for h in p.headers():
            for f in range(len(h.fields)):
                assert cat.list_info(f).group


# ---- words


def test_words():
    w = cat.Words(vars={4: "in_hunt_area"}, maps={141: (3,), 149: (3,), 99: (11, 12)})
    assert [w.var(v) for v in (4, 3, 0, 10)] == [
        "in_hunt_area",
        "var 3",
        "every variable",
        "the destination",
    ]
    assert w.area(141) == "st141" and w.area(99) == "Snowy area 1" and w.area(0xFFFF) == "any area"
    assert w.where([141, 149]) == "Map 3" and w.where([141, 99]) == "" and w.where([7]) == ""
    assert cat.summary(insn("set_var", 4, 1), w) == "in_hunt_area = 1"
    assert cat.summary(edit.if_("if_var", 4, 1, then=[]), w) == "in_hunt_area is 1"
    sec = edit.switch("switch_section", cases=[(141, []), (149, [])])
    assert cat.summary(sec, w) == "Own area: Map 3"
    maps = edit.switch("switch_area", cases=[(11, []), (3, [])])
    assert cat.ports(maps).arms == ("Snowy Mountains (day)", "Map 3")


def test_gloss():
    w = cat.Words(vars={4: "in_hunt_area"}, maps={141: (3,)}, limits=((450.0,) * 4,) * 2)
    assert cat.gloss(insn("set_var", 4, 1), w) == "in_hunt_area = 1"
    assert cat.gloss(insn("set_var", 3, 1), w) == ""  # the line says it all
    assert cat.gloss(insn("wait", 30), w) == ""
    assert cat.gloss(insn("enter", 3, 0, 0), w) == ""  # what it plays is the caller's
    assert cat.gloss(insn("end", 2), w) == "Return from deep call"
    assert cat.gloss(insn("end", 1), w) == ""  # `end return` says it
    sec = edit.switch("switch_section", cases=[(141, [])], default=[])
    assert cat.gloss(sec, w) == "Map 3"
    assert [cat.gloss(a, w, sec) for a in sec.arms] == ["st141", ""]
    bucket = edit.switch("switch_range", cases=[([],), ([],)])
    assert [cat.gloss(a, w, bucket) for a in bucket.arms] == ["≤ 450", "beyond 450"]
    pick = ir.Block(BY_NAME["random"], {}, [], [ir.Arm(1, {"weight": w}, []) for w in (8, 255)])
    assert [cat.gloss(a, w, pick) for a in pick.arms] == ["", "24/32"]  # 255 takes the rest


def test_hint():
    def routine(*body):
        return ir.Routine("r", list(body))

    w = cat.Words(maps={141: (3,)})
    assert cat.hint(routine(insn("end", 1))) == "does nothing"
    assert cat.hint(routine(insn("enter", 3, 0, 0), insn("end", 1))) == "plays (3, 0)"
    sec = edit.switch("switch_section", cases=[(141, [insn("enter", 0, 1, 0)])])
    assert cat.hint(routine(sec, insn("end", 1)), w) == "by own area in Map 3 · plays (0, 1)"
    pick = edit.random([(16, [insn("enter", 0, 1, 0)]), (16, [insn("enter", 0, 2, 0)])])
    assert cat.hint(routine(pick)) == "picks 1 of 2 · plays (0, 1), (0, 2)"
    walk = insn("walk", 1, 3, 5, 0, 1)
    assert cat.hint(routine(walk, insn("end", 2))) == "walks a route"
