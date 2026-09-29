import itertools

import pytest
from mhfu import addresses as a
from mhfu.live import home, travel
from mhfu.live import navigation as nav

PLAYER_AT = a.PLAYER_ENTITY + a.ENTITY.TRANSLATION


def at(fake, x, z):
    fake.poke("3f", PLAYER_AT, x, 0.0, z)


class Walks(list):
    """Where walks and creeps went; a creep toward a point in `misses` stops short."""

    def __init__(self):
        super().__init__()
        self.misses = set()


@pytest.fixture
def walks(monkeypatch, fake):
    """Walks teleport the player."""
    log = Walks()

    def walk_to(s, x, z, **kw):
        log.append((x, z))
        at(fake, x, z)
        return nav.Walk(True, (x, z), 0.0, 0.0, "arrived")

    def creep_to(s, x, z, **kw):
        if (x, z) in log.misses:
            return False
        walk_to(s, x, z)
        return True

    monkeypatch.setattr(nav, "walk_to", walk_to)
    monkeypatch.setattr(nav, "walk_path", lambda s, points, **kw: [walk_to(s, *p) for p in points])
    monkeypatch.setattr(nav, "creep_to", creep_to)
    fake.poke("I", a.SCENE_OBJECT, a.SCENE_ROAM)
    at(fake, *home.ROOM_CENTRE)
    return log


def test_in_home(s, fake):
    at(fake, *home.ROOM_CENTRE)
    assert home.in_home(s)
    at(fake, *home.DOOR_XZ)
    assert not home.in_home(s)


def test_enter_and_leave(s, fake, walks):
    at(fake, 11157.0, 12273.0)
    fake.on_press.append(lambda b: at(fake, *home.ROOM_CENTRE))
    assert home.enter(s)
    fake.on_press[:] = [lambda b: at(fake, *home.DOOR_XZ)]
    assert home.leave(s)
    assert fake.presses == ["square", "square"]


def test_stand_at_tries_the_next_approach(s, fake, walks):
    exit_ = home.FIXTURES["exit"]
    walks.misses.add(exit_.creep[0])
    assert home.stand_at(s, "exit")
    assert walks == [home.ROOM_CENTRE, *exit_.walk, exit_.creep[1]]


def test_stand_at_misses(s, fake, walks):
    walks.misses.update(home.FIXTURES["bed"].creep)
    assert not home.stand_at(s, "bed")
    with pytest.raises(KeyError, match="bed"):
        home.stand_at(s, "sofa")


def test_open_and_close_the_item_box(s, fake, walks):
    fake.on_press.append(
        lambda b: fake.poke(
            "I", a.SCENE_OBJECT, a.SCENE_ITEM_BOX_MENU if b == "square" else a.SCENE_PROMPT
        )
    )
    assert home.open_item_box(s)
    assert home.close_menu(s)
    assert fake.presses == ["square", "circle"]


def test_at_save_point(s, fake, walks):
    at(fake, *home.FIXTURES["bed"].zone)
    assert not home.at_save_point(s)
    flicker = itertools.cycle([a.SCENE_ROAM, a.SCENE_PROMPT + 0x40])
    fake.on_read.append(
        lambda addr: addr == a.SCENE_OBJECT and fake.poke("I", a.SCENE_OBJECT, next(flicker))
    )
    assert home.at_save_point(s)
    assert fake.presses == []


def test_travel_to(s, fake, walks, monkeypatch):
    monkeypatch.setattr(travel, "travel_to", lambda s, dest: dest == "farm")
    assert home.travel_to(s, "farm")
    at(fake, *home.DOOR_XZ)
    with pytest.raises(home.NotInHome):
        home.travel_to(s, "farm")
