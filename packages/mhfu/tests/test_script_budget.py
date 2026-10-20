# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu.em import script
from mhfu.em.script import asm, budget, check, edit, ir, lift
from mhfu.em.script.budget import Budget
from mhfu.em.script.opcodes import FIRST_DEEP, LISTS
from mhfu.files import EM_SPECIES

BASE = 0x0010_0000


def nop():
    return edit.insn("nop")


def nops(n):
    return [nop() for _ in range(n)]


def enter(main=0, sub=1, gate=0):
    return edit.insn("enter", main, sub, gate)


def native(p):
    """Give a program the addresses the game has: ids, `va`, `pc`, `close` and `end` from its
    own layout."""
    for k, n in enumerate(p.nodes()):
        n.id = n.id or f"n{k}"
    img = asm.assemble(p)
    for it in p.items:
        if not isinstance(it, ir.Blob):
            it.va = BASE + img.items[it.name][0]
    for n in p.nodes():
        n.pc = BASE + img.nodes[n.id][0]
        if isinstance(n, ir.Block):
            n.close = BASE + img.closes[n.id]
    p.end = BASE + len(img.data)
    return p


def build(routines, lists=None):
    """A native program of routines by name and the entries of each list by slot name; the
    base list defaults to the first routine."""
    lists = lists or {"base": [next(iter(routines))]}
    items = [ir.Routine(name, body) for name, body in routines.items()]
    fields = [None] * FIRST_DEEP
    for name, entries in lists.items():
        at = LISTS[name] if name in LISTS else FIRST_DEEP + int(name.removeprefix("deep"))
        fields += [None] * (at + 1 - len(fields))
        fields[at] = f"{name}_l"
        items.append(ir.Table(f"{name}_l", entries))
    items.append(ir.Header("h0", fields, tuple(range(17))))
    return native(ir.Program(1, BASE, items))


_ROUTINES = {"call": "call_0", "ret": "ret_0", "deep0": "deep0_0", "loop": "loop_0"}


def _lists_base(n):
    return {"base": [f"base_{i}" for i in range(n)]}


def _lists(*names):
    """The base list and a list holding one routine for each name, as `build` takes them."""
    return {"base": ["base_0"], **{n: [_ROUTINES[n]] for n in names}}


def count(routines, lists=None, entry="base_0"):
    return Budget(build(routines, lists)).runs[entry].count


# ---- how the game counts


def test_enter_only():
    assert count({"base_0": [enter()]}) == 1


@pytest.mark.parametrize(
    ("body", "want"),
    [
        # opener + body + the marker it falls onto, or the opener alone when it is skipped
        (lambda: edit.if_("if_latch", then=[nop(), nop()], else_=[nop()]), 5),
        (lambda: edit.if_("if_latch", then=[nop(), nop()]), 5),
        (lambda: edit.if_("if_latch", then=[], else_=[nop(), nop()]), 5),
        (lambda: edit.if_("if_latch", then=[nop()], else_=[]), 4),
        (
            lambda: edit.switch(
                "switch_species",
                cases=[(1, [nop()]), (2, [nop(), nop()])],
                default=[nop(), nop(), nop()],
            ),
            6,
        ),
        (lambda: edit.switch("switch_species", cases=[(1, [nop()]), (2, [nop(), nop()])]), 5),
        (
            lambda: edit.random([(10, [nop()]), (10, [nop(), nop()]), (12, [nop(), nop(), nop()])]),
            6,
        ),
        (lambda: edit.repeat(3, [nop(), nop()]), 11),
    ],
    ids=[
        "if_else",
        "if",
        "if_empty_then",
        "else_empty",
        "switch",
        "no_default",
        "random",
        "repeat",
    ],
)
def test_blocks(body, want):
    assert count({"base_0": [body(), enter()]}) == want  # the enter is 1 more


