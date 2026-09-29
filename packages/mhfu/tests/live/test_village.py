import itertools

import pytest
from mhfu import addresses as a
from mhfu.live import navigation as nav
from mhfu.live import village
from mhfu.structs import Screen

PLAYER_AT = a.PLAYER_ENTITY + a.ENTITY.TRANSLATION
TEXT = a.RAM.start + 0x90_0000  # a free spot for dialogue lines
OTHER_NPC = a.NPC_HEAP + 0x1_0000


def npc(fake, base, x, z, talkable=0):
    fake.poke("I", base + a.ENTITY.VTABLE, a.NPC_VTABLE)
    fake.poke("3f", base + a.ENTITY.TRANSLATION, x, 0.0, z)
    fake.poke("f", base + a.ENTITY.TRANSLATION_W, 1.0)
    fake.poke("B", base + a.ENTITY.TALKABLE, talkable)


def say(fake, line, scene=a.SCENE_DIALOG):
    raw = line.encode() + b"\0"
    fake.poke(f"{len(raw)}s", TEXT, raw)
    fake.poke("I", a.DIALOG_TEXT, TEXT)
    fake.poke("I", a.SCENE_OBJECT, scene)


@pytest.fixture
def walks(monkeypatch, fake):
    """Walks teleport the player to their end and are recorded."""
    log = []

    def walk_to(s, x, z, **kw):
        log.append((x, z))
        fake.poke("3f", PLAYER_AT, x, 0.0, z)
        return nav.Walk(True, (x, z), 0.0, 0.0, "arrived")

    def walk_path(s, points, **kw):
        return [walk_to(s, x, z) for x, z in points][-1]

    monkeypatch.setattr(nav, "walk_to", walk_to)
    monkeypatch.setattr(nav, "walk_path", walk_path)
    monkeypatch.setattr(nav, "face", lambda s, x, z, **kw: True)
    fake.poke("I", a.SCENE_OBJECT, a.SCENE_VILLAGE)
    return log


def test_scan_npcs(s, fake):
    npc(fake, OTHER_NPC, 1.0, 2.0)
    npc(fake, a.NPC_ELDER, 10394.0, 8487.0)
    fake.poke("I", a.NPC_HEAP + 0x100, a.NPC_VTABLE)  # w is 0: not a live transform
    fake.poke("I", a.NPC_HEAP + 0x202, a.NPC_VTABLE)  # misaligned
    found = village.scan_npcs(s)
    assert [n.base for n in found] == [OTHER_NPC, a.NPC_ELDER]
    assert found[1].xz == (10394.0, 8487.0)
    assert village.nearest_npc(s, 0.0, 0.0).base == OTHER_NPC
    assert village.nearest_npc(s, 0.0, 0.0, within=1.0) is None


def test_move_npc(s, fake):
    npc(fake, a.NPC_ELDER, 0.0, 0.0)
    village.move_npc(s, a.NPC_ELDER, 1.0, 2.0, 3.0)
    assert fake.peek("3f", a.NPC_ELDER + a.ENTITY.POSITION) == (1.0, 2.0, 3.0)
    assert fake.peek("3f", a.NPC_ELDER + a.ENTITY.TRANSLATION) == (1.0, 2.0, 3.0)


def test_talk_to(s, fake, walks):
    npc(fake, a.NPC_ELDER, 10394.0, 8487.0, talkable=1)
    fake.on_press.append(lambda b: b == "cross" and say(fake, "What level of Quest?"))
    assert village.talk_to(s, a.NPC_ELDER) == "What level of Quest?"
    assert fake.presses == ["cross"]
    assert walks == [(10394.0, 8487.0)]


def test_talk_circles_until_the_flag_lights(s, fake, walks):
    npc(fake, a.NPC_ELDER, 0.0, 0.0)
    fake.on_read.append(
        lambda addr: len(walks) == 3 and fake.poke("B", a.NPC_ELDER + a.ENTITY.TALKABLE, 1)
    )
    fake.on_press.append(lambda b: say(fake, "Hello"))
    assert village.talk_to(s, a.NPC_ELDER) == "Hello"
    assert fake.presses == ["cross"]
    assert [round(nav.distance(w, (0.0, 0.0))) for w in walks[1:]] == [150, 150]


def test_talk_to_nobody(s, fake, walks):
    assert village.talk_to(s, a.NPC_ELDER) is None
    assert fake.presses == [] and walks == []


def test_open_npc_menu(s, fake, walks):
    npc(fake, a.NPC_ELDER, 0.0, 0.0, talkable=1)
    menu = a.NPC_MENU_STATE

    def press(button):
        if len(fake.presses) == 1:
            say(fake, "Welcome!")
        else:
            fake.poke("I", menu + a.NPC_MENU.PARTNER, a.NPC_ELDER)
            fake.poke("I", a.SCENE_OBJECT, a.SCENE_SHOP_MENU)

    fake.on_press.append(press)
    assert village.open_npc_menu(s, a.NPC_ELDER) == "Welcome!"
    assert fake.presses == ["cross", "cross"]


def test_open_npc_menu_without_a_list(s, fake, walks):
    npc(fake, a.NPC_ELDER, 0.0, 0.0, talkable=1)
    fake.poke("I", a.NPC_MENU_STATE + a.NPC_MENU.PARTNER, OTHER_NPC)  # a stale partner
    fake.on_press.append(
        lambda b: say(
            fake, "Nice day.", a.SCENE_DIALOG if len(fake.presses) == 1 else a.SCENE_VILLAGE
        )
    )
    assert village.open_npc_menu(s, a.NPC_ELDER) is None


def at_exit(fake, walks):
    """The exit's prompt pair flickers while the player stands at the exit."""
    flicker = itertools.cycle([a.SCENE_PROMPT, a.SCENE_PROMPT_ALT])

    def read(addr):
        if addr == a.SCENE_OBJECT and walks and walks[-1] == village.EXIT_XZ:
            fake.poke("I", a.SCENE_OBJECT, next(flicker))

    fake.on_read.append(read)


def test_depart(s, fake, walks):
    at_exit(fake, walks)

    def press(button):
        if fake.presses == ["square", "square"]:  # the first press is lost
            fake.poke("B", a.SCREEN_STATE, Screen.IN_AREA)
            fake.poke("H", a.AREA_INDEX, 98)

    fake.on_press.append(press)
    assert village.depart(s) == 98
    assert fake.presses == ["square", "square"]
    assert walks == list(village.EXIT_ROUTE)


def test_depart_without_a_contract(s, fake, walks):
    with pytest.raises(TimeoutError, match="under contract"):
        village.depart(s, attempts=2)
    assert fake.presses == []
    assert len(walks) == len(village.EXIT_ROUTE) + 2  # a nudge per attempt
