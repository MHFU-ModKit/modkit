# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Watch the game: which rules a brain's slots are, and the state of the toggle."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import replace
from typing import Any

import pytest
from mhfu.live.brain import UNLIMITED, Brain, Slot
from mhfu_port import behaviour as model
from mhfu_port.behaviour import Block
from mhfu_port.manifest import Manifest, ManifestError
from mhfu_studio.monster import rule_game as rg
from mhfu_studio.monster.document import PortDocument

BODY = """
[clips.walk]
slot = 1
frames = 10
[moves.charge]
main = 1
sub = 4
clip = "walk"
[moves.stamp]
clip = "walk"
[moves.howl]
clip = "walk"
[behaviour.blocks.b1]
kind = "played_for"
at = [260.0, 0.0]
frames = 20
play = ["stamp"]
[behaviour.moves.charge]
at = [0.0, 0.0]
during = ["b1"]
"""
Open = Callable[[str], PortDocument]


@pytest.fixture
def m(doc: Open) -> Manifest:
    d = doc(BODY)

    def more(x: Manifest) -> None:
        b = x.behaviour
        b.blocks["b2"] = Block("on_flinch", (260.0, 160.0), play=["howl"])
        b.blocks["b3"] = Block("on_noticed", (260.0, 320.0), next=["b4"])
        b.blocks["b4"] = Block("cooldown", (460.0, 320.0), {"frames": 30}, play=["stamp"])

    d.edit(more)
    return d.manifest


CFG = 0x1000


def brain(m: Manifest, fired: list[int], ticks: int = 100) -> Brain:
    """The slots a game that runs `m` shows."""
    return Brain(
        CFG,
        ticks,
        tuple(
            Slot(k, rg.kind(r.on), -1, True, r.min_frames, r.cooldown, n, UNLIMITED, 0)
            for k, (r, n) in enumerate(zip(model.compile(m), fired, strict=True))
        ),
    )


# ---- match ----------------------------------------------------------------------------------- #


def test_the_slots_of_these_rules_give_each_rules_count_by_rank(m: Manifest) -> None:
    assert len(model.compile(m)) == 3
    assert rg.match(brain(m, [4, 0, 9]), m) == {1: 4, 2: 0, 3: 9}


def test_a_slot_that_differs_in_any_field_is_another_build(m: Manifest) -> None:
    good = brain(m, [1, 2, 3])
    for field, value in (
        ("on", rg.kind("noticed")),
        ("signal", 0),
        ("plays", False),
        ("min_frames", 21),
        ("cooldown", 5),
    ):
        slots = list(good.slots)
        slots[1] = replace(slots[1], **{field: value})
        assert rg.match(replace(good, slots=tuple(slots)), m) == rg.OTHER_BUILD, field


def test_a_rule_with_no_play_in_the_game_is_not_the_rule_that_plays(m: Manifest) -> None:
    good = brain(m, [0, 0, 0])
    slots = (replace(good.slots[0], plays=False), *good.slots[1:])
    assert rg.match(replace(good, slots=slots), m) == rg.OTHER_BUILD


def test_fewer_slots_than_rules_is_another_build_and_more_are_not_looked_at(m: Manifest) -> None:
    good = brain(m, [1, 2, 3])
    assert rg.match(replace(good, slots=good.slots[:2]), m) == rg.OTHER_BUILD
    extra = Slot(5, rg.kind("tail_cut"), -1, True, 0, 0, 99, UNLIMITED, 0)
    assert rg.match(replace(good, slots=(*good.slots, extra)), m) == {1: 1, 2: 2, 3: 3}


def test_a_changed_manifest_no_longer_matches(m: Manifest) -> None:
    good = brain(m, [1, 2, 3])
    m.behaviour.blocks["b4"].params["frames"] = 31
    assert rg.match(good, m) == rg.OTHER_BUILD


def test_rules_that_do_not_compile_say_so(m: Manifest, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_: Manifest) -> Any:
        raise ManifestError("b1: no")

    read = brain(m, [1, 2, 3])
    monkeypatch.setattr(model, "compile", boom)
    got = rg.match(read, m)
    assert isinstance(got, str) and got.startswith("these rules do not compile")


def test_an_event_is_its_place_in_the_kinds_the_game_counts_from_one() -> None:
    assert rg.kind(None) == 0 and rg.kind("noticed") == 1 and rg.kind("flinch") == 4
    assert [rg.kind(e) for e in model.EVENTS] == list(range(1, len(model.EVENTS) + 1))


# ---- the state of the toggle ----------------------------------------------------------------- #


class Game:
    """What `Watch.poll` asks: a clock, and a reader that answers each ask at once."""

    def __init__(self, m: Manifest) -> None:
        self.t = 0.0
        self.asked: list[tuple[float, int | None]] = []
        self.answer: Callable[[], rg.Read] = lambda: rg.Read(CFG, brain(m, [0, 0, 0]))
        self.watch = rg.Watch(clock=lambda: self.t)

    def ask(self, cfg: int | None) -> Future[rg.Read]:
        self.asked.append((self.t, cfg))
        f: Future[rg.Read] = Future()
        try:
            f.set_result(self.answer())
        except Exception as e:
            f.set_exception(e)
        return f

    def poll(self, m: Manifest, running: bool = True, dt: float = 0.0) -> bool:
        self.t += dt
        return self.watch.poll(m, running, self.ask)

    def show(self, m: Manifest, fired: list[int], ticks: int = 100) -> None:
        read = rg.Read(CFG, brain(m, fired, ticks))
        self.answer = lambda: read

    def feed(self, m: Manifest, fired: list[int], ticks: int = 100) -> None:
        """The game now shows `fired`, and the watch has taken it."""
        self.show(m, fired, ticks)
        self.poll(m, dt=1.0)
        self.poll(m, dt=1.0)


