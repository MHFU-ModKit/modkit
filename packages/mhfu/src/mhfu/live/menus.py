"""Menus steered closed-loop: read the cursor, press, wait for it to move, repeat.

Every menu debounces input, so presses sent faster than it accepts them vanish without an error
(eight taps move the main menu four rows). Counting presses therefore never works; `Menu.goto`
re-reads the cursor after each press instead.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ppsspp_debug import Button

from ..structs import Game
from .session import Session

BOX_MENU_TOP, BOX_MENU_STEP = 32, 16  # BOX_MENU_HIGHLIGHT = TOP + STEP * row, house box only
PRESS_FRAMES = 2
STUCK_PRESSES = 8  # presses the cursor may ignore in a row before goto gives up


def _highlight_row(y: int, top: int, step: int, rows: int, what: str) -> int:
    row, rest = divmod(y - top, step)
    if rest or not 0 <= row < rows:
        raise ValueError(f"highlight at y={y} is no {what} row; is that menu open?")
    return row


@dataclass(frozen=True)
class Menu:
    """A menu whose cursor row can be read back; `rows` names them by position.

    `count` reads a live row count, for menus that vary (NPC option lists).
    """

    name: str
    rows: tuple[str, ...]
    read: Callable[[Game], int]
    count: Callable[[Game], int] | None = None
    wrap: bool = True
    up: Button = "up"
    down: Button = "down"

    def row(self, s: Session) -> int:
        return self.read(s.game)

    def row_count(self, s: Session) -> int:
        n = self.count(s.game) if self.count else len(self.rows)
        if n <= 0:
            raise ValueError(f"{self.name} reports {n} rows; is it open?")
        return n

    def index(self, target: int | str) -> int:
        if isinstance(target, int):
            return target
        try:
            return self.rows.index(target)
        except ValueError:
            raise KeyError(f"{self.name} has no row {target!r}; rows: {self.rows}") from None

    def goto(
        self, s: Session, target: int | str, timeout: float = 15.0, window: float = 0.5
    ) -> None:
        """Move the cursor to `target`, a row index or name; each press gets `window` s to land."""
        want, rows = self.index(target), self.row_count(s)
        if not 0 <= want < rows:
            raise ValueError(f"{self.name}: row {want} is outside 0..{rows - 1}")
        deadline = s.now() + timeout
        ignored = 0
        while (row := self.row(s)) != want:
            if s.now() >= deadline:
                raise TimeoutError(f"{self.name}: cursor on row {row}, not {want}, at {timeout:g}s")
            s.press(self._button(row, want, rows), PRESS_FRAMES)
            try:
                s.wait(lambda: self.row(s) != row, window, f"{self.name} cursor to move")
                ignored = 0
            except TimeoutError:
                ignored += 1
                if ignored >= STUCK_PRESSES:
                    raise TimeoutError(
                        f"{self.name}: cursor stuck on row {row} after {ignored} presses"
                    ) from None

    def _button(self, row: int, want: int, rows: int) -> Button:
        delta = want - row
        if self.wrap and abs(delta) > rows // 2:
            delta -= rows if delta > 0 else -rows
        return self.down if delta > 0 else self.up


LANGUAGE_MENU = Menu(
    "language menu",
    ("english", "francais", "deutsch", "espanol", "italiano"),
    lambda g: g.language_cursor,
)

QUEST_RANK_MENU = Menu(
    "quest rank menu",
    ("1_star", "2_star", "3_star", "4_star", "5_star", "6_star", "urgent"),
    lambda g: g.quest_rank_cursor,
)
"""The elder's and the hall counters' rank list; a locked rank ignores cross."""

NPC_MENU = Menu("npc menu", (), lambda g: g.npc_menu.cursor, count=lambda g: g.npc_menu.row_count)
"""Every NPC's option list; its labels are not readable, so steer it by index."""

TRAVEL_MENU = Menu(
    "travel menu",
    ("house", "farm", "kitchen", "hall_offline", "hall_online", "training_school"),
    lambda g: g.travel_cursor,
)

_BOX_ROWS = ("put_in", "take_out", "combine", "equipment", "sort", "sell")
ITEM_BOX_MENU = Menu(
    "item box menu",
    _BOX_ROWS,
    lambda g: _highlight_row(
        g.box_menu_highlight, BOX_MENU_TOP, BOX_MENU_STEP, len(_BOX_ROWS), "item box"
    ),
)
"""The house's item box, as the German UI draws it; the hall's draws its highlight elsewhere,
and the draw memory moves with the language."""
