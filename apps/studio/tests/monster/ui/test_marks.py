# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Watch the game on the Behaviour tab: the toggle, the marks on the frames and the outline, the
flash, and when it polls. A fake reader stands in for the game."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

import pytest
from mhfu.live.brain import UNLIMITED, Brain, Slot
from mhfu_port import behaviour as model
from mhfu_port.behaviour import Block, MoveNode
from mhfu_port.manifest import Manifest, Move
from mhfu_studio.monster import rule_game as rg
from mhfu_studio.monster.panels import triggers
from mhfu_studio.monster.panels.behaviour import BehaviourPanel
from mhfu_studio.monster.panels.node_graph import GLOW_MUTED
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit, theme
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

LEFT, NONE = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
CFG = 0x1000


def graph(m: Any) -> None:
    """Six rules: two while charge plays (ranks 1, 2), two flinches (3, 4), a notice (5) and a
    part break (6)."""
    for name in ("stamp", "stamp_2", "flinch_head", "howl"):
        m.moves[name] = Move(clip="walk")
    b = m.behaviour
    b.blocks["b1"] = Block("played_for", (260.0, 0.0), {"frames": 20}, next=["b2"])
    b.blocks["b2"] = Block("cooldown", (460.0, 0.0), {"frames": 30}, play=["stamp"])
    b.blocks["b3"] = Block("played_for", (260.0, 100.0), {"frames": 90}, play=["stamp_2"])
    b.blocks["b4"] = Block("on_flinch", (260.0, 200.0), play=["stamp"], label="any flinch")
    b.blocks["b5"] = Block("on_flinch", (260.0, 300.0), {"part": 1}, play=["flinch_head"])
    b.blocks["b6"] = Block("on_noticed", (260.0, 400.0), next=["b7"])
    b.blocks["b7"] = Block("force", (460.0, 400.0), play=["howl"], label="at once")
    b.blocks["b8"] = Block("on_part_broken", (260.0, 500.0), {"part": 1}, play=["stamp"])
    b.moves["charge"] = MoveNode((0.0, 0.0), ["b1", "b3"])
    m.moves["charge"] = Move(clip="walk")
    for k, name in enumerate(("stamp", "stamp_2", "flinch_head", "howl")):
        b.moves[name] = MoveNode((900.0, 160.0 * k))


class Game:
    """The game: it runs the rules of `m` as sent, fired `fired` times each; `calls` is where
    each read was told the brain was."""

    def __init__(self, m: Manifest) -> None:
        self.rules = model.compile(m)
        self.fired = [0] * len(self.rules)
        self.ticks = 100
        self.calls: list[int | None] = []

    def __call__(
        self, attach: Callable[[], AbstractContextManager[Any]], cfg: int | None
    ) -> rg.Read:
        self.calls.append(cfg)
        slots = tuple(
            Slot(
                k,
                rg.kind(r.on),
                -1 if r.signal is None else r.signal,
                bool(r.play),
                r.min_frames,
                r.cooldown,
                n,
                UNLIMITED,
                0,
            )
            for k, (r, n) in enumerate(zip(self.rules, self.fired, strict=True))
        )
        return rg.Read(CFG, Brain(CFG, self.ticks, slots))


@pytest.fixture
def ws(workspace: MonsterWorkspace) -> MonsterWorkspace:
    assert workspace.doc is not None
    workspace.doc.edit(graph)
    workspace.sync()
    workspace.game_running = lambda: True
    return workspace


@pytest.fixture
def game(ws: MonsterWorkspace) -> Game:
    g = Game(ws.manifest)  # type: ignore[arg-type]
    ws.read_brain = g
    return g


@pytest.fixture
def clock(ws: MonsterWorkspace) -> list[float]:
    now = [100.0]
    ws.watch.clock = lambda: now[0]
    return now


@pytest.fixture
def panel(qtbot: Any, ws: MonsterWorkspace, game: Game, clock: list[float]) -> BehaviourPanel:
    studio = Studio([ws])
    p = BehaviourPanel(ws, studio)
    studio.listen(p.sync)
    qtbot.addWidget(p)
    p.resize(1100, 640)
    p.show()
    qtbot.waitExposed(p)
    p.sync()
    qtbot.wait(20)
    return p


def rank(n: int) -> str:
    return f"#{n}"


def titles(p: BehaviourPanel) -> list[str]:
    nodes = [n for n in p.view.viewer.all_nodes() if not hasattr(n, "text_item")]
    return [n.name for n in sorted(nodes, key=lambda n: n.xy_pos[1])]


