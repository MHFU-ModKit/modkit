# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Edit a program in place: build nodes, put them in routines, change lists and headers, and
turn the labels of new code into list indexes.

    from mhfu.em.script import edit, ir
    edit.replace(p, hub.id, edit.insn("enter", 3, 0, 0))
    edit.add(p, ir.Routine("spin", [edit.insn("enter", 3, 0, 0), edit.insn("end", 1)]))
    edit.put(p, "base_0", [edit.insn("call", ir.Sym("spin"))])
    q = edit.resolve(p)                 # a copy: `call` has its list index now

    edit.modify(p, hub.id, sub=2)       # same id, new operands
    edit.move(p, hub.id, other.id, where="after")
    edit.unwrap(p, block.id, "then")    # the block is gone, its then-branch stays
    edit.reorder_arms(p, pick.id, [b.id, a.id])

Builders return nodes with no id; inserting gives each (arms too) an `n<k>`. A node whose place
or operands an edit changes is new code from then on (no `pc`), its `origin` the id it had. An
edit that cannot be done raises EditError and leaves the program as it was.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from . import asm, ir
from .opcodes import (
    BY_NAME,
    FIRST_DEEP,
    NUMBERED,
    Op,
    Operand,
    Ref,
    Shape,
    marker_sub,
    operands,
    slot,
)

_RANDOM_TOTAL = 32
_RANDOM_BRANCHES = 13
_T = TypeVar("_T", bound=ir.Item)
_N = TypeVar("_N")


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
    return ir.Block(op, _bind(name, op.opener, args), list(then), arms)


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
    return ir.Block(op, _bind(name, op.opener, args), [], arms)


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
    return ir.Block(op, {} if shift is None else _bind(op.name, op.opener, [shift]), [], arms)


def repeat(count: int, body: Sequence[ir.Node]) -> ir.Block:
    op = _op("repeat")
    return ir.Block(op, _bind("repeat", op.opener, [count]), list(body), [])


def _named(what: str, ops: Sequence[Operand], args: Mapping[str, ir.Value]) -> dict[str, ir.Value]:
    """The operands `ops` from `args` by name, each checked against its width."""
    names = [o.name for o in ops]
    if set(args) != set(names):
        raise EditError(f"{what} takes {names}, not {sorted(args)}")
    return _bind(what, ops, [args[n] for n in names])


def _check_arms(op: Op, arms: Sequence[ir.Arm]) -> None:
    """Whether `arms` is what a block of `op` can hold, in this order."""
    shape = op.shape
    subs = [a.sub for a in arms]
    if shape is Shape.IF and (len(arms) > 1 or any(s != 1 for s in subs)):
        raise EditError(f"{op.name} has at most one arm, its else")
    if shape is Shape.SWITCH:
        if any(s not in (1, 2) for s in subs) or subs.count(2) > 1 or 2 in subs[:-1]:
            raise EditError(f"{op.name} takes cases and one default, last")
    if shape in NUMBERED and (not arms or any(s != 1 for s in subs)):
        raise EditError(f"{op.name} needs arms, each of kind 1")
    for k, a in enumerate(arms):
        sub = marker_sub(shape, a.sub, k)
        if op.role(sub) != "mid":
            raise EditError(f"{op.name} has no marker {sub} for its arm {k}")
        _named(f"{op.name} arm", operands(op.layout(sub)), a.args)


def build(
    name: str,
    args: Mapping[str, ir.Value],
    *,
    head: Sequence[ir.Node] = (),
    arms: Sequence[ir.Arm] = (),
) -> ir.Node:
    """The node `name` with operands by name: the dict form of `insn` and the block builders.
    `head` is an if's then-branch or a repeat's body."""
    op = _op(name)
    if not op.coded:
        if head or arms:
            raise EditError(f"{name} is no block")
        return ir.Insn(op, _named(name, operands(op.plain_layout(args)), args))
    if head and op.shape not in (Shape.IF, Shape.REPEAT):
        raise EditError(f"{name} has no head; its nodes go in its arms")
    block = ir.Block(op, _named(name, op.opener, args), list(head), list(arms))
    _check_arms(op, block.arms)
    return block


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


def copy(nodes: Sequence[ir.Node]) -> list[ir.Node]:
    """Copies that share nothing mutable with `nodes`, ids and addresses as they are."""
    return _copy(nodes, False)


