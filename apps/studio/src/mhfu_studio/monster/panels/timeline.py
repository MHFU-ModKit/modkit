# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Timeline: the transport at the engine's rate, the base monster's action's frames on the
clip, and an own move's attack windows under it; above them, the steps of its sequence.

The RATE is the action's (one monster plays some actions at 2.0, others at 2.4); the clip owns
only its SPAN. A gate past the clip's last frame never runs: the strip runs on past the end,
shaded, and draws it hollow there (at the far edge when it lies further out than a third of the
clip). The attack lanes share the strip's frames (`x_of`, `frame_at`); a drag on them is
`authoring.WindowDrag`'s."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TYPE_CHECKING

from mhfu_port.manifest import MOVE_ATTACKS
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QHelpEvent, QKeyEvent, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QStackedWidget, QToolTip, QVBoxLayout, QWidget

from mhfu_studio.monster.align import EFFECT, GATE, IMPACT, OURS, WINDOW, Marker
from mhfu_studio.monster.authoring import EDGE_PX
from mhfu_studio.monster.panels.common import ATTACK, MARKERS
from mhfu_studio.monster.panels.sequence import SequenceBar
from mhfu_studio.monster.panels.widgets import NoScene
from mhfu_studio.monster.render.playback import GAME_HZ, OBSERVED_SPEEDS
from mhfu_studio.shell.overlay import Ink
from mhfu_studio.shell.text import plain
from mhfu_studio.ui import kit, theme

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

LEGEND = (
    (GATE, "hit check"),
    (WINDOW, "timing window"),
    (EFFECT, "base monster's effect"),
    (OURS, "your effect"),
    (IMPACT, "impact"),
)
STRIP_TIP = (
    "The clip's frames; click or drag to scrub, hover a mark to read it. Marks are the base"
    " monster's frames for the picked action (hit checks, timing windows, effects) and your"
    " clip's impact; a hollow one past the end is never reached."
)
#: the strip's height and the legend's under it
STRIP_H, LEGEND_H = 26.0, 18.0
#: the most frames past the clip's end the strip shows, as a share of the clip
OVERRUN = 1 / 3
#: playback redraws this often (ms) while it plays
TICK = 33
#: an attack lane's height
LANE_H = 18.0
LANES_TIP = (
    "The move's attacks, one lane each: drag on the empty lane to add one (a click adds one"
    " without an end), drag an edge or the span to move it, click one to pick it, Delete"
    " removes it. Lit while the playhead is inside: the attack is out. A dashed line is where"
    " the move's Length ends it."
)
#: the strip's inset at either end, in points
INSET = 3.0


def x_of(frame: float, span: float, width: float) -> float:
    """Where clip frame `frame` sits on a strip `width` points wide showing `span` frames."""
    return INSET + (width - 2 * INSET) * min(frame, span) / max(span, 1e-9)


def frame_at(x: float, span: float, width: float) -> float:
    return (x - INSET) / max(width - 2 * INSET, 1.0) * span


