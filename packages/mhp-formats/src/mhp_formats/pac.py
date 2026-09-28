# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""PAC, the container both games pack everything into: a count, an `(offset, size)` table and the
entries, each aligned. Monster models, stages and the collision inside a stage are all PACs."""

from dataclasses import dataclass, field
from typing import Self

from construct import Array, Int32ul, Struct, this

from ._base import FormatError

_HEADER = Struct("count" / Int32ul, "table" / Array(this.count, Array(2, Int32ul)))
_ALIGNS = (16, 8, 4)
_MAX_ENTRIES = 256


def _up(n: int, align: int) -> int:
    return -(-n // align) * align


@dataclass
class Pac:
    """An empty entry is `b""` and sits in the table as `(0, 0)`."""

    entries: list[bytes] = field(default_factory=list)
    align: int = 16
    tail: bytes = b""
    """Bytes after the last entry, up to the end of the file (a monster PAC pads to 0x800)."""

    @staticmethod
    def sniff(data: bytes) -> bool:
        return _layout(data) is not None

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        table = _layout(data)
        if table is None:
            raise FormatError("not a PAC: the table does not describe the file")
        entries = [data[off : off + size] for off, size in table]
        end = max((off + size for off, size in table if size), default=4 + 8 * len(table))
        for align in _ALIGNS:
            pac = cls(entries, align, data[end:])
            if pac.to_bytes() == data:
                return pac
        raise FormatError("PAC entries are not laid out at any alignment the games use")

    def to_bytes(self) -> bytes:
        out = bytearray(_up(4 + 8 * len(self.entries), self.align))
        table = []
        for entry in self.entries:
            if not entry:
                table.append((0, 0))
                continue
            out += bytes(_up(len(out), self.align) - len(out))
            table.append((len(out), len(entry)))
            out += entry
        out[: 4 + 8 * len(table)] = _HEADER.build({"count": len(table), "table": table})
        return bytes(out) + self.tail


def _layout(data: bytes) -> list[tuple[int, int]] | None:
    if len(data) < 12:
        return None
    count = int.from_bytes(data[:4], "little")
    head = 4 + 8 * count
    if not 0 < count <= _MAX_ENTRIES or head > len(data):
        return None
    table = [(int(off), int(size)) for off, size in _HEADER.parse(data).table]
    cursor = head
    for off, size in table:
        if not size:
            if off:
                return None
            continue
        pad = data[cursor:off]
        if off < cursor or off + size > len(data) or len(pad) >= _ALIGNS[0] or any(pad):
            return None
        cursor = off + size
    return table if cursor > head else None
