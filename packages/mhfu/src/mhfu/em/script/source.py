# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A program as source text, one Line per row of it.

    lines = render(prog)                  # or render(prog, "h3"): links as that header sees them
    text = "\\n".join(l.text for l in lines)
    code = render_nodes(nodes)            # new code alone: a snippet, no items around it

A name, then its operands by position. A block's arms sit level with its opener and a dedent
closes it; its count and the numbering of its markers are the tree's. u16 operands are hex, a
list slot that is empty is `-`, a word outside the program is `extern ADDR`, an operand that is
a Sym is `@label`. Data and blobs are rows of bytes, a label before the colon.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from . import ir
from .opcodes import BLOCKS, END_KINDS, Flow, Op, operands, slot

_INDENT = "  "
_ROW = 16
"""Bytes per row of data."""


@dataclass(frozen=True)
class Line:
    node: str | None
    """The id of the node the line is: an instruction, a block's opener, an arm."""
    depth: int
    text: str
    """With its indentation, so that the lines joined are the source."""
    pc: int | None
    """The native address of the bytes the line stands for."""
    kind: str
    """condition, action, random, flow, state, structure or data."""
    target: str | None
    """The id of the first node of the routine a call, goto or walk line leads to, when its
    entry resolves to one."""


def _kind(op: Op) -> str:
    if op.flow is not Flow.NEXT:
        return "flow"
    return {"wait": "action", "other": "structure"}.get(op.kind, op.kind)


def _value(op: Op, name: str, v: ir.Value, size: int) -> str:
    if isinstance(v, ir.Sym):
        return f"@{v.label}"
    if op.flow is Flow.END and name == "kind":
        return END_KINDS.get(v, str(v))
    return f"0x{v:04X}" if size == 2 else str(v)


def _args(op: Op, layout: str, args: Mapping[str, ir.Value]) -> list[str]:
    return [_value(op, o.name, args[o.name], o.size) for o in operands(layout)]


def _entry(e: ir.Entry) -> str:
    match e:
        case None:
            return "-"
        case ir.External(addr):
            return f"extern 0x{addr:08X}"
        case _:
            return e


def ranges(values: Iterable[int]) -> str:
    """Map types as `0-4,6,9-10`."""
    out: list[list[int]] = []
    for v in sorted(values):
        if out and out[-1][1] == v - 1:
            out[-1][1] = v
        else:
            out.append([v, v])
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in out)


def _rows(data: bytes) -> list[str]:
    return [data[i : i + _ROW].hex(" ") for i in range(0, len(data), _ROW)]


class _Links:
    """Where a routine-naming operand leads, as the headers that run the routine see it."""

    def __init__(self, p: ir.Program, header: str | None) -> None:
        self.p = p
        self.only = header
        self.tables = p.tables()
        self.headers = {h.name: h for h in p.headers()}
        if header is not None and header not in self.headers:
            raise KeyError(header)
        self.first = {r.name: r.body[0].id for r in p.routines() if r.body}
        self._reach: dict[str, tuple[str, ...]] | None = None

    def target(self, routine: str, node: ir.Node) -> str | None:
        if self.only is not None:
            seen = [self.headers[self.only]]
        else:
            if self._reach is None:
                self._reach = self.p.reach()
            seen = [self.headers[n] for n in self._reach.get(routine, ())]
        for ref in ir.refs(node):
            if ref.kind in ("stages", "route"):
                continue
            names = {self.p.entry(h, ref, self.tables) for h in seen} - {None}
            if len(names) == 1:
                return self.first.get(next(iter(names)) or "")
            return None
        return None


class _Writer:
    def __init__(self, p: ir.Program, header: str | None) -> None:
        self.links = _Links(p, header)
        self.out: list[Line] = []

    def add(
        self,
        depth: int,
        text: str,
        kind: str,
        node: str | None = None,
        pc: int | None = None,
        target: str | None = None,
    ) -> None:
        self.out.append(Line(node, depth, _INDENT * depth + text, pc, kind, target))

    def body(self, routine: str, nodes: list[ir.Node], depth: int) -> None:
        for n in nodes:
            if isinstance(n, ir.Insn):
                op = n.op
                text = " ".join([op.name, *_args(op, op.plain_layout(n.args), n.args)])
                self.add(depth, text, _kind(op), n.id, n.pc, self.links.target(routine, n))
            else:
                self.block(routine, n, depth)

    def block(self, routine: str, b: ir.Block, depth: int) -> None:
        op, kind = b.op, _kind(b.op)
        args = [_value(op, o.name, b.args[o.name], o.size) for o in op.opener]
        self.add(depth, " ".join([op.name, *args]), kind, b.id, b.pc)
        self.body(routine, b.head, depth + 1)
        for arm in b.arms:
            name = BLOCKS[op.shape][arm.sub]
            text = " ".join([name, *_args(op, op.layout(arm.sub), arm.args)])
            self.add(depth, text, kind, arm.id, arm.pc)
            self.body(routine, arm.body, depth + 1)

    def rows(self, depth: int, data: bytes, va: int | None, labels: Mapping[int, str]) -> None:
        """Rows of bytes, a label before the colon on the row it names."""
        cuts = sorted({0, *labels, len(data)})
        for a, b in zip(cuts, cuts[1:], strict=False):
            for i, row in enumerate(_rows(data[a:b])):
                head = labels[a] if i == 0 and a in labels else ""
                pc = None if va is None else va + a + i * _ROW
                self.add(depth, f"{head}: {row}", "data", None, pc)

    def item(self, item: ir.Item) -> None:
        match item:
            case ir.Routine():
                self.add(0, f"routine {item.name}", "structure", None, item.va)
                self.body(item.name, item.body, 1)
                if item.tail:
                    tail = _rows(item.tail)
                    self.add(1, f"tail {tail[0]}", "data")
                    for row in tail[1:]:
                        self.add(1, f": {row}", "data")
            case ir.Table():
                slots = " ".join(_entry(e) for e in item.slots)
                self.add(
                    0,
                    f"table {item.name}:" + (f" {slots}" if slots else ""),
                    "structure",
                    None,
                    item.va,
                )
            case ir.Data():
                self.add(0, f"data {item.name}", "structure", None, item.va)
                self.rows(1, item.data, item.va, {at: n for n, at in item.labels.items()})
            case ir.Blob():
                self.add(0, "blob", "structure", None, item.va)
                self.rows(1, item.data, item.va, {})
            case ir.Header():
                variants = f" variants {ranges(item.variants)}" if item.variants else ""
                self.add(0, f"header {item.name}{variants}", "structure", None, item.va)
                for f, e in enumerate(item.fields):
                    self.add(1, f"{slot(f).name} {_entry(e)}", "structure")


def render(p: ir.Program, header: str | None = None) -> list[Line]:
    """The program's source, one line per row. `header` names the header that links resolve
    through; by default a link is given where every header that runs the routine agrees."""
    w = _Writer(p, header)
    region = f"row {p.row} at 0x{p.base:08X}"
    if p.end:
        region += f" end 0x{p.end:08X} sha1 {p.sha1}"
    w.add(0, region, "structure")
    for item in p.items:
        w.item(item)
    return w.out


def render_nodes(nodes: Sequence[ir.Node]) -> list[Line]:
    """The source of nodes alone, as `parse.nodes` reads them; links are not resolved."""
    w = _Writer(ir.Program(0, 0, []), None)
    w.body("", list(nodes), 0)
    return w.out
