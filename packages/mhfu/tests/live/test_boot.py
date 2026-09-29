import pytest
from mhfu import addresses as a
from mhfu.live import boot
from mhfu.structs import Screen


class Boot:
    """A cold boot: logos for a few reads, the language menu, title, main menu, save, village."""

    def __init__(self, fake, logo_reads: int = 5) -> None:
        self.fake, self.logo_reads = fake, logo_reads
        self.set(screen=Screen.BOOT, scene=0)
        fake.on_read.append(self.read)
        fake.on_press.append(self.press)

    def set(self, screen=None, scene=None):
        if screen is not None:
            self.fake.poke("B", a.SCREEN_STATE, screen)
        if scene is not None:
            self.fake.poke("I", a.SCENE_OBJECT, scene)

    def get(self, fmt, address):
        return self.fake.peek(fmt, address)[0]

    def read(self, address):
        if address == a.SCENE_OBJECT and self.get("I", a.SCENE_OBJECT) in (0, a.SCENE_ROAM):
            self.logo_reads -= 1
            if self.logo_reads < 0:
                self.set(scene=a.SCENE_LANGUAGE_MENU)

    def press(self, button):
        screen, scene = self.get("B", a.SCREEN_STATE), self.get("I", a.SCENE_OBJECT)
        if scene == a.SCENE_LANGUAGE_MENU and button in ("up", "down"):
            row = self.get("B", a.LANGUAGE_CURSOR) + (1 if button == "down" else -1)
            self.fake.poke("B", a.LANGUAGE_CURSOR, row % 5)
        elif scene == a.SCENE_LANGUAGE_MENU and button == "cross":
            self.set(screen=Screen.TITLE, scene=0)
        elif screen == Screen.TITLE and button == "start":
            self.set(screen=Screen.MENU, scene=a.SCENE_MAIN_MENU)  # it opens on Continue
        elif button == "cross":
            after = {
                a.SCENE_MAIN_MENU: a.SCENE_CHAR_SELECT,
                a.SCENE_CHAR_SELECT: a.SCENE_YES_NO,
                a.SCENE_YES_NO: a.SCENE_MESSAGE_BOX,
            }
            if scene in after:
                self.set(scene=after[scene])
            elif scene == a.SCENE_MESSAGE_BOX:
                self.set(screen=Screen.BOOT, scene=a.SCENE_VILLAGE)
                self.fake.poke("B", a.MAP_SUBSECTION, 35)


def test_cold_boot_to_the_village(s, fake):
    Boot(fake)
    boot.to_village(s, "deutsch")
    assert s.game.language_cursor == boot.LANGUAGES.index("deutsch")
    assert s.game.scene == a.SCENE_VILLAGE
    assert fake.presses == ["down", "down", "cross", "start"] + ["cross"] * 4


def test_logos_that_flash_a_village_scene(s, fake):
    Boot(fake)
    fake.poke("I", a.SCENE_OBJECT, a.SCENE_ROAM)  # the logos reuse village scene ids
    boot.to_village(s, "english")
    assert s.game.scene == a.SCENE_VILLAGE


def test_main_menu_is_a_no_op_on_the_main_menu(s, fake):
    fake.poke("B", a.SCREEN_STATE, Screen.MENU)
    boot.boot_to_main_menu(s)
    assert fake.presses == []


def test_past_the_title_in_the_village(s, fake):
    fake.poke("I", a.SCENE_OBJECT, a.SCENE_VILLAGE)
    fake.poke("I", a.PLAYER_ENTITY + a.ENTITY.VTABLE, a.PLAYER_ENTITY_VTABLE)
    assert not boot.boot_to_title(s)
    with pytest.raises(RuntimeError, match="past the title"):
        boot.boot_to_main_menu(s)
    assert fake.presses == []


def test_unknown_language(s):
    with pytest.raises(ValueError, match="one of"):
        boot.select_language(s, "german")