def copy_arm(arm: ir.Arm) -> ir.Arm:
    return ir.Arm(arm.sub, dict(arm.args), copy(arm.body), **_ident(arm, False))


def _labelled(nodes: Sequence[ir.Node]) -> bool:
    return any(isinstance(v, ir.Sym) for n in ir.walk(nodes) for v in n.args.values())


def _program(p: ir.Program, share: bool = False) -> ir.Program:
    """A copy that shares nothing mutable with `p`; with `share`, one that keeps the items an
    edit of labels leaves alone: the routines with none, data, blobs and headers."""
    items: list[ir.Item] = []
    for it in p.items:
        match it:
            case ir.Routine():
                keep = share and not _labelled(it.body)
                items.append(it if keep else ir.Routine(it.name, copy(it.body), it.tail, it.va))
            case ir.Table():
                items.append(it if share else ir.Table(it.name, list(it.slots), it.va))
            case ir.Data():
                items.append(it if share else ir.Data(it.name, it.data, dict(it.labels), it.va))
            case ir.Blob():
                items.append(it if share else ir.Blob(it.data, it.va))
            case ir.Header():
                items.append(
                    it if share else ir.Header(it.name, list(it.fields), it.variants, it.va)
                )
    return ir.Program(p.row, p.base, items, p.end, p.sha1)


# ---- node edits


def _anchor(index: ir.Index, nid: str) -> ir.Located:
    try:
        return index.node(nid)
    except KeyError:
        raise EditError(f"no node {nid}") from None


def everything(items: Sequence[ir.Located]) -> Iterator[ir.Located]:
    """The items and everything under them, arms included, in source order."""
    for x in items:
        if isinstance(x, ir.Arm):
            yield x
            yield from ir.walk(x.body)
        else:
            yield from ir.walk([x])


def _ids(p: ir.Program, index: ir.Index, nodes: Sequence[ir.Located]) -> None:
    """Give the nodes (arms too) with no id a fresh one; an id in use is an error."""
    mine = list(everything(nodes))
    seen: set[str] = set()
    for n in mine:
        if n.id and (n.id in seen or n.id in index):
            raise EditError(f"{n.id} is in the program already")
        seen.add(n.id)
    k = int(p.fresh(mine)[1:])
    for n in mine:
        if not n.id:
            n.id, k = f"n{k}", k + 1


def _position(home: Sequence[_N], n: _N) -> int:
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


def _owner(p: ir.Program, arm: ir.Arm) -> ir.Block:
    """The block an arm belongs to."""
    for n in p.nodes():
        if isinstance(n, ir.Block) and any(a is arm for a in n.arms):
            return n
    raise EditError(f"{arm.id} is in no block")


def _bodies(n: ir.Node) -> Iterator[list[ir.Node]]:
    """Every list of nodes in the subtree of `n`."""
    for x in ir.walk([n]):
        if isinstance(x, ir.Block):
            yield x.head
        elif isinstance(x, ir.Arm):
            yield x.body


def _renew(n: ir.Located) -> None:
    """Make `n` new code, as an edit that changes it or its place does: its `pc` goes, its id
    stays, and `origin` says it was native."""
    if n.pc is not None:
        n.origin = n.id or None
        n.pc = None


def _scope(p: ir.Program, index: ir.Index, target: str) -> list[ir.Node]:
    """The list `target` names: a routine's body, a block's head (an if's then-branch, a repeat's
    body) or an arm's body."""
    for it in p.items:
        if isinstance(it, ir.Routine) and it.name == target:
            return it.body
    try:
        at = index.node(target)
    except KeyError:
        raise EditError(f"no routine or node {target}") from None
    if isinstance(at, ir.Arm):
        return at.body
    if isinstance(at, ir.Block):
        if at.op.shape not in (Shape.IF, Shape.REPEAT):
            raise EditError(f"{at.op.name} has no head; name one of its arms")
        return at.head
    raise EditError(f"{target} is an instruction, with no place inside")


