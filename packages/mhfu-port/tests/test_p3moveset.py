# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import csv
import io
import struct
from collections.abc import Callable
from pathlib import Path

import pytest
from mhfu.memory import Image
from mhfu.mips import Code
from mhfu.overlay import Overlay
from mhfu_port import p3moveset as p3
from mhfu_port.cli import main
from mhfu_port.cli.p3moveset import COLUMNS
from mhfu_port.p3moveset import Attack, Moveset
from mhp_formats import p3rd
from modkit_testing import mips as asm

LOAD = 0x0010_0000
TEXT = LOAD + 0x40
Item = int | str | Callable[[dict[str, int], int], int]
"""A word, a label, or a word that needs the labels and its own address."""
PROLOGUE = asm.addiu("sp", "sp", -0x10)
EPILOGUE = (asm.RET, asm.addiu("sp", "sp", 0x10))
RECORD = next(iter(p3.RECORDS))
REACTION = p3.REACTIONS[0]
LEAVE = next(iter(p3.END))


def beq(rs, rt, label):
    return lambda labels, at: asm.beq(rs, rt, labels[label], at)


def bne(rs, rt, label):
    return lambda labels, at: asm.bne(rs, rt, labels[label], at)


def jal(label):
    return lambda labels, at: asm.jal(labels[label])


def j(label):
    return lambda labels, at: asm.j(labels[label])


def la(reg, label):
    return (
        lambda labels, at: asm.lui(reg, asm.hi(labels[label])),
        lambda labels, at: asm.addiu(reg, reg, asm.lo(labels[label])),
    )


def addr(label):
    return lambda labels, at: labels[label]


def switch(offset, count, table, default):
    """The tick's and a dispatcher's jump through `table` on the byte at `offset`."""
    return (
        asm.lbu("v0", offset, "a0"),
        asm.sltiu("at", "v0", count),
        beq("at", "zero", default),
        asm.NOP,
        *la("a1", table),
        asm.sll("v1", "v0", 2),
        asm.addu("v1", "v1", "a1"),
        asm.lw("v1", 0, "v1"),
        asm.jr("v1"),
        asm.NOP,
    )


def handler(label, *body):
    return (label, PROLOGUE, *body, *EPILOGUE)


def play(clip):
    return asm.li("a1", clip), asm.jal(p3.EXECUTOR), asm.NOP


def inline(label, *body):
    return (label, *body, j("d1_end"), asm.NOP)