class FrameStrip(QWidget):
    """The clip's frames, the action's markers and the playhead; a drag scrubs."""

    def __init__(self, seek: Callable[[float], None]) -> None:
        super().__init__()
        self._seek = seek
        self.markers: list[Marker] = []
        self.end = 0.0
        self.phase = 0.0
        self.setToolTip(STRIP_TIP)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(int(STRIP_H + LEGEND_H))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def show_frames(self, markers: list[Marker], end: float, phase: float) -> None:
        if (markers, end, phase) != (self.markers, self.end, self.phase):
            self.markers, self.end, self.phase = list(markers), end, phase
            self.update()

    @property
    def span(self) -> float:
        """The clip, and on past its end to the last marker, within `OVERRUN`."""
        last = max((m.frame for m in self.markers), default=0.0)
        return max(min(last, self.end * (1 + OVERRUN)), self.end, 1.0)

    def x_of(self, frame: float) -> float:
        return x_of(frame, self.span, self.width())

    def frame_at(self, x: float) -> float:
        return max(0.0, min(frame_at(x, self.span, self.width()), self.end))

    def marker_at(self, x: float, y: float) -> Marker | None:
        if not 0 <= y <= STRIP_H:
            return None
        near = [(abs(self.x_of(m.frame) - x), i) for i, m in enumerate(self.markers)]
        d, i = min(near, default=(99.0, -1))
        return self.markers[i] if d < 6 else None

    def paintEvent(self, e: QPaintEvent) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = float(self.width())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.color(theme.current().view))
        p.drawRoundedRect(QRectF(0, 0, w, STRIP_H), 4, 4)
        if self.span > self.end:  # past the clip: frames the engine never plays
            shade = theme.color(Ink.SHADOW)
            shade.setAlphaF(shade.alphaF() * 0.6)
            p.setBrush(shade)
            p.drawRect(QRectF(self.x_of(self.end), 0, w - self.x_of(self.end), STRIP_H))
        for mk in self.markers:
            x, col = self.x_of(mk.frame), theme.color(MARKERS[mk.kind])
            if mk.unreachable:
                p.setPen(QPen(col, 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRectF(x - 2, 3, 4, STRIP_H - 6))
            else:
                p.setPen(QPen(col, 2.0))
                p.drawLine(QPointF(x, 1), QPointF(x, STRIP_H - 1))
        self._labels(p)
        p.setPen(QPen(theme.color(Ink.TEXT), 2.0))  # the playhead, over the picture's colour
        x = self.x_of(self.phase)
        p.drawLine(QPointF(x, 0), QPointF(x, STRIP_H))
        self._legend(p)
        p.end()

    def _labels(self, p: QPainter) -> None:
        """Placed, not just drawn: close gates overprint into a smear. The impact goes first,
        it is the answer the strip exists to give."""
        font = p.font()
        font.setPixelSize(10)
        p.setFont(font)
        fm = p.fontMetrics()
        taken: list[tuple[float, float]] = []
        for mk in sorted(self.markers, key=lambda k: (k.kind != IMPACT, k.frame)):
            tw = fm.horizontalAdvance(mk.label)
            x = self.x_of(mk.frame) + 3.0
            if x + tw > self.width():  # no room on the right: on the left
                x -= tw + 6.0
            if any(x < b and x + tw > a for a, b in taken):
                continue
            taken.append((x - 2.0, x + tw + 2.0))
            # on a chip of the picture's shadow: the strip is mid-grey in a light theme
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color(Ink.SHADOW))
            p.drawRoundedRect(QRectF(x - 2.0, 2.0, tw + 4.0, fm.height() + 1.0), 3.0, 3.0)
            p.setPen(theme.color(MARKERS[mk.kind]))
            p.drawText(QPointF(x, 3 + fm.ascent()), mk.label)

    def _legend(self, p: QPainter) -> None:
        fm = p.fontMetrics()
        x, y = 2.0, STRIP_H + 4
        for kind, name in LEGEND:
            if not any(mk.kind == kind for mk in self.markers):
                continue
            p.setPen(QPen(theme.color(MARKERS[kind]), 3.0))
            p.drawLine(QPointF(x, y + 2), QPointF(x, y + 11))
            p.setPen(self.palette().color(self.foregroundRole()))
            p.drawText(QPointF(x + 6, y + fm.ascent()), name)
            x += 6 + fm.horizontalAdvance(name) + 14

    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.ToolTip and isinstance(e, QHelpEvent):
            mk = self.marker_at(e.x(), e.y())
            if mk is None:
                QToolTip.showText(e.globalPos(), STRIP_TIP, self)
            else:
                never = "\npast the clip's last frame: never reached" if mk.unreachable else ""
                tip = plain(f"frame {mk.frame:g}: {mk.detail or mk.label}{never}")
                QToolTip.showText(e.globalPos(), tip, self)
            return True
        return super().event(e)

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.end > 0:
            self._seek(self.frame_at(e.position().x()))

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.buttons() & Qt.MouseButton.LeftButton and self.end > 0:
            self._seek(self.frame_at(e.position().x()))


