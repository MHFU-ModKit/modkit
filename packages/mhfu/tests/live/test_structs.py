import math

import pytest
from mhfu import addresses as a
from mhfu.memory import Image
from mhfu.structs import Entity, Game

MONSTER = a.RAM.start + 0x90_0000  # a heap address inside the `ram` fixture


def test_entity(ram):
    e = Entity(ram, MONSTER)
    e.hp, e.position, e.species = 900, (1.0, 2.0, 3.0), 0x4B
    rotation = [0.0] * 12
    rotation[8], rotation[10] = 1.0, 1.0  # forward (1, 0, 1)
    e.rotation = tuple(rotation)
    assert (e.hp, e.position, e.species) == (900, (1.0, 2.0, 3.0), 0x4B)
    assert math.degrees(e.facing) == pytest.approx(45)


def test_player(ram):
    g = Game(ram)
    assert not g.player.loaded
    g.player.vtable = a.PLAYER_ENTITY_VTABLE
    assert g.player.loaded
    assert g.player.base == a.PLAYER_ENTITY


def test_monsters_skip_gaps_and_slot_0(ram):
    registry = [0] * 21
    registry[0] = a.ACTION_EXECUTOR  # slot 0 holds a stale code address
    registry[2], registry[7] = MONSTER, MONSTER + 0x2000
    Game(ram).registry = tuple(registry)
    assert {k: e.base for k, e in Game(ram).monsters().items()} == {2: MONSTER, 7: MONSTER + 0x2000}


def test_text(ram):
    g = Game(ram)
    assert g.text() == ""
    ram.write(MONSTER, "Hallo Jäger\nZeile\0rest".encode())
    g.dialog_text = MONSTER
    assert g.text() == "Hallo Jäger\nZeile"


def test_quest_card(ram):
    card = Game(ram).quest_card
    ram.write(card.base + a.QUEST_CARD.NAME, b"Hunt a Velocidrome\0")
    ram.write(card.base + a.QUEST_CARD.MONSTERS, b"Velocidrome\nVelociprey\0")
    assert card.name == "Hunt a Velocidrome"
    assert card.monsters == ["Velocidrome", "Velociprey"]
    assert card.client == ""


def test_card_fields_stop_at_the_next():
    card = Game(Image(b"x" * 0x600, a.QUEST_CARD_TEXT)).quest_card
    assert len(card.objective) == a.QUEST_CARD.FAIL_CONDITION - a.QUEST_CARD.OBJECTIVE


def test_quest(ram):
    q = Game(ram).quest
    q.timer = 36000
    first, second = q.targets
    second.em_id, second.count = 0x4B, 2
    assert q.timer == 36000
    assert (second.em_id, second.count) == (0x4B, 2)
    assert first.base + a.QUEST_TARGET.size == second.base


def test_quest_records(ram):
    data = MONSTER
    ram.write_u32(data + a.QUEST_DATA.LIST_A, 0x100)
    for i, record in enumerate([0x200, 0x240]):
        node = data + 0x100 + i * a.QUEST_LIST_NODE.size
        ram.write_u32(node + a.QUEST_LIST_NODE.HEADER, 0x300)
        ram.write_u32(node + a.QUEST_LIST_NODE.RECORD, record)
        ram.write_u16(data + record + a.QUEST_RECORD.EM_ID, 0x46 + i)
    q = Game(ram).quest
    q.records = data
    assert [r.em_id for r in q.data.monsters()] == [0x46, 0x47]


def test_species(ram):
    tigrex = Game(ram).species(0x4B)
    assert tigrex.base == a.SPECIES_TABLE + 0x4B * a.SPECIES.stride
    tigrex.sight_radius = 5000.0
    assert ram.f32(tigrex.base + a.SPECIES.SIGHT_RADIUS) == 5000.0


def test_npc_menu(ram):
    menu = Game(ram).npc_menu
    ram.write_u8(a.NPC_MENU_STATE + a.NPC_MENU.ROW_COUNT, 3)
    assert (menu.cursor, menu.row_count) == (0, 3)
