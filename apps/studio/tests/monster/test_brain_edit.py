# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Brain editor's gestures, on a small native program and on em75's own."""

from __future__ import annotations

import collections
import hashlib
import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from mhfu.em import script
from mhfu.em.script import asm, brainfile, build, edit, editset, ir, parse, source
from mhfu.em.script import catalogue as cat
from mhfu.em.script.check import is_terminal
from mhfu.em.script.edit import EditError
from mhfu.em.script.editset import Anchor, EditSet, ListEdit
from mhfu.em.script.opcodes import Shape, operands
from mhfu.files import Extracted
from mhfu_studio.monster import brain_edit as be
from mhfu_studio.monster.brain_edit import Gesture, Place

BASE = 0x0010_0000


def enter(main: int, sub: int) -> ir.Insn:
    return edit.insn("enter", main, sub, 0)


def native(p: ir.Program) -> ir.Program:
    """Give a program the addresses, tails and ids a lift has: `{row}:{pc:08X}`."""
    for k, n in enumerate(p.nodes()):
        n.id = f"t{k}"
    img = asm.assemble(p)
    order = sorted(img.items.values())
    for r in p.routines():
        off, size = img.items[r.name]
        nxt = next((o for o, _ in order if o > off), len(img.data))
        r.tail += bytes(nxt - off - size)
    img = asm.assemble(p)
    for it in p.items:
        if not isinstance(it, ir.Blob):
            it.va = BASE + img.items[it.name][0]
    for n in p.nodes():
        n.pc = BASE + img.nodes[n.id][0]
        if isinstance(n, ir.Block):
            n.close = BASE + img.closes[n.id]
    for n in p.nodes():
        n.id = f"{p.row}:{n.pc:08X}"
    p.end = BASE + len(img.data)
    p.sha1 = hashlib.sha1(asm.assemble(p).data).hexdigest()
    return p


def world(row: int = 1) -> ir.Program:
    """Routines of every shape of node, two headers that share their lists."""
    top = edit.if_(
        "if_latch", then=[enter(0, 1), enter(0, 2)], else_=[edit.insn("wait", 3), enter(0, 3)]
    )
    pick = edit.random([(16, [enter(1, 0)]), (8, [enter(1, 1), enter(1, 2)])])
    sw = edit.switch(
        "switch_hp", cases=[(1, [enter(2, 0)]), (2, [enter(2, 1)])], default=[enter(2, 2)]
    )
    deep = edit.if_("if_aware", then=[edit.random([(20, [enter(4, 0)]), (12, [enter(4, 1)])])])
    fields = ["base_l", "call_l", *[None] * 13]
    return native(
        ir.Program(
            row,
            BASE,
            [
                ir.Routine("base_0", [top, pick, edit.insn("call", 0), edit.insn("end", 0)]),
                ir.Routine("base_1", [enter(5, 0), enter(5, 1), enter(5, 2), edit.insn("goto", 0)]),
                ir.Routine(
                    "call_0", [sw, deep, edit.repeat(3, [enter(3, 0)]), edit.insn("end", 1)]
                ),
                ir.Routine(
                    "call_1",
                    [
                        enter(6, 0),
                        enter(6, 1),
                        enter(6, 2),
                        edit.switch("switch_range", cases=[([enter(6, 3)],), ([enter(6, 4)],)]),
                        edit.insn("end", 1),
                    ],
                ),
                ir.Table("base_l", ["base_0", "base_1"]),
                ir.Table("call_l", ["call_0", "call_1", None]),
                ir.Header("h0", list(fields), tuple(range(5))),
                ir.Header("h1", list(fields), tuple(range(5, 17))),
            ],
        )
    )


@pytest.fixture(scope="module")
def em75(mhfu_data: Path) -> ir.Program:
    return script.lift.lift(script.Scripts(Extracted.find(mhfu_data), 75), 75)


@pytest.fixture(params=["world", "em75"])
def lift(request: pytest.FixtureRequest) -> ir.Program:
    """The same tests on both: the native program, and the game's."""
    return world() if request.param == "world" else request.getfixturevalue("em75")  # type: ignore[no-any-return]


def snap(p: ir.Program) -> dict[str, list[str]]:
    """A program as text by routine: what it is, with no ids."""
    return {r.name: [ln.text for ln in source.render_nodes(r.body)] for r in p.routines()}


def copy_of(p: ir.Program) -> ir.Program:
    """A program to apply `edit` functions to, as the oracle for a gesture."""
    items: list[ir.Item] = [
        ir.Routine(i.name, edit.copy(i.body), i.tail, i.va) if isinstance(i, ir.Routine) else i
        for i in p.items
    ]
    return ir.Program(p.row, p.base, items, p.end, p.sha1)


class Rig:
    """A brain file over a lift, and what each gesture must leave behind."""

    def __init__(self, lift: ir.Program) -> None:
        self.lift = lift
        self.bf = brainfile.new(75)
        self.row = lift.row
        self.lift_ids: set[str] = set()

    @property
    def es(self) -> EditSet | None:
        return self.bf.edits(self.row)

    @property
    def program(self) -> ir.Program:
        es = self.es
        return editset.apply(self.lift, es).program if es else self.lift

    def snap(self) -> dict[str, list[str]]:
        return snap(self.program)

    def model(self) -> ir.Program:
        return copy_of(self.program)

    def run(self, g: Gesture, want: ir.Program | None = None, *, builds: bool = True) -> None:
        """Run a gesture; the program is `want`'s, the file in normal form and it lays out. With
        `builds` the whole build is clean too."""
        g.edit(self.bf, self.row, self.lift)
        if want is not None:
            assert self.snap() == snap(want), g.label
        self.check(builds)

    def check(self, builds: bool = False) -> None:
        es = self.es
        if es is None:
            return
        got = editset.apply(self.lift, es)
        assert [f for f in got.findings if f.level == "error"] == []
        assert es == es.copy().prune()
        assert all(i.nodes or i.arm for i in es.inserts.values())
        again = brainfile.loads(brainfile.dumps(self.bf)).edits(self.row)
        assert again is not None and editset.equal(again, es)
        asm.assemble(got.program)
        new = [x for i in es.inserts.values() for x in edit.everything(i.nodes or [i.arm])]
        new += [x for body in es.routines.values() for x in edit.everything(body)]
        assert [x for x in new if x.pc is not None or x.origin is not None or x.id] == []
        if builds:
            built = build.Built.of(self.lift, es)
            assert [f for f in built.findings if f.level == "error"] == []

    def refuse(self, g: Gesture, text: str = "") -> None:
        """The gesture is refused, and the file is as it was."""
        before = brainfile.dumps(self.bf)
        with pytest.raises(EditError, match=text):
            g.edit(self.bf, self.row, self.lift)
        assert brainfile.dumps(self.bf) == before


@pytest.fixture
def rig(lift: ir.Program) -> Rig:
    return Rig(lift)


# ---- finding things to edit


def scopes(p: ir.Program) -> Iterator[tuple[str, list[ir.Node]]]:
    """Every list of nodes of a program with the scope that names it."""
    for r in p.routines():
        yield r.name, r.body
        for x in ir.walk(r.body):
            if isinstance(x, ir.Block) and x.op.shape in (Shape.IF, Shape.REPEAT):
                yield x.id, x.head
            if isinstance(x, ir.Arm):
                yield x.id, x.body


def run_of(
    p: ir.Program, n: int, pred: Callable[[ir.Insn], bool] | None = None
) -> tuple[str, list[str]]:
    """A scope and the ids of `n` enters that follow each other in it."""
    for scope, body in scopes(p):
        for i in range(len(body) - n + 1):
            part = body[i : i + n]
            if all(
                isinstance(x, ir.Insn) and x.op.name == "enter" and (not pred or pred(x))
                for x in part
            ):
                return scope, [x.id for x in part]
    raise LookupError("enter")


def get_scope(p: ir.Program, scope: str) -> list[ir.Node]:
    return next(b for s, b in scopes(p) if s == scope)


def get_block(p: ir.Program, ref: str) -> ir.Block:
    return next(b for b in blocks(p) if b.id == ref)


def live_end(body: list[ir.Node]) -> int:
    """Where the end of a scope is: before its first end, goto or block that always ends."""
    return next((i for i, n in enumerate(body) if is_terminal(n)), len(body))


def live_end_found(body: list[ir.Node]) -> bool:
    return live_end(body) < len(body)


def model_insert(want: ir.Program, where: Place, nodes: list[ir.Node]) -> None:
    """The same insert with `edit`'s functions."""
    if where.where in ("before", "after"):
        edit.insert(want, where.ref, nodes, after=where.where == "after")
        return
    body = get_scope(want, where.ref)
    edit.put_in(want, where.ref, nodes, 0 if where.where == "start" else live_end(body))


def model_move(want: ir.Program, ref: str, where: Place) -> None:
    if where.where in ("before", "after", "start"):
        edit.move(want, ref, where.ref, where.where)
        return
    body = get_scope(want, where.ref)
    k = live_end(body)
    if k < len(body):
        if body[k].id != ref:
            edit.move(want, ref, body[k].id, "before")
    elif not (body and body[-1].id == ref):
        edit.move(want, ref, where.ref, "end")


def size(n: ir.Node) -> int:
    return sum(1 for _ in edit.everything([n]))


