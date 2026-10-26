# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""How an MHP3rd handler moves its monster by code: the Hermite curves it samples and the
kinematic cells its helpers act on.

    motion = Motion(ovl)
    motion.begin(cursor=40.0, end=70.0, step=2.0)
    motion.call(VEL_ACC, ())          # position += velocity turned by yaw, then velocity += accel
    motion.cells[Y]                   # the height over the floor, as the handler's tests read it

A walk (`p3hits.Walk`) hands `cells` to the walker, which reads and writes them as the handler's
float loads and stores go, and answers each helper call through `call`. The frame is the monster's
at the first playing frame of a step (x toward its +x, its left; z forward), yaw counted from there;
the cursor is the playing clip's, in clip frames, `step` of them to an AI frame. The donor's x is
MHFU's: a clip that looks to the monster's right (the Zinogre's 40) has its head at -x in the
port's model space and its code slides it toward -x.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import NamedTuple

from mhfu.memory import Memory
from mhfu.mips import Fpr, Gpr

ARGS = (
    Gpr.a1,
    Gpr.a2,
    Gpr.a3,
    Gpr.t0,
    Fpr.fa0,
    Fpr.fa0f,
    Gpr.t1,
    Fpr.fa1,
    Fpr.fa1f,
    Fpr.ft4,
    Fpr.ft4f,
    Fpr.ft5,
)
"""The registers a walk reads at a call: a1-a3 and t0, $f12 and $f13 (a cursor gate's two frames),
then t1 and $f14-$f18 (what the lunge helper takes)."""
A1, A2, A3, T0, F12, F13, _T1, F14, F15, F16, F17, F18 = range(len(ARGS))
"""Where each sits in a call's `args`."""

CURSOR = 0x09C9DF3C  # noaddr: () $f0 the playing clip's cursor, in clip frames
CLIP_END = 0x09C9DF68  # noaddr: () $f0 the playing clip's last frame
SCALE = 0x09C9E888  # noaddr: () $f0 the monster's size against its species'
VEL_APPLY = 0x09CA1BB0  # noaddr: (entity, angles) position += velocity turned by the yaw
VEL_ACC = 0x09CA20EC  # noaddr: (entity, angles) VEL_APPLY, then velocity += acceleration
VEL_WORLD = 0x09CA1F34  # noaddr: position += velocity, unturned
VEL_ZERO = 0x09CA19E0  # noaddr: velocity = 0
VEL_ACC_ZERO = 0x09CA1B58  # noaddr: velocity and acceleration = 0
LIFT = 0x09C9E8D4  # noaddr: (entity, $f12) height += $f12
ROT_Y = 0x08875BD8  # noaddr: (vector, $f12 radians) turns x and z about y
YAW_SPREAD = 0x09CA15AC  # noaddr: (entity, total, $f12 lo, $f13 hi) yaw += total over (lo, hi]
CURVE_DELTA = 0x09DB3FB4  # noaddr: (entity, curve, $f12 t) $f0 the curve's change over a step
LUNGE = 0x09DB44F0  # noaddr: (entity, bearing, $f12..$f18) travel toward the target
CURVE_MOVE = 0x09DB4030  # noaddr: (entity, x, y, z, yaw curves, bias) a curve walk
CURVE_MOVE_INIT = 0x09DB3FFC  # noaddr: (entity) zero the velocity, keep the yaw

X, Y, Z = 0x80, 0x84, 0x88
"""The entity's position cells."""
VELOCITY, ACCELERATION, GROUND = 0xB0, 0xC0, 0xD8
"""Velocity (x, y, z), acceleration (x, y, z) and the floor height, in cells 4 bytes apart."""
SPEED = 0xAC4
"""The cell of the clip's speed, the factor a handler's turn rates go by: 1.0 in a walk."""
REST = 298.0
"""The height over the floor a monster stands at: the value the donor's stamp snaps to when it
lands, and the one its landing test reads (`Y - D8 <= 298`). Not read from a cell: MOVE_MODE(0)
sets Y to D8 when the mode changes, which would make it 0, and a jump then comes down in 21 and
10 AI frames, not 22 and 18."""
TURN = 0x10000
"""YAW units in a full turn."""
MAX_KEYS = 256
"""Keys a curve is read with: the donor's hold a few dozen."""
_HEADER = struct.Struct("<HhI")
_KEY = struct.Struct("<fiff")


class Key(NamedTuple):
    """A curve key: a value at a clip frame with the slopes (per frame) it arrives and leaves by."""

    value: float
    time: int
    ease_in: float
    ease_out: float


@dataclass(frozen=True)
class Curve:
    """A Hermite curve of clip frames, as the donor's curve sampler reads it."""

    keys: tuple[Key, ...]

    @classmethod
    def read(cls, mem: Memory, at: int) -> Curve:
        """The curve whose header, `{u16 keys, s16 hint, u32 table}`, is at `at`."""
        count, _, table = mem.unpack(_HEADER, at)
        if count > MAX_KEYS:
            raise ValueError(f"the curve at 0x{at:08X} claims {count} keys")
        keys = tuple(Key(*mem.unpack(_KEY, table + _KEY.size * i)) for i in range(count))
        if not keys:
            raise ValueError(f"the curve at 0x{at:08X} has no keys")
        return cls(keys)

    def at(self, t: float) -> float:
        """The value at clip frame `t`: the first key's before it, the last's after."""
        keys = self.keys
        if t <= keys[0].time:
            return keys[0].value
        for a, b in zip(keys, keys[1:], strict=False):
            if t <= b.time and b.time > a.time:
                return _hermite(t, a, b)
        return keys[-1].value

    def delta(self, t: float, step: float) -> float:
        """The change over the `step` frames up to `t` (from the start at the earliest); 0 at the
        start."""
        return 0.0 if t == 0 else self.at(t) - self.at(max(t - step, 0.0))


def _hermite(t: float, a: Key, b: Key) -> float:
    span = b.time - a.time
    u = (t - a.time) / span
    return (
        (2 * u**3 - 3 * u**2 + 1) * a.value
        + (u**3 - 2 * u**2 + u) * span * a.ease_out
        + (3 * u**2 - 2 * u**3) * b.value
        + (u**3 - u**2) * span * b.ease_in
    )


class Lunge(NamedTuple):
    """A handler's lunge: `base + range * min((distance - near) / span, 1)` units toward the
    target across the clip frames `window` (lo, hi], none under `near`."""

    window: tuple[float, float]
    near: float
    span: float
    base: float
    range: float


class Spread(NamedTuple):
    """A turn of `total` YAW units spread over the clip frames `window` (lo, hi]."""

    total: int
    window: tuple[float, float]

    @property
    def degrees(self) -> float:
        return self.total / TURN * 360


class Pose(NamedTuple):
    """A position in the frame of the step it moved in."""

    x: float
    y: float
    z: float


Args = tuple[int | None, ...]
"""What a call's `ARGS` registers held, where known."""


def _float(bits: int | None) -> float | None:
    if bits is None:
        return None
    return float(struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0])


