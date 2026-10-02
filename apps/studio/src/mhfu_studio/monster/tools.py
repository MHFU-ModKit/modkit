# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The view's hands on hitboxes and hurtboxes: a click picks one, the shell's gizmo drags yours.

A volume's numbers are in its bone's space, so the gizmo's pose is the bone's world matrix moved
to the volume: its arrows are the bone's axes, the form's x, y and z. A drag previews in the
overlay and commits once, on release, through `MonsterWorkspace.edit_volume`, where the forms'
edits go too. A capsule's far end has a gizmo of its own.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

from mhfu_studio.shell.camera import Mat
from mhfu_studio.shell.input import Button, Key, Mod, Pointer
from mhfu_studio.shell.manipulator import Manipulation, Manipulator, Operation
from mhfu_studio.shell.overlay import Overlay
from mhfu_studio.shell.workspace import Gesture, Shortcut

if TYPE_CHECKING:
    from mhfu_studio.monster.render.hitboxes import HitboxOverlay, Volume
    from mhfu_studio.monster.workspace import MonsterWorkspace

#: the overlays: the Parts panel's hurtboxes and the Hitboxes panel's hitboxes
HURT, HIT = "hitboxes", "attacks"
NOUN = {HURT: "hurtbox", HIT: "hitbox"}
PANEL = {HURT: "Parts", HIT: "Hitboxes"}
MOVE, SIZE = "move", "size"
OPS: dict[str, Operation] = {MOVE: "translate", SIZE: "scale"}
MOVE_KEY = Shortcut(("W",), "The picked hitbox or hurtbox gets arrows to move it")
SIZE_KEY = Shortcut(("R",), "The picked hitbox or hurtbox gets boxes to resize it")
FRAME = Shortcut(("F",), "Points the camera at the picked hitbox or hurtbox")
DESELECT = Shortcut(("Escape",), "Lets go of the picked hitbox or hurtbox, or drops a drag")
KEYS = (MOVE_KEY, SIZE_KEY, FRAME, DESELECT)
#: points a click may travel and still pick, not orbit
CLICK_SLOP = 4.0
#: what the form shows; a drag commits no finer
DECIMALS = 1
MIN_RADIUS = 0.1
End = Literal["near", "far"]


def describe(fields: dict[str, Any]) -> str:
    """`radius=300.0, bone=4`: an edit for the status line."""
    return ", ".join(f"{k}={v}" for k, v in fields.items())


def _pt(v: Any) -> tuple[float, float, float]:
    x, y, z = (round(float(c), DECIMALS) for c in v)
    return x, y, z


@dataclass(frozen=True)
class _Drag:
    which: str
    index: int
    end: End
    op: Operation
    #: the volume, its bone's world matrix and the gizmo's pose at the press
    start: Volume
    frame: Mat
    pose: Mat
    #: Shift on the near end: the far end comes along
    both: bool