def poll(qtbot: Any, p: BehaviourPanel, clock: list[float], until: Callable[[], bool]) -> None:
    """Ticks the panel a second apart until `until`; the read comes back from a worker."""

    def step() -> bool:
        clock[0] += 1.0
        p._tick()
        return until()

    qtbot.waitUntil(step, timeout=3000)


def watch(qtbot: Any, p: BehaviourPanel, clock: list[float]) -> None:
    QTest.mouseClick(p.watch, LEFT, NONE)
    poll(qtbot, p, clock, lambda: p.ws.fires is not None)


def scope(qtbot: Any, p: BehaviourPanel, key: str) -> None:
    out = p.outline
    QTest.mouseClick(out.viewport(), LEFT, NONE, out.visualItemRect(out.item_of(key)).center())
    qtbot.wait(40)


def test_nothing_is_marked_until_the_toggle_is_on(panel: BehaviourPanel, game: Game) -> None:
    assert not panel.watch.isChecked() and panel.live.isHidden() and not panel.ticker.isActive()
    assert all("×" not in t for t in titles(panel))
    assert panel.outline.width() == triggers.WIDTH and game.calls == []
    assert panel.watch.toolTip() and kit.missing_tips(panel) == []


def test_the_toggle_starts_a_poll_once_a_second_while_the_tab_is_shown(
    qtbot: Any, panel: BehaviourPanel, clock: list[float]
) -> None:
    QTest.mouseClick(panel.watch, LEFT, NONE)
    assert panel.ws.watch.on and panel.ticker.isActive() and panel.ticker.interval() == 1000
    assert not panel.live.isHidden()
    panel.hide()
    assert not panel.ticker.isActive()
    panel.show()
    assert panel.ticker.isActive()
    QTest.mouseClick(panel.watch, LEFT, NONE)
    assert not panel.ws.watch.on and not panel.ticker.isActive() and panel.live.isHidden()


def test_a_tab_hidden_polls_nothing(
    qtbot: Any, panel: BehaviourPanel, game: Game, clock: list[float]
) -> None:
    watch(qtbot, panel, clock)
    panel.hide()
    qtbot.wait(100)  # a read in flight comes back
    n = len(game.calls)
    for _ in range(5):
        clock[0] += 2.0
        qtbot.wait(30)
    assert not panel.ticker.isActive() and len(game.calls) == n


def test_it_reads_at_most_once_a_second_whatever_the_ticks(
    qtbot: Any, panel: BehaviourPanel, game: Game, clock: list[float]
) -> None:
    QTest.mouseClick(panel.watch, LEFT, NONE)
    for _ in range(40):
        clock[0] += 0.1
        panel._tick()
        qtbot.wait(5)
    qtbot.waitUntil(lambda: panel.ws.fires is not None, timeout=3000)
    assert len(game.calls) <= 5 and game.calls[0] is None
    assert set(game.calls[1:]) <= {CFG}


def test_a_trigger_view_marks_every_frame_and_quiets_what_never_fired(
    qtbot: Any, panel: BehaviourPanel, game: Game, clock: list[float]
) -> None:
    game.fired = [0, 0, 7, 0, 0, 0]
    watch(qtbot, panel, clock)
    scope(qtbot, panel, "on_flinch")
    assert [t.rsplit(" \u00b7 ", 1)[1] for t in titles(panel)] == ["7\u00d7", "0\u00d7"]
    assert titles(panel)[0] == f"{rank(3)} \u00b7 any flinch \u00b7 7\u00d7"
    frames = panel.view._backdrops
    assert frames["frame:3"].view.text_color != frames["frame:4"].view.text_color
    assert frames["frame:4"].view.text_color[3] == GLOW_MUTED


def test_in_order_marks_every_rule_and_the_status_says_the_tick(
    qtbot: Any, panel: BehaviourPanel, game: Game, clock: list[float]
) -> None:
    game.fired, game.ticks = [1, 2, 3, 4, 5, 6], 4321
    watch(qtbot, panel, clock)
    assert [t.rsplit(" · ", 1)[1] for t in titles(panel)] == [
        "1×",
        "2×",
        "3×",
        "4×",
        "5×",
        "6×",
    ]
    assert panel.live.text() == "watching · AI tick 4321" and panel.live.level is None


