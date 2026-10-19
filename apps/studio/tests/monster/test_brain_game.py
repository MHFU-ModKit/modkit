# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Follow the game: the marks a monster's script state puts on a view, and the toggle's state."""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from mhfu import addresses as a
from mhfu.em.script import asm, inject
from mhfu.em.script.inject import Container, Head
from mhfu.files import Extracted
from mhfu.live.script import CONTEXTS, ScriptState
from mhfu.memory import Image, Space
from mhfu.views import layout
from mhfu_studio.monster import brain
from mhfu_studio.monster import brain_game as bg
from mhfu_studio.monster.brain import BrainView, Variant
from mhfu_studio.monster.brain_game import BLOCK_END, NEXT, PARKED, PLAYING, Mark

from .conftest import program

ENTITY = a.USER_RAM + 0x100000
H0, H1, IMAGE = 0x3000, 0x3100, a.USER_RAM + 0x200000
BASE, INT20, INT10 = CONTEXTS[0], CONTEXTS[1], CONTEXTS[2]


def state(
    pc: int | None = 0x1008,
    *,
    context: int = BASE,
    pair: tuple[int, int] = (3, 0),
    header: int = H0,
    parked: dict[int, int] | None = None,
    species: int = 75,
) -> ScriptState:
    """A monster running `context`, which resumes at `pc`, with the `parked` contexts' pcs."""
    pcs = [0, 0, 0, 0]
    if context in CONTEXTS:
        pcs[CONTEXTS.index(context)] = pc or 0
    for ctx, at in (parked or {}).items():
        pcs[CONTEXTS.index(ctx)] = at
    return ScriptState(
        ENTITY, species, header, (pcs[0], pcs[1], pcs[2], pcs[3]), context, 0, 0, 0, {},
        0, 0, 0, 0, 0, 0, 0, *pair,
    )  # fmt: skip


@pytest.fixture
def view() -> BrainView:
    """`program()`'s h0: its enter lines end where the next line starts."""
    return brain.view(program(), "h0")


@pytest.fixture
def heads() -> list[Variant]:
    p = program()
    p.headers()[1].va = H1
    return brain.variants(p)


def row(v: BrainView, text: str) -> int:
    (r,) = v.find(text)
    return r


def marked(
    view: BrainView, heads: list[Variant], s: ScriptState, box: Container | None = None
) -> bg.Follow:
    """A watch that has read a game with `s` in slot 2 (and `box` pushed) and placed `view`."""
    f = bg.Follow()
    f.set(True)
    f._read = bg.Read(view.row, {2: s}, box)
    f.place(view, heads)
    return f


# ---- the marks


def test_next_is_the_line_it_resumes_at_and_playing_the_enter_before(
    view: BrainView, heads: list[Variant]
) -> None:
    f = marked(view, heads, state(0x1008))
    call, enter = row(view, "call 0"), row(view, "enter 3 0 0")
    assert f.marks == {call: (Mark(NEXT, ""),), enter: (Mark(PLAYING, ""),)}
    assert f.next_row == call and f.header == "h0" and f.status == "following · slot 2"


def test_playing_says_what_plays_instead_of_the_move_asked_for(
    view: BrainView, heads: list[Variant]
) -> None:
    other = row(view, "enter 0 3 0")
    view.labels[other] = "anim 9"
    f = marked(view, heads, state(0x1008, pair=(0, 3)))
    assert f.marks[row(view, "enter 3 0 0")] == (Mark(PLAYING, "plays 0 3 instead (anim 9)"),)
    f = marked(view, heads, state(0x1008, pair=(9, 9)))
    assert f.marks[row(view, "enter 3 0 0")] == (Mark(PLAYING, "plays 9 9 instead"),)


def test_no_playing_where_no_enter_ended(view: BrainView, heads: list[Variant]) -> None:
    start = row(view, "if_latch")
    f = marked(view, heads, state(0x1000))  # a context that has not entered anything yet
    assert f.marks == {start: (Mark(NEXT, ""),)}


def test_parked_contexts_are_marked_and_labelled(view: BrainView, heads: list[Variant]) -> None:
    where = row(view, "enter 1 4 0")
    parked = {INT20: 0x1010, INT10: 0x1010, CONTEXTS[3]: 0}  # two on one line; one holds no pc
    f = marked(view, heads, state(0x1008, parked=parked))
    assert f.marks[where] == (Mark(PARKED, "parked in interrupt 0x20, interrupt 0x10"),)
    assert set(f.marks) == {row(view, "call 0"), row(view, "enter 3 0 0"), where}


