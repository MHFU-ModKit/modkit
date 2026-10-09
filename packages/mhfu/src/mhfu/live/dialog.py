# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What is on screen: the scene, whether the player can move, and backing out of menus.

SCREEN_STATE cannot tell the village from the boot logos (both 0), so screens are told apart by
the scene object at SCENE_OBJECT. Menus and prompt zones are pool allocations whose values move
between visits, so free roam is an allow-list and any other scene counts as a modal. The line
being said is `s.game.text()`.
"""

from __future__ import annotations

from ppsspp_debug import Button

from .. import addresses as a
from .session import POLL, Session

WALKING: frozenset[int] = a.FREE_ROAM_SCENES
"""Free roam with nothing on screen."""

FREE = WALKING | {a.SCENE_PROMPT, a.SCENE_PROMPT_ALT}
"""Free roam, counting the square-prompt zones the player can walk out of."""

MODAL = frozenset(
    {
        a.SCENE_LANGUAGE_MENU,
        a.SCENE_MAIN_MENU,
        a.SCENE_CHAR_SELECT,
        a.SCENE_DIALOG,
        a.SCENE_MESSAGE_BOX,
        a.SCENE_YES_NO,
        a.SCENE_SAVE_CONFIRM,
        a.SCENE_PAUSE_MENU,
        a.SCENE_NPC_MENU,
        a.SCENE_SHOP_MENU,
        a.SCENE_FELYNE_MENU,
        a.SCENE_SHOP_SELL,
        a.SCENE_FARM_UPGRADES,
        a.SCENE_QUEST_RANK,
        a.SCENE_QUEST_LIST,
        a.SCENE_QUEST_LIST_ALT,
        a.SCENE_TRAVEL_MENU,
        a.SCENE_ITEM_BOX_MENU,
        a.SCENE_ITEM_BOX_MENU_HALL,
    }
)
"""Known menus; not complete and cannot be, since each NPC's option list has its own scene."""

_NAMES = {
    int(v): v.name.removeprefix("SCENE_").lower()
    for v in a.table().addresses.values()
    if v.name.startswith("SCENE_") and v != a.SCENE_OBJECT
}


def scene_name(scene: int) -> str:
    """The scene's name from addresses.toml, such as "dialog", or its value if it has none."""
    return _NAMES.get(scene, f"unknown 0x{scene:08X}")


def has_control(s: Session) -> bool:
    """True while the player can move; an unknown scene counts as a menu.

    A prompt zone's scene is unknown too, so this reads False while standing in one: ask
    `modal_is_up` whether a press opened something.
    """
    return s.game.scene in FREE


def settled_control(s: Session, hold: float = 0.4) -> bool:
    """True once the player has had control for `hold` seconds.

    The scene drops back to free roam for a frame between two pages of a conversation, so a
    single `has_control` read says the talk is over while it is not.
    """
    return s.holds(lambda: has_control(s), hold)


def prompt_showing(s: Session) -> bool:
    """True while anything is on screen: a prompt zone, a speech box, a menu."""
    return s.game.scene not in WALKING


def in_prompt_zone(s: Session, samples: int = 12) -> bool:
    """True while the player stands in a square-prompt zone: walkable, but with an unknown scene.

    A zone flips between values every few frames while a menu holds one, and neither is a known
    menu. An unknown menu that alternates between two values of its own reads as a zone.
    """
    seen = []
    for i in range(samples):
        if i:
            s.sleep(POLL)
        seen.append(s.game.scene)
    distinct = set(seen)
    if distinct & MODAL or distinct <= WALKING:
        return False
    return len(distinct) >= 2


def modal_is_up(s: Session) -> bool:
    """True only for a real menu or speech box, not for a prompt zone."""
    return not has_control(s) and not in_prompt_zone(s)


def one_of(s: Session, scenes: frozenset[int]) -> int:
    """The scene if it is one of `scenes`, else 0."""
    scene = s.game.scene
    return scene if scene in scenes else 0


def _names(scenes: frozenset[int]) -> str:
    return " or ".join(sorted(scene_name(x) for x in scenes))


def wait_for_scene(s: Session, *scenes: int, timeout: float = 10.0) -> int:
    """Wait until the scene is one of `scenes`, and return it."""
    want = frozenset(scenes)
    try:
        return s.wait(lambda: one_of(s, want), timeout, f"scene {_names(want)}")
    except TimeoutError as e:
        raise TimeoutError(f"{e}; the scene is {scene_name(s.game.scene)}") from None


def press_until_scene(
    s: Session,
    *scenes: int,
    button: Button = "cross",
    frames: int = 5,
    attempts: int = 12,
    window: float = 0.9,
) -> int:
    """Press `button` until the scene is one of `scenes`, and return it."""
    want = frozenset(scenes)
    what = f"scene {_names(want)}"
    try:
        return s.press_until(
            lambda: one_of(s, want), what, button, frames=frames, attempts=attempts, window=window
        )
    except TimeoutError as e:
        raise TimeoutError(f"{e}; the scene is {scene_name(s.game.scene)}") from None


def dismiss(
    s: Session, timeout: float = 12.0, *, hold: float = 0.5, skip_in_zone: bool = True
) -> bool:
    """Press circle until the player has had control for `hold` seconds; False on timeout.

    In a prompt zone circle is not a cancel (at the hall door it joins the online hall), and
    the player has control there anyway, so with `skip_in_zone` a zone returns at once.
    """
    if skip_in_zone and in_prompt_zone(s):
        return True
    deadline = s.now() + timeout
    while s.now() < deadline:
        if settled_control(s, hold):
            return True
        s.press("circle", 4)
        try:
            s.wait(lambda: has_control(s), 0.6, "control")
        except TimeoutError:
            pass
    return has_control(s)
