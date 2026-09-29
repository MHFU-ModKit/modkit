import pytest
from mhfu import addresses as a
from mhfu.live import camp
from mhfu.live import navigation as nav


@pytest.fixture
def moves(monkeypatch, fake):
    """Walks and pushes, recorded."""
    log = []
    monkeypatch.setattr(nav, "walk_to", lambda s, x, z, **kw: log.append((x, z)))
    monkeypatch.setattr(nav, "push", lambda s, heading, seconds, **kw: log.append(heading))
    fake.poke("I", a.SCENE_OBJECT, a.SCENE_QUEST)
    return log


def box(fake, opens_on=1):
    """The supply box's grid opens on circle press `opens_on` and closes on the next circle."""

    def press(button):
        if button != "circle":
            return
        n = fake.presses.count("circle")
        grid = a.SCENE_SUPPLY_BOX if n == opens_on else a.SCENE_QUEST
        fake.poke("I", a.SCENE_OBJECT, grid)

    fake.on_press.append(press)


def test_open_box_sweeps_along_the_face(s, fake, moves):
    box(fake, opens_on=3)
    assert camp.open_box(s)
    x, z = camp.SNOWY_MOUNTAINS.stand
    assert moves == [(x, z), 0.0, (x + 40, z), 0.0, (x - 40, z), 0.0]


def test_open_box_gives_up(s, fake, moves):
    assert not camp.open_box(s, attempts=2)
    assert fake.presses == ["circle", "circle"]


def test_take_map(s, fake, moves):
    box(fake)
    assert camp.take_map(s)
    assert fake.presses == ["circle", "cross", "circle"]
    assert not camp.is_open(s)
