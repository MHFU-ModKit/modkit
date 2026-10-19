# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The game's block scan, as it really runs: `SCRIPT_SKIP`'s steps and the guard around them.

    walk.scan(read, pc)        # the markers of the block at pc, how far apart, why it stopped

`disasm.length` is the length of an instruction, the sweep over bytes that decode cleanly. The
game steps by its own table, which differs in two places: a `switch_enemy_var` opener steps one
byte short, onto its first case value, and an opcode the table has no case for steps 0, so the
scan never moves on. The scan also stops at SENTINEL and after GUARD steps between two markers.
"""

from __future__ import annotations

from .disasm import Reader, Scan, length, sweep
from .opcodes import OPCODES

__all__ = ["GUARD", "SHORT", "Scan", "scan", "step"]

GUARD = 1000
"""Steps between two markers after which the game gives up the scan."""
SHORT = 0x94
"""The opcode whose opener the game steps onto its first case value."""
_SHORT_BY = 2
"""Bytes the game steps past the opener's own length: the marker without its case value."""


def step(read: Reader, pc: int) -> int:
    """Bytes the game steps from the instruction at `pc`: `disasm.length` apart from the two
    differences above; the first case marker of a switch is stepped on its own."""
    if read(pc) not in OPCODES:
        return 0
    n = length(read, pc)
    return n + _SHORT_BY if read(pc) == SHORT and read(pc + 1) == 0 else n


def scan(read: Reader, pc: int) -> Scan:
    """The markers of the block the instruction at `pc` opens or sits in, as the game finds
    them."""
    return sweep(read, pc, step, GUARD)
