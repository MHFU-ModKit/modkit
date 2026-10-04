# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu import addresses as a
from mhfu.live import travel

PLAYER_AT = a.PLAYER_ENTITY + a.ENTITY.TRANSLATION
HALL = (4322.0, 5698.0)


class Menu:
    """The destination list at the house exit: triangle opens it, cross on a row that is not
    greyed goes there. `scene` is the value it shows; the first `swallow` crosses are lost."""

    def __init__(self, fake, scene=a.SCENE_TRAVEL_MENU, greyed=(), swallow=0):
        self.fake, self.scene, self.greyed, self.swallow = fake, scene, greyed, swallow
        self.open, self.row, self.went = False, 0, None
        fake.poke("I", a.PLAYER_ENTITY + a.ENTITY.VTABLE, a.PLAYER_ENTITY_VTABLE)
        fake.poke("3f", PLAYER_AT, 2360.0, 0.0, 1200.0)
        fake.poke("I", a.SCENE_OBJECT, a.SCENE_ROAM)
        fake.on_press.append(self.press)

    def show(self, open):
        self.open = open
        self.fake.poke("I", a.SCENE_OBJECT, self.scene if open else a.SCENE_ROAM)

    def press(self, button):
        if button == "triangle" and not self.open:
            self.row = 0
            self.show(True)
        elif not self.open:
            return
        elif button in ("up", "down"):
            self.row = (self.row + (1 if button == "down" else -1)) % len(travel.DESTINATIONS)
        elif button == "circle":
            self.show(False)
        elif button == "cross" and self.swallow:
            self.swallow -= 1
        elif button == "cross" and self.row not in self.greyed:
            self.went = travel.DESTINATIONS[self.row]
            self.show(False)
            self.fake.poke("3f", PLAYER_AT, HALL[0], 0.0, HALL[1])
        self.fake.poke("B", a.TRAVEL_CURSOR, self.row)


def test_travel_to(s, fake):
    menu = Menu(fake)
    assert travel.travel_to(s, "hall_offline")
    assert menu.went == "hall_offline"
    assert fake.presses == ["down", "triangle", "down", "down", "down", "cross", "down"]


def test_greyed_row(s, fake):
    Menu(fake, greyed={0})
    with pytest.raises(travel.DestinationUnavailable, match="house"):
        travel.travel_to(s, "house")
    assert fake.presses == ["down", "triangle"] + ["cross"] * 3 + ["circle", "down"]


def test_a_lost_cross_goes_to_the_right_row(s, fake):
    menu = Menu(fake, scene=a.SCENE_PROMPT + 0x100, swallow=1)  # a pool value this visit
    assert travel.travel_to(s, "farm")
    assert menu.went == "farm"


def test_no_menu_out_of_a_zone(s, fake):
    fake.poke("I", a.SCENE_OBJECT, a.SCENE_ROAM)
    with pytest.raises(RuntimeError, match="exit zone"):
        travel.travel_to(s, "farm")


def test_no_arrival(s, fake):
    menu = Menu(fake)
    fake.on_press.append(lambda b: menu.went and fake.poke("3f", PLAYER_AT, 2360.0, 0.0, 1200.0))
    assert not travel.travel_to(s, "farm", timeout=5)
