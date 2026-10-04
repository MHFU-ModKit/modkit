# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Quest boards: opening one, reading the cards, taking a quest by name and leaving on it.

The card being browsed is plain text in RAM (`s.game.quest_card`), rewritten on every page step,
so a quest is picked by a string match. The elder and the hall counters drive the same widget:
talk, rank menu, quest list, card, "a contract fee, still want it?" (cross is yes), then back to
free roam.

No cell says a contract is signed. With one active the elder asks to cancel it instead of
offering the board, and a board-less talk is retried; the proof of a contract is the
departure prompt, which is why `take` retries the whole chain when departing fails.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Literal

from ppsspp_debug import Button

from .. import addresses as a
from ..structs import Screen
from . import dialog, hall, village
from .menus import QUEST_RANK_MENU
from .session import Session

Board = Literal["elder", "hall"]
Log = Callable[[str], None]

RANKS = QUEST_RANK_MENU.rows
QUEST_LIST = frozenset({a.SCENE_QUEST_LIST, a.SCENE_QUEST_LIST_ALT})
ADVANCE_PRESSES = 40  # a first visit to a hall counter talks for 24 pages before its board
ADVANCE_WINDOW = 0.9
RANK_PRESSES = 12  # a locked rank ignores cross, leaving the rank menu up through these
PAGE_WINDOW = 0.3
CONTRACT_WINDOW = 2.0


class LockedRankError(RuntimeError):
    """The rank or counter is not unlocked on this save."""


class NoContractError(RuntimeError):
    """The board chain ran but no quest ended up under contract."""


@dataclass(frozen=True)
class Card:
    """One quest card; `index` is its place in the rank's list."""

    index: int
    name: str
    objective: str
    fail_condition: str
    description: str
    monsters: tuple[str, ...]
    client: str


@dataclass(frozen=True)
class Taken:
    card: Card
    area: int | None  # AREA_INDEX landed in; None when not departed


def read_card(s: Session) -> Card:
    c = s.game.quest_card
    return Card(
        s.game.quest_index,
        c.name,
        c.objective,
        c.fail_condition,
        c.description,
        tuple(c.monsters),
        c.client,
    )


def in_quest_list(s: Session) -> bool:
    return s.game.scene in QUEST_LIST


def advance_until(
    s: Session, scenes: Collection[int], what: str, attempts: int = ADVANCE_PRESSES
) -> int:
    """Press cross through a conversation until one of `scenes` is up, and return it.

    Raises TimeoutError once control comes back for good: the talk ended without it.
    """
    want = frozenset(scenes)

    def up() -> int:
        return dialog.one_of(s, want)

    for _ in range(attempts):
        if scene := up():
            return scene
        if dialog.settled_control(s):  # control flickers back between pages
            break
        s.press("cross", 5)
        try:
            return s.wait(up, ADVANCE_WINDOW, what)
        except TimeoutError:
            pass
    if scene := up():
        return scene
    scene = s.game.scene
    raise TimeoutError(f"never reached {what}; the scene is {dialog.scene_name(scene)}")


def open_board(
    s: Session,
    rank: int | str = 0,
    *,
    board: Board = "elder",
    counter: str = "counter_low",
    tries: int = 3,
) -> None:
    """Talk to the elder or a hall counter (in the hall) and end on the quest list at `rank`.

    A talk that ends without the rank menu is retried: the elder cancelling an active contract
    (cross accepts), or a counter's first-visit tutorial. A counter that ends the same way twice
    in a row is locked, and a rank that ignores cross is locked (its stars are grey).
    """
    want = QUEST_RANK_MENU.index(rank)
    ended: str | None = None
    for _ in range(tries):
        dialog.dismiss(s)
        if board == "hall":
            if not hall.talk_to_counter(s, counter):
                continue
        elif village.talk_to(s, village.ELDER) is None:
            continue
        try:
            advance_until(s, {a.SCENE_QUEST_RANK}, "the quest-rank menu")
        except TimeoutError:
            line = s.game.text()
            if board == "hall" and line == ended:
                raise LockedRankError(f"the hall's {counter} is locked; it said {line!r}") from None
            ended = line
            continue
        QUEST_RANK_MENU.goto(s, want)
        try:
            advance_until(s, QUEST_LIST, "the quest list", attempts=RANK_PRESSES)
        except TimeoutError:
            if s.game.scene == a.SCENE_QUEST_RANK:
                raise LockedRankError(
                    f"rank {RANKS[want]} ignores cross; locked on this save"
                ) from None
            raise
        return
    raise TimeoutError(
        f"no quest board from the {counter if board == 'hall' else board} in {tries} tries"
    )