def test_path_ids():
    p = build({"base_0": [nop(), edit.if_("if_latch", then=[nop()]), enter()]})
    run = Budget(p).runs["base_0"]
    assert (run.path, run.ends, run.count) == (("n0", "n1", "n2", "n1", "n3"), "enter", 5)


def test_calls():
    base = [edit.insn("call", 0), nop(), enter()]
    assert (
        count({"base_0": base, "call_0": [nop(), nop(), edit.insn("end", 1)]}, _lists("call")) == 6
    )
    deep = {
        "base_0": base,
        "call_0": [edit.insn("call_deep", 0, 0), nop(), edit.insn("end", 1)],
        "deep0_0": [nop(), edit.insn("end", 2)],
    }
    assert count(deep, _lists("call", "deep0")) == 8
    ret = {
        "base_0": [edit.insn("call_ret", 0), nop(), enter()],
        "ret_0": [nop(), nop(), edit.insn("end", 3)],
    }
    assert count(ret, _lists("ret")) == 6


def test_goto_and_fall_off():
    two = {"base_0": [edit.insn("goto", 1)], "base_1": [nop(), enter()]}
    assert count(two, {"base": ["base_0", "base_1"]}) == 3
    run = Budget(build({"base_0": [nop()]})).runs["base_0"]
    assert (run.count, run.ends) == (2, "reset")  # the byte past the end is dispatched


def test_unbounded():
    spin = {"base_0": [enter()], "base_1": [edit.insn("goto", 1)]}
    run = Budget(build(spin, {"base": ["base_0", "base_1"]})).runs["base_1"]
    assert (run.unbounded, run.ends, run.count) == (True, "unbounded", budget.LIMIT + 1)


def test_restart_once():
    body = [edit.if_("if_latch", then=[nop(), nop(), enter()]), nop(), edit.insn("end", 0)]
    b = Budget(build({"base_0": body}))
    # a first pass that restarts (open, nop, end), then one that yields (open, nop, nop, enter)
    assert (b.runs["base_0"].count, b.direct["base_0"].count) == (7, 4)
    assert b.runs["base_0"].path == ("n0", "n4", "n5", "n0", "n1", "n2", "n3")
    assert b.direct["base_0"].path == ("n0", "n1", "n2", "n3")


def test_interrupt_end_restarts_base():
    routines = {
        "base_0": [enter(), nop(), nop(), nop(), enter()],
        "int_0": [nop(), edit.insn("end", 0xFC)],
    }
    b = Budget(build(routines, {"base": ["base_0"], "int_0x20": ["int_0"]}))
    run = b.runs["int_0"]
    # not at the resume point after the first enter, which is 4 more: base[0] again
    assert (run.own, run.count, run.ends) == (2, 3, "int")
    assert run.path == ("n5", "n6", "n0")


def test_resume_points():
    p = build({"base_0": [enter(0, 1), nop(), enter(0, 2)]})
    b = Budget(p)
    assert b.runs["base_0"].count == 1
    assert b.runs["after n0"].count == 2  # nop, enter


def test_resume_keeps_the_stack():
    routines = {
        "base_0": [edit.insn("call", 0), nop(), enter()],
        "call_0": [enter(), nop(), edit.insn("end", 1)],
    }
    b = Budget(build(routines, _lists("call")))
    assert b.runs["base_0"].count == 2  # call, enter
    assert b.runs["after n3"].count == 4  # nop, end return, then back in the caller: nop, enter


def test_route_ops_may_be_unarmed():
    routines = {
        "base_0": [edit.insn("route_run"), enter()],
        "loop_0": [nop(), edit.insn("end", 0xFB)],
    }
    # armed: route_run, nop, end loop_next, enter; unarmed: route_run, enter
    assert count(routines, {"base": ["base_0"], "loop": ["loop_0"]}) == 4


# ---- new code