def test_a_parked_pc_off_the_view_is_left_out(view: BrainView, heads: list[Variant]) -> None:
    f = marked(view, heads, state(0x1008, parked={INT20: 0x9999}))
    assert PARKED not in {m.kind for ms in f.marks.values() for m in ms}


def test_a_running_interrupt_resumes_by_its_own_pc(view: BrainView, heads: list[Variant]) -> None:
    f = marked(view, heads, state(0x1008, context=INT20, parked={BASE: 0x1010}))
    assert f.next_row == row(view, "call 0")
    assert f.marks[row(view, "enter 1 4 0")] == (Mark(PARKED, "parked in the base routine"),)


def test_a_line_that_is_next_and_parked_shows_next_first(
    view: BrainView, heads: list[Variant]
) -> None:
    f = marked(view, heads, state(0x1008, parked={INT20: 0x1008}))
    assert [m.kind for m in f.marks[row(view, "call 0")]] == [NEXT, PARKED]


def test_the_end_of_a_block_answers_with_its_opener(view: BrainView, heads: list[Variant]) -> None:
    opener = row(view, "if_latch")
    view.closes[0x1100] = opener
    f = marked(view, heads, state(0x1100))
    assert f.marks[opener] == (Mark(NEXT, BLOCK_END),) and f.next_row == opener


def test_a_pc_no_line_has_is_said(view: BrainView, heads: list[Variant]) -> None:
    f = marked(view, heads, state(0x7777))
    assert f.marks == {} and f.next_row is None and f.header == "h0"
    assert f.status == f"following · slot 2 · {bg.NOT_SHOWN}"


def test_a_context_with_no_pc_is_said(view: BrainView, heads: list[Variant]) -> None:
    f = marked(view, heads, state(0x1008, context=0x08))
    assert f.next_row is None and f.status.endswith(bg.NO_PC)


def test_a_monster_on_another_header_names_it(view: BrainView, heads: list[Variant]) -> None:
    f = marked(view, heads, state(0x1008, header=H1))
    assert f.header == "h1" and f.marks == {} and f.status == "following · slot 2"
    other = brain.view(program(), "h1")
    f.place(other, heads)
    assert f.next_row is not None and f.next_row == other.at[0x1008]


def test_a_header_no_variant_has_is_said(view: BrainView, heads: list[Variant]) -> None:
    f = marked(view, heads, state(0x1008, header=0x4444))
    assert f.header is None and f.status == f"following · slot 2 · {bg.NOT_SHOWN}"


def test_the_first_of_several_monsters_is_followed(view: BrainView, heads: list[Variant]) -> None:
    f = bg.Follow()
    f.set(True)
    f._read = bg.Read(75, {5: state(0x1000), 3: state(0x1008)}, None)
    f.place(view, heads)
    assert f.next_row == row(view, "call 0") and f.status == "following · slot 3 · first of 2"


# ---- a pushed container


def container(*, origin: tuple[tuple[int, int], ...]) -> Container:
    """A container at `IMAGE` with its header at offset 0x10 and `origin` as its native places."""
    head = Head(75, 0, IMAGE, (inject.HEAD, 0x80), (0, 0), (0, 0), (0, 0), (0, 0))
    return Container(head, bytes(0x80), (), ((H0, 0x10),), origin, ())


PUSHED = ((0x1008, 0x24), (0x1004, 0x20), (0x1010, 0x30))


def test_a_pushed_monster_is_mapped_through_the_container(
    view: BrainView, heads: list[Variant]
) -> None:
    box = container(origin=PUSHED)
    s = state(IMAGE + 0x24, header=IMAGE + 0x10, parked={INT20: IMAGE + 0x30})
    f = marked(view, heads, s, box)
    assert f.next_row == row(view, "call 0") and f.status == "following · slot 2"
    assert f.marks[row(view, "enter 3 0 0")] == (Mark(PLAYING, ""),)
    assert f.marks[row(view, "enter 1 4 0")] == (Mark(PARKED, "parked in interrupt 0x20"),)


