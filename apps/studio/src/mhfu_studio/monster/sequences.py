# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A port's sequences (`mhfu_port.sequence`) as document edits, each as a sentence.

No toolkit: the Moves panel calls these through the workspace, one undo step per edit; a refusal
is a `ManifestError` saying why. A pair move may be a step but is never edited here: an edit
that would change its `after` is refused.
"""

from __future__ import annotations

from dataclasses import dataclass

from mhfu_port import behaviour, continuity, sequence
from mhfu_port.continuity import Ends
from mhfu_port.manifest import Manifest, ManifestError, Move, Steer

from mhfu_studio.monster import authoring, clips
from mhfu_studio.monster.document import PortDocument

#: the clip picker's orders: by fit after a clip, by the source numbers near it
BY = (("fit", "Fits after"), ("near", "Neighbours"))


@dataclass(frozen=True)
class Step:
    """A move of a sequence as the strip and the bar show it."""

    name: str
    clip: str | None
    frames: int | None
    label: str
    own: bool


@dataclass(frozen=True)
class PickRow:
    """A clip of the manifest offered as a step."""

    name: str
    label: str
    frames: int | None
    fit: float | None
    """Degrees from the end of the clip before; None: not rated."""
    used: tuple[str, ...]


def used_in(m: Manifest, clip: str) -> list[str]:
    """The moves that play clip `clip`."""
    return [n for n, mv in m.moves.items() if mv.clip == clip]


def _place(m: Manifest, name: str) -> tuple[list[str], int]:
    """The steps of the sequence `name` is in, and its place among them."""
    steps = sequence.chain(m, sequence.head_of(m, name))
    return steps, steps.index(name)


def _own(m: Manifest, names: list[str]) -> None:
    for n in names:
        authoring.own(m, n)


def _clip(m: Manifest, clip: str) -> str:
    if clip not in m.clips:
        raise ManifestError(f"no clip {clip!r}")
    return clip


def append_step(doc: PortDocument, after_move: str, clip: str) -> str:
    """A new own move playing `clip`, handed to when `after_move` ends and handing on to what
    `after_move` handed to. It rides its sequence's head's carrier and stops for no wall."""
    m = doc.manifest
    _own(m, [after_move])
    _clip(m, clip)
    head = sequence.head_of(m, after_move)
    name = sequence.step_name(m, head)
    carrier = m.moves[head].carrier

    def add(m: Manifest) -> None:
        prev = m.moves[after_move]
        step = Move(clip=clip, after=prev.after, carrier=carrier, steer=Steer(walls=False))
        prev.after = name
        order = list(m.moves.items())
        order.insert(list(m.moves).index(after_move) + 1, (name, step))
        m.moves = dict(order)

    doc.edit(add)
    return f"moves.{name}: a step after {after_move}, playing {clip}"


def remove_step(doc: PortDocument, name: str) -> str:
    """Removes step `name`; the move before it hands to the move after it. Refused while a block
    or an effect names it, or two moves hand to it."""
    m = doc.manifest
    _own(m, [name])
    users = clips.move_users(m, name)
    before = [k for k, mv in m.moves.items() if mv.after == name]
    if len(users) > len(before) or len(before) > 1:
        raise ManifestError(f"{name} is still used by {', '.join(users)}: change that first")
    _own(m, before)
    then = m.moves[name].after

    def drop(m: Manifest) -> None:
        for k in before:
            m.moves[k].after = None if then == k else then
        del m.moves[name]
        behaviour.drop_move(m, name)  # its node on the canvas

    doc.edit(drop)
    return f"moves.{name} removed" + (f"; {before[0]} hands to {then}" if before and then else "")


def move_step(doc: PortDocument, name: str, delta: int) -> str:
    """Swaps step `name` with the step `delta` (-1 or 1) places on; the names stay."""
    m = doc.manifest
    steps, i = _place(m, name)
    j = i + delta
    if abs(delta) != 1 or not 0 <= j < len(steps):
        raise ManifestError(f"{name} is already the {'first' if delta < 0 else 'last'} step")
    now = steps.copy()
    now[i], now[j] = now[j], now[i]
    tail = m.moves[steps[-1]].after
    then: dict[str, str | None] = {n: now[k + 1] for k, n in enumerate(now[:-1])}
    then[now[-1]] = None if tail is None else now[steps.index(tail)]
    changed = [n for n in steps if m.moves[n].after != then[n]]
    _own(m, changed)

    def swap(m: Manifest) -> None:
        for n in changed:
            m.moves[n].after = then[n]

    doc.edit(swap)
    return f"{name} is now step {j + 1} of {len(steps)}"


def split(doc: PortDocument, name: str) -> str:
    """Ends the sequence before step `name`, which heads one of its own."""
    m = doc.manifest
    steps, i = _place(m, name)
    if i == 0:
        raise ManifestError(f"{name} already starts its sequence")
    _own(m, [steps[i - 1]])

    def cut(m: Manifest) -> None:
        m.moves[steps[i - 1]].after = None

    doc.edit(cut)
    return f"{name} starts a sequence of its own"


def set_clip(doc: PortDocument, name: str, clip: str) -> str:
    """Step `name` plays clip `clip`."""
    m = doc.manifest
    authoring.own(m, name)
    _clip(m, clip)

    def play(m: Manifest) -> None:
        m.moves[name].clip, m.moves[name].anim = clip, None

    doc.edit(play)
    return f"moves.{name} plays {clip}"


def pick_rows(m: Manifest, e: Ends | None, after: str | None, by: str = "fit") -> list[PickRow]:
    """The manifest's clips as steps to put after clip `after`, best first by `by` (`BY`); in
    the manifest's order when nothing rates them. `e`: the donor's end poses."""
    ids = {n: c.id for n, c in m.clips.items()}
    a = ids.get(after) if after is not None else None
    fit: dict[int, float] = {}
    if e is not None and a is not None and a in e.ids:
        fit = dict(continuity.fits_after(e, a, itself=True))
    if a is None:
        order: list[int] = []
    elif by == "fit":
        order = list(fit)
    else:
        order = continuity.neighbours(a, ids.values())
    place = {i: k for k, i in enumerate(order)}
    names = sorted(m.clips, key=lambda n: place.get(ids[n], len(place)))
    return [
        PickRow(n, m.clips[n].label, m.clips[n].frames, fit.get(ids[n]), tuple(used_in(m, n)))
        for n in names
    ]
