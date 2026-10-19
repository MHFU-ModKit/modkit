# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Follow the game: where the running monster's AI script stands, as marks on the Brain tab's lines.

`mhfu.live.script` reads a monster's script state; `Follow` is the state of the tab's toggle: it
asks for a read at most once a second (every memory request stalls the emulator about 10 ms) and
turns what it got into marks on the rows of the view shown. No toolkit.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, NamedTuple

from mhfu.em.script import inject
from mhfu.em.script.inject import Container
from mhfu.live import script
from mhfu.live.script import CONTEXTS, ScriptState
from ppsspp_debug import DebuggerError

from mhfu_studio.monster import species
from mhfu_studio.monster.brain import BrainView, Variant

if TYPE_CHECKING:
    from mhfu.live.session import Session

PERIOD, RETRY = 1.0, 10.0
"""Seconds: between two reads, and between two tries once the game has no such monster or did not
answer."""
NEXT, PLAYING, PARKED = "next", "playing", "parked"
KINDS = (NEXT, PLAYING, PARKED)
"""The kinds of mark, in the order a line shows them."""
NO_GAME = "no game running"
READING = "reading the game…"
PUSHED = "runs pushed code this view does not show"
NOT_SHOWN = "runs a script this view does not show"
NO_PC = "its running context holds no resume address"
BLOCK_END = "at the end of this block"
DOT = "·"

Native = Callable[[int], int | None]
"""A resume address of the monster's script as an address of the game's own script."""


class Mark(NamedTuple):
    kind: str
    """One of `KINDS`."""
    label: str
    """What to add after the line; empty for none."""


Marks = dict[int, tuple[Mark, ...]]


class Read(NamedTuple):
    """What one read of the game brought."""

    row: int
    monsters: dict[int, ScriptState]
    """The live monsters of the species row, by registry slot."""
    box: Container | None
    """The container at SCRIPT_PUSH, when there is one."""


def fetch(
    attach: Callable[[], AbstractContextManager[Session]], row: int, known: Container | None = None
) -> Read:
    """Attaches to the game and reads the monsters of species `row` and the container pushed into
    it (`known`, the last read's, is not read again). The seam the tests fake; it runs on a
    worker."""
    with attach() as s:
        found = {k: m for k, m in script.monsters(s.mem).items() if m.species == row}
        return Read(row, found, inject.pushed(s.mem, known) if found else None)


def context_words(context: int) -> str:
    return "the base routine" if context == CONTEXTS[0] else f"interrupt 0x{context:02X}"


def row_at(view: BrainView, at: int | None) -> tuple[int, str] | None:
    """The row of the line at the native address `at`, and a word when it is the end of a block
    (which has no line: it answers with its opener); None when the view has nothing there."""
    if at is None:
        return None
    if (row := view.at.get(at)) is not None:
        return row, ""
    if (row := view.closes.get(at)) is not None:
        return row, BLOCK_END
    return None


def instead(view: BrainView, pair: tuple[int, int]) -> str:
    """What the monster plays when it is not the move the script asked for, as an `enter` of it
    in this script names it."""
    named = next(
        (view.labels[r] for r, p in view.enters.items() if p == pair and r in view.labels), ""
    )
    return f"plays {pair[0]} {pair[1]} instead" + (f" ({named})" if named else "")


def marks_of(view: BrainView, state: ScriptState, native: Native) -> tuple[int | None, Marks]:
    """The row the running context resumes at, and every mark of `state` on `view`: NEXT where it
    resumes, PLAYING on the `enter` that saved that address (labelled when the monster plays
    another move), PARKED where each other context waits."""
    out: dict[int, list[Mark]] = {}
    here = None if state.pc is None else native(state.pc)
    found = row_at(view, here)
    if here is not None and found is not None:
        out.setdefault(found[0], []).append(Mark(NEXT, found[1]))
        if (played := view.ends.get(here)) is not None:
            label = "" if view.enters[played] == state.pair else instead(view, state.pair)
            out.setdefault(played, []).append(Mark(PLAYING, label))
    parked: dict[int, list[int]] = {}
    for context, pc in zip(CONTEXTS, state.pcs, strict=True):
        if context != state.context and (where := row_at(view, native(pc))) is not None:
            parked.setdefault(where[0], []).append(context)
    for row, contexts in parked.items():
        words = ", ".join(context_words(c) for c in contexts)
        out.setdefault(row, []).append(Mark(PARKED, f"parked in {words}"))
    ordered = {r: tuple(sorted(ms, key=lambda m: KINDS.index(m.kind))) for r, ms in out.items()}
    return (None if found is None else found[0]), ordered