def page(s: Session, direction: Button = "right", timeout: float = 4.0) -> int:
    """Step the list once and return the new index; the list debounces like every menu."""
    start = s.game.quest_index
    s.press_until(
        lambda: s.game.quest_index != start,
        f"the quest list to move {direction}",
        direction,
        frames=2,
        attempts=max(1, round(timeout / PAGE_WINDOW)),
        window=PAGE_WINDOW,
    )
    return s.game.quest_index


def _lap(s: Session, limit: int, stop: Callable[[Card], bool] = lambda c: False) -> list[Card]:
    """Cards from the current one round the list back to it, or up to the first `stop` card.

    A list that never moves holds one quest.
    """
    cards = [read_card(s)]
    for i in range(limit):
        if stop(cards[-1]):
            break
        try:
            index = page(s)
        except TimeoutError:
            if i:
                raise
            break
        if index == cards[0].index:
            break
        cards.append(read_card(s))
    return cards


def list_quests(s: Session, limit: int = 60) -> list[Card]:
    """Every quest of the open rank, in list order; the total is not in memory, so this laps."""
    return sorted(_lap(s, limit), key=lambda c: c.index)


def select(s: Session, name: str, limit: int = 60, names_only: bool = False) -> Card | None:
    """Page to the quest matching `name`, a case-insensitive substring; None if none does.

    The rank is read whole first, so the pick does not depend on where the cursor starts: the
    first name match in list order, else the first quest whose objective, description or
    monsters mention it (people know the monster; quest names often do not say it), unless
    `names_only`.
    """
    want = name.casefold()
    cards = list_quests(s, limit)
    named = [c for c in cards if want in c.name.casefold()]
    mentioned = [
        c
        for c in cards
        if not names_only and want in " ".join([c.objective, c.description, *c.monsters]).casefold()
    ]
    picks = named or mentioned
    if not picks:
        return None
    for _ in range(limit):
        if s.game.quest_index == picks[0].index:
            return read_card(s)
        page(s)
    raise TimeoutError(f"lost {picks[0].name!r} paging back to it")


def under_contract(s: Session) -> bool:
    """True in the village while a quest is under contract."""
    return s.game.screen_state == Screen.CONTRACT


def accept(s: Session, timeout: float = 25.0) -> str:
    """Take the quest on the card and press through to free roam; returns its name.

    Cross through the fee question is yes. Stops only on settled control, since a circle on the
    question during a page break would decline the quest.
    """
    name = s.game.quest_card.name
    s.press_until(
        lambda: dialog.settled_control(s),
        "free roam after accepting",
        frames=4,
        attempts=max(1, round(timeout / ADVANCE_WINDOW)),
        window=ADVANCE_WINDOW,
    )
    return name


def depart(s: Session, board: Board = "elder", attempts: int = 6) -> int:
    """Leave on the quest under contract from the village exit or the hall's back door."""
    if board == "hall":
        return hall.depart(s, attempts=attempts)
    return village.depart(s, attempts=attempts)


def take(
    s: Session,
    rank: int | str,
    name: str,
    *,
    board: Board = "elder",
    counter: str = "counter_low",
    leave: bool = True,
    attempts: int = 3,
    log: Log | None = None,
) -> Taken:
    """Open the board, take the quest matching `name`, and (with `leave`) depart on it.

    In the village a signed quest turns SCREEN_STATE to CONTRACT, so a lost acceptance runs the
    chain again at once. The hall has no such signal known: there a departure that never
    happens is the tell, and early departures get fewer tries so a failure is cheap. With
    `board="hall"` the player must be in the hall.
    """
    say = log or (lambda _: None)
    failure: Exception | None = None
    for attempt in range(attempts):
        final = attempt == attempts - 1
        open_board(s, rank, board=board, counter=counter)
        say(f"{board} board, rank {RANKS[QUEST_RANK_MENU.index(rank)]}")
        card = select(s, name)
        if card is None:
            raise LookupError(
                f"no quest matching {name!r} at rank {RANKS[QUEST_RANK_MENU.index(rank)]}"
            )
        say(f"selected {card.name!r}: {card.objective}")
        try:
            accept(s)
            if board == "elder":
                s.wait(lambda: under_contract(s), CONTRACT_WINDOW, "the quest under contract")
            say(f"accepted {card.name!r}")
            if not leave:
                return Taken(card, None)
            return Taken(card, depart(s, board, attempts=6 if final else 2))
        except TimeoutError as e:
            failure = e
            say(f"no contract took (attempt {attempt + 1}/{attempts}): {e}")
    raise NoContractError(
        f"could not get {name!r} under contract in {attempts} attempts"
    ) from failure
