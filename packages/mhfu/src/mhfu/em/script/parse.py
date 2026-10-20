# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Source text back into a Program, the inverse of `source.render`.

    prog = parse(text)                  # every item and node is new
    prog = parse(text, like=lifted)     # the program the text was rendered from
    code = nodes("if_enraged\n  enter 3 0 0\n")   # a snippet: nodes only, as `render_nodes` writes

With `like`, an item takes the address of the one it is named after, and a routine keeps the
identity (id, `pc`) of each node still the same code, found by aligning the two trees; what the
text added or changed is new, so `check` holds exactly that to the rules. Without it nothing is
native. A syntax error names its line and column in a `ParseError`.

The text is lines and indentation (lark's `Indenter`); what a line means follows from its first
word and where it sits, resolved through the opcode table.
"""

from __future__ import annotations

import re
from collections.abc import Hashable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import cache

from lark import Lark, Token, Transformer
from lark.exceptions import UnexpectedInput, UnexpectedToken
from lark.indenter import DedentError, Indenter

from . import ir
from .opcodes import (
    BLOCKS,
    BY_NAME,
    CLOSE,
    END_KINDS,
    NUMBERED,
    Flow,
    Op,
    Operand,
    operands,
    slot,
)

__all__ = ["ParseError", "nodes", "parse"]

_GRAMMAR = r"""
start: _NL? line*
line: (WORD | COLON)+ _NL (_INDENT line+ _DEDENT)?
WORD: /[^\s:#]+/
COLON: ":"
_NL: /(\n[\t ]*|#[^\n]*)+/
%declare _INDENT _DEDENT
%ignore /[ \t\f\v\r]+/
"""
"""Lines of words, at most one colon each; a comment is part of the newline it ends in."""
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"-?(?:0[xX][0-9a-fA-F]+|[0-9]+)")
_BYTE = re.compile(r"[0-9a-fA-F]{2}")
_KEYWORDS = frozenset({"extern"})
"""Words that mean something in a list entry: no item is named one."""
_END_CODES = {name: code for code, name in END_KINDS.items()}
_MARKERS = {
    shape: {sub: name for sub, name in marks.items() if sub != CLOSE[shape]}
    for shape, marks in BLOCKS.items()
}
"""Each block shape's arm markers by sub-code; the closing one is implied by a dedent."""
_ARMS = frozenset(name for marks in _MARKERS.values() for name in marks.values())
_ONCE = frozenset({"else", "default"})
"""The arms a block has one of, last; any other arm repeats."""


class ParseError(ValueError):
    """Source text that is no program; the message names the line, and `column` counts from 1."""

    def __init__(self, message: str, line: int, column: int = 1) -> None:
        super().__init__(f"line {line}: {message}")
        self.line = line
        self.column = column


def _at(tok: Token, message: str) -> ParseError:
    return ParseError(message, tok.line or 0, tok.column or 1)


# ---- lines


class _Indent(Indenter):
    NL_type = "_NL"
    OPEN_PAREN_types: list[str] = []
    CLOSE_PAREN_types: list[str] = []
    INDENT_type = "_INDENT"
    DEDENT_type = "_DEDENT"
    tab_len = 8

    def handle_NL(self, token: Token) -> Iterator[Token]:
        try:
            yield from super().handle_NL(token)
        except DedentError:
            raise ParseError(
                "indentation matches no enclosing level", token.end_line or 0
            ) from None


@dataclass
class _Line:
    """A line as written: the words before its colon, the colon, the words after it, and the
    lines indented under it."""

    words: list[Token]
    colon: Token | None
    rest: list[Token]
    kids: list[_Line]

    @property
    def first(self) -> Token:
        if self.words:
            return self.words[0]
        assert self.colon is not None
        return self.colon

    @property
    def name(self) -> str:
        return str(self.first)


class _Lines(Transformer[Token, list[_Line]]):
    def line(self, children: list[Token | _Line]) -> _Line:
        toks = [c for c in children if isinstance(c, Token)]
        kids = [c for c in children if isinstance(c, _Line)]
        cut = next((i for i, t in enumerate(toks) if t.type == "COLON"), None)
        if cut is None:
            return _Line(toks, None, [], kids)
        return _Line(toks[:cut], toks[cut], toks[cut + 1 :], kids)

    def start(self, children: list[_Line]) -> list[_Line]:
        return children


@cache
def _lark() -> Lark:
    return Lark(_GRAMMAR, parser="lalr", postlex=_Indent(), transformer=_Lines())


def _lines(text: str) -> list[_Line]:
    try:
        out = _lark().parse(text.replace("\r\n", "\n") + "\n")
    except UnexpectedToken as e:
        if e.token.type == "_INDENT":  # it borrows its position from the newline before it
            raise ParseError("unexpected indent", e.token.end_line or 0) from None
        raise ParseError(f"unexpected {e.token.type}", e.line, e.column) from None
    except UnexpectedInput as e:
        raise ParseError("unexpected character", e.line, e.column) from None
    assert isinstance(out, list)
    return out


# ---- reading


def _number(text: str) -> int | None:
    if _NUMBER.fullmatch(text) is None:
        return None
    return int(text, 16) if "x" in text.lower() else int(text)


class _Reader:
    """The parse of one text: items, nodes and the names they use."""

    def __init__(self) -> None:
        self.named: dict[str, Token] = {}
        self.used: list[Token] = []
        """The tokens that refer to an item or a data label by name."""

    def define(self, tok: Token) -> str:
        name = str(tok)
        if _NAME.fullmatch(name) is None or name in _KEYWORDS:
            raise _at(tok, f"{name!r} is no name: letters, digits and _, not starting with a digit")
        if name in self.named:
            raise _at(tok, f"{name} is named already, on line {self.named[name].line}")
        self.named[name] = tok
        return name

    def use(self, tok: Token, name: str) -> str:
        if _NAME.fullmatch(name) is None:
            raise _at(tok, f"{name!r} is no name")
        # `name` is all of the token or what follows its `@`
        self.used.append(
            Token("WORD", name, None, tok.line, (tok.column or 1) + len(tok) - len(name))
        )
        return name

    def number(self, tok: Token, what: str) -> int:
        n = _number(str(tok))
        if n is None or n < 0:
            raise _at(tok, f"{what} is {str(tok)!r}, not a number")
        return n

    def data(self, toks: Sequence[Token]) -> bytes:
        for t in toks:
            if _BYTE.fullmatch(t) is None:
                raise _at(t, f"{str(t)!r} is no byte: two hex digits")
        return bytes(int(t, 16) for t in toks)

    # lists and headers

    def entries(self, toks: Sequence[Token]) -> list[ir.Entry]:
        out: list[ir.Entry] = []
        it = iter(toks)
        for t in it:
            if t == "-":
                out.append(None)
            elif t == "extern":
                addr = next(it, None)
                if addr is None:
                    raise _at(t, "extern takes an address")
                out.append(ir.External(self.number(addr, "the address")))
            else:
                out.append(self.use(t, str(t)))
        return out

    def variants(self, tok: Token) -> tuple[int, ...]:
        out: set[int] = set()
        for part in str(tok).split(","):
            lo, _, hi = part.partition("-")
            if not lo.isdecimal() or not (hi.isdecimal() or not hi) or int(hi or lo) < int(lo):
                raise _at(tok, f"{str(tok)!r} is no list of map types: `0-4,6`")
            out.update(range(int(lo), int(hi or lo) + 1))
        return tuple(sorted(out))

    def header(self, ln: _Line) -> ir.Header:
        w = ln.words
        if ln.colon is not None or len(w) not in (2, 4) or (len(w) == 4 and w[2] != "variants"):
            raise _at(ln.first, "a header is `header NAME [variants 0-16]`")
        name = self.define(w[1])
        fields: list[ir.Entry] = []
        for k, kid in enumerate(ln.kids):
            if kid.name != slot(k).name:
                raise _at(kid.first, f"field {k} is {slot(k).name}, not {kid.name}")
            entry = self.entries(kid.words[1:])
            if kid.colon is not None or kid.kids or len(entry) != 1:
                raise _at(kid.first, f"{kid.name} takes one entry: a name, `-` or `extern ADDR`")
            fields.append(entry[0])
        return ir.Header(name, fields, self.variants(w[3]) if len(w) == 4 else ())

    def table(self, ln: _Line) -> ir.Table:
        if ln.colon is None or len(ln.words) != 2 or ln.kids:
            raise _at(ln.first, "a list is `table NAME: entries`")
        return ir.Table(self.define(ln.words[1]), self.entries(ln.rest))

    # bytes

    def rows(
        self, kids: Sequence[_Line], own: str | None = None
    ) -> tuple[bytearray, dict[str, int]]:
        """The bytes of `label: bytes` and `: bytes` rows, and the offsets of their labels.
        `own` is the name of the item they belong to, which may label its first byte."""
        out = bytearray()
        labels: dict[str, int] = {}
        for ln in kids:
            if ln.colon is None or ln.kids or len(ln.words) > (own is not None):
                raise _at(ln.first, "a row of bytes is `label: 00 01` or `: 00 01`")
            if ln.words:
                label = ln.words[0]
                if not (str(label) == own and not out):
                    self.define(label)
                labels[str(label)] = len(out)
            out += self.data(ln.rest)
        return out, labels

    def blob(self, ln: _Line) -> ir.Blob:
        if ln.colon is not None or len(ln.words) != 1:
            raise _at(ln.first, "a blob is `blob`, then rows of bytes")
        return ir.Blob(bytes(self.rows(ln.kids)[0]))

    def data_item(self, ln: _Line) -> ir.Data:
        if ln.colon is not None or len(ln.words) != 2:
            raise _at(ln.first, "a data run is `data NAME`")
        name = self.define(ln.words[1])
        raw, labels = self.rows(ln.kids, name)
        return ir.Data(name, bytes(raw), labels)

    # code

    def routine(self, ln: _Line) -> ir.Routine:
        if ln.colon is not None or len(ln.words) != 2:
            raise _at(ln.first, "a routine is `routine NAME`")
        name = self.define(ln.words[1])
        cut = next((i for i, k in enumerate(ln.kids) if k.name == "tail"), len(ln.kids))
        body = self.nodes(ln.kids[:cut])
        return ir.Routine(name, body, self.tail(ln.kids[cut:]))

    def tail(self, kids: list[_Line]) -> bytes:
        if not kids:
            return b""
        head, *more = kids
        if head.colon is not None or head.kids:
            raise _at(head.first, "tail takes bytes: `tail 00 01`")
        out = bytearray(self.data(head.words[1:]))
        for ln in more:
            if ln.words or ln.colon is None or ln.kids:
                raise _at(ln.first, "only `: 00 01` rows follow the tail")
            out += self.data(ln.rest)
        return bytes(out)

    def value(self, o: Operand, tok: Token, op: Op) -> ir.Value:
        text = str(tok)
        if text.startswith("@"):
            if o.ref is None:
                raise _at(tok, f"{o.name} is no list index: it takes a number, not {text}")
            return ir.Sym(self.use(tok, text[1:]))
        if op.flow is Flow.END and o.name == "kind" and text in _END_CODES:
            return _END_CODES[text]
        n = _number(text)
        if n is None:
            raise _at(tok, f"{o.name} is {text!r}, not a number")
        try:
            n.to_bytes(o.size, "big", signed=o.signed)
        except OverflowError:
            raise _at(tok, f"{o.name} {n} does not fit {o.size} byte(s)") from None
        return n

    def bind(
        self, ln: _Line, what: str, layout: Sequence[Operand], toks: Sequence[Token], op: Op
    ) -> dict[str, ir.Value]:
        if len(toks) != len(layout):
            names = f" ({' '.join(o.name for o in layout)})" if layout else ""
            raise _at(ln.first, f"{what} takes {len(layout)} operand(s){names}, not {len(toks)}")
        return {o.name: self.value(o, t, op) for o, t in zip(layout, toks, strict=True)}

    def nodes(self, lines: Sequence[_Line]) -> list[ir.Node]:
        """The nodes of a list of lines; an arm belongs to the block before it on its level."""
        out: list[ir.Node] = []
        last: ir.Block | None = None
        for ln in lines:
            if ln.colon is not None:
                raise _at(ln.colon, "unexpected `:`")
            op = BY_NAME.get(ln.name)
            if op is None:
                self.arm(ln, last)
            elif op.coded:
                args = self.bind(ln, op.name, list(op.opener), ln.words[1:], op)
                last = ir.Block(op, args, self.nodes(ln.kids), [])
                out.append(last)
            else:
                if ln.kids:
                    raise _at(
                        ln.kids[0].first, f"{op.name} is an instruction: nothing goes under it"
                    )
                words = ln.words[1:]
                first = _number(str(words[0])) if words and op.by else None
                layout = operands(op.layout(first) if first is not None else op.args)
                out.append(ir.Insn(op, self.bind(ln, op.name, layout, words, op)))
                last = None
        return out

    def arm(self, ln: _Line, block: ir.Block | None) -> None:
        name = ln.name
        if block is None:
            known = "belongs to no block here" if name in _ARMS else "is no instruction"
            raise _at(ln.first, f"{name} {known}")
        op = block.op
        marks = _MARKERS[op.shape]
        subs = [sub for sub, n in marks.items() if n == name]
        if not subs:
            have = ", ".join(sorted(set(marks.values()))) or "none"
            raise _at(ln.first, f"{op.name} takes no {name} (its arms: {have})")
        if op.shape in NUMBERED:
            sub = 1
            if len(block.arms) >= len(marks):
                raise _at(ln.first, f"{op.name} has at most {len(marks)} arms")
        else:
            sub = subs[0]
            prev = block.arms[-1].sub if block.arms else 0
            if sub < prev or (sub == prev and name in _ONCE):
                raise _at(ln.first, f"{name} is out of order in {op.name}")
        args = self.bind(ln, name, operands(op.layout(sub)), ln.words[1:], op)
        block.arms.append(ir.Arm(sub, args, self.nodes(ln.kids)))

    # the program

    def region(self, ln: _Line) -> tuple[int, int, int, str]:
        """Row, base, end and sha1 of the first line: `row N at ADDR [end ADDR sha1 HEX]`."""
        w = ln.words
        ok = ln.colon is None and not ln.kids and len(w) >= 4 and w[0] == "row" and w[2] == "at"
        more = [str(t) for t in w[4:]]
        ok = ok and (not more or (len(more) in (3, 4) and more[0] == "end" and more[2] == "sha1"))
        if not ok:
            raise _at(ln.first, "the first line is `row N at ADDR [end ADDR sha1 HEX]`")
        end = self.number(w[5], "the end") if more else 0
        return (
            self.number(w[1], "the row"),
            self.number(w[3], "the base"),
            end,
            more[3] if len(more) == 4 else "",
        )

    def item(self, ln: _Line) -> ir.Item:
        match ln.name:
            case "routine":
                return self.routine(ln)
            case "table":
                return self.table(ln)
            case "data":
                return self.data_item(ln)
            case "blob":
                return self.blob(ln)
            case "header":
                return self.header(ln)
        raise _at(ln.first, f"{ln.name} starts no item: routine, table, data, blob or header")

    def names(self) -> None:
        """Every name a list entry or label uses must be an item or a data label."""
        for tok in sorted(self.used, key=lambda t: (t.line or 0, t.column or 0)):
            if str(tok) not in self.named:
                raise _at(tok, f"nothing is named {tok}")


# ---- identity


def _key(n: ir.Node) -> Hashable:
    if isinstance(n, ir.Insn):
        return "insn", n.op.code, frozenset(n.args.items())
    return "block", n.op.code, frozenset(n.args.items()), tuple(a.sub for a in n.arms)


def _opcodes(
    a: Sequence[Hashable], b: Sequence[Hashable]
) -> Iterator[tuple[str, int, int, int, int]]:
    if list(a) == list(b):
        yield "equal", 0, len(a), 0, len(b)
    else:
        yield from SequenceMatcher(None, a, b, autojunk=False).get_opcodes()


class _Native:
    """What a text takes from the program it was rendered from."""

    def __init__(self, like: ir.Program) -> None:
        self.like = like

    def items(self, items: list[ir.Item]) -> None:
        named = {(type(i), i.name): i for i in self.like.items if not isinstance(i, ir.Blob)}
        blobs = [b for b in self.like.items if isinstance(b, ir.Blob)]
        for it in items:
            if isinstance(it, ir.Blob):
                same = next((b for b in blobs if b.data == it.data), None)
                if same is not None:
                    blobs.remove(same)
                    it.va = same.va
                continue
            old = named.get((type(it), it.name))
            if old is None:
                continue
            it.va = old.va
            if isinstance(it, ir.Routine) and isinstance(old, ir.Routine):
                self.align(it.body, old.body)

    def align(self, new: list[ir.Node], old: list[ir.Node]) -> None:
        """Give each node of `new` that is `old`'s code its identity. Where the lists differ,
        blocks of one opcode still share their insides, so adding an arm keeps the rest."""
        for tag, i1, i2, j1, j2 in _opcodes([_key(n) for n in new], [_key(n) for n in old]):
            if tag == "equal":
                for a, b in zip(new[i1:i2], old[j1:j2], strict=True):
                    self.adopt(a, b)
            elif tag == "replace":
                nb = [n for n in new[i1:i2] if isinstance(n, ir.Block)]
                ob = [n for n in old[j1:j2] if isinstance(n, ir.Block)]
                same = SequenceMatcher(
                    None, [b.op.code for b in nb], [b.op.code for b in ob], autojunk=False
                )
                for m in same.get_matching_blocks():
                    for k in range(m.size):
                        self.adopt(nb[m.a + k], ob[m.b + k])

    def adopt(self, a: ir.Node, b: ir.Node) -> None:
        if isinstance(a, ir.Insn) and isinstance(b, ir.Insn):
            a.id, a.pc = b.id, b.pc
        elif isinstance(a, ir.Block) and isinstance(b, ir.Block):
            if (a.op.code, a.args) == (b.op.code, b.args):  # not a changed opener
                a.id, a.pc, a.close = b.id, b.pc, b.close
            self.align(a.head, b.head)
            self.arms(a.arms, b.arms)

    def arms(self, new: list[ir.Arm], old: list[ir.Arm]) -> None:
        def key(a: ir.Arm) -> Hashable:
            return a.sub, frozenset(a.args.items())

        for tag, i1, i2, j1, j2 in _opcodes([key(a) for a in new], [key(a) for a in old]):
            pairs = zip(new[i1:i2], old[j1:j2], strict=False)
            if tag == "equal":
                for a, b in pairs:
                    a.id, a.pc = b.id, b.pc
                    self.align(a.body, b.body)
            elif tag == "replace" and i2 - i1 == j2 - j1:  # a changed arm, its body as it was
                for a, b in pairs:
                    if a.sub == b.sub:
                        self.align(a.body, b.body)


def _name(nodes: Iterable[ir.Located], k: int) -> None:
    """Give the nodes with no id (arms too) `n<k>`, `n<k+1>`, ..."""
    for n in nodes:
        if not n.id:
            n.id, k = f"n{k}", k + 1


def _number_new(p: ir.Program, like: ir.Program | None) -> None:
    """Give the nodes with no id (arms too) an `n<k>` past every id of the program and `like`."""
    _name(p.nodes(), int(p.fresh(like.nodes() if like else ())[1:]))


def parse(text: str, like: ir.Program | None = None) -> ir.Program:
    """The program `text` says. `like` is the lifted program it was rendered from: the region
    line must be its own, and unchanged items and code keep their native addresses."""
    lines = _lines(text)
    if not lines:
        raise ParseError("the text is empty: the first line is `row N at ADDR`", 1)
    rd = _Reader()
    row, base, end, sha1 = rd.region(lines[0])
    if like is not None:
        if row != like.row:
            raise _at(lines[0].first, f"the text is row {row}, not row {like.row}")
        if (base, end, sha1) != (like.base, like.end, like.sha1):
            raise _at(
                lines[0].first, "the text belongs to another game build: its region is not this one"
            )
    items = [rd.item(ln) for ln in lines[1:]]
    rd.names()
    if like is None:
        prog = ir.Program(row, base, items)
    else:
        prog = ir.Program(row, like.base, items, like.end, like.sha1)
        _Native(like).items(items)
    _number_new(prog, like)
    return prog


def nodes(text: str, like: ir.Program | None = None) -> list[ir.Node]:
    """The code `text` says, instructions and blocks with no items around them: what
    `source.render_nodes` writes. The nodes have no id, as the `edit` builders leave them, so
    equal code is equal nodes; `like` numbers them `n<k>` past the ids of that program. A label
    (`@name`) is not looked up: an edit set's `apply` resolves it."""
    out = _Reader().nodes(_lines(text))
    if like is not None:
        _name(ir.walk(out), int(like.fresh()[1:]))
    return out
