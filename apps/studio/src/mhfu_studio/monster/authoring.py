# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Own moves as document edits (`mhfu_port.manifest.Move` without a pair): their fields, their
steer and their attack windows; and a drag on the Timeline's attack lanes, in clip frames.

No toolkit: the Moves panel and the Timeline call these through the workspace, one undo step
per edit; a refusal is a `ManifestError` saying why.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from mhfu_port.manifest import (
    MOVE_ATTACKS,
    AttackWindow,
    Manifest,
    ManifestError,
    Move,
    Steer,
)

from mhfu_studio.monster.document import PortDocument

#: the Move fields the Moves panel edits
FIELDS = ("clip", "length", "carrier", "host_attacks", "after", "label")
STEER_FIELDS = tuple(f.name for f in dataclasses.fields(Steer))
Part = Literal["new", "start", "end", "body"]
#: points from a window's edge that grab the edge rather than the span
EDGE_PX = 5.0


def own(m: Manifest, name: str) -> Move:
    mv = m.moves.get(name)
    if mv is None:
        raise ManifestError(f"no move {name!r}")
    if not mv.own:
        raise ManifestError(
            f"{name} rides the base monster's ({mv.main},{mv.sub}): not an own move"
        )
    return mv


def free_name(m: Manifest, base: str) -> str:
    """`base`, else `base_2`, `base_3` ...: a move name not taken."""
    n, name = 1, base
    while name in m.moves:
        n += 1
        name = f"{base}_{n}"
    return name


def new_move(doc: PortDocument, name: str, clip: str) -> str:
    """A new own move `[moves.<name>]` playing clip `clip`."""
    if name in doc.manifest.moves:
        raise ManifestError(f"moves.{name} already exists")

    def make(m: Manifest) -> None:
        m.moves[name] = Move(clip=clip)

    doc.edit(make)
    return f"moves.{name}: an own move on {clip}"


def _edit(doc: PortDocument, name: str, fn: Callable[[Move], object]) -> None:
    own(doc.manifest, name)
    doc.edit(lambda m: fn(m.moves[name]))


def set_fields(doc: PortDocument, name: str, **fields: Any) -> str:
    """`FIELDS` of own move `name`."""
    bad = sorted(set(fields) - set(FIELDS))
    if bad:
        raise ManifestError(f"not a move field: {', '.join(bad)}")
    if "carrier" in fields and fields["carrier"] is not None:
        fields["carrier"] = tuple(int(x) for x in fields["carrier"])

    def change(mv: Move) -> None:
        for k, v in fields.items():
            setattr(mv, k, v)

    _edit(doc, name, change)
    return f"moves.{name}: " + ", ".join(f"{k} = {v}" for k, v in fields.items())


def set_steer(doc: PortDocument, name: str, frames_hint: int = 1, **fields: Any) -> str:
    """`STEER_FIELDS` of own move `name`. Turning `fixed` on brings an angle (0) and `frames`
    (`frames_hint`, the clip's AI frames) with it, turning it off takes them away: the loader
    wants both with `fixed` and neither without."""
    bad = sorted(set(fields) - set(STEER_FIELDS))
    if bad:
        raise ManifestError(f"not a steer field: {', '.join(bad)}")

    def change(mv: Move) -> None:
        s = dataclasses.replace(mv.steer, **fields)
        if s.turn == "fixed":
            s.angle = 0.0 if s.angle is None else float(s.angle)
            s.frames = max(1, frames_hint) if s.frames is None else s.frames
        elif "turn" in fields:
            s.angle = s.frames = None
        if s.turn not in ("hunter", "away"):
            s.rate = None
        mv.steer = s

    _edit(doc, name, change)
    return f"moves.{name}.steer: " + ", ".join(f"{k} = {v}" for k, v in fields.items())


def add_window(doc: PortDocument, name: str, w: AttackWindow) -> int:
    """Appends `w` to own move `name`'s attacks; its index."""
    n = len(own(doc.manifest, name).attacks)
    if n >= MOVE_ATTACKS:
        raise ManifestError(f"the move player holds {MOVE_ATTACKS} attacks a move: {name} has them")
    _edit(doc, name, lambda mv: mv.attacks.append(dataclasses.replace(w)))
    return n


