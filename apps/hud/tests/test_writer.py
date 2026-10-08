# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The writer: each staged edit once per monster and quest area, on its own thread, through
the reader's connection; QUEST_PREP drives it."""

import struct
import time
from dataclasses import replace

import pygame
from mhfu import addresses as a
from mhfu_hud.app import TAB_QUEST_PREP
from mhfu_hud.calibration import Calibration
from mhfu_hud.edits import Edit, Kind
from mhfu_hud.monster_db import PICKER_SPECIES
from mhfu_hud.reader import QUEST_MAP, MemoryReader
from mhfu_hud.writer import GameWriter

E = a.ENTITY


def wait(cond, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_once(writer, recorder, snaps):
    quest = snaps["quest"]
    tigrex = quest.monsters[0]
    writer.stage(Edit(Kind.SIZE, tigrex.species, 1.5))
    assert writer.apply(quest) == 1
    size = struct.pack("<f", 1.5)
    assert recorder.writes == [
        (tigrex.ptr + E.SIZE_SCALE, size),
        (tigrex.ptr + E.RENDER_SCALE, size * 3),
        (tigrex.ptr + E.SIZE_RADIUS, size),
    ]
    assert writer.apply(quest) == 0
    assert len(recorder.writes) == 3
    assert writer.status.staged[0].applied == 1


def test_rearm(writer, recorder, snaps):
    writer.stage(Edit(Kind.HP, 0x4B, 500))
    assert writer.apply(snaps["quest"]) == 1
    assert writer.apply(snaps["disconnected"], snaps["quest"]) == 0  # no game: no new area
    assert writer.apply(snaps["loading"], snaps["quest"]) == 1  # one batch, in order
    assert recorder.writes[-1] == (snaps["quest"].monsters[0].ptr + E.HP, struct.pack("<H", 500))
    assert writer.status.writes == 2


def test_change(writer, recorder, snaps):
    writer.stage(Edit(Kind.SPECIES, 0x46, 0x4B))
    assert writer.apply(snaps["quest"]) == 1  # the Popo only
    assert recorder.writes == [(snaps["quest"].monsters[1].ptr + E.SPECIES, b"\x4b")]
    edit = writer.status.staged[0].edit
    writer.change(0, replace(edit, enabled=False))
    writer.change(0, edit)
    assert writer.apply(snaps["quest"]) == 0  # toggling keeps what was written
    writer.change(0, edit.stepped(1))
    assert writer.apply(snaps["quest"]) == 1  # a new value writes again
    writer.change(0, None)
    assert writer.status.staged == ()


def test_master(writer, recorder, snaps):
    writer.stage(Edit.of(Kind.SIZE, 0x4B))
    writer.toggle()
    assert not writer.status.active
    assert writer.apply(snaps["quest"]) == 0
    assert recorder.writes == []


def test_thread(writer, source, recorder, snaps):
    writer.stage(Edit.of(Kind.SIZE, 0x4B))
    writer.start()
    try:
        source.queue.put(snaps["quest"])
        wait(lambda: writer.status.writes == 1)
    finally:
        writer.stop()
    assert len(recorder.writes) == 3


def test_live(fake, tmp_path):
    """The real reader and writer on a fake PPSSPP: the edit lands in its memory."""
    sections = Calibration(tmp_path / "cal.json").section_map(QUEST_MAP)
    reader = MemoryReader(port=fake.port, poll_hz=50, sections=sections)
    writer = GameWriter(reader)
    writer.stage(Edit(Kind.SIZE, 0x4B, 1.5))
    reader.start()
    writer.start()
    try:
        wait(lambda: fake.peek("3f", fake.tigrex + E.RENDER_SCALE) == (1.5,) * 3)
        wait(lambda: reader.snapshot.monsters and reader.snapshot.monsters[0].render_scale == 1.5)
    finally:
        writer.stop()
        reader.stop()
    assert fake.peek("3f", fake.popo + E.RENDER_SCALE) != (1.5,) * 3
    assert len(fake.writes) == 3


def test_quest_prep(app, writer):
    app.tab = TAB_QUEST_PREP
    prep, snap = app.quest_prep, app.reader.snapshot
    prep.selected_species = next(i for i, (n, _) in enumerate(PICKER_SPECIES) if n == "Tigrex")
    for key in (pygame.K_RETURN, pygame.K_EQUALS, pygame.K_EQUALS, pygame.K_MINUS):
        assert prep.handle_key(key, snap)
    (staged,) = writer.status.staged
    assert staged.edit == Edit(Kind.SIZE, 0x4B, 1.05)
    assert prep.handle_key(pygame.K_t, snap)
    assert writer.status.staged[0].edit.kind is Kind.SPECIES
    assert prep.handle_key(pygame.K_RETURN, snap)
    assert not writer.status.staged[0].edit.enabled
    assert prep.handle_key(pygame.K_m, snap)
    assert not writer.status.enabled
    assert prep.handle_key(pygame.K_x, snap)
    assert writer.status.staged == ()
    app._render(snap)


def test_quest_prep_read_only(app_ro):
    prep, snap = app_ro.quest_prep, app_ro.reader.snapshot
    assert prep.handle_key(pygame.K_RETURN, snap)  # taken, stages nothing
    prep.zone = "edits"
    for key in (pygame.K_EQUALS, pygame.K_t, pygame.K_m, pygame.K_x):
        assert not prep.handle_key(key, snap)
    app_ro.tab = TAB_QUEST_PREP
    app_ro._render(snap)


def test_picker_names_once():
    names = [n for n, _ in PICKER_SPECIES]
    assert len(names) == len(set(names))
    assert dict(PICKER_SPECIES)["Nargacuga"] == 0x51, "a checked id binds the picker"
