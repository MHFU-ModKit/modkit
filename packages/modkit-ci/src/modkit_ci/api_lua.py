# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Lua API model from LuaLS doc JSON, and its break and doc rules."""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TypeVar

from modkit_ci import lua_tool, luals
from modkit_ci.findings import Finding
from modkit_ci.registry import Module

VERSION_NAME: Final = "api_version"
_FENCE: Final = re.compile(r"^```lua[ \t]*\n(.*?)^```[ \t]*$", re.DOTALL | re.MULTILINE)
_SPACE: Final = re.compile(r"\s+")
_STRING: Final = re.compile(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'")
_NUMBER: Final = re.compile(r"\b(?:0[xX][0-9a-fA-F]+|\d+(?:\.\d+)?)\b")
_KEY: Final = re.compile(r"\b[A-Za-z_]\w*\s*=(?!=)")
_WORD: Final = re.compile(r"\b(?!(?:true|false|nil)\b)[A-Za-z_]\w*")


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    optional: bool
    desc: str


@dataclass(frozen=True)
class Func:
    name: str
    params: tuple[Param, ...]
    returns: tuple[str, ...]
    desc: str
    examples: tuple[str, ...]
    file: str
    line: int


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    optional: bool
    desc: str


@dataclass(frozen=True)
class Class:
    name: str
    fields: tuple[Field, ...]
    desc: str
    file: str
    line: int


@dataclass(frozen=True)
class Const:
    name: str
    value: str
    desc: str
    file: str
    line: int


@dataclass(frozen=True)
class LuaApi:
    funcs: tuple[Func, ...]
    consts: tuple[Const, ...]
    classes: tuple[Class, ...]


def _under(file: str, dirs: Sequence[str]) -> bool:
    return any(file == d or file.startswith(d.rstrip("/") + "/") for d in dirs)


def _view(view: str) -> tuple[str, bool]:
    """A LuaLS view split into (type, optional)."""
    return (view[:-1], True) if view.endswith("?") else (view, False)


def _prose(raw: str) -> str:
    """A raw description without its ```lua blocks (LuaLS adds some for literal types)."""
    return _FENCE.sub("", raw).strip()


def _examples(root: Path, file: str, line: int, cache: dict[str, list[str]]) -> tuple[str, ...]:
    """The examples in the doc block directly above 1-based `line` of `file`."""
    if file not in cache:
        path = root / file
        cache[file] = path.read_text(encoding="utf-8").split("\n") if path.is_file() else []
    lines = cache[file]
    top = line - 1
    while top > 0 and lines[top - 1].lstrip().startswith("---"):
        top -= 1
    return tuple(lua_tool.load().block_fences(lines[top : line - 1]))


def _param(arg: Mapping[str, Any]) -> Param:
    type_, optional = _view(str(arg.get("view", "any")))
    return Param(str(arg.get("name", "")), type_, optional, str(arg.get("rawdesc", "")))


def _slice(root: Path, member: Mapping[str, Any], cache: dict[str, list[str]]) -> str:
    """The source text of a member's value expression, whitespace collapsed."""
    ext = member["extends"]
    file = str(member["file"])
    if file not in cache:
        path = root / file
        cache[file] = path.read_text(encoding="utf-8").split("\n") if path.is_file() else []
    lines = cache[file]
    (l0, c0), (l1, c1) = ext["start"], ext["finish"]
    if l1 >= len(lines):
        return str(ext.get("view", ""))
    parts = [lines[n] for n in range(l0, l1 + 1)]
    parts[-1] = parts[-1][:c1]
    parts[0] = parts[0][c0:] if l0 != l1 else parts[0][c0:c1]
    return _SPACE.sub(" ", " ".join(parts)).strip()


def _func(name: str, member: Mapping[str, Any], root: Path, cache: dict[str, list[str]]) -> Func:
    ext = member["extends"]
    line = int(member["start"][0]) + 1
    return Func(
        name=name,
        params=tuple(_param(a) for a in ext.get("args", [])),
        returns=tuple(str(r.get("view", "any")) for r in ext.get("returns", [])),
        desc=_prose(str(member.get("rawdesc", ""))),
        examples=_examples(root, str(member["file"]), line, cache),
        file=str(member["file"]),
        line=line,
    )


def model(doc: list[dict[str, Any]], root: Path, dirs: Sequence[str]) -> LuaApi:
    """The entries of LuaLS doc JSON defined under `dirs`, sorted by (file, line)."""
    funcs: list[Func] = []
    consts: list[Const] = []
    classes: list[Class] = []
    cache: dict[str, list[str]] = {}
    class_names = {str(e["name"]) for e in doc if e.get("type") == "type"}

    def member(owner: str, m: Mapping[str, Any]) -> None:
        if not _under(str(m["file"]), dirs):
            return
        kind = m["type"]
        if str(m["name"]).startswith("__"):
            return  # metamethod plumbing, not API
        if kind in ("setfield", "setmethod"):
            sep = ":" if kind == "setmethod" else "."
            name = f"{owner}{sep}{m['name']}" if owner else str(m["name"])
            if m["extends"]["type"] == "function":
                funcs.append(_func(name, m, root, cache))
            else:
                desc = _prose(str(m.get("rawdesc", "")))
                value = _slice(root, m, cache)
                consts.append(Const(name, value, desc, str(m["file"]), int(m["start"][0]) + 1))

    for entry in doc:
        name = str(entry.get("name", ""))
        defines = entry.get("defines", [])
        if entry.get("type") == "type":
            own = [d for d in defines if d["type"] == "doc.class" and _under(d["file"], dirs)]
            if own:
                fields = tuple(
                    Field(str(f["name"]), *_view(str(f["view"])), str(f.get("rawdesc", "")))
                    for f in entry.get("fields", [])
                    if f["type"] == "doc.field" and _under(str(f["file"]), dirs)
                )
                first = own[0]
                desc = _prose(str(first.get("rawdesc", "")))
                line = int(first["start"][0]) + 1
                classes.append(Class(name, fields, desc, str(first["file"]), line))
            for f in entry.get("fields", []):
                member(name, f)
        elif entry.get("type") == "variable":
            owner, _, short = name.rpartition(".")
            if owner in class_names or name in class_names:
                continue  # reached through its class
            for d in defines:
                if d["type"] in ("setglobal", "setfield", "setmethod"):
                    member(owner, {**d, "name": short})
    for items in (funcs, consts, classes):
        items.sort(key=lambda i: (i.file, i.line))
    return LuaApi(tuple(funcs), tuple(consts), tuple(classes))


def load(root: Path, dirs: Sequence[str]) -> LuaApi:
    """LuaLS doc of `root` through `model`."""
    with tempfile.TemporaryDirectory() as tmp:
        raw = luals.doc(root, Path(tmp) / "doc.json")
    return model(raw, root, dirs)


T = TypeVar("T")


def _by_name(items: Sequence[T], key: Callable[[T], str]) -> dict[str, T]:
    return {key(i): i for i in items}


def _bare(name: str) -> str:
    return re.split(r"[.:]", name)[-1]


def _version(api: LuaApi) -> int | None:
    for const in api.consts:
        if _bare(const.name) == VERSION_NAME:
            try:
                return int(const.value, 0)
            except ValueError:
                return None
    return None


def _literal(value: str) -> bool:
    """True when `value` is only literals: an expression naming code can change freely."""
    bare = _KEY.sub("", _NUMBER.sub("", _STRING.sub('""', value)))
    return _WORD.search(bare) is None


def _func_breaks(old: Func, new: Func) -> list[str]:
    out: list[str] = []
    out += [
        f"param {i + 1} ({p.name}) removed"
        for i, p in enumerate(old.params)
        if i >= len(new.params)
    ]
    for i, (a, b) in enumerate(zip(old.params, new.params, strict=False)):
        if a.type != b.type:
            out.append(f"param {i + 1} ({a.name}) type {a.type} -> {b.type}")
        elif a.optional and not b.optional:
            out.append(f"param {i + 1} ({a.name}) is now required")
    out += [
        f"required param {p.name} added" for p in new.params[len(old.params) :] if not p.optional
    ]
    if len(new.returns) < len(old.returns):
        out.append(f"return {len(new.returns) + 1} removed")
    for i, (r, s) in enumerate(zip(old.returns, new.returns, strict=False)):
        if r != s:
            out.append(f"return {i + 1} type {r} -> {s}")
    return out


def _signature(api: LuaApi) -> dict[str, object]:
    """Every declaration reduced to what a caller depends on."""
    sig: dict[str, object] = {}
    for f in api.funcs:
        sig[f.name] = ([(p.type, p.optional) for p in f.params], f.returns)
    for c in api.consts:
        keep = _bare(c.name) != VERSION_NAME and _literal(c.value)
        sig[c.name] = c.value if keep else None
    for k in api.classes:
        sig[k.name] = [(f.name, f.type, f.optional) for f in k.fields]
    return sig


def diff(module: str, base: LuaApi, head: LuaApi) -> list[Finding]:
    """Break, doc and warning findings of `head` against `base`."""
    out: list[Finding] = []

    def add(kind: Any, message: str, item: Any = None) -> None:
        file = item.file if item else None
        line = item.line if item else None
        out.append(Finding(module, kind, message, file, line))

    bf, hf = _by_name(base.funcs, lambda f: f.name), _by_name(head.funcs, lambda f: f.name)
    bc, hc = _by_name(base.consts, lambda c: c.name), _by_name(head.consts, lambda c: c.name)
    bk, hk = _by_name(base.classes, lambda c: c.name), _by_name(head.classes, lambda c: c.name)

    for name, old in bf.items():
        new = hf.get(name)
        if new is None:
            add("break", f"function {name} removed")
            continue
        for what in _func_breaks(old, new):
            add("break", f"{name}: {what}", new)
        if len(new.examples) < len(old.examples):
            add("doc", f"{name}: example removed", new)
    for name, old_c in bc.items():
        new_c = hc.get(name)
        if new_c is None:
            add("break", f"constant {name} removed")
        elif (
            _bare(name) != VERSION_NAME
            and old_c.value != new_c.value
            and _literal(old_c.value)
            and _literal(new_c.value)
        ):
            add("break", f"constant {name} changed: {old_c.value} -> {new_c.value}", new_c)
    for name, old_k in bk.items():
        new_k = hk.get(name)
        if new_k is None:
            add("break", f"class {name} removed")
            continue
        new_fields = _by_name(new_k.fields, lambda f: f.name)
        for f in old_k.fields:
            g = new_fields.get(f.name)
            if g is None:
                add("break", f"{name}.{f.name} removed", new_k)
            elif (f.type, f.optional) != (g.type, g.optional):
                old_t = f.type + ("?" if f.optional else "")
                new_t = g.type + ("?" if g.optional else "")
                add("break", f"{name}.{f.name} type {old_t} -> {new_t}", new_k)

    for name, new in hf.items():
        if name not in bf:
            if not new.desc:
                add("doc", f"function {name} has no description", new)
            if not new.examples:
                add("doc", f"function {name} has no example", new)
    for name, new_c in hc.items():
        if name not in bc and not new_c.desc:
            add("doc", f"constant {name} has no description", new_c)
    for name, new_k in hk.items():
        if name not in bk and not new_k.desc:
            add("doc", f"class {name} has no description", new_k)

    old_v, new_v = _version(base), _version(head)
    if _signature(base) != _signature(head) and old_v is not None and new_v is not None:
        if new_v <= old_v:
            add("warning", f"declarations changed but {VERSION_NAME} is still {new_v}")
    return out


def check(module: Module, base_root: Path, head_root: Path) -> list[Finding]:
    """Lua breaks and doc findings of `module` between two trees."""
    if "lua" not in module.api or not module.lua:
        return []
    return diff(module.name, load(base_root, module.lua), load(head_root, module.lua))
