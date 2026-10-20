# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""One species row's changes against its lifted native script, as data, and what they make of
the program.

    es = EditSet.of(lift)                       # no changes yet; `build` is the lift's sha1
    set_args(es, lift, nid, sub=2)              # an operand of a native node, by its id
    es.node(nid).delete = True                  # or bypass, move, arm order
    es.inserts["u1"] = Insert(Anchor("after", nid), [edit.insn("enter", 3, 0, 0)])
    got = apply(lift, es)                       # Applied: program, findings
    img = asm.assemble(got.program)             # resolved: ready to assemble and check

An edit set never holds the brain, only what differs from the game's. Native nodes are named by
the lift's ids (`{row}:{pc:08X}`); new code is IR nodes, each insert under a key `u<k>`, and
`apply` names them `u<k>.<i>` in source order (`name.<i>` inside a new routine), so an anchor can
name a node of another insert. Applying never changes the edit set or the lift: the program
shares every routine, list and header the edits leave alone with the lift, so treat it as
read-only.

`apply` runs in a fixed order: build check, new routines, opcode swaps, operands, arm order, then
moves and inserts together, bypasses, deletes, lists and forks, labels. Moves and inserts go
each after the one it is anchored in, so a game node can be moved before, after or into new code
(`u<k>.<i>`, a new routine's node), and an insert can sit beside a game node that has moved;
otherwise the moves go first, in the order of their ids, then the inserts by key. A bad or
removed anchor, or edits that cannot go together, is a Finding (`DOC_ANCHOR`, `DOC_CONFLICT`)
and leaves the rest applied; a build that is not the lift's refuses everything (`DOC_BUILD`).
Names and notes are for display and change no bytes.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from . import edit, ir
from .check import Finding
from .edit import EditError
from .opcodes import BY_NAME, FIRST_DEEP, LISTS, Op, Operand, operands

__all__ = [
    "NOTHING",
    "Anchor",
    "Applied",
    "DocError",
    "EditSet",
    "Insert",
    "ListEdit",
    "NodeEdit",
    "apply",
    "equal",
    "is_empty",
    "list_field",
    "revert_insert",
    "revert_node",
    "revert_routine",
    "set_arms",
    "set_args",
]

NOTHING = "none"
"""A bypass that keeps no branch."""
KINDS = ("before", "after", "into")
_NATIVE = re.compile(r"(\d+):([0-9A-F]{8})")
_KEY = re.compile(r"u(\d+)")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DEEP = re.compile(r"deep(\d+)")

Plain = dict[str, Any]
Code = Callable[[Sequence[ir.Node]], str]
"""Writes nodes as code text, for a document that keeps them as source."""
Parse = Callable[[str], list[ir.Node]]


class DocError(EditError):
    """Data that is no edit set."""


# ---- the model


@dataclass(frozen=True)
class Anchor:
    """Where nodes go: before or after the node `ref` (an arm: the start or end of its body),
    or into the list `ref` names (a routine, a block's head or an arm) at position `at`, by
    default the end."""

    kind: str
    ref: str
    at: int | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise EditError(f"an anchor is before, after or into, not {self.kind!r}")
        if self.at is not None and (self.kind != "into" or self.at < 0):
            raise EditError("only into takes a position, from 0")


@dataclass
class NodeEdit:
    """What is done to one native node, block opener or arm."""

    args: dict[str, ir.Value] = field(default_factory=dict)
    """The operands that differ from the game's."""
    delete: bool = False
    bypass: str | None = None
    """Replace a block by its `then`, its `else`, the arm with this id, or by `NOTHING`."""
    move: Anchor | None = None
    arms: list[str] | None = None
    """A block's native arms, in the order wanted."""
    op: str | None = None
    """The mnemonic the node becomes, in place: a block keeps its arms and what is in them, an
    instruction the operands whose names the new opcode has. `args` give what it lacks; so do the
    edits of its arms, for a block whose arms take other operands."""

    def is_empty(self) -> bool:
        return not (
            self.args
            or self.delete
            or self.bypass is not None
            or self.move
            or self.arms is not None
            or self.op is not None
        )

    def copy(self) -> NodeEdit:
        arms = None if self.arms is None else list(self.arms)
        return NodeEdit(dict(self.args), self.delete, self.bypass, self.move, arms, self.op)


@dataclass
class Insert:
    """New code: `nodes`, or an `arm` of a block, whose body is the code."""

    anchor: Anchor
    nodes: list[ir.Node] = field(default_factory=list)
    arm: ir.Arm | None = None

    def copy(self) -> Insert:
        arm = None if self.arm is None else edit.copy_arm(self.arm)
        return Insert(self.anchor, edit.copy(self.nodes), arm)


@dataclass
class ListEdit:
    """Changes to one header's list: slots set, entries appended, the list forked first."""

    slots: dict[int, ir.Entry] = field(default_factory=dict)
    append: list[ir.Entry] = field(default_factory=list)
    fork: bool = False
    """Give the header its own copy of the list, so the headers that shared it keep theirs."""

    def is_empty(self) -> bool:
        return not (self.slots or self.append or self.fork)

    def copy(self) -> ListEdit:
        return ListEdit(dict(self.slots), list(self.append), self.fork)


@dataclass
class EditSet:
    row: int
    build: str
    """The sha1 of the lift the edits are against; another build refuses them."""
    also: list[int] = field(default_factory=list)
    """Rows with the same region, to which the edits apply as they are."""
    nodes: dict[str, NodeEdit] = field(default_factory=dict)
    """By the id of a native node, block opener or arm."""
    inserts: dict[str, Insert] = field(default_factory=dict)
    """By key, `u<k>`."""
    routines: dict[str, list[ir.Node]] = field(default_factory=dict)
    """New routines, by name; code reaches them by label."""
    lists: dict[str, ListEdit] = field(default_factory=dict)
    """By `<header>.<list>`: `h0.call`, `h1.int_0x20`, `h0.deep2`."""
    names: dict[str, str] = field(default_factory=dict)
    """A display name for a native routine."""
    notes: dict[str, str] = field(default_factory=dict)
    """A note on a node, an insert or a routine."""

    @classmethod
    def of(cls, lift: ir.Program) -> EditSet:
        return cls(lift.row, lift.sha1)

    def node(self, nid: str) -> NodeEdit:
        """The edits on a node, made when there are none."""
        return self.nodes.setdefault(nid, NodeEdit())

    def new_key(self) -> str:
        """An insert key no insert has: one past the highest."""
        used = (int(m[1]) for k in self.inserts if (m := _KEY.fullmatch(k)))
        return f"u{max(used, default=0) + 1}"

    def copy(self) -> EditSet:
        return EditSet(
            self.row,
            self.build,
            list(self.also),
            {k: v.copy() for k, v in self.nodes.items()},
            {k: v.copy() for k, v in self.inserts.items()},
            {k: edit.copy(v) for k, v in self.routines.items()},
            {k: v.copy() for k, v in self.lists.items()},
            dict(self.names),
            dict(self.notes),
        )

    def prune(self) -> EditSet:
        """Drop what changes nothing: empty edits and blank texts. In place; itself."""
        self.nodes = {k: v for k, v in self.nodes.items() if not v.is_empty()}
        self.lists = {k: v for k, v in self.lists.items() if not v.is_empty()}
        self.names = {k: v for k, v in self.names.items() if v}
        self.notes = {k: v for k, v in self.notes.items() if v}
        return self

    def is_empty(self) -> bool:
        """Whether it builds the game's own bytes: names and notes change none."""
        return not (
            any(not v.is_empty() for v in self.nodes.values())
            or self.inserts
            or self.routines
            or any(not v.is_empty() for v in self.lists.values())
        )

    def to_data(self, code: Code | None = None) -> Plain:
        """Plain dicts, lists, strings and ints, ready for TOML; the row is the document's key.
        New code is nodes as tables, or source text where `code` writes it."""
        return _to_data(self, code)

    @classmethod
    def from_data(cls, row: int, data: Any, parse: Parse | None = None) -> EditSet:
        """The edit set `to_data` wrote, code as tables or, with `parse`, as source text. A
        DocError names what is wrong."""
        return _from_data(row, data, parse)

    def retarget(self, row: int) -> EditSet:
        """The edits as another row of the same region has them: native ids carry the row."""

        def rid(s: str) -> str:
            m = _NATIVE.fullmatch(s)
            return f"{row}:{m[2]}" if m else s

        def anchor(a: Anchor) -> Anchor:
            return Anchor(a.kind, rid(a.ref), a.at)

        out = self.copy()
        out.row, out.also = row, []
        out.nodes = {
            rid(k): NodeEdit(
                v.args,
                v.delete,
                None if v.bypass is None else rid(v.bypass),
                None if v.move is None else anchor(v.move),
                None if v.arms is None else [rid(a) for a in v.arms],
                v.op,
            )
            for k, v in out.nodes.items()
        }
        for ins in out.inserts.values():
            ins.anchor = anchor(ins.anchor)
        out.notes = {rid(k): v for k, v in out.notes.items()}
        return out


def equal(a: EditSet, b: EditSet) -> bool:
    """Whether two edit sets make the same program: the same after dropping empty edits."""
    return a.copy().prune() == b.copy().prune()


def is_empty(es: EditSet) -> bool:
    return es.is_empty()


# ---- gestures: the normal form


def _native(lift: ir.Program, nid: str) -> tuple[ir.Index, ir.Located]:
    index = ir.Index(lift)
    try:
        return index, index.node(nid)
    except KeyError:
        raise EditError(f"no node {nid} in row {lift.row}") from None


def set_args(es: EditSet, lift: ir.Program, nid: str, /, **args: ir.Value) -> None:
    """Set operands of a native node: a value the game has is not kept, and a node with nothing
    left to change drops out. For a node that becomes another opcode, the operands are that
    opcode's."""
    _, n = _native(lift, nid)
    ne = es.node(nid)
    for k, v in args.items():
        if n.args.get(k) == v:
            ne.args.pop(k, None)
        else:
            ne.args[k] = v
    if isinstance(n, ir.Insn) and ne.args:  # a new kind of set_target has other operands
        op = BY_NAME.get(ne.op or "", n.op)
        names = {o.name for o in operands(op.plain_layout({**n.args, **ne.args}))}
        ne.args = {k: v for k, v in ne.args.items() if k in names}
    if ne.is_empty():
        del es.nodes[nid]


def set_arms(es: EditSet, lift: ir.Program, nid: str, order: Sequence[str]) -> None:
    """Set the order of a block's native arms; the game's own order is no edit."""
    _, b = _native(lift, nid)
    if not isinstance(b, ir.Block):
        raise EditError(f"{nid} is no block")
    ne = es.node(nid)
    ne.arms = None if list(order) == [a.id for a in b.arms] else list(order)
    if ne.is_empty():
        del es.nodes[nid]


def _owner(ref: str) -> str:
    """The key of the insert or the name of the new routine a new id belongs to."""
    return ref.partition(".")[0] if "." in ref else ""


def _drop(es: EditSet, ids: set[str], names: set[str], gone: set[str]) -> None:
    """Remove every edit on `ids`, every insert and move anchored at them or at `names`, and
    what hangs on those in turn. `gone` holds inserts and new routines already removed."""

    def anchored(a: Anchor) -> bool:
        return a.ref in ids or a.ref in names or _owner(a.ref) in gone

    for nid in [k for k in es.nodes if k in ids]:
        del es.nodes[nid]
    while True:
        lost = [k for k, ins in es.inserts.items() if anchored(ins.anchor)]
        moved = [k for k, ne in es.nodes.items() if ne.move and anchored(ne.move)]
        for k in lost:
            del es.inserts[k]
            gone.add(k)
        for k in moved:
            es.nodes[k].move = None
        if not (lost or moved):
            break
    drop = ids | names
    es.notes = {
        k: v
        for k, v in es.notes.items()
        if k not in drop and k not in gone and _owner(k) not in gone
    }
    es.prune()


def _subtree(lift: ir.Program, nid: str) -> set[str]:
    _, n = _native(lift, nid)
    return {x.id for x in edit.everything([n]) if x.id}


def revert_insert(es: EditSet, key: str) -> None:
    """Take an insert out, and the inserts and moves anchored in it."""
    es.inserts.pop(key, None)
    _drop(es, set(), set(), {key})


def revert_node(es: EditSet, lift: ir.Program, nid: str) -> None:
    """Back to the game's for a node and all that is in it: its edits, and the inserts and moves
    anchored there."""
    _drop(es, _subtree(lift, nid), set(), set())


def revert_routine(es: EditSet, lift: ir.Program, name: str) -> None:
    """Back to the game's for a routine: the edits of its nodes, the inserts and moves anchored
    in it, its name and notes; for a new routine, the routine itself and what names it."""
    if name in es.routines:
        del es.routines[name]
        for le in es.lists.values():
            le.slots = {i: e for i, e in le.slots.items() if e != name}
            le.append = [e for e in le.append if e != name]
        _drop(es, set(), {name}, {name})
    else:
        try:
            r = next(r for r in lift.routines() if r.name == name)
        except StopIteration:
            raise EditError(f"no routine {name} in row {lift.row}") from None
        _drop(es, {x.id for x in ir.walk(r.body) if x.id}, {name}, set())
    es.names.pop(name, None)
    es.prune()


# ---- as data


def _only(what: str, d: Any, allowed: Iterable[str] | None = None) -> Plain:
    """`d` as a table; with `allowed`, its keys must be among them."""
    if not isinstance(d, Mapping) or not all(isinstance(k, str) for k in d):
        raise DocError(f"{what} is no table")
    if allowed is not None and (extra := sorted(set(d) - set(allowed))):
        raise DocError(f"{what} has no {extra[0]}")
    return dict(d)


def _list(what: str, v: Any) -> list[Any]:
    if not isinstance(v, list):
        raise DocError(f"{what} is no list")
    return v


def _int(what: str, v: Any) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise DocError(f"{what} is no number")
    return v


def _str(what: str, v: Any) -> str:
    if not isinstance(v, str):
        raise DocError(f"{what} is no text")
    return v


def _value_data(v: ir.Value) -> int | str:
    return f"@{v.label}" if isinstance(v, ir.Sym) else v


def _value(what: str, v: Any) -> ir.Value:
    if isinstance(v, str):
        if len(v) < 2 or v[0] != "@":
            raise DocError(f"{what}: {v!r} is no operand")
        return ir.Sym(v[1:])
    return _int(what, v)


def _args_data(args: Mapping[str, ir.Value]) -> Plain:
    return {k: _value_data(v) for k, v in args.items()}


def _args(what: str, d: Any) -> dict[str, ir.Value]:
    return {k: _value(f"{what} {k}", v) for k, v in _only(what, d).items()}


def _entry_data(e: ir.Entry) -> str:
    match e:
        case None:
            return "-"
        case ir.External(addr):
            return f"extern 0x{addr:08X}"
        case _:
            return e


def _entry(what: str, v: Any) -> ir.Entry:
    text = _str(what, v)
    if text == "-":
        return None
    if text.startswith("extern "):
        try:
            return ir.External(int(text[7:], 0))
        except ValueError:
            raise DocError(f"{what}: {text!r} is no address") from None
    return text


def _node_data(n: ir.Node) -> Plain:
    d: Plain = {"op": n.op.name}
    if n.args:
        d["args"] = _args_data(n.args)
    if n.origin:
        d["origin"] = n.origin
    if isinstance(n, ir.Block):
        if n.head:
            d["head"] = [_node_data(x) for x in n.head]
        if n.arms:
            d["arms"] = [_arm_data(a, True) for a in n.arms]
    return d


def _arm_data(a: ir.Arm, body: bool) -> Plain:
    d: Plain = {}
    if a.sub != 1:
        d["sub"] = a.sub
    if a.args:
        d["args"] = _args_data(a.args)
    if a.origin:
        d["origin"] = a.origin
    if body and a.body:
        d["body"] = [_node_data(x) for x in a.body]
    return d


def _node(d: Any) -> ir.Node:
    d = _only("a node", d, ("op", "args", "origin", "head", "arms"))
    name = _str("a node's op", d.get("op"))
    head = [_node(x) for x in _list(f"{name} head", d.get("head", []))]
    arms = [_arm(x) for x in _list(f"{name} arms", d.get("arms", []))]
    try:
        n = edit.build(name, _args(name, d.get("args", {})), head=head, arms=arms)
    except DocError:
        raise
    except EditError as e:
        raise DocError(str(e)) from None
    if "origin" in d:
        n.origin = _str(f"{name} origin", d["origin"])
    return n


def _arm(d: Any) -> ir.Arm:
    d = _only("an arm", d, ("sub", "args", "origin", "body"))
    body = [_node(x) for x in _list("an arm's body", d.get("body", []))]
    origin = _str("an arm's origin", d["origin"]) if "origin" in d else None
    sub = _int("an arm's sub", d.get("sub", 1))
    return ir.Arm(sub, _args("an arm", d.get("args", {})), body, origin=origin)


def _code_data(nodes: Sequence[ir.Node], code: Code | None) -> str | list[Plain]:
    return code(nodes) if code else [_node_data(n) for n in nodes]


def _code(what: str, v: Any, parse: Parse | None) -> list[ir.Node]:
    if isinstance(v, str):
        if parse is None:
            raise DocError(f"{what} is code text, and nothing reads it")
        return parse(v)
    return [_node(x) for x in _list(what, v)]


def _anchor_data(a: Anchor) -> Plain:
    return {a.kind: a.ref} | ({} if a.at is None else {"at": a.at})


def _anchor(what: str, d: Plain) -> Anchor:
    kinds = [k for k in KINDS if k in d]
    if len(kinds) != 1:
        raise DocError(f"{what} needs one of before, after, into")
    at = _int(f"{what} at", d["at"]) if "at" in d else None
    try:
        return Anchor(kinds[0], _str(f"{what} {kinds[0]}", d[kinds[0]]), at)
    except DocError:
        raise
    except EditError as e:
        raise DocError(f"{what}: {e}") from None


def list_field(name: str) -> int | None:
    """The header field a list is called by (`call`, `deep2`); None for a name that is none."""
    if name in LISTS:
        return LISTS[name]
    m = _DEEP.fullmatch(name)
    return FIRST_DEEP + int(m[1]) if m else None


def _list_key(what: str, key: str) -> tuple[str, int]:
    header, _, name = key.partition(".")
    field_ = list_field(name)
    if not _NAME.fullmatch(header) or field_ is None:
        raise DocError(f"{what} {key!r} is no header.list")
    return header, field_


def _to_data(es: EditSet, code: Code | None) -> Plain:
    out: Plain = {"build": es.build}
    if es.also:
        out["also"] = list(es.also)
    nodes: Plain = {}
    for nid, ne in sorted(es.nodes.items()):
        d: Plain = {}
        if ne.op is not None:
            d["op"] = ne.op
        if ne.args:
            d["args"] = _args_data(ne.args)
        if ne.delete:
            d["delete"] = True
        if ne.bypass is not None:
            d["bypass"] = ne.bypass
        if ne.move:
            d["move"] = _anchor_data(ne.move)
        if ne.arms is not None:
            d["arms"] = list(ne.arms)
        if d:
            nodes[nid] = d
    inserts: Plain = {}
    for key, ins in sorted(es.inserts.items(), key=lambda kv: _number(kv[0])):
        d = _anchor_data(ins.anchor)
        if ins.arm is not None:
            d["arm"] = _arm_data(ins.arm, False)
        d["code"] = _code_data(ins.arm.body if ins.arm else ins.nodes, code)
        inserts[key] = d
    lists: Plain = {}
    for key, le in sorted(es.lists.items()):
        d = {}
        if le.fork:
            d["fork"] = True
        if le.slots:
            d["set"] = {str(i): _entry_data(e) for i, e in sorted(le.slots.items())}
        if le.append:
            d["append"] = [_entry_data(e) for e in le.append]
        if d:
            lists[key] = d
    for name, value in (
        ("node", nodes),
        ("insert", inserts),
        ("routine", {k: _code_data(v, code) for k, v in sorted(es.routines.items())}),
        ("list", lists),
        ("name", dict(sorted(es.names.items()))),
        ("note", dict(sorted(es.notes.items()))),
    ):
        if value:
            out[name] = value
    return out


def _number(key: str) -> int:
    m = _KEY.fullmatch(key)
    return int(m[1]) if m else 1 << 31


def _from_data(row: int, data: Any, parse: Parse | None) -> EditSet:
    top = ("build", "also", "node", "insert", "routine", "list", "name", "note")
    d = _only("the edit set", data, top)
    es = EditSet(row, _str("build", d.get("build", "")))
    es.also = [_int("also", v) for v in _list("also", d.get("also", []))]
    for nid, v in _only("node", d.get("node", {})).items():
        v = _only(f"node {nid}", v, ("op", "args", "delete", "bypass", "move", "arms"))
        delete = v.get("delete", False)
        if not isinstance(delete, bool):
            raise DocError(f"node {nid} delete is no yes or no")
        es.nodes[nid] = NodeEdit(
            _args(f"node {nid}", v.get("args", {})),
            delete,
            _str(f"node {nid} bypass", v["bypass"]) if "bypass" in v else None,
            _anchor(f"node {nid} move", _only("move", v["move"], (*KINDS, "at")))
            if "move" in v
            else None,
            [_str(f"node {nid} arms", x) for x in _list(f"node {nid} arms", v["arms"])]
            if "arms" in v
            else None,
            _str(f"node {nid} op", v["op"]) if "op" in v else None,
        )
    for key, v in _only("insert", d.get("insert", {})).items():
        if not _KEY.fullmatch(key):
            raise DocError(f"an insert is called u<k>, not {key!r}")
        v = _only(f"insert {key}", v, (*KINDS, "at", "arm", "code"))
        anchor = _anchor(f"insert {key}", {k: v[k] for k in (*KINDS, "at") if k in v})
        body = _code(f"insert {key} code", v.get("code", []), parse)
        if "arm" in v:
            arm = _arm({**_only(f"insert {key} arm", v["arm"], ("sub", "args", "origin"))})
            arm.body = body
            es.inserts[key] = Insert(anchor, [], arm)
        else:
            es.inserts[key] = Insert(anchor, body)
    for name, v in _only("routine", d.get("routine", {})).items():
        if not _NAME.fullmatch(name):
            raise DocError(f"{name!r} is no routine name")
        es.routines[name] = _code(f"routine {name}", v, parse)
    for key, v in _only("list", d.get("list", {})).items():
        _list_key("list", key)
        v = _only(f"list {key}", v, ("fork", "set", "append"))
        fork = v.get("fork", False)
        if not isinstance(fork, bool):
            raise DocError(f"list {key} fork is no yes or no")
        slots = _only(f"list {key} set", v.get("set", {}))
        try:
            numbered = {int(i): _entry(f"list {key} slot {i}", e) for i, e in slots.items()}
        except ValueError:
            raise DocError(f"list {key} set names a slot that is no number") from None
        appended = [_entry(f"list {key} append", e) for e in _list("append", v.get("append", []))]
        es.lists[key] = ListEdit(numbered, appended, fork)
    for field_, table in (("name", es.names), ("note", es.notes)):
        for k, v in _only(field_, d.get(field_, {})).items():
            table[k] = _str(f"{field_} {k}", v)
    return es


# ---- apply


@dataclass
class Applied:
    program: ir.Program
    """Resolved, so it assembles as it is; unresolved where a finding says a label has no
    entry. Shares what the edits leave alone with the lift."""
    findings: list[Finding] = field(default_factory=list)
    touched: tuple[str, ...] = ()
    """The routines the program has new objects for, native ones first, then the new ones."""

    @property
    def ok(self) -> bool:
        return not any(f.level == "error" for f in self.findings)


def _natural(key: str) -> tuple[int, str]:
    return _number(key), key


def _stamp(prefix: str, items: Sequence[ir.Located]) -> None:
    """Name new code `<prefix>.<i>` in source order, arms too, and make it addressless."""
    for i, x in enumerate(edit.everything(items)):
        x.id, x.pc = f"{prefix}.{i}", None
        if isinstance(x, ir.Block):
            x.close = None


def _carry(
    op: Op, have: Mapping[str, ir.Value], given: Mapping[str, ir.Value], sub: int | None = None
) -> dict[str, ir.Value]:
    """The operands of a node that becomes `op` (or of an arm of kind `sub` of a block that does):
    those the edits give, else those it has by name. A name neither has is an error."""
    missing: list[str] = []

    def pick(o: Operand) -> ir.Value:
        if o.name in given:
            return given[o.name]
        if o.name in have:
            return have[o.name]
        if o.name not in missing:
            missing.append(o.name)
        return 0

    out: dict[str, ir.Value] = {}
    if sub is not None:
        out = {o.name: pick(o) for o in operands(op.layout(sub))}
    elif op.coded:
        out = {o.name: pick(o) for o in op.opener}
    else:
        if op.args:
            first = operands(op.args)[0]
            out[first.name] = pick(first)
        for o in operands(op.plain_layout(out)):
            out[o.name] = pick(o)
    if missing:
        raise EditError(f"{op.name} needs {missing} too")
    return out


class _Apply:
    def __init__(self, lift: ir.Program, es: EditSet) -> None:
        self.lift, self.es = lift, es
        self.out: list[Finding] = []
        self.index: ir.Index
        self.names: set[str] = set()
        self.native: dict[str, ir.Routine] = {}
        self.copies: dict[str, ir.Routine] = {}
        self.fresh: list[ir.Routine] = []
        self.scope = ir.Program(lift.row, lift.base, [], lift.end, lift.sha1)
        """The routines the edits work on: copies of the native ones they touch, then the new
        ones. Edits are made here, so the lift is never written."""
        self.skip: set[str] = set()
        self.dead: set[str] = set()
        self.owned: set[str] = set()
        self.result = lift

    def add(
        self,
        code: str,
        text: str,
        node: str | None = None,
        item: str | None = None,
        *,
        info: bool = False,
    ) -> None:
        self.out.append(Finding("info" if info else "error", code, text, node, item))

    def item(self, ref: str) -> str | None:
        """The routine a native id is in."""
        return self.index.routine(ref).name if ref in self.index else None

    # ---- before the edits

    def prepare(self) -> None:
        """What the edits are looked up in; not made for a set that changes nothing."""
        self.index = ir.Index(self.lift)
        self.names = edit.taken(self.lift)
        self.native = {r.name: r for r in self.lift.routines()}

    def build(self) -> bool:
        es, lift = self.es, self.lift
        if lift.row != es.row and lift.row not in es.also:
            self.add("DOC_BUILD", f"the edits are for row {es.row}, not row {lift.row}")
        elif es.build.lower() != lift.sha1:
            self.add("DOC_BUILD", f"the edits are for build {es.build[:8]}, not {lift.sha1[:8]}")
        else:
            if lift.row != es.row:
                self.es = es.retarget(lift.row)
            return True
        return False

    def touch(self, name: str) -> None:
        """Work on a copy of a native routine."""
        if name in self.copies or name not in self.native:
            return
        r = self.native[name]
        self.copies[name] = ir.Routine(name, edit.copy(r.body), r.tail, r.va)
        self.scope.items.append(self.copies[name])

    def collect(self) -> None:
        """Copy the routines the edits name, and set aside edits that cannot go together."""
        es = self.es
        for nid, ne in sorted(es.nodes.items()):
            if ne.is_empty():
                continue
            if nid not in self.index:
                self.add("DOC_ANCHOR", f"no node {nid} in this build", nid)
                self.skip.add(nid)
                continue
            if why := self.conflict(ne, self.index.node(nid)):
                self.add("DOC_CONFLICT", f"{nid}: {why}", nid, self.item(nid))
                self.skip.add(nid)
                continue
            self.touch(self.index.routine(nid).name)
        refs = [ne.move.ref for k, ne in es.nodes.items() if ne.move and k not in self.skip]
        refs += [ins.anchor.ref for ins in es.inserts.values()]
        for ref in sorted(set(refs)):
            if ref in self.native:
                self.touch(ref)
            elif ref in self.index:
                self.touch(self.index.routine(ref).name)
        self.sweep()

    def conflict(self, ne: NodeEdit, n: ir.Located) -> str | None:
        rest = bool(
            ne.args or ne.bypass is not None or ne.move or ne.arms is not None or ne.op is not None
        )
        if ne.delete and rest:
            return "deleted, so nothing else can be done to it"
        if ne.bypass is not None and (ne.args or ne.arms is not None or ne.op is not None):
            return "bypassed, so its operands, opcode and arms are no matter"
        if (ne.bypass is not None or ne.arms is not None) and not isinstance(n, ir.Block):
            return "bypass and arm order are for blocks"
        if ne.op is not None and isinstance(n, ir.Arm):
            return "an arm has no opcode"
        if ne.move and isinstance(n, ir.Arm):
            return "an arm moves by the order of its block's arms"
        if ne.bypass in self.es.nodes and self.es.nodes[ne.bypass].delete:
            return f"it keeps {ne.bypass}, which is deleted"
        return None

    def sweep(self) -> None:
        """Find what the deletes and bypasses take away, a node that moves out of it excepted:
        an anchor there is lost."""
        nodes = self.es.nodes

        def edits(i: str) -> NodeEdit | None:
            return None if i in self.skip else nodes.get(i)

        def go(items: Sequence[ir.Node], gone: bool) -> None:
            for n in items:
                ne = edits(n.id)
                here = (gone and not (ne and ne.move)) or bool(ne and ne.delete)
                if here:
                    self.dead.add(n.id)
                keep = ne.bypass if ne and isinstance(n, ir.Block) else None
                if isinstance(n, ir.Block):
                    go(n.head, here or (keep is not None and keep != "then"))
                    for k, a in enumerate(n.arms):
                        kept = keep == a.id or (keep == "else" and k == 0)
                        ae = edits(a.id)
                        out = here or bool(ae and ae.delete) or (keep is not None and not kept)
                        if out:
                            self.dead.add(a.id)
                        go(a.body, out)

        for name in self.copies:
            go(self.native[name].body, False)

    # ---- the edits

    def attempt(
        self,
        key: str,
        run: Callable[[], None],
        *refs: str,
        vanished: bool = False,
        item: str | None = None,
    ) -> bool:
        """Run an edit; an EditError is a finding, an anchor if one of `refs` is nowhere. A
        node that is gone with the block above it, if `vanished`, is no finding. `item` says the
        key names a routine or list, not a node."""
        try:
            run()
        except EditError as e:
            here = ir.Index(self.scope)
            if vanished and key not in here:
                return True
            routines = {r.name for r in self.scope.routines()}
            code = (
                "DOC_ANCHOR"
                if any(r not in here and r not in routines for r in refs)
                else "DOC_CONFLICT"
            )
            if item is None:
                self.add(code, f"{key}: {e}", key, self.item(key))
            else:
                self.add(code, f"{key}: {e}", None, item)
            return False
        return True

    def routines(self) -> None:
        for name in sorted(self.es.routines):
            if not _NAME.fullmatch(name) or _KEY.fullmatch(name) or name in self.names:
                self.add("DOC_CONFLICT", f"{name} is no name for a new routine", None, name)
                continue
            body = edit.copy(self.es.routines[name])
            _stamp(name, body)
            r = ir.Routine(name, body)
            if self.attempt(name, partial(edit.add, self.scope, r), item=name):
                self.names.add(name)
                self.fresh.append(r)

    def ops(self) -> None:
        for nid, ne in sorted(self.es.nodes.items()):
            if ne.op is not None and nid not in self.skip:
                self.attempt(nid, partial(self.retype, nid, ne.op), nid)

    def retype(self, nid: str, name: str) -> None:
        """Make the node `nid` another opcode, in place."""
        try:
            op = BY_NAME[name]
        except KeyError:
            raise EditError(f"{name} is no opcode") from None
        index = ir.Index(self.scope)
        n = index.node(nid)
        if isinstance(n, ir.Arm) or op is n.op:
            return
        given = self.es.nodes[nid].args
        if isinstance(n, ir.Insn):
            if op.coded:
                raise EditError(f"{n.op.name} cannot become the block {name}")
            args = _carry(op, n.args, given)
            edit.build(name, args)
            n.op, n.args = op, args
        else:
            if op.shape is not n.op.shape:
                raise EditError(f"{n.op.name} cannot become {name}: the blocks are not alike")
            args = _carry(op, n.args, given)
            carried = [_carry(op, a.args, self.arm_edits(a.id), a.sub) for a in n.arms]
            edit.build(
                name,
                args,
                head=n.head,
                arms=[ir.Arm(a.sub, c, []) for a, c in zip(n.arms, carried, strict=True)],
            )
            n.op, n.args = op, args
            for a, c in zip(n.arms, carried, strict=True):
                if c != a.args:
                    a.args = c
                    if a.pc is not None:
                        a.origin, a.pc = a.id or None, None
        if n.pc is not None:
            n.origin, n.pc = n.id or None, None

    def arm_edits(self, nid: str) -> dict[str, ir.Value]:
        ne = self.es.nodes.get(nid)
        return {} if ne is None or nid in self.skip else ne.args

    def args(self) -> None:
        for nid, ne in sorted(self.es.nodes.items()):
            if ne.args and nid not in self.skip:
                self.attempt(nid, partial(edit.modify, self.scope, nid, **ne.args), nid)

    def arm_order(self) -> None:
        for nid, ne in sorted(self.es.nodes.items()):
            if ne.arms is not None and nid not in self.skip:
                self.attempt(nid, partial(edit.reorder_arms, self.scope, nid, ne.arms), nid)

    def drain(
        self,
        keys: Iterable[str],
        dep: Callable[[str], str | None],
        run: Callable[[str], bool],
        order: Callable[[str], Any] = _natural,
    ) -> None:
        """`run` the first of the keys, by `order`, whose dependency has run, and again, so what
        waited for another goes where its place in the order is; one whose dependency failed is a
        finding, and so is a cycle."""
        pending = sorted(keys, key=order)
        done: set[str] = set()
        lost: set[str] = set()
        while pending:
            for k in pending:
                d = dep(k)
                if d is None or d in done:
                    (done if run(k) else lost).add(k)
                elif d in lost:
                    lost.add(k)
                    self.add("DOC_ANCHOR", f"{k}: anchored in {d}, which was not placed", k)
                else:
                    continue
                pending.remove(k)
                break
            else:
                for k in pending:
                    self.add("DOC_CONFLICT", f"{k}: its anchors go round in a circle", k)
                return

    def place(self) -> None:
        """The moves and the inserts, each after what it is anchored in: an insert, or a game node
        that has a move of its own. Left to themselves the moves go first, so an insert beside a
        game node that something moved beside lands next to it, the move after it."""
        moves = {
            nid: ne.move for nid, ne in self.es.nodes.items() if ne.move and nid not in self.skip
        }
        ins = self.es.inserts

        def dep(key: str) -> str | None:
            ref = (moves[key] if key in moves else ins[key].anchor).ref
            owner = _owner(ref)
            if owner in ins and owner != key:
                return owner
            return ref if ref in moves and ref != key else None

        def move(nid: str) -> bool:
            a = moves[nid]
            if a.ref in self.dead:
                self.add("DOC_ANCHOR", f"{nid}: anchored at {a.ref}, which is not there", nid)
                return False
            if a.kind == "into" and a.at not in (None, 0):
                self.add("DOC_CONFLICT", f"{nid}: a move goes to the start or the end", nid)
                return False
            where = a.kind if a.kind != "into" else "end" if a.at is None else "start"
            return self.attempt(nid, partial(edit.move, self.scope, nid, a.ref, where), a.ref)

        def insert(key: str) -> bool:
            i, a = ins[key], ins[key].anchor
            if a.ref in self.dead:
                self.add("DOC_ANCHOR", f"{key}: anchored at {a.ref}, which is not there", key)
                return False
            if i.arm is not None and i.nodes:
                self.add("DOC_CONFLICT", f"{key} is an arm and nodes both", key)
                return False
            beside = a.kind != "into"
            fn: Callable[[], None]
            if i.arm is None:
                nodes = edit.copy(i.nodes)
                _stamp(key, nodes)
                if beside:
                    fn = partial(edit.insert, self.scope, a.ref, nodes, after=a.kind == "after")
                else:
                    fn = partial(edit.put_in, self.scope, a.ref, nodes, a.at)
            else:
                arm = edit.copy_arm(i.arm)
                _stamp(key, [arm])
                if beside:
                    fn = partial(edit.insert_arm, self.scope, a.ref, arm, after=a.kind == "after")
                else:
                    fn = partial(edit.add_arm, self.scope, a.ref, arm, a.at)
            return self.attempt(key, fn, a.ref)

        self.drain(
            [*moves, *ins],
            dep,
            lambda key: move(key) if key in moves else insert(key),
            lambda key: (key not in moves, _natural(key)),
        )

    def bypass(self) -> None:
        for nid, ne in sorted(self.es.nodes.items()):
            if ne.bypass is not None and nid not in self.skip:
                keep = None if ne.bypass == NOTHING else ne.bypass
                self.attempt(nid, partial(edit.unwrap, self.scope, nid, keep), nid, vanished=True)

    def delete(self) -> None:
        for nid, ne in sorted(self.es.nodes.items()):
            if ne.delete and nid not in self.skip:
                gone = edit.delete_arm if isinstance(self.index.node(nid), ir.Arm) else edit.remove
                self.attempt(nid, partial(gone, self.scope, nid), nid, vanished=True)

    # ---- lists

    def assemble(self) -> None:
        """The program: the lift's items, the routines the edits changed in their place, the new
        routines last."""
        self.copies = {k: r for k, r in self.copies.items() if r.body != self.native[k].body}
        items: list[ir.Item] = [
            self.copies.get(i.name, i) if isinstance(i, ir.Routine) else i for i in self.lift.items
        ]
        items += self.fresh
        lift = self.lift
        self.result = ir.Program(lift.row, lift.base, items, lift.end, lift.sha1)

    def own(self, name: str) -> None:
        """Replace a list or header by a copy of its own, once, before it is written."""
        items = self.result.items
        for k, it in enumerate(items):
            match it:
                case ir.Table() if it.name == name and name not in self.owned:
                    items[k] = ir.Table(it.name, list(it.slots), it.va)
                case ir.Header() if it.name == name and name not in self.owned:
                    items[k] = ir.Header(it.name, list(it.fields), it.variants, it.va)
                case _:
                    continue
            self.owned.add(name)
            return

    def lists(self) -> None:
        for key, le in sorted(self.es.lists.items()):
            if le.is_empty():
                continue
            try:
                hname, fld = _list_key("list", key)
            except DocError as e:
                self.add("DOC_ANCHOR", str(e), None, key)
                continue
            header = next((h for h in self.result.headers() if h.name == hname), None)
            table = header.fields[fld] if header and fld < len(header.fields) else None
            if header is None or not isinstance(table, str):
                self.add("DOC_ANCHOR", f"{key}: the header has no such list", None, key)
                continue
            if le.fork:
                self.own(hname)
                try:
                    table = edit.fork_table(self.result, hname, fld)
                except EditError as e:
                    self.add("DOC_CONFLICT", f"{key}: {e}", None, key)
                    continue
                self.owned.add(table)
            else:
                others = [
                    h.name for h in self.result.headers() if h.name != hname and table in h.fields
                ]
                if others:
                    text = f"{key}: {table} is also the list of {', '.join(others)}"
                    self.add("SHARED_LIST", text, None, table, info=True)
                self.own(table)
            self.write(key, table, le)

    def write(self, key: str, table: str, le: ListEdit) -> None:
        names = edit.taken(self.result)
        puts = [(i, e, True) for i, e in sorted(le.slots.items())] + [
            (0, e, False) for e in le.append
        ]
        for index, entry, fixed in puts:
            if isinstance(entry, str) and entry not in names:
                self.add("DOC_ANCHOR", f"{key}: nothing is named {entry}", None, table)
                continue
            at = index if fixed else len(self.result.tables()[table].slots)
            self.attempt(key, partial(edit.set_slot, self.result, table, at, entry), item=table)

    # ---- labels

    def resolve(self) -> ir.Program:
        """Swap the labels of the new code for list indexes; only the new code has any."""
        names = edit.taken(self.result)
        lost = labelled = False
        for r in self.scope.routines():
            for n in ir.walk(r.body):
                for v in n.args.values():
                    if isinstance(v, ir.Sym):
                        labelled = True
                        if v.label not in names:
                            self.add("DOC_ANCHOR", f"nothing is named {v.label}", n.id, r.name)
                            lost = True
        if lost or not labelled:
            return self.result
        try:
            return edit.resolve(self.result, share=True)
        except EditError as e:
            self.add("DOC_CONFLICT", str(e))
            return self.result

    def run(self) -> Applied:
        if not self.build() or self.es.is_empty():
            return Applied(self.lift, self.out)
        self.prepare()
        self.collect()
        self.routines()
        self.ops()
        self.args()
        self.arm_order()
        self.place()
        self.bypass()
        self.delete()
        self.assemble()
        self.lists()
        touched = (*self.copies, *(r.name for r in self.fresh))
        return Applied(self.resolve(), self.out, touched)


def apply(lift: ir.Program, edits: EditSet) -> Applied:
    """The edits made to the lift: a program, and the findings on what could not be done. The
    edits belong to `lift`'s row, or to a row they list in `also`."""
    return _Apply(lift, edits).run()
