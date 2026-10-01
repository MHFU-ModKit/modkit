# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path
from typing import Any

import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.panels.document import DocumentPanel
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import dialogs, kit
from PySide6.QtWidgets import QFileDialog, QPushButton

TEXTURE = {"op": "texture", "slot": 1, "rgb": [9, 9, 9]}


def panel(qtbot: Any, ws: MapWorkspace, studio: Studio | None = None) -> DocumentPanel:
    studio = studio or Studio([ws])
    p = DocumentPanel(ws, studio)
    qtbot.addWidget(p)
    studio.listen(p.sync)  # the window's part
    p.sync()
    return p


def button(p: DocumentPanel, text: str) -> QPushButton:
    return next(b for b in p.findChildren(QPushButton) if b.text() == text)


def folder(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: str(path))


def test_no_data(qtbot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MHFU_DATA", raising=False)
    p = panel(qtbot, MapWorkspace())
    assert not p.nodata.isHidden() and kit.missing_tips(p) == []


def test_new_and_save(
    qtbot: Any, game: Extracted, atlas: Atlas, doc_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = MapWorkspace(game, atlas)
    ws.load_stage(139, row=0)
    p = panel(qtbot, ws)
    assert kit.missing_tips(p) == [] and "not saved yet" in p.where.text()
    assert not p.add.isHidden()
    p.add.click()
    assert ws.doc.stage(139) is not None and p.add.isHidden()
    folder(monkeypatch, doc_dir)
    button(p, "New…").click()
    assert ws.doc.directory == doc_dir and ws.doc.name == "doc" and ws.doc.stage(139)
    p.name.setText("snow")
    p.name.editingFinished.emit()
    assert ws.doc.name == "snow" and not p.dirty.isHidden()
    button(p, "Save").click()
    assert (doc_dir / "map.toml").is_file() and not ws.doc.dirty and p.dirty.isHidden()


def test_save_as_and_open(
    qtbot: Any, game: Extracted, atlas: Atlas, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = MapWorkspace(game, atlas)
    ws.load_stage(139, row=0)
    studio = Studio([ws])
    p = panel(qtbot, ws, studio)
    assert ws.session is not None
    ws.session.push([dict(TEXTURE)])
    ws.sync_renderer()
    studio.ask_path = lambda w: tmp_path / "a" / "map.toml"  # the window's part
    button(p, "Save").click()
    assert (tmp_path / "a" / "st139.json").is_file() and ws.doc.name == "a"
    monkeypatch.setattr(dialogs, "ask_open", lambda *a: tmp_path / "a")
    ws.load_stage(98)
    p._open()
    assert "opened" in studio.message and ws.doc.directory == tmp_path / "a"


def test_new_asks_first(
    qtbot: Any, game: Extracted, atlas: Atlas, doc_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = MapWorkspace(game, atlas)
    ws.load_stage(139, row=0)
    studio = Studio([ws])
    p = panel(qtbot, ws, studio)
    assert ws.session is not None
    ws.session.push([dict(TEXTURE)])
    ws.sync_renderer()
    asked: list[list[str]] = []
    studio.ask_discard = lambda names: asked.append(names) or "cancel"
    folder(monkeypatch, doc_dir)
    old = ws.doc
    button(p, "New…").click()
    assert asked == [["untitled map"]] and ws.doc is old


def test_check_and_export(
    qtbot: Any, game: Extracted, atlas: Atlas, doc_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = MapWorkspace(game, atlas)
    ws.load_stage(139, row=0)
    p = panel(qtbot, ws)
    assert ws.session is not None
    ws.session.push([dict(TEXTURE)])
    ws.sync_renderer()
    p.sync()
    assert p.stages.count() == 1 and "(loaded)" in p.stages.item(0).text()
    assert "Save the document first" in p.export(doc_dir / "out")
    ws.doc.save(doc_dir)
    p._check()
    assert "finding(s)" in p.note.text()
    folder(monkeypatch, doc_dir / "out")
    p._export()
    assert (doc_dir / "out" / "export.json").is_file() and "st139: textures" in p.note.text()