TICK = (
    "tick",
    *switch(p3.MAIN, 2, "mains", "tick_end"),
    "tick_end",
    asm.RET,
    asm.NOP,
    "m0",
    jal("d0"),
    asm.NOP,
    j("tick_end"),
    asm.NOP,
    "m1",
    jal("d1"),
    asm.NOP,
    j("tick_end"),
    asm.NOP,
)
D0 = (
    "d0",
    *switch(p3.SUB, 3, "d0_subs", "d0_end"),
    "d0_end",
    asm.RET,
    asm.NOP,
    "d0_0",
    j("h00"),
    asm.NOP,
    "d0_1",
    j("h01"),
    asm.NOP,
    "d0_2",
    j("h02"),
    asm.NOP,
)
HANDLERS = (
    *handler("h00", *play(5), asm.li("a1", 1), asm.li("a2", 2), asm.jal(p3.ACT_SET), asm.NOP),
    *handler(
        "h01",
        *play(6),
        asm.li("a1", 3),
        asm.li("a2", 11),
        asm.li("a3", 1),
        asm.jal(p3.ATTACK),
        asm.NOP,
    ),
    *handler("h02", asm.lbu("a1", 0x18E, "a0"), asm.jal(p3.EXECUTOR), asm.NOP),
)
D1 = (
    "d1",
    *switch(p3.SUB, 5, "d1_subs", "d1_end"),
    "d1_end",
    asm.RET,
    asm.NOP,
    *inline("d1_0", *play(9)),
    *inline("d1_1", *play(10)),
    *inline("d1_2", *la("a1", "rec"), asm.jal(RECORD), asm.NOP),
    *inline("d1_3", *la("a1", "steps"), asm.jal(REACTION), asm.NOP),
    *inline("d1_4", asm.jal(LEAVE), asm.NOP),
)
TRANSLATOR = (
    "xlate",
    asm.lbu("v1", 0, "a1"),
    bne("v1", "zero", "x1"),
    asm.NOP,
    asm.lbu("v0", 0, "a2"),  # main 0, sub 2: sub 1 when the entity says so
    asm.li("at", 2),
    bne("v0", "at", "x_end"),
    asm.NOP,
    asm.lbu("a0", 0x585, "a0"),
    beq("a0", "zero", "x_end"),
    asm.NOP,
    asm.li("at", 1),
    asm.sb("at", 0, "a2"),
    asm.RET,
    asm.NOP,
    "x1",
    asm.li("v0", 1),
    bne("v1", "v0", "x_end"),
    asm.NOP,
    asm.lbu("v0", 0, "a2"),  # main 1, sub 7: sub 0
    asm.li("at", 7),
    bne("v0", "at", "x_end"),
    asm.NOP,
    asm.sb("zero", 0, "a2"),
    "x_end",
    asm.RET,
    asm.NOP,
)
DATA = (
    "vtable",
    addr("tick"),
    *[0] * 32,
    addr("tick"),  # +0x84
    addr("xlate"),  # +0x88
    "mains",
    addr("m0"),
    addr("m1"),
    "d0_subs",
    addr("d0_0"),
    addr("d0_1"),
    addr("d0_2"),
    "d1_subs",
    *(addr(f"d1_{i}") for i in range(5)),
    "rec",
    0,
    0,
    0,
    0,
    12,  # a descriptor at +0x10: clip 12
    0,
    0,
    0,
    0,
    "steps",
    0,
    addr("clip13"),
    1,
    addr("clip14"),
    0xFF,
    0,
    "clip13",
    13,
    "clip14",
    14,
)


def assemble(text: tuple[Item, ...], data: tuple[Item, ...]) -> tuple[bytes, bytes, dict[str, int]]:
    """The words of text and data, and the address of each label."""
    labels: dict[str, int] = {}
    n = sum(not isinstance(i, str) for i in text)
    for start, items in ((TEXT, text), (TEXT + 4 * n, data)):
        at = start
        for item in items:
            if isinstance(item, str):
                labels[item] = at
            else:
                at += 4
    out = []
    for start, items in ((TEXT, text), (TEXT + 4 * n, data)):
        words = []
        for item in (i for i in items if not isinstance(i, str)):
            words.append(item(labels, start + 4 * len(words)) if callable(item) else item)
        out.append(struct.pack(f"<{len(words)}I", *words))
    return out[0], out[1], labels


def overlay(text: tuple[Item, ...], data: tuple[Item, ...]) -> tuple[Overlay, dict[str, int]]:
    code, tables, labels = assemble(text, data)
    header = struct.pack("<4sIIIIIII", b"MWo3", 0, LOAD, len(code), len(tables), 0, 0, 0)
    return Overlay(header.ljust(0x40, b"\0") + code + tables, "em000m0.ovl"), labels


@pytest.fixture(scope="module")
def species() -> tuple[Moveset, dict[str, int]]:
    """Two mains: main 0 tail-calls a handler per sub, main 1 runs its subs inline."""
    ovl, labels = overlay(TICK + D0 + HANDLERS + D1 + TRANSLATOR, DATA)
    return Moveset(ovl), labels


def test_dispatch(species):
    ms, at = species
    assert ms.main_count == 2 and ms.vtable == at["vtable"] and ms.tick_entry == at["tick"]
    assert sorted(ms.pairs) == [
        (0, 0),
        (0, 1),
        (0, 2),
        (1, 0),
        (1, 1),
        (1, 2),
        (1, 3),
        (1, 4),
        (1, 7),
    ]
    assert [ms.pairs[0, s].handler for s in range(3)] == [at["h00"], at["h01"], at["h02"]]
    assert ms.pairs[1, 2].handler == at["d1_2"]  # an inline case


def test_clips(species):
    ms, _ = species
    got = {k: (p.clips, p.kind, p.computed) for k, p in ms.pairs.items()}
    assert got[0, 0] == ((5,), "move", False)
    assert got[0, 1] == ((6,), "move", False)
    assert got[0, 2] == ((), "move", True)  # an id from a load
    assert got[1, 0] == ((9,), "move", False)
    assert got[1, 2][0] == (12,)  # a descriptor record
    assert got[1, 3] == ((13, 14), "reaction", False)  # a step list


