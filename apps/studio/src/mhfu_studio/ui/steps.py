# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The next-steps line over the view: the workspace's `next_steps`, each ticked once done."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from mhfu_studio.shell import settings
from mhfu_studio.shell.workspace import Step
from mhfu_studio.ui import about, kit

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio
    from mhfu_studio.shell.workspace import Workspace

TICK = "\u2713"
HIDE_TIP = "Hides these steps for this workspace; View > Next steps brings them back"


def hidden(ws: Workspace) -> bool:
    return settings.store.get(f"steps/{ws.name}") == "hidden"


def hide(ws: Workspace, on: bool = True) -> None:
    settings.store.put(f"steps/{ws.name}", "hidden" if on else None)


def text(i: int, s: Step) -> str:
    """`2 Move it (W)`, a done one ticked."""
    key = f" ({about.native(s.key)})" if s.key and not s.done else ""
    return f"{TICK if s.done else i} {s.text}{key}"


class Steps(QWidget):
    """Shown while the view shows a document and its workspace has steps, unless hidden."""

    def __init__(self, studio: Studio) -> None:
        super().__init__()
        self.studio = studio
        self.setObjectName("Steps")
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(8, 0, 4, 4)
        self.lay.setSpacing(10)
        self.lay.addWidget(kit.label("Next steps", role="caps", wrap=False))
        self.row = QWidget()
        self.row.setMinimumWidth(1)  # a narrow view clips the line, not the panels beside it
        self.items = QHBoxLayout(self.row)
        self.items.setContentsMargins(0, 0, 0, 0)
        self.items.setSpacing(14)
        self.lay.addWidget(self.row)
        self.lay.addStretch(1)
        self.close_button = kit.icon_button("ph.x", tip=HIDE_TIP, on=self._hide)
        self.lay.addWidget(self.close_button)
        self._shown: tuple[Step, ...] | None = None
        self.labels: list[QLabel] = []

    def _hide(self) -> None:
        self.studio.act("hide next steps", lambda: hide(self.studio.active))()

    def steps(self, on_start: bool) -> tuple[Step, ...]:
        ws = self.studio.active
        return () if on_start or hidden(ws) else tuple(ws.next_steps())

    def sync(self, on_start: bool) -> None:
        steps = self.steps(on_start)
        self.setVisible(bool(steps))
        if steps == self._shown:
            return
        self._shown = steps
        while (item := self.items.takeAt(0)) is not None:
            w = item.widget()
            if w is not None:
                w.deleteLater()
        nxt = next((i for i, s in enumerate(steps) if not s.done), None)
        self.labels = []
        for i, s in enumerate(steps):
            role: kit.LabelRole = "muted" if s.done else "next" if i == nxt else "body"
            lb = kit.label(text(i + 1, s), role=role, wrap=False)
            lb.setToolTip(f"Done: {s.text}" if s.done else "Next" if i == nxt else "Later")
            self.items.addWidget(lb)
            lb.show()  # now, not a loop turn later: `_fit` measures it
            self.labels.append(lb)
        self._fit()

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        self._fit()

    def _fit(self) -> None:
        """Done steps shrink to their tick, the first first, until the line fits."""
        shown = self._shown or ()
        for i, (lb, s) in enumerate(zip(self.labels, shown, strict=True)):
            lb.setText(text(i + 1, s))
        for lb, s in zip(self.labels, shown, strict=True):
            if self.sizeHint().width() <= self.width():
                break
            if s.done:
                lb.setText(TICK)
