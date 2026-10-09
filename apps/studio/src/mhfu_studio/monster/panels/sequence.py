# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What a sequence is made of on screen: the chips of its steps (Moves), the clip picker that
ranks what fits after a step (Moves), and the bar of its steps above the transport (Timeline)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from mhfu_port import continuity
from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QHelpEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QFrame, QScrollArea, QSizePolicy, QToolTip, QVBoxLayout, QWidget

from mhfu_studio.monster.sequences import BY, PickRow, Step
from mhfu_studio.shell.overlay import Ink
from mhfu_studio.ui import kit, theme

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

BAR_H = 20.0
#: the least width of a step on the bar, and the gap between two
MIN_STEP, GAP = 14.0, 2.0
PAIR_TIP = "rides the base monster's pair: its place in the sequence is edited in Actions"
PICKER_ROWS = 8


def frames_text(frames: int | None) -> str:
    return "" if frames is None else f"{frames}f"


def step_tip(s: Step) -> str:
    lines = [s.name, f"plays {s.clip}" if s.clip else "plays no clip", s.label]
    return "\n".join([x for x in lines if x] + ([] if s.own else [PAIR_TIP]))


class StepChips(QScrollArea):
    """One chip per step, clip name and frames; the picked one lit. A click picks a step."""

    picked = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._key: object = None
        self.chips: kit.Segmented | None = None

    def show_steps(self, steps: Sequence[Step], picked: str | None) -> None:
        key = tuple(steps)
        if not steps:
            return
        if key != self._key:
            self._key = key
            self.chips = kit.Segmented(
                [
                    (s.name, " ".join(filter(None, [s.clip or s.name, frames_text(s.frames)])))
                    for s in steps
                ],
                tip="The steps of this sequence, in the order they play: click one to pick it",
                on=self.picked.emit,
                tips={s.name: step_tip(s) for s in steps},
            )
            self.setWidget(self.chips)
            self.setFixedHeight(
                self.chips.sizeHint().height() + self.horizontalScrollBar().sizeHint().height() + 4
            )
        if self.chips is not None and picked is not None:
            self.chips.set(picked)


class ClipPicker(QWidget):
    """The manifest's clips as the next step, best fit first: a row picked is `picked(name)`."""

    picked = Signal(str)
    cancelled = Signal()
    COLUMNS = ("Clip", "Shows", "Frames", "Fit", "Used in")

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws = ws
        self.after: str | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.title = kit.label(role="caps", wrap=False)
        self.by = kit.Segmented(
            list(BY),
            tip="Fits after: the clips whose first pose continues where the clip before ends,"
            " best first. Neighbours: the clips numbered next to it in the original.",
            on=lambda v: studio.act("order clips", lambda: setattr(ws, "pick_by", v))(),
            current=ws.pick_by,
        )
        self.cancel = kit.button(
            "Cancel",
            tip="Closes the list without changing the sequence",
            on=self.cancelled.emit,
            icon="ph.x",
        )
        self.note = kit.label(role="muted")
        self.table = kit.Table(
            list(self.COLUMNS),
            tip="Click a clip to use it. Fit is the mean angle between the two poses where the"
            " clips meet: the lower, the smoother the join. Used in names the moves playing it.",
        )
        self.table.picked.connect(lambda n: self.picked.emit(str(n)))
        for w in (kit.row(self.title, self.by, self.cancel, stretch=True), self.note, self.table):
            lay.addWidget(w)

    def show_after(self, after: str | None, title: str) -> None:
        """Ranks the clips as a step after clip `after` (None: unranked)."""
        self.after = after
        self.title.setText(title)
        self.refresh()

    def refresh(self) -> None:
        rows = self.ws.pick_rows(self.after)
        kit.put(self.by, self.ws.pick_by)
        rated = any(r.fit is not None for r in rows)
        self.note.setText(
            "" if rated else self.ws.ends_note or "No clip before this one to rank by"
        )
        self.note.setVisible(not rated)
        self.table.set_rows(
            [self._cells(r) for r in rows],
            [r.name for r in rows],
            tips=[f"{r.name}\n{r.label}".strip() for r in rows],
        )
        self.table.fit(PICKER_ROWS)

    @staticmethod
    def _cells(r: PickRow) -> tuple[str, ...]:
        fit = "·" if r.fit is None else f"{r.fit:.0f}° {continuity.word(r.fit)}"
        return r.name, r.label, frames_text(r.frames), fit, ", ".join(r.used)


class SequenceBar(QWidget):
    """The picked move's sequence as one bar, each step as long as its clip; the picked step lit.
    A click picks a step."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        self.steps: list[Step] = []
        self.picked: str | None = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(int(BAR_H))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(
            "The steps of the picked move's sequence, as long as their clips: click one"
        )

    def show_steps(self, steps: list[Step], picked: str | None) -> None:
        if (steps, picked) != (self.steps, self.picked):
            self.steps, self.picked = list(steps), picked
            self.update()

    def segments(self) -> list[tuple[Step, float, float]]:
        """Each step with its left and right edge on the bar."""
        weights = [float(s.frames or 1) for s in self.steps]
        room = max(self.width() - GAP * (len(weights) - 1), 1.0)
        out, x = [], 0.0
        for s, w in zip(self.steps, weights, strict=True):
            span = max(room * w / sum(weights), MIN_STEP)
            out.append((s, x, x + span))
            x += span + GAP
        return out

    def step_at(self, x: float) -> Step | None:
        return next((s for s, a, b in self.segments() if a <= x <= b), None)

    def paintEvent(self, e: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = p.font()
        font.setPixelSize(10)
        p.setFont(font)
        for s, a, b in self.segments():
            lit = s.name == self.picked
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color(Ink.HOT) if lit else theme.color(theme.current().view))
            p.drawRoundedRect(QRectF(a, 0, b - a, BAR_H), 3, 3)
            p.setPen(theme.color(theme.current().view) if lit else theme.color(Ink.TEXT))
            text = p.fontMetrics().elidedText(s.name, Qt.TextElideMode.ElideRight, int(b - a - 8))
            p.drawText(QRectF(a + 4, 0, b - a - 8, BAR_H), Qt.AlignmentFlag.AlignVCenter, text)
        p.end()

    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.ToolTip and isinstance(e, QHelpEvent):
            s = self.step_at(e.x())
            if s is None:
                QToolTip.hideText()
            else:
                QToolTip.showText(e.globalPos(), step_tip(s), self)
            return True
        return super().event(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        s = self.step_at(e.position().x())
        if e.button() == Qt.MouseButton.LeftButton and s is not None:
            self.studio.act("pick step", lambda: self.ws.select_move(s.name))()
