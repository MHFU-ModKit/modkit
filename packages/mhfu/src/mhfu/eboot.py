"""BOOT.BIN, the plain-ELF twin of the encrypted EBOOT.BIN: the main binary as the game loads
it, before any JIT touches it."""

from __future__ import annotations

import io
from pathlib import Path

from elftools.elf.elffile import ELFFile

from .memory import Image


class Eboot(Image):
    """The loaded segment, mapped at its address; `sections` names the ranges the ELF lists."""

    def __init__(self, data: bytes, name: str = "BOOT.BIN") -> None:
        elf = ELFFile(io.BytesIO(data))
        loads = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"]
        if len(loads) != 1:
            raise ValueError(f"{name}: expected one PT_LOAD segment, found {len(loads)}")
        seg = loads[0]
        start = seg["p_offset"]
        super().__init__(data[start : start + seg["p_filesz"]], seg["p_vaddr"], name)
        self.entry: int = elf.header["e_entry"]
        self.sections = {
            s.name: range(s["sh_addr"], s["sh_addr"] + s["sh_size"])
            for s in elf.iter_sections()
            if s["sh_addr"]
        }
        self.text = self.sections[".text"]

    @classmethod
    def from_path(cls, path: str | Path) -> Eboot:
        path = Path(path)
        return cls(path.read_bytes(), path.name)