def test_what_a_pair_does(species):
    ms, _ = species
    assert ms.pairs[0, 0].next == ((1, 2),)
    assert ms.pairs[0, 1].attacks == (Attack(3, 11, 1),)
    assert ms.pairs[1, 4].next == tuple(sorted(p3.END[LEAVE]))
    assert ms.pairs[1, 4].clips == ()


def test_sites(species):
    ms, _ = species
    assert len(ms.sites) == 7 and not ms.orphans
    assert ms.pairs[1, 3].sites <= ms.sites and len(ms.pairs[1, 3].sites) == 1


def test_translator(species):
    ms, _ = species
    assert ms.translator == {(0, 2): frozenset([(0, 1)]), (1, 7): frozenset([(1, 0)])}
    alias, target = ms.pairs[1, 7], ms.pairs[1, 0]
    assert (alias.handler, alias.clips, alias.next) == (target.handler, target.clips, target.next)
    assert ms.pairs[0, 2].computed  # a conditional rewrite leaves the pair its own row


BASE = 0x0010_0000


def va(i):
    return BASE + 4 * i


def translator(*words, data=()):
    """The translator whose code is `words` at BASE, followed by the data words."""
    blob = struct.pack(f"<{len(words) + len(data)}I", *words, *data)
    return p3._Translator(Code(Image(blob, BASE), range(BASE, va(len(words)))), BASE)


def test_translator_learns_equality():
    run = translator(
        asm.lbu("v1", 0, "a1"),
        asm.lbu("a0", 0x585, "a0"),  # unknown
        asm.bne("a0", "v1", va(6), va(2)),
        asm.NOP,
        asm.sb("a0", 0, "a2"),  # sub = main where the two compared equal
        asm.NOP,
        asm.RET,
        asm.NOP,
    )
    assert run(3, 9) == {(3, 9), (3, 3)}


def test_translator_through_a_jump_table():
    table = va(17)
    run = translator(
        asm.lbu("t0", 0, "a2"),
        asm.addiu("t0", "t0", -4),
        asm.sltiu("at", "t0", 2),
        asm.beq("at", "zero", va(15), va(3)),
        asm.NOP,
        asm.lui("t1", asm.hi(table)),
        asm.addiu("t1", "t1", asm.lo(table)),
        asm.sll("t2", "t0", 2),
        asm.addu("t2", "t2", "t1"),
        asm.lw("t2", 0, "t2"),
        asm.jr("t2"),
        asm.NOP,
        asm.li("t3", 8),  # 12: the case
        asm.sb("t3", 0, "a1"),
        asm.NOP,
        asm.RET,
        asm.NOP,
        data=(va(12), va(12)),
    )
    assert run(0, 4) == {(8, 4)} and run(0, 5) == {(8, 5)}
    assert run(0, 6) == {(0, 6)} and run(0, 3) == {(0, 3)}


def test_translator_forks_on_a_call():
    run = translator(
        PROLOGUE,
        asm.addu("s0", "a2", "zero"),  # a call clobbers a2
        asm.jal(va(12)),
        asm.NOP,
        asm.beq("v0", "zero", va(9), va(4)),
        asm.NOP,
        asm.li("at", 5),
        asm.sb("at", 0, "s0"),
        asm.NOP,
        asm.RET,  # 9
        asm.addiu("sp", "sp", 0x10),
        asm.NOP,
        asm.RET,  # 12: the callee
        asm.NOP,
    )
    assert run(2, 0) == {(2, 0), (2, 5)}


@pytest.fixture
def written(tmp_path: Path, species) -> Path:
    ovl, _ = overlay(TICK + D0 + HANDLERS + D1 + TRANSLATOR, DATA)
    path = tmp_path / "em000m0.ovl"
    path.write_bytes(ovl.file)
    return path