@pytest.fixture
def game(m: Manifest) -> Game:
    g = Game(m)
    g.watch.set(True)
    return g


def test_nothing_is_asked_while_off(m: Manifest) -> None:
    g = Game(m)
    assert not g.poll(m) and g.asked == [] and g.watch.fires is None and g.watch.status == ""


def test_turning_it_on_says_it_is_reading_and_the_first_poll_asks(game: Game, m: Manifest) -> None:
    assert game.watch.status == rg.READING
    game.poll(m)
    assert [cfg for _, cfg in game.asked] == [None]


def test_no_game_running_is_said_and_nothing_is_asked(game: Game, m: Manifest) -> None:
    assert game.poll(m, running=False) and game.watch.status == rg.NO_GAME
    assert game.watch.fires is None and game.asked == []
    assert not game.poll(m, running=False, dt=5)


def test_the_counts_come_by_rank_with_the_tick_in_the_status(game: Game, m: Manifest) -> None:
    game.show(m, [3, 0, 7], ticks=1234)
    game.poll(m)  # asks
    assert game.poll(m, dt=1.0)  # takes it
    assert game.watch.fires == {1: 3, 2: 0, 3: 7}
    assert game.watch.status == "watching · AI tick 1234"


def test_it_asks_at_most_once_a_second_and_remembers_where_the_brain_is(
    game: Game, m: Manifest
) -> None:
    for _ in range(100):
        game.poll(m, dt=0.1)
    times = [t for t, _ in game.asked]
    assert 9 <= len(times) <= 11
    assert all(b - a >= rg.PERIOD - 1e-9 for a, b in zip(times, times[1:], strict=False))
    assert [cfg for _, cfg in game.asked] == [None, *[CFG] * (len(times) - 1)]


def test_a_read_that_is_not_done_is_waited_for_not_asked_again(game: Game, m: Manifest) -> None:
    pending: Future[rg.Read] = Future()
    asked: list[float] = []

    def ask(cfg: int | None) -> Future[rg.Read]:
        asked.append(game.t)
        return pending

    for k in range(30):
        game.t = k * 0.5
        game.watch.poll(m, True, ask)
    assert asked == [0.0]
    pending.set_result(rg.Read(CFG, brain(m, [1, 1, 1])))
    game.t = 20.0
    assert game.watch.poll(m, True, ask) and game.watch.fires == {1: 1, 2: 1, 3: 1}


def test_a_brain_not_found_is_said_and_looked_for_again_only_after_a_while(
    game: Game, m: Manifest
) -> None:
    game.answer = lambda: rg.Read(None, None)
    for _ in range(40):
        game.poll(m, dt=0.5)
    assert game.watch.status == rg.NOT_FOUND and game.watch.fires is None
    times = [t for t, _ in game.asked]
    assert len(times) == 2 and times[1] - times[0] >= rg.RETRY


def test_a_game_that_does_not_answer_is_said_and_found_again(game: Game, m: Manifest) -> None:
    def broken() -> rg.Read:
        raise OSError("connection refused")

    game.feed(m, [1, 2, 3])
    assert game.watch.fires == {1: 1, 2: 2, 3: 3}
    game.answer = broken
    game.poll(m, dt=1.0)
    game.poll(m, dt=1.0)
    assert game.watch.status == "the game did not answer: connection refused"
    assert game.watch.fires is None
    game.show(m, [1, 2, 3])
    game.poll(m, dt=1.0)
    assert game.asked[-1][1] is None, "a new attach finds the brain again"


def test_another_build_clears_the_counts_and_says_why(game: Game, m: Manifest) -> None:
    game.feed(m, [1, 2, 3])
    assert game.watch.fires == {1: 1, 2: 2, 3: 3}
    m.behaviour.blocks["b4"].params["frames"] = 31
    game.poll(m, dt=1.0)
    assert game.watch.fires is None and game.watch.status == rg.OTHER_BUILD


def test_a_count_that_rose_lights_its_rank_for_a_moment(game: Game, m: Manifest) -> None:
    game.feed(m, [1, 2, 3])
    assert game.watch.lit() == frozenset(), "the first read is no rise"
    game.feed(m, [1, 5, 3])
    assert game.watch.fires == {1: 1, 2: 5, 3: 3} and game.watch.lit() == frozenset({2})
    game.t += rg.FLASH - 0.1
    assert game.watch.lit() == frozenset({2})
    game.t += 0.2
    assert game.watch.lit() == frozenset()


def test_a_count_that_fell_is_taken_and_the_status_says_since_when(game: Game, m: Manifest) -> None:
    game.feed(m, [10, 20, 30], ticks=5000)
    game.feed(m, [0, 1, 0], ticks=40)
    assert game.watch.fires == {1: 0, 2: 1, 3: 0}
    assert game.watch.status == "watching · since AI tick 40"
    assert game.watch.lit() == frozenset(), "a reset is no fire"


def test_off_forgets_everything_and_stops_asking(game: Game, m: Manifest) -> None:
    game.feed(m, [1, 2, 3])
    game.watch.set(False)
    n = len(game.asked)
    assert game.watch.fires is None and game.watch.status == ""
    assert not game.poll(m, dt=5) and len(game.asked) == n


def test_poll_says_whether_the_marks_changed(game: Game, m: Manifest) -> None:
    game.show(m, [1, 2, 3])
    assert game.poll(m) is False  # asked, nothing to show yet
    assert game.poll(m, dt=1.0) is True
    game.show(m, [1, 2, 3])
    game.poll(m, dt=1.0)
    assert game.poll(m, dt=0.2) is False
