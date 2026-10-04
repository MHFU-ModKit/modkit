# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct

from mhfu import files
from mhfu.em import abi
from mhfu.memory import Image
from mhfu.mips import Code
from modkit_testing import mips as asm

EM, TASK, SUB, EBOOT = 0x0020_0000, 0x0030_0000, 0x0038_0000, 0x0040_0000
PROLOGUE = asm.addiu("sp", "sp", -0x10)


class Binary(Image):
    """An image with the `text` (and `sections`) the engine reads off BOOT.BIN and overlays."""

    def __init__(self, words, base, text=None, data=None):
        super().__init__(struct.pack(f"<{len(words)}I", *words), base)
        self.text = text or range(base, base + 4 * len(words))
        self.sections = {".data": data} if data else {}


def em(starts):
    """16 words of overlay text with a function at each of `starts` (word indices)."""
    words = [asm.NOP] * 16
    for i in starts:
        words[i] = PROLOGUE
        if i:
            words[i - 2] = asm.RET
    return Binary(words, EM)


def engine():
    task = Binary([asm.NOP] * 16, TASK)
    base = [TASK + 4 * (k % 16) for k in range(abi.ENTITY_SLOTS)]
    one = list(base)
    one[3], one[5] = EM, EM + 16  # overrides slots 3 and 5 into em(0, 4)
    two = list(base)
    two[3], two[7] = EM + 32, EM + 44  # slot 3 and 7 into em(8, 11)
    words = [asm.NOP] * 8 + [0, 0, *one, 0, 0, *two, 0]
    end = EBOOT + 4 * len(words)
    eboot = Binary(words, EBOOT, range(EBOOT, EBOOT + 32), range(EBOOT + 32, end))
    ems = {1: em([0, 4]), 2: em([8, 11])}
    return abi.Engine(eboot, task, Binary([asm.NOP], SUB), ems)


def test_vtables():
    e = engine()
    first, second = e.vtables
    assert first.va == EBOOT + 32 and len(first.slots) == abi.ENTITY_SLOTS
    assert second.va == first.va + abi.offset(abi.ENTITY_SLOTS)
    assert {s: o.vtable for s, o in e.owners.items()} == {1: first, 2: second}
    assert e.owners[1].score == 1.0 > e.owners[1].second
    kinds = {x.index: x.kind for x in e.interface()}
    assert (kinds[3], kinds[5], kinds[7], kinds[0]) == ("mandatory", "optional", "optional", "base")
    slot5 = e.interface()[5]
    assert slot5.overrides == (1,) and slot5.inherited == (TASK + 20,)
    assert e.zone(EM + 4) == "em" and e.zone(TASK) == "game_task" and e.zone(1) is None


def test_vptr_stores_and_slots():
    code = Binary(
        [
            PROLOGUE,
            asm.lui("v0", EBOOT >> 16),
            asm.addiu("v0", "v0", 0x20),
            asm.sw("v0", 0, "a0"),  # 3 installs a vtable
            asm.sw("v0", 4, "a0"),  # not at +0
            asm.lw("t9", 0, "a0"),
            asm.bne("a0", "zero", 8 * 4 + TASK, 6 * 4 + TASK, likely=True),  # 6 loads the vptr
            asm.lw("t9", 0, "a0"),
            asm.lw("t9", 0x88, "t9"),  # 8 reached from two paths
            asm.jalr("t9"),
            asm.NOP,
            asm.RET,
            asm.NOP,
        ],
        TASK,
    )
    c = Code(code, code.text)
    assert abi._vptr_stores(c, c.text) == [(EBOOT + 0x20, TASK + 12)]
    (call,) = [x for x in c.calls if x.site == TASK + 36]
    assert call.slot == 0x88


def test_game(game):
    e = abi.Engine.load(game)
    assert list(e.owners) == list(files.EM_SPECIES)
    assert all(o.score > o.second for o in e.owners.values())
    slots = e.interface()
    assert abi.offset(32) == 0x88 and slots[32].kind == "mandatory"  # enter_action
    factory = e.factory()
    assert all(factory[s] == s for s in files.EM_SPECIES)
    assert set(factory.values()) == set(files.EM_SPECIES)
    installed = e.classes(75)
    assert installed and all(va in e.eboot.sections[".data"] for va in installed)
    assert e.dispatches(32)
