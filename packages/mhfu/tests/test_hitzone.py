# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct

from mhfu import addresses as a
from mhfu import files
from mhfu import hitzone as hz
from mhfu.cli import main
from mhfu.memory import Image, Space
from mhfu.overlay import TEXT, Overlay

LOAD = 0x0010_0000
SPECIES = 75


def record(bone: int, row: int = 0, part: int = 0, at: tuple[float, ...] = (1, 2, 3)) -> bytes:
    return struct.pack("<4HIf6f", bone, 0, row, part, 0, 100.0, *at, 4, 5, 6)


SENTINEL = struct.pack("<4H", *[hz.SENTINEL_BONE] * 4).ljust(hz.STRIDE, b"\0")


def em(data: bytes) -> Overlay:
    text = bytes(0x10)
    header = struct.pack("<4sIIIIIII", b"MWo3", 1, LOAD, len(text), len(data), 0x20, 0, 0)
    return Overlay((header + b"em75.ovl").ljust(TEXT, b"\0") + text + data)


DATA = LOAD + TEXT + 0x10
HURTBOX = b"".join(record(b, row=b % 7, part=b % 8) for b in range(1, 6)) + SENTINEL
VOLUMES = b"".join(record(b, at=(0, 0, 0)) for b in (4, 9, 12)) + SENTINEL


def game_task(set_va: int, grids: int = 2) -> Image:
    """A species row pointing at `set_va` and at `grids` hitzone grids, then the grid table."""
    row = a.SPECIES_TABLE + SPECIES * a.SPECIES.stride
    data = bytearray(0x600)
    grid0 = row + 0x400
    table = grid0 + grids * (a.HITZONE_GRID.size or 0)
    struct.pack_into("<I", data, a.SPECIES.HURTBOX_SET, set_va)
    struct.pack_into("<I", data, a.SPECIES.HITZONE_STATES, table)
    for g in range(grids):
        at = 0x400 + g * 0x48
        data[at : at + 70] = bytes((10 * r + c + g) % 256 for r in range(7) for c in range(10))
        struct.pack_into("<I", data, table - row + 4 * g, grid0 + g * 0x48)
    return Image(bytes(data), row)


def test_find_sets():
    ovl = em(bytes(0x28) + HURTBOX + bytes(0x50) + VOLUMES + bytes(0x28))
    hurt, vol = hz.find_sets(ovl)
    assert (hurt.va, hurt.kind, len(hurt.volumes)) == (DATA + 0x28, hz.HURTBOX, 5)
    assert hurt.parts == [1, 2, 3, 4, 5] and hurt.rows == [1, 2, 3, 4, 5]
    assert vol.kind == hz.VOLUME and vol.bones == [4, 9, 12]
    v = hurt.volumes[0]
    assert (v.bone, v.radius, v.offset, v.far, v.capsule) == (1, 100.0, (1, 2, 3), (4, 5, 6), False)


def test_own_set_and_grids():
    ovl = em(bytes(0x28) + HURTBOX)
    mem = Space([game_task(DATA + 0x28), ovl])
    own = hz.own_set(mem, ovl, SPECIES)
    assert own is not None and own.va == DATA + 0x28 and len(own.volumes) == 5
    assert hz.species_sets(mem, ovl) == {DATA + 0x28: SPECIES}
    zones = hz.species_hitzones(mem, SPECIES)
    assert zones is not None and len(zones.states) == 2
    assert zones.states[1].rows[2][:3] == (21, 22, 23)


def test_pointer_outside_the_overlay():
    ovl = em(HURTBOX)
    mem = Space([game_task(LOAD), ovl])
    assert hz.own_set(mem, ovl, SPECIES) is None
    assert hz.species_hitzones(mem, SPECIES + 1) is None


def test_walk_needs_a_sentinel():
    ovl = em(record(1) * 3)
    assert hz.walk_set(ovl, DATA) is None


def test_every_overlay(game):
    task = game.overlay(files.GAME_TASK)
    for species in files.EM_SPECIES:
        ovl = game.em(species)
        own = hz.own_set(Space([task, ovl]), ovl, species)
        assert own is not None and own.volumes
        assert any(s.kind == hz.HURTBOX for s in hz.find_sets(ovl))
    assert hz.owner(game, 76) == 75
    assert len(hz.all_hitzones(task)) == 90


def test_verify(game, capsys):
    assert main(["hitzones", "--verify", "--data", str(game.root)]) == 0
    assert "all checks passed" in capsys.readouterr().out
