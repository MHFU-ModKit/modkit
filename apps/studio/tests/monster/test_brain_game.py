# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Follow the game: the marks a monster's script state puts on a view, and the toggle's state."""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from mhfu import addresses as a
from mhfu.em.script import asm, edit, graph, inject, ir
from mhfu.em.script.build import Built
from mhfu.em.script.editset import Anchor, EditSet, Insert
from mhfu.em.script.inject import Container, Head
from mhfu.files import Extracted
from mhfu.live.script import CONTEXTS, ScriptState, Step, Trace, slot
from mhfu.memory import Image, Memory, Space
from mhfu.views import layout
from mhfu_studio.monster import brain
from mhfu_studio.monster import brain_game as bg
from mhfu_studio.monster.brain import BrainView, Variant
from mhfu_studio.monster.brain_game import BLOCK_END, NEXT, PARKED, PLAYING, Mark
from ppsspp_debug import DebuggerError

from .conftest import program

ENTITY = a.USER_RAM + 0x100000
H0, H1, IMAGE = 0x3000, 0x3100, a.USER_RAM + 0x200000
BASE, INT20, INT10 = CONTEXTS[0], CONTEXTS[1], CONTEXTS[2]


def state(
    pc: int = 0x1008,
    *,
    context: int = BASE,
    pair: tuple[int, int] = (3, 0),
    header: int = H0,
    parked: dict[int, int] | None = None,
    species: int = 75,
) -> ScriptState:
    """A monster running `context`, which resumes at `pc`, with the `parked` contexts' pcs."""
    pcs = [0, 0, 0, 0]
    pcs[slot(context)] = pc
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
    view: BrainView,
    heads: list[Variant],
    s: ScriptState,
    box: Container | None = None,
    built: Built | None = None,
) -> bg.Follow:
    """A watch that has read a game with `s` in slot 2 (and `box` pushed) and placed `view`."""
    f = bg.Follow()
    f.set(True)
    f._read = bg.Read(view.row, {2: s}, box)
    f.place(view, heads, built)
    return f


# ---- the marks


def test_next_is_the_line_it_resumes_at_and_playing_the_enter_before(
    view: BrainView, heads: list[Variant]
) -> None:
    f = marked(view, heads, state(0x1008))
    call, enter = row(view, "call 0"), row(view, "enter 3 0 0")
    assert f.marks == {call: (Mark(NEXT, ""),), enter: (Mark(PLAYING, ""),)}
    assert f.next_row == call and f.header == "h0" and f.status == "following · slot 2"
    assert f.next_node == view.lines[call].node and f.target == bg.Target(
        ENTITY, bg.NATIVE, None, None
    )


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


def test_an_interrupt_without_a_slot_resumes_at_the_base_pc(
    view: BrainView, heads: list[Variant]
) -> None:
    f = marked(view, heads, state(0x1008, context=0x08, parked={INT20: 0x1010}))
    assert f.next_row == row(view, "call 0")
    assert f.marks[row(view, "enter 1 4 0")] == (Mark(PARKED, "parked in interrupt 0x20"),)


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