def put_in(p: ir.Program, target: str, nodes: Sequence[ir.Node], at: int | None = None) -> None:
    """Put `nodes` into the list `target` names (a routine, a block's head or an arm) at
    position `at`, by default the end."""
    index = ir.Index(p)
    dest = _scope(p, index, target)
    k = len(dest) if at is None else at
    if not 0 <= k <= len(dest):
        raise EditError(f"{target} has {len(dest)} nodes; {k} is out of range")
    new = list(nodes)
    _ids(p, index, new)
    dest[k:k] = new


def modify(p: ir.Program, nid: str, /, **args: ir.Value) -> None:
    """Change operands of a node, block opener or arm, by name. It keeps its id and becomes new
    code. An opcode whose layout depends on its first operand (`set_target`) takes the operands
    of the new layout, keeping those it has."""
    index = ir.Index(p)
    n = _anchor(index, nid)
    if not args:
        raise EditError(f"nothing to change on {nid}")
    match n:
        case ir.Insn():
            what, ops = n.op.name, operands(n.op.plain_layout({**n.args, **args}))
        case ir.Block():
            what, ops = n.op.name, n.op.opener
        case ir.Arm():
            block = _owner(p, n)
            sub = marker_sub(block.op.shape, n.sub, _position(block.arms, n))
            what, ops = f"{block.op.name} arm", operands(block.op.layout(sub))
    names = [o.name for o in ops]
    if extra := sorted(set(args) - set(names)):
        raise EditError(f"{what} takes {names}, not {extra}")
    merged = {**n.args, **args}
    if missing := [k for k in names if k not in merged]:
        raise EditError(f"{what} needs {missing} too")
    n.args = _bind(what, ops, [merged[k] for k in names])
    _renew(n)


def move(p: ir.Program, nid: str, to: str, where: str = "before") -> None:
    """Move a node, with all that is in it, before or after the node `to` (an arm: at the start
    or the end of its body), or `where` is `start` or `end` of the list `to` names (a routine, a
    block's head or an arm). It becomes new code."""
    if where not in ("before", "after", "start", "end"):
        raise EditError(f"{where!r} is no place: before, after, start or end")
    index = ir.Index(p)
    n, home = _owned(index, nid)
    beside = where in ("before", "after")
    if beside:
        at = _anchor(index, to)
        if any(x is at for x in ir.walk([n])):
            raise EditError(f"{to} is {nid} or inside it")
        dest = at.body if isinstance(at, ir.Arm) else index.home(to)
    else:
        at = None
        dest = _scope(p, index, to)
        if any(dest is b for b in _bodies(n)):
            raise EditError(f"{to} is inside {nid}")
    del home[_position(home, n)]
    if at is None or isinstance(at, ir.Arm):
        k = len(dest) if where in ("after", "end") else 0
    else:
        k = _position(dest, at) + (where == "after")
    dest[k:k] = [n]
    _renew(n)


def _kept(b: ir.Block, keep: str | None) -> list[ir.Node]:
    """The nodes of the branch of `b` that `keep` names: `then` (an if's or a repeat's head),
    `else`, an arm's id; None for nothing."""
    if keep is None:
        return []
    if keep == "then":
        if b.op.shape not in (Shape.IF, Shape.REPEAT):
            raise EditError(f"{b.op.name} has no then-branch")
        return b.head
    if keep == "else":
        if b.op.shape is not Shape.IF or not b.arms:
            raise EditError(f"{b.op.name} {b.id} has no else")
        return b.arms[0].body
    for a in b.arms:
        if a.id == keep:
            return a.body
    raise EditError(f"{keep} is no arm of {b.id}")


def unwrap(p: ir.Program, nid: str, keep: str | None) -> None:
    """Replace the block `nid` by the body of one branch (`then`, `else` or an arm's id) or by
    nothing. What stays at the top of the branch becomes new code: it runs unguarded now."""
    index = ir.Index(p)
    n, home = _owned(index, nid)
    if not isinstance(n, ir.Block):
        raise EditError(f"{nid} is no block")
    body = list(_kept(n, keep))
    for x in body:
        _renew(x)
    k = _position(home, n)
    home[k : k + 1] = body


# ---- arms


