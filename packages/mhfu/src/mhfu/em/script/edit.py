# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Edit a program in place: build nodes, put them in routines, change lists and headers, and
turn the labels of new code into list indexes.

    from mhfu.em.script import edit, ir
    edit.replace(p, hub.id, edit.insn("enter", 3, 0, 0))
    edit.add(p, ir.Routine("spin", [edit.insn("enter", 3, 0, 0), edit.insn("end", 1)]))
    edit.put(p, "base_0", [edit.insn("call", ir.Sym("spin"))])
    q = edit.resolve(p)                 # a copy: `call` has its list index now

Builders return nodes with no id; inserting gives each (arms too) an `n<k>`. An edit that
cannot be done raises EditError and leaves the program as it was.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from . import ir
from .opcodes import BY_NAME, COUNT, COUNTED, FIRST_DEEP, Op, Operand, Ref, Shape, operands, slot

_RANDOM_TOTAL = 32
_RANDOM_BRANCHES = 13
_T = TypeVar("_T", bound=ir.Item)


class EditError(ValueError):
    """An edit the program cannot take."""


# ---- builders


def _op(name: str) -> Op:
    try:
        return BY_NAME[name]
    except KeyError:
        raise EditError(f"{name} is no opcode") from None


def _bind(what: str, names: Sequence[Operand], values: Sequence[ir.Value]) -> dict[str, ir.Value]:
    """The operands by name, each checked against its width."""
    if len(names) != len(values):
        raise EditError(f"{what} takes {[o.name for o in names]}, not {len(values)} operand(s)")
    for o, v in zip(names, values, strict=True):
        if isinstance(v, ir.Sym):
            continue
        if not isinstance(v, int):
            raise EditError(f"{what}: {o.name} {v!r} is no number")
        try:
            v.to_bytes(o.size, "big", signed=o.signed)
        except OverflowError:
            raise EditError(f"{what}: {o.name} {v} does not fit {o.size} byte(s)") from None
    return {o.name: v for o, v in zip(names, values, strict=True)}


def _opener(op: Op) -> tuple[Operand, ...]:
    """The operands a block's builder takes: its opener's, less the derived count."""
    return tuple(o for o in operands(op.args) if not (o.name == COUNT and op.shape in COUNTED))


def _block(name: str, *shapes: Shape) -> Op:
    op = _op(name)
    if op.shape not in shapes:
        raise EditError(f"{name} is not a {'/'.join(s.value for s in shapes)} block")
    return op


def insn(name: str, *args: ir.Value) -> ir.Insn:
    """A plain instruction, operands by position; the first picks the layout where it varies
    (`set_target`)."""
    op = _op(name)
    if op.coded:
        raise EditError(f"{name} opens a block: use if_, switch, random or repeat")
    first = args[0] if args else None
    layout = op.layout(first) if op.by and isinstance(first, int) else op.args
    return ir.Insn(op, _bind(name, operands(layout), args))


def if_(
    name: str,
    *args: ir.Value,
    then: Sequence[ir.Node],
    else_: Sequence[ir.Node] | None = None,
) -> ir.Block:
    op = _block(name, Shape.IF)
    arms = [] if else_ is None else [ir.Arm(1, {}, list(else_))]
    return ir.Block(op, _bind(name, _opener(op), args), list(then), arms)


def switch(
    name: str,
    *args: ir.Value,
    cases: Sequence[Sequence[Any]],
    default: Sequence[ir.Node] | None = None,
) -> ir.Block:
    """A switch, bucket or zone block; a case is `(*operands, body)` with the operands of
    `op.label`, so `(body,)` where it has none."""
    op = _block(name, Shape.SWITCH, Shape.BUCKET, Shape.ZONE)
    if default is not None and op.shape is not Shape.SWITCH:
        raise EditError(f"{name} has no default")
    arms = []
    for *vals, body in cases:
        arms.append(ir.Arm(1, _bind(f"{name} case", operands(op.label), vals), list(body)))
    if default is not None:
        arms.append(ir.Arm(2, {}, list(default)))
    return ir.Block(op, _bind(name, _opener(op), args), [], arms)


def random(branches: Sequence[tuple[int, Sequence[ir.Node]]], shift: int | None = None) -> ir.Block:
    """A weighted pick, `[(weight, body), ...]`. Weights under 32 get an empty last branch with
    the rest; `shift` makes it a `random_shifted`."""
    total = sum(w for w, _ in branches)
    if total > _RANDOM_TOTAL or any(w < 0 for w, _ in branches):
        raise EditError(f"random weights add up to {total}, and 32 is all there is")
    arms = [ir.Arm(1, {"weight": w}, list(body)) for w, body in branches]
    if total < _RANDOM_TOTAL:
        arms.append(ir.Arm(1, {"weight": _RANDOM_TOTAL - total}, []))
    if len(arms) > _RANDOM_BRANCHES:
        raise EditError(f"a random has at most {_RANDOM_BRANCHES} branches")
    op = _op("random" if shift is None else "random_shifted")
    return ir.Block(op, {} if shift is None else _bind(op.name, _opener(op), [shift]), [], arms)