def test_a_pushed_pc_with_no_native_place_is_said(view: BrainView, heads: list[Variant]) -> None:
    box = container(origin=PUSHED)
    f = marked(view, heads, state(IMAGE + 0x60, header=IMAGE + 0x10), box)
    assert f.marks == {} and f.next_row is None and f.header == "h0"
    assert f.status == f"following · slot 2 · {bg.PUSHED}"


def test_a_pushed_header_the_view_lacks_is_said(view: BrainView, heads: list[Variant]) -> None:
    head = Head(75, 0, IMAGE, (inject.HEAD, 0x80), (0, 0), (0, 0), (0, 0), (0, 0))
    box = Container(head, bytes(0x80), (), ((0x5555, 0x10),), PUSHED, ())
    f = marked(view, heads, state(IMAGE + 0x24, header=IMAGE + 0x10), box)
    assert f.header is None and f.status == f"following · slot 2 · {bg.PUSHED}"


def test_a_container_no_monster_runs_changes_nothing(view: BrainView, heads: list[Variant]) -> None:
    box = container(origin=PUSHED)
    f = marked(view, heads, state(0x1008), box)  # the monster runs the game's own script
    assert f.next_row == row(view, "call 0") and f.status == "following · slot 2"


# ---- the toggle's state


class Game:
    """What `Follow.poll` asks: a clock, and a reader that answers each ask at once."""

    def __init__(self, view: BrainView, heads: list[Variant]) -> None:
        self.t = 0.0
        self.asked: list[tuple[float, int]] = []
        self.view, self.heads = view, heads
        self.answer: Callable[[], bg.Read] = lambda: bg.Read(75, {2: state()}, None)
        self.follow = bg.Follow(clock=lambda: self.t)
        self.follow.set(True)

    def ask(self, row: int, known: Container | None = None) -> Future[bg.Read]:
        self.asked.append((self.t, row))
        f: Future[bg.Read] = Future()
        try:
            f.set_result(self.answer())
        except Exception as e:
            f.set_exception(e)
        return f

    def poll(self, running: bool = True, dt: float = 0.0) -> bool:
        self.t += dt
        return self.follow.poll(self.view, self.heads, running, self.ask)


@pytest.fixture
def game(view: BrainView, heads: list[Variant]) -> Game:
    return Game(view, heads)


def test_nothing_is_asked_while_off(view: BrainView, heads: list[Variant]) -> None:
    g = Game(view, heads)
    g.follow.set(False)
    assert not g.poll() and g.asked == [] and g.follow.status == "" and g.follow.marks == {}


def test_turning_it_on_says_it_is_reading_and_the_first_poll_asks(game: Game) -> None:
    assert game.follow.status == bg.READING
    game.poll()
    assert game.asked == [(0.0, 75)]


def test_there_is_nothing_to_ask_about_before_a_view(game: Game) -> None:
    assert not game.follow.poll(None, [], True, game.ask) and game.asked == []
    assert game.follow.status == bg.READING


def test_the_marks_come_with_the_second_poll(game: Game) -> None:
    assert not game.poll()  # asks
    assert game.poll(dt=1.0)  # takes it
    assert game.follow.next_row == row(game.view, "call 0")
    assert game.follow.status == "following · slot 2"


def test_no_game_running_is_said_and_nothing_is_asked(game: Game) -> None:
    assert game.poll(running=False) and game.follow.status == bg.NO_GAME and game.asked == []
    assert not game.poll(running=False, dt=5)


def test_a_game_that_starts_is_found(game: Game) -> None:
    game.poll(running=False)
    game.poll(dt=1.0)
    game.poll(dt=1.0)
    assert game.follow.status == "following · slot 2"


def test_it_asks_at_most_once_a_second(game: Game) -> None:
    for _ in range(100):
        game.poll(dt=0.1)
    times = [t for t, _ in game.asked]
    assert 9 <= len(times) <= 11
    assert all(b - a >= bg.PERIOD - 1e-9 for a, b in zip(times, times[1:], strict=False))


def test_a_read_that_is_not_done_is_waited_for_not_asked_again(game: Game) -> None:
    pending: Future[bg.Read] = Future()
    asked: list[float] = []

    def ask(row: int, known: Container | None = None) -> Future[bg.Read]:
        asked.append(game.t)
        return pending

    for k in range(30):
        game.t = k * 0.5
        game.follow.poll(game.view, game.heads, True, ask)
    assert asked == [0.0]
    pending.set_result(bg.Read(75, {2: state()}, None))
    game.t = 20.0
    assert game.follow.poll(game.view, game.heads, True, ask) and game.follow.marks