def blocks(p: ir.Program, op: str | None = None, shape: Shape | None = None) -> list[ir.Block]:
    return [
        x
        for x in p.nodes()
        if isinstance(x, ir.Block)
        and (op is None or x.op.name == op)
        and (shape is None or x.op.shape is shape)
    ]


CODE = """\
enter 7 0 0
enter 7 1 0
enter 7 2 0
if_enraged
  enter 3 0 0
else
  enter 3 1 0
if_aware
  enter 3 7 0
random
branch 16
  enter 3 2 0
branch 16
  enter 3 3 0
switch_hp
case 1
  enter 3 4 0
case 2
  enter 3 5 0
default
  enter 3 6 0
"""


def nest(text: str) -> str:
    return "if_enraged\n" + "".join(f"  {ln}\n" for ln in text.splitlines())


@dataclass
class Site:
    """Nodes to edit, as the game's, as inserted into its scope, inside an inserted block or in a
    new routine. New code is found again by what it is each time it is asked for, because its ids
    move as code is put in or taken out."""

    rig: Rig
    kind: str
    own: Callable[[str], bool] | None = None
    """Which ids are new code's, for a site of new code."""
    game: dict[str, Any] | None = None

    @property
    def p(self) -> ir.Program:
        return self.rig.program

    def _find(self, role: str) -> Any:
        if self.game is not None:
            return self.game[role]
        own = self.own
        assert own is not None
        p = self.p
        enters = {
            x.args["sub"]: x.id
            for x in p.nodes()
            if isinstance(x, ir.Insn) and own(x.id) and x.op.name == "enter" and x.args["main"] == 7
        }
        ids = [enters[k] for k in range(3)]
        if role == "ids":
            return ids
        if role == "scope":
            return next(s for s, body in scopes(p) if ids[0] in [n.id for n in body])
        op, mark = {
            "cond": ("if_enraged", 0),
            "bare": ("if_aware", 7),
            "pick": ("random", 2),
            "switch": ("switch_hp", 4),
        }[role]
        for b in blocks(p, op):
            holds = {
                (x.args["main"], x.args["sub"])
                for x in ir.walk([b])
                if isinstance(x, ir.Insn) and x.op.name == "enter"
            }
            if own(b.id) and (3, mark) in holds:
                return b.id
        raise LookupError(role)

    @property
    def ids(self) -> list[str]:
        """Three enters in a row; for new code, the enters 7 0, 7 1, 7 2."""
        return self._find("ids")  # type: ignore[no-any-return]

    @property
    def scope(self) -> str:
        return self._find("scope")  # type: ignore[no-any-return]

    @property
    def cond(self) -> str:
        """An if with an else."""
        return self._find("cond")  # type: ignore[no-any-return]

    @property
    def bare(self) -> str:
        """An if with none."""
        return self._find("bare")  # type: ignore[no-any-return]

    @property
    def pick(self) -> str:
        return self._find("pick")  # type: ignore[no-any-return]

    @property
    def switch(self) -> str:
        return self._find("switch")  # type: ignore[no-any-return]

    def enter(self, k: int) -> str:
        """The k-th of the three enters, whatever else has come between them."""
        if self.game is not None:
            return self.game["ids"][k]  # type: ignore[no-any-return]
        own = self.own
        assert own is not None
        return next(
            x.id
            for x in self.p.nodes()
            if isinstance(x, ir.Insn)
            and own(x.id)
            and x.op.name == "enter"
            and (x.args["main"], x.args["sub"]) == (7, k)
        )

    def order(self) -> list[str]:
        """The three enters in the order they stand in now."""
        ids = [self.enter(k) for k in range(3)]
        body = [n.id for n in get_scope(self.p, self.scope)]
        return sorted(ids, key=body.index)


@pytest.fixture(params=["native", "inserted", "nested", "routine"])
def site(request: pytest.FixtureRequest, lift: ir.Program) -> Site:
    rig, kind = Rig(lift), request.param
    first = lift.routines()[0].name
    if kind == "native":
        scope, ids = run_of(lift, 3)
        ifs = blocks(lift, shape=Shape.IF)
        cond = min((b for b in ifs if b.arms and b.arms[0].body), key=size)
        bare = min((b for b in ifs if not b.arms), key=size)
        pick = min((b for b in blocks(lift, shape=Shape.RANDOM) if len(b.arms) > 2), key=size)
        sw = min((b for b in blocks(lift, shape=Shape.SWITCH) if len(b.arms) > 2), key=size)
        game = {
            "scope": scope,
            "ids": ids,
            "cond": cond.id,
            "bare": bare.id,
            "pick": pick.id,
            "switch": sw.id,
        }
        return Site(rig, kind, None, game)
    if kind == "inserted":
        rig.run(be.insert_code(Place.start(first), CODE))
        return Site(rig, kind, lambda i: i.startswith("u"))
    if kind == "nested":
        rig.run(be.insert_code(Place.start(first), nest(CODE)))
        return Site(rig, kind, lambda i: i.startswith("u") and i != "u1.0")
    es = EditSet.of(lift)
    es.routines["spin"] = parse.nodes(CODE + "end return\n")
    es.lists["h0.call"] = ListEdit(append=["spin"])
    rig.bf.put(es)
    rig.check()
    return Site(rig, kind, lambda i: i.startswith("spin."))


# ---- insert


def test_insert_beside_a_node(site: Site) -> None:
    for pick in (
        lambda: Place.before(site.ids[0]),
        lambda: Place.after(site.ids[0]),
        lambda: Place.after(site.ids[2]),
    ):
        where = pick()
        want = site.rig.model()
        model_insert(want, where, [enter(7, 18)])
        site.rig.run(be.insert(where, [enter(7, 18)]), want)


def test_insert_at_the_ends_of_a_scope(site: Site) -> None:
    for where in (Place.start(site.scope), Place.end(site.scope)):
        want = site.rig.model()
        model_insert(want, where, [enter(7, 18)])
        site.rig.run(be.insert(where, [enter(7, 18)]), want)


def arm0(site: Site) -> str:
    """The id of the first arm of the site's `cond`, as the program has it now."""
    return get_block(site.p, site.cond).arms[0].id


def test_insert_into_a_block_and_an_arm(site: Site) -> None:
    for pick in (
        lambda: Place.start(site.cond),
        lambda: Place.end(site.cond),
        lambda: Place.end(arm0(site)),
    ):
        where = pick()
        want = site.rig.model()
        model_insert(want, where, [enter(7, 18)])
        site.rig.run(be.insert(where, [enter(7, 18)]), want)


def test_a_run_of_inserts_is_one_insert(rig: Rig) -> None:
    _, ids = run_of(rig.lift, 2)
    rig.run(be.insert(Place.after(ids[0]), [enter(7, 11)]))
    rig.run(be.insert(Place.after(ids[0]), [enter(7, 12)]))
    es = rig.es
    assert es is not None and len(es.inserts) == 1
    (only,) = es.inserts.values()
    assert [(n.args["main"], n.args["sub"]) for n in only.nodes] == [(7, 12), (7, 11)]


def test_insert_a_palette_entry_and_a_snippet(rig: Rig) -> None:
    scope, ids = run_of(rig.lift, 2)
    entry = be.PALETTE["Conditions"][0]
    rig.run(be.insert_entry(Place.before(ids[0]), entry))
    rig.run(be.insert_code(Place.after(ids[1]), "enter 6 6 0\n"))
    es = rig.es
    assert es is not None and sorted(es.inserts) == ["u1", "u2"]
    assert be.insert_entry(Place.before(ids[0]), entry).label == f"Insert {entry.title}"


def test_insert_is_refused_where_it_cannot_go(site: Site) -> None:
    rig = site.rig
    rig.refuse(be.insert(Place.before(arm0(site)), [enter(7, 18)]), "is an arm")
    rig.refuse(be.insert(Place.after("nowhere"), [enter(7, 18)]), "no node")
    rig.refuse(be.insert(Place.start("nowhere"), [enter(7, 18)]), "no routine or node")
    rig.refuse(be.insert(Place.start(site.ids[0]), [enter(7, 18)]), "instruction")
    rig.refuse(be.insert(Place.start(site.pick), [enter(7, 18)]), "no then-branch")
    rig.refuse(be.insert(Place.start(site.scope), []), "nothing to insert")


def test_a_gesture_needs_a_known_opcode_and_parsable_code() -> None:
    with pytest.raises(EditError, match="is no opcode"):
        be.replace("x", "nope")
    with pytest.raises(EditError, match="is no opcode"):
        be.wrap_if(["x"], "nope")
    with pytest.raises(EditError, match="line"):
        be.insert_code(Place.start("base_0"), "enter 1\n  enter 2\n")
    with pytest.raises(EditError, match="before, after, start or end"):
        Place("beside", "x")


# ---- delete, replace, move


def test_delete_a_node(site: Site) -> None:
    for nid in reversed(site.ids):
        want = site.rig.model()
        edit.remove(want, nid)
        site.rig.run(be.delete(nid), want)


def test_delete_a_block_takes_what_is_in_it(site: Site) -> None:
    for nid in (site.switch, site.pick, site.cond):
        want = site.rig.model()
        edit.remove(want, nid)
        site.rig.run(be.delete(nid), want)


def test_delete_leaves_what_is_beside_it(site: Site) -> None:
    rig = site.rig
    rig.run(be.insert(Place.after(site.enter(0)), [enter(7, 11)]))
    rig.run(be.insert(Place.before(site.enter(0)), [enter(7, 12)]))
    rig.run(be.insert(Place.before(site.enter(1)), [enter(7, 13)]))
    for k in (0, 1):
        want = rig.model()
        edit.remove(want, site.enter(k))
        rig.run(be.delete(site.enter(k)), want)