def repeat(count: int, body: Sequence[ir.Node]) -> ir.Block:
    op = _op("repeat")
    return ir.Block(op, _bind("repeat", _opener(op), [count]), list(body), [])


# ---- copies


def _ident(n: ir.Located, clone: bool) -> dict[str, Any]:
    if clone:
        return {"id": "", "pc": None, "origin": n.id or None}
    return {"id": n.id, "pc": n.pc, "origin": n.origin}


def _copy(nodes: Sequence[ir.Node], clone: bool) -> list[ir.Node]:
    """A deep copy; a clone has no ids and addresses, and names its source in `origin`."""
    out: list[ir.Node] = []
    for n in nodes:
        if isinstance(n, ir.Insn):
            out.append(ir.Insn(n.op, dict(n.args), **_ident(n, clone)))
            continue
        arms = [
            ir.Arm(a.sub, dict(a.args), _copy(a.body, clone), **_ident(a, clone)) for a in n.arms
        ]
        close = None if clone else n.close
        head = _copy(n.head, clone)
        out.append(ir.Block(n.op, dict(n.args), head, arms, close=close, **_ident(n, clone)))
    return out


def clone(nodes: Sequence[ir.Node]) -> list[ir.Node]:
    """Copies of `nodes` to insert elsewhere: new code, each naming its source in `origin`."""
    return _copy(nodes, True)


def _program(p: ir.Program) -> ir.Program:
    """A copy that shares nothing mutable with `p`."""
    items: list[ir.Item] = []
    for it in p.items:
        match it:
            case ir.Routine():
                items.append(ir.Routine(it.name, _copy(it.body, False), it.tail, it.va))
            case ir.Table():
                items.append(ir.Table(it.name, list(it.slots), it.va))
            case ir.Data():
                items.append(ir.Data(it.name, it.data, dict(it.labels), it.va))
            case ir.Blob():
                items.append(ir.Blob(it.data, it.va))
            case ir.Header():
                items.append(ir.Header(it.name, list(it.fields), it.variants, it.va))
    return ir.Program(p.row, p.base, items, p.end, p.sha1)


# ---- node edits


def _anchor(index: ir.Index, nid: str) -> ir.Located:
    try:
        return index.node(nid)
    except KeyError:
        raise EditError(f"no node {nid}") from None


def _ids(p: ir.Program, index: ir.Index, nodes: Sequence[ir.Node]) -> None:
    """Give the nodes (arms too) with no id a fresh one; an id in use is an error."""
    mine = list(ir.walk(nodes))
    seen: set[str] = set()
    for n in mine:
        if n.id and (n.id in seen or n.id in index):
            raise EditError(f"{n.id} is in the program already")
        seen.add(n.id)
    k = int(p.fresh(mine)[1:])
    for n in mine:
        if not n.id:
            n.id, k = f"n{k}", k + 1


def _position(home: list[ir.Node], n: ir.Node) -> int:
    """Where `n` itself sits in `home`; a list's `index` would find an equal node."""
    return next(i for i, x in enumerate(home) if x is n)


def insert(p: ir.Program, nid: str, nodes: Sequence[ir.Node], *, after: bool = False) -> None:
    """Put `nodes` before the node `nid`, or after it. For an arm, at the start of its body, or
    the end."""
    index = ir.Index(p)
    at = _anchor(index, nid)
    new = list(nodes)
    _ids(p, index, new)
    if isinstance(at, ir.Arm):
        k = len(at.body) if after else 0
        at.body[k:k] = new
    else:
        home = index.home(nid)
        k = _position(home, at) + after
        home[k:k] = new


def put(p: ir.Program, routine: str, nodes: Sequence[ir.Node], at: int = 0) -> None:
    """Put `nodes` into a routine's body at position `at`: the way into an empty one."""
    r = _item(p, routine, ir.Routine)
    if not 0 <= at <= len(r.body):
        raise EditError(f"{routine} has {len(r.body)} nodes; {at} is out of range")
    new = list(nodes)
    _ids(p, ir.Index(p), new)
    r.body[at:at] = new


def _owned(index: ir.Index, nid: str) -> tuple[ir.Node, list[ir.Node]]:
    n = _anchor(index, nid)
    if isinstance(n, ir.Arm):
        raise EditError(f"{nid} is an arm, not a node")
    return n, index.home(nid)


def remove(p: ir.Program, nid: str) -> None:
    n, home = _owned(ir.Index(p), nid)
    del home[_position(home, n)]


def replace(p: ir.Program, nid: str, node: ir.Node) -> None:
    index = ir.Index(p)
    n, home = _owned(index, nid)
    _ids(p, index, [node])
    home[_position(home, n)] = node


# ---- items


def _item(p: ir.Program, name: str, kind: type[_T]) -> _T:
    for i in p.items:
        if isinstance(i, kind) and getattr(i, "name", None) == name:
            return i
    raise EditError(f"no {kind.__name__.lower()} named {name}")


