# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A big monster's AI script: disassembled (`Scripts`, `decode`), lifted into a tree
(`lift`, `ir`), edited (`edit`, vetted by `check` and `walk`), encoded back (`asm`), written as
source text (`source`) and read back from it (`parse`).

    from mhfu.em import script
    sc = script.Scripts(game, 75)         # an em overlay and the species table in game_task
    prog = script.lift.lift(sc, 75)       # one species row as a Program
    image = script.asm.assemble(prog)     # its bytes, identical to the game's
    script.source.render(prog)            # the source lines
    script.parse.parse(text, like=prog)   # an edited text as a program again

The opcode table is `opcodes.OPCODES`, the one every part reads.
"""

from . import asm, check, edit, ir, lift, opcodes, parse, source, walk
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
    Scan,
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
    sweep,
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
    "Scan",
    "Scripts",
    "Shape",
    "Slot",
    "Source",
    "Stats",
    "asm",
    "check",
    "decode",
    "disassemble",
    "edit",
    "fetch",
    "ir",
    "length",
    "lift",
    "markers",
    "merge",
    "op_at",
    "opcodes",
    "parse",
    "slot",
    "source",
    "sweep",
    "walk",
]
