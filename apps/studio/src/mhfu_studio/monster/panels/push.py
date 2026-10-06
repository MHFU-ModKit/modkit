# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Push to game, beside Send to game: the hit tables straight into the running game."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QVBoxLayout, QWidget

from mhfu_studio.ui import kit

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio


class PushRow(QWidget):
    """The push button and why it cannot run."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws = ws
        self.push = kit.button(
            "Push to game",
            tip="Writes your hitboxes, hurtboxes and damage grid straight into the game running"
            " in PPSSPP (the lane MHFU_LANE names, if set) and reads them back, saved or not."
            " Nothing goes on the memory stick; the next quest puts the game's own back.",
            on=studio.act("push", ws.push),
            icon="ph.lightning",
        )
        self.hint = kit.label(role="muted")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(kit.row(self.push, stretch=True))
        lay.addWidget(self.hint)

    def sync(self) -> None:
        why = self.ws.push_blocker()
        self.push.setEnabled(why is None)
        self.hint.setText(why or "")
        self.hint.setVisible(why is not None)