def test_cli(written, tmp_path, capsys):
    out = tmp_path / "pairs.csv"
    assert main(["p3moveset", "--overlay", str(written), "-o", str(out)]) == 0
    rows = list(csv.DictReader(io.StringIO(out.read_text())))
    assert tuple(rows[0]) == COLUMNS and len(rows) == 9
    by = {(r["main"], r["sub"]): r for r in rows}
    assert by["1", "3"]["clips"] == "13 14" and by["1", "3"]["kind"] == "reaction"
    assert by["0", "1"]["attacks"] == "3:11:1" and by["0", "0"]["next"] == "1:2"
    assert by["0", "2"]["computed"] == "yes" and by["0", "2"]["rewrites"] == "0:1"
    assert "9 pairs" in capsys.readouterr().err


def test_cli_needs_one_overlay(written, capsys):
    assert main(["p3moveset"]) == 1
    assert main(["p3moveset", "23", "--overlay", str(written)]) == 1
    assert "monster id or --overlay" in capsys.readouterr().err


def test_cli_not_an_em_overlay(tmp_path, capsys):
    ovl, _ = overlay((asm.RET, asm.NOP), ())
    path = tmp_path / "x.ovl"
    path.write_bytes(ovl.file)
    assert main(["p3moveset", "--overlay", str(path)]) == 1
    assert "no action tick" in capsys.readouterr().err


BRUTE, BRUTE_PACK = 5246, 5250
ZINOGRE, ZINOGRE_PACK = 5337, 5341


def pack_ids(path: Path) -> set[int]:
    anim = p3rd.Anim.from_bytes(path.read_bytes())
    return {si * 100 + slot for si, s in enumerate(anim.streams) for slot, c in enumerate(s) if c}


@pytest.fixture(scope="module")
def brute(mhp3rd_data: Path) -> Moveset:
    return Moveset(Overlay.from_path(mhp3rd_data / f"file_{BRUTE:05d}.bin"))


@pytest.fixture(scope="module")
def zinogre(mhp3rd_data: Path) -> Moveset:
    return Moveset(Overlay.from_path(mhp3rd_data / f"file_{ZINOGRE:05d}.bin"))


def test_brute(brute, mhp3rd_data):
    pairs = brute.pairs.values()
    ids = {c for p in pairs for c in p.clips}
    assert len(brute.pairs) == 131 and sum(bool(p.clips) for p in pairs) == 128
    assert not any(p.computed for p in pairs) and len(ids) == 75
    assert ids <= pack_ids(mhp3rd_data / f"file_{BRUTE_PACK:05d}.bin")
    assert len(brute.sites) == 145 and not brute.orphans
    assert brute.translator == {
        (1, 0): frozenset([(1, 1)]),
        (2, 0): frozenset([(2, 1)]),
        (2, 25): frozenset([(2, 3)]),
    }


def test_zinogre(zinogre, mhp3rd_data):
    pairs = zinogre.pairs.values()
    ids = {c for p in pairs for c in p.clips}
    assert len(zinogre.pairs) == 301 and sum(bool(p.clips) for p in pairs) == 294
    assert [k for k, p in zinogre.pairs.items() if p.computed] == [(7, 90), (7, 91)]
    assert len(zinogre.sites) == 206 and len(zinogre.orphans) == 10
    assert len(ids - pack_ids(mhp3rd_data / f"file_{ZINOGRE_PACK:05d}.bin")) == 2
    assert {m for m, _ in zinogre.translator} == {7}  # the sources are main 7, not 10


def test_zinogre_alias(zinogre):
    alias, target = zinogre.pairs[7, 203], zinogre.pairs[7, 0]
    assert zinogre.translator[7, 203] == {(7, 0)}
    assert (alias.handler, alias.clips) == (target.handler, target.clips)
    assert target.clips == (221, 222, 223)  # a variant's row of a constant id table
    assert zinogre.pairs[7, 1].clips == (218, 219, 220)


def test_cli_by_monster_id(mhp3rd_data, capsys):
    assert main(["p3moveset", "23", "--p3rd-data", str(mhp3rd_data)]) == 0
    out, err = capsys.readouterr()
    assert len(list(csv.reader(io.StringIO(out)))) == 1 + 131
    assert "145 of 145 play calls reached" in err
    assert main(["p3moveset", "23", "--slot", "9", "--p3rd-data", str(mhp3rd_data)]) == 1