def set_window(doc: PortDocument, name: str, index: int, **fields: Any) -> str:
    """Fields (`id`, `frame`, `end`, `label`) of attack `index` of own move `name`."""
    attacks = own(doc.manifest, name).attacks
    if not 0 <= index < len(attacks):
        raise ManifestError(f"{name} has no attack {index}")

    def change(mv: Move) -> None:
        mv.attacks[index] = dataclasses.replace(mv.attacks[index], **fields)

    _edit(doc, name, change)
    return f"moves.{name} attack {index}: " + ", ".join(f"{k} = {v}" for k, v in fields.items())


def remove_window(doc: PortDocument, name: str, index: int) -> str:
    attacks = own(doc.manifest, name).attacks
    if not 0 <= index < len(attacks):
        raise ManifestError(f"{name} has no attack {index}")
    _edit(doc, name, lambda mv: mv.attacks.pop(index))
    return f"moves.{name}: attack {index} removed"


def live(attacks: Sequence[AttackWindow], phase: float) -> list[int]:
    """The windows whose attack is out at clip frame `phase`: spawned, not yet ended (one
    without an end lives as its record says, here to the clip's end)."""
    return [
        i for i, a in enumerate(attacks) if a.frame <= phase and (a.end is None or phase < a.end)
    ]


@dataclass(frozen=True)
class Grab:
    """What a press on the attack lanes took: window `index` (None: a new one) by `part`, at
    clip frame `at`."""

    index: int | None
    part: Part
    at: float
    was: AttackWindow | None


class WindowDrag:
    """A drag on the Timeline's attack lanes, one lane per window and one to add on: a drag on
    the add lane draws a new window (a click: one without an end), an edge drags that edge, the
    span drags both; whole clip frames, inside the clip. `commit` takes the result on release;
    `preview` is what the lanes draw meanwhile."""

    def __init__(self, commit: Callable[[int | None, AttackWindow], object]) -> None:
        self._commit = commit
        self.grab: Grab | None = None
        self.preview: AttackWindow | None = None
        #: clip frames the lanes span, and the id a new window gets
        self.frames = 0
        self.id = 0

    def part_at(
        self, attacks: Sequence[AttackWindow], lane: int, frame: float, slop: float
    ) -> tuple[int | None, Part] | None:
        """What `frame` on `lane` grabs, `slop` frames from an edge counting as the edge."""
        if lane == len(attacks):
            return (None, "new") if lane < MOVE_ATTACKS else None
        if not 0 <= lane < len(attacks):
            return None
        a = attacks[lane]
        end = self.frames if a.end is None else a.end
        if abs(frame - a.frame) <= slop:
            return lane, "start"
        if abs(frame - end) <= slop:
            return lane, "end"
        if a.frame < frame < end:
            return lane, "body"
        return None

    def press(self, attacks: Sequence[AttackWindow], lane: int, frame: float, slop: float) -> bool:
        """Starts a drag where `part_at` finds something; whether it did."""
        got = self.part_at(attacks, lane, frame, slop)
        if got is None or self.frames <= 0:
            return False
        index, part = got
        self.grab = Grab(index, part, frame, None if index is None else attacks[index])
        self.preview = self._moved(frame)
        return True

    def move(self, frame: float) -> None:
        if self.grab is not None:
            self.preview = self._moved(frame)

    def release(self, frame: float) -> bool:
        """Commits the drag; whether it changed anything."""
        g = self.grab
        if g is None:
            return False
        w = self._moved(frame)
        self.cancel()
        if w == g.was:
            return False
        self._commit(g.index, w)
        return True

    def cancel(self) -> None:
        self.grab, self.preview = None, None

    @property
    def dragging(self) -> bool:
        return self.grab is not None

    def _moved(self, frame: float) -> AttackWindow:
        g = self.grab
        assert g is not None
        n = self.frames
        f = min(max(round(frame), 0), n)
        if g.part == "new" or g.was is None:
            a, b = sorted((min(max(round(g.at), 0), n), f))
            a = min(a, n - 1)
            return AttackWindow(self.id, a, None if b <= a else b)
        w = g.was
        if g.part == "start":
            hi = (n if w.end is None else w.end) - 1
            return dataclasses.replace(w, frame=min(f, hi))
        if g.part == "end":
            return dataclasses.replace(w, end=max(f, w.frame + 1))
        span = 0 if w.end is None else w.end - w.frame
        start = min(max(w.frame + round(frame - g.at), 0), n - 1 - max(span - 1, 0))
        return dataclasses.replace(w, frame=start, end=None if w.end is None else start + span)
