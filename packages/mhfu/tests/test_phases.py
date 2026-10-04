# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct

from mhfu import addresses as a
from mhfu import files
from mhfu.em import phases
from mhfu.em.moveset import Moveset
from mhfu.memory import Image
from mhfu.mips import Code
from modkit_testing import mips as asm

BASE = 0x0010_0000


def code(*words):
    blob = struct.pack(f"<{len(words)}I", *words)
    return Code(Image(blob, BASE), range(BASE, BASE + 4 * len(words)))


HANDLER = code(
    asm.addiu("sp", "sp", -0x10),
    asm.li("v0", 1),
    asm.sb("v0", a.ENTITY.PHASE, "a0"),  # phase 0 ends
    asm.li("v1", 150),
    asm.sw("v1", a.ENTITY.ACTION_BUDGET, "a0"),
    asm.beq("zero", "zero", BASE + 32, BASE + 20),  # b +2
    asm.sw("zero", a.ENTITY.ACTION_BUDGET, "a0"),  # the delay slot stores 0
    asm.li("v1", 30),
    asm.sw("v1", a.ENTITY.ACTION_BUDGET, "a0"),  # a later phase's seed
    asm.lui("at", 0x4270),  # 60.0
    asm.jal(a.CLIP_CURSOR_REACHED),
    asm.mtc1("at", 12),  # f12 in the delay slot
    asm.lhu("v0", a.ENTITY.CLIP_FLAGS, "a0"),
    asm.lw("v0", a.ENTITY.ACTION_BUDGET, "a0"),
    asm.RET,
    asm.NOP,
)


def test_gates():
    g = phases.gates(HANDLER, BASE)
    assert (g.reached, g.crosses, g.clip_done, g.budget) == ((60.0,), (), 1, 1)
    assert g.phase_stores == (BASE + 8,) and g.ends_on == "clip+cursor"
    assert phases.Gates((), (), 0, 2, ()).ends_on == "budget"
    assert phases.Gates((), (None,), 0, 0, ()).ends_on == "cursor"


def test_budget_seeds():
    assert phases.budget_seeds(HANDLER, BASE) == (0, 150)


def test_game(game):
    ends = {}
    for species in files.EM_SPECIES:
        ms = Moveset(game.em(species))
        for pair in ms.pairs.values():
            if pair.handler is not None:
                g = phases.gates(ms.code, pair.handler)
                ends[g.ends_on] = ends.get(g.ends_on, 0) + 1
    assert ends["clip+cursor"] > ends["budget"] > 0
    ms = Moveset(game.em(75))
    assert phases.budget_seeds(ms.code, ms.pairs[2, 16].handler) == (150,)
    assert phases.budget_seeds(ms.code, ms.pairs[2, 24].handler) == ()
