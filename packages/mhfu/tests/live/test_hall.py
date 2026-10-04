# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu import addresses as a
from mhfu.live import hall, travel
from mhfu.live import navigation as nav
from mhfu.structs import Screen

PLAYER_AT = a.PLAYER_ENTITY + a.ENTITY.TRANSLATION


def staff(fake, present=True):
    """Put the hall's NPCs at their spots, or clear the counter slots as a load does."""
    for base, (_, (x, z)) in hall.NPCS.items():
        fake.poke("I", base + a.ENTITY.VTABLE, a.NPC_VTABLE if present else 0)
        fake.poke("3f", base + a.ENTITY.TRANSLATION, x, 0.0, z)
        fake.poke("f", base + a.ENTITY.TRANSLATION_W, 1.0)


def at(fake, x, z):
    fake.poke("3f", PLAYER_AT, x, 0.0, z)


@pytest.fixture
def walks(monkeypatch, fake):
    """Walks and creeps teleport the player to their end and are recorded."""
    log = []

    def walk_to(s, x, z, **kw):
        log.append((x, z))
        at(fake, x, z)
        return nav.Walk(True, (x, z), 0.0, 0.0, "arrived")

    def creep_to(s, x, z, **kw):
        walk_to(s, x, z)
        return True

    monkeypatch.setattr(nav, "walk_to", walk_to)
    monkeypatch.setattr(nav, "walk_path", lambda s, points, **kw: [walk_to(s, *p) for p in points])
    monkeypatch.setattr(nav, "creep_to", creep_to)
    fake.poke("I", a.SCENE_OBJECT, a.SCENE_ROAM)
    return log


def test_in_hall(s, fake):
    assert not hall.in_hall(s)
    staff(fake)
    assert hall.in_hall(s)
    fake.poke("3f", a.NPC_HALL_COUNTER_G + a.ENTITY.TRANSLATION, 9000.0, 0.0, 9000.0)
    assert not hall.in_hall(s)  # the village's NPC in that slot


def test_enter(s, fake, walks):
    fake.on_press.append(lambda b: b == "square" and len(fake.presses) == 2 and staff(fake))
    assert hall.enter(s)
    assert fake.presses == ["square", "square"]
    assert walks[: len(hall.DOOR_ROUTE)] == list(hall.DOOR_ROUTE)


def test_leave(s, fake, walks):
    staff(fake)
    fake.on_press.append(lambda b: staff(fake, present=False))
    assert hall.leave(s)
    assert fake.presses == ["square"]
    assert walks == [hall.ARRIVAL_XZ, hall.ZONE_EXIT]


def test_stand_at_counter_sweeps_to_the_flag(s, fake, walks):
    staff(fake)
    counter = a.NPC_HALL_COUNTER_HIGH

    def sweep(addr):
        if addr == PLAYER_AT:
            x, _, z = fake.peek("3f", PLAYER_AT)
            at(fake, x, z - 20)
            fake.poke("B", counter + a.ENTITY.TALKABLE, 5250 < z - 20 < 5300)

    fake.on_read.append(sweep)
    assert hall.stand_at_counter(s, counter)
    assert 5250 < fake.peek("3f", PLAYER_AT)[2] < 5300


def test_stand_at_counter_runs_off_the_desk(s, fake, walks):
    staff(fake)
    fake.on_read.append(
        lambda addr: addr == PLAYER_AT and at(fake, 4150.0, fake.peek("3f", PLAYER_AT)[2] - 100)
    )
    assert not hall.stand_at_counter(s, a.NPC_HALL_COUNTER_G)


def test_talk_to_counter_needs_a_counter():
    with pytest.raises(KeyError, match="counter_low"):
        hall.talk_to_counter(None, "counter_x")


def test_depart_takes_two_presses(s, fake, walks):
    staff(fake)

    def press(button):
        if len(fake.presses) == 2:
            staff(fake, present=False)
            fake.poke("B", a.SCREEN_STATE, Screen.IN_AREA)
            fake.poke("H", a.AREA_INDEX, 98)

    fake.on_press.append(press)
    assert hall.depart(s) == 98
    assert fake.presses == ["square", "square"]


def test_depart_without_a_contract(s, fake, walks):
    staff(fake)
    with pytest.raises(TimeoutError, match="under contract"):
        hall.depart(s, attempts=1)
    assert fake.presses == ["square"] * 3


def test_open_item_box(s, fake, walks):
    staff(fake)
    fake.on_press.append(lambda b: fake.poke("I", a.SCENE_OBJECT, a.SCENE_ITEM_BOX_MENU_HALL))
    assert hall.open_item_box(s)
    assert walks[0] == hall.ITEM_BOX_APPROACH and walks[-1] == hall.ZONE_ITEM_BOX


def test_a_zone_press_that_leaves_the_hall(s, fake, walks):
    staff(fake)
    fake.on_press.append(lambda b: staff(fake, present=False))
    with pytest.raises(hall.NotInHall, match="wrong zone"):
        hall.sit(s)


def test_travel_to_retries_the_zone(s, fake, walks, monkeypatch):
    staff(fake)
    calls = []

    def travel_to(s, dest):
        calls.append(dest)
        if len(calls) == 1:
            raise RuntimeError("triangle opened no travel menu")
        return True

    monkeypatch.setattr(travel, "travel_to", travel_to)
    assert hall.travel_to(s, "house")
    assert calls == ["house", "house"]


def test_travel_to_a_greyed_row(s, fake, walks, monkeypatch):
    staff(fake)

    def travel_to(s, dest):
        raise travel.DestinationUnavailable(dest)

    monkeypatch.setattr(travel, "travel_to", travel_to)
    with pytest.raises(travel.DestinationUnavailable):
        hall.travel_to(s, "hall_offline")


def test_read_conversation(s, fake):
    lines = iter(["Welcome.", "Quests are here.", "Good luck."])

    def show(line):
        raw = line.encode() + b"\0"
        fake.poke(f"{len(raw)}s", a.RAM.start + 0x90_0000, raw)
        fake.poke("I", a.DIALOG_TEXT, a.RAM.start + 0x90_0000)

    show(next(lines))
    fake.poke("I", a.SCENE_OBJECT, a.SCENE_DIALOG)

    def press(button):
        line = next(lines, None)
        if line is None:
            fake.poke("I", a.SCENE_OBJECT, a.SCENE_ROAM)
        else:
            show(line)

    fake.on_press.append(press)
    assert hall.read_conversation(s) == "Welcome.\nQuests are here.\nGood luck."


def test_scan(s, fake):
    staff(fake)
    roles = {n.base: role for n, role in hall.scan(s)}
    assert roles[a.NPC_HALL_COUNTER_LOW] == "counter_low"
    assert roles[a.NPC_ELDER] == "info"