def _names(item: ir.Item) -> set[str]:
    """The names an item answers to: its own, and a data run's labels."""
    match item:
        case ir.Blob():
            return set()
        case ir.Data():
            return {item.name, *item.labels}
        case _:
            return {item.name}


def _taken(p: ir.Program) -> set[str]:
    return {name for i in p.items for name in _names(i)}


def add(p: ir.Program, item: ir.Item) -> None:
    """Append a new item: no address, and a name nothing has."""
    if item.va is not None:
        raise EditError("a new item has no address")
    if clash := sorted(_names(item) & _taken(p)):
        raise EditError(f"{clash[0]} names something already")
    if isinstance(item, ir.Routine):
        _ids(p, ir.Index(p), item.body)
    p.items.append(item)


def set_slot(p: ir.Program, table: str, index: int, entry: ir.Entry) -> None:
    """Set a list's slot; one past the end grows the list by one."""
    t = _item(p, table, ir.Table)
    if not 0 <= index <= len(t.slots):
        raise EditError(f"{table} has {len(t.slots)} entries; slot {index} would leave a gap")
    if index >= ir.MAX_LIST:
        raise EditError(f"{table} cannot hold more than {ir.MAX_LIST} entries")
    t.slots[index : index + 1] = [entry]


def fork_table(p: ir.Program, header: str, field: int) -> str:
    """Give a header its own copy of the list one of its fields names, so that editing it leaves
    the headers that shared the list as they were; the copy's name."""
    h = _item(p, header, ir.Header)
    name = h.fields[field] if field < len(h.fields) else None
    if not isinstance(name, str):
        raise EditError(f"{header} has no list in field {field}")
    taken = _taken(p)
    stem = f"{header}_{slot(field).name}_l"
    new, k = stem, 1
    while new in taken:
        k += 1
        new = f"{stem}{k}"
    p.items.append(ir.Table(new, list(_item(p, name, ir.Table).slots)))
    h.fields[field] = new
    return new


def add_deep(p: ir.Program, header: str, table: str) -> int:
    """Give a header a new field naming `table` as a `call_deep` list; the `table` operand."""
    h = _item(p, header, ir.Header)
    _item(p, table, ir.Table)
    if len(h.fields) < FIRST_DEEP:
        raise EditError(f"{header} has {len(h.fields)} fields, fewer than the fixed {FIRST_DEEP}")
    h.fields.append(table)
    return len(h.fields) - 1 - FIRST_DEEP


# ---- labels


@dataclass
class _Label:
    routine: str
    node: ir.Insn
    operand: str
    label: str
    ref: Ref
    under: dict[str, int] = field(default_factory=dict)
    """The index in the list of each header that reaches the routine."""


def _labels(p: ir.Program) -> list[_Label]:
    """The labels on list operands; a label on any other operand is an error."""
    out: list[_Label] = []
    for r in p.routines():
        for n in ir.walk(r.body):
            tagged = (
                {name: (sym, ref) for name, sym, ref in ir.labels(n)}
                if isinstance(n, ir.Insn)
                else {}
            )
            for name, v in n.args.items():
                if not isinstance(v, ir.Sym):
                    continue
                if name not in tagged or not isinstance(n, ir.Insn):
                    raise EditError(f"{r.name}: {name} is the label {v.label!r} and names no list")
                out.append(_Label(r.name, n, name, v.label, tagged[name][1]))
    return out


def resolve(p: ir.Program) -> ir.Program:
    """A copy with every label replaced by its list index: its slot in the list of each header
    that reaches the routine, a new last slot where it has none. Every header must agree."""
    out = _program(p)
    taken = _taken(out)
    tables = out.tables()
    headers = {h.name: h for h in out.headers()}
    reach = out.reach()
    labels = _labels(out)
    for lab in labels:
        if lab.label not in taken:
            raise EditError(f"nothing is named {lab.label}")
        if not reach[lab.routine]:
            raise EditError(
                f"{lab.routine}: the label {lab.label!r} is in a routine no header reaches"
            )
        for hn in reach[lab.routine]:
            fields = headers[hn].fields
            table = fields[lab.ref.field] if lab.ref.field < len(fields) else None
            if not isinstance(table, str) or table not in tables:
                raise EditError(f"{hn} has no {slot(lab.ref.field).name} list for {lab.label!r}")
            slots = tables[table].slots
            if lab.label not in slots:
                if len(slots) >= ir.MAX_LIST:
                    raise EditError(f"{table} cannot hold more than {ir.MAX_LIST} entries")
                slots.append(lab.label)
            lab.under[hn] = slots.index(lab.label)
        if len(set(lab.under.values())) > 1:
            seen = " and ".join(f"{i} under {hn}" for hn, i in lab.under.items())
            raise EditError(f"{lab.routine}: the label {lab.label!r} is slot {seen}")
    for lab in labels:
        lab.node.args[lab.operand] = next(iter(lab.under.values()))
    return out