def test_no_such_monster_is_said_and_looked_for_again_only_after_a_while(game: Game) -> None:
    game.answer = lambda: bg.Read(75, {}, None)
    for _ in range(40):
        game.poll(dt=0.5)
    assert game.follow.status == "no Tigrex (em75) in the game" and game.follow.marks == {}
    times = [t for t, _ in game.asked]
    assert len(times) == 2 and times[1] - times[0] >= bg.RETRY
    game.answer = lambda: bg.Read(75, {2: state()}, None)
    game.t += bg.RETRY
    game.poll()
    game.poll(dt=1.0)
    assert game.follow.status == "following · slot 2"


def test_a_game_that_does_not_answer_is_said_and_asked_again_after_a_while(game: Game) -> None:
    game.poll()
    game.poll(dt=1.0)
    assert game.follow.marks

    def broken() -> bg.Read:
        raise OSError("connection refused")

    game.answer = broken
    game.poll(dt=1.0)
    game.poll(dt=1.0)
    assert game.follow.status == "the game did not answer: connection refused"
    assert game.follow.marks == {} and game.follow.next_row is None
    seen = len(game.asked)
    for _ in range(8):
        game.poll(dt=1.0)
    assert len(game.asked) == seen  # not before RETRY
    game.poll(dt=3.0)
    assert len(game.asked) == seen + 1


def test_a_read_of_another_row_marks_nothing(game: Game) -> None:
    game.answer = lambda: bg.Read(76, {2: state()}, None)
    game.poll()
    game.poll(dt=1.0)
    assert game.follow.marks == {} and game.follow.status == bg.READING


def test_poll_says_whether_what_the_tab_shows_changed(game: Game) -> None:
    assert game.poll() is False  # asked, nothing to show yet
    assert game.poll(dt=1.0) is True  # taken
    assert game.poll(dt=0.1) is False  # not due, nothing new
    game.answer = lambda: bg.Read(75, {2: state(0x1014)}, None)
    assert game.poll(dt=1.0) is False  # asks; the last read is not news
    assert game.poll(dt=1.0) is True  # the marks moved
    assert game.follow.next_row == row(game.view, "goto 1")


def test_off_forgets_everything_and_stops_asking(game: Game) -> None:
    game.poll()
    game.poll(dt=1.0)
    game.follow.set(False)
    n = len(game.asked)
    assert game.follow.marks == {} and game.follow.status == "" and game.follow.header is None
    assert not game.poll(dt=5) and len(game.asked) == n


def test_reset_keeps_the_toggle_and_drops_what_was_read(game: Game) -> None:
    game.poll()
    game.poll(dt=1.0)
    game.follow.reset()
    assert game.follow.on and game.follow.marks == {} and game.follow.status == bg.READING
    assert game.poll(dt=1.0) is False and game.asked[-1][1] == 75


def test_place_without_a_view_or_while_off_changes_nothing(
    view: BrainView, heads: list[Variant]
) -> None:
    f = bg.Follow()
    assert not f.place(view, heads) and f.status == ""
    f.set(True)
    assert not f.place(None, heads)


def test_every_kind_has_a_place_in_the_order() -> None:
    assert bg.KINDS == (NEXT, PLAYING, PARKED)


# ---- fetch: the game's memory


def entity(**fields: Any) -> bytes:
    raw = bytearray(0x800)
    for name, value in fields.items():
        field = getattr(a.ENTITY, name)
        fmt = layout(field.type)
        assert fmt is not None
        fmt.pack_into(raw, field, *(value if isinstance(value, tuple) else (value,)))
    return bytes(raw)


def world(*entities: bytes, pushed: bytes = b"") -> Space:
    """The entity registry (slot 1 on), an image per entity, and what is at SCRIPT_PUSH."""
    at = [a.USER_RAM + 0x10000 * (k + 1) for k in range(len(entities))]
    registry = struct.pack("<21I", 0, *at, *[0] * (20 - len(at)))
    images = [Image(registry, int(a.ENTITY_REGISTRY))]
    images += [Image(raw, where) for raw, where in zip(entities, at, strict=True)]
    images.append(Image(pushed.ljust(inject.PUSH_SIZE, b"\0"), int(a.SCRIPT_PUSH)))
    return Space(images)


