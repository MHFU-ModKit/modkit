# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Fire on the Behaviour tab: when a trigger can be raised in the running game, why not, and the
one byte it writes. A fake reader and a fake session stand in for the game."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Any

import pytest
from mhfu import addresses as a
from mhfu_port import behaviour as model
from mhfu_port.behaviour import Block
from mhfu_port.manifest import Move
from mhfu_studio.monster import rule_game as rg
from mhfu_studio.monster.panels import triggers
from mhfu_studio.monster.panels.behaviour import BehaviourPanel
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from .conftest import CFG, FakeGame

LEFT, NONE = Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
ROAR, FLINCH = "on_signal:roar", "on_flinch"
TIP = (
    "Raises Signal · roar in the running game now; the rules' counts show whether their"
    " conditions held"
)


def graph(m: Any) -> None:
    """A rule on the signal `bellow` (board signal 0), one on `roar` (1) and a flinch."""
    for name in ("stamp", "howl"):
        m.moves[name] = Move(clip="walk")
    b = m.behaviour
    b.blocks["b1"] = Block("on_signal", (260.0, 0.0), {"name": "bellow"}, play=["stamp"])
    b.blocks["b2"] = Block("on_signal", (260.0, 100.0), {"name": "roar"}, play=["howl"])
    b.blocks["b3"] = Block("on_flinch", (260.0, 200.0), play=["stamp"])


class Mem:
    """What a session's memory is asked to write."""

    def __init__(self) -> None:
        self.writes: list[tuple[int, int]] = []
        self.fail: Exception | None = None
        self.opened = 0

    def session(self) -> AbstractContextManager[Session]:
        """The game's session, as `ws.game_session` opens it."""
        self.opened += 1
        return nullcontext(Session(self))

    def write_u8(self, address: int, value: int) -> None:
        if self.fail is not None:
            raise self.fail
        self.writes.append((address, value))


class Session:
    def __init__(self, mem: Mem) -> None:
        self.mem = mem


@pytest.fixture
def ws(workspace: MonsterWorkspace) -> MonsterWorkspace:
    assert workspace.doc is not None
    workspace.doc.edit(graph)
    workspace.sync()
    workspace.game_running = lambda: True
    return workspace


@pytest.fixture
def game(ws: MonsterWorkspace) -> FakeGame:
    g = FakeGame(ws.manifest)  # type: ignore[arg-type]
    ws.read_brain = g
    return g


@pytest.fixture
def mem(ws: MonsterWorkspace) -> Mem:
    m = Mem()
    ws.game_session = m.session
    return m


@pytest.fixture
def clock(ws: MonsterWorkspace) -> list[float]:
    now = [100.0]
    ws.watch.clock = lambda: now[0]
    return now


@pytest.fixture
def panel(
    qtbot: Any, ws: MonsterWorkspace, game: FakeGame, mem: Mem, clock: list[float]
) -> BehaviourPanel:
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


def scope(qtbot: Any, p: BehaviourPanel, key: str) -> None:
    out = p.outline
    QTest.mouseClick(out.viewport(), LEFT, NONE, out.visualItemRect(out.item_of(key)).center())
    qtbot.wait(40)


def watching(qtbot: Any, p: BehaviourPanel, clock: list[float]) -> None:
    QTest.mouseClick(p.watch, LEFT, NONE)

    def step() -> bool:
        clock[0] += 1.0
        p._tick()
        return p.ws.fires is not None

    qtbot.waitUntil(step, timeout=3000)


def test_fire_is_there_for_a_signal_trigger_only(qtbot: Any, panel: BehaviourPanel) -> None:
    assert panel.fire.isHidden()
    scope(qtbot, panel, ROAR)
    assert not panel.fire.isHidden() and panel.fire.text() == "Fire"
    scope(qtbot, panel, FLINCH)
    assert panel.fire.isHidden()
    scope(qtbot, panel, "all")
    assert panel.fire.isHidden()


def test_not_watching_is_the_first_reason(qtbot: Any, panel: BehaviourPanel) -> None:
    scope(qtbot, panel, ROAR)
    assert not panel.fire.isEnabled()
    assert panel.fire.toolTip() == f"Fire: {panel.ws.watch.blocker()}"
    assert "Watch the game" in panel.fire.toolTip()


def test_it_says_why_it_cannot_fire_while_watching(
    qtbot: Any, panel: BehaviourPanel, game: FakeGame, clock: list[float]
) -> None:
    scope(qtbot, panel, ROAR)
    QTest.mouseClick(panel.watch, LEFT, NONE)
    assert panel.fire.toolTip() == "Fire: Watch the game has not read the game yet"
    panel.ws.game_running = lambda: False
    clock[0] += 1.0
    panel._tick()
    assert panel.fire.toolTip() == f"Fire: {rg.NO_GAME}" and not panel.fire.isEnabled()