class Follow:
    """The state of Follow the game. `marks` (a row's marks, in `KINDS` order) and `status` say
    what the tab shows; `header` is the header the monster runs, which the view shown should be,
    and `next_row` the line it resumes at."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.on = False
        self.status = ""
        self.marks: Marks = {}
        self.header: str | None = None
        self.next_row: int | None = None
        self._read: Read | None = None
        self._why = ""
        self._job: Future[Read] | None = None
        self._asked = self._lost = float("-inf")

    def set(self, on: bool) -> None:
        self.on = on
        self.reset()

    def reset(self) -> None:
        """Forgets what was read: another script, or the toggle changed."""
        self._read, self._job, self._why = None, None, ""
        self.marks, self.header, self.next_row = {}, None, None
        self.status = READING if self.on else ""
        self._asked = self._lost = float("-inf")

    def poll(
        self,
        view: BrainView | None,
        heads: Sequence[Variant],
        running: bool,
        ask: Callable[[int, Container | None], Future[Read]],
    ) -> bool:
        """Takes a finished read, and asks for the next when `PERIOD` has passed (`ask` runs it
        on a worker, told the species row and the container read last). True when what the tab
        shows changed."""
        if not self.on or view is None:
            return False
        before = self._shown()
        if self._job is not None and not self._job.done():
            return False
        if self._job is not None:
            job, self._job = self._job, None
            self._took(job)
        now = self.clock()
        if not running:
            self.reset()
            self._why = NO_GAME
        else:
            if self._why == NO_GAME:
                self._why = ""
            if now - self._asked >= PERIOD and now - self._lost >= RETRY:
                self._asked = now
                self._job = ask(view.row, None if self._read is None else self._read.box)
        self._place(view, heads)
        return before != self._shown()

    def place(self, view: BrainView | None, heads: Sequence[Variant]) -> bool:
        """Marks `view` from what was read last, for a view or header that came after the read.
        True when what the tab shows changed."""
        before = self._shown()
        if self.on and view is not None:
            self._place(view, heads)
        return before != self._shown()

    def _shown(self) -> tuple[object, ...]:
        return self.marks, self.status, self.header, self.next_row

    def _took(self, job: Future[Read]) -> None:
        try:
            got = job.result()
        except (LookupError, OSError, ValueError, DebuggerError) as e:
            self._read, self._why = None, f"the game did not answer: {e}"
            self._lost = self.clock()
            return
        self._read, self._why = got, ""
        if not got.monsters:
            self._lost = self.clock()

    def _place(self, view: BrainView, heads: Sequence[Variant]) -> None:
        self.marks, self.header, self.next_row = {}, None, None
        got = self._read
        if self._why:
            self.status = self._why
        elif got is None or got.row != view.row:
            self.status = READING
        elif not got.monsters:
            self.status = f"no {species.label(got.row)} in the game"
        else:
            self._follow(view, heads, got)

    def _follow(self, view: BrainView, heads: Sequence[Variant], got: Read) -> None:
        slot = min(got.monsters)
        state = got.monsters[slot]
        runs = got.box is not None and state.header in got.box.head.addresses
        box = got.box if runs else None
        who = f"following {DOT} slot {slot}"
        if len(got.monsters) > 1:
            who += f" {DOT} first of {len(got.monsters)}"
        native: Native = (lambda pc: pc) if box is None else box.native
        header = native(state.header)
        want = next((h.header for h in heads if h.va is not None and h.va == header), None)
        why = NOT_SHOWN if box is None else PUSHED
        if want is None:
            self.status = f"{who} {DOT} {why}"
            return
        self.header, self.status = want, who
        if view.header == want:  # else the tab switches to it next
            self.next_row, self.marks = marks_of(view, state, native)
            if self.next_row is None:
                self.status += f" {DOT} {NO_PC if state.pc is None else why}"