def test_delete_a_block_drops_what_hangs_in_it(site: Site) -> None:
    rig = site.rig
    inner = get_block(site.p, site.cond).head[0].id
    rig.run(be.insert(Place.after(inner), [enter(7, 11)]))
    rig.run(be.set_note(inner, "keep"))
    want = rig.model()
    edit.remove(want, site.cond)
    rig.run(be.delete(site.cond), want)
    es = rig.es
    assert es is None or not es.notes
    if site.kind == "native":
        assert es is not None and not es.inserts and list(es.nodes) == [site.cond]


def test_delete_is_refused_for_an_arm(site: Site) -> None:
    site.rig.refuse(be.delete(arm0(site)), "is an arm")
    site.rig.refuse(be.delete("nowhere"), "no node")


def test_replace_a_move(site: Site) -> None:
    a = site.ids[0]
    want = site.rig.model()
    edit.replace(want, a, edit.insn("wait", 0))
    site.rig.run(be.replace(a, "wait"), want)
    site.rig.refuse(be.replace(site.enter(1), "enter"), "already")
    site.rig.refuse(be.replace(site.enter(1), "if_enraged"), "cannot become the block")


def test_replace_a_block_keeps_its_arms(site: Site) -> None:
    n = get_block(site.p, site.cond)
    want = site.rig.model()
    edit.replace(
        want,
        n.id,
        edit.if_("if_provoked", then=edit.clone(n.head), else_=edit.clone(n.arms[0].body)),
    )
    site.rig.run(be.replace(n.id, "if_provoked"), want)
    site.rig.refuse(be.replace(site.pick, "if_provoked"), "not alike")


def test_replace_a_pick_by_a_shifted_one_keeps_its_weights(site: Site) -> None:
    n = get_block(site.p, site.pick)
    arms = [(a.args["weight"], edit.clone(a.body)) for a in n.arms]
    want = site.rig.model()
    edit.replace(want, n.id, edit.random(arms, shift=0))
    site.rig.run(be.replace(n.id, "random_shifted"), want, builds=False)  # nested picks


def test_earlier_and_later(site: Site) -> None:
    rig = site.rig
    a, b, c = site.order()
    want = rig.model()
    edit.move(want, b, a, "before")
    rig.run(be.earlier(b), want)
    a, b, c = site.order()  # b, a, c
    want = rig.model()
    edit.move(want, a, b, "after")
    rig.run(be.later(a), want)
    a, b, c = site.order()
    want = rig.model()
    edit.move(want, b, c, "after")
    rig.run(be.later(b), want)
    a, b, c = site.order()
    want = rig.model()
    edit.move(want, c, b, "before")
    rig.run(be.earlier(c), want)


def test_earlier_then_later_is_nothing(site: Site) -> None:
    if site.kind != "native":
        pytest.skip("a game node has no edit to take back")
    site.rig.run(be.earlier(site.ids[1]))
    assert site.rig.es is not None and site.rig.es.nodes
    site.rig.run(be.later(site.ids[1]))
    assert site.rig.es is None


def test_earlier_and_later_stop_at_the_ends(site: Site) -> None:
    body = get_scope(site.p, site.scope)
    site.rig.refuse(be.earlier(body[0].id), "first")
    site.rig.refuse(be.later(body[-1].id), "last")


PLACES: dict[str, Callable[[Site], Place]] = {
    "into a then": lambda s: Place.start(s.cond),
    "into an arm": lambda s: Place.end(arm_of(s, s.cond)),
    "end of another if": lambda s: Place.end(s.bare),
    "after a pick": lambda s: Place.after(s.pick),
    "before a switch": lambda s: Place.before(s.switch),
    "start of the scope": lambda s: Place.start(s.scope),
    "beside its neighbour": lambda s: Place.after(s.ids[2]),
}


def arm_of(site: Site, block: str) -> str:
    return get_block(site.p, block).arms[0].id


@pytest.mark.parametrize("name", PLACES)
def test_move_to_another_place(site: Site, name: str) -> None:
    a = site.ids[0]
    where = PLACES[name](site)
    want = site.rig.model()
    model_move(want, a, where)
    site.rig.run(be.move_to(a, where), want)


def test_move_a_block(site: Site) -> None:
    want = site.rig.model()
    edit.move(want, site.pick, site.ids[0], "before")
    site.rig.run(be.move_to(site.pick, Place.before(site.ids[0])), want)


def test_move_is_refused_into_itself(site: Site) -> None:
    site.rig.refuse(be.move_to(site.cond, Place.start(site.cond)), "inside")
    site.rig.refuse(be.move_to(site.ids[0], Place.before(site.ids[0])), "inside")
    site.rig.refuse(be.move_to(site.ids[0], Place.start(site.ids[1])), "instruction")


# ---- arms


def arms_of(p: ir.Program, block: str) -> list[ir.Arm]:
    return next(b for b in blocks(p) if b.id == block).arms


def test_add_and_take_away_an_else(site: Site) -> None:
    rig = site.rig
    want = rig.model()
    edit.add_arm(want, site.bare, ir.Arm(1, {}, []))
    rig.run(be.add_else(site.bare), want)
    rig.refuse(be.add_else(site.bare), "has an else already")
    arm = arms_of(site.p, site.bare)[0].id
    want = rig.model()
    edit.delete_arm(want, arm)
    rig.run(be.remove_else(arm), want)
    assert site.kind != "native" or rig.es is None


def test_take_away_the_else_of_the_game_and_get_it_back(site: Site) -> None:
    rig = site.rig
    arm = arm0(site)
    want = rig.model()
    edit.delete_arm(want, arm)
    rig.run(be.remove_arm(arm), want)
    if site.kind == "native":
        rig.run(be.back_to_game(arm))
        assert rig.es is None


def test_a_pick_gains_a_branch_and_stays_a_draw(site: Site) -> None:
    rig = site.rig
    ws = [a.args["weight"] for a in arms_of(site.p, site.pick)]
    top = ws.index(max(ws))
    give = ws[top] // 2
    want = rig.model()
    arms = arms_of(want, site.pick)
    edit.modify(want, arms[top].id, weight=ws[top] - give)
    edit.add_arm(want, site.pick, ir.Arm(1, {"weight": give}, []))
    rig.run(be.add_arm(site.pick), want)
    got = [a.args["weight"] for a in arms_of(site.p, site.pick)]
    assert sum(got) == 32 and len(got) == len(ws) + 1
    want = rig.model()
    edit.add_arm(want, site.pick, ir.Arm(1, {"weight": 0}, []), at=1)
    rig.run(be.add_arm(site.pick, 1))
    assert sum(a.args["weight"] for a in arms_of(site.p, site.pick)) == 32
    assert len(arms_of(site.p, site.pick)) == len(ws) + 2


def test_a_pick_loses_a_branch_to_its_neighbour(site: Site) -> None:
    rig = site.rig
    for first in (True, False):
        arms = arms_of(site.p, site.pick)
        if len(arms) < 2:
            break
        k = len(arms) - 1 if first else 0
        near = arms[k - 1] if k else arms[1]
        want = rig.model()
        edit.modify(want, near.id, weight=near.args["weight"] + arms[k].args["weight"])
        edit.delete_arm(want, arms[k].id)
        rig.run(be.remove_arm(arms[k].id), want)
        assert sum(a.args["weight"] for a in arms_of(site.p, site.pick)) == 32


def test_a_pick_keeps_its_last_branch(site: Site) -> None:
    rig = site.rig
    while len(arms_of(site.p, site.pick)) > 1:
        rig.run(be.remove_arm(arms_of(site.p, site.pick)[-1].id))
    rig.refuse(be.remove_arm(arms_of(site.p, site.pick)[0].id), "needs arms")


def test_a_pick_of_a_bad_sum_is_evened(site: Site) -> None:
    rig = site.rig
    first = arms_of(site.p, site.pick)[0]
    rig.run(be.set_operands(first.id, {"weight": 1}), builds=False)
    ws = [a.args["weight"] for a in arms_of(site.p, site.pick)]
    assert sum(ws) != 32
    rig.run(be.add_arm(site.pick))
    got = [a.args["weight"] for a in arms_of(site.p, site.pick)]
    assert sum(got) == 32 and max(got) - min(got) <= 1


def test_a_switch_gains_a_case_and_loses_one(site: Site) -> None:
    rig = site.rig
    arms = arms_of(site.p, site.switch)
    cases = [a for a in arms if a.sub == 1]
    dflt = next((i for i, a in enumerate(arms) if a.sub == 2), len(arms))
    name = next(iter(cases[-1].args))
    want = rig.model()
    edit.add_arm(want, site.switch, ir.Arm(1, {name: cases[-1].args[name] + 1}, []), at=dflt)
    rig.run(be.add_arm(site.switch), want)
    want = rig.model()
    edit.delete_arm(
        want, cases[0].id if site.kind == "native" else arms_of(site.p, site.switch)[0].id
    )
    rig.run(be.remove_arm(arms_of(site.p, site.switch)[0].id), want)


