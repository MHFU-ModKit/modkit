"""Typed views of game structs. Offsets and types come from addresses.toml only: a field
declared here with a type the table disagrees with fails at import.

    class Entity(View):
        struct = addresses.ENTITY
        hp = u16(addresses.ENTITY.HP)
        position = vec3(addresses.ENTITY.POSITION)

    Entity(mem, base).hp          # reads; assigning writes

A view with no `struct` reads absolute addresses (`u8(addresses.SCREEN_STATE)`) at base 0.
"""

from __future__ import annotations

import re
import struct as _struct
from typing import Any, ClassVar, Generic, Self, TypeVar, overload

from .addresses import Address, Field, Struct
from .memory import Memory

T = TypeVar("T")

_ARRAY = re.compile(r"(\w+)\[(\d+)\]\Z")
_FORMATS = {"u8": "B", "u16": "H", "u32": "I", "s8": "b", "s16": "h", "s32": "i", "f32": "f"}
_FORMATS |= {"ptr": "I", "vtable": "I", "fn": "I", "code": "I", "vec3": "3f"}


class View:
    """A struct (`struct`) or a set of absolute addresses (no `struct`) in `mem`."""

    struct: ClassVar[Struct | None] = None

    def __init__(self, mem: Memory, base: int = 0) -> None:
        self.mem = mem
        self.base = base

    def __repr__(self) -> str:
        return f"<{type(self).__name__} 0x{self.base:08X}>"


class Value(Generic[T]):
    """One field of a view; `u16(...)`, `vec3(...)` and friends build it."""

    def __init__(self, where: Field | Address, type: str, array: bool) -> None:
        declared = where.type
        if array:
            m = _ARRAY.match(declared)
            if not m or m[1] != type:
                raise TypeError(f"{where.name} is {declared} in addresses.toml, not {type}[N]")
            count = int(m[2])
        elif declared != type:
            raise TypeError(f"{where.name} is {declared} in addresses.toml, not {type}")
        else:
            count = 1
        self.where = where
        self.multi = array or type == "vec3"
        self.format = _struct.Struct("<" + _FORMATS[type] * count)

    def __set_name__(self, owner: type[View], name: str) -> None:
        s = owner.struct
        if s is None:
            ok = isinstance(self.where, Address)
        else:
            ok = s.fields.get(self.where.name) is self.where
        if not ok:
            raise TypeError(f"{owner.__name__}.{name}: {self.where!r} is not in {s or 'addresses'}")

    @overload
    def __get__(self, view: None, owner: type[View]) -> Self: ...
    @overload
    def __get__(self, view: View, owner: type[View]) -> T: ...
    def __get__(self, view: View | None, owner: type[View]) -> Self | T:
        if view is None:
            return self
        values = view.mem.unpack(self.format, view.base + self.where)
        return self._value(values)

    def __set__(self, view: View, value: T) -> None:
        values: Any = value if self.multi else (value,)
        view.mem.write(view.base + self.where, self.format.pack(*values))

    def address(self, view: View) -> int:
        """The field's absolute address in `view`."""
        return view.base + self.where

    def _value(self, values: tuple[Any, ...]) -> T:
        return values if self.multi else values[0]  # type: ignore[return-value]


def u8(where: Field | Address) -> Value[int]:
    return Value(where, "u8", False)


def u16(where: Field | Address) -> Value[int]:
    return Value(where, "u16", False)


def u32(where: Field | Address) -> Value[int]:
    return Value(where, "u32", False)


def s8(where: Field | Address) -> Value[int]:
    return Value(where, "s8", False)


def s16(where: Field | Address) -> Value[int]:
    return Value(where, "s16", False)


def s32(where: Field | Address) -> Value[int]:
    return Value(where, "s32", False)


def f32(where: Field | Address) -> Value[float]:
    return Value(where, "f32", False)


def ptr(where: Field | Address) -> Value[int]:
    return Value(where, "ptr", False)


def vec3(where: Field | Address) -> Value[tuple[float, float, float]]:
    return Value(where, "vec3", False)


def u8s(where: Field | Address) -> Value[tuple[int, ...]]:
    return Value(where, "u8", True)


def u16s(where: Field | Address) -> Value[tuple[int, ...]]:
    return Value(where, "u16", True)


def u32s(where: Field | Address) -> Value[tuple[int, ...]]:
    return Value(where, "u32", True)


def s16s(where: Field | Address) -> Value[tuple[int, ...]]:
    return Value(where, "s16", True)


def f32s(where: Field | Address) -> Value[tuple[float, ...]]:
    return Value(where, "f32", True)


def ptrs(where: Field | Address) -> Value[tuple[int, ...]]:
    return Value(where, "ptr", True)