class VolumeTools:
    """Picking, the gizmo, the keys and the status hint for volumes in the monster's view."""

    def __init__(self, ws: MonsterWorkspace) -> None:
        self.ws = ws
        self.near, self.far = Manipulator(), Manipulator()
        self.reset()

    def reset(self) -> None:
        self.tool = MOVE
        #: the overlay picked last: its volume gets the gizmo
        self.last = HIT
        self._drag: _Drag | None = None
        self._press: tuple[float, float] | None = None
        self.near.cancel()
        self.far.cancel()

    # what is picked

    def overlay(self, which: str) -> HitboxOverlay | None:
        vp = self.ws.vp
        if vp is None or not (self.ws.show_attacks if which == HIT else self.ws.show_parts):
            return None
        return vp.attacks if which == HIT else vp.hitboxes

    def selected(self, which: str) -> int | None:
        return self.ws.selected_attack_volume if which == HIT else self.ws.selected_volume

    def select(self, which: str, index: int | None) -> None:
        if which == HIT:
            self.ws.select_attack_volume(index)
        else:
            self.ws.select_volume(index)

    def editable(self, which: str) -> bool:
        """Yours, not the base monster's."""
        from mhfu_studio.monster.workspace import PORT

        return (self.ws.attacks_source if which == HIT else self.ws.parts_source) == PORT

    def picked(self) -> tuple[str, int, Volume, HitboxOverlay] | None:
        """The picked volume the view shows, the last picked first."""
        for which in (self.last, HURT if self.last == HIT else HIT):
            ov, i = self.overlay(which), self.selected(which)
            if ov is not None and i is not None and ov.shows(i):
                return which, i, ov.volumes[i], ov
        return None

    def target(self) -> tuple[str, int, Volume, HitboxOverlay] | None:
        """The picked volume when it is yours: the one the gizmo drags."""
        got = self.picked()
        return got if got is not None and self.editable(got[0]) else None

    # the view's hooks

    def pointer(self, ev: Pointer) -> Gesture:
        """The gizmo first; else a left click that does not travel picks."""
        t = self.target()
        if t is None:
            self.cancel()
        elif ev.kind != "wheel":
            got = self._gizmo(ev, *t)
            if got.consumed or got.done or got.over:
                self._press = None
                return got.consumed
        if ev.kind == "press" and ev.button == Button.LEFT:
            self._press = ev.pos
        elif ev.kind == "release" and ev.button == Button.LEFT and self._press is not None:
            (x, y), self._press = self._press, None
            if abs(ev.x - x) + abs(ev.y - y) <= CLICK_SLOP:
                self.click(ev.x, ev.y, ev.size)
        return Gesture.NONE

    def key(self, ev: Key) -> bool:
        if ev.mods & (Mod.CTRL | Mod.ALT):
            return False
        got = self.picked()
        if ev.name == DESELECT.keys[0]:
            if self._drag is not None:
                self.cancel()
            elif got is not None:
                self.select(got[0], None)
            return got is not None
        if got is None:
            return False
        which, i = got[0], got[1]
        tools = {MOVE_KEY.keys[0]: MOVE, SIZE_KEY.keys[0]: SIZE}
        if ev.name in tools:
            if not self.editable(which):
                self.ws.message = self.read_only(which)
            elif self.tool != tools[ev.name]:
                self.cancel()
                self.tool = tools[ev.name]
            return True
        if ev.name == FRAME.keys[0]:
            self.frame(which, i)
            return True
        return False

    def hint(self) -> str:
        """The picked volume and the keys that act on it; empty with none picked."""
        got = self.picked()
        if got is None:
            return ""
        which, i, v, _ = got
        name = f"{NOUN[which]} {i}" + (f" of hit group {v.group}" if which == HIT else "")
        if not self.editable(which):
            return f"the base monster's {NOUN[which]}: {self.read_only(which)} · F frame"
        if self.tool == SIZE:
            does = "drag a box to resize it"
        elif v.b is not None:
            does = "drag the arrows at either end to move it; Shift moves both ends"
        else:
            does = "drag the arrows to move it"
        keys = "W move · R size" if self.tool == SIZE else "R size"
        return f"{name}: {does} · {keys} · F frame · Esc deselect"

    @staticmethod
    def read_only(which: str) -> str:
        return f"read only. Edit yours in {PANEL[which]} to change it"

    def paint(self, o: Overlay) -> None:
        t, vp = self.target(), self.ws.vp
        if t is None or vp is None:
            return
        _, _, v, ov = t
        op = OPS[self.tool]
        for end, pose in self._poses(ov, v).items():
            m = self.near if end == "near" else self.far
            m.paint(o, vp.camera, o.size, pose, op, local=True)

    # picking

    def click(self, x: float, y: float, size: tuple[int, int]) -> None:
        """Selects the volume under `(x, y)` (a hitbox over a hurtbox), else the joint; a second
        click lets go."""
        vp = self.ws.vp
        if vp is None or vp.skeleton is None:
            return
        for which in (HIT, HURT):
            ov = self.overlay(which)
            v = None if ov is None else ov.pick(vp.camera, size, x, y)
            i = None if ov is None or v is None else ov.index_of(v)
            if i is not None:
                self.select(which, None if self.selected(which) == i else i)
                return
        hit = vp.skeleton.pick(vp.camera, size, x, y)
        if hit is not None:
            vp.select_joint(None if vp.selected_joint == hit else hit)

    def frame(self, which: str, index: int | None) -> None:
        """Points the camera at volume `index` of `which`, as the pose places it."""
        vp, ov = self.ws.vp, self.overlay(which)
        if vp is None or ov is None or index is None or not 0 <= index < len(ov.volumes):
            return
        a, b = ov.place(ov.volumes[index])
        vp.camera.fly_to(a if b is None else (a + b) * 0.5)

    # the gizmo

    def _poses(self, ov: HitboxOverlay, v: Volume) -> dict[End, Mat]:
        """The bone's matrix at the near end and a capsule's far end; sizing, at its middle."""
        f = ov.frame(v)
        a, b = ov.place(v)
        if self.tool == SIZE:
            at = {"near": a if b is None else (a + b) * 0.5}
        else:
            at = {"near": a} if b is None else {"far": b, "near": a}
        out: dict[End, Mat] = {}
        for end, p in at.items():
            m = np.array(f, np.float64)
            m[:3, 3] = p
            out[end] = m  # type: ignore[index]
        return out

    def _gizmo(self, ev: Pointer, which: str, i: int, v: Volume, ov: HitboxOverlay) -> Manipulation:
        vp = self.ws.vp
        assert vp is not None
        poses = self._poses(ov, v)
        drag = self._drag
        if drag is not None and (drag.which, drag.index) != (which, i):
            self.cancel()
            drag = None
        got = Manipulation(np.eye(4), False, False)
        taken: End | None = None if drag is None else drag.end
        for end, pose in poses.items():
            m = self.near if end == "near" else self.far
            heard = ev if taken in (None, end) else _leave(ev)
            out = m.pointer(heard, vp.camera, pose, OPS[self.tool], local=True)
            if heard is ev and taken is None and (out.using or out.over):
                taken = end
            if taken == end:
                got = out
        if taken is None:
            return got
        if got.using and self._drag is None:  # the press that took a handle
            self._drag = _Drag(
                which,
                i,
                taken,
                OPS[self.tool],
                v,
                ov.frame(v).copy(),
                poses[taken].copy(),
                taken == "near" and Mod.SHIFT in ev.mods,
            )
            vp.playback.pause()  # a volume riding a playing clip runs from the pointer
        elif got.using and self._drag is not None:
            ov.preview(i, self._moved(self._drag, got.matrix))
        elif got.done and self._drag is not None:
            self._commit(self._drag, self._moved(self._drag, got.matrix), ov)
        return got

    def _moved(self, d: _Drag, matrix: Mat) -> Volume:
        """The volume after the gizmo went to `matrix`, in its bone's numbers."""
        v = d.start
        if d.op == "scale":
            # an even drag scales by f, an axis drag by f along it: f is the odd eigenvalue
            lin = np.asarray(matrix, np.float64)[:3, :3] @ np.linalg.inv(d.pose[:3, :3])
            w = np.linalg.eigvalsh(0.5 * (lin + lin.T))
            f = float(w[np.argmax(np.abs(w - 1.0))])
            return dataclasses.replace(v, radius=max(round(v.radius * f, DECIMALS), MIN_RADIUS))
        p = _pt((np.linalg.inv(d.frame) @ np.append(np.asarray(matrix)[:3, 3], 1.0))[:3])
        if d.end == "far":
            return dataclasses.replace(v, b=p)
        b = v.b
        if d.both and b is not None:
            b = _pt(np.add(b, np.subtract(p, v.a)))
        return dataclasses.replace(v, a=p, b=b)

    def _commit(self, d: _Drag, v: Volume, ov: HitboxOverlay) -> None:
        self._drag = None
        fields: dict[str, Any] = {}
        if v.radius != d.start.radius:
            fields["radius"] = v.radius
        if v.a != d.start.a:
            fields["offset"] = list(v.a)
        if v.b != d.start.b and v.b is not None:
            fields["to"] = list(v.b)
        if not fields or not self.ws.edit_volume(d.which, d.index, **fields):
            ov.preview(d.index, None)

    def cancel(self) -> None:
        """Drops a drag under way: the volume goes back."""
        d, self._drag = self._drag, None
        self.near.cancel()
        self.far.cancel()
        ov = None if d is None else self.overlay(d.which)
        if d is not None and ov is not None and 0 <= d.index < len(ov.volumes):
            ov.preview(d.index, None)

    @property
    def dragging(self) -> bool:
        return self._drag is not None


def _leave(ev: Pointer) -> Pointer:
    """`ev` as a leave: a gizmo another one took the pointer from lets go of its hover."""
    return dataclasses.replace(ev, kind="leave")
