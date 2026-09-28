"""MWo3 overlays, the game's loadable code modules (game_task, game_sub, em*, stage*), each
mapped at the address it loads to."""

from __future__ import annotations

import struct
from pathlib import Path

from .memory import Image

_HEADER = struct.Struct("<4sIIIIIII")
MAGIC = b"MWo3"
TEXT = 0x80
"""Code starts at file offset 0x80: a 64-byte header, then 64 bytes of padding."""


class Overlay(Image):
    """An overlay file; its header sits at `load` and its code at `load + TEXT`."""

    def __init__(self, data: bytes, name: str = "") -> None:
        if not self.sniff(data):
            raise ValueError(f"{name or 'data'} is not an MWo3 overlay")
        (_, self.id, load, text_size, data_size, bss_size, ctor_start, ctor_end) = (
            _HEADER.unpack_from(data)
        )
        super().__init__(data, load, data[32:64].split(b"\0", 1)[0].decode("ascii") or name)
        self.load = load
        self.text = range(load + TEXT, load + TEXT + text_size)
        self.initialised = range(self.text.stop, self.text.stop + data_size)
        self.bss = range(self.initialised.stop, self.initialised.stop + bss_size)
        self.ctors = range(ctor_start, ctor_end)

    @staticmethod
    def sniff(data: bytes) -> bool:
        return len(data) >= TEXT and data[:4] == MAGIC

    @classmethod
    def from_path(cls, path: str | Path) -> Overlay:
        path = Path(path)
        return cls(path.read_bytes(), path.name)

    @property
    def species(self) -> int | None:
        """The monster species of an `emNN.ovl`, else None."""
        stem = self.name.removesuffix(".ovl")
        return int(stem[2:]) if stem.startswith("em") and stem[2:].isdigit() else None