def world(*entities: bytes, pushed: bytes = b"", staged: tuple[int, bytes] | None = None) -> Space:
    """The entity registry (slot 1 on), an image per entity, what is at SCRIPT_PUSH, and a
    container `staged` at an address (page aligned)."""
    at = [a.USER_RAM + 0x10000 * (k + 1) for k in range(len(entities))]
    registry = struct.pack("<21I", 0, *at, *[0] * (20 - len(at)))
    images = [Image(registry, int(a.ENTITY_REGISTRY))]
    images += [Image(raw, where) for raw, where in zip(entities, at, strict=True)]
    images.append(Image(pushed.ljust(inject.PUSH_SIZE, b"\0"), int(a.SCRIPT_PUSH)))
    if staged is not None:
        base, raw = staged
        images.append(Image(raw.ljust(-(-len(raw) // 0x1000) * 0x1000, b"\0"), base))
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


# ---- an edited build


class Rig(NamedTuple):
    """`program()` with an `enter` put first in r_a (a node with no native address), built and
    loaded at `box.load`, and the view and headers of the build."""

    built: Built
    box: Container
    view: BrainView
    heads: list[Variant]

    @property
    def image(self) -> asm.Image:
        assert self.built.image is not None
        return self.built.image

    def at(self, nid: str) -> int:
        """The live address of a node."""
        return self.box.load + self.image.nodes[nid][0]

    def after(self, nid: str) -> int:
        """Where the run resumes when `nid` is an `enter`."""
        off, n = self.image.nodes[nid]
        return self.box.load + off + n

    @property
    def header(self) -> int:
        return self.box.load + self.image.items["h0"][0]


def make_rig(load: int = IMAGE, sub: int = 2, *, es: EditSet | None = None) -> Rig:
    p = program()
    if es is None:
        es = EditSet.of(p)
        es.inserts["u1"] = Insert(Anchor("before", "n0"), [edit.insn("enter", 1, sub, 0)])
    built = Built.of(p, es)
    assert built.ok and built.image is not None
    raw = inject.container(built.program, built.image, load, origin=False)
    view = brain.view(built.program, "h0")
    return Rig(built, Container.read(raw), view, brain.variants(built.program))


@pytest.fixture
def rig() -> Rig:
    return make_rig()


def edited(r: Rig, s: ScriptState, box: Container | None = None) -> bg.Follow:
    return marked(r.view, r.heads, s, box or r.box, r.built)


def test_an_edited_build_is_marked_by_node(rig: Rig) -> None:
    f = edited(rig, state(rig.after("u1.0"), pair=(1, 2), header=rig.header))
    opener, new = row(rig.view, "if_latch"), rig.view.row_of["u1.0"]
    assert f.marks == {opener: (Mark(NEXT, ""),), new: (Mark(PLAYING, ""),)}
    assert f.next_row == opener and f.next_node == "n0" and f.header == "h0"
    assert f.status == "following · slot 2"
    assert f.target == bg.Target(ENTITY, bg.BUILD, rig.built.id, rig.box.load)


def test_a_node_of_the_game_in_an_edited_build_is_marked(rig: Rig) -> None:
    f = edited(rig, state(rig.at("n2"), parked={INT20: rig.at("n4")}, header=rig.header))
    assert f.next_row == row(rig.view, "call 0") and f.next_node == "n2"
    assert f.marks[row(rig.view, "enter 3 0 0")] == (Mark(PLAYING, ""),)
    assert f.marks[row(rig.view, "enter 1 4 0")] == (Mark(PARKED, "parked in interrupt 0x20"),)


def test_an_edited_playing_says_what_plays_instead(rig: Rig) -> None:
    f = edited(rig, state(rig.after("u1.0"), pair=(9, 9), header=rig.header))
    assert f.marks[rig.view.row_of["u1.0"]] == (Mark(PLAYING, "plays 9 9 instead"),)


def test_the_end_of_a_block_in_an_edited_build_answers_with_its_opener(rig: Rig) -> None:
    close = rig.box.load + rig.image.closes["n0"]
    f = edited(rig, state(close, pair=(1, 4), header=rig.header))
    opener, enter = row(rig.view, "if_latch"), row(rig.view, "enter 1 4 0")
    assert f.marks == {opener: (Mark(NEXT, BLOCK_END),), enter: (Mark(PLAYING, ""),)}
    assert f.next_node == "n0"


def test_an_address_the_build_has_no_node_at_is_said(rig: Rig) -> None:
    for pc in (rig.at("n2") + 1, rig.box.load + 0x9000, 0):
        f = edited(rig, state(pc, header=rig.header))
        assert f.marks == {} and f.next_row is None and f.next_node is None
        assert f.header == "h0" and f.status == f"following · slot 2 · {bg.NOT_SHOWN}"


def test_an_edited_build_on_another_header_names_it(rig: Rig) -> None:
    other = rig.box.load + rig.image.items["h1"][0]
    f = edited(rig, state(rig.at("n10"), header=other))
    assert f.header == "h1" and f.marks == {} and f.status == "following · slot 2"
    f.place(brain.view(rig.built.program, "h1"), rig.heads, rig.built)
    assert f.next_node == "n10"


def test_a_header_the_build_lacks_is_said(rig: Rig) -> None:
    f = edited(rig, state(rig.at("n2"), header=rig.box.load + rig.image.items["r_a"][0]))
    assert f.header is None and f.status == f"following · slot 2 · {bg.NOT_SHOWN}"


def test_another_build_gets_no_marks_and_a_sentence(rig: Rig) -> None:
    other = make_rig(sub=3)
    assert other.built.id != rig.built.id
    f = edited(rig, state(other.after("u1.0"), header=other.header), other.box)
    assert f.marks == {} and f.next_row is None and f.header is None
    say = f"runs another build of your edits (id {other.built.id:08X}): Send or Push"
    assert f.status == f"following · slot 2 · {say}"
    assert f.target == bg.Target(ENTITY, bg.OTHER, other.built.id, other.box.load)


def test_a_rebuild_makes_the_running_build_another(rig: Rig) -> None:
    f = edited(rig, state(rig.after("u1.0"), header=rig.header))
    assert f.marks
    newer = make_rig(sub=3)
    assert f.place(newer.view, newer.heads, newer.built)
    assert f.marks == {} and "runs another build of your edits" in f.status


def test_the_games_own_script_gets_no_marks_beside_a_build(rig: Rig) -> None:
    f = marked(rig.view, rig.heads, state(0x1008), None, rig.built)  # a header of the game's
    assert f.marks == {} and f.header is None and f.status == f"following · slot 2 · {bg.OWN}"
    assert f.target == bg.Target(ENTITY, bg.NATIVE, None, None)


def test_a_script_nothing_names_is_not_shown(rig: Rig) -> None:
    f = marked(rig.view, rig.heads, state(0x1008, header=0x4444), None, rig.built)
    assert f.status == f"following · slot 2 · {bg.NOT_SHOWN}"
    assert f.target == bg.Target(ENTITY, bg.OTHER, None, None)


def test_edits_that_do_not_build_have_nothing_to_compare(rig: Rig) -> None:
    p = program()
    bad = EditSet.of(p)
    bad.inserts["u1"] = Insert(Anchor("after", "n5"), [edit.insn("enter", 1, 2, 0)])
    built = Built.of(p, bad)
    assert built.id is None
    f = marked(rig.view, rig.heads, state(rig.at("n2"), header=rig.header), rig.box, built)
    assert f.marks == {} and f.status == f"following · slot 2 · {bg.NOT_BUILT}"


def test_a_quest_load_replacing_a_push_with_the_sticks_build_is_said() -> None:
    push = int(a.SCRIPT_PUSH) + inject.IMAGE_OFF
    mine, stick = make_rig(push), make_rig(IMAGE, sub=3)
    f = edited(mine, state(mine.after("u1.0"), header=mine.header))
    assert f.marks and f.target is not None and f.target.runs == bg.BUILD
    f._read = bg.Read(75, {2: state(stick.after("u1.0"), header=stick.header)}, stick.box)
    f.place(mine.view, mine.heads, mine.built)
    say = f"a quest load replaced your push with the stick's build (id {stick.built.id:08X})"
    assert f.marks == {} and f.status == f"following · slot 2 · {say}: Push again"
    f._read = bg.Read(75, {2: state(0x1008)}, None)
    f.place(mine.view, mine.heads, mine.built)
    own = bg.replaced("the game's own script")
    assert f.status == f"following · slot 2 · {own}"


def test_another_push_is_another_build_not_a_quest_load() -> None:
    push = int(a.SCRIPT_PUSH) + inject.IMAGE_OFF
    mine, newer = make_rig(push), make_rig(push, sub=3)
    f = edited(mine, state(mine.after("u1.0"), header=mine.header))
    f._read = bg.Read(75, {2: state(newer.after("u1.0"), header=newer.header)}, newer.box)
    f.place(mine.view, mine.heads, mine.built)
    assert newer.built.id is not None
    assert f.status.endswith(bg.other_build(newer.built.id))


def test_a_build_that_never_ran_is_not_replaced(rig: Rig) -> None:
    f = marked(rig.view, rig.heads, state(0x1008), None, rig.built)
    assert f.status.endswith(bg.OWN)


def test_a_build_unseen_after_its_rebuild_is_just_another() -> None:
    push = int(a.SCRIPT_PUSH) + inject.IMAGE_OFF
    mine, newer = make_rig(push), make_rig(IMAGE, sub=3)
    f = edited(mine, state(mine.after("u1.0"), header=mine.header))
    f.place(newer.view, newer.heads, newer.built)  # edited since: its id is not the pushed one
    f._read = bg.Read(75, {2: state(0x1008)}, None)
    f.place(newer.view, newer.heads, newer.built)
    assert f.status.endswith(bg.OWN)


def test_reset_forgets_the_target_and_what_was_seen(rig: Rig) -> None:
    f = edited(rig, state(rig.after("u1.0"), header=rig.header))
    f.seen[1] = None
    f.reset()
    assert f.target is None and f.next_node is None and f.seen == {}


# ---- fetch finds the container around a header


def staged(r: Rig, base: int) -> bytes:
    """`r`'s container as it sits at `base`: the image follows the head."""
    return inject.container(r.built.program, r.image, base + inject.HEAD, origin=False)


STAGE = a.USER_RAM + 0x300000


def test_fetch_with_seen_finds_a_staged_container_and_keeps_it() -> None:
    r = make_rig(STAGE + inject.HEAD)
    seen: bg.Seen = {}
    mem = world(monster(75, SCRIPT_HEADER=r.header), staged=(STAGE, staged(r, STAGE)))
    got = bg.fetch(Attached(mem), 75, seen=seen)
    assert got.box is not None and got.box.head.id == r.built.id and seen[r.header] is got.box
    assert bg.fetch(Attached(mem), 75, seen=seen).box is got.box  # its head read the same


def test_fetch_without_seen_looks_at_the_push_only() -> None:
    r = make_rig(STAGE + inject.HEAD)
    mem = world(monster(75, SCRIPT_HEADER=r.header), staged=(STAGE, staged(r, STAGE)))
    assert bg.fetch(Attached(mem), 75).box is None


def test_a_miss_is_remembered_so_the_game_is_not_scanned_each_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    real = inject.around

    def spy(mem: Memory, header: int, known: Container | None = None) -> Container | None:
        calls.append(header)
        return real(mem, header, known)

    monkeypatch.setattr(inject, "around", spy)
    seen: bg.Seen = {}
    mem = world(monster(75))
    for _ in range(3):
        assert bg.fetch(Attached(mem), 75, seen=seen).box is None
    header = a.USER_RAM + 0x9000
    assert calls == [header] and seen == {header: None}


def test_a_miss_in_the_push_area_is_not_remembered() -> None:
    header = int(a.SCRIPT_PUSH) + 0x100
    seen: bg.Seen = {}
    mem = world(monster(75, SCRIPT_HEADER=header))
    assert bg.locate(mem, header, seen) is None and seen == {}


def test_a_pushed_container_is_found_by_its_header_alone() -> None:
    push = int(a.SCRIPT_PUSH) + inject.IMAGE_OFF
    r = make_rig(push)
    raw = inject.container(r.built.program, r.image, push)
    mem = world(monster(75, SCRIPT_HEADER=r.header), pushed=raw)
    got = bg.fetch(Attached(mem), 75, seen={})
    assert got.box is not None and got.box.head.id == r.built.id


def test_follow_keeps_its_seen_for_the_reader(game: Game) -> None:
    assert game.follow.seen == {}
    game.follow.seen[5] = None
    game.follow.reset()
    assert game.follow.seen == {}


# ---- the toggle waits for a count


def test_a_held_follow_asks_nothing_and_takes_nothing(game: Game) -> None:
    game.poll()
    game.follow.hold(True)
    asked = len(game.asked)
    assert not game.poll(dt=5.0) and len(game.asked) == asked and game.follow.paused


def test_a_released_follow_reads_again_at_once(game: Game) -> None:
    game.poll()
    game.follow.hold(True)
    game.poll(dt=0.1)
    game.follow.hold(False)
    asked = len(game.asked)
    game.poll(dt=0.1)
    assert len(game.asked) == asked + 1
    game.follow.hold(False)  # nothing to undo


# ---- count


@pytest.fixture
def follow() -> bg.Follow:
    f = bg.Follow()
    f.set(True)
    return f


class Run:
    """The function the workspace gives Count: what it was asked, and the future it answers."""

    def __init__(self) -> None:
        self.asked: list[tuple[int, float]] = []
        self.job: Future[Trace] = Future()

    def __call__(self, entity: int, seconds: float) -> Future[Trace]:
        self.asked.append((entity, seconds))
        return self.job


def steps(at: dict[int, int]) -> tuple[Step, ...]:
    """`n` instructions at each address."""
    return tuple(Step(t, pc, 0) for pc, n in at.items() for t in range(n))


def trace(at: dict[int, int], dropped: int = 0, seconds: float = 30.0) -> Trace:
    return Trace(ENTITY, seconds, steps(at), dropped)


def target(runs: str = bg.NATIVE, id_: int | None = None, load: int | None = None) -> bg.Target:
    return bg.Target(ENTITY, runs, id_, load)


class Counting(NamedTuple):
    """r0: an if with an empty else, a switch with an empty default, one with no default, a
    random with an empty last branch, a random with two. r1 runs twice, r2 never."""

    p: ir.Program
    first: ir.Insn
    cond: ir.Block
    then: ir.Insn
    sw: ir.Block
    cases: tuple[ir.Insn, ir.Insn]
    sw2: ir.Block
    case: ir.Insn
    rnd: ir.Block
    picks: tuple[ir.Insn, ir.Insn]
    rnd2: ir.Block
    r1: ir.Routine

    def pc(self, n: ir.Node | ir.Arm) -> int:
        assert n.pc is not None
        return n.pc


def counting() -> Counting:
    first, then = edit.insn("enter", 1, 0, 0), edit.insn("enter", 2, 0, 0)
    cond = edit.if_("if_latch", then=[then], else_=[])
    cases = (edit.insn("enter", 3, 0, 0), edit.insn("enter", 3, 1, 0))
    sw = edit.switch("switch_hp", cases=[(30, [cases[0]]), (60, [cases[1]])], default=[])
    case = edit.insn("enter", 4, 0, 0)
    sw2 = edit.switch("switch_hp", cases=[(30, [case])])
    picks = (edit.insn("enter", 5, 0, 0), edit.insn("enter", 5, 1, 0))
    rnd = edit.random([(16, [picks[0]]), (8, [picks[1]])])  # the rest, 8, is an empty branch
    rnd2 = edit.random([(8, [])])
    last = edit.insn("enter", 6, 0, 0)
    r0 = ir.Routine("r0", [first, cond, sw, sw2, rnd, rnd2, last], va=0x1000)
    r1 = ir.Routine("r1", [edit.insn("enter", 7, 0, 0), edit.insn("enter", 7, 1, 0)], va=0x1200)
    r2 = ir.Routine("r2", [edit.insn("enter", 8, 0, 0)], va=0x1400)
    p = ir.Program(75, 0x1000, [r0, r1, r2], 0x1800)
    for k, n in enumerate(p.nodes()):
        n.id, n.pc = f"x{k}", 0x1000 + 4 * k
    return Counting(p, first, cond, then, sw, cases, sw2, case, rnd, picks, rnd2, r1)


def counted(c: Counting, *ran: tuple[ir.Node | ir.Arm, int], dropped: int = 0) -> bg.Counted:
    """What a native count of `c` comes to when each node ran as many times as `ran` says."""
    t = trace({c.pc(n): k for n, k in ran}, dropped)
    return bg.tally(t, bg.Basis(c.p, None))


def port(c: Counting, b: ir.Block, k: int = 0) -> str:
    """The key of port `k` of a block: its head if it has one, then its arms."""
    return b.arms[k].id if b.arms and not (k == 0 and b.head) else bg.head_key(b.id)


def test_nodes_are_counted_by_id_and_the_rest_never_ran() -> None:
    c = counting()
    got = counted(c, (c.first, 4), (c.r1.body[0], 2))
    assert got.nodes == {c.first.id: 4, c.r1.body[0].id: 2}
    assert c.cond.id in got.never and c.r1.body[1].id in got.never
    assert c.first.id not in got.never and got.steps == 6 and got.outside == 0
    assert got.id is None and got.entity == ENTITY and got.seconds == 30.0


def test_routines_total_their_instructions_and_their_starts() -> None:
    c = counting()
    got = counted(c, (c.first, 4), (c.cond, 4), (c.then, 3), (c.r1.body[0], 2), (c.r1.body[1], 2))
    assert got.routines == {"r0": bg.Total(4, 11), "r1": bg.Total(2, 4)}


def test_an_if_arm_with_a_body_is_the_count_of_its_first_node_and_the_empty_one_the_rest() -> None:
    c = counting()
    got = counted(c, (c.cond, 10), (c.then, 6))
    assert got.taken[graph.head_key(c.cond.id)] == 6
    assert got.taken[c.cond.arms[0].id] == 4


def test_an_if_with_an_empty_then_has_the_else_by_its_body() -> None:
    p = ir.Program(75, 0x1000, [], 0x1800)
    x = edit.insn("enter", 2, 0, 0)
    cond = edit.if_("if_latch", then=[], else_=[x])
    p.items.append(ir.Routine("r", [cond], va=0x1000))
    for k, n in enumerate(p.nodes()):
        n.id, n.pc = f"y{k}", 0x1000 + 4 * k
    got = bg.tally(trace({cond.pc or 0: 9, x.pc or 0: 2}), bg.Basis(p, None))
    assert got.taken == {graph.head_key(cond.id): 7, cond.arms[0].id: 2}


def test_an_if_with_no_else_has_an_implicit_port_of_the_rest() -> None:
    p = ir.Program(75, 0x1000, [], 0x1800)
    x = edit.insn("enter", 2, 0, 0)
    cond = edit.if_("if_latch", then=[x])
    p.items.append(ir.Routine("r", [cond], va=0x1000))
    for k, n in enumerate(p.nodes()):
        n.id, n.pc = f"y{k}", 0x1000 + 4 * k
    got = bg.tally(trace({cond.pc or 0: 5, x.pc or 0: 2}), bg.Basis(p, None))
    assert got.taken == {graph.head_key(cond.id): 2, bg.IMPLICIT.format(cond.id): 3}


def test_a_switch_counts_each_case_and_the_empty_default_is_the_rest() -> None:
    c = counting()
    got = counted(c, (c.sw, 9), (c.cases[0], 3), (c.cases[1], 2))
    assert [got.taken[a.id] for a in c.sw.arms] == [3, 2, 4]


def test_a_switch_with_no_default_has_the_rest_in_its_implicit_port() -> None:
    c = counting()
    got = counted(c, (c.sw2, 7), (c.case, 5))
    assert got.taken == {c.sw2.arms[0].id: 5, bg.IMPLICIT.format(c.sw2.id): 2}


def test_a_random_counts_each_branch_and_the_empty_one_is_the_rest() -> None:
    c = counting()
    got = counted(c, (c.rnd, 12), (c.picks[0], 7), (c.picks[1], 3))
    assert [got.taken[a.id] for a in c.rnd.arms] == [7, 3, 2]


def test_two_empty_arms_are_not_told_apart_and_are_left_out() -> None:
    c = counting()
    got = counted(c, (c.rnd2, 6))
    assert not any(a.id in got.taken for a in c.rnd2.arms)
    got = counted(c, (c.rnd, 4), (c.picks[0], 1), (c.picks[1], 1), (c.rnd2, 6))
    assert (
        len([k for k in got.taken if k.startswith(c.rnd.id) or k in {a.id for a in c.rnd.arms}])
        == 3
    )


def test_a_dropped_line_cannot_make_an_arm_negative() -> None:
    c = counting()
    got = counted(c, (c.cond, 3), (c.then, 5), dropped=2)
    assert got.taken[c.cond.arms[0].id] == 0 and got.dropped == 2


def test_a_block_that_did_not_run_has_no_ports_counted() -> None:
    c = counting()
    got = counted(c, (c.first, 1))
    assert got.taken == {}


def test_a_marker_is_inside_the_script_and_counts_for_nothing() -> None:
    c = counting()
    marker = c.pc(c.cond.arms[0])
    got = counted(c, (c.first, 2), (c.cond.arms[0], 3))
    assert marker not in {c.pc(n) for n in (c.first, c.cond)}
    assert got.steps == 5 and got.outside == 0 and list(got.nodes) == [c.first.id]


def test_an_address_outside_the_script_is_outside() -> None:
    c = counting()
    t = trace({c.pc(c.first): 2, int(a.USER_RAM): 3, 0x500: 1})
    got = bg.tally(t, bg.Basis(c.p, None))
    assert got.steps == 6 and got.outside == 4 and got.nodes == {c.first.id: 2}


def test_a_build_is_read_through_its_image(rig: Rig) -> None:
    at = {rig.at("u1.0"): 5, rig.at("n0"): 5, rig.at("n1"): 3, rig.at("n3"): 9, rig.at("n4"): 2}
    basis = bg.Basis(rig.built.program, rig.built.id, rig.image, rig.box.load)
    got = bg.tally(trace(at), basis)
    assert got.id == rig.built.id and got.steps == 24 and got.outside == 0
    assert got.nodes == {"u1.0": 5, "n0": 5, "n1": 3, "n4": 2}  # n3 is the else marker
    assert got.taken == {graph.head_key("n0"): 3, "n3": 2} and "n5" in got.never
    assert got.routines["r_a"] == bg.Total(5, 15)


def test_addresses_beyond_the_image_are_outside(rig: Rig) -> None:
    t = trace({rig.at("n0"): 1, rig.box.load + len(rig.image.data): 2, rig.box.load - 4: 1})
    got = bg.tally(t, bg.Basis(rig.built.program, rig.built.id, rig.image, rig.box.load))
    assert got.outside == 3 and got.nodes == {"n0": 1}


# ---- the count's state


def native_count(follow: bg.Follow, c: Counting) -> tuple[bg.Count, Run]:
    follow.target = target()
    run = Run()
    count = bg.Count(follow)
    assert count.start(None, run, lift=c.p)
    return count, run


def test_a_count_asks_for_the_followed_monsters_trace_and_holds_follow(
    follow: bg.Follow,
) -> None:
    count, run = native_count(follow, counting())
    assert run.asked == [(ENTITY, bg.SECONDS)] and count.busy and follow.paused
    assert count.status == "counting 30 s…" and not count.poll()
    assert count.busy and follow.paused and count.result is None


def test_a_count_finishes_with_the_nodes_and_lets_follow_go(follow: bg.Follow) -> None:
    c = counting()
    count, run = native_count(follow, c)
    run.job.set_result(trace({c.pc(c.first): 4, c.pc(c.cond): 2}, dropped=3))
    assert count.poll() and not count.busy and not follow.paused
    assert count.result is not None and count.result.nodes == {c.first.id: 4, c.cond.id: 2}
    assert count.status == (
        "counted 6 instructions in 30 s · 3 log lines were lost, so the counts are low"
    )
    assert not count.poll() and not count.stale


def test_a_second_count_waits_for_the_first(follow: bg.Follow) -> None:
    count, run = native_count(follow, counting())
    other = Run()
    assert not count.start(None, other, lift=counting().p) and other.asked == []
    assert count.status == "counting 30 s…"


def test_no_followed_monster_is_said(follow: bg.Follow) -> None:
    run, count = Run(), bg.Count(follow)
    assert not count.start(None, run) and run.asked == [] and count.status == bg.NOT_FOLLOWING
    assert not follow.paused


def test_the_game_s_own_script_needs_a_lift() -> None:
    f = bg.Follow()
    f.target = target()
    count = bg.Count(f)
    assert not count.start(None, Run()) and count.status == bg.NO_LIFT


def test_a_build_the_monster_does_not_run_is_not_counted(rig: Rig) -> None:
    f = bg.Follow()
    f.target = target(bg.OTHER, 0xABCD1234, rig.box.load)
    count = bg.Count(f)
    run = Run()
    assert not count.start(rig.built, run) and run.asked == []
    assert count.status == "the monster runs another build (id ABCD1234): Send or Push, then count"
    f.target = target(bg.OTHER)
    assert not count.start(None, run)
    assert count.status == "the monster runs a script this view does not show"
    f.target = target(bg.BUILD, (rig.built.id or 0) + 1, rig.box.load)  # the view has moved on
    assert not count.start(rig.built, run) and "older build than the view shows" in count.status


def test_a_build_is_counted_through_its_image(rig: Rig, follow: bg.Follow) -> None:
    follow.target = target(bg.BUILD, rig.built.id, rig.box.load)
    run, count = Run(), bg.Count(follow)
    assert count.start(rig.built, run)
    run.job.set_result(trace({rig.at("u1.0"): 3, rig.at("n1"): 1}))
    assert count.poll(rig.built) and count.result is not None
    assert count.result.id == rig.built.id and count.result.nodes == {"u1.0": 3, "n1": 1}
    assert not count.stale


def test_counts_finished_after_a_rebuild_say_they_are_of_the_old_build(
    rig: Rig, follow: bg.Follow
) -> None:
    follow.target = target(bg.BUILD, rig.built.id, rig.box.load)
    run, count = Run(), bg.Count(follow)
    count.start(rig.built, run)
    newer = make_rig(sub=3)
    assert not count.poll(newer.built)  # still counting
    run.job.set_result(trace({rig.at("u1.0"): 3}))
    assert count.poll(newer.built) and count.stale and count.result is not None
    assert count.result.id == rig.built.id  # the node ids are of the build counted
    old = f"these counts belong to an older build (id {rig.built.id:08X})"
    assert count.status == f"counted 3 instructions in 30 s · {old}"
    assert count.poll(rig.built) and not count.stale  # edited back to it


def test_native_counts_are_never_of_an_older_build(rig: Rig, follow: bg.Follow) -> None:
    c = counting()
    count, run = native_count(follow, c)
    run.job.set_result(trace({c.pc(c.first): 1}))
    assert count.poll(rig.built) and not count.stale and count.result is not None
    assert count.result.id is None


def test_a_game_that_does_not_run_is_said_not_raised(follow: bg.Follow) -> None:
    count, run = native_count(follow, counting())
    run.job.set_exception(TimeoutError("the game did not run 30 emulated s in 130 s"))
    assert count.poll() and not follow.paused and count.result is None
    assert count.status == (
        "the count timed out: the game did not run 30 emulated s in 130 s; is the game paused?"
    )


@pytest.mark.parametrize(
    ("error", "words"),
    [
        (
            DebuggerError("cpu.breakpoint.add", "no"),
            "the debugger failed the count: cpu.breakpoint.add: no",
        ),
        (ConnectionError("gone"), "the game did not answer: gone"),
        (ValueError("bad reply"), "the game did not answer: bad reply"),
    ],
)
def test_other_failures_are_said_too(follow: bg.Follow, error: Exception, words: str) -> None:
    count, run = native_count(follow, counting())
    run.job.set_exception(error)
    assert count.poll() and count.status == words and count.result is None
    assert not count.busy and not follow.paused


def test_a_monster_that_ran_nothing_has_left_or_does_not_tick(follow: bg.Follow) -> None:
    count, run = native_count(follow, counting())
    run.job.set_result(trace({}))
    assert count.poll() and count.result is None
    assert count.status.startswith("no instruction ran in 30 s: the monster left")


def test_a_monster_that_ran_elsewhere_only_has_left_the_script(follow: bg.Follow) -> None:
    count, run = native_count(follow, counting())
    run.job.set_result(trace({int(a.USER_RAM): 40}))
    assert count.poll() and count.result is None
    assert count.status == "the monster left this script during the count"


def test_a_new_count_drops_the_old_result(follow: bg.Follow) -> None:
    c = counting()
    count, run = native_count(follow, c)
    run.job.set_result(trace({c.pc(c.first): 1}))
    count.poll()
    assert count.result is not None
    again = Run()
    assert count.start(None, again, 10.0, lift=c.p) and count.result is None
    assert again.asked == [(ENTITY, 10.0)] and count.status == "counting 10 s…"


def test_reset_forgets_the_result_and_follow_waits_for_the_trace_in_flight(
    follow: bg.Follow,
) -> None:
    c = counting()
    count, run = native_count(follow, c)
    count.reset()
    assert follow.paused and count.busy and count.status == ""
    run.job.set_result(trace({c.pc(c.first): 1}))
    assert not count.poll() and not follow.paused and count.result is None and not count.busy
    count.reset()  # nothing in flight
    assert not follow.paused and count.status == ""


def test_em75_build_marks_whatever_it_resumes_at(mhfu_data: Path) -> None:
    p = brain.decode(Extracted.find(mhfu_data), 75)
    v = brain.view(p, brain.default_header(p))
    built = Built.of(p, EditSet.of(p))
    assert built.image is not None
    raw = inject.container(p, built.image, IMAGE, origin=False)
    box = Container.read(raw)
    name = brain.default_header(p)
    heads = brain.variants(p)
    header = IMAGE + built.image.items[name][0]
    for pc, enter in list(v.ends.items())[::5]:
        s = state(IMAGE + built.image.origin[pc], header=header, pair=v.enters[enter])
        found = bg.row_at(v, pc)
        f = marked(v, heads, s, box, built)
        assert found is not None and f.next_row == found[0] and f.header == name
        assert Mark(PLAYING, "") in f.marks[enter]