def add_arm(p: ir.Program, nid: str, arm: ir.Arm, at: int | None = None) -> None:
    """Put `arm` into the block `nid` at position `at` among its arms, by default last. The
    block becomes new code."""
    index = ir.Index(p)
    b = _anchor(index, nid)
    if not isinstance(b, ir.Block):
        raise EditError(f"{nid} is no block")
    k = len(b.arms) if at is None else at
    if not 0 <= k <= len(b.arms):
        raise EditError(f"{nid} has {len(b.arms)} arms; {k} is out of range")
    arms = [*b.arms[:k], arm, *b.arms[k:]]
    _check_arms(b.op, arms)
    _ids(p, index, [arm])
    b.arms[:] = arms
    _renew(b)


def insert_arm(p: ir.Program, nid: str, arm: ir.Arm, *, after: bool = False) -> None:
    """Put `arm` before the arm `nid`, or after it."""
    index = ir.Index(p)
    at = _anchor(index, nid)
    if not isinstance(at, ir.Arm):
        raise EditError(f"{nid} is no arm")
    b = _owner(p, at)
    k = _position(b.arms, at) + after
    arms = [*b.arms[:k], arm, *b.arms[k:]]
    _check_arms(b.op, arms)
    _ids(p, index, [arm])
    b.arms[:] = arms
    _renew(b)


def delete_arm(p: ir.Program, nid: str) -> None:
    """Remove an arm and all that is in it; its block becomes new code."""
    at = _anchor(ir.Index(p), nid)
    if not isinstance(at, ir.Arm):
        raise EditError(f"{nid} is no arm")
    b = _owner(p, at)
    arms = [a for a in b.arms if a is not at]
    _check_arms(b.op, arms)
    b.arms[:] = arms
    _renew(b)


def reorder_arms(p: ir.Program, nid: str, order: Sequence[str]) -> None:
    """Put the arms of the block `nid` in the order of these ids, which name each arm once. The
    block becomes new code."""
    b = _anchor(ir.Index(p), nid)
    if not isinstance(b, ir.Block):
        raise EditError(f"{nid} is no block")
    by_id = {a.id: a for a in b.arms}
    if sorted(order) != sorted(by_id) or len(by_id) != len(b.arms):
        raise EditError(f"{nid} has the arms {list(by_id)}, not {list(order)}")
    arms = [by_id[i] for i in order]
    _check_arms(b.op, arms)
    b.arms[:] = arms
    _renew(b)


def ids_by_offset(img: asm.Image) -> dict[int, str]:
    """The id of the node whose bytes start at each offset of the image: `Image.nodes` the other
    way round."""
    return {off: nid for nid, (off, _) in img.nodes.items()}


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


def taken(p: ir.Program) -> set[str]:
    """Every name in the program: its items' and the labels of its data runs."""
    return {name for i in p.items for name in _names(i)}


def add(p: ir.Program, item: ir.Item) -> None:
    """Append a new item: no address, and a name nothing has."""
    if item.va is not None:
        raise EditError("a new item has no address")
    if clash := sorted(_names(item) & taken(p)):
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
    used = taken(p)
    stem = f"{header}_{slot(field).name}_l"
    new, k = stem, 1
    while new in used:
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


def resolve(p: ir.Program, *, share: bool = False) -> ir.Program:
    """A copy with every label replaced by its list index: its slot in the list of each header
    that reaches the routine, a new last slot where it has none. Every header must agree. With
    `share` the copy keeps the items it does not change, so it must not be edited."""
    out = _program(p, share)
    names = taken(out)
    tables = out.tables()
    grown: set[str] = set()
    headers = {h.name: h for h in out.headers()}
    reach = out.reach()
    labels = _labels(out)
    for lab in labels:
        if lab.label not in names:
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
            if lab.label not in tables[table].slots:
                if len(tables[table].slots) >= ir.MAX_LIST:
                    raise EditError(f"{table} cannot hold more than {ir.MAX_LIST} entries")
                if share and table not in grown:  # the list is the program's own until now
                    old = tables[table]
                    tables[table] = ir.Table(table, list(old.slots), old.va)
                    out.items[out.items.index(old)] = tables[table]
                    grown.add(table)
                tables[table].slots.append(lab.label)
            lab.under[hn] = tables[table].slots.index(lab.label)
        if len(set(lab.under.values())) > 1:
            seen = " and ".join(f"{i} under {hn}" for hn, i in lab.under.items())
            raise EditError(f"{lab.routine}: the label {lab.label!r} is slot {seen}")
    for lab in labels:
        lab.node.args[lab.operand] = next(iter(lab.under.values()))
    return out
