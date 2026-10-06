# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Cold boot to the main menu and into the village, each step waiting on a memory read.

    logos -> language menu -> title (cycling with the attract movie) -> main menu
          -> continue -> character select -> village

SCREEN_STATE reads 0 for the logos, the language menu and the village alike; the scene tells them
apart. The language menu waits for input forever, and a press sent during the logos is lost.

Every wait is on memory and every press counts frames, so the flows run under `fast_forward`,
except the title, which takes START only at the game's own rate.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress

from ppsspp_debug import Disconnected, Unsupported

from .. import addresses as a
from ..structs import Screen
from . import dialog
from .area import VILLAGE
from .menus import LANGUAGE_MENU
from .session import Session

LANGUAGES = LANGUAGE_MENU.rows


def at_language_menu(s: Session) -> bool:
    return s.game.screen_state == Screen.BOOT and s.game.scene == a.SCENE_LANGUAGE_MENU


def select_language(s: Session, language: str = "english", timeout: float = 60.0) -> None:
    """Wait for the language menu, then pick `language` (one of LANGUAGES)."""
    if language not in LANGUAGES:
        raise ValueError(f"unknown language {language!r}; one of {LANGUAGES}")
    s.wait(lambda: at_language_menu(s), timeout, "language menu")
    LANGUAGE_MENU.goto(s, language)
    s.press_until(lambda: s.game.screen_state != Screen.BOOT, "exit from the language menu")


def _after_logos(s: Session) -> str:
    # the logos cycle through scene ids the village also uses, so "in game" is the player
    if at_language_menu(s):
        return "language"
    if s.game.screen_state != Screen.BOOT:
        return "title"
    return "in game" if s.game.player.loaded else ""


def boot_to_title(s: Session, language: str = "english", timeout: float = 90.0) -> bool:
    """Take a booting game to the title screen, answering the language menu if it asks.

    Returns False without pressing anything when the game is already past the title.
    """
    screen = s.game.screen_state
    if screen not in (Screen.BOOT, Screen.TITLE, Screen.INTRO):
        return False
    if screen == Screen.BOOT:
        where = s.wait(lambda: _after_logos(s), 30.0, "end of the boot logos")
        if where == "in game":
            return False
        if where == "language":
            select_language(s, language, timeout)
    s.wait(lambda: s.game.screen_state in (Screen.TITLE, Screen.INTRO), timeout, "title screen")
    return True


def press_past_title(s: Session, timeout: float = 30.0) -> None:
    """Press START until the main menu is up; the attract movie swallows some presses.

    Under fast-forward the title ignores START for minutes, so this runs at the game's rate.
    """
    window = 2.6
    with own_speed(s):
        s.press_until(
            lambda: s.game.screen_state == Screen.MENU,
            "main menu",
            "start",
            frames=3,
            attempts=max(1, round(timeout / window)),
            window=window,
        )


def boot_to_main_menu(s: Session, language: str = "english", timeout: float = 90.0) -> None:
    """Logos, language, title, main menu; returns at once on the main menu."""
    if s.game.screen_state == Screen.MENU:
        return
    if not boot_to_title(s, language, timeout):
        raise RuntimeError("the game is past the title screen; restart it to reach the main menu")
    press_past_title(s)


def to_village(s: Session, language: str = "english", timeout: float = 60.0) -> None:
    """Boot into the village on the first character slot.

    Cross from the main menu until the village: the menu opens on Continue when a save exists,
    and the screens between (character list, load question, messages) take cross. Their scene
    ids and the menu's highlight move with the language, so none is steered or checked. The
    character list has no known cursor cell, so other slots cannot be chosen closed-loop.
    """
    boot_to_main_menu(s, language)
    window = 1.0
    s.press_until(
        lambda: dialog.has_control(s) and s.game.map_subsection == VILLAGE,
        "village",
        frames=4,
        attempts=max(1, round(timeout / window)),
        window=window,
    )


def is_fast(s: Session) -> bool:
    try:
        return s.client.speed().fast_forward
    except Unsupported:
        return False


def set_fast(s: Session, on: bool) -> bool:
    """Run the emulator unlimited, or at the game's own rate; False on a PPSSPP that cannot."""
    try:
        s.client.set_speed(None, fast_forward=on)
    except Unsupported:
        return False
    return True


@contextmanager
def fast_forward(s: Session, on: bool = True) -> Iterator[bool]:
    """Unlimited speed for the block, the game's own rate after; yields whether it is fast."""
    fast = on and set_fast(s, True)
    try:
        yield fast
    finally:
        if fast:
            with suppress(Disconnected):
                set_fast(s, False)


@contextmanager
def own_speed(s: Session) -> Iterator[None]:
    """The game's own rate for the block, fast-forward again after if it was."""
    fast = is_fast(s)
    if fast:
        set_fast(s, False)
    try:
        yield
    finally:
        if fast:
            with suppress(Disconnected):
                set_fast(s, True)
