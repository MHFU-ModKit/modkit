import struct

from mhfu import addresses as a
from mhfu import files, hitbox
from mhfu.em import attacks
from mhfu.memory import Image
from mhfu.mips import Code

BASE = 0x0010_0000
R = {"zero": 0, "a2": 6, "s0": 16, "sp": 29, "ra": 31}
MOVESET = a.ATTACK_SPAWNERS + 0x100
SHARED = a.ATTACK_SPAWNERS + 0x200


def addiu(rt, rs, v):
    return 0x09 << 26 | R[rs] << 21 | R[rt] << 16 | v & 0xFFFF


def jal(target):
    return 3 << 26 | (target >> 2) & 0x03FF_FFFF


def j(target):
    return 2 << 26 | (target >> 2) & 0x03FF_FFFF


NOP, RET = 0, R["ra"] << 21 | 0x08
PROLOGUE = addiu("sp", "sp", -0x10)


def call(target, attack_id):
    return [jal(target), addiu("a2", "zero", attack_id)]


def build(*words):
    blob = struct.pack(f"<{len(words)}I", *words)
    return Code(Image(blob, BASE), range(BASE, BASE + 4 * len(words)))


def test_spawner():
    code = build(
        PROLOGUE,  # 0
        *call(MOVESET, 1),
        *call(MOVESET, 2),
        *call(SHARED, 34),
        RET,
        NOP,
        PROLOGUE,  # 9
        *call(MOVESET, 2),
        *call(MOVESET, 5),
        jal(MOVESET),  # 14 a computed id
        addiu("a2", "s0", 4),
        j(SHARED),  # 16 a tail call
        addiu("a2", "zero", 34),
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