def test_new_runs():
    p = build({"base_0": [nop(), enter()]})
    b = Budget(p)
    assert b.new == {}
    edit.put(p, "base_0", [nop()])
    b = Budget(p)
    assert (b.runs["base_0"].count, b.new["base_0"].count, b.new["base_0"].new) == (3, 3, True)
    assert b.new["base_0"].path[0] == "n2"


# ---- contexts


def test_contexts():
    routines = {
        "base_0": [edit.insn("call", 0), enter(), edit.insn("end", 0)],
        "call_0": [edit.insn("call_deep", 0, 0), edit.insn("end", 1)],
        "deep0_0": [edit.insn("end", 2)],
        "int_0": [edit.insn("end", 0xFC)],
        "orphan": [edit.insn("end", 1)],
    }
    lists = {"base": ["base_0"], "call": ["call_0"], "deep0": ["deep0_0"], "int_0x20": ["int_0"]}
    got = budget.contexts(build(routines, lists))
    assert got["base_0"] == {"base"}
    assert got["call_0"] == {"base", "call"}  # a call's return label stays with the callee
    assert got["deep0_0"] == {"base", "deep"}
    assert got["int_0"] == {"int_0x20"} and got["orphan"] == set()


def test_needs():
    assert [budget.needs(k) for k in (0, 1, 2, 3, 0xFC, 0xF8, 0xFF, 99)] == [
        "base",
        "call",
        "deep",
        "ret",
        "int_0x20",
        "int_0x08",
        "walk",
        None,
    ]


# ---- findings on edited code


def world():
    """A hub with a random pick and a call, the pieces most edits touch."""
    base = [
        edit.if_("if_latch", then=[enter(0, 1)]),
        edit.random([(16, [enter(1, 0)]), (16, [])]),
        edit.insn("call", 0),
        edit.insn("end", 0),
    ]
    call = [nop(), edit.insn("end", 1)]
    return build({"base_0": base, "call_0": call}, _lists("call"))


def findings(p, code=None):
    got = check.check(edit.resolve(p))
    return [f for f in got if f.code == code] if code else got


def codes(p):
    return [(f.level, f.code) for f in findings(p)]


def test_world_is_clean():
    p = world()
    assert codes(p) == []
    edit.insert(p, "n1", [nop()])
    edit.put(p, "call_0", [nop()])
    edit.insert(p, "n3", [nop()])
    assert codes(p) == []  # a valid edit passes
    q = world()
    edit.put(q, "base_0", [edit.if_("if_enraged", then=[enter(3, 1)])])
    assert codes(q) == []


def only_ops(bodies, passes, extra=0):
    """The longest run through the new code and the OPS_PER_RUN finding of a repeat that runs
    `passes` times over `bodies` nops, with `extra` more nops after it."""
    p = build({"base_0": [enter()]})
    edit.put(p, "base_0", [edit.repeat(passes, nops(bodies)), *nops(extra)])
    got = [f for f in findings(p) if f.code == "OPS_PER_RUN"]
    return Budget(p).new["base_0"].count, [(f.level, f.node, f.item) for f in got]


def test_ops_per_run():
    assert only_ops(4, 255) == (1277, [("error", "n1", "base_0")])
    assert only_ops(4, 180) == (902, [("warning", "n1", "base_0")])
    assert only_ops(4, 100) == (502, [])


def test_ops_run_limit_is_the_999th():
    assert only_ops(4, 199, 2) == (999, [("warning", "n1", "base_0")])
    assert only_ops(4, 199, 3) == (1000, [("error", "n1", "base_0")])


def test_ops_one_finding_per_new_node():
    p = world()
    edit.put(p, "call_0", [edit.repeat(255, nops(4))])
    assert [f.code for f in findings(p)] == ["OPS_PER_RUN"]  # not one per entry and resume point


def test_ops_unbounded():
    p = world()
    edit.add(p, ir.Routine("spin", [edit.insn("goto", 1)]))
    edit.set_slot(p, "base_l", 1, "spin")
    edit.put(p, "base_0", [edit.insn("goto", 1)])
    f = findings(p, "OPS_PER_RUN")[0]
    assert f.level == "error" and "go round" in f.text


