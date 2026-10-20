# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What the Brain editor's user does, as changes to a brain file's edit set. No toolkit.

    brain_edit.delete(nid).edit(bf, row, lift)      # one gesture, one undo step named `.label`
    brain_edit.insert_entry(Place.after(nid), brain_edit.PALETTE["Moves"][0])
    brain_edit.wrap_if([a, b], "if_enraged")
    brain_edit.bar(node, program, pairs=labels).set("sub", 3)

A gesture names nodes by the ids of the program as the edits make it (`apply`): the game's,
`u<k>.<i>` in an insert, `NAME.<i>` in a new routine. EditError refuses and leaves the file as it
was. It applies the edit set once, to check the result (that program is the next gesture's view),
and never builds.

Where a node goes follows what the user sees: a scope's list is arranged as a whole (`_arrange`).
In a game scope a game node keeps its place or gets one move, and new code is one insert per run
between game nodes; in new code each game node is moved after what stands before it. A file
names game nodes by id and holds no game code.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from mhfu.em.script import catalogue as cat
from mhfu.em.script import edit, editset, ir, parse, walk
from mhfu.em.script.brainfile import BrainFile
from mhfu.em.script.catalogue import Pairs
from mhfu.em.script.check import Finding, is_terminal
from mhfu.em.script.edit import EditError
from mhfu.em.script.editset import Anchor, Applied, EditSet, Insert, NodeEdit
from mhfu.em.script.opcodes import BY_NAME, FIRST_DEEP, LISTS, Op, Operand, Shape, operands, slot

from mhfu_studio.monster import brain

__all__ = [
    "BACK",
    "PALETTE",
    "Bar",
    "Choice",
    "Control",
    "Gesture",
    "PaletteEntry",
    "Place",
    "add_arm",
    "add_else",
    "back_to_game",
    "bar",
    "check_operands",
    "default_node",
    "delete",
    "earlier",
    "even_weights",
    "insert",
    "insert_code",
    "insert_entry",
    "later",
    "move_to",
    "remove_arm",
    "remove_else",
    "rename_routine",
    "replace",
    "revert_insert",
    "set_note",
    "set_operands",
    "set_pair",
    "set_weights",
    "wrap_if",
    "wrap_pick",
]

BACK = "Back to the game's"
_PAIR_DEFAULT = (0, 1)
_BLOCK_HEADS = (Shape.IF, Shape.REPEAT)
_WHERE = ("before", "after", "start", "end")
_NAME_LIMIT = 40


# ---- where


@dataclass(frozen=True)
class Place:
    """Where nodes go: before or after a node, or at the start or end of a scope, which is a
    routine (its name), a block's then-branch or body (the block's id) or an arm (its id)."""

    where: str
    ref: str

    def __post_init__(self) -> None:
        if self.where not in _WHERE:
            raise EditError(f"a place is before, after, start or end, not {self.where!r}")

    @classmethod
    def before(cls, ref: str) -> Place:
        return cls("before", ref)

    @classmethod
    def after(cls, ref: str) -> Place:
        return cls("after", ref)

    @classmethod
    def start(cls, ref: str) -> Place:
        return cls("start", ref)

    @classmethod
    def end(cls, ref: str) -> Place:
        return cls("end", ref)


# ---- the contract


@dataclass(frozen=True)
class Gesture:
    """One user action: `edit` changes the row's edit set, in normal form."""

    label: str
    """The undo step's name."""

    def edit(self, bf: BrainFile, row: int, lift: ir.Program) -> None:
        """Change `bf`: the row's edit set is made when there is none, dropped when the change
        nets to nothing, and kept pruned. EditError refuses; `bf` is as it was then."""
        if lift.row != row:
            raise EditError(f"the lift is row {lift.row}'s, not row {row}'s")
        old = bf.edits(row)
        if old is not None and old.build.lower() != lift.sha1:
            raise EditError("the edits in the file are for another build of the game")
        es = _open(old, lift)
        cx = _Cx(lift, es)
        names = cx.tagged()
        self.change(cx)
        _renumber(cx, names)
        _anchor_later(cx)
        _tidy(es)
        _verify(lift, cx.findings, es)
        _store(bf, old, es)

    def change(self, cx: _Cx) -> None:
        raise NotImplementedError


def _open(old: EditSet | None, lift: ir.Program) -> EditSet:
    """A copy of the row's edits, in the ids of `lift`'s row."""
    if old is None:
        return EditSet.of(lift)
    return old.retarget(lift.row) if old.row != lift.row else old.copy()


def _store(bf: BrainFile, old: EditSet | None, es: EditSet) -> None:
    if old is not None and old.row != es.row:
        es = es.retarget(old.row)
    if old is not None:
        es.also = list(old.also)
    if es.is_empty() and not (es.names or es.notes):
        if old is not None:
            bf.rows.pop(old.row, None)
        return
    bf.put(es)


def _tidy(es: EditSet) -> None:
    es.prune()
    es.inserts = {k: v for k, v in es.inserts.items() if v.nodes or v.arm is not None}


def _key(f: Finding) -> tuple[str, str]:
    return f.code, f.text


def _verify(lift: ir.Program, was: Sequence[Finding], es: EditSet) -> None:
    """Refuse a change `apply` finds fault with: a bad operand, a lost anchor, arms that break
    their block's rules. Faults the edits had already (`was`) are not the gesture's."""
    known = {_key(f) for f in was if f.level == "error"}
    got = _applied(lift, es)
    for f in got.findings:
        if f.level == "error" and _key(f) not in known:
            text = f.text.removeprefix(f"{f.node}: ") if f.node else f.text
            raise EditError(text)


_VIEW: list[tuple[ir.Program, EditSet, Applied]] = []
"""The last edit set applied, as it was, with its lift: the check of a gesture's result is the
program the next gesture starts from."""


def _applied(lift: ir.Program, es: EditSet) -> Applied:
    if es.is_empty():
        return Applied(lift)
    if _VIEW and _VIEW[0][0] is lift and _VIEW[0][1] == es:
        return _VIEW[0][2]
    got = editset.apply(lift, es)
    _VIEW[:] = [(lift, es.copy(), got)]
    return got


# ---- the edit set as the user sees it


@dataclass(frozen=True)
class _It:
    """A node in a scope's list: a game node (`obj` None) or a node of new code."""

    id: str
    obj: ir.Node | None = None


def _list_of(p: ir.Program, index: ir.Index, ref: str) -> list[ir.Node]:
    for it in p.items:
        if isinstance(it, ir.Routine) and it.name == ref:
            return it.body
    try:
        n = index.node(ref)
    except KeyError:
        raise EditError(f"no routine or node {ref}") from None
    if isinstance(n, ir.Arm):
        return n.body
    if isinstance(n, ir.Block):
        if n.op.shape in _BLOCK_HEADS:
            return n.head
        raise EditError(f"{cat.title(n)} has no then-branch; name one of its arms")
    raise EditError(f"{ref} is an instruction, with no place inside")


@dataclass(frozen=True)
class _Scope:
    """A list of nodes to arrange, found before anything changes: ids of new code shift as the
    code around changes, so a scope is held as the list itself."""

    ref: str
    own: list[ir.Node] | None
    """The list of a scope of new code; None for a game's."""
    owner: ir.Located | None = None
    """The block or arm of new code whose list it is; none for a new routine."""


_LAST: list[tuple[ir.Program, ir.Index]] = []
"""The last lift and its index: a lift does not change, and an index of em01's takes a while."""


def _native(lift: ir.Program) -> ir.Index:
    if not _LAST or _LAST[0][0] is not lift:
        _LAST[:] = [(lift, ir.Index(lift))]
    return _LAST[0][1]


class _Cx:
    """What a gesture works on: the edit set it changes, and the program those edits made when
    the gesture began. Never write to that program: it shares its nodes with the lift."""

    def __init__(self, lift: ir.Program, es: EditSet) -> None:
        self.lift, self.es = lift, es
        self.native = _native(lift)
        self.routines = {r.name for r in lift.routines()}
        got = _applied(lift, es)
        self.findings = got.findings
        self.later: list[tuple[str, str | ir.Located | None, _Scope]] = []
        """Game nodes to move after what stands before them in a scope of new code, once the ids
        of new code are known."""
        self.program = got.program
        self.index = ir.Index(self.program)
        self.scopes: dict[int, str] = {}
        self.blocks: dict[str, ir.Block] = {}
        """The block of each arm, by the arm's id."""
        for r in self.program.routines():
            self.scopes[id(r.body)] = r.name
            self._scan(r.body)

    def _scan(self, nodes: Sequence[ir.Node]) -> None:
        for n in nodes:
            if isinstance(n, ir.Block):
                if n.op.shape in _BLOCK_HEADS:
                    self.scopes[id(n.head)] = n.id
                self._scan(n.head)
                for a in n.arms:
                    self.scopes[id(a.body)] = a.id
                    self.blocks[a.id] = n
                    self._scan(a.body)

    def at(self, ref: str) -> ir.Located:
        try:
            return self.index.node(ref)
        except KeyError:
            raise EditError(f"no node {ref}") from None

    def node(self, ref: str) -> ir.Node:
        n = self.at(ref)
        if isinstance(n, ir.Arm):
            raise EditError(f"{ref} is an arm, not a node")
        return n

    def scope_of(self, ref: str) -> str:
        """The scope whose list holds the node `ref`."""
        self.node(ref)
        return self.scopes[id(self.index.home(ref))]

    def native_scope(self, ref: str) -> bool:
        """Whether the scope is the game's: a routine, a block or an arm it has."""
        return ref in self.routines or ref in self.native

    def table(self) -> dict[str, ir.Located]:
        """The new code of the edit set by the id `apply` gives it."""
        out: dict[str, ir.Located] = {}
        for key, ins in self.es.inserts.items():
            top: list[ir.Located] = [ins.arm] if ins.arm is not None else list(ins.nodes)
            for i, x in enumerate(edit.everything(top)):
                out[f"{key}.{i}"] = x
        for name, body in self.es.routines.items():
            for i, x in enumerate(edit.everything(body)):
                out[f"{name}.{i}"] = x
        return out

    def tagged(self) -> dict[int, tuple[ir.Located, str]]:
        """`table` by the `id()` of the node, which keeps it in the dict."""
        return {id(x): (x, ident) for ident, x in self.table().items()}

    def items(self, ref: str) -> list[_It]:
        objs = self.table()
        out = []
        for n in _list_of(self.program, self.index, ref):
            o = objs.get(n.id)
            out.append(_It(n.id, o if isinstance(o, ir.Insn | ir.Block) else None))
        return out

    def inside(self, ref: str) -> set[str]:
        """The ids of a node or arm and all it holds, as the program has them."""
        return {x.id for x in edit.everything([self.at(ref)]) if x.id}

    def below(self, ref: str) -> set[str]:
        """The same in the game's own program."""
        return {x.id for x in edit.everything([self.native.node(ref)]) if x.id}

    def code(self, ref: str) -> ir.Located:
        """The node or arm of new code with this id, as an object of the edit set."""
        try:
            return self.table()[ref]
        except KeyError:
            raise EditError(f"{ref} is not new code") from None

    def home(self, ref: str) -> _Scope:
        """The scope whose list holds the node `ref`."""
        return self.scope(self.scope_of(ref))

    def scope(self, ref: str) -> _Scope:
        """The scope `ref` names: a routine, or a block's then-branch or body, or an arm."""
        _list_of(self.program, self.index, ref)
        if self.native_scope(ref):
            return _Scope(ref, None)
        return _Scope(ref, self.slot_list(ref), None if ref in self.es.routines else self.code(ref))

    def slot_list(self, ref: str) -> list[ir.Node]:
        """The list of new code a scope holds, which is a new routine's body, a new block's
        then-branch or body, or a new arm's body."""
        if ref in self.es.routines:
            return self.es.routines[ref]
        n = self.code(ref)
        if isinstance(n, ir.Arm):
            return n.body
        if isinstance(n, ir.Block) and n.op.shape in _BLOCK_HEADS:
            return n.head
        raise EditError(f"{ref} has no place inside")

    def plain(self, ref: str, movers: Collection[str]) -> list[str]:
        """The game's nodes a scope holds when `movers` are not moved: the order a move is
        measured against. Moves of what holds the scope stay, or it might not be there."""
        es = self.es.copy()
        for nid in movers:
            if nid in es.nodes:
                es.nodes[nid].move = None
        p = editset.apply(self.lift, es).program
        return [n.id for n in _list_of(p, ir.Index(p), ref) if n.id in self.native]


# ---- arranging a scope's list


def _keep(seq: Sequence[tuple[int, int]]) -> set[int]:
    """The indexes of a rising run of the ranks in `seq` that weighs most, `(rank, weight)`."""
    best = [w for _, w in seq]
    prev = [-1] * len(seq)
    for i, (rank, w) in enumerate(seq):
        for j in range(i):
            if seq[j][0] < rank and best[j] + w > best[i]:
                best[i], prev[i] = best[j] + w, j
    out: set[int] = set()
    i = max(range(len(seq)), key=best.__getitem__, default=-1)
    while i >= 0:
        out.add(i)
        i = prev[i]
    return out


def _after(prev: str | None, scope: str) -> Anchor:
    return Anchor("after", prev) if prev is not None else Anchor("into", scope, 0)


def _arrange(
    cx: _Cx, scope: _Scope, old: Sequence[_It], new: Sequence[_It], prefer: Collection[str] = ()
) -> None:
    """Make the scope hold `new` where it held `old`. Nothing else changes in the program: ids
    of new code follow its nodes."""
    if scope.own is None:
        _arrange_native(cx, scope.ref, old, new, prefer)
        return
    if [id(x) for x in scope.own] != [id(it.obj) for it in old if it.obj is not None]:
        raise EditError(f"{scope.ref}: other inserts are anchored inside this new code, by hand")
    scope.own[:] = [it.obj for it in new if it.obj is not None]
    prev: str | ir.Located | None = None
    for it in new:
        if it.obj is None:
            cx.later.append((it.id, prev, scope))
        prev = it.id if it.obj is None else it.obj


def _anchor_later(cx: _Cx) -> None:
    """Move each game node that is in new code after what stands before it, or into the start of
    its scope: anchors the ids of new code, which are settled now."""
    if not cx.later:
        return
    ids = {id(x): ident for ident, x in cx.table().items()}
    for nid, prev, scope in cx.later:
        if prev is None:
            ref = scope.ref if scope.owner is None else ids[id(scope.owner)]
            cx.es.node(nid).move = Anchor("into", ref, 0)
        else:
            cx.es.node(nid).move = Anchor("after", prev if isinstance(prev, str) else ids[id(prev)])


def _arrange_native(
    cx: _Cx, ref: str, old: Sequence[_It], new: Sequence[_It], prefer: Collection[str]
) -> None:
    """A game scope: its game nodes keep their place in the game's order or get one move each,
    and each run of new code between them is one insert after the game node before it."""
    es = cx.es
    roots = {id(n): k for k, ins in es.inserts.items() if ins.arm is None for n in ins.nodes}
    owners: list[str] = []
    for it in old:
        if it.obj is not None:
            key = roots.get(id(it.obj))
            if key is None:
                raise EditError(f"{it.id} is not part of an insert")
            if key not in owners:
                owners.append(key)
    movers = [it.id for it in (*old, *new) if it.obj is None]
    if any((ne := es.nodes.get(m)) and ne.move for m in movers):
        natural = cx.plain(ref, movers)
    else:  # nothing to take back, so the order it has is the game's
        natural = [it.id for it in old if it.obj is None]
    plain = {nid: i for i, nid in enumerate(natural)}
    games = [it.id for it in new if it.obj is None]
    ranked = [(k, nid) for k, nid in enumerate(games) if nid in plain]
    keep = {
        ranked[i][1] for i in _keep([(plain[nid], 1 if nid in prefer else 2) for _, nid in ranked])
    }
    for key in owners:
        del es.inserts[key]
    prev: str | None = None
    run: list[ir.Node] = []
    runs: list[tuple[Anchor, list[ir.Node]]] = []
    for it in new:
        if it.obj is not None:
            run.append(it.obj)
            continue
        if run:
            runs.append((_after(prev, ref), run))
            run = []
        if it.id in keep:
            if (ne := es.nodes.get(it.id)) and ne.move:
                ne.move = None
        else:
            es.node(it.id).move = _after(prev, ref)
        prev = it.id
    if run:
        runs.append((_after(prev, ref), run))
    for anchor, objs in runs:
        key = next((k for o in objs if (k := roots.get(id(o))) and k not in es.inserts), None)
        es.inserts[key or es.new_key()] = Insert(anchor, objs)
    for key in owners:
        if key not in es.inserts:
            es.notes.pop(key, None)


def _renumber(cx: _Cx, before: Mapping[int, tuple[ir.Located, str]]) -> None:
    """New code is named by its place, so a change shifts ids: point every reference at the
    new id of the node it named."""
    after = cx.tagged()
    moved = {
        old: after[i][1] for i, (_, old) in before.items() if i in after and after[i][1] != old
    }
    if not moved:
        return
    es = cx.es

    def to(ref: str) -> str:
        return moved.get(ref, ref)

    for ins in es.inserts.values():
        ins.anchor = Anchor(ins.anchor.kind, to(ins.anchor.ref), ins.anchor.at)
    for ne in es.nodes.values():
        if ne.move:
            ne.move = Anchor(ne.move.kind, to(ne.move.ref), ne.move.at)
        if ne.bypass is not None:
            ne.bypass = to(ne.bypass)
    es.notes = {to(k): v for k, v in es.notes.items()}


# ---- taking nodes out, copying game code


def _inner(roots: Collection[str], inside: Collection[str], a: Anchor) -> bool:
    """Whether an anchor is in the removed code; before or after a root itself is beside it."""
    return a.ref in inside and not (a.ref in roots and a.kind != "into")


def _kids(n: ir.Located) -> list[ir.Located]:
    """What a game node holds, one level down: a block's then-branch and arms, an arm's body."""
    match n:
        case ir.Block():
            return [*n.head, *n.arms]
        case ir.Arm():
            return list(n.body)
        case _:
            return []


def _clear(cx: _Cx, roots: Sequence[str]) -> None:
    """Take nodes or arms out of the program with all in them: game nodes are deleted, the edits
    and new code inside go, game nodes moved in are deleted. What is beside a root stays for its
    list to re-anchor, and so does a game node moved out of one, with its edits."""
    es = cx.es
    inside: set[str] = set()
    for r in roots:
        inside |= cx.inside(r)
    game = {x for r in roots if r in cx.native for x in cx.below(r)}
    tops = [r for r in roots if r in cx.native]
    for r in roots:
        for x in edit.everything([cx.at(r)]):
            if x.id in cx.native and x.id not in game:  # a game node moved in from elsewhere
                tops.append(x.id)
                game |= cx.below(x.id)
    for key in [k for k, ins in es.inserts.items() if _inner(roots, inside, ins.anchor)]:
        del es.inserts[key]

    def sweep(n: ir.Located, escaped: bool) -> None:
        ne = es.nodes.get(n.id)
        escaped = escaped or bool(ne and ne.move and not _inner(roots, inside, ne.move))
        if not escaped:
            es.nodes.pop(n.id, None)
        for k in _kids(n):
            sweep(k, escaped)

    for t in tops:  # a top goes, so where it was moved to is no matter, but its children may be
        es.nodes[t] = NodeEdit(delete=True)
        for k in _kids(cx.native.node(t)):
            sweep(k, False)
    es.notes = {k: v for k, v in es.notes.items() if k not in inside}


def _fresh(nodes: Sequence[ir.Node]) -> list[ir.Node]:
    """Copies of nodes the caller made, for new code: none has an address or an origin, which a
    file keeps no text of."""
    out = edit.clone(nodes)
    for x in edit.everything(out):
        x.origin = None
    return out


# ---- insert, delete, replace, move


def _slot(cx: _Cx, place: Place) -> tuple[_Scope, int]:
    """The scope and the position in its list that a place is. The end of a scope is before its
    first end or goto (or block that always ends): what follows one never runs."""
    if place.where in ("before", "after"):
        cx.node(place.ref)
        scope = cx.scope(cx.scope_of(place.ref))
        ids = [it.id for it in cx.items(scope.ref)]
        return scope, ids.index(place.ref) + (place.where == "after")
    body = _list_of(cx.program, cx.index, place.ref)
    if place.where == "start":
        return cx.scope(place.ref), 0
    return cx.scope(place.ref), next((i for i, n in enumerate(body) if is_terminal(n)), len(body))


@dataclass(frozen=True)
class _Insert(Gesture):
    place: Place
    nodes: tuple[ir.Node, ...]

    def change(self, cx: _Cx) -> None:
        if not self.nodes:
            raise EditError("there is nothing to insert")
        scope, k = _slot(cx, self.place)
        old = cx.items(scope.ref)
        fresh = [_It("", n) for n in _fresh(self.nodes)]
        _arrange(cx, scope, old, [*old[:k], *fresh, *old[k:]])


@dataclass(frozen=True)
class _Delete(Gesture):
    ref: str

    def change(self, cx: _Cx) -> None:
        scope = cx.home(self.ref)
        old = cx.items(scope.ref)
        _clear(cx, [self.ref])
        _arrange(cx, scope, old, [it for it in old if it.id != self.ref])


def _defaults(op: Op, carry: Mapping[str, ir.Value], pairs: Pairs | None) -> dict[str, ir.Value]:
    """Operands for `op`: the values of `carry` by name where it has them, else a default."""
    info = cat.entry(op)
    args: dict[str, ir.Value] = {}
    pair = min(pairs, default=_PAIR_DEFAULT) if pairs else _PAIR_DEFAULT

    def value(o: Operand) -> ir.Value:
        if o.name in carry:
            return carry[o.name]
        if o.name in ("main", "sub") and op.name == "enter":
            return pair[o.name == "sub"]
        fmt = next((i.fmt for i in info.operands if i.name == o.name), cat.Fmt.NUM)
        if fmt is cat.Fmt.SECTION:
            return 0xFFFF
        return 2 if (op.name, o.name) == ("repeat", "count") else 0

    if op.shape is not Shape.PLAIN:
        return {o.name: value(o) for o in op.opener}
    first = operands(op.args)[0] if op.args else None
    if first is not None:
        args[first.name] = value(first)
    for o in operands(op.plain_layout(args)):
        args[o.name] = value(o)
    return args


def default_node(op: str | cat.Entry, pairs: Pairs | None = None) -> ir.Node:
    """A node of `op` that is valid as it stands: an if with an empty then-branch, a pick of two
    arms of 16, a switch of one case and a default, an enter of a pair the labels know."""
    o = op.op if isinstance(op, cat.Entry) else _op(op)
    args = _defaults(o, {}, pairs)
    arms: list[ir.Arm] = []
    match o.shape:
        case Shape.SWITCH:
            arms = [_arm(o, 1), ir.Arm(2, {}, [])]
        case Shape.RANDOM:
            arms = [ir.Arm(1, {"weight": cat.DRAW // 2}, []) for _ in range(2)]
        case Shape.BUCKET | Shape.ZONE:
            arms = [_arm(o, 1), _arm(o, 1)]
    return edit.build(o.name, args, arms=arms)


def _op(name: str) -> Op:
    try:
        return BY_NAME[name]
    except KeyError:
        raise EditError(f"{name} is no opcode") from None


def _arm(op: Op, sub: int, carry: Mapping[str, ir.Value] | None = None) -> ir.Arm:
    """A new arm of kind `sub`: the operands of its marker, from `carry` or 0."""
    got = carry or {}
    return ir.Arm(sub, {o.name: got.get(o.name, 0) for o in operands(op.layout(sub))}, [])


@dataclass(frozen=True)
class _Replace(Gesture):
    ref: str
    op: str

    def change(self, cx: _Cx) -> None:
        n, op = cx.node(self.ref), _op(self.op)
        if op is n.op:
            raise EditError(f"it is {cat.entry(op).title} already")
        if isinstance(n, ir.Insn) and op.coded:
            raise EditError(f"{n.op.name} cannot become the block {op.name}")
        if isinstance(n, ir.Block) and op.shape is not n.op.shape:
            raise EditError(f"{n.op.name} cannot become {op.name}: they are not alike")
        args = _defaults(op, n.args, None)
        arms = (
            [(a, _arm(op, a.sub, a.args).args) for a in n.arms] if isinstance(n, ir.Block) else []
        )
        edit.build(
            op.name,
            args,
            head=n.head if isinstance(n, ir.Block) else (),
            arms=[ir.Arm(a.sub, c, []) for a, c in arms],
        )
        if self.ref in cx.native:
            self._swap(cx, op, args, arms)
        else:
            self._remake(cx, op, args, arms)

    def _swap(
        self,
        cx: _Cx,
        op: Op,
        args: dict[str, ir.Value],
        arms: list[tuple[ir.Arm, dict[str, ir.Value]]],
    ) -> None:
        """A game node becomes another opcode in place: the edit is its `op` and the operands
        the game's node does not have by name."""
        game = cx.native.node(self.ref)
        assert not isinstance(game, ir.Arm)
        ne = cx.es.node(self.ref)
        ne.op = None if op is game.op else op.name
        ne.args = {k: v for k, v in args.items() if game.args.get(k) != v}
        for a, c in arms:
            if a.id in cx.native:
                kept = cx.native.node(a.id)
                cx.es.node(a.id).args = {k: v for k, v in c.items() if kept.args.get(k) != v}
            else:
                cx.code(a.id).args = dict(c)

    def _remake(
        self,
        cx: _Cx,
        op: Op,
        args: dict[str, ir.Value],
        arms: list[tuple[ir.Arm, dict[str, ir.Value]]],
    ) -> None:
        """New code becomes another opcode where it is, so what hangs on it still does."""
        obj = cx.code(self.ref)
        if isinstance(obj, ir.Arm) or (isinstance(obj, ir.Block) and len(obj.arms) != len(arms)):
            raise EditError(f"{self.ref}: other inserts add arms to this new block, by hand")
        obj.op, obj.args = op, args
        if isinstance(obj, ir.Block):
            for mine, (_, c) in zip(obj.arms, arms, strict=True):
                mine.args = dict(c)


@dataclass(frozen=True)
class _Shift(Gesture):
    ref: str
    by: int

    def change(self, cx: _Cx) -> None:
        scope = cx.home(self.ref)
        old = cx.items(scope.ref)
        i = [it.id for it in old].index(self.ref)
        j = i + self.by
        if not 0 <= j < len(old):
            raise EditError("it is " + ("last" if self.by > 0 else "first") + " already")
        new = list(old)
        new[i], new[j] = new[j], new[i]
        _arrange(cx, scope, old, new, {self.ref})


@dataclass(frozen=True)
class _MoveTo(Gesture):
    ref: str
    place: Place

    def change(self, cx: _Cx) -> None:
        cx.node(self.ref)
        src = cx.home(self.ref)
        dst, k = _slot(cx, self.place)
        if self.place.ref in cx.inside(self.ref):
            raise EditError(f"{self.place.ref} is {self.ref} or inside it")
        old = cx.items(src.ref)
        i = [it.id for it in old].index(self.ref)
        item = old[i]
        if src.ref == dst.ref:
            rest = [*old[:i], *old[i + 1 :]]
            k -= k > i
            _arrange(cx, src, old, [*rest[:k], item, *rest[k:]], {self.ref})
            return
        there = cx.items(dst.ref)
        _arrange(cx, src, old, [*old[:i], *old[i + 1 :]])
        _arrange(cx, dst, there, [*there[:k], item, *there[k:]], {self.ref})


@dataclass(frozen=True)
class _Wrap(Gesture):
    refs: tuple[str, ...]
    op: str
    weights: tuple[int, ...] = ()

    def change(self, cx: _Cx) -> None:
        if not self.refs:
            raise EditError("select the nodes to wrap")
        scope = cx.home(self.refs[0])
        if any(cx.scope_of(r) != scope.ref for r in self.refs):
            raise EditError("the nodes to wrap are not in one list")
        old = cx.items(scope.ref)
        ids = [it.id for it in old]
        at = sorted(ids.index(r) for r in self.refs)
        if at != list(range(at[0], at[0] + len(at))) or len(set(self.refs)) != len(at):
            raise EditError("the nodes to wrap must follow each other")
        shell, inner = self._shell()
        _arrange(cx, inner, [], old[at[0] : at[-1] + 1])
        _arrange(cx, scope, old, [*old[: at[0]], _It("", shell), *old[at[-1] + 1 :]])

    def _shell(self) -> tuple[ir.Block, _Scope]:
        """The new block, empty, and the scope the wrapped nodes go in."""
        if self.weights:
            if sum(self.weights) != cat.DRAW:
                raise EditError(f"weights add up to {sum(self.weights)}, not {cat.DRAW}")
            pick = edit.random([(w, []) for w in self.weights])
            return pick, _Scope("", pick.arms[0].body, pick.arms[0])
        node = default_node(self.op)
        if not isinstance(node, ir.Block) or node.op.shape is not Shape.IF:
            raise EditError(f"{self.op} is no condition")
        return node, _Scope("", node.head, node)


# ---- arms


def _even(n: int) -> list[int]:
    """`n` weights adding up to a draw, as even as whole numbers go."""
    return [cat.DRAW // n + (i < cat.DRAW % n) for i in range(n)]


def _block(cx: _Cx, ref: str) -> ir.Block:
    n = cx.at(ref)
    if not isinstance(n, ir.Block):
        raise EditError(f"{ref} is no block")
    return n


def _weights(cx: _Cx, block: ir.Block, ws: Sequence[int]) -> None:
    for a, w in zip(block.arms, ws, strict=True):
        if a.args.get("weight") != w:
            _put(cx, a.id, {"weight": w})


def _own_arms(cx: _Cx, ref: str, shown: ir.Block) -> ir.Block:
    """The block of new code `ref` is, whose arms are all it shows."""
    own = cx.code(ref)
    if not isinstance(own, ir.Block) or len(own.arms) != len(shown.arms):
        raise EditError(f"{ref}: other inserts add arms to this new block, by hand")
    return own


def _fit(block: ir.Block, arms: Sequence[ir.Arm]) -> None:
    """Refuse arms the block cannot have, as building it would."""
    edit.build(block.op.name, block.args, head=block.head, arms=arms)


def _share(cx: _Cx, b: ir.Block, at: int) -> int:
    """The weight of a new branch at `at` of a pick, and the others made to give it room: half
    of the largest, or an even split where they do not add up to a draw."""
    ws = [a.args.get("weight") for a in b.arms]
    ints = [w for w in ws if isinstance(w, int)]
    if len(ints) == len(ws) and sum(ints) == cat.DRAW and max(ints) > 1:
        top = ints.index(max(ints))
        give = ints[top] // 2
        _put(cx, b.arms[top].id, {"weight": ints[top] - give})
        return give
    even = _even(len(ws) + 1)
    _weights(cx, b, [*even[:at], *even[at + 1 :]])
    return even[at]


@dataclass(frozen=True)
class _AddArm(Gesture):
    block: str
    at: int | None = None
    default: bool = False

    def change(self, cx: _Cx) -> None:
        b = _block(cx, self.block)
        arm, at = self._new(b)
        if not 0 <= at <= len(b.arms):
            raise EditError(f"{self.block} has {len(b.arms)} arms; {at} is out of range")
        if self.block in cx.native and self._revive(cx, b, arm):
            return
        if b.op.shape is Shape.RANDOM:
            arm.args["weight"] = _share(cx, b, at)
        if self.block in cx.native:
            anchor = Anchor("after", b.arms[at - 1].id) if at else Anchor("into", b.id, 0)
            cx.es.inserts[cx.es.new_key()] = Insert(anchor, [], arm)
            return
        own = _own_arms(cx, self.block, b)
        arms = [*own.arms[:at], arm, *own.arms[at:]]
        _fit(own, arms)
        own.arms[:] = arms

    def _revive(self, cx: _Cx, b: ir.Block, arm: ir.Arm) -> bool:
        """An else or default the game has and the edits delete comes back empty. A new arm
        would be put in before the delete runs, beside the one that is going."""
        native = cx.native.node(self.block)
        assert isinstance(native, ir.Block)
        for a in native.arms:
            gone = cx.es.nodes.get(a.id)
            if gone and gone.delete and a.sub == arm.sub and (b.op.shape is Shape.IF or a.sub == 2):
                del cx.es.nodes[a.id]
                for kid in a.body:
                    moved = cx.es.nodes.get(kid.id)
                    if not (moved and moved.move):  # one that was moved out stays where it is
                        cx.es.nodes[kid.id] = NodeEdit(delete=True)
                return True
        return False

    def _new(self, b: ir.Block) -> tuple[ir.Arm, int]:
        op = b.op
        arms = b.arms
        end = len(arms)
        match op.shape:
            case Shape.IF:
                if arms:
                    raise EditError(f"{cat.title(b)} has an else already")
                return ir.Arm(1, {}, []), 0
            case Shape.SWITCH:
                dflt = next((i for i, a in enumerate(arms) if a.sub == 2), None)
                if self.default:
                    if dflt is not None:
                        raise EditError(f"{cat.title(b)} has a default already")
                    return ir.Arm(2, {}, []), end
                last = [a for a in arms if a.sub == 1 and a.args]
                nxt = 0
                if last and isinstance(v := next(iter(last[-1].args.values())), int):
                    nxt = v + 1
                arm = _arm(op, 1, dict.fromkeys((o.name for o in operands(op.label)), nxt))
                at = self.at if self.at is not None else end if dflt is None else dflt
                return arm, at
            case Shape.REPEAT | Shape.PLAIN:
                raise EditError(f"{cat.title(b)} has no arms")
            case _:
                arm = _arm(op, 1)
                return arm, self.at if self.at is not None else end


@dataclass(frozen=True)
class _RemoveArm(Gesture):
    arm: str

    def change(self, cx: _Cx) -> None:
        a = cx.at(self.arm)
        if not isinstance(a, ir.Arm):
            raise EditError(f"{self.arm} is no arm")
        b = cx.blocks[self.arm]
        ids = [x.id for x in b.arms]
        if b.op.shape is Shape.RANDOM and len(ids) > 1:
            k = ids.index(self.arm)
            near = b.arms[k - 1 if k else 1]
            w, v = a.args.get("weight"), near.args.get("weight")
            if isinstance(w, int) and isinstance(v, int):
                _put(cx, near.id, {"weight": v + w})
        _clear(cx, [self.arm])
        key = self.arm.partition(".")[0]
        if self.arm in cx.native:
            pass
        elif self.arm == f"{key}.0" and cx.es.inserts.get(key) and cx.es.inserts[key].arm:
            del cx.es.inserts[key]  # the arm an insert adds
        else:
            own = _own_arms(cx, b.id, b)
            gone = cx.code(self.arm)
            arms = [x for x in own.arms if x is not gone]
            _fit(own, arms)
            own.arms[:] = arms
        rest = [i for i in ids if i != self.arm]
        for k, i in enumerate(rest):
            ins = cx.es.inserts.get(i.partition(".")[0]) if i.endswith(".0") else None
            if ins and ins.arm is not None and ins.anchor.ref == self.arm:
                ins.anchor = Anchor("after", rest[k - 1]) if k else Anchor("into", b.id, 0)


# ---- operands


def _operand_list(node: ir.Located, block: ir.Block | None) -> tuple[Operand, ...]:
    match node:
        case ir.Insn():
            return operands(node.op.plain_layout(node.args))
        case ir.Block():
            return node.op.opener
        case ir.Arm():
            if block is None:
                raise EditError("an arm's operands need its block")
            return operands(block.op.layout(node.sub))


def check_operands(
    node: ir.Located, args: Mapping[str, ir.Value], block: ir.Block | None = None
) -> dict[str, ir.Value]:
    """The operands the node would have with `args` set, checked as `edit.modify` checks them.
    A plain opcode whose layout follows its first operand takes the new layout, 0 for what it
    lacks. `block` is an arm's block."""
    if not args:
        raise EditError("nothing to change")
    if isinstance(node, ir.Insn):
        merged = {**node.args, **args}
        names = [o.name for o in operands(node.op.plain_layout(merged))]
        final = {k: merged.get(k, 0) for k in names}
    else:
        names = [o.name for o in _operand_list(node, block)]
        final = {**node.args, **args}
    if extra := sorted(set(args) - set(names)):
        what = (
            node.op.name
            if not isinstance(node, ir.Arm) and block is None
            else (block.op.name if block else "an arm")
        )
        raise EditError(f"{what} takes {names}, not {extra}")
    match node:
        case ir.Insn():
            edit.build(node.op.name, final)
        case ir.Block():
            edit.build(node.op.name, final, head=node.head, arms=node.arms)
        case ir.Arm():
            assert block is not None
            edit.build(block.op.name, block.args, arms=[ir.Arm(node.sub, final, [])])
    return final


def _put(cx: _Cx, ref: str, args: Mapping[str, ir.Value]) -> None:
    """Set operands of a node or arm, the game's or new."""
    n = cx.at(ref)
    final = check_operands(n, args, cx.blocks.get(ref) if isinstance(n, ir.Arm) else None)
    if ref in cx.native:
        editset.set_args(cx.es, cx.lift, ref, **final)
    else:
        cx.code(ref).args = dict(final)


@dataclass(frozen=True)
class _SetOperands(Gesture):
    ref: str
    args: tuple[tuple[str, ir.Value], ...]

    def change(self, cx: _Cx) -> None:
        _put(cx, self.ref, dict(self.args))


@dataclass(frozen=True)
class _SetWeights(Gesture):
    block: str
    weights: tuple[int, ...]

    def change(self, cx: _Cx) -> None:
        b = _block(cx, self.block)
        if b.op.shape is not Shape.RANDOM:
            raise EditError(f"{cat.title(b)} has no weights")
        ws = self.weights or tuple(_even(len(b.arms)))
        if len(ws) != len(b.arms):
            raise EditError(f"{len(b.arms)} arms, but {len(ws)} weights")
        if sum(ws) != cat.DRAW:
            raise EditError(f"weights add up to {sum(ws)}, and {cat.DRAW} is all there is")
        _weights(cx, b, ws)


# ---- words


@dataclass(frozen=True)
class _Note(Gesture):
    ref: str
    text: str

    def change(self, cx: _Cx) -> None:
        known = (
            self.ref in cx.routines
            or self.ref in cx.es.routines
            or self.ref in cx.es.inserts
            or self.ref in cx.native
            or self.ref in cx.index
        )
        if not known:
            raise EditError(f"no node, insert or routine {self.ref}")
        cx.es.notes[self.ref] = self.text.strip()


@dataclass(frozen=True)
class _Rename(Gesture):
    routine: str
    name: str

    def change(self, cx: _Cx) -> None:
        es, old, new = cx.es, self.routine, self.name.strip()
        if old in es.routines:
            _rename_new(cx, old, new)
            return
        if old not in cx.routines:
            raise EditError(f"no routine {old}")
        if not new or new == old:
            es.names.pop(old, None)
            return
        if "\n" in new or len(new) > _NAME_LIMIT:
            raise EditError(f"a name is one line of at most {_NAME_LIMIT} letters")
        taken = {*cx.routines, *es.routines, *(v for k, v in es.names.items() if k != old)}
        if new in taken:
            raise EditError(f"a routine is called {new} already")
        es.names[old] = new


def _rename_new(cx: _Cx, old: str, new: str) -> None:
    """Rename a new routine, which is its name in code: labels, lists and ids follow."""
    es = cx.es
    if not new or new in {*cx.routines, *es.routines}:
        raise EditError(f"{new!r} is no free name for a routine")

    def to(ref: str) -> str:
        return new if ref == old else new + ref[len(old) :] if ref.startswith(old + ".") else ref

    def relabel(args: dict[str, ir.Value]) -> None:
        for k, v in args.items():
            if isinstance(v, ir.Sym) and v.label == old:
                args[k] = ir.Sym(new)

    every = [x for body in es.routines.values() for x in edit.everything(body)]
    for ins in es.inserts.values():
        every += edit.everything([ins.arm] if ins.arm is not None else ins.nodes)
        ins.anchor = Anchor(ins.anchor.kind, to(ins.anchor.ref), ins.anchor.at)
    for x in every:
        relabel(x.args)
    for ne in es.nodes.values():
        relabel(ne.args)
        if ne.move:
            ne.move = Anchor(ne.move.kind, to(ne.move.ref), ne.move.at)
        if ne.bypass is not None:
            ne.bypass = to(ne.bypass)
    for le in es.lists.values():
        le.slots = {i: new if e == old else e for i, e in le.slots.items()}
        le.append = [new if e == old else e for e in le.append]
    es.routines = {(new if k == old else k): v for k, v in es.routines.items()}
    es.notes = {to(k): v for k, v in es.notes.items()}


@dataclass(frozen=True)
class _Back(Gesture):
    ref: str

    def change(self, cx: _Cx) -> None:
        es = cx.es
        if self.ref in cx.native:
            editset.revert_node(es, cx.lift, self.ref)
        elif self.ref in cx.routines or self.ref in es.routines:
            editset.revert_routine(es, cx.lift, self.ref)
        else:
            raise EditError(f"{self.ref} is new code: delete it, or take its insert out")
        _unhang(cx)


def _unhang(cx: _Cx) -> None:
    """Drop what hangs on a node a revert took away or put somewhere else: a game node moved in
    goes back to where it was, which may be a deleted place or inside what it was moved into, and
    new code or moves anchored at it have nothing to stand on."""
    es = cx.es
    known = {_key(f) for f in cx.findings}
    while True:
        lost = {
            f.node
            for f in _applied(cx.lift, es).findings
            if f.node
            and _key(f) not in known
            and (f.code == "DOC_ANCHOR" or "inside" in f.text or "circle" in f.text)
        }
        gone = False
        for key in lost:
            if key in es.inserts:
                editset.revert_insert(es, key)
                gone = True
            elif key in es.nodes and es.nodes[key].move:
                es.nodes[key].move = None
                gone = True
        if not gone:
            return


@dataclass(frozen=True)
class _RevertInsert(Gesture):
    key: str

    def change(self, cx: _Cx) -> None:
        if self.key not in cx.es.inserts:
            raise EditError(f"no insert {self.key}")
        editset.revert_insert(cx.es, self.key)


# ---- constructors


def insert(place: Place, nodes: Sequence[ir.Node], label: str = "Insert") -> Gesture:
    """New nodes at a place; they are copied, so the gesture can be run again."""
    return _Insert(label, place, tuple(nodes))


def insert_entry(place: Place, entry: PaletteEntry, pairs: Pairs | None = None) -> Gesture:
    """The default node of a palette entry at a place."""
    return _Insert(f"Insert {entry.title}", place, (entry.node(pairs),))


def insert_code(place: Place, text: str) -> Gesture:
    """The code `text` says (`parse.nodes`) at a place."""
    try:
        nodes = parse.nodes(text)
    except parse.ParseError as e:
        raise EditError(str(e)) from None
    return _Insert("Insert code", place, tuple(nodes))


def delete(ref: str) -> Gesture:
    """A node with all in it; an arm goes by `remove_arm`."""
    return _Delete("Delete", ref)


def replace(ref: str, op: str) -> Gesture:
    """A node by one of another opcode, operands kept where the names match. A block keeps its
    arms and what they hold, and must become a block of its shape."""
    return _Replace(f"Replace with {cat.entry(_op(op)).title}", ref, op)


def earlier(ref: str) -> Gesture:
    return _Shift("Earlier", ref, -1)


def later(ref: str) -> Gesture:
    return _Shift("Later", ref, 1)


def move_to(ref: str, place: Place) -> Gesture:
    return _MoveTo("Move", ref, place)


def add_arm(block: str, at: int | None = None, *, default: bool = False) -> Gesture:
    """An arm for a block: a pick's branch (weights stay adding up to a draw), a switch's case
    before its default (`default` adds the default), a range of a bucket or zone. `at` is its
    position among the arms."""
    return _AddArm("Add default" if default else "Add arm", block, at, default)


def add_else(block: str) -> Gesture:
    return _AddArm("Add else", block)


def remove_arm(arm: str) -> Gesture:
    """An arm with all in it; a pick's weight goes to the arm beside it."""
    return _RemoveArm("Remove arm", arm)


def remove_else(arm: str) -> Gesture:
    return _RemoveArm("Remove else", arm)


def wrap_if(refs: Sequence[str], op: str) -> Gesture:
    """Nodes that follow each other in one list, inside an if of condition `op`."""
    return _Wrap(f"Wrap in {cat.entry(_op(op)).title}", tuple(refs), op)


def wrap_pick(refs: Sequence[str], weights: Sequence[int] = (16, 16)) -> Gesture:
    """The same inside a pick whose first branch holds them; the weights add up to 32."""
    return _Wrap("Wrap in Pick", tuple(refs), "random", tuple(weights))


def set_operands(ref: str, args: Mapping[str, ir.Value], label: str = "Set operands") -> Gesture:
    """Operands of a node or arm by name: a move's pair, a call's target (a list index, or the
    label of a new routine), a case's value, a branch's weight."""
    return _SetOperands(label, ref, tuple(args.items()))


def set_pair(ref: str, main: int, sub: int) -> Gesture:
    return set_operands(ref, {"main": main, "sub": sub}, "Set move")


def set_weights(block: str, weights: Sequence[int]) -> Gesture:
    """All weights of a pick at once; they add up to 32."""
    return _SetWeights("Weights", block, tuple(weights))


def even_weights(block: str) -> Gesture:
    return _SetWeights("Even", block, ())


def set_note(ref: str, text: str) -> Gesture:
    """A note on a node, arm, insert (`u<k>`) or routine; blank takes it away."""
    return _Note("Note", ref, text)


def rename_routine(routine: str, name: str) -> Gesture:
    """A routine's display name, blank for the game's own. A new routine's name is its name in
    code, which labels, lists and ids follow."""
    return _Rename("Rename", routine, name)


def back_to_game(ref: str) -> Gesture:
    """A node or routine as the game has it: its edits, and the new code and moves anchored in
    it, are gone; a new routine goes away."""
    return _Back(BACK, ref)


def revert_insert(key: str) -> Gesture:
    """An insert, with what is anchored in it."""
    return _RevertInsert("Remove insert", key)


# ---- the palette


@dataclass(frozen=True, eq=False)
class PaletteEntry:
    """An opcode in the picker; one object per entry, compared by identity."""

    entry: cat.Entry
    group: str

    @property
    def op(self) -> str:
        return self.entry.name

    @property
    def title(self) -> str:
        return self.entry.title

    @property
    def meaning(self) -> str:
        return self.entry.meaning

    @property
    def theme(self) -> str:
        """What the catalogue files it under (`Hunter`, `Timer`), to sort a long group by."""
        return self.entry.group

    def node(self, pairs: Pairs | None = None) -> ir.Node:
        """A new node of it, valid as it stands."""
        return default_node(self.entry, pairs)


_GROUPS: Mapping[str, tuple[cat.Kind, ...]] = {
    "Moves": (cat.Kind.MOVE,),
    "Conditions": (cat.Kind.IF,),
    "Switch": (cat.Kind.SWITCH,),
    "Pick one": (cat.Kind.PICK,),
    "Flow": (cat.Kind.CALL, cat.Kind.JUMP, cat.Kind.END, cat.Kind.REPEAT),
    "Other": (cat.Kind.TARGET, cat.Kind.VAR, cat.Kind.OP),
}
PALETTE: Mapping[str, tuple[PaletteEntry, ...]] = {
    group: tuple(
        PaletteEntry(e, group) for k in kinds for e in cat.entries(k) if e.op.code != walk.SHORT
    )
    for group, kinds in _GROUPS.items()
}
"""The catalogue's opcodes by what a picker groups them under, in opcode order; each is in one
group. New code cannot use the one opcode the game's scan steps wrongly (`walk.SHORT`)."""


# ---- the bar


@dataclass(frozen=True)
class Choice:
    value: ir.Value | tuple[int, int]
    """The operand's value; a pair for a move."""
    text: str


@dataclass(frozen=True)
class Control:
    """An operand a bar edits."""

    name: str
    label: str
    meaning: str
    value: ir.Value
    text: str
    lo: int
    hi: int
    choices: tuple[Choice, ...] = ()
    """What to offer; none for a plain number."""


@dataclass(frozen=True)
class Bar:
    """What the bar offers for a selected node or arm."""

    ref: str
    title: str
    controls: tuple[Control, ...]
    pairs: tuple[Choice, ...]
    """For a node with a move pair: the pairs the labels name, the current one first."""
    swaps: tuple[cat.Entry, ...]
    """Opcodes it can become with every operand kept."""

    def control(self, name: str) -> Control:
        try:
            return next(c for c in self.controls if c.name == name)
        except StopIteration:
            raise EditError(f"{self.title} has no operand {name}") from None

    def set(self, name: str, value: ir.Value) -> Gesture:
        c = self.control(name)
        return set_operands(self.ref, {name: value}, f"Set {c.label}")

    def pair(self, main: int, sub: int) -> Gesture:
        return set_pair(self.ref, main, sub)

    def swap(self, op: str) -> Gesture:
        if all(e.name != op for e in self.swaps):
            raise EditError(f"{self.title} cannot become {op} with its operands kept")
        return replace(self.ref, op)


def _range(o: Operand) -> tuple[int, int]:
    bits = 8 * o.size
    return (-(1 << (bits - 1)), (1 << (bits - 1)) - 1) if o.signed else (0, (1 << bits) - 1)


def _targets(
    o: Operand,
    args: Mapping[str, ir.Value],
    program: ir.Program,
    header: str,
    names: Mapping[str, str],
    fresh: Collection[str],
) -> tuple[Choice, ...]:
    """The entries of the list an operand indexes, under the header; the new routines too."""
    h = next(h for h in program.headers() if h.name == header)
    table = args.get("table", 0)
    if o.ref == "deep":
        field = FIRST_DEEP + table if isinstance(table, int) else -1
    else:
        field = LISTS[o.ref] if o.ref else -1
    entry = h.fields[field] if 0 <= field < len(h.fields) else None
    found = program.tables().get(entry) if isinstance(entry, str) else None
    out = []
    for i, e in enumerate(found.slots if found else []):
        if e is None:
            what = "empty"
        elif isinstance(e, ir.External):
            what = "outside the program"
        else:
            what = names.get(e, e)
        out.append(Choice(i, f"{i}  {what}"))
    if field >= 0 and slot(field).kind != "data" and o.name != "stages":
        out += [Choice(ir.Sym(n), f"@{n}  new routine") for n in sorted(fresh)]
    return tuple(out)


def _control(
    node: ir.Located,
    f: cat.Field,
    o: Operand,
    info: cat.OperandInfo,
    program: ir.Program,
    header: str,
    names: Mapping[str, str],
    fresh: Collection[str],
) -> Control:
    choices: tuple[Choice, ...] = ()
    if o.ref is not None:
        choices = _targets(o, node.args, program, header, names, fresh)
    elif info.words:
        choices = tuple(Choice(v, f"{v}  {w}") for v, w in info.words.items())
    elif info.fmt is cat.Fmt.SECTION:
        choices = (Choice(0xFFFF, "any"),)
    elif info.fmt is cat.Fmt.APPLY:
        choices = (Choice(0, "apply"), Choice(1, "no-op"))
    elif info.fmt is cat.Fmt.END:
        choices = tuple(Choice(k, e.title) for k, e in cat.END_KIND_INFO.items())
    lo, hi = _range(o)
    return Control(f.name, f.label, f.meaning, f.value, f.text, lo, hi, choices)


def bar(
    node: ir.Located,
    program: ir.Program,
    *,
    block: ir.Block | None = None,
    header: str | None = None,
    pairs: Pairs | None = None,
    names: Mapping[str, str] | None = None,
    fresh: Collection[str] = (),
) -> Bar:
    """The bar of a node or arm of `program` (the program the edits make, so its ids are the
    gestures'). `block` is an arm's block; `header` the one whose lists name the routines, by
    default the one most map types run; `pairs` the labels of move pairs; `names` the display
    names of routines; `fresh` the names of new routines, offered as call targets."""
    shown = brain.pick_header(program, header)
    labels = names or {}
    if isinstance(node, ir.Arm):
        if block is None:
            raise EditError("an arm's bar needs its block")
        fields, layout = cat.arm_operands(block, node), operands(block.op.layout(node.sub))
        entry, title = cat.entry(block.op), f"{cat.title(block)} arm"
    else:
        fields, layout = cat.operands(node), _operand_list(node, None)
        entry, title = cat.entry(node.op), cat.title(node)
    controls = tuple(
        _control(node, f, o, entry.info(f.name), program, shown, labels, fresh)
        for f, o in zip(fields, layout, strict=True)
    )
    named: tuple[Choice, ...] = ()
    main, sub = node.args.get("main"), node.args.get("sub")
    if isinstance(main, int) and isinstance(sub, int):
        listed = [p for p in sorted(pairs or {}) if p != (main, sub)]
        named = tuple(Choice(p, cat.pair_text(*p, pairs)) for p in [(main, sub), *listed])
    swaps: tuple[cat.Entry, ...] = ()
    if not isinstance(node, ir.Arm):
        o = node.op
        swaps = tuple(
            e
            for e in cat.entries(cat.kind_of(node))
            if e.op is not o
            and (e.op.shape, e.op.args, e.op.label, e.op.flow) == (o.shape, o.args, o.label, o.flow)
        )
    return Bar(node.id, title, controls, named, swaps)