class AttackLanes(QWidget):
    """The selected own move's attack windows, one lane each and one to add on, on the strip's
    frames; the playhead runs through them."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio, strip: FrameStrip) -> None:
        super().__init__()
        self.ws, self.studio, self.strip = ws, studio, strip
        self.setToolTip(LANES_TIP)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def lanes(self) -> int:
        mv = self.ws.own_move()
        n = 0 if mv is None else len(mv.attacks)
        return n + (n < MOVE_ATTACKS)

    def fit(self) -> None:
        self.setFixedHeight(int(LANE_H * max(self.lanes(), 1)) + 2)

    def frame_of(self, x: float) -> float:
        return frame_at(x, self.strip.span, self.width())

    def x_of(self, frame: float) -> float:
        return x_of(frame, self.strip.span, self.width())

    def paintEvent(self, e: QPaintEvent) -> None:  # noqa: N802
        ws, p = self.ws, QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        mv = ws.own_move()
        if mv is None:
            p.end()
            return
        w, end = float(self.width()), float(ws.window_frames())
        live = set(ws.live_windows())
        drag = ws.windows.grab
        font = p.font()
        font.setPixelSize(10)
        p.setFont(font)
        for lane in range(self.lanes()):
            top = lane * LANE_H + 1
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color(theme.current().view))
            p.drawRoundedRect(QRectF(0, top, w, LANE_H - 2), 3, 3)
            a = mv.attacks[lane] if lane < len(mv.attacks) else None
            if drag is not None and drag.index == (lane if a is not None else None):
                a = ws.windows.preview
            if a is None:
                p.setPen(self.palette().color(self.foregroundRole()))
                p.drawText(
                    QRectF(6, top, w - 12, LANE_H - 2),
                    Qt.AlignmentFlag.AlignVCenter,
                    f"drag here to add attack {ws.window_id()}",
                )
                continue
            x0, x1 = self.x_of(a.frame), self.x_of(end if a.end is None else a.end)
            fill = theme.color(Ink.HOT) if lane in live else theme.color(ATTACK)
            if a.end is None:  # it lives as its record says: drawn open
                fill.setAlphaF(fill.alphaF() * 0.45)
            p.setBrush(fill)
            picked = lane == ws.picked_window
            p.setPen(QPen(theme.color(Ink.SELECTION), 2.0) if picked else Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(x0, top + 1, max(x1 - x0, 3.0), LANE_H - 4), 3, 3)
            on = lane in live  # dark on the lit fill
            p.setPen(theme.color(theme.current().view) if on else theme.color(Ink.TEXT))
            tail = " →" if a.end is None else ""
            p.drawText(QPointF(x0 + 4, top + LANE_H - 6), f"{a.id}{tail}")
        vp = ws.vp
        if vp is not None and vp.clip is not None and mv.length is not None:
            x = self.x_of(mv.length * vp.playback.speed)  # Length is AI frames
            p.setPen(QPen(theme.color(Ink.WARNING), 1.5, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.setPen(QPen(theme.color(Ink.TEXT), 2.0))
        if vp is not None and vp.clip is not None:
            x = self.x_of(vp.playback.phase)
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.end()

    def _at(self, e: QMouseEvent) -> tuple[int, float]:
        pos = e.position()
        return int(pos.y() // LANE_H), self.frame_of(pos.x())

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() != Qt.MouseButton.LeftButton:
            return
        lane, f = self._at(e)
        slop = EDGE_PX * self.strip.span / max(self.width() - 2 * INSET, 1.0)
        self.studio.act("attack window", lambda: self.ws.press_window(lane, f, slop))()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self.ws.windows.dragging:
            self.ws.windows.move(self._at(e)[1])
            self.update()
            return
        lane, f = self._at(e)
        mv = self.ws.own_move()
        part = None
        if mv is not None:
            self.ws.windows.frames = self.ws.window_frames()
            slop = EDGE_PX * self.strip.span / max(self.width() - 2 * INSET, 1.0)
            got = self.ws.windows.part_at(mv.attacks, lane, f, slop)
            part = None if got is None else got[1]
        edge = part in ("start", "end")
        self.setCursor(Qt.CursorShape.SizeHorCursor if edge else Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self.ws.windows.dragging:
            f = self._at(e)[1]
            self.studio.act("attack window", lambda: self.ws.windows.release(f))()

    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.studio.act("remove attack", self.ws.remove_window)()
        elif e.key() == Qt.Key.Key_Escape and self.ws.windows.dragging:
            self.ws.windows.cancel()
            self.update()
        else:
            super().keyPressEvent(e)


class TimelinePanel(kit.Panel):
    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        act = studio.act
        self.rewind = kit.icon_button(
            "ph.skip-back", tip="Back to the clip's first frame (Home)", on=act("rewind", ws.rewind)
        )
        self.back = kit.icon_button(
            "ph.caret-left",
            tip="One game frame back; the cursor moves by the speed (Left arrow)",
            on=act("step back", lambda: ws.step(-1)),
        )
        self.play = kit.icon_button(
            "ph.play",
            tip="Plays the clip at the game's own 30 frames a second, or pauses it (Space)",
            on=act("play", ws.play_pause),
        )
        self.fwd = kit.icon_button(
            "ph.caret-right",
            tip="One game frame on; the cursor moves by the speed (Right arrow)",
            on=act("step", lambda: ws.step(1)),
        )
        self.frame = kit.label(role="mono", wrap=False)
        self.frame.setMinimumWidth(self.frame.fontMetrics().horizontalAdvance("frame 000.0 / 000"))
        self.loop = kit.check(
            "Loop",
            tip="Starts over at the end instead of stopping, as the engine runs a looping clip",
            on=lambda on: act("loop", lambda: self._set("loop", on))(),
        )
        self.in_place = kit.check(
            "In place",
            tip="Holds the body where the clip starts, so one that travels (a charge, a leap)"
            " plays on the spot. Changes only the view, not the port.",
            on=lambda on: act("in place", lambda: self._in_place(on))(),
        )
        self.speed = kit.number(
            tip="Clip frames played per game frame. The action sets it, not the clip: one"
            " monster plays some actions at 2.0, others at 2.4.",
            value=2.0,
            step=0.1,
            decimals=2,
            lo=0.05,
            hi=4.0,
            suffix=" f/frame",
            on=lambda v: act("speed", lambda: self._set("speed", v))(),
        )
        presets = [
            kit.button(
                f"{s:.1f}",
                tip=f"Speed {s:g}, a rate the game plays actions at",
                on=act("speed", partial(self._set, "speed", s)),
            )
            for s in OBSERVED_SPEEDS
        ]
        self.strip = FrameStrip(lambda f: act("scrub", lambda: ws.seek(f))())
        self.lanes = AttackLanes(ws, studio, self.strip)
        self.attack_id = kit.integer(
            tip="The picked attack's record id; with none picked, the id the next one drawn gets",
            lo=0,
            hi=0xFFFF,
            on=lambda v: act("attack id", lambda: self._set_id(v))(),
        )
        self.attack_what = kit.label(role="muted", wrap=False)
        self.attack_live = kit.label(role="mono", wrap=False)
        self.remove = kit.button(
            "Remove",
            tip="Removes the picked attack from the move (Delete on the lanes)",
            on=act("remove attack", ws.remove_window),
            icon="ph.trash",
        )
        self.hint = kit.label(
            "No marks yet: pick an action in Actions to see the base monster's frames here.",
            role="hint",
        )
        self.timing = kit.label(role="muted")
        self.timing.setToolTip(
            "How long the clip runs: its length in frames (the clip's) divided by the speed"
            " (the action's) at the game's 30 frames a second"
        )
        self.travel = kit.label(role="muted")
        self.travel.setToolTip("How far the body moves over the clip: start to end, and at most")
        self.impact = kit.button(
            "Set impact here",
            tip="Records the frame on screen as this clip's impact frame in the manifest: where"
            " its hit lands. Actions checks it against the base monster's hit checks.",
            on=act("set impact", ws.set_impact_here),
            icon="ph.target",
        )
        self.impact_note = kit.label(role="muted", wrap=False)

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        self.steps = SequenceBar(ws, studio)
        lay.addWidget(self.steps)
        lay.addWidget(
            kit.row(
                self.rewind, self.back, self.play, self.fwd, self.frame, self.loop,
                self.in_place, stretch=True,
            )
        )  # fmt: skip
        lay.addWidget(self.strip)
        lay.addWidget(self.lanes)
        self.attack_row = kit.row(
            kit.label("Attack", role="muted"),
            self.attack_id,
            self.attack_what,
            self.remove,
            self.attack_live,
            stretch=True,
        )
        lay.addWidget(self.attack_row)
        lay.addWidget(self.hint)
        self.impact_row = kit.row(self.impact, self.impact_note, stretch=True)
        lay.addWidget(self.impact_row)
        self.more = kit.More(tip="The playback speed, how long the clip runs and how far it goes")
        self.more.body.addWidget(
            kit.row(kit.label("Speed", role="muted"), self.speed, *presets, stretch=True)
        )
        self.more.body.addWidget(self.timing)
        self.more.body.addWidget(self.travel)
        lay.addWidget(self.more)
        lay.addStretch(1)
        self.no_scene = NoScene(studio)
        self.no_clip = kit.Empty(
            "No clip playing", "Pick an action in Actions, or a clip in Clips, and it plays here."
        )
        self.empty = QStackedWidget()
        self.empty.addWidget(self.no_scene)
        self.empty.addWidget(self.no_clip)
        self.pages = kit.Pages(page, self.empty)
        self.body.addWidget(self.pages)
        self._playing: bool | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(TICK)
        self.timer.timeout.connect(self._tick)

    def _set_id(self, v: int) -> None:
        """The picked window's id, else the next one's."""
        ws = self.ws
        if ws.picked_window is None:
            ws.next_window_id = v
        else:
            ws.set_window(ws.picked_window, id=v)

    def _set(self, what: str, value: float | bool) -> None:
        vp = self.ws.vp
        if vp is not None and vp.clip is not None:
            setattr(vp.playback, what, value)

    def _in_place(self, on: bool) -> None:
        if self.ws.vp is not None:
            self.ws.vp.strip_root = on

    def sync(self) -> None:
        ws = self.ws
        vp = ws.vp
        clip = None if vp is None else vp.clip
        self.pages.show_page(clip is not None)
        if vp is None or clip is None:
            self.empty.setCurrentWidget(
                self.no_scene if vp is None or vp.scene is None else self.no_clip
            )
            self.timer.stop()
            return
        pb = vp.playback
        if pb.playing != self._playing:
            self._playing = pb.playing
            theme.bind(self.play, "ph.pause" if pb.playing else "ph.play")
        kit.put(self.loop, pb.loop)
        kit.put(self.in_place, vp.strip_root)
        kit.put(self.speed, pb.speed)
        self._show_phase()
        self._sequence()
        self._attacks()
        self.hint.setVisible(not ws.markers and ws.own_move_on_screen() is None)
        self.timing.setText(
            f"{pb.duration:.2f} s: {pb.end:.0f} frames at {pb.speed:.2f} per game frame,"
            f" {GAME_HZ:g} a second"
        )
        net, peak = ws.travel(clip.slot)
        self.travel.setText(
            f"travels {net:.0f} start to end, {peak:.0f} at most"
            + ("" if net >= 1.0 else " (on the spot)")
        )
        found = ws.manifest_clip(clip.slot)
        self.impact_row.setVisible(ws.manifest is not None)
        at = None if found is None else found[1].impact_frame
        self.impact_note.setText("no impact frame yet" if at is None else f"impact at {at}")
        if pb.playing:
            self.timer.start()
        else:
            self.timer.stop()

    def _sequence(self) -> None:
        """The picked move's steps above the transport, when it has company."""
        ws = self.ws
        steps = ws.sequence_of()
        self.steps.setVisible(len(steps) > 1)
        self.steps.show_steps([ws.step_of(n) for n in steps] if len(steps) > 1 else [], ws.move)

    def _attacks(self) -> None:
        """The lanes and the picked attack's row, while an own move's clip is on screen."""
        ws = self.ws
        on = ws.own_move_on_screen() is not None
        self.lanes.setVisible(on)
        self.attack_row.setVisible(on)
        if not on:
            return
        self.lanes.fit()
        mv = ws.own_move()
        i = ws.picked_window
        a = None if mv is None or i is None else mv.attacks[i]
        kit.put(self.attack_id, ws.window_id() if a is None else a.id)
        rec = ws.attack_record(ws.window_id() if a is None else a.id)
        what = "no record" if rec is None else f"power {rec.power}, hit group {rec.volume}"
        span = "" if a is None else f"frames {a.frame}-{'' if a.end is None else a.end}, "
        self.attack_what.setText(f"{span}{what}" + ("" if a is not None else ", the next drawn"))
        self.remove.setEnabled(a is not None)
        self._show_live()

    def _show_live(self) -> None:
        ws, mv = self.ws, self.ws.own_move()
        live = [] if mv is None else [mv.attacks[i].id for i in ws.live_windows()]
        self.attack_live.setText("out: " + ", ".join(map(str, live)) if live else "no attack out")
        self.lanes.update()

    def _show_phase(self) -> None:
        vp = self.ws.vp
        if vp is None or vp.clip is None:
            return
        pb = vp.playback
        self.frame.setText(f"frame {pb.phase:.1f} / {pb.end:.0f}")
        self.impact.setText(f"Set impact = frame {round(pb.phase)}")
        self.strip.show_frames(self.ws.markers, pb.end, pb.phase)
        if self.lanes.isVisible():
            self._show_live()

    def _tick(self) -> None:
        """The playhead follows playback; a stop by itself (the end, no loop) re-syncs, and so
        does a played sequence going on to its next step."""
        if self.ws.take_stepped():
            self.studio.changed()
        vp = self.ws.vp
        if vp is None or vp.clip is None or not vp.playback.playing:
            self.timer.stop()
            self.sync()
            return
        self._show_phase()
