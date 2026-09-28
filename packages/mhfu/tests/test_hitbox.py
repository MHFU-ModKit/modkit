import struct

from mhfu import addresses as a
from mhfu import files
from mhfu import hitbox as hb
from mhfu import hitzone as hz
from mhfu.cli import main
from mhfu.mips import Code, Gpr
from mhfu.overlay import TEXT, Overlay

LOAD = 0x0010_0000
TEXT_SIZE = 0x28
DATA = LOAD + TEXT + TEXT_SIZE
A3, T0, T1, V0, S0 = 7, 8, 9, 2, 16


def lui(reg: int, hi: int) -> int:
    return 0x3C00_0000 | reg << 16 | hi


def addiu(reg: int, lo: int) -> int:
    return 0x2400_0000 | reg << 21 | reg << 16 | lo


def jal(target: int) -> int:
    return 0x0C00_0000 | target >> 2 & 0x03FF_FFFF


def sw(rt: int, offset: int, base: int) -> int:
    return 0xAC00_0000 | base << 21 | rt << 16 | offset


def lw(rt: int, offset: int, base: int) -> int:
    return 0x8C00_0000 | base << 21 | rt << 16 | offset


def addu(rd: int, rs: int, rt: int) -> int:
    return rs << 21 | rt << 16 | rd << 11 | 0x21


def em(store: bool, pointers: list[int]) -> Overlay:
    """One setter call passing the handle at DATA; with `store`, an entry of the volume table
    is stored at the node's VOLUME_SET right after it."""
    vt = DATA + 0x80
    code = [lui(A3, DATA >> 16), jal(a.ATTACK_TABLE_SETTER), addiu(A3, DATA & 0xFFFF)]
    table_entry = [lui(T0, vt >> 16), addiu(T0, vt & 0xFFFF), addu(T0, T0, T1), lw(V0, 0, T0)]
    code += [*table_entry, sw(V0, a.ATTACK_NODE.VOLUME_SET, S0)] if store else [0] * 5
    code += [0x03E0_0008, 0]
    data = bytearray(0x200)
    struct.pack_into("<I", data, 0, DATA + 0x10)
    data[0x28 + a.ATTACK_RECORD.POWER] = 50
    data[0x40 + a.ATTACK_RECORD.POWER] = 70
    data[0x40 + a.ATTACK_RECORD.VOLUME_SET] = 1
    data[0x58] = 1  # a nonzero lead ends the table
    struct.pack_into(f"<{len(pointers)}I", data, 0x80, *pointers)
    for at, bone, shape in ((0x100, 3, 0), (0x128, 5, 0), (0x180, 0x7E, 1)):
        data[at : at + hz.STRIDE] = struct.pack("<4HIf6f", bone, shape, shape, 0, 0, 90, *[0] * 6)
    for at in (0x150, 0x1A8):
        struct.pack_into("<H", data, at, hz.SENTINEL_BONE)
    header = struct.pack("<4sIIIIIII", b"MWo3", 1, LOAD, TEXT_SIZE, len(data), 0, 0, 0)
    return Overlay(
        (header + b"em75.ovl").ljust(TEXT, b"\0") + struct.pack(f"<{len(code)}I", *code) + data
    )


SETS = [DATA + 0x100, DATA + 0x180]


def test_tables_from_the_setter_call():
    ovl = em(True, SETS)
    (call,) = hb.setter_calls(ovl)
    assert Code(ovl, ovl.text).constant(call, Gpr.a3) == DATA
    (t,) = hb.tables(ovl)
    assert (t.handle, t.records, t.volume_table) == (DATA, DATA + 0x10, DATA + 0x80)
    assert [x.power for x in t.attacks] == [0, 50, 70]
    assert [v.bones for v in t.volumes] == [[3, 5], [0x7E]]
    capsule = t.volume_for(2)
    assert capsule is not None and not capsule.rigged and t.rigged
    assert hb.primary_table([t]) is t


def test_structural_fallback():
    ovl = em(False, SETS * 2)
    (t,) = hb.tables(ovl)
    assert t.volume_table == DATA + 0x80 and len(t.volumes) == 4


def test_no_setter_call():
    ovl = em(True, SETS)
    ovl.write_u32(ovl.text.start + 4, 0)
    assert hb.tables(ovl) == [] and hb.primary_table([]) is None


def test_id_offset():
    assert hb.id_offset(75, 75) == 0 and hb.id_offset(75, 88) == 70
    assert hb.id_offset(2, 3) is None


def test_every_overlay(game):
    for species in files.EM_SPECIES:
        ts = hb.tables(game.em(species))
        assert bool(ts) == (species not in (1, 33))
        assert all(t.volumes and t.attacks for t in ts)
    primary = hb.primary_table(hb.tables(game.em(75)))
    assert primary is not None and len(primary.attacks) == 107


def test_verify(game, capsys):
    assert main(["hitboxes", "--verify", "--data", str(game.root)]) == 0
    assert "all checks passed" in capsys.readouterr().out