def test_falls_off():
    p = world()
    edit.add(p, ir.Routine("loose", [enter()]))
    edit.put(p, "base_0", [edit.insn("call", ir.Sym("loose"))])
    assert [(f.node, f.item) for f in findings(p, "FALLS_OFF")] == [("n10", "loose")]
    q = world()
    edit.replace(q, "n9", nop())  # the native end becomes a new nop
    assert [(f.node, f.item) for f in findings(q, "FALLS_OFF")] == [("n10", "call_0")]
    r = world()
    edit.replace(r, "n9", edit.if_("if_latch", then=[edit.insn("end", 1)]))
    assert [f.node for f in findings(r, "FALLS_OFF")] == [("n10")]  # a new if lets it through
    s = world()
    edit.replace(s, "n9", edit.random([(16, [edit.insn("end", 1)]), (16, [])]))
    assert [f.node for f in findings(s, "FALLS_OFF")] == ["n13"]  # the empty new arm
    t = world()
    edit.remove(t, "n9")
    edit.put(t, "call_0", [nop()])  # the end is gone and the routine has new code in it
    assert [f.node for f in findings(t, "FALLS_OFF")] == ["n8"]
    u = world()
    edit.remove(u, "n9")
    assert findings(u, "FALLS_OFF") == []  # nothing new: the decoder says it
    assert [f.code for f in findings(u)] == ["DECODE"]


def test_restart_loop():
    forced = world()
    edit.put(forced, "base_0", [nop(), edit.insn("end", 0)])
    assert [(f.level, f.node) for f in findings(forced, "RESTART_LOOP")] == [("error", "n11")]
    cond = world()
    edit.put(cond, "base_0", [edit.if_("if_enraged", then=[edit.insn("end", 0)])])
    assert [(f.level, f.node) for f in findings(cond, "RESTART_LOOP")] == [("warning", "n11")]
    (rs,) = Budget(edit.resolve(cond)).restarts
    assert rs.forced is False and rs.path == ("n10", "n11")
    callee = world()
    edit.put(callee, "call_0", [edit.insn("goto", 0)])  # a goto 0 restarts too, from a call
    assert [f.level for f in findings(callee, "RESTART_LOOP")] == ["warning"]


def test_native_restart_is_not_new_code():
    p = world()
    edit.put(p, "base_0", [nop()])  # the native end base restarts too, and it is native
    assert findings(p, "RESTART_LOOP") == []


def test_dead_code():
    p = world()
    edit.insert(p, "n9", [nop()], after=True)
    assert [(f.level, f.node, f.item) for f in findings(p, "DEAD_CODE")] == [
        ("warning", "n10", "call_0")
    ]
    q = world()
    edit.insert(q, "n1", [edit.insn("end", 0), nop()], after=True)
    assert [f.node for f in findings(q, "DEAD_CODE")] == ["n11"]  # inside a block
    ok = world()
    edit.put(ok, "base_0", [edit.if_("if_latch", then=[edit.insn("end", 0)], else_=[nop()])])
    assert findings(ok, "DEAD_CODE") == []
    both = world()
    ends = [edit.insn("end", 0)]
    edit.put(both, "base_0", [edit.if_("if_latch", then=ends, else_=[edit.insn("end", 0)]), nop()])
    assert [f.node for f in findings(both, "DEAD_CODE")] == ["n14"]  # every way through ends
    r = world()
    edit.put(r, "base_0", [edit.random([(16, [edit.insn("end", 0)]), (16, [])]), nop()])
    assert findings(r, "DEAD_CODE") == []  # one branch goes on


