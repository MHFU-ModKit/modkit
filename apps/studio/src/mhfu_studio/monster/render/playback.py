# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A clip played the way the engine plays it, and its root travel; numpy only, no GL.

The engine's clip cursor (entity `+0x80 + slot*0x40`): `phase` in clip frames, `speed` clip
frames per game frame, set by the ACTION (2.0 and 2.4 both seen on one monster), and the gate
`phase + speed <= end` tested before each step. A clip has no duration of its own:
`end / speed / 30` seconds.
"""

from __future__ import annotations

import math
from typing import Protocol
from weakref import WeakKeyDictionary

import numpy as np
from mhfu_port import layout, records, travel
from mhfu_port.manifest import Clip as ManifestClip
from mhfu_port.manifest import Steer
from mhfu_port.model import MHFU, Clip

from mhfu_studio.monster.clip_browser import preview_slot
from mhfu_studio.monster.core.pose import Pose
from mhfu_studio.monster.core.scene import Scene

GAME_HZ = 30.0
DEFAULT_SPEED = 2.0
OBSERVED_SPEEDS = (1.0, 2.0, 2.4)
#: the longest real-time step `advance` takes: a stall must not replay seconds at once
MAX_STEP = 0.25


class Span(Protocol):
    @property
    def frames(self) -> int: ...

    @property
    def loop(self) -> bool: ...


def wall_clock(end: float, speed: float = DEFAULT_SPEED) -> float:
    """Seconds a clip of `end` frames takes at `speed`."""
    return float("inf") if speed <= 0 else float(end) / float(speed) / GAME_HZ


class Playback:
    """The engine's cursor; `advance` takes real seconds and runs whole 30 Hz game frames."""

    def __init__(
        self,
        end: float = 0.0,
        *,
        loop: bool = False,
        speed: float = DEFAULT_SPEED,
        loop_restart: float = 0.0,
    ) -> None:
        self.end = float(end)
        self.loop = bool(loop)
        self.speed = float(speed)
        self.loop_restart = float(loop_restart)
        self.phase = 0.0
        self.playing = False
        self._carry = 0.0

    def set_clip(self, clip: Span | None, *, keep_phase: bool = False) -> Playback:
        """Adopt a clip's span and loop flag; None parks at 0."""
        self.end = float(clip.frames) if clip is not None else 0.0
        self.loop = bool(clip.loop) if clip is not None else False
        self.phase = min(self.phase, self.end) if keep_phase else 0.0
        self._carry = 0.0
        return self

    @property
    def at_end(self) -> bool:
        """A one-shot clip the gate stops: the next step would pass its end."""
        return not self.loop and self.end > 0 and self.phase + self.speed > self.end

    def play(self) -> None:
        """Plays on; from the end of a one-shot, from frame 0."""
        if self.at_end:
            self.rewind()
        self.playing = True

    def pause(self) -> None:
        self.playing = False

    def toggle(self) -> None:
        if self.playing:
            self.pause()
        else:
            self.play()

    def seek(self, frame: float) -> None:
        self.phase = max(0.0, min(float(frame), self.end))
        self._carry = 0.0

    def step(self, game_frames: int = 1) -> None:
        """Whole game frames through the same gate; pauses."""
        self.playing = False
        for _ in range(abs(int(game_frames))):
            self._one(-1.0 if game_frames < 0 else 1.0)

    def rewind(self) -> None:
        self.phase = 0.0
        self._carry = 0.0

    def advance(self, dt: float) -> None:
        """`dt` real seconds at 30 Hz, the fraction of a game frame carried; no-op paused."""
        if not self.playing or self.end <= 0:
            return
        want = max(0.0, min(float(dt), MAX_STEP)) * GAME_HZ + self._carry
        whole = int(want + 1e-9)  # 144 fractions of 1/144 s sum to 29.999999999999996
        self._carry = max(0.0, want - whole)
        for _ in range(whole):
            if not self.playing:
                break
            self._one(1.0)

    def _one(self, direction: float) -> None:
        nxt = self.phase + self.speed * direction
        span = max(self.end - self.loop_restart, 1e-6)
        if direction > 0:
            if nxt <= self.end:
                self.phase = nxt
            elif self.loop:
                self.phase = self.loop_restart + (nxt - self.end - 1e-9) % span
            else:
                self.phase, self.playing = self.end, False
        elif nxt >= 0.0:
            self.phase = nxt
        else:
            self.phase = self.end - (-nxt) % span if self.loop else 0.0

    @property
    def progress(self) -> float:
        return 0.0 if self.end <= 0 else self.phase / self.end

    @property
    def duration(self) -> float:
        return wall_clock(self.end, self.speed)

    def __repr__(self) -> str:
        state = "playing" if self.playing else "paused"
        loop = " loop" if self.loop else ""
        return f"<Playback {self.phase:.1f}/{self.end:.0f} speed={self.speed:.2f} {state}{loop}>"


