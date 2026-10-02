# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A finding on an edit lands on that edit in Selection's list."""

from pathlib import Path
from typing import Any

from mhfu_studio.map.document import MapDocument
from mhfu_studio.map.panels.selection import SelectionPanel
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.stage import ops as O
from PySide6.QtCore import Qt


def test_an_edit_error_selects_the_edit(
    qtbot: Any, ws: MapWorkspace, studio: Studio, doc_dir: Path
) -> None:
    d = MapDocument("d", 0, doc_dir)
    d.ensure_stage(98).ops.extend(
        [
            {"op": "transform", "sub": 0, "group": 1, "vertices": [0, 1, 2], "by": [0, 9, 0]},
            {"op": "move", "sub": 0, "group": 1, "vertices": [0], "by": [0, 1, 0], "odd": 1},
        ]
    )
    d.save()
    ws.open(doc_dir)
    f = next(f for f in ws.document.findings() if f.code == "unknown-key")
    assert f.target == (98, 1) and f.focus == O.EDIT
    p = SelectionPanel(ws, studio)
    qtbot.addWidget(p)
    p.show()
    qtbot.waitExposed(p)
    ws.reveal(f.target, f.focus)
    assert ws.take_focus() == "Selection"
    p.sync()
    assert ws.landing == "" and p.ops.currentItem().data(Qt.ItemDataRole.UserRole) == 1
    qtbot.waitUntil(lambda: p.focusWidget() is p.ops)
