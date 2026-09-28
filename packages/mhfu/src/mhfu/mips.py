"""MIPS as the PSP's Allegrex CPU runs it, decoded by rabbitizer."""

from __future__ import annotations

import struct
from collections.abc import Iterator

import rabbitizer
from rabbitizer import Instruction

from .memory import Memory

ALLEGREX = rabbitizer.InstrCategory.R4000ALLEGREX


def decode(word: int, va: int) -> Instruction:
    return Instruction(word, vram=va, category=ALLEGREX)


def instructions(mem: Memory, start: int, stop: int) -> Iterator[Instruction]:
    """The instructions from `start` up to `stop`, one per word."""
    for i, (word,) in enumerate(struct.iter_unpack("<I", mem.read(start, stop - start))):
        yield decode(word, start + 4 * i)