def leading_chain(parents: np.ndarray | list[int]) -> tuple[int, ...]:
    """The body fork and its ancestors, root first: where a location channel may land."""
    par = [int(p) for p in parents]
    if not par:
        return ()
    fork = records.body_fork(par)
    chain, seen, j = [fork], {fork}, par[fork]
    while 0 <= j < len(par) and j not in seen:
        chain.append(j)
        seen.add(j)
        j = par[j]
    return tuple(reversed(chain))


def root_joints(scene: Scene) -> tuple[int, ...]:
    return tuple(int(i) for i in np.flatnonzero(scene.rig.parents < 0))


def travel_joints(scene: Scene) -> tuple[int, ...]:
    """The joints whose location moves the whole animal: the leading chain, not the roots
    (a Tigrex's root carries no location; its hip carries ~357 u of travel)."""
    return leading_chain(scene.rig.parents) or root_joints(scene)


def pose_at(
    scene: Scene,
    clip: Clip | None,
    frame: float,
    *,
    strip_root: bool = False,
    steer: Steer | None = None,
    speed: float = DEFAULT_SPEED,
) -> Pose:
    """The rig at `frame`, turned as YAW turns while the clip plays (`yaw_at`); `strip_root`
    holds the travel joints' location at frame 0, so the clip plays in place without dropping
    the animal to its bind height."""
    if clip is None:
        return scene.bind_pose()
    curves = scene.curves(clip)
    rot, loc = curves.at(float(frame))
    if strip_root:
        js = list(travel_joints(scene))
        if js:
            loc = loc.copy()
            loc[js] = curves.at(0.0)[1][js]
    world = scene.world(rot, loc)
    th = yaw_at(scene, clip, frame, steer, speed)
    if th:
        world = yaw_matrix(th) @ world
    return Pose(scene.rig, float(frame), world, clip.slot)


def yaw_matrix(th: float) -> np.ndarray:
    """A turn of `th` radians about y, positive the way YAW grows: +z toward +x."""
    yaw = np.eye(4)
    yaw[0, 0] = yaw[2, 2] = math.cos(th)
    yaw[0, 2], yaw[2, 0] = math.sin(th), -math.sin(th)
    return yaw


def yaw_at(
    scene: Scene,
    clip: Clip,
    frame: float,
    steer: Steer | None = None,
    speed: float = DEFAULT_SPEED,
) -> float:
    """Radians YAW has turned at clip `frame`: the clip's turn (`turn_of`), or an own move's
    `steer` as the move player turns it (`fixed` evenly over its AI frames; `still`, `hunter`
    and `away` not at all here, as the studio has no hunter)."""
    if steer is None or steer.turn == "clip":
        t = turn_of(scene, clip)
        return 0.0 if t is None else float(t.at(float(frame)) - t.keys[0]) / travel.TURN * math.tau
    if steer.turn == "fixed" and steer.angle is not None and steer.frames:
        ai = min(float(frame) / max(speed, 1e-6), float(steer.frames))
        return math.radians(steer.angle) * ai / steer.frames
    return 0.0


def end_turn(scene: Scene, clip: Clip, steer: Steer | None = None) -> float:
    """Degrees YAW has turned when `clip` ends (`yaw_at`); a fixed steer's whole angle."""
    if steer is not None and steer.turn == "fixed":
        return float(steer.angle or 0.0)
    return math.degrees(yaw_at(scene, clip, float(clip.frames), steer))


_TURNS: WeakKeyDictionary[Scene, dict[tuple[int, float | None], travel.Turn | None]] = (
    WeakKeyDictionary()
)


def turn_of(scene: Scene, clip: Clip) -> travel.Turn | None:
    """What YAW turns while a port's `clip` plays (`travel.turn_of`): the turn the build took
    out of it, or the manifest's; None on a donor or a rig without a root."""
    if scene.game != MHFU or clip.joint_tracks is not None:
        return None
    m = scene.manifest
    authored = None
    if m is not None:
        given = (c.turn for c in m.clips.values() if _plays(c, scene, clip.slot))
        authored = next((t for t in given if t is not None), None)
    cache = _TURNS.setdefault(scene, {})
    key = (clip.slot, authored)
    if key not in cache:
        try:
            cache[key] = travel.turn_of(clip.source, scene.skeleton, authored)
        except ValueError:
            cache[key] = None
    return cache[key]


def _plays(c: ManifestClip, scene: Scene, slot: int) -> bool:
    """`c` is the clip in anim `slot`, or the preview of a clip in none (`preview_slot`)."""
    return layout.where(c, scene.placed) == slot or (slot < 0 and preview_slot(c.id) == slot)


def root_travel(scene: Scene, clip: Clip) -> tuple[float, float]:
    """`(net, peak)` travel of the travel joints over the clip, one sample per game frame."""
    roots = list(travel_joints(scene))
    if not roots or clip.frames <= 0:
        return 0.0, 0.0
    steps = max(2, min(int(clip.frames / DEFAULT_SPEED) + 1, 200))
    loc = scene.curves(clip).at(np.linspace(0.0, clip.frames, steps))[1]
    path = loc[:, roots].sum(axis=1)
    d = np.linalg.norm(path - path[0], axis=1)
    return float(np.linalg.norm(path[-1] - path[0])), float(d.max())
