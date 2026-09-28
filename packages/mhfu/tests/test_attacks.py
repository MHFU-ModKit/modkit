import struct

from mhfu import addresses as a
from mhfu import files, hitbox
from mhfu.em import attacks
from mhfu.memory import Image
from mhfu.mips import Code
from modkit_testing import mips as asm

BASE = 0x0010_0000
MOVESET = a.ATTACK_SPAWNERS + 0x100
SHARED = a.ATTACK_SPAWNERS + 0x200
PROLOGUE = asm.addiu("sp", "sp", -0x10)


def call(target, attack_id):
    return [asm.jal(target), asm.li("a2", attack_id)]


def build(*words):
    blob = struct.pack(f"<{len(words)}I", *words)
    return Code(Image(blob, BASE), range(BASE, BASE + 4 * len(words)))


def test_spawner():
    code = build(
        PROLOGUE,  # 0
        *call(MOVESET, 1),
        *call(MOVESET, 2),
        *call(SHARED, 34),
        asm.RET,
        asm.NOP,
        PROLOGUE,  # 9
        *call(MOVESET, 2),
        *call(MOVESET, 5),
        asm.jal(MOVESET),  # 14 a computed id
        asm.addiu("a2", "s0", 4),
        asm.j(SHARED),  # 16 a tail call
        asm.li("a2", 34),
    )
    family = attacks.family(code)
    assert list(family) == [MOVESET, SHARED]
    main = attacks.spawner(code)
    assert main == family[MOVESET] and main.ids == [1, 2, 5] and main.literal == 4
    assert [s.fn for s in main.sites] == [BASE] * 2 + [BASE + 36] * 3
    assert [s.fn for s in attacks.extras(code, main)] == [SHARED]
    assert family[SHARED].ids == [34] and not family[SHARED].moveset
    assert attacks.by_handler(code) == {BASE: [1, 2], BASE + 36: [2, 5]}


def test_fit():
    table = hitbox.AttackTable(0, 0, None, [None] * 5)
    sp = attacks.Spawner(MOVESET, [attacks.Site(0, 4, 0)])
    assert attacks.fit(sp, table) == "consistent"
    assert attacks.fit(attacks.Spawner(MOVESET, [attacks.Site(0, 5, 0)]), table) == (
        "ids_exceed_table"
    )
    assert attacks.fit(attacks.Spawner(a.TIGREX_ATTACK_SPAWN), table) == "measured"
    assert attacks.fit(sp, None) == "no_table"
    assert attacks.fit(None, table) == "no_spawner"


def test_game(game):
    for species in files.EM_SPECIES:
        ovl = game.em(species)
        sp = attacks.spawner(Code(ovl, ovl.text))
        if species in (1, 33):
            assert sp is None
            continue
        assert sp is not None and sp.fn in attacks.SPAWNERS
        assert (sp.fn == a.TIGREX_ATTACK_SPAWN) == (species == 75)
        fit = attacks.fit(sp, hitbox.primary_table(hitbox.tables(ovl)))
        assert fit in ("measured", "consistent", "ids_exceed_table")