def test_a_switch_has_one_default(site: Site) -> None:
    rig = site.rig
    arms = arms_of(site.p, site.switch)
    dflt = next((a for a in arms if a.sub == 2), None)
    if dflt is not None:
        rig.refuse(be.add_arm(site.switch, default=True), "has a default already")
        want = rig.model()
        edit.delete_arm(want, dflt.id)
        rig.run(be.remove_arm(dflt.id), want)
        if site.kind == "native":
            want = rig.model()
            edit.add_arm(want, site.switch, ir.Arm(2, {}, []))
            rig.run(be.add_arm(site.switch, default=True), want)
            es = rig.es
            assert es is not None and dflt.id not in es.nodes
            return
    want = rig.model()
    edit.add_arm(want, site.switch, ir.Arm(2, {}, []))
    rig.run(be.add_arm(site.switch, default=True), want)


def test_arms_a_block_cannot_have_are_refused(site: Site) -> None:
    rig = site.rig
    rig.refuse(be.add_arm(site.switch, 0, default=True), "default")
    rig.refuse(be.remove_arm(site.ids[0]), "is no arm")
    rig.refuse(be.add_arm(site.ids[0]), "is no block")
    rig.refuse(be.add_arm(site.pick, 99), "out of range")


def test_a_bucket_gains_a_range_and_loses_one(lift: ir.Program) -> None:
    rig = Rig(lift)
    buckets = blocks(lift, shape=Shape.BUCKET)
    room = [b for b in buckets if len(b.arms) < 5]
    if not room:
        rig.refuse(be.add_arm(buckets[0].id), "no marker")
        return
    want = rig.model()
    edit.add_arm(want, room[0].id, ir.Arm(1, {}, []))
    rig.run(be.add_arm(room[0].id), want)
    arm = arms_of(rig.program, room[0].id)[0].id
    want = rig.model()
    edit.delete_arm(want, arm)
    rig.run(be.remove_arm(arm), want)


def test_a_new_arm_holds_what_is_put_in_it(rig: Rig) -> None:
    bare = next(b for b in blocks(rig.lift, shape=Shape.IF) if not b.arms)
    rig.run(be.add_else(bare.id))
    arm = arms_of(rig.program, bare.id)[0].id
    rig.run(be.insert(Place.start(arm), [enter(7, 18)]))
    marked = [
        x.id
        for x in rig.program.nodes()
        if isinstance(x, ir.Insn) and x.args.get("main") == 7 and x.args.get("sub", 0) >= 10
    ]
    want = rig.model()
    edit.remove(want, marked[0])
    rig.run(be.delete(marked[0]), want)
    rig.run(be.remove_else(arms_of(rig.program, bare.id)[0].id))
    assert rig.es is None


# ---- wrap


def wrapped(rig: Rig, refs: list[str], block: ir.Block) -> ir.Program:
    """What the program is when the nodes are inside `block`, which is put where the first was."""
    want = rig.model()
    index = ir.Index(want)
    body = edit.clone([index.node(r) for r in refs])  # type: ignore[arg-type]
    block.head = body if block.op.shape is Shape.IF else block.head
    if block.arms and block.op.shape is Shape.RANDOM:
        block.arms[0].body = body
    edit.insert(want, refs[0], [block])
    for r in refs:
        edit.remove(want, r)
    return want


def test_wrap_in_an_if(site: Site) -> None:
    a, b = site.order()[:2]
    block = edit.if_("if_enraged", then=[])
    site.rig.run(be.wrap_if([a, b], "if_enraged"), wrapped(site.rig, [a, b], block))
    es = site.rig.es
    assert site.kind != "native" or (es is not None and len(es.inserts) == 1)


def test_wrap_in_a_pick(site: Site) -> None:
    ids = site.order()
    block = edit.random([(20, []), (12, [])])
    site.rig.run(be.wrap_pick(ids, (20, 12)), wrapped(site.rig, ids, block))


def test_wrap_a_block(site: Site) -> None:
    block = edit.if_("if_aware", then=[])
    site.rig.run(be.wrap_if([site.pick], "if_aware"), wrapped(site.rig, [site.pick], block))


def test_wrap_is_refused_for_what_it_cannot_wrap(site: Site) -> None:
    rig = site.rig
    a, _, c = site.order()
    rig.refuse(be.wrap_if([a, c], "if_enraged"), "follow each other")
    inner = get_block(site.p, site.cond).head[0].id
    rig.refuse(be.wrap_if([a, inner], "if_enraged"), "not in one list")
    rig.refuse(be.wrap_if([], "if_enraged"), "select")
    rig.refuse(be.wrap_if([a], "enter"), "no condition")
    rig.refuse(be.wrap_pick([a], (10, 10)), "add up")
    rig.refuse(be.wrap_if([a, a], "if_enraged"), "follow each other")


# ---- operands


def test_set_a_pair(site: Site) -> None:
    rig = site.rig
    a = site.ids[0]
    want = rig.model()
    edit.modify(want, a, main=3, sub=4)
    rig.run(be.set_pair(a, 3, 4), want)
    want = rig.model()
    edit.modify(want, a, sub=5)
    rig.run(be.set_operands(a, {"sub": 5}, "Set Sub"), want)


def test_setting_what_the_game_has_is_no_edit(site: Site) -> None:
    if site.kind != "native":
        pytest.skip("new code has no game value")
    rig = site.rig
    a = site.ids[0]
    node = ir.Index(site.rig.program).node(a)
    assert isinstance(node, ir.Insn)
    was = (node.args["main"], node.args["sub"])
    rig.run(be.set_pair(a, 3, 4))
    assert rig.es is not None and list(rig.es.nodes) == [a]
    rig.run(be.set_pair(a, *was))
    assert rig.es is None


def test_operands_are_checked_as_modify_checks_them(site: Site) -> None:
    rig = site.rig
    a = site.ids[0]
    rig.refuse(be.set_operands(a, {"sub": 300}), "does not fit")
    rig.refuse(be.set_operands(a, {"nope": 1}), "takes")
    rig.refuse(be.set_operands(a, {}), "nothing")
    rig.refuse(be.set_operands(a, {"sub": ir.Sym("x")}), "named x|no list")
    rig.refuse(be.set_operands("nowhere", {"sub": 1}), "no node")


def test_set_a_weight_and_a_case(site: Site) -> None:
    rig = site.rig
    arm = arms_of(site.p, site.pick)[0]
    want = rig.model()
    edit.modify(want, arm.id, weight=3)
    rig.run(be.set_operands(arm.id, {"weight": 3}), want, builds=False)
    case = next(a for a in arms_of(site.p, site.switch) if a.sub == 1)
    name = next(iter(case.args))
    want = rig.model()
    edit.modify(want, case.id, **{name: 9})
    rig.run(be.set_operands(case.id, {name: 9}), want, builds=False)
    rig.refuse(be.set_operands(case.id, {"weight": 1}), "takes")