def test_a_brain_not_found_and_another_build_disable_it_with_their_reason(
    qtbot: Any, panel: BehaviourPanel, game: FakeGame, clock: list[float]
) -> None:
    scope(qtbot, panel, ROAR)
    game.answer = lambda: rg.Read(None, None)
    QTest.mouseClick(panel.watch, LEFT, NONE)

    def step(until: str) -> bool:
        clock[0] += 1.0
        panel._tick()
        return panel.ws.watch.status == until

    qtbot.waitUntil(lambda: step(rg.NOT_FOUND), timeout=3000)
    assert panel.fire.toolTip() == f"Fire: {rg.NOT_FOUND}" and not panel.fire.isEnabled()
    QTest.mouseClick(panel.watch, LEFT, NONE)
    game.answer, game.other_build = None, True
    QTest.mouseClick(panel.watch, LEFT, NONE)
    qtbot.waitUntil(lambda: step(rg.OTHER_BUILD), timeout=3000)
    assert panel.fire.toolTip() == f"Fire: {rg.OTHER_BUILD}" and not panel.fire.isEnabled()


def test_found_and_running_these_rules_it_is_on_with_the_tip(
    qtbot: Any, panel: BehaviourPanel, clock: list[float]
) -> None:
    scope(qtbot, panel, ROAR)
    watching(qtbot, panel, clock)
    assert panel.fire.isEnabled() and panel.fire.toolTip() == TIP


def test_fire_writes_the_triggers_board_signal_where_watch_found_the_brain(
    qtbot: Any, panel: BehaviourPanel, game: FakeGame, mem: Mem, clock: list[float]
) -> None:
    scope(qtbot, panel, ROAR)
    watching(qtbot, panel, clock)
    qtbot.wait(100)  # the read the last tick asked has landed on its worker
    reads, doc = len(game.calls), panel.ws.doc
    assert doc is not None
    before = (doc.manifest, doc.can_undo())
    index = model.signals(doc.manifest)["roar"]
    assert index == 1
    QTest.mouseClick(panel.fire, LEFT, NONE)
    board = CFG + int(a.EM_BLOCK.BOARD) + int(a.EM_BOARD.SIGNALS)
    assert mem.writes == [(board + index, 1)]
    assert panel.ws.message == f"raised signal roar in the game (board signal {index})"
    assert len(game.calls) == reads, "no second look for the brain"
    assert (doc.manifest, doc.can_undo()) == before, "the game is not the document"


def test_fire_on_another_signal_writes_its_own_index(
    qtbot: Any, panel: BehaviourPanel, mem: Mem, clock: list[float]
) -> None:
    scope(qtbot, panel, "on_signal:bellow")
    watching(qtbot, panel, clock)
    QTest.mouseClick(panel.fire, LEFT, NONE)
    board = CFG + int(a.EM_BLOCK.BOARD) + int(a.EM_BOARD.SIGNALS)
    assert mem.writes == [(board + model.signals(panel.ws.manifest)["bellow"], 1)]  # type: ignore[arg-type]


def test_a_click_is_one_act_and_one_session(
    qtbot: Any, panel: BehaviourPanel, mem: Mem, clock: list[float]
) -> None:
    scope(qtbot, panel, ROAR)
    watching(qtbot, panel, clock)
    told: list[int] = []
    panel.studio.listen(lambda: told.append(1))
    QTest.mouseClick(panel.fire, LEFT, NONE)
    assert len(told) == 1 and mem.opened == 1 and len(mem.writes) == 1
    assert panel.studio.errors == []


def test_a_disabled_fire_writes_nothing(qtbot: Any, panel: BehaviourPanel, mem: Mem) -> None:
    scope(qtbot, panel, ROAR)
    t = panel._current()
    assert t is not None and panel.ws.fire_blocker(t) is not None
    panel.ws.fire(t)
    assert mem.writes == [] and mem.opened == 0
    assert panel.ws.message.startswith("not fired: ")


def test_a_write_that_fails_says_so(
    qtbot: Any, panel: BehaviourPanel, mem: Mem, clock: list[float]
) -> None:
    scope(qtbot, panel, ROAR)
    watching(qtbot, panel, clock)
    mem.fail = OSError("connection reset")
    QTest.mouseClick(panel.fire, LEFT, NONE)
    assert panel.ws.message == "not fired: connection reset" and panel.studio.errors == []


def test_only_a_trigger_in_the_table_can_fire(panel: BehaviourPanel) -> None:
    flinch = next(t for t in panel._triggers if t.key == FLINCH)
    assert set(rg.FIRE) == {"on_signal"} and flinch.kind not in rg.FIRE
    assert panel.ws.fire_blocker(flinch) == "nothing in the studio raises this trigger"
    assert triggers.title(next(t for t in panel._triggers if t.key == ROAR)) == "Signal · roar"
