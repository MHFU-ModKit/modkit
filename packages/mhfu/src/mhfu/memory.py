"""Game memory, live or from bytes: views and analyses read through `Memory` and run on either.

Live(client)                      # the running game, through ppsspp_debug.Client
Image(dump, base=RAM.start)       # a RAM dump, an overlay file, BOOT.BIN
Space([eboot, game_task, em75])   # several images as one address space
"""

from __future__ import annotations

import struct
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ppsspp_debug import Client, DebuggerError

_U8, _U16, _U32 = struct.Struct("<B"), struct.Struct("<H"), struct.Struct("<I")
_S8, _S16, _S32 = struct.Struct("<b"), struct.Struct("<h"), struct.Struct("<i")
_F32, _VEC3 = struct.Struct("<f"), struct.Struct("<3f")


class Unmapped(LookupError):
    """An address no image covers."""


class Memory(ABC):
    """Little-endian game memory; subclasses supply `read` and `write`."""

    @abstractmethod
    def read(self, address: int, size: int) -> bytes: ...

    @abstractmethod
    def write(self, address: int, data: bytes) -> None: ...

    def unpack(self, fmt: struct.Struct, address: int) -> tuple[Any, ...]:
        return fmt.unpack(self.read(address, fmt.size))

    def u8(self, address: int) -> int:
        return int(self.unpack(_U8, address)[0])

    def u16(self, address: int) -> int:
        return int(self.unpack(_U16, address)[0])

    def u32(self, address: int) -> int:
        return int(self.unpack(_U32, address)[0])

    def s8(self, address: int) -> int:
        return int(self.unpack(_S8, address)[0])

    def s16(self, address: int) -> int:
        return int(self.unpack(_S16, address)[0])

    def s32(self, address: int) -> int:
        return int(self.unpack(_S32, address)[0])

    def f32(self, address: int) -> float:
        return float(self.unpack(_F32, address)[0])

    def vec3(self, address: int) -> tuple[float, float, float]:
        x, y, z = self.unpack(_VEC3, address)
        return x, y, z

    def cstr(self, address: int, limit: int = 256) -> str:
        """The NUL-terminated string at `address`, at most `limit` bytes."""
        return self.read(address, limit).split(b"\0", 1)[0].decode("utf-8", "replace")

    def write_u8(self, address: int, value: int) -> None:
        self.write(address, _U8.pack(value))

    def write_u16(self, address: int, value: int) -> None:
        self.write(address, _U16.pack(value))

    def write_u32(self, address: int, value: int) -> None:
        self.write(address, _U32.pack(value))

    def write_f32(self, address: int, value: float) -> None:
        self.write(address, _F32.pack(value))


class Image(Memory):
    """Bytes mapped at `base`; writes change the copy, never the source."""

    def __init__(self, data: bytes, base: int, name: str = "") -> None:
        self.data = bytearray(data)
        self.base = base
        self.name = name

    @classmethod
    def from_file(cls, path: str | Path, base: int) -> Image:
        path = Path(path)
        return cls(path.read_bytes(), base, path.name)

    @property
    def end(self) -> int:
        return self.base + len(self.data)

    def __contains__(self, address: object) -> bool:
        return isinstance(address, int) and self.base <= address < self.end

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name} 0x{self.base:08X}-0x{self.end:08X}>"

    def _offset(self, address: int, size: int) -> int:
        offset = address - self.base
        if offset < 0 or offset + size > len(self.data):
            raise Unmapped(f"0x{address:08X}+{size} is outside {self!r}")
        return offset

    def read(self, address: int, size: int) -> bytes:
        offset = self._offset(address, size)
        return bytes(self.data[offset : offset + size])

    def write(self, address: int, data: bytes) -> None:
        offset = self._offset(address, len(data))
        self.data[offset : offset + len(data)] = data

    def unpack(self, fmt: struct.Struct, address: int) -> tuple[Any, ...]:
        return fmt.unpack_from(self.data, self._offset(address, fmt.size))


class Space(Memory):
    """Several images as one address space; an access must fall inside one of them."""

    def __init__(self, images: Iterable[Image]) -> None:
        self.images = sorted(images, key=lambda i: i.base)
        for a, b in zip(self.images, self.images[1:], strict=False):
            if b.base < a.end:
                raise ValueError(f"{a!r} overlaps {b!r}")

    def image(self, address: int) -> Image:
        for i in self.images:
            if address in i:
                return i
        raise Unmapped(f"0x{address:08X} is in none of {self.images}")

    def __contains__(self, address: object) -> bool:
        return any(address in i for i in self.images)

    def read(self, address: int, size: int) -> bytes:
        return self.image(address).read(address, size)

    def write(self, address: int, data: bytes) -> None:
        self.image(address).write(address, data)

    def unpack(self, fmt: struct.Struct, address: int) -> tuple[Any, ...]:
        return self.image(address).unpack(fmt, address)


class Live(Memory):
    """The running game's memory, through the debugger; a read it refuses is `Unmapped`."""

    def __init__(self, client: Client) -> None:
        self.client = client

    def read(self, address: int, size: int) -> bytes:
        try:
            return self.client.read(address, size)
        except DebuggerError as e:
            raise Unmapped(f"0x{address:08X}+{size}: {e}") from e

    def write(self, address: int, data: bytes) -> None:
        self.client.write(address, data)