class Attached:
    """What `attach()` is: a context manager over a fake session."""

    def __init__(self, mem: Space) -> None:
        self.mem = mem

    @contextmanager
    def __call__(self) -> Iterator[Attached]:
        yield self


def monster(species: int, **over: Any) -> bytes:
    fields = dict(
        SPECIES=species,
        SCRIPT_HEADER=a.USER_RAM + 0x9000,
        SCRIPT_PCS=(1, 2, 3, 4),
        SCRIPT_CONTEXT=0x20,
        MAIN_STATE=1,
        SUB_STATE=4,
    )
    return entity(**{**fields, **over})


def test_fetch_reads_the_monsters_of_the_row_with_their_pair() -> None:
    mem = world(monster(75), monster(76), entity(SPECIES=75), monster(75, SUB_STATE=7))
    got = bg.fetch(Attached(mem), 75)
    assert got.row == 75 and sorted(got.monsters) == [1, 4] and got.box is None
    assert got.monsters[1].pair == (1, 4) and got.monsters[4].pair == (1, 7)
    assert got.monsters[1].pc == 2  # context 0x20


def test_fetch_reads_nothing_more_when_no_monster_is_there() -> None:
    got = bg.fetch(Attached(world(monster(76))), 75)
    assert got.monsters == {} and got.box is None


def test_follow_hands_the_last_container_to_the_next_read(game: Game) -> None:
    p = program()
    box = Container.read(inject.container(p, asm.assemble(p), int(a.SCRIPT_PUSH) + 0x40))
    known: list[Container | None] = []
    game.answer = lambda: bg.Read(75, {2: state()}, box)
    real = game.ask

    def ask(row: int, last: Container | None = None) -> Future[bg.Read]:
        known.append(last)
        return real(row, last)

    game.ask = ask  # type: ignore[method-assign]
    game.poll()
    game.poll(dt=1.0)
    assert known == [None, box]


def test_fetch_brings_the_pushed_container() -> None:
    p = program()
    raw = inject.container(p, asm.assemble(p), int(a.SCRIPT_PUSH) + inject.IMAGE_OFF)
    got = bg.fetch(Attached(world(monster(75), pushed=raw)), 75)
    assert got.box is not None and got.box.row == 75


# ---- the real script


@pytest.fixture(scope="module")
def em75(mhfu_data: Path) -> BrainView:
    p = brain.decode(Extracted.find(mhfu_data), 75)
    return brain.view(p, brain.default_header(p))


def test_every_resume_address_of_em75_is_a_line_or_a_block_end(em75: BrainView) -> None:
    assert len(em75.ends) == len(em75.enters) > 100
    words = set()
    for pc, enter in em75.ends.items():
        found = bg.row_at(em75, pc)
        assert found is not None, hex(pc)
        assert em75.lines[enter].text.strip().startswith("enter")
        words.add(found[1])
    assert words == {"", BLOCK_END}


def test_em75_marks_whatever_it_resumes_at(em75: BrainView) -> None:
    head = brain.Variant("h0", 0x1234, (0,), "")
    for pc, enter in list(em75.ends.items())[::7]:
        s = state(pc, header=0x1234, pair=em75.enters[enter])
        where, marks = bg.marks_of(em75, s, lambda x: x)
        assert where is not None and Mark(PLAYING, "") in marks[enter]
        f = marked(em75, [head], s)
        assert f.next_row == where and f.header == "h0"


def test_em75_pushed_maps_back_to_its_lines(em75: BrainView, mhfu_data: Path) -> None:
    p = brain.decode(Extracted.find(mhfu_data), 75)
    box = Container.read(
        inject.container(p, asm.assemble(p), int(a.SCRIPT_PUSH) + inject.IMAGE_OFF)
    )
    (native,) = box.headers
    heads = [Variant("h0", native, (0,), "")]
    for pc, enter in list(em75.ends.items())[::5]:
        at = box.rebase(pc)
        assert at is not None
        s = state(at, header=box.headers[native], pair=em75.enters[enter])
        found = bg.row_at(em75, pc)
        f = marked(em75, heads, s, box)
        assert found is not None and f.next_row == found[0]
        assert Mark(PLAYING, "") in f.marks[enter]
