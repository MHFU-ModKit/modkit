# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Game addresses and struct offsets for MHFU EU, read from addresses.toml.

    from mhfu import addresses
    addresses.SCREEN_STATE        # an int that also has .name, .type and .doc
    addresses.ENTITY.HP           # the offset of a struct field, the same kind of int
    addresses.ENTITY_REGISTRY.count   # 21: the n of a `type[n]` entry, else None
    addresses.MONSTER_EVENT_KIND.names  # an enum's names, numbered from 1 in C and Lua

The C header and the Lua table are generated from the same file:

    python -m mhfu.addresses c -o addresses.gen.h
    python -m mhfu.addresses lua -o addresses.gen.lua
"""

from __future__ import annotations

import functools
import re
import sys
import tomllib
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Any

GAME_ID = "ULES01213"
REGION = "eu"
RAM = range(0x0800_0000, 0x0C00_0000)  # noaddr: user RAM and the extra-RAM window

_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_LOWER = re.compile(r"[a-z][a-z0-9_]*\Z")
_BASE_TYPES = frozenset("u8 u16 u32 s8 s16 s32 f32 vec3 ptr vtable fn code bytes".split())
# a base type or a struct name, optionally [n] of them
_TYPE = re.compile(r"(\w+)(?:\[([1-9][0-9]*)\])?\Z")


class _Named(int):
    name: str
    type: str
    doc: str

    def __new__(cls, value: int, name: str, type: str, doc: str) -> _Named:
        self = super().__new__(cls, value)
        self.name, self.type, self.doc = name, type, doc
        return self

    __str__ = int.__repr__

    @property
    def count(self) -> int | None:
        """The n of a `type[n]` entry; None for a single value."""
        m = _TYPE.match(self.type)
        return int(m[2]) if m and m[2] else None


class Address(_Named):
    """An absolute game address."""

    def __repr__(self) -> str:
        return f"<{self.name} 0x{int(self):08X} {self.type}>"


class Field(_Named):
    """A field's offset from the base of its struct."""

    def __repr__(self) -> str:
        return f"<{self.name} +0x{int(self):X} {self.type}>"


@dataclass(frozen=True)
class Struct:
    """A struct layout; its fields are attributes."""

    name: str
    doc: str
    fields: dict[str, Field]
    size: int | None = None
    stride: int | None = None

    @property
    def step(self) -> int:
        """Bytes from one of these to the next in a table: `stride`, else `size`."""
        step = self.stride or self.size
        if step is None:
            raise TypeError(f"struct {self.name} has no size or stride in addresses.toml")
        return step

    def __getattr__(self, name: str) -> Field:
        fields: dict[str, Field] = self.__dict__.get("fields", {})
        if name in fields:
            return fields[name]
        raise AttributeError(f"struct {self.__dict__.get('name')} has no field {name}")

    def __dir__(self) -> list[str]:
        return [*super().__dir__(), *self.fields]


@dataclass(frozen=True)
class Enum:
    """Names numbered from 1 (`[enum.X] names`): MHFU_X_<NAME> in C, a list in Lua."""

    name: str
    doc: str
    names: tuple[str, ...]

    def number(self, name: str) -> int:
        return self.names.index(name) + 1


@dataclass(frozen=True)
class Table:
    addresses: dict[str, Address]
    structs: dict[str, Struct]
    enums: dict[str, Enum] = field(default_factory=dict)


def load(path: Path | None = None) -> Table:
    """Read and check a table; the packaged addresses.toml by default."""
    source = Path(path) if path else files(__package__).joinpath("addresses.toml")
    return parse(tomllib.loads(source.read_text(encoding="utf-8")))


@functools.cache
def table() -> Table:
    """The packaged table, read once."""
    return load()


