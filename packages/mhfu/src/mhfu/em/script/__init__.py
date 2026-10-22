# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A big monster's AI script: disassembled (`Scripts`, `decode`), lifted into a tree
(`lift`, `ir`), edited (`edit`, `editset`, vetted by `check`, `walk` and `budget`), encoded back
(`asm`), written as source text (`source`), read back from it (`parse`), worded for an editor
(`catalogue`) and drawn as a graph (`graph`), kept as a species' edit file (`brainfile`) and
built from it (`build`), pushed into the running game (`inject`), put on the memory stick for
the framework to load (`stick`) and surveyed for the script variables a modder may use (`vars`).

    from mhfu.em import script
    sc = script.Scripts(game, 75)         # an em overlay and the species table in game_task
    prog = script.lift.lift(sc, 75)       # one species row as a Program
    image = script.asm.assemble(prog)     # its bytes, identical to the game's
    script.source.render(prog)            # the source lines
    script.parse.parse(text, like=prog)   # an edited text as a program again
    script.Budget(prog).worst             # the most instructions a run dispatches, of 999

The opcode table is `opcodes.OPCODES`, the one every part reads.
"""

from . import (
    asm,
    brainfile,
    budget,
    build,
    catalogue,
    check,
    edit,
    editset,
    graph,
    inject,
    ir,
    lift,
    opcodes,
    parse,
    source,
    stick,
    vars,
    walk,
)
from .budget import Budget
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
    "Budget",
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
    "brainfile",
    "budget",
    "build",
    "catalogue",
    "check",
    "decode",
    "disassemble",
    "edit",
    "editset",
    "fetch",
    "graph",
    "inject",
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
    "stick",
    "sweep",
    "vars",
    "walk",
]
