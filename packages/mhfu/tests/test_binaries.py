# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct

import pytest
from mhfu import files, mips
from mhfu.overlay import TEXT, Overlay


def overlay(load: int = 0x100000, text: bytes = b"") -> bytes:
    header = struct.pack("<4sIIIIIII", b"MWo3", 7, load, len(text), 0, 16, 0, 0)
    return (header + b"em75.ovl").ljust(TEXT, b"\0") + text


def test_overlay_maps_at_its_load_address():
    text = bytes(64) + struct.pack("<I", 0x03E00008)
    ovl = Overlay(overlay(text=text))
    assert ovl.name == "em75.ovl" and ovl.species == 75
    assert ovl.text == range(0x100000 + TEXT, 0x100000 + TEXT + len(text))
    assert ovl.u32(ovl.text.stop - 4) == 0x03E00008
    (ins,) = mips.instructions(ovl, ovl.text.stop - 4, ovl.text.stop)
    assert ins.isReturn() and ins.vram == ovl.text.stop - 4


def test_not_an_overlay():
    with pytest.raises(ValueError):
        Overlay(b"\0" * 0x100)


def test_file_ids():
    assert files.em_overlay(75) == 6108
    assert files.monster_pac(75) == 6185
    assert files.engine_id(files.GAME_TASK) == 71


def test_extracted_game(game):
    task = game.overlay(files.GAME_TASK)
    assert task.name == "game_task.ovl"
    for species in files.EM_SPECIES:  # the ctor list follows text and data directly
        em = game.em(species)
        assert em.ctors.start == em.initialised.stop
        assert em.read(em.text.start, 64) == bytes(64)
        assert em.species == species
    eboot = game.eboot()
    assert eboot.entry in eboot.text
    first = next(mips.instructions(eboot, eboot.text.start, eboot.text.start + 4))
    assert first.isValid()