def parse(data: dict[str, Any]) -> Table:
    """Build a table from parsed TOML; raises ValueError listing every problem."""
    problems: list[str] = []
    unknown = set(data) - {"address", "struct", "enum"}
    if unknown:
        problems.append(f"unknown top-level tables: {sorted(unknown)}")

    addresses: dict[str, Address] = {}
    by_value: dict[int, str] = {}
    for name, entry in data.get("address", {}).items():
        where = f"address.{name}"
        if _entry_ok(where, name, entry, "eu", problems):
            value = entry["eu"]
            if value not in RAM and value != RAM.stop:  # the window's end bound may be named
                problems.append(f"{where}: 0x{value:X} is outside 0x{RAM.start:X}-0x{RAM.stop:X}")
            elif value in by_value:
                problems.append(f"{where}: 0x{value:08X} is already {by_value[value]}")
            by_value.setdefault(value, name)
            addresses[name] = Address(value, name, entry["type"], entry["doc"])

    structs: dict[str, Struct] = {}
    for name, entry in data.get("struct", {}).items():
        where = f"struct.{name}"
        if not _NAME.match(name):
            problems.append(f"{where}: names are UPPER_SNAKE_CASE")
        if not isinstance(entry, dict) or set(entry) - {"doc", "size", "stride", "fields"}:
            problems.append(f"{where}: expects doc, fields and an optional size or stride")
            continue
        size, stride = entry.get("size"), entry.get("stride")
        if size is not None and (not isinstance(size, int) or size <= 0):
            problems.append(f"{where}: size must be a positive integer")
            size = None
        if stride is not None and (not isinstance(stride, int) or stride <= 0):
            problems.append(f"{where}: stride must be a positive integer")
            stride = None
        _doc_ok(where, entry.get("doc"), problems)
        fields: dict[str, Field] = {}
        by_offset: dict[int, str] = {}
        for fname, fentry in entry.get("fields", {}).items():
            fwhere = f"{where}.{fname}"
            if not _entry_ok(fwhere, fname, fentry, "offset", problems):
                continue
            offset = fentry["offset"]
            if offset < 0 or (size is not None and offset >= size):
                problems.append(f"{fwhere}: offset 0x{offset:X} is outside the struct")
            elif offset in by_offset:
                problems.append(f"{fwhere}: +0x{offset:X} is already {by_offset[offset]}")
            by_offset.setdefault(offset, fname)
            fields[fname] = Field(offset, fname, fentry["type"], fentry["doc"])
        if not fields:
            problems.append(f"{where}: has no fields")
        structs[name] = Struct(name, entry.get("doc", ""), fields, size, stride)

    typed: list[tuple[str, _Named]] = [(f"address.{n}", a) for n, a in addresses.items()]
    typed += [(f"struct.{s.name}.{n}", f) for s in structs.values() for n, f in s.fields.items()]
    for where, named in typed:
        m = _TYPE.match(named.type)
        if m and m[1] not in _BASE_TYPES and m[1] not in structs:
            problems.append(f"{where}: unknown type {named.type!r}")

    enums: dict[str, Enum] = {}
    for name, entry in data.get("enum", {}).items():
        where = f"enum.{name}"
        if not _NAME.match(name):
            problems.append(f"{where}: names are UPPER_SNAKE_CASE")
        if not isinstance(entry, dict) or set(entry) != {"names", "doc"}:
            problems.append(f"{where}: expects exactly names and doc")
            continue
        names = entry["names"]
        if (
            not isinstance(names, list)
            or not names
            or not all(isinstance(n, str) and _LOWER.match(n) for n in names)
            or len(set(names)) != len(names)
        ):
            problems.append(f"{where}: names are distinct lower_snake_case strings")
            continue
        if _doc_ok(where, entry["doc"], problems):
            enums[name] = Enum(name, entry["doc"], tuple(names))

    # every generated name (Python and Lua namespace, C macro) must be unique
    for name in addresses.keys() & structs.keys():
        problems.append(f"{name}: is both an address and a struct")
    for name in enums.keys() & (addresses.keys() | structs.keys()):
        problems.append(f"{name}: is an enum and an address or struct")
    macros: dict[str, str] = {}
    members = [(f"address.{n}", n) for n in addresses]
    members += [(f"address.{n}_COUNT", f"{n}_COUNT") for n, a in addresses.items() if a.count]
    for s in structs.values():
        names = [*s.fields, *(["SIZE"] if s.size else []), *(["STRIDE"] if s.stride else [])]
        names += [f"{f}_COUNT" for f, v in s.fields.items() if v.count]
        members += [(f"struct.{s.name}.{m}", f"{s.name}_{m}") for m in names]
    for e in enums.values():
        names = [*(n.upper() for n in e.names), "COUNT", "NAMES"]
        members += [(f"enum.{e.name}.{m}", f"{e.name}_{m}") for m in names]
    for where, macro in members:
        if macro in macros:
            problems.append(f"{where}: MHFU_{macro} clashes with {macros[macro]}")
        macros.setdefault(macro, where)

    if problems:
        raise ValueError("addresses.toml:\n  " + "\n  ".join(problems))
    return Table(addresses, structs, enums)


def _entry_ok(where: str, name: str, entry: Any, key: str, problems: list[str]) -> bool:
    if not _NAME.match(name):
        problems.append(f"{where}: names are UPPER_SNAKE_CASE")
    if not isinstance(entry, dict) or set(entry) != {key, "type", "doc"}:
        problems.append(f"{where}: expects exactly {key}, type and doc")
        return False
    if not isinstance(entry[key], int):
        problems.append(f"{where}: {key} must be an integer")
        return False
    t = entry["type"]
    if (
        not isinstance(t, str)
        or not (m := _TYPE.match(t))
        or not (m[1] in _BASE_TYPES or _NAME.match(m[1]))
    ):
        problems.append(f"{where}: unknown type {t!r}")
        return False
    return _doc_ok(where, entry["doc"], problems)


