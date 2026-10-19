# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A big monster's AI script: disassembled (`Scripts`, `decode`), lifted into a tree
(`lift`, `ir`), encoded back (`asm`) and written as source text (`source`).

    from mhfu.em import script
    sc = script.Scripts(game, 75)         # an em overlay and the species table in game_task

The opcode table is `opcodes.OPCODES`, the one every part reads.
"""

from . import opcodes
from .disasm import (
    SCAN_LIMIT,
    SENTINEL,
    VARIANTS,
    BadScript,
    Header,
    Insn,
    Listing,
    Problem,
    Reader,
    Routine,
    Scripts,
    Source,
    Stats,
    decode,
    disassemble,
    fetch,
    length,
    markers,
    merge,
    op_at,
)
from .opcodes import (
    BLOCKS,
    CLOSE,
    END_KINDS,
    OPCODES,
    SLOTS,
    Flow,
    Op,
    Ref,
    Shape,
    Slot,
    slot,
)

__all__ = [
    "BLOCKS",
    "CLOSE",
    "END_KINDS",
    "OPCODES",
    "SCAN_LIMIT",
    "SENTINEL",
    "SLOTS",
    "VARIANTS",
    "BadScript",
    "Flow",
    "Header",
    "Insn",
    "Listing",
    "Op",
    "Problem",
    "Reader",
    "Ref",
    "Routine",
    "Scripts",
    "Shape",
    "Slot",
    "Source",
    "Stats",
    "decode",
    "disassemble",
    "fetch",
    "length",
    "markers",
    "merge",
    "op_at",
    "opcodes",
    "slot",
]
