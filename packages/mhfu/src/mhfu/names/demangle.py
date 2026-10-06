# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""CodeWarrior's C++ mangling (the game's compiler, and the MHP2G decomp's symbols) read back:
`act_ck__7ObjBaseFUcUc` is `ObjBase::act_ck(unsigned char, unsigned char)`."""

from __future__ import annotations

_BUILTIN = {
    "v": "void",
    "b": "bool",
    "c": "char",
    "w": "wchar_t",
    "s": "short",
    "i": "int",
    "l": "long",
    "x": "long long",
    "f": "float",
    "d": "double",
    "r": "long double",
    "e": "...",
}
_OPERATORS = {
    "__nw": "operator new",
    "__dl": "operator delete",
    "__nwa": "operator new[]",
    "__dla": "operator delete[]",
    "__as": "operator=",
    "__eq": "operator==",
    "__ne": "operator!=",
    "__cl": "operator()",
    "__vc": "operator[]",
}


class _Bad(ValueError):
    pass


def demangle(symbol: str) -> str:
    """The C++ the symbol names; a symbol that is not mangled comes back as it is."""
    at = symbol.find("__", 1)
    while at > 0:
        try:
            return _Reader(symbol[at + 2 :]).entity(symbol[:at])
        except _Bad:
            at = symbol.find("__", at + 1)
    return symbol


class _Reader:
    def __init__(self, text: str) -> None:
        self.s, self.i = text, 0

    def peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def take(self, char: str) -> bool:
        if self.peek() == char:
            self.i += 1
            return True
        return False

    def end(self) -> bool:
        return self.i == len(self.s)

    def entity(self, name: str) -> str:
        """`name` and what follows it: a qualifier, then `F` and the parameters, or nothing for
        a data member."""
        scope = self.scope() if self.peek().isdigit() or self.peek() == "Q" else []
        const = self.s[self.i : self.i + 2] == "CF" and self.take("C")
        if self.end():
            if not scope:
                raise _Bad(name)
            return "::".join([*scope, _special(name, scope)])
        if not self.take("F"):
            raise _Bad(self.s)
        params = self.params(stop="")
        return "::".join([*scope, _special(name, scope)]) + f"({params})" + " const" * const

    def params(self, stop: str) -> str:
        out = []
        while not self.end() and self.peek() != stop:
            out.append(self.type())
        return ", ".join(t for t in out if t != "void" or len(out) > 1)

    def number(self) -> int:
        start = self.i
        while self.peek().isdigit():
            self.i += 1
        if start == self.i:
            raise _Bad(self.s)
        return int(self.s[start : self.i])

    def name(self) -> str:
        n = self.number()
        text = self.s[self.i : self.i + n]
        if len(text) != n:
            raise _Bad(self.s)
        self.i += n
        return _template(text)

    def scope(self) -> list[str]:
        """`7ObjBase`, or `Q23std9exception` for nested names."""
        if not self.take("Q"):
            return [self.name()]
        count = self.peek()
        if not count.isdigit():
            raise _Bad(self.s)
        self.i += 1
        return [self.name() for _ in range(int(count))]

    def type(self) -> str:
        c = self.peek()
        if not c:
            raise _Bad(self.s)
        if c.isdigit() or c == "Q":
            return "::".join(self.scope())
        self.i += 1
        if c in _BUILTIN:
            return _BUILTIN[c]
        if c in "US":
            base = self.type()
            return ("unsigned " if c == "U" else "signed ") + base
        if c in "CV":
            word = "const" if c == "C" else "volatile"
            inner = self.type()
            return f"{inner} {word}" if inner.endswith(("*", "&")) else f"{word} {inner}"
        if c in "PR":
            return self.type() + ("*" if c == "P" else "&")
        if c == "A":
            n = self.number()
            if not self.take("_"):
                raise _Bad(self.s)
            return f"{self.type()}[{n}]"
        if c == "F":
            params = self.params(stop="_")
            if not self.take("_"):
                raise _Bad(self.s)
            return f"{self.type()} ({params})"
        if c == "M":
            cls = "::".join(self.scope())
            return f"{self.type()} {cls}::*"
        raise _Bad(self.s)


def _special(name: str, scope: list[str]) -> str:
    if name in ("__ct", "__dt") and scope:
        base = scope[-1].partition("<")[0]
        return base if name == "__ct" else f"~{base}"
    return _OPERATORS.get(name, name)


def _template(text: str) -> str:
    """`Singleton<4Book>` is `Singleton<Book>`; an argument that is not a type stays as it is."""
    head, lt, rest = text.partition("<")
    if not lt or not rest.endswith(">"):
        return text
    args, depth, start = [], 0, 0
    body = rest[:-1]
    for k, ch in enumerate(body + ","):
        depth += (ch == "<") - (ch == ">")
        if ch == "," and depth == 0:
            args.append(body[start:k])
            start = k + 1
    out = []
    for arg in args:
        try:
            reader = _Reader(arg)
            t = reader.type()
            out.append(t if reader.end() else arg)
        except _Bad:
            out.append(arg)
    return f"{head}<{', '.join(out)}>"
