# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct

from mhfu import addresses as a
from mhfu import files
from mhfu.em import abi, events
from mhfu.em.events import Entry, Gate, Spawn
from mhfu.memory import Image
from mhfu.mips import Code
from modkit_testing import mips as asm

BASE = 0x0010_0000
INPUT = 0x3E8
SPAWNER = a.ATTACK_SPAWNERS + 0x100
HUNDRED, TWENTY = 0x42C8, 0x41A0  # the high halves of 100.0 and 20.0
PROLOGUE = asm.addiu("sp", "sp", -0x10)


def va(i):
    return BASE + 4 * i


def code(*words):
    return Code(Image(struct.pack(f"<{len(words)}I", *words), BASE), range(BASE, va(len(words))))


def link(entry, target, at):
    """The compiler's per-entry test: a0 = the entry's input, taken when v1 holds it."""
    return [asm.li("a0", INPUT + entry), asm.beq("v1", "a0", target, at + 4), asm.NOP]


def branch(frame_hi, attack_id):
    """A cursor test at a literal frame, then an attack spawn: `crosses` takes the frame in f12."""
    return [
        asm.lui("at", frame_hi),
        asm.mtc1("at", 12),
        asm.jal(a.CLIP_CURSOR_CROSSES),
        asm.NOP,
        asm.li("a2", attack_id),
        asm.jal(SPAWNER),
        asm.NOP,
        asm.RET,
        asm.NOP,
    ]


# a table of entries 3 and 4: links first, a branch each; a third spawn site gives the spawner
# the ids a moveset's has (three literals, the lowest at most 2)
TABLE = [
    PROLOGUE,
    *link(3, va(9), va(1)),
    *link(4, va(18), va(4)),
    asm.RET,
    asm.NOP,
    *branch(HUNDRED, 5),  # 9
    *branch(TWENTY, 1),  # 18
    *branch(TWENTY, 2),  # 27
]


def table_code():
    return code(*TABLE)


def test_links():
    c = table_code()
    assert events.links(c, c.function(BASE)) == {3: va(1), 4: va(4)}


def test_table():
    got = events.table(table_code(), BASE, 75)
    assert got == {
        3: Entry(3, (Gate("crosses", 100.0),), (Spawn(5, 100.0),)),
        4: Entry(4, (Gate("crosses", 20.0),), (Spawn(1, 20.0),)),
    }
    assert got[3].spawn_frames == [100.0]
    assert events.table(table_code(), None, 75) == {}


def test_a_species_switch_names_the_table():
    # the slot function tests the species and calls one table: 76 the second, any other the first
    first, second = 16, 32
    switch = [
        PROLOGUE,
        asm.lbu("v0", a.ENTITY.SPECIES, "a0"),
        asm.li("v1", 76),
        asm.bne("v0", "v1", va(9), va(3)),
        asm.NOP,
        asm.jal(va(second)),  # 5
        asm.NOP,
        asm.RET,
        asm.NOP,
        asm.jal(va(first)),  # 9
        asm.NOP,
        asm.RET,
        asm.NOP,
    ]

    def table(entry, start, frame_hi, attack_id):
        links = [PROLOGUE, *link(entry, va(start + 6), va(start + 1)), asm.RET, asm.NOP]
        return [*links, *branch(frame_hi, attack_id)]

    one = table(3, first, HUNDRED, 5)
    words = [
        *switch,
        *[asm.NOP] * (first - len(switch)),
        *one,
        *[asm.NOP] * (second - first - len(one)),
        *table(4, second, TWENTY, 1),
    ]
    c = code(*words)
    assert events.branches(c, BASE, 75).keys() == {3}
    assert events.branches(c, BASE, 76).keys() == {4}


def test_no_links_is_an_empty_table():
    c = code(PROLOGUE, asm.RET, asm.NOP)
    assert events.table(c, BASE, 75) == {}


def test_document_reads_back():
    entries = {
        3: Entry(3, (Gate("crosses", 100.0), Gate("reached", None)), (Spawn(5, 100.0),)),
        4: Entry(4, (), (Spawn(None, None),)),
    }
    doc = events.document(entries, BASE)
    assert doc["present"] and doc["function"] == "0x00100000"  # noaddr: a synthetic function
    assert events.read(doc) == entries
    assert not events.document({}, None)["present"]
    assert events.read(None) == {} and events.read({}) == {}


def test_em75(game):
    engine = abi.Engine.load(game)
    fn = engine.owners[75].vtable.slots[events.SLOT]
    got = events.table(engine.codes[75], fn, 75)
    assert len(got) == 71 and sum(bool(e.attacks) for e in got.values()) == 19
    assert got[46].attacks == (Spawn(18, 120.0), Spawn(8, 120.0), Spawn(7, 120.0))
    assert got[2].attacks == (Spawn(11, 16.0),)
    assert Gate("reached", 108.0) in got[51].gates
    assert got[51].spawn_frames == [122.0, 150.0, 238.0, 258.0, 284.0]


def test_every_species(game):
    engine = abi.Engine.load(game)
    found = {}
    for species, owner in engine.owners.items():
        fn = owner.vtable.slots[events.SLOT]
        found[species] = events.table(engine.codes[species], fn, species)
    # the other species lay their entry tests out some other way, and say nothing
    assert {s for s, t in found.items() if not t} == {2, 20, 21, 33, 54, 58, 82}
    assert set(found) <= set(files.EM_SPECIES)
