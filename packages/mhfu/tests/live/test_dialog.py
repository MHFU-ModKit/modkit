import itertools

import pytest
from mhfu import addresses as a
from mhfu.live import dialog

# the hall door's prompt zone as seen once: two pool values, neither in the table
ZONE = (a.SCENE_PROMPT + 0xC4, a.SCENE_PROMPT - 0x14)


def show(fake, *scenes):
    """Make successive reads of the scene return `scenes`, the last one from then on."""
    values = itertools.chain(scenes, itertools.repeat(scenes[-1]))
    fake.on_read.append(
        lambda addr: addr == a.SCENE_OBJECT and fake.poke("I", a.SCENE_OBJECT, next(values))
    )


def cycle(fake, *scenes):
    """Make reads of the scene cycle through `scenes`, as a flickering screen does."""
    values = itertools.cycle(scenes)
    fake.on_read.append(
        lambda addr: addr == a.SCENE_OBJECT and fake.poke("I", a.SCENE_OBJECT, next(values))
    )


def test_control(s, fake):
    fake.poke("I", a.SCENE_OBJECT, a.SCENE_VILLAGE)
    assert dialog.has_control(s) and not dialog.prompt_showing(s)
    fake.poke("I", a.SCENE_OBJECT, a.SCENE_PROMPT)
    assert dialog.has_control(s) and dialog.prompt_showing(s)
    fake.poke("I", a.SCENE_OBJECT, ZONE[0])
    assert not dialog.has_control(s)


def test_scene_name():
    assert dialog.scene_name(a.SCENE_DIALOG) == "dialog"
    assert dialog.scene_name(0) == "unknown 0x00000000"


def test_settled_control_sees_a_page_break(s, fake):
    show(fake, a.SCENE_ROAM, a.SCENE_ROAM, a.SCENE_DIALOG)
    assert not dialog.settled_control(s)
    fake.poke("I", a.SCENE_OBJECT, a.SCENE_ROAM)
    fake.on_read.clear()
    assert dialog.settled_control(s)


@pytest.mark.parametrize(
    ("scenes", "zone"),
    [
        (ZONE, True),
        ((ZONE[0], a.SCENE_VILLAGE), True),
        ((ZONE[0],), False),
        ((ZONE[0], a.SCENE_DIALOG), False),
        ((a.SCENE_VILLAGE, a.SCENE_ROAM), False),
    ],
)
def test_prompt_zone(s, fake, scenes, zone):
    cycle(fake, *scenes)
    assert dialog.in_prompt_zone(s) is zone


def test_modal_is_up(s, fake):
    fake.poke("I", a.SCENE_OBJECT, ZONE[0])
    assert dialog.modal_is_up(s)
    cycle(fake, *ZONE)
    assert not dialog.modal_is_up(s)


def test_press_until_scene(s, fake):
    fake.poke("I", a.SCENE_OBJECT, a.SCENE_MAIN_MENU)
    fake.on_press.append(
        lambda b: len(fake.presses) == 2 and fake.poke("I", a.SCENE_OBJECT, a.SCENE_CHAR_SELECT)
    )
    assert dialog.press_until_scene(s, a.SCENE_CHAR_SELECT) == a.SCENE_CHAR_SELECT
    assert fake.presses == ["cross", "cross"]


def test_wait_for_scene_names_the_screen(s, fake):
    fake.poke("I", a.SCENE_OBJECT, a.SCENE_DIALOG)
    with pytest.raises(TimeoutError, match="quest_rank .* the scene is dialog"):
        dialog.wait_for_scene(s, a.SCENE_QUEST_RANK)


def test_dismiss(s, fake):
    fake.poke("I", a.SCENE_OBJECT, a.SCENE_DIALOG)
    fake.on_press.append(
        lambda b: b == "circle" and fake.poke("I", a.SCENE_OBJECT, a.SCENE_VILLAGE)
    )
    assert dialog.dismiss(s)
    assert fake.presses == ["circle"]


def test_dismiss_leaves_a_prompt_zone_alone(s, fake):
    cycle(fake, *ZONE)
    assert dialog.dismiss(s)
    assert fake.presses == []


def test_dismiss_gives_up(s, fake):
    fake.poke("I", a.SCENE_OBJECT, a.SCENE_DIALOG)
    assert not dialog.dismiss(s, timeout=3)
    assert fake.presses and set(fake.presses) == {"circle"}