def _doc_ok(where: str, doc: Any, problems: list[str]) -> bool:
    if not isinstance(doc, str) or not doc.strip():
        problems.append(f"{where}: doc is missing")
        return False
    if "\n" in doc or "*/" in doc:
        problems.append(f"{where}: doc must be one line without */")
        return False
    return True


def render_c(t: Table) -> str:
    """The table as a C header of MHFU_ macros."""
    out = [
        f"/* MHFU {GAME_ID} addresses, generated from mhfu/addresses.toml by",
        " * `python -m mhfu.addresses c`. Do not edit. */",
        "#ifndef MHFU_ADDRESSES_GEN_H",
        "#define MHFU_ADDRESSES_GEN_H",
        "",
    ]
    for a in t.addresses.values():
        out += [f"/* {a.type}: {a.doc} */", f"#define MHFU_{a.name} 0x{int(a):08X}u"]
        if a.count:
            out.append(f"#define MHFU_{a.name}_COUNT {a.count}")  # an int, for int loop indices
    for s in t.structs.values():
        out += ["", f"/* struct {s.name}: {s.doc} */"]
        if s.size:
            out.append(f"#define MHFU_{s.name}_SIZE 0x{s.size:X}u")
        if s.stride:
            out.append(f"#define MHFU_{s.name}_STRIDE 0x{s.stride:X}u")
        for f in s.fields.values():
            out += [f"/* {f.type}: {f.doc} */", f"#define MHFU_{s.name}_{f.name} 0x{int(f):X}u"]
            if f.count:
                out.append(f"#define MHFU_{s.name}_{f.name}_COUNT {f.count}")
    for e in t.enums.values():
        out += ["", f"/* enum {e.name}: {e.doc} */"]
        out += [f"#define MHFU_{e.name}_{n.upper()} {k}" for k, n in enumerate(e.names, 1)]
        out.append(f"#define MHFU_{e.name}_COUNT {len(e.names)}")
        out.append(f"#define MHFU_{e.name}_NAMES " + ", ".join(f'"{n}"' for n in e.names))
    out += ["", "#endif", ""]
    return "\n".join(out)


def render_lua(t: Table) -> str:
    """The table as a Lua module returning mhfu.addr, annotated for LuaLS."""
    out = [
        f"-- MHFU {GAME_ID} addresses, generated from mhfu/addresses.toml by",
        "-- `python -m mhfu.addresses lua`. Do not edit.",
        "",
        "---@class mhfu.addr",
        "local addr = {",
    ]
    for a in t.addresses.values():
        out += [f"    ---{a.type}: {a.doc}", f"    {a.name} = 0x{int(a):08X},"]
        if a.count:
            out += [f"    ---elements in {a.name}", f"    {a.name}_COUNT = {a.count},"]
    for s in t.structs.values():
        out += [f"    ---struct: {s.doc}", f"    {s.name} = {{"]
        if s.size:
            out += ["        ---the struct's size in bytes", f"        SIZE = 0x{s.size:X},"]
        if s.stride:
            out += [
                "        ---bytes from one row to the next",
                f"        STRIDE = 0x{s.stride:X},",
            ]
        for f in s.fields.values():
            out += [f"        ---{f.type}: {f.doc}", f"        {f.name} = 0x{int(f):X},"]
            if f.count:
                out += [f"        ---elements in {f.name}", f"        {f.name}_COUNT = {f.count},"]
        out.append("    },")
    for e in t.enums.values():
        out += [f"    ---enum, numbered from 1: {e.doc}"]
        out.append(f"    {e.name} = {{ " + ", ".join(f'"{n}"' for n in e.names) + " },")
    out += ["}", "", "return addr", ""]
    return "\n".join(out)


RENDER = {"c": render_c, "lua": render_lua}


def __getattr__(name: str) -> Any:
    t = table()
    if name in t.addresses:
        return t.addresses[name]
    if name in t.structs:
        return t.structs[name]
    if name in t.enums:
        return t.enums[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    t = table()
    return sorted({*globals(), *t.addresses, *t.structs, *t.enums})


def main(argv: list[str] | None = None) -> int:
    """`python -m mhfu.addresses c|lua`, the same as `mhfu addresses c|lua`."""
    from .cli import main as cli

    return cli(["addresses", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