def test_native_dead_code_is_left_alone():
    sw = edit.switch("switch_species", cases=[(1, [edit.insn("end", 0), nop()])])
    p = build({"base_0": [sw, edit.insn("end", 0)]})
    assert check.check(p) == []
    edit.put(p, "base_0", [nop()])
    assert findings(p, "DEAD_CODE") == []
    # the defensive end base after gotos, with an edit inside the block
    gotos = edit.if_("if_latch", then=[edit.insn("goto", 1)], else_=[edit.insn("goto", 1)])
    q = build(
        {"base_0": [gotos, edit.insn("end", 0)], "base_1": [enter(), edit.insn("end", 0)]},
        _lists_base(2),
    )
    assert check.check(q) == []
    edit.insert(q, "n1", [nop()])
    assert findings(q, "DEAD_CODE") == []
    r = build(
        {"base_0": [edit.if_("if_latch", then=[enter()], else_=[edit.insn("end", 0)]), nop()]}
    )
    edit.replace(r, "n1", edit.insn("end", 0))  # the new end makes the if end: the nop is dead
    assert [f.node for f in findings(r, "DEAD_CODE")] == ["n4"]


def test_end_kind_context():
    def bad(kind, routine="base_0"):
        p = world()
        edit.put(p, routine, [edit.insn("end", kind)])
        return [f.node for f in findings(p, "END_KIND_CONTEXT")]

    assert bad(0xFC) == ["n10"]  # notice outside int 0x20
    assert bad(2) == ["n10"]  # return2 outside a call_deep
    assert bad(7) == ["n10"]  # a kind the game ignores
    assert bad(1, "call_0") == [] and bad(0, "call_0") == []
    p = world()
    edit.add(p, ir.Routine("spin", [enter(), edit.insn("end", 2)]))
    edit.put(p, "base_0", [edit.insn("call", ir.Sym("spin"))])
    assert [f.item for f in findings(p, "END_KIND_CONTEXT")] == ["spin"]  # a call routine


def test_enter_fields():
    def bad(*args):
        p = world()
        edit.put(p, "base_0", [edit.insn("enter", *args)])
        return {f.code for f in findings(p)} - {"DEAD_CODE", "DECODE"}

    assert bad(3, 0, 1) == {"GATE_NONZERO"}
    assert bad(8, 0, 0) == {"PAIR_RANGE"}
    assert bad(255, 0, 7) == {"GATE_NONZERO", "PAIR_RANGE"}
    assert bad(7, 255, 0) == set()


def test_weight_zero():
    p = world()
    edit.put(p, "base_0", [edit.random([(0, [enter(0, 1)]), (32, [])])])
    assert [(f.level, f.node) for f in findings(p, "WEIGHT_ZERO")] == [("warning", "n11")]
    run = Budget(edit.resolve(p)).new["base_0"]
    assert "n12" not in run.path  # the branch that never runs is not on the worst path


def test_correlated_draw():
    p = world()
    edit.insert(p, "n2", [edit.random([(16, [enter(2, 0)]), (16, [])])], after=True)
    assert [(f.level, f.item) for f in findings(p, "CORRELATED_DRAW")] == [("warning", "base_0")]
    q = world()
    edit.put(q, "base_0", [edit.random([(16, [enter(2, 0)]), (16, [])])])
    assert findings(q, "CORRELATED_DRAW") == []  # the native draw after it is not the edit's
    r = world()
    edit.put(r, "call_0", [edit.random([(16, [enter(2, 0)]), (16, [])])])
    assert [f.item for f in findings(r, "CORRELATED_DRAW")] == ["call_0"]  # after the hub's pick


def test_correlated_draw_edited_random():
    def two():
        pick = edit.random([(16, []), (16, [])])
        return build({"base_0": [edit.random([(16, []), (16, [])]), pick, enter()]})

    p = two()
    edit.insert(p, "n4", [nop()])  # into an arm of the second pick: its odds are as they were
    assert findings(p, "CORRELATED_DRAW") == []
    q = two()
    q.item("base_0").body[1].arms[0].pc = None  # a branch of it edited in place
    assert [f.node for f in findings(q, "CORRELATED_DRAW")] == ["n3"]
    r = two()
    r.item("base_0").body[0].arms[0].pc = None  # and of the first, which has no draw before it
    assert findings(r, "CORRELATED_DRAW") == []