def test_set_all_weights_and_even(site: Site) -> None:
    rig = site.rig
    arms = arms_of(site.p, site.pick)
    n = len(arms)
    uneven = [32 - (n - 1)] + [1] * (n - 1)
    want = rig.model()
    for a, w in zip(arms_of(want, site.pick), uneven, strict=True):
        edit.modify(want, a.id, weight=w)
    rig.run(be.set_weights(site.pick, uneven), want)
    even = [32 // n + (i < 32 % n) for i in range(n)]
    want = rig.model()
    for a, w in zip(arms_of(want, site.pick), even, strict=True):
        edit.modify(want, a.id, weight=w)
    rig.run(be.even_weights(site.pick), want)
    rig.refuse(be.set_weights(site.pick, [1] * n), "add up")
    rig.refuse(be.set_weights(site.pick, [32]), "arms")
    rig.refuse(be.set_weights(site.switch, [32]), "no weights")


def test_a_call_names_a_list_entry_or_a_new_routine(rig: Rig) -> None:
    call = next(x for x in rig.lift.nodes() if isinstance(x, ir.Insn) and x.op.name == "call")
    other = (call.args["routine"] + 1) % 2
    want = rig.model()
    edit.modify(want, call.id, routine=other)
    rig.run(be.set_operands(call.id, {"routine": other}), want)
    es = EditSet.of(rig.lift) if rig.es is None else rig.es
    es.routines["spin"] = parse.nodes("enter 7 0 0\nend return\n")
    es.lists["h0.call"] = ListEdit(append=["spin"])
    rig.bf.put(es)
    rig.run(be.set_operands(call.id, {"routine": ir.Sym("spin")}))
    node = ir.Index(rig.program).node(call.id)
    assert isinstance(node, ir.Insn) and node.args["routine"] not in (call.args["routine"], other)


def test_a_layout_that_follows_the_first_operand() -> None:
    node = edit.insn("set_target", 1, 2, 3)
    assert be.check_operands(node, {"kind": 3}) == {"kind": 3, "a": 2, "section": 0}
    assert be.check_operands(node, {"b": 9}) == {"kind": 1, "a": 2, "b": 9}
    with pytest.raises(EditError, match="takes"):
        be.check_operands(node, {"section": 1})


# ---- notes and names


def test_a_note_on_a_node_a_routine_and_an_insert(site: Site) -> None:
    rig = site.rig
    a = site.ids[0]
    rig.run(be.set_note(a, "  why  "))
    es = rig.es
    assert es is not None and es.notes == {a: "why"}
    rig.run(be.set_note(site.scope, "the routine"))
    rig.run(be.set_note(a, ""))
    es = rig.es
    assert es is not None and list(es.notes) == [site.scope]
    rig.run(be.set_note(site.scope, "   "))
    assert rig.es is None or not rig.es.notes
    rig.refuse(be.set_note("nowhere", "x"), "no node")


def test_a_note_on_an_insert_by_its_key(rig: Rig) -> None:
    _, ids = run_of(rig.lift, 2)
    rig.run(be.insert(Place.after(ids[0]), [enter(7, 11)]))
    rig.run(be.set_note("u1", "mine"))
    assert rig.es is not None and rig.es.notes == {"u1": "mine"}


def test_a_note_follows_its_node_as_ids_shift(site: Site) -> None:
    if site.kind == "native":
        pytest.skip("a game node keeps its id")
    rig = site.rig
    c = site.ids[2]
    rig.run(be.set_note(c, "third"))
    rig.run(be.insert(Place.before(site.ids[0]), [enter(7, 18)]))
    rig.run(be.delete(site.ids[0]))
    es = rig.es
    assert es is not None
    (ref,) = es.notes
    node = ir.Index(rig.program).node(ref)
    assert isinstance(node, ir.Insn) and (node.args["main"], node.args["sub"]) == (7, 2)


def test_a_routine_is_given_a_display_name(rig: Rig) -> None:
    name = rig.lift.routines()[0].name
    other = rig.lift.routines()[1].name
    rig.run(be.rename_routine(name, " Opening "))
    es = rig.es
    assert es is not None and es.names == {name: "Opening"}
    rig.refuse(be.rename_routine(other, "Opening"), "called Opening already")
    rig.refuse(be.rename_routine(other, name), "called")
    rig.refuse(be.rename_routine(other, "two\nlines"), "one line")
    rig.refuse(be.rename_routine(other, "x" * 41), "one line")
    rig.refuse(be.rename_routine("nowhere", "x"), "no routine")
    rig.run(be.rename_routine(name, ""))
    assert rig.es is None


def test_a_new_routine_is_renamed_where_code_names_it(lift: ir.Program) -> None:
    rig = Rig(lift)
    es = EditSet.of(lift)
    es.routines["spin"] = parse.nodes("enter 7 0 0\nend return\n")
    es.lists["h0.call"] = ListEdit(append=["spin"])
    es.notes["spin.0"] = "first"
    es.notes["spin"] = "the routine"
    rig.bf.put(es)
    rig.run(be.insert(Place.start(lift.routines()[0].name), parse.nodes("call @spin\n")))
    rig.run(be.rename_routine("spin", "twirl"))
    es = rig.es
    assert es is not None
    assert list(es.routines) == ["twirl"] and es.lists["h0.call"].append == ["twirl"]
    assert es.notes == {"twirl.0": "first", "twirl": "the routine"}
    (ins,) = es.inserts.values()
    assert ins.nodes[0].args["routine"] == ir.Sym("twirl")
    rig.refuse(be.rename_routine("twirl", lift.routines()[0].name), "no free name")
    rig.refuse(be.rename_routine("twirl", ""), "no free name")
    rig.refuse(be.rename_routine("twirl", "bad name"), "no name")


# ---- back to the game's


def test_back_to_the_game_for_a_node(rig: Rig) -> None:
    _, ids = run_of(rig.lift, 2)
    rig.run(be.set_pair(ids[0], 3, 4))
    rig.run(be.insert(Place.after(ids[0]), [enter(7, 11)]))
    rig.run(be.set_note(ids[0], "x"))
    rig.run(be.earlier(ids[1]))
    rig.run(be.back_to_game(ids[0]))
    es = rig.es
    assert es is not None and ids[0] not in es.nodes and not es.notes
    assert [i.anchor.ref for i in es.inserts.values()] == [ids[1]]  # it followed the move
    rig.run(be.back_to_game(ids[1]))
    assert rig.es is None


def test_back_to_the_game_for_an_arm_and_a_block(site: Site) -> None:
    if site.kind != "native":
        pytest.skip("new code has no game version")
    rig = site.rig
    arm = arms_of(site.p, site.pick)[0].id
    rig.run(be.set_operands(arm, {"weight": 3}), builds=False)
    rig.run(be.add_arm(site.pick), builds=False)
    rig.run(be.back_to_game(site.pick))
    assert rig.es is None or site.pick not in rig.es.nodes
    rig.run(be.back_to_game(arm))
    rig.run(be.delete(site.cond), builds=False)
    rig.run(be.back_to_game(site.cond))
    assert rig.es is None


def test_back_to_the_game_for_a_routine(rig: Rig) -> None:
    r = rig.lift.routines()[0]
    move = next(x.id for x in ir.walk(r.body) if isinstance(x, ir.Insn) and x.op.name == "enter")
    rig.run(be.set_pair(move, 3, 4))
    rig.run(be.insert(Place.start(r.name), [enter(7, 11)]))
    rig.run(be.rename_routine(r.name, "Mine"))
    rig.run(be.set_note(r.name, "n"))
    rig.run(be.back_to_game(r.name))
    assert rig.es is None


def test_back_to_the_game_takes_a_new_routine_away(lift: ir.Program) -> None:
    rig = Rig(lift)
    es = EditSet.of(lift)
    es.routines["spin"] = parse.nodes("enter 7 0 0\nend return\n")
    es.lists["h0.call"] = ListEdit(append=["spin"])
    rig.bf.put(es)
    rig.run(be.back_to_game("spin"))
    assert rig.es is None


def test_back_to_the_game_is_refused_for_new_code(site: Site) -> None:
    if site.kind == "native":
        pytest.skip("it is the game's")
    site.rig.refuse(be.back_to_game(site.ids[0]), "new code")


def test_an_insert_is_taken_out_with_what_hangs_on_it(rig: Rig) -> None:
    _, ids = run_of(rig.lift, 2)
    rig.run(be.insert(Place.after(ids[0]), [enter(7, 11), enter(7, 12)]))
    rig.run(be.set_note("u1", "n"))
    rig.run(be.insert(Place.after(ids[1]), [enter(7, 13)]))
    rig.run(be.revert_insert("u1"))
    es = rig.es
    assert es is not None and sorted(es.inserts) == ["u2"] and not es.notes
    rig.refuse(be.revert_insert("u1"), "no insert")
    rig.run(be.revert_insert("u2"))
    assert rig.es is None


# ---- the file


def test_a_stale_build_is_refused(rig: Rig) -> None:
    _, ids = run_of(rig.lift, 2)
    rig.run(be.set_pair(ids[0], 3, 4))
    es = rig.es
    assert es is not None
    es.build = "00" * 20
    rig.refuse(be.set_pair(ids[1], 3, 4), "another build")
    rig.refuse(be.back_to_game(ids[0]), "another build")


def test_the_lift_must_be_the_rows(rig: Rig) -> None:
    g = be.delete(run_of(rig.lift, 2)[1][0])
    with pytest.raises(EditError, match="row"):
        g.edit(rig.bf, rig.row + 1, rig.lift)


def test_a_gesture_can_be_run_twice(rig: Rig) -> None:
    _, ids = run_of(rig.lift, 2)
    g = be.insert(Place.after(ids[0]), [enter(7, 11)])
    other = Rig(rig.lift)
    g.edit(rig.bf, rig.row, rig.lift)
    g.edit(other.bf, rig.row, rig.lift)
    g.edit(other.bf, rig.row, rig.lift)
    es, twice = rig.es, other.es
    assert es is not None and twice is not None
    assert len(es.inserts) == 1 and len(twice.inserts) == 1
    assert [len(i.nodes) for i in twice.inserts.values()] == [2]


# ---- between the game and new code


def test_a_node_moves_between_the_game_and_new_code(rig: Rig) -> None:
    first = rig.lift.routines()[0].name
    rig.run(be.insert_code(Place.start(first), "if_enraged\n  enter 3 0 0\nenter 7 10 0\n"))
    block = next(b for b in blocks(rig.program, "if_enraged") if b.id.startswith("u"))
    second, game = next(
        (r.name, x.id)
        for r in rig.lift.routines()[1:]
        for x in ir.walk(r.body)
        if isinstance(x, ir.Insn) and x.op.name == "enter"
    )
    want = rig.model()
    edit.move(want, game, block.id, "start")
    rig.run(be.move_to(game, Place.start(block.id)), want)
    es = rig.es
    assert es is not None and not es.nodes[game].delete
    assert es.nodes[game].move == Anchor("into", block.id, 0)
    new = next(
        x.id
        for x in rig.program.nodes()
        if isinstance(x, ir.Insn) and x.args == {"main": 7, "sub": 10, "gate": 0}
    )
    want = rig.model()
    model_move(want, new, Place.end(second))
    rig.run(be.move_to(new, Place.end(second)), want)
    rig.run(be.back_to_game(game))


def test_a_game_node_moves_beside_new_code(rig: Rig) -> None:
    r0, r1 = rig.lift.routines()[:2]
    rig.run(be.insert(Place.start(r1.name), [enter(7, 11), enter(7, 12)]))
    game = next(x.id for x in r0.body if isinstance(x, ir.Insn))
    new = [
        x.id
        for x in rig.program.nodes()
        if isinstance(x, ir.Insn) and x.args.get("main") == 7 and x.args.get("sub", 0) >= 10
    ]
    for where in (Place.after(new[0]), Place.before(new[0]), Place.after(new[1])):
        want = rig.model()
        edit.move(want, game, where.ref, where.where)
        rig.run(be.move_to(game, where), want, builds=False)
        new = [
            x.id
            for x in rig.program.nodes()
            if isinstance(x, ir.Insn) and x.args.get("main") == 7 and x.args.get("sub", 0) >= 10
        ]
    es = rig.es
    assert es is not None and not es.nodes[game].delete


def test_new_code_moves_between_two_game_scopes(rig: Rig) -> None:
    r0, r1 = rig.lift.routines()[:2]
    rig.run(be.insert(Place.start(r0.name), [enter(7, 11), enter(7, 12)]))
    first = next(
        x.id
        for x in rig.program.nodes()
        if isinstance(x, ir.Insn) and x.args.get("main") == 7 and x.args.get("sub", 0) >= 10
    )
    want = rig.model()
    model_move(want, first, Place.end(r1.name))
    rig.run(be.move_to(first, Place.end(r1.name)), want)
    es = rig.es
    assert es is not None and len(es.inserts) == 2


def test_twin_rows_share_their_edits() -> None:
    one, two = world(1), world(2)
    bf = brainfile.new(75)
    a = next(x.id for x in one.nodes() if isinstance(x, ir.Insn))
    be.insert(Place.after(a), [enter(7, 11)]).edit(bf, 1, one)
    es = bf.edits(1)
    assert es is not None
    es.also = [2]
    twin = next(
        x.id
        for x in two.nodes()
        if isinstance(x, ir.Insn) and x.id.split(":")[1] != a.split(":")[1]
    )
    be.set_pair(twin, 3, 4).edit(bf, 2, two)
    es = bf.edits(1)
    assert es is not None and es.also == [2] and 2 not in bf.rows
    assert all(k.startswith("1:") for k in es.nodes) and es.inserts["u1"].anchor.ref == a
    for lf in (one, two):
        got = editset.apply(lf, bf.edits(lf.row) or EditSet.of(lf))
        assert got.ok and snap(got.program) == snap(editset.apply(one, es).program)
    be.back_to_game(twin).edit(bf, 2, two)
    be.revert_insert("u1").edit(bf, 2, two)
    assert bf.edits(1) is None and bf.edits(2) is None


# ---- the palette


def test_the_palette_holds_each_opcode_once() -> None:
    assert list(be.PALETTE) == ["Moves", "Conditions", "Switch", "Pick one", "Flow", "Other"]
    ops = [e.op for g in be.PALETTE.values() for e in g]
    assert len(ops) == len(set(ops))
    assert set(ops) == set(cat.ENTRIES) - {"switch_enemy_var"}
    assert [e.op for e in be.PALETTE["Moves"]] == ["enter"]
    assert all(cat.kind_of(e.entry.op) is cat.Kind.IF for e in be.PALETTE["Conditions"])
    assert {e.op for e in be.PALETTE["Pick one"]} == {"random", "random_shifted"}
    assert {"call", "goto", "end", "repeat"} <= {e.op for e in be.PALETTE["Flow"]}


def test_a_default_node_is_valid(lift: ir.Program) -> None:
    """Every entry builds, inserted into a routine, with nothing wrong with the node itself."""
    intrinsic = {"ASSEMBLE", "UNRESOLVED", "RANDOM_WEIGHTS", "WEIGHT_ZERO", "REPEAT_COUNT"}
    intrinsic |= {"ORDERED", "GATE_NONZERO", "PAIR_RANGE", "RANDOM_NESTED", "REPEAT_NESTED"}
    entries = [e for g in be.PALETTE.values() for e in g]
    rig = Rig(lift)
    first = lift.routines()[0].name
    rig.run(be.insert(Place.start(first), [e.node() for e in entries]), builds=False)
    es = rig.es
    assert es is not None
    found = build.Built.of(lift, es).findings
    assert not [f for f in found if f.code in intrinsic or f.code.startswith("DOC_")]


def test_the_default_nodes_are_what_a_user_expects() -> None:
    pairs = {(3, 27): "stomp", (0, 4): "roar"}
    enter_ = be.default_node("enter", pairs)
    assert isinstance(enter_, ir.Insn) and (enter_.args["main"], enter_.args["sub"]) == (0, 4)
    other = be.default_node("enter")
    assert isinstance(other, ir.Insn) and (other.args["main"], other.args["sub"]) == (0, 1)
    cond = be.default_node("if_aware")
    assert isinstance(cond, ir.Block) and cond.head == [] and cond.arms == []
    pick = be.default_node("random")
    assert isinstance(pick, ir.Block) and [a.args["weight"] for a in pick.arms] == [16, 16]
    sw = be.default_node("switch_hp")
    assert isinstance(sw, ir.Block) and [a.sub for a in sw.arms] == [1, 2]
    rep = be.default_node("repeat")
    assert isinstance(rep, ir.Block) and rep.args == {"count": 2}
    sec = be.default_node("if_in_section")
    assert isinstance(sec, ir.Block) and sec.args == {"section": 0xFFFF}
    for entry in be.PALETTE["Other"]:
        assert entry.node().op.name == entry.op
    with pytest.raises(EditError, match="no opcode"):
        be.default_node("nope")


def test_a_default_node_is_new_each_time() -> None:
    entry = be.PALETTE["Pick one"][0]
    a, b = entry.node(), entry.node()
    assert a == b and a is not b


# ---- the bar


def named(p: ir.Program, op: str) -> Any:
    return next(x for x in p.nodes() if isinstance(x, ir.Insn | ir.Block) and x.op.name == op)


def test_the_bar_of_a_move_offers_its_pairs(lift: ir.Program) -> None:
    node = named(lift, "enter")
    pairs = {(3, 27): "stomp", (0, 4): "roar", (node.args["main"], node.args["sub"]): "own"}
    got = be.bar(node, lift, pairs=pairs)
    assert got.ref == node.id and got.title == "Move"
    assert [c.name for c in got.controls] == ["main", "sub", "gate"]
    assert got.pairs[0].value == (node.args["main"], node.args["sub"])
    assert [c.text for c in got.pairs][:1] == [cat.pair_text(*got.pairs[0].value, pairs)]
    assert {c.value for c in got.pairs} == set(pairs)
    sub = got.control("sub")
    assert (sub.lo, sub.hi, sub.value) == (0, 255, node.args["sub"])
    gate = got.control("gate")
    assert gate.choices == ()
    with pytest.raises(EditError, match="no operand"):
        got.control("nope")


def test_the_bar_sets_and_swaps(rig: Rig) -> None:
    node = named(rig.lift, "enter")
    got = be.bar(node, rig.lift)
    rig.run(got.pair(3, 4))
    rig.run(got.set("gate", 0))
    assert got.set("sub", 1).label == "Set Sub"
    cond = next(b for b in blocks(rig.lift, shape=Shape.IF) if b.op.name == "if_aware")
    swap = be.bar(cond, rig.lift)
    assert "if_provoked" in {e.name for e in swap.swaps} and "if_aware" not in {
        e.name for e in swap.swaps
    }
    assert all(cat.kind_of(e.op) is cat.Kind.IF for e in swap.swaps)
    rig.run(swap.swap("if_provoked"))
    with pytest.raises(EditError, match="cannot become"):
        swap.swap("enter")


def test_swaps_keep_every_operand(lift: ir.Program) -> None:
    node = be.default_node("if_in_pair")
    swaps = {e.name for e in be.bar(node, lift).swaps}
    assert swaps == {"if_hunter_in_pair"}
    assert be.bar(named(lift, "enter"), lift).swaps == ()
    pick = named(lift, "random")
    assert {e.name for e in be.bar(pick, lift).swaps} == set()


def test_the_bar_of_a_call_lists_the_entries(lift: ir.Program) -> None:
    node = named(lift, "call")
    names = {lift.routines()[0].name: "Opening"}
    got = be.bar(node, lift, names=names, fresh=["spin"])
    target = got.control("routine")
    values = [c.value for c in target.choices]
    table = next(
        iter(t for t in lift.tables().values() if t.name == "call_l" or t.name.endswith("call_l"))
    )
    assert values[: len(table.slots)] == list(range(len(table.slots)))
    assert values[-1] == ir.Sym("spin") and "new routine" in target.choices[-1].text
    assert any("empty" in c.text for c in target.choices) == (None in table.slots)
    goto = named(lift, "goto")
    base = be.bar(goto, lift, names=names).control("routine")
    assert base.choices[0].text.endswith("Opening") or "base_0" in base.choices[0].text


def test_the_bar_of_an_arm(site: Site) -> None:
    arm = arms_of(site.p, site.pick)[0]
    block = get_block(site.p, site.pick)
    got = be.bar(arm, site.p, block=block)
    weight = got.control("weight")
    assert weight.value == arm.args["weight"] and (weight.lo, weight.hi) == (0, 255)
    assert got.swaps == () and got.pairs == ()
    case = next(a for a in arms_of(site.p, site.switch) if a.sub == 1)
    sw = get_block(site.p, site.switch)
    bar_ = be.bar(case, site.p, block=sw)
    assert [c.name for c in bar_.controls] == list(case.args)
    site.rig.run(bar_.set(next(iter(case.args)), 9))
    with pytest.raises(EditError, match="needs its block"):
        be.bar(arm, site.p)
    other = arms_of(site.p, site.cond)[0]
    assert be.bar(other, site.p, block=get_block(site.p, site.cond)).controls == ()


def test_the_bar_words_its_choices(lift: ir.Program) -> None:
    end = be.bar(named(lift, "end"), lift).control("kind")
    assert {c.value for c in end.choices} == set(cat.END_KIND_INFO)
    sec = be.bar(be.default_node("if_in_section"), lift).control("section")
    assert sec.choices == (be.Choice(0xFFFF, "any"),) and sec.hi == 0xFFFF
    apply_ = be.bar(be.default_node("self_heal"), lift).control("apply")
    assert [c.text for c in apply_.choices] == ["apply", "no-op"]
    deg = be.bar(be.default_node("if_off_facing"), lift).control("deg")
    assert (deg.lo, deg.hi) == (0, 255)
    signed = be.bar(be.default_node("if_pursuit_off_facing"), lift).control("deg")
    assert (signed.lo, signed.hi) == (-128, 127)


def test_check_operands_is_edit_modify(lift: ir.Program) -> None:
    node = named(lift, "enter")
    assert be.check_operands(node, {"sub": 3}) == {"main": node.args["main"], "sub": 3, "gate": 0}
    for args, text in (({"sub": 256}, "does not fit"), ({"x": 1}, "takes"), ({}, "nothing")):
        with pytest.raises(EditError, match=text):
            be.check_operands(node, args)
        probe = copy_of(lift)
        with pytest.raises(EditError):
            edit.modify(probe, node.id, **args)
    arm = arms_of(lift, blocks(lift, shape=Shape.RANDOM)[0].id)[0]
    block = blocks(lift, shape=Shape.RANDOM)[0]
    assert be.check_operands(arm, {"weight": 3}, block) == {"weight": 3}
    with pytest.raises(EditError, match="does not fit"):
        be.check_operands(arm, {"weight": 256}, block)
    with pytest.raises(EditError, match="takes"):
        be.check_operands(block, {"zzz": 1})


# ---- the file names game nodes by id


def test_wrapping_game_nodes_moves_them_and_copies_nothing(site: Site) -> None:
    if site.kind != "native":
        pytest.skip("only game nodes")
    rig = site.rig
    a, b = site.order()[:2]
    rig.run(be.wrap_if([a, b], "if_enraged"))
    es = rig.es
    assert es is not None and len(es.inserts) == 1
    assert not any(ne.delete for ne in es.nodes.values())
    assert [es.nodes[x].move for x in (a, b)] == [Anchor("into", "u1.0", 0), Anchor("after", a)]
    (shell,) = es.inserts["u1"].nodes
    assert isinstance(shell, ir.Block) and shell.head == []


def test_wrapping_in_a_pick_moves_them_into_its_first_branch(site: Site) -> None:
    if site.kind != "native":
        pytest.skip("only game nodes")
    a, b = site.order()[:2]
    site.rig.run(be.wrap_pick([a, b], (20, 12)))
    es = site.rig.es
    assert es is not None and es.nodes[a].move == Anchor("into", "u1.1", 0)
    assert es.nodes[b].move == Anchor("after", a)


def test_replacing_a_game_node_is_an_opcode_not_new_code(site: Site) -> None:
    if site.kind != "native":
        pytest.skip("only game nodes")
    rig = site.rig
    rig.run(be.replace(site.ids[0], "wait"))
    rig.run(be.replace(site.cond, "if_provoked"))
    es = rig.es
    assert es is not None and not es.inserts
    assert (es.nodes[site.ids[0]].op, es.nodes[site.cond].op) == ("wait", "if_provoked")
    assert es.nodes[site.ids[0]].args == {"frames": 0}
    rig.run(be.replace(site.cond, get_block(site.rig.lift, site.cond).op.name))
    assert site.cond not in rig.es.nodes


def test_a_block_that_becomes_another_keeps_its_arms_by_edit(site: Site) -> None:
    if site.kind != "native":
        pytest.skip("only game nodes")
    rig = site.rig
    pick = get_block(site.p, site.pick)
    rig.run(be.replace(site.pick, "random_shifted"), builds=False)  # a pick inside a pick
    es = rig.es
    assert es is not None and es.nodes[site.pick].op == "random_shifted"
    assert es.nodes[site.pick].args == {"shift": 0} and not es.inserts
    arms = get_block(site.p, site.pick).arms
    assert [a.args for a in arms] == [a.args for a in pick.arms]


def test_a_game_node_moves_into_a_new_block_by_its_id(site: Site) -> None:
    if site.kind != "native":
        pytest.skip("only game nodes")
    rig = site.rig
    rig.run(be.insert_code(Place.before(site.ids[0]), "if_enraged\n  enter 7 10 0\n"))
    rig.run(be.move_to(site.ids[0], Place.end("u1.0")))
    es = rig.es
    assert es is not None and es.nodes[site.ids[0]].move == Anchor("after", "u1.1")
    assert not any(ne.delete for ne in es.nodes.values())


def test_the_end_of_a_scope_is_before_its_end_or_goto(site: Site) -> None:
    rig = site.rig
    body = get_scope(site.p, site.scope)
    k = live_end(body)
    rig.run(be.insert(Place.end(site.scope), [enter(7, 18)]))
    got = get_scope(site.p, site.scope)
    new = got[k]
    assert isinstance(new, ir.Insn) and new.args["sub"] == 18
    assert [n.id for n in got[k + 1 :]] == [n.id for n in body[k:]] or site.kind != "native"
    assert len(got) == len(body) + 1


def test_the_end_of_a_scope_without_a_terminal_is_its_last_node(rig: Rig) -> None:
    scope, body = next((s, b) for s, b in scopes(rig.lift) if b and not live_end_found(b))
    rig.run(be.insert(Place.end(scope), [enter(7, 18)]))
    got = get_scope(rig.program, scope)
    assert [n.id for n in got[:-1]] == [n.id for n in body] and got[-1].id.startswith("u")
    ending = next(r for r in rig.lift.routines() if live_end_found(r.body))
    k = live_end(ending.body)
    rig.run(be.insert(Place.end(ending.name), [enter(7, 19)]))
    got = get_scope(rig.program, ending.name)
    assert got[k].id.startswith("u") and [n.id for n in got[k + 1 :]] == [
        n.id for n in ending.body[k:]
    ]


def test_a_gesture_applies_the_edit_set_once(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    real = editset.apply

    def counting(lift: ir.Program, es: EditSet) -> editset.Applied:
        calls.append(es)
        return real(lift, es)

    monkeypatch.setattr(editset, "apply", counting)
    scope, ids = run_of(rig.lift, 2, lambda x: x.args["main"] < 8)
    for g in (
        be.insert(Place.after(ids[0]), [enter(7, 11)]),
        be.set_pair(ids[1], 3, 4),
        be.set_note(ids[0], "n"),
        be.insert(Place.before(ids[0]), [enter(7, 12)]),
        be.delete(ids[0]),
        be.wrap_if([ids[1]], "if_enraged"),
    ):
        calls.clear()
        g.edit(rig.bf, rig.row, rig.lift)
        assert len(calls) == 1, g.label


def test_a_gesture_starts_from_the_edit_set_the_file_has(rig: Rig) -> None:
    """The check of one gesture is the next one's view, unless the file changed in between."""
    _, ids = run_of(rig.lift, 3)
    rig.run(be.insert(Place.after(ids[0]), [enter(7, 11)]))
    es = rig.es
    assert es is not None
    es.node(ids[1]).delete = True  # something else wrote to the file
    rig.refuse(be.set_pair(ids[1], 3, 4), "no node")
    rig.run(be.set_pair(ids[2], 3, 4))
    assert ids[1] in rig.es.nodes and rig.es.nodes[ids[1]].delete  # type: ignore[union-attr]


# ---- builds


def test_the_usual_edits_build_clean(lift: ir.Program) -> None:
    """Each of these leaves a program the checks have nothing to say against, errors at least."""
    rig = Rig(lift)
    r = lift.routines()[1]
    scope, ids = run_of(lift, 2, lambda x: x.args["main"] < 8)
    cond = min((b for b in blocks(lift, shape=Shape.IF) if b.arms and b.arms[0].body), key=size)
    bare = min((b for b in blocks(lift, shape=Shape.IF) if not b.arms), key=size)
    pick = min((b for b in blocks(lift, shape=Shape.RANDOM) if len(b.arms) > 2), key=size)
    steps = [
        be.set_pair(ids[0], 3, 5),
        be.insert_entry(Place.before(ids[1]), be.PALETTE["Moves"][0]),
        be.insert_code(Place.after(ids[1]), "if_enraged\n  enter 3 0 0\n"),
        be.wrap_if([ids[0]], "if_aware"),
        be.add_else(bare.id),
        be.add_arm(pick.id),
        be.even_weights(pick.id),
        be.earlier(ids[1]),
        be.set_note(r.name, "a note"),
        be.rename_routine(r.name, "Mine"),
        be.replace(cond.id, "if_provoked"),
    ]
    for g in steps:
        rig.run(g, builds=True)
    assert rig.es is not None and not rig.es.is_empty()


# ---- chains of gestures against a model


LIMITS = ("called",)
"""What a gesture may refuse though its model cannot: a name in use."""


class Fuzz:
    """Random gestures on one rig. Each one has a model, the same change made with `edit`'s
    functions to a copy of the program as it was; the program a gesture leaves must read the
    same, and the file must stay in normal form."""

    def __init__(self, lift: ir.Program, seed: int) -> None:
        self.rig = Rig(lift)
        self.rig.lift_ids = {x.id for x in lift.nodes()}
        self.rng = random.Random(seed)
        self.refused: collections.Counter[str] = collections.Counter()
        self.ran: collections.Counter[str] = collections.Counter()

    def nodes(self) -> list[ir.Insn | ir.Block]:
        return [x for x in self.rig.program.nodes() if isinstance(x, ir.Insn | ir.Block)]

    def scope(self) -> str:
        return self.rng.choice([s for s, _ in scopes(self.rig.program)])

    def place(self) -> Place:
        if self.rng.random() < 0.5:
            return Place(self.rng.choice(["before", "after"]), self.rng.choice(self.nodes()).id)
        return Place(self.rng.choice(["start", "end"]), self.scope())

    def entry(self) -> be.PaletteEntry:
        names = [
            "enter",
            "if_aware",
            "random",
            "switch_hp",
            "wait",
            "if_enraged",
            "repeat",
            "set_var",
        ]
        want = self.rng.choice(names)
        return next(e for g in be.PALETTE.values() for e in g if e.op == want)

    def make(self) -> tuple[Gesture, Callable[[ir.Program], None]]:
        kind = self.rng.choice(
            ["insert"] * 3
            + ["delete"] * 2
            + ["shift", "move", "replace", "wrap", "arm", "operand", "weights", "word", "back"]
        )
        return getattr(self, f"make_{kind}")()  # type: ignore[no-any-return]

    def make_insert(self) -> tuple[Gesture, Callable[[ir.Program], None]]:
        place, node = self.place(), self.entry().node()

        def model(p: ir.Program) -> None:
            model_insert(p, place, [edit.copy([node])[0]])

        return be.insert(place, [node]), model

    def make_delete(self) -> tuple[Gesture, Callable[[ir.Program], None]]:
        n = self.rng.choice(self.nodes())
        return be.delete(n.id), lambda p: edit.remove(p, n.id)

    def make_shift(self) -> tuple[Gesture, Callable[[ir.Program], None]]:
        n = self.rng.choice(self.nodes())
        by = self.rng.choice([-1, 1])

        def model(p: ir.Program) -> None:
            home = ir.Index(p).home(n.id)
            at = next(i for i, x in enumerate(home) if x.id == n.id) + by
            if not 0 <= at < len(home):
                raise EditError("end")
            edit.move(p, n.id, home[at].id, "before" if by < 0 else "after")

        return (be.earlier if by < 0 else be.later)(n.id), model

    def make_move(self) -> tuple[Gesture, Callable[[ir.Program], None]]:
        n, place = self.rng.choice(self.nodes()), self.place()
        return be.move_to(n.id, place), lambda p: model_move(p, n.id, place)

    def make_replace(self) -> tuple[Gesture, Callable[[ir.Program], None]]:
        n = self.rng.choice(self.nodes())
        swaps = be.bar(n, self.rig.program).swaps
        if not swaps:
            return self.make_delete()
        op = self.rng.choice(swaps).name

        def model(p: ir.Program) -> None:
            old = ir.Index(p).node(n.id)
            assert isinstance(old, ir.Insn | ir.Block)
            if isinstance(old, ir.Insn):
                new: ir.Node = edit.build(op, dict(old.args))
            else:
                new = edit.build(
                    op,
                    dict(old.args),
                    head=edit.clone(old.head),
                    arms=[ir.Arm(a.sub, dict(a.args), edit.clone(a.body)) for a in old.arms],
                )
            edit.replace(p, n.id, new)

        return be.replace(n.id, op), model

    def make_wrap(self) -> tuple[Gesture, Callable[[ir.Program], None]]:
        _, body = self.rng.choice(list(scopes(self.rig.program)))
        if not body:
            return self.make_delete()
        i = self.rng.randrange(len(body))
        part = body[i : i + self.rng.randint(1, 3)]
        refs = [x.id for x in part]
        pick = self.rng.random() < 0.3

        def model(p: ir.Program) -> None:
            index = ir.Index(p)
            kids = edit.clone([index.node(r) for r in refs])  # type: ignore[arg-type]
            block = (
                edit.random([(16, kids), (16, [])]) if pick else edit.if_("if_enraged", then=kids)
            )
            edit.insert(p, refs[0], [block])
            for r in refs:
                edit.remove(p, r)

        return (be.wrap_pick(refs) if pick else be.wrap_if(refs, "if_enraged")), model

    def make_arm(self) -> tuple[Gesture, Callable[[ir.Program], None]]:
        block = self.rng.choice([n for n in self.nodes() if isinstance(n, ir.Block)] or [None])
        if block is None:
            return self.make_delete()
        if self.rng.random() < 0.5 or not block.arms:
            shape = block.op.shape

            def add(p: ir.Program) -> None:
                b = ir.Index(p).node(block.id)
                assert isinstance(b, ir.Block)
                if shape is Shape.IF:
                    edit.add_arm(p, block.id, ir.Arm(1, {}, []))
                elif shape is Shape.RANDOM:
                    ws = [a.args["weight"] for a in b.arms]
                    top = ws.index(max(ws))
                    if sum(ws) != 32 or ws[top] < 2:
                        raise EditError("evened")  # the gesture evens it out; not modelled
                    edit.modify(p, b.arms[top].id, weight=ws[top] - ws[top] // 2)
                    edit.add_arm(p, block.id, ir.Arm(1, {"weight": ws[top] // 2}, []))
                elif shape is Shape.SWITCH:
                    cases = [a for a in b.arms if a.sub == 1]
                    dflt = next((i for i, a in enumerate(b.arms) if a.sub == 2), len(b.arms))
                    (name,) = [o.name for o in operands(b.op.label)]
                    value = cases[-1].args[name] + 1 if cases else 0
                    edit.add_arm(p, block.id, ir.Arm(1, {name: value}, []), at=dflt)
                else:
                    edit.add_arm(p, block.id, ir.Arm(1, {}, []))

            return be.add_arm(block.id), add
        arm = self.rng.choice(block.arms)

        def take(p: ir.Program) -> None:
            b = ir.Index(p).node(block.id)
            assert isinstance(b, ir.Block)
            if block.op.shape is Shape.RANDOM and len(b.arms) > 1:
                k = [a.id for a in b.arms].index(arm.id)
                near = b.arms[k - 1 if k else 1]
                edit.modify(p, near.id, weight=near.args["weight"] + arm.args["weight"])
            edit.delete_arm(p, arm.id)

        return be.remove_arm(arm.id), take

    def make_operand(self) -> tuple[Gesture, Callable[[ir.Program], None]]:
        n = self.rng.choice([x for x in self.nodes() if x.args] or self.nodes())
        controls = be.bar(n, self.rig.program).controls
        if not controls:
            return self.make_delete()
        c = self.rng.choice(controls)
        if c.choices and isinstance(c.choices[0].value, ir.Sym):
            return self.make_delete()
        if isinstance(n, ir.Insn) and n.op.by and c.name == operands(n.op.args)[0].name:
            return self.make_delete()  # a new layout needs operands the model asks for
        v = self.rng.randint(max(c.lo, 0), min(c.hi, 40))
        return be.set_operands(n.id, {c.name: v}), lambda p: edit.modify(p, n.id, **{c.name: v})

    def make_weights(self) -> tuple[Gesture, Callable[[ir.Program], None]]:
        picks = [n for n in self.nodes() if isinstance(n, ir.Block) and n.op.shape is Shape.RANDOM]
        if not picks:
            return self.make_delete()
        b = self.rng.choice(picks)
        n = len(b.arms)

        def model(p: ir.Program) -> None:
            for a, w in zip(
                arms_of(p, b.id), [32 // n + (i < 32 % n) for i in range(n)], strict=True
            ):
                edit.modify(p, a.id, weight=w)

        return be.even_weights(b.id), model

    def make_word(self) -> tuple[Gesture, Callable[[ir.Program], None]]:
        """A note or a name: the program reads the same."""
        if self.rng.random() < 0.5:
            return be.set_note(self.rng.choice(self.nodes()).id, "note"), lambda p: None
        names = [r.name for r in self.rig.lift.routines()]
        return be.rename_routine(self.rng.choice(names), "Name " + str(self.rng.randint(0, 9))), (
            lambda p: None
        )

    def make_back(self) -> tuple[Gesture, Callable[[ir.Program], None]]:
        """Back to the game's: what the program becomes is the editor's to say, not modelled."""
        game = [n for n in self.nodes() if n.id in self.rig.lift_ids]
        if not game:
            return self.make_delete()
        return be.back_to_game(self.rng.choice(game).id), lambda p: None

    def step(self) -> None:
        rig = self.rig
        g, model = self.make()
        want = rig.model()
        try:
            model(want)
        except EditError:
            want = None  # type: ignore[assignment]
        before = brainfile.dumps(rig.bf)
        try:
            g.edit(rig.bf, rig.row, rig.lift)
        except EditError as e:
            assert brainfile.dumps(rig.bf) == before
            self.refused[f"{g.label}: {str(e)[:50]}"] += 1
            if want is not None and not any(w in str(e) for w in LIMITS):
                raise AssertionError(f"{g.label} refused where the model is not: {e}") from e
            return
        assert want is not None, f"{g.label} ran where the model refuses"
        self.ran[g.label] += 1
        if g.label not in ("Back to the game's",):
            assert rig.snap() == snap(want), g.label
        rig.check()


@pytest.mark.parametrize("seed", range(5))
def test_a_chain_of_gestures_keeps_the_file_whole(lift: ir.Program, seed: int) -> None:
    fuzz = Fuzz(lift, seed)
    for _ in range(45 if lift.row == 1 else 15):
        fuzz.step()
    assert sum(fuzz.ran.values()) > 10
