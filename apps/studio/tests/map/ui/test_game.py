# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import sys
from pathlib import Path
from typing import Any

import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.panels.game import MAIN, GamePanel
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.stage.live import QUEST_CATCH
from mhfu_studio.ui import kit

CLIMB = {"op": "collision", "group": None, "chunk": 0, "tri": 0, "flags": {"material": 9}}


def panel(qtbot: Any, ws: MapWorkspace) -> GamePanel:
    studio = Studio([ws])
    p = GamePanel(ws, studio)
    qtbot.addWidget(p)
    studio.listen(p.sync)
    p.sync()
    return p


def test_nothing_loaded(qtbot: Any, game: Extracted, atlas: Atlas) -> None:
    p = panel(qtbot, MapWorkspace(game, atlas))
    assert "Load a section" in p.gate.empty.hint.text() and kit.missing_tips(p) == []


def test_unsaved(qtbot: Any, game: Extracted, atlas: Atlas) -> None:
    ws = MapWorkspace(game, atlas)
    ws.load_stage(139, row=0)
    p = panel(qtbot, ws)
    assert kit.missing_tips(p) == [] and not p.unsaved.isHidden()
    assert not any(b.isEnabled() for b in p.pushes)


def test_village_catch(qtbot: Any, game: Extracted, atlas: Atlas, doc_dir: Path) -> None:
    ws = MapWorkspace(game, atlas)
    ws.load_stage(139, row=0)
    ws.doc.save(doc_dir)
    p = panel(qtbot, ws)
    assert p.catch.value() == 0 and p.village.isHidden() and all(b.isEnabled() for b in p.pushes)
    p.catch.setValue(30)
    assert not p.village.isHidden()


def test_command(qtbot: Any, game: Extracted, doc_dir: Path) -> None:
    ws = MapWorkspace(game, Atlas(game, [(139,), (98,)]))
    ws.load_stage(98)
    ws.doc.save(doc_dir)
    p = panel(qtbot, ws)
    assert p.catch.value() == QUEST_CATCH and p.village.isHidden()
    cmd = p.command(["--mesh"])
    assert cmd[:3] == [sys.executable, "-c", MAIN] and cmd[3:6] == ["map", "inject", str(doc_dir)]
    assert cmd[cmd.index("--stage") + 1] == "98" and cmd[cmd.index("--catch") + 1] == "120"
    assert cmd[-1] == "--mesh" and cmd[cmd.index("--data") + 1] == str(game.root)
    assert ws.session is not None
    ws.session.ops.append(dict(CLIMB))
    assert p.command(["--collision"])[-2:] == ["--hold", "120"]


def test_runs_and_logs(
    qtbot: Any, game: Extracted, atlas: Atlas, doc_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = MapWorkspace(game, atlas)
    ws.load_stage(139, row=0)
    ws.doc.save(doc_dir)
    p = panel(qtbot, ws)
    stub = [sys.executable, "-c", "print('line one')"]
    monkeypatch.setattr(p, "command", lambda flags: stub)
    p.pushes[0].click()
    assert p.busy and not p.pushes[0].isEnabled() and p.stop.isEnabled()
    qtbot.waitUntil(lambda: "[exit 0]" in p.log.toPlainText(), timeout=10_000)
    assert "line one" in p.log.toPlainText() and not p.busy and p.pushes[0].isEnabled()
    p.clear.click()
    assert p.log.toPlainText() == ""


def test_stop(
    qtbot: Any, game: Extracted, atlas: Atlas, doc_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = MapWorkspace(game, atlas)
    ws.load_stage(139, row=0)
    ws.doc.save(doc_dir)
    p = panel(qtbot, ws)
    stub = [sys.executable, "-c", "import time; time.sleep(30)"]
    monkeypatch.setattr(p, "command", lambda flags: stub)
    p.pushes[0].click()
    qtbot.waitUntil(lambda: p.proc is not None and p.proc.processId() > 0, timeout=5_000)
    p.stop.click()
    qtbot.waitUntil(lambda: not p.busy, timeout=10_000)
    assert "[exit" in p.log.toPlainText()
