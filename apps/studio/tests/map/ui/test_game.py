# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import json
from typing import Any

from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.panels.game import GamePanel
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.studio import MAIN, Studio
from mhfu_studio.ui import kit

MOVE = {"op": "move", "sub": 0, "group": 0, "vertices": [0, 1, 2], "by": [0, 40, 0]}


class Runner:
    def __init__(self) -> None:
        self.started: list[tuple[list[str], bytes]] = []
        self.stopped = 0

    def start(self, argv: list[str], stdin: bytes) -> None:
        self.started.append((argv, stdin))

    def stop(self) -> None:
        self.stopped += 1


def panel(qtbot: Any, ws: MapWorkspace) -> tuple[GamePanel, Studio, Runner]:
    studio = Studio([ws])
    studio.runner = runner = Runner()
    p = GamePanel(ws, studio)
    qtbot.addWidget(p)
    studio.listen(p.sync)
    p.sync()
    return p, studio, runner


def edited(game: Extracted, atlas: Atlas) -> MapWorkspace:
    ws = MapWorkspace(game, atlas)
    ws.load_stage(139, row=0)
    assert ws.session is not None
    ws.session.ops.append(dict(MOVE))
    return ws


def test_nothing_loaded(qtbot: Any, game: Extracted, atlas: Atlas) -> None:
    p, _, _ = panel(qtbot, MapWorkspace(game, atlas))
    assert "Pick an area" in p.gate.empty.hint.text() and kit.missing_tips(p) == []


def test_no_edits(qtbot: Any, game: Extracted, atlas: Atlas) -> None:
    ws = MapWorkspace(game, atlas)
    ws.load_stage(139, row=0)
    p, _, _ = panel(qtbot, ws)
    assert kit.missing_tips(p) == [] and not any(b.isEnabled() for b in p.pushes)
    assert p.restore.isEnabled() and p.running.text() == "no edits to Pokke village yet"


def test_unsaved_pushes(qtbot: Any, game: Extracted, atlas: Atlas) -> None:
    ws = edited(game, atlas)
    p, studio, runner = panel(qtbot, ws)
    assert ws.doc.directory is None and all(b.isEnabled() for b in p.pushes)
    p.pushes[1].click()
    argv, stdin = runner.started[0]
    assert argv[1:3] == ["-c", MAIN] and argv[3:] == list(ws.push_job(("mesh",)).argv)
    assert json.loads(stdin) == [MOVE] and studio.job is not None
    assert not any(b.isEnabled() for b in [*p.pushes, p.restore]) and p.stop.isEnabled()
    assert p.running.text() == "send Pokke village's mesh to the game… (running)"
    p.stop.click()
    assert runner.stopped == 1
    studio.heard("line one")
    studio.ended(0)
    assert p.log.toPlainText().splitlines()[1:] == ["line one", "[exit 0]"]
    assert p.pushes[0].isEnabled() and not p.stop.isEnabled()
    p.restore.click()
    assert runner.started[1][0][-1] == "--restore" and runner.started[1][1] == b""
    p.clear.click()
    assert p.log.toPlainText() == "" and studio.log == []


def test_village_catch(qtbot: Any, game: Extracted, atlas: Atlas) -> None:
    ws = edited(game, atlas)
    p, _, runner = panel(qtbot, ws)
    assert p.catch.value() == 0 == ws.catch and p.village.isHidden()
    p.catch.setValue(30)
    assert ws.catch == 30 and not p.village.isHidden()
    p.pushes[0].click()
    argv = runner.started[0][0]
    assert argv[argv.index("--catch") + 1] == "30"
