# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Markdown pages for the Lua reference, rendered from the API model."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

from modkit_ci.api_lua import Class, Const, Func, LuaApi, Param

_SHORT: Final = 60
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def anchor(name: str) -> str:
    """The HTML id of a declaration: its qualified name, `:` as `.`."""
    return _UNSAFE.sub("-", name.replace(":", "."))


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _value(text: str) -> str:
    one = " ".join(text.split())
    return one if len(one) <= _SHORT else one[: _SHORT - 3] + "..."


def _type(type_: str, optional: bool) -> str:
    return type_ + ("?" if optional else "")


def _arg(p: Param) -> str:
    return f"{p.name}{'?' if p.optional else ''}: {p.type}"


def signature(f: Func) -> str:
    """The call as LuaLS shows it: `name(a: T, b?: U): R1, R2`."""
    text = f"{f.name}({', '.join(_arg(p) for p in f.params)})"
    return text + (f": {', '.join(f.returns)}" if f.returns else "")


def _func(f: Func) -> list[str]:
    out = [f"### `{f.name}` {{ #{anchor(f.name)} }}", "", f"```lua\n{signature(f)}\n```", ""]
    if f.desc:
        out += [f.desc, ""]
    if f.params:
        out += ["| Parameter | Type | Description |", "| --- | --- | --- |"]
        out += [
            f"| `{p.name}` | `{_type(p.type, p.optional)}` | {_cell(p.desc)} |" for p in f.params
        ]
        out.append("")
    if f.returns:
        out += ["Returns: " + ", ".join(f"`{r}`" for r in f.returns), ""]
    for example in f.examples:
        out += ["Example:", "", f"```lua\n{example}\n```", ""]
    return out


def _const_row(c: Const) -> str:
    name = f'<a id="{anchor(c.name)}"></a>`{c.name}`'
    return f"| {name} | `{_cell(_value(c.value))}` | {_cell(c.desc)} |"


def _consts(items: Iterable[Const]) -> list[str]:
    rows = [_const_row(c) for c in items]
    if not rows:
        return []
    return ["## Constants", "", "| Name | Value | Description |", "| --- | --- | --- |", *rows, ""]


def _class(k: Class) -> list[str]:
    out = [f"### `{k.name}` {{ #{anchor(k.name)} }}", ""]
    if k.desc:
        out += [k.desc, ""]
    if k.fields:
        out += ["| Field | Type | Description |", "| --- | --- | --- |"]
        out += [
            f"| `{f.name}` | `{_type(f.type, f.optional)}` | {_cell(f.desc)} |" for f in k.fields
        ]
        out.append("")
    return out


def render(api: LuaApi, title: str) -> str:
    """One Markdown page: constants, classes, then functions, each in declaration order."""
    out = [f"# {title}", ""]
    out += _consts(api.consts)
    if api.classes:
        out += ["## Classes", ""]
        for k in api.classes:
            out += _class(k)
    if api.funcs:
        out += ["## Functions", ""]
        for f in api.funcs:
            out += _func(f)
    return "\n".join(out).rstrip("\n") + "\n"