def test_correlated_draw_in_a_repeat():
    p = world()
    edit.put(p, "base_0", [edit.repeat(2, [edit.random([(16, []), (16, [])])])])
    assert [f.node for f in findings(p, "CORRELATED_DRAW")] == ["n11"]  # the second pass


def test_a_restart_rerolls():
    p = world()
    edit.put(p, "base_0", [edit.random([(16, []), (16, [])])])
    assert findings(p, "CORRELATED_DRAW") == []  # native pick, `end base`, the new draw again


def test_unresolved_labels_skip_run_checks():
    p = world()
    edit.add(p, ir.Routine("spin", [edit.insn("end", 1)]))
    edit.put(p, "base_0", [edit.insn("call", ir.Sym("spin"))])
    assert [f.code for f in check.check(p)] == ["UNRESOLVED"]


# ---- the game


@pytest.fixture(scope="module")
def rows(game):
    """Every overlay's species rows, lifted: {em: (Scripts, {row: Program})}."""
    out = {}
    for em in EM_SPECIES:
        sc = script.Scripts(game, em)
        out[em] = (sc, lift.lift_all(sc))
    return out


def test_game_rows_stay_in_budget(rows):
    worst = (0, 0, "")
    for _, ps in rows.values():
        for row, p in ps.items():
            b = Budget(p)
            top = b.worst
            assert top is not None and not any(r.unbounded for r in b.runs.values()), row
            assert b.new == {} and check.check(p) == [], row
            worst = max(worst, (top.count, row, top.entry))
    assert worst == (162, 45, "h1_int_0x10_0")  # an interrupt, then the base run it cuts in on
    assert worst[0] < budget.LIMIT


def test_game_em75(rows):
    p = rows[75][1][75]
    b = Budget(p)
    got = {k: (r.count, r.own) for k, r in b.runs.items() if not k.startswith("after")}
    # the longest runs per routine; routes may be armed or not, which the first counts of the
    # planner (base_1 20) did not
    assert got["base_0"] == (85, 85) and got["base_2"] == (26, 26) and got["base_3"] == (19, 19)
    assert got["int_0x20_0"] == (12, 12) and got["int_0x08_0"] == (5, 5)
    assert got["int_0x02_0"] == (90, 5)  # and then the longest base run it can cut in on
    assert b.direct["base_0"].count == 44 and b.direct["base_1"].count == 23
    assert b.direct["base_2"].count == 26
    run = b.runs["base_0"]
    assert (run.ends, run.path[0]) == ("enter", p.item("base_0").body[0].id)
    assert run.count - b.direct["base_0"].count == 41  # a restart pass that yields nothing


def test_game_edits_on_em75(rows):
    sc = rows[75][0]
    p = lift.lift(sc, 75)
    edit.put(p, "base_0", [edit.if_("if_enraged", then=[enter(3, 1)])])
    assert check.check(edit.resolve(p)) == []
    b = Budget(edit.resolve(p))
    assert b.new["base_0"].count == b.runs["base_0"].count == 87  # the guard, once a pass
    q = lift.lift(sc, 75)
    edit.put(q, "base_0", [edit.repeat(255, nops(4))])
    (f,) = check.check(edit.resolve(q))
    assert (f.level, f.code, f.item) == ("error", "OPS_PER_RUN", "base_0")
    assert "a run from" in f.text and "stops a run at 999" in f.text
    r = lift.lift(sc, 75)
    edit.put(r, "base_0", [edit.insn("end", 0)])
    assert {f.code for f in check.check(edit.resolve(r))} >= {"RESTART_LOOP", "DEAD_CODE"}
