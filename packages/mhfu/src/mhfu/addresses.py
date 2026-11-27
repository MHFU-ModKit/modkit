# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Game addresses and struct offsets for MHFU EU, read from addresses.toml.

    from mhfu import addresses
    addresses.SCREEN_STATE        # an int that also has .name, .type and .doc
    addresses.ENTITY.HP           # the offset of a struct field, the same kind of int
    addresses.ENTITY_REGISTRY.count   # 21: the n of a `type[n]` entry, else None
    addresses.MONSTER_EVENT_KIND.names  # an enum's names, numbered from 1 in C and Lua
    addresses.FREE_ROAM_SCENES    # a set of addresses, a frozenset
    addresses.OWN_MOVE_MAIN       # a constant: a number C, Lua and Python agree on, no address

A struct's `size` may be "auto" and a field's `offset` "next": the file then says the layout the
way C lays it out (each field aligned to its type, the size rounded to the largest alignment), and
an array's `[n]` may name a `[const]`, so a table's capacity is one number.

The C header, the Lua table and the C data lua_host builds mhfu.addr from are generated from
the same file:

    python -m mhfu.addresses c -o addresses.gen.h
    python -m mhfu.addresses lua -o addresses.gen.lua
    python -m mhfu.addresses c-table -o addresses_table.gen.inc
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
# a base type or a struct name, optionally [n] of them, n a number or a [const]'s name
_TYPE = re.compile(r"(\w+)(?:\[([1-9][0-9]*|[A-Z][A-Z0-9_]*)\])?\Z")
# the bytes of a base type that a field can have; its alignment is its size, a vec3's 4
_SIZES = {"u8": 1, "s8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4, "f32": 4, "vec3": 12}
_SIZES |= {"ptr": 4, "vtable": 4, "fn": 4, "code": 4}
AUTO, NEXT = "auto", "next"


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
        return int(m[2]) if m and m[2] and m[2].isdigit() else None


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


class Const(_Named):
    """A number the game does not hold that C, Lua and Python must agree on (`[const]`)."""

    def __repr__(self) -> str:
        return f"<{self.name} {int(self)}>"


class AddressSet(frozenset[Address]):
    """Addresses named together (`[set]`); `of` keeps the file's order."""

    name: str
    doc: str
    of: tuple[Address, ...]

    def __new__(cls, of: tuple[Address, ...], name: str, doc: str) -> AddressSet:
        self = super().__new__(cls, of)
        self.name, self.doc, self.of = name, doc, of
        return self


@dataclass(frozen=True)
class Table:
    addresses: dict[str, Address]
    structs: dict[str, Struct]
    enums: dict[str, Enum] = field(default_factory=dict)
    sets: dict[str, AddressSet] = field(default_factory=dict)
    consts: dict[str, Const] = field(default_factory=dict)


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
    unknown = set(data) - {"address", "struct", "enum", "set", "const"}
    if unknown:
        problems.append(f"unknown top-level tables: {sorted(unknown)}")

    consts: dict[str, Const] = {}
    for name, entry in data.get("const", {}).items():
        where = f"const.{name}"
        if not _NAME.match(name):
            problems.append(f"{where}: names are UPPER_SNAKE_CASE")
        if not isinstance(entry, dict) or set(entry) != {"value", "doc"}:
            problems.append(f"{where}: expects exactly value and doc")
            continue
        value = entry["value"]
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFF_FFFF:
            problems.append(f"{where}: value is an integer from 0 to 0xFFFFFFFF")
        elif _doc_ok(where, entry["doc"], problems):
            consts[name] = Const(value, name, "u32", entry["doc"])

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
            type_ = _counted(where, entry["type"], consts, problems)
            addresses[name] = Address(value, name, type_, entry["doc"])

    raw: dict[str, dict[str, Any]] = {}
    raw_fields: dict[str, dict[str, dict[str, Any]]] = {}
    for name, entry in data.get("struct", {}).items():
        where = f"struct.{name}"
        if not _NAME.match(name):
            problems.append(f"{where}: names are UPPER_SNAKE_CASE")
        if not isinstance(entry, dict) or set(entry) - {"doc", "size", "stride", "fields"}:
            problems.append(f"{where}: expects doc, fields and an optional size or stride")
            continue
        size, stride = entry.get("size"), entry.get("stride")
        if size is not None and size != AUTO and (not isinstance(size, int) or size <= 0):
            problems.append(f'{where}: size must be a positive integer or "{AUTO}"')
            entry = {**entry, "size": None}
        if stride is not None and (not isinstance(stride, int) or stride <= 0):
            problems.append(f"{where}: stride must be a positive integer")
            entry = {**entry, "stride": None}
        _doc_ok(where, entry.get("doc"), problems)
        raw[name] = entry
        raw_fields[name] = {}
        for fname, fentry in entry.get("fields", {}).items():
            fwhere = f"{where}.{fname}"
            if _entry_ok(fwhere, fname, fentry, "offset", problems, NEXT):
                type_ = _counted(fwhere, fentry["type"], consts, problems)
                raw_fields[name][fname] = {**fentry, "type": type_}

    layout = _Layout(raw, raw_fields, problems)
    structs: dict[str, Struct] = {}
    for name, entry in raw.items():
        where = f"struct.{name}"
        size, _, offsets = layout.of(name)
        fields: dict[str, Field] = {}
        by_offset: dict[int, str] = {}
        for fname, fentry in raw_fields[name].items():
            fwhere = f"{where}.{fname}"
            offset = offsets.get(fname)
            if offset is None:
                continue
            if offset < 0 or (size is not None and offset >= size):
                problems.append(f"{fwhere}: offset 0x{offset:X} is outside the struct")
            elif offset in by_offset:
                problems.append(f"{fwhere}: +0x{offset:X} is already {by_offset[offset]}")
            by_offset.setdefault(offset, fname)
            fields[fname] = Field(offset, fname, fentry["type"], fentry["doc"])
        if not fields:
            problems.append(f"{where}: has no fields")
        structs[name] = Struct(name, entry.get("doc", ""), fields, size, entry.get("stride"))

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

    sets: dict[str, AddressSet] = {}
    for name, entry in data.get("set", {}).items():
        where = f"set.{name}"
        if not _NAME.match(name):
            problems.append(f"{where}: names are UPPER_SNAKE_CASE")
        if not isinstance(entry, dict) or set(entry) != {"of", "doc"}:
            problems.append(f"{where}: expects exactly of and doc")
            continue
        of = entry["of"]
        if (
            not isinstance(of, list)
            or not of
            or not all(isinstance(n, str) for n in of)
            or len(set(of)) != len(of)
        ):
            problems.append(f"{where}: of is a list of distinct address names")
            continue
        missing = [n for n in of if n not in addresses]
        if missing:
            problems.append(f"{where}: {', '.join(missing)} is not an address")
        elif _doc_ok(where, entry["doc"], problems):
            sets[name] = AddressSet(tuple(addresses[n] for n in of), name, entry["doc"])

    # every generated name (Python and Lua namespace, C macro) must be unique
    for name in addresses.keys() & structs.keys():
        problems.append(f"{name}: is both an address and a struct")
    for name in enums.keys() & (addresses.keys() | structs.keys()):
        problems.append(f"{name}: is an enum and an address or struct")
    for name in sets.keys() & (addresses.keys() | structs.keys() | enums.keys()):
        problems.append(f"{name}: is a set and an address, struct or enum")
    for name in consts.keys() & (addresses.keys() | structs.keys() | enums.keys() | sets.keys()):
        problems.append(f"{name}: is a const and an address, struct, enum or set")
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
    members += [(f"set.{n}{m}", f"{n}{m}") for n in sets for m in ("", "_COUNT")]
    members += [(f"const.{n}", n) for n in consts]
    for where, macro in members:
        if macro in macros:
            problems.append(f"{where}: MHFU_{macro} clashes with {macros[macro]}")
        macros.setdefault(macro, where)

    if problems:
        raise ValueError("addresses.toml:\n  " + "\n  ".join(problems))
    return Table(addresses, structs, enums, sets, consts)


def _entry_ok(
    where: str, name: str, entry: Any, key: str, problems: list[str], word: str | None = None
) -> bool:
    if not _NAME.match(name):
        problems.append(f"{where}: names are UPPER_SNAKE_CASE")
    if not isinstance(entry, dict) or set(entry) != {key, "type", "doc"}:
        problems.append(f"{where}: expects exactly {key}, type and doc")
        return False
    if not isinstance(entry[key], int) and entry[key] != word:
        problems.append(f"{where}: {key} must be an integer" + (f' or "{word}"' if word else ""))
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


def _counted(where: str, type_: str, consts: dict[str, Const], problems: list[str]) -> str:
    """`type_` with a const's name in its `[n]` replaced by the number."""
    m = _TYPE.match(type_)
    if not m or not m[2] or m[2].isdigit():
        return type_
    if not consts.get(m[2]):
        problems.append(f"{where}: [{m[2]}] is not a const above zero")
        return type_
    return f"{m[1]}[{int(consts[m[2]])}]"


class _Layout:
    """The size, alignment and field offsets of the file's structs, worked out on demand: a
    field at "next" follows the one before it as C places it, a struct of size "auto" ends where
    its last field does, rounded to its largest alignment."""

    def __init__(
        self,
        entries: dict[str, dict[str, Any]],
        fields: dict[str, dict[str, dict[str, Any]]],
        problems: list[str],
    ) -> None:
        self.entries, self.fields, self.problems = entries, fields, problems
        self.done: dict[str, tuple[int | None, int, dict[str, int]]] = {}
        self.busy: set[str] = set()

    def of(self, name: str) -> tuple[int | None, int, dict[str, int]]:
        """(size or None, alignment, offset by field) of struct `name`."""
        if name in self.done:
            return self.done[name]
        if name in self.busy:
            self.problems.append(f"struct.{name}: contains itself")
            return None, 1, {}
        self.busy.add(name)
        end, align = 0, 1
        offsets: dict[str, int] = {}
        unsized: list[str] = []
        for fname, fentry in self.fields[name].items():
            where = f"struct.{name}.{fname}"
            have = self.sized(fentry["type"])
            offset = fentry["offset"]
            if offset == NEXT:
                if have is None or unsized:
                    why = "a type with a size" if have is None else f"{unsized[-1]} to have one"
                    self.problems.append(f'{where}: offset "{NEXT}" needs {why}')
                    continue
                offset = -(-end // have[1]) * have[1]
            offsets[fname] = offset
            if have is None:
                unsized.append(fname)
            else:
                end, align = max(end, offset + have[0]), max(align, have[1])
        size = self.entries[name].get("size")
        if size == AUTO:
            if unsized:
                self.problems.append(f'struct.{name}: size "{AUTO}" needs a size for {unsized}')
            size = -(-end // align) * align
        self.busy.discard(name)
        self.done[name] = size, align, offsets
        return self.done[name]

    def sized(self, type_: str) -> tuple[int, int] | None:
        """(bytes, alignment) of a field's type, None where it has no size."""
        m = _TYPE.match(type_)
        if not m:
            return None
        if m[1] in _SIZES:
            size, align = _SIZES[m[1]], 4 if m[1] == "vec3" else _SIZES[m[1]]
        elif m[1] in self.entries:
            size_, align = self.of(m[1])[:2]
            if size_ is None:
                return None
            size = size_
        else:
            return None
        return size * (int(m[2]) if m[2] and m[2].isdigit() else 1), align


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
    for st in t.sets.values():
        out += ["", f"/* set: {st.doc} */"]
        out.append(
            f"#define MHFU_{st.name} {{ " + ", ".join(f"MHFU_{a.name}" for a in st.of) + " }"
        )
        out.append(f"#define MHFU_{st.name}_COUNT {len(st.of)}")
    for c in t.consts.values():
        out += ["", f"/* const: {c.doc} */", f"#define MHFU_{c.name} {int(c)}"]
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
    for st in t.sets.values():
        out += [f"    ---set, member = address: {st.doc}", f"    {st.name} = {{"]
        out += [f"        {a.name} = 0x{int(a):08X}," for a in st.of]
        out.append("    },")
    for c in t.consts.values():
        out += [f"    ---const: {c.doc}", f"    {c.name} = {int(c)},"]
    out += ["}", "", "return addr", ""]
    return "\n".join(out)


def render_c_table(t: Table) -> str:
    """The table as C data with addresses.gen.h's values: what lua_host builds mhfu.addr from,
    one entry at a time."""
    out = [
        f"/* mhfu.addr for MHFU {GAME_ID}, generated from mhfu/addresses.toml by",
        " * `python -m mhfu.addresses c-table`. Do not edit. */",
        "#include <stdint.h>",
        '#include "addresses.gen.h"',
        "",
        "typedef struct { const char *name; uint32_t value; } mhfu_addr_field;",
        "/* an address or a count, a struct's or set's fields, or an enum's names */",
        "typedef struct {",
        "    const char *name;",
        "    uint32_t value;",
        "    const mhfu_addr_field *fields;",
        "    const char *const *names;",
        "} mhfu_addr_entry;",
        "",
    ]
    rows = []
    for a in t.addresses.values():
        rows.append(f'    {{ "{a.name}", MHFU_{a.name}, 0, 0 }},')
        if a.count:
            rows.append(f'    {{ "{a.name}_COUNT", MHFU_{a.name}_COUNT, 0, 0 }},')
    for s in t.structs.values():
        names = ["SIZE"] * bool(s.size) + ["STRIDE"] * bool(s.stride)
        for f in s.fields.values():
            names += [f.name] + [f"{f.name}_COUNT"] * bool(f.count)
        out.append(f"static const mhfu_addr_field k_addr_{s.name}[] = {{")
        out += [f'    {{ "{n}", MHFU_{s.name}_{n} }},' for n in names]
        out += ["    { 0, 0 },", "};"]
        rows.append(f'    {{ "{s.name}", 0, k_addr_{s.name}, 0 }},')
    for e in t.enums.values():
        out.append(f"static const char *const k_addr_{e.name}[] = {{ MHFU_{e.name}_NAMES, 0 }};")
        rows.append(f'    {{ "{e.name}", 0, 0, k_addr_{e.name} }},')
    for st in t.sets.values():
        out.append(f"static const mhfu_addr_field k_addr_{st.name}[] = {{")
        out += [f'    {{ "{a.name}", MHFU_{a.name} }},' for a in st.of]
        out += ["    { 0, 0 },", "};"]
        rows.append(f'    {{ "{st.name}", 0, k_addr_{st.name}, 0 }},')
    for c in t.consts.values():
        rows.append(f'    {{ "{c.name}", MHFU_{c.name}, 0, 0 }},')
    out += [
        "",
        "static const mhfu_addr_entry k_mhfu_addr[] = {",
        *rows,
        "    { 0, 0, 0, 0 },",
        "};",
        "",
    ]
    return "\n".join(out)


RENDER = {"c": render_c, "c-table": render_c_table, "lua": render_lua}


def __getattr__(name: str) -> Any:
    t = table()
    if name in t.addresses:
        return t.addresses[name]
    if name in t.structs:
        return t.structs[name]
    if name in t.enums:
        return t.enums[name]
    if name in t.sets:
        return t.sets[name]
    if name in t.consts:
        return t.consts[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    t = table()
    return sorted({*globals(), *t.addresses, *t.structs, *t.enums, *t.sets, *t.consts})


def main(argv: list[str] | None = None) -> int:
    """`python -m mhfu.addresses c|lua`, the same as `mhfu addresses c|lua`."""
    from .cli import main as cli

    return cli(["addresses", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