def test_a_long_title_is_cut_before_its_mark_never_through_it(
    qtbot: Any, panel: BehaviourPanel, game: Game, clock: list[float]
) -> None:
    def note(m: Manifest) -> None:
        m.behaviour.blocks["b4"].label = "a note that goes on and on and on " * 6

    panel.ws.doc.edit(note)  # type: ignore[union-attr]
    panel.ws.refresh()
    game.rules = model.compile(panel.ws.manifest)  # type: ignore[arg-type]
    game.fired = [0, 0, 12, 0, 0, 0]
    watch(qtbot, panel, clock)
    scope(qtbot, panel, "on_flinch")
    first = titles(panel)[0]
    assert first.endswith(" · 12×") and "…" in first


def test_the_outline_shows_each_triggers_total_beside_its_ranks(
    qtbot: Any, panel: BehaviourPanel, game: Game, clock: list[float]
) -> None:
    game.fired = [1, 2, 3, 4, 5, 6]
    watch(qtbot, panel, clock)
    out = panel.outline

    def fired(key: str) -> object:
        return out.item_of(key).data(triggers.FIRED)

    assert fired("on_flinch") == "7×" and fired("during:charge") == "3×"
    assert fired("on_noticed") == "5×" and fired("on_tail_cut") == ""
    assert fired("all") == "21×"
    assert out.width() == triggers.WIDTH + triggers.FIRED_W
    QTest.mouseClick(panel.watch, LEFT, NONE)
    assert fired("on_flinch") == "" and out.width() == triggers.WIDTH


def test_a_rule_that_just_fired_is_outlined_for_a_moment(
    qtbot: Any, panel: BehaviourPanel, game: Game, clock: list[float]
) -> None:
    watch(qtbot, panel, clock)
    assert panel.view._glows == []
    game.fired = [0, 0, 0, 0, 2, 0]
    poll(qtbot, panel, clock, lambda: panel.ws.watch.lit() == frozenset({5}))
    assert len(panel.view._glows) == 1 and panel.glow.isActive()
    rect = panel.view.frame_rects()["frame:5"]
    assert panel.view._glows[0].rect() == rect
    assert panel.view._glows[0].pen().color() == theme.accent()
    clock[0] += rg.FLASH + 0.1
    panel._marks()
    assert panel.view._glows == []


def test_counts_that_fall_are_taken_and_say_since_when(
    qtbot: Any, panel: BehaviourPanel, game: Game, clock: list[float]
) -> None:
    game.fired = [5, 5, 5, 5, 5, 5]
    watch(qtbot, panel, clock)
    game.fired, game.ticks = [0, 1, 0, 0, 0, 0], 12
    poll(qtbot, panel, clock, lambda: panel.ws.fires == {1: 0, 2: 1, 3: 0, 4: 0, 5: 0, 6: 0})
    assert panel.live.text() == "watching · since AI tick 12"
    assert panel.view._glows == [] and titles(panel)[1].endswith(" · 1×")


def test_a_game_running_other_rules_shows_no_marks_and_says_so(
    qtbot: Any, panel: BehaviourPanel, game: Game, clock: list[float]
) -> None:
    game.fired = [1, 2, 3, 4, 5, 6]
    watch(qtbot, panel, clock)

    def edit(m: Manifest) -> None:
        m.behaviour.blocks["b2"].params["frames"] = 31

    panel.ws.doc.edit(edit)  # type: ignore[union-attr]
    panel.ws.refresh()
    panel.sync()
    poll(qtbot, panel, clock, lambda: panel.ws.watch.status == rg.OTHER_BUILD)
    assert panel.ws.fires is None and panel.live.text() == rg.OTHER_BUILD
    assert panel.live.level == "warning"
    assert all("×" not in t for t in titles(panel))
    assert panel.outline.item_of("on_flinch").data(triggers.FIRED) == ""


def test_no_game_running_is_said_and_nothing_is_read(
    qtbot: Any, panel: BehaviourPanel, game: Game, clock: list[float]
) -> None:
    panel.ws.game_running = lambda: False
    QTest.mouseClick(panel.watch, LEFT, NONE)
    clock[0] += 1.0
    panel._tick()
    assert panel.live.text() == rg.NO_GAME and panel.live.level == "warning" and game.calls == []


def test_the_canvas_has_no_frames_to_mark_but_still_says_how_it_goes(
    qtbot: Any, panel: BehaviourPanel, game: Game, clock: list[float]
) -> None:
    panel.ws.all_view = "canvas"
    panel.sync()
    game.fired = [1, 2, 3, 4, 5, 6]
    watch(qtbot, panel, clock)
    assert panel.view._backdrops == {} and panel.live.text().startswith("watching")
