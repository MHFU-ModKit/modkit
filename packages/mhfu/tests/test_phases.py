import struct

from mhfu import addresses as a
from mhfu import files
from mhfu.em import phases
from mhfu.em.moveset import Moveset
from mhfu.memory import Image
from mhfu.mips import Code

BASE = 0x0010_0000
AT, V0, V1, A0, RA = 1, 2, 3, 4, 31
NOP = 0


def imm(op, rt, rs, v):
    return op << 26 | rs << 21 | rt << 16 | v & 0xFFFF


def code(*words):
    blob = struct.pack(f"<{len(words)}I", *words)
    return Code(Image(blob, BASE), range(BASE, BASE + 4 * len(words)))


HANDLER = code(
    imm(0x09, 29, 29, -0x10),  # addiu sp, sp, -16
    imm(0x09, V0, 0, 1),
    imm(0x28, V0, A0, a.ENTITY.PHASE),  # sb v0, PHASE(a0): phase 0 ends
    imm(0x09, V1, 0, 150),
    imm(0x2B, V1, A0, a.ENTITY.ACTION_BUDGET),  # sw v1, ACTION_BUDGET(a0)
    imm(0x04, 0, 0, 2),  # b +2
    imm(0x2B, 0, A0, a.ENTITY.ACTION_BUDGET),  # the delay slot stores 0
    imm(0x09, V1, 0, 30),
    imm(0x2B, V1, A0, a.ENTITY.ACTION_BUDGET),  # a later phase's seed
    imm(0x0F, AT, 0, 0x4270),  # lui at, 60.0
    3 << 26 | a.CLIP_CURSOR_REACHED >> 2 & 0x03FF_FFFF,
    0x11 << 26 | 4 << 21 | AT << 16 | 12 << 11,  # mtc1 at, f12 in the delay slot
    imm(0x25, V0, A0, a.ENTITY.CLIP_FLAGS),  # lhu v0, CLIP_FLAGS(a0)
    imm(0x23, V0, A0, a.ENTITY.ACTION_BUDGET),  # lw v0, ACTION_BUDGET(a0)
    RA << 21 | 0x08,
    NOP,
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
