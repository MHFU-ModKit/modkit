# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct

from mhfu import addresses as a
from mhfu import files
from mhfu.em import effects
from mhfu.memory import Image
from mhfu.mips import Code
from modkit_testing import mips as asm

BASE = 0x0010_0000
PROLOGUE = asm.addiu("sp", "sp", -0x10)


def build(*words, base=BASE):
    blob = struct.pack(f"<{len(words)}I", *words)
    return Code(Image(blob, base), range(base, base + 4 * len(words)))


def va(i, base=BASE):
    return base + 4 * i


def test_spawns():
    code = build(
        PROLOGUE,  # 0 a handler
        asm.li("a1", 12),
        asm.addu("a2", "a1", "zero"),
        asm.jal(va(20)),  # 3 through the wrapper: id 12 at bone 12
        asm.NOP,
        asm.li("a1", 90),  # frame
        asm.li("a3", 60),
        asm.jal(a.EFFECT_SPAWN_FRAMED),  # 7
        asm.addu("t0", "zero", "zero"),  # bone 0, in the delay slot
        asm.lw("a1", 0, "s0"),
        asm.jal(a.EFFECT_SPAWN_AT),  # 10 a computed id
        asm.li("a2", 3),
        asm.RET,
        asm.NOP,
        *[asm.NOP] * 6,
        PROLOGUE,  # 20 the wrapper
        asm.jal(a.EFFECT_SPAWN),
        asm.NOP,
        asm.RET,
        asm.NOP,
    )
    assert effects.wrappers(code) == [va(20)]
    local, framed, at, inner = effects.spawns(code)
    assert (local.via, local.id, local.bone, local.fn) == ("local", 12, 12, va(0))
    assert (framed.via, framed.id, framed.bone, framed.frame) == ("framed", 60, 0, 90)
    assert (at.via, at.id, at.bone, at.frame) == ("positional", None, 3, None)
    assert (inner.via, inner.fn) == ("biased", va(20))
    census = effects.census({7: [local, framed, at], 75: [local]})
    assert census == {12: {7: 1, 75: 1}, 60: {7: 1}}


def test_long_caller_is_no_wrapper():
    code = build(
        PROLOGUE, asm.jal(a.EFFECT_SPAWN), *[asm.NOP] * effects.WRAPPER_SIZE, asm.RET, asm.NOP
    )
    assert effects.wrappers(code) == []


def test_bias():
    def at(i):
        return va(i, a.EFFECT_SPAWN)

    code = build(
        PROLOGUE,
        asm.addu("s0", "a1", "zero"),
        asm.li("v0", 80),
        asm.beq("v1", "v0", at(8), at(3), likely=True),  # 3
        asm.addiu("s0", "s0", 30),
        asm.li("v0", 13),
        asm.beq("v1", "v0", at(9), at(6)),  # 6
        asm.NOP,
        asm.NOP,
        asm.addiu("s0", "s0", 100),  # 9
        asm.RET,
        asm.NOP,
        base=a.EFFECT_SPAWN,
    )
    assert effects.bias(code) == {80: 30, 13: 100}


def test_game(game):
    task = game.overlay(files.GAME_TASK)
    bias = effects.bias(Code(task, task.text))
    assert bias and set(bias.values()) <= {30, 100} and not set(bias) & set(files.EM_SPECIES)
    tigrex = game.em(75)
    code = Code(tigrex, tigrex.text)
    sites = effects.spawns(code)
    assert len(effects.wrappers(code)) == 1
    assert sum(s.id is not None for s in sites) > 0.9 * len(sites)
    assert {s.via for s in sites} >= {"local", "framed"}