def _signed(bits: int) -> int:
    bits &= 0xFFFFFFFF
    return bits - (1 << 32) if bits & 0x80000000 else bits


def _turned(x: float, z: float, yaw: int) -> tuple[float, float]:
    """`x` and `z` turned about y by `yaw` YAW units, as the donor's rotation does."""
    a = yaw / TURN * math.tau
    s, c = math.sin(a), math.cos(a)
    return x * c + z * s, z * c - x * s


@dataclass
class Motion:
    """The kinematic cells of one walk and the effect of each helper on them."""

    mem: Memory
    """Where the curves are."""
    rest: float = REST
    """The height over the floor the monster starts at, the one its jumps are counted from."""
    cells: dict[int, float] = field(default_factory=dict)
    """The float cells by entity offset: what a walker run reads and writes."""
    cursor: float | None = None
    end: float | None = None
    step: float = 2.0
    yaw: int = 0
    """YAW units turned since the step began."""
    yaw0: int = 0
    """The yaw a curve walk began at."""
    handed: float = 0.0
    """The height `LIFT` added this AI frame: it belongs to the step the frame ends in."""
    lost: str = ""
    """What the model could not follow: a value it does not know, a curve it cannot read."""
    events: list[Lunge | Spread] = field(default_factory=list)
    """What helpers asked for, for the walk to take."""
    _curves: dict[int, Curve] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for base in (X, VELOCITY, ACCELERATION):
            self.cells.update({base: 0.0, base + 4: 0.0, base + 8: 0.0})
        self.cells[Y] = self.rest
        self.cells[GROUND] = 0.0
        self.cells[SPEED] = 1.0

    # --- a step and an AI frame ---

    def begin(self, cursor: float | None, end: float | None, step: float) -> None:
        """An AI frame starts: the clip's cursor and last frame (None where none plays)."""
        self.cursor, self.end, self.step, self.handed = cursor, end, step, 0.0

    def turn_over(self) -> None:
        """A step starts: the frame is the monster's now."""
        self.yaw = self.yaw0 = 0

    def now(self) -> Pose:
        c = self.cells
        return Pose(c.get(X, 0.0), c.get(Y, self.rest), c.get(Z, 0.0))

    def before_hand_over(self) -> Pose:
        """Where it is without the height this frame's `LIFT` added."""
        p = self.now()
        return Pose(p.x, p.y - self.handed, p.z)

    # --- the helpers ---

    def call(self, target: int, args: Args) -> float | None:
        """The effect of a call to `target`, and its $f0 where it answers one."""
        if target == CURSOR:
            return self.cursor
        if target == CLIP_END:
            return self.end
        if target == SCALE:
            return 1.0
        helper = _HELPERS.get(target)
        return None if helper is None else helper(self, args)

    def _lose(self, why: str) -> None:
        self.lost = self.lost or why

    def _axes(self, base: int) -> tuple[float, float, float] | None:
        c = self.cells
        x, y, z = (c.get(base + 4 * i) for i in range(3))
        if x is None or y is None or z is None:
            self._lose(f"the cell 0x{base:02X} is not known")
            return None
        return x, y, z

    def _move(self, yaw: int | None) -> None:
        """Position += velocity, turned by `yaw` YAW units (None: not turned)."""
        v, p = self._axes(VELOCITY), self._axes(X)
        if v is None or p is None:
            return
        x, y, z = v
        if yaw:
            x, z = _turned(x, z, yaw)
        self.cells.update({X: p[0] + x, Y: p[1] + y, Z: p[2] + z})

    def _vel_apply(self, args: Args) -> float | None:
        self._move(self.yaw)
        return None

    def _vel_acc(self, args: Args) -> float | None:
        self._move(self.yaw)
        v, a = self._axes(VELOCITY), self._axes(ACCELERATION)
        if v is not None and a is not None:
            for i in range(3):
                self.cells[VELOCITY + 4 * i] = v[i] + a[i]
        return None

    def _vel_world(self, args: Args) -> float | None:
        self._move(None)
        return None

    def _vel_zero(self, args: Args) -> float | None:
        self.cells.update({VELOCITY: 0.0, VELOCITY + 4: 0.0, VELOCITY + 8: 0.0})
        return None

    def _vel_acc_zero(self, args: Args) -> float | None:
        self._vel_zero(args)
        self.cells.update({ACCELERATION: 0.0, ACCELERATION + 4: 0.0, ACCELERATION + 8: 0.0})
        return None

    def _lift(self, args: Args) -> float | None:
        height = _float(args[F12])
        if height is None or Y not in self.cells:
            self._lose("a height it adds is not known")
        else:
            self.cells[Y] += height
            self.handed += height
        return None

    def _rot_y(self, args: Args) -> float | None:
        """The handler turns its velocity by the yaw, into world axes."""
        v = self._axes(VELOCITY)
        if v is not None:
            self.cells[VELOCITY], self.cells[VELOCITY + 8] = _turned(v[0], v[2], self.yaw)
        return None

    def _curve(self, at: int | None) -> Curve | None:
        if at is None:
            self._lose("a curve is not known")
            return None
        if at not in self._curves:
            try:
                self._curves[at] = Curve.read(self.mem, at)
            except (ValueError, LookupError, struct.error) as err:
                self._lose(f"the curve at 0x{at:08X} cannot be read ({err})")
                return None
        return self._curves[at]

    def _curve_delta(self, args: Args) -> float | None:
        curve, t = self._curve(args[A1]), _float(args[F12])
        if curve is None or t is None:
            self._lose("a curve's cursor is not known")
            return None
        return curve.delta(t, self.step)

    def _curve_move_init(self, args: Args) -> float | None:
        self._vel_acc_zero(args)
        self.yaw0 = self.yaw
        return None

    def _curve_move(self, args: Args) -> float | None:
        """The x, y and z curves read at the cursor into the velocity (a curve left out leaves its
        axis alone), y then zeroed, the move turned by the yaw `CURVE_MOVE_INIT` kept."""
        if self.cursor is None:
            self._lose("a curve walk without a clip")
            return None
        if args[T0]:
            self._lose("a curve walk's yaw curve is not followed")
        for axis, arg in enumerate((A1, A2, A3)):
            if args[arg]:
                curve = self._curve(args[arg])
                if curve is None:
                    return None
                self.cells[VELOCITY + 4 * axis] = curve.delta(self.cursor, self.step)
        self.cells[VELOCITY + 4] = 0.0
        self._move(self.yaw0)
        return None

    def _yaw_spread(self, args: Args) -> float | None:
        total, lo, hi = args[A1], _float(args[F12]), _float(args[F13])
        if total is None or lo is None or hi is None or hi <= lo:
            self._lose("a turn's total or window is not known")
            return None
        spread = Spread(_signed(total), (lo, hi))
        if spread not in self.events:
            self.events.append(spread)
        if self.cursor is None or self.end is None:
            return None
        if not lo < min(self.cursor + self.step, self.end) <= hi + self.step:
            return None
        part = self.step + min(self.cursor - lo, 0.0)  # the frames of the step inside the window
        part -= max(part + self.cursor - hi, 0.0)
        self.yaw += int(part * (spread.total / (hi - lo)))
        return None

    def _lunge(self, args: Args) -> float | None:
        lo, hi, near, far = (_float(args[i]) for i in (F12, F13, F15, F16))
        v0, v1 = _float(args[F17]), _float(args[F18])
        if lo is None or hi is None or near is None or far is None or v0 is None or v1 is None:
            self._lose("a lunge's window or lengths are not known")
        elif far <= near:
            self._lose("a lunge's distances do not grow")
        else:
            lunge = Lunge((lo, hi), near, far - near, v0, v1 - v0)
            if lunge not in self.events:
                self.events.append(lunge)
        return None


_HELPERS: dict[int, Callable[[Motion, Args], float | None]] = {
    VEL_APPLY: Motion._vel_apply,
    VEL_ACC: Motion._vel_acc,
    VEL_WORLD: Motion._vel_world,
    VEL_ZERO: Motion._vel_zero,
    VEL_ACC_ZERO: Motion._vel_acc_zero,
    LIFT: Motion._lift,
    ROT_Y: Motion._rot_y,
    YAW_SPREAD: Motion._yaw_spread,
    CURVE_DELTA: Motion._curve_delta,
    LUNGE: Motion._lunge,
    CURVE_MOVE: Motion._curve_move,
    CURVE_MOVE_INIT: Motion._curve_move_init,
}
"""The effect of each helper on a `Motion`."""
TARGETS = frozenset(_HELPERS)
"""The helpers the model acts for, which a walk does not descend into."""
