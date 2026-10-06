# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Getting around a quest map: its exits, an area change on request, and walks planned over the
floor.

    plan = Map.live(s, Extracted.find())   # the map on screen, every stage's exits
    goto(s, point, plan)                   # one area change straight to the point
    walk(s, point, plan)                   # walked: exit to exit, on paths over the floor

An exit fires when the hunter's position is inside its trigger (INTERSECTING_EXIT, every frame
in EXIT_CHECK), walked in, knocked in or teleported in. The same change can be asked for: stop
at AREA_CHANGE_POLL, point MONSTER_MANAGER.EXIT at a STAGE_EXIT, set REQUESTS bit 0 and
HUNTER.EXITING. EXIT_CHECK rewrites EXIT every frame, so a write while the game runs is lost or
reaches the poll as 0 and crashes it. The record's target, landing and facing may be rewritten in
that stop: the area change copies them at once and the next stage's overlay replaces the record.
"""

from __future__ import annotations

import heapq
import itertools
import math
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from .. import addresses as a
from .. import points
from ..files import Extracted
from ..points import Point
from ..stage import (
    WALL_CHUNK,
    Exit,
    Floor,
    NotLoaded,
    StageOverlay,
    Triangle,
    map_manager,
    read_map_table,
)
from ..structs import AreaChange, Hunter
from . import area, boot, rig
from . import navigation as nav
from .navigation import XZ
from .session import Session

Vec3 = tuple[float, float, float]
Log = Callable[[str], None]

STEP = 100.0
"""Spacing of the walk planner's floor samples; the hunter is about 100 wide."""
RISE = 1.0
"""Climb per unit walked the planner allows (45 degrees); a drop is any height."""
LEVEL = 150.0
"""Floor heights closer than this under one sample are one level."""
HEADROOM = 900.0
"""A level with another this close above is under a tent, a rock or a crate: not walkable."""
STEEP = 0.7
"""A wall-chunk triangle with |normal y| under this stops the hunter."""
CLEARANCE = 75.0
"""Samples this close to a wall in x and z are not walked: every point of a sample's square is
within step / sqrt(2) of its centre."""
BODY = (40.0, 250.0)
"""A wall blocks a level whose floor plus this band it overlaps: a face that ends at the feet
is a ledge to step off."""
SEARCH = 250_000
"""Planner nodes expanded before it gives up."""
LEG = 1500.0
"""Longest straight leg of a planned path; a turning camera drifts on longer ones."""
REPLANS = 4
"""Walks re-planned around a spot that blocked the hunter, per stage."""
CLIMB_COST = 300.0
"""What a climb counts as in walked units, besides its height."""
CLIMB_MATERIALS = (9, 10)
"""Collision materials of a climbable wall: 10 roots and vines, 9 rock (`file-formats`)."""
VERTICAL = 0.34
"""A climbable triangle's |normal y| is under this; material 9 is also flat bulk floor."""
FOOT = 100.0
"""How far in front of a climbable wall its foot is."""
TOP = 150.0
"""How far behind a climbable wall's top edge the hunter stands after it."""
LEDGE = (120.0, 350.0)
"""A step this high is climbed onto (section 1's two: 200 and 300); lower is walked."""
LEDGE_SLACK = 100.0
"""A ledge's floor is this close to its wall's foot and top."""
LEDGE_EVERY = 300.0
"""Spacing of the climbs found along one ledge."""
LEDGE_WIDTH = 150.0
"""A face narrower than this is a corner, where a climb slides off."""
BUCKET = 500.0
"""Cell size of the spatial buckets that join wall triangles into faces."""
CLIMB_SPEED = 60.0
"""Units a second a climb gains, a little under the 75 measured on a section-2 root."""
CHANGE_COST = 1000.0
"""What an area change counts as in walked units: the load."""
CLIMB_TRIES = ((0.0, 0.0), (0.0, 60.0), (0.0, -60.0), (-12.0, 0.0), (12.0, 0.0))
"""(degrees turned, units shifted along the wall) per try: a grab is sensitive to where the
hunter meets the wall as well as to the angle."""
OVERRIDE = 250.0
"""A hand-set climb point replaces the climbs found this close to it."""
CLIMB_AT = 45.0
"""How close to its foot a climb starts."""
CLIMB_LIFT = 40.0
"""Height a climb must gain to count."""
CHANGE_TIMEOUT = 20.0


class NoPath(LookupError):
    """The planner found no walk on the floor; a ledge needs a climb point."""


# --- exits ---


@dataclass(frozen=True)
class Gate:
    """One STAGE_EXIT of `stage`, as the engine tests it, and its live record's address."""

    stage: int
    target: int
    trigger: Vec3
    radius: float
    height: float
    dest: Vec3
    yaw: int
    address: int
    end: Vec3 | None = None
    """A capsule's far end (STAGE_EXIT.FLAG 1); None for the cylinder."""

    @classmethod
    def of(cls, stage: int, e: Exit) -> Gate:
        end = e.end if e.flag == 1 else None
        return cls(stage, e.target, e.trigger, e.radius, e.height, e.dest, e.yaw, e.base, end)

    @property
    def centre(self) -> XZ:
        tx, _, tz = self.trigger
        if self.end is None:
            return tx, tz
        return (tx + self.end[0]) / 2, (tz + self.end[2]) / 2

    def holds(self, x: float, y: float, z: float) -> bool:
        """The trigger test of INTERSECTING_EXIT; a capsule is measured in x and z."""
        tx, ty, tz = self.trigger
        if not ty <= y < ty + self.height:
            return False
        if self.end is None:
            return (x - tx) ** 2 + (z - tz) ** 2 <= self.radius**2
        return _segment_distance((x, z), (tx, tz), (self.end[0], self.end[2])) <= self.radius

    def spot(self, floor: Floor, step: float = STEP) -> Vec3 | None:
        """A point on the floor inside the trigger, the nearest to its centre; None for none."""
        cx, cz = self.centre
        rings = int(self.radius // step) + 1
        for k in range(rings):
            r = k * step
            n = max(1, int(2 * math.pi * r // step))
            for j in range(n):
                x, z = (
                    cx + r * math.sin(2 * math.pi * j / n),
                    cz + r * math.cos(2 * math.pi * j / n),
                )
                for y in floor.heights(x, z):
                    if self.holds(x, y, z):
                        return x, y, z
        return None


def _triangle_distance(p: XZ, corners: Sequence[XZ]) -> float:
    """From `p` to a triangle in x and z: 0 inside, else to its nearest edge."""
    (ax, az), (bx, bz), (cx, cz) = corners
    px, pz = p
    d = (
        (bx - ax) * (pz - az) - (bz - az) * (px - ax),
        (cx - bx) * (pz - bz) - (cz - bz) * (px - bx),
        (ax - cx) * (pz - cz) - (az - cz) * (px - cx),
    )
    if all(v >= 0 for v in d) or all(v <= 0 for v in d):
        return 0.0
    return min(_segment_distance(p, corners[k], corners[(k + 1) % 3]) for k in range(3))


def _segments_cross(p: XZ, q: XZ, a: XZ, b: XZ) -> bool:
    def side(o: XZ, u: XZ, v: XZ) -> float:
        return (u[0] - o[0]) * (v[1] - o[1]) - (u[1] - o[1]) * (v[0] - o[0])

    return side(p, q, a) * side(p, q, b) < 0 and side(a, b, p) * side(a, b, q) < 0


def _segment_distance(p: XZ, a0: XZ, a1: XZ) -> float:
    (px, pz), (ax, az), (bx, bz) = p, a0, a1
    dx, dz = bx - ax, bz - az
    length = dx * dx + dz * dz
    t = 0.0 if not length else max(0.0, min(1.0, ((px - ax) * dx + (pz - az) * dz) / length))
    return math.hypot(px - ax - t * dx, pz - az - t * dz)


@dataclass
class Map:
    """The stages of one map row and their exits, from the extracted stage overlays (which load
    at a fixed address, so a record's address is its live one)."""

    stages: tuple[int, ...]
    gates: dict[int, list[Gate]]
    game: Extracted | None = None
    _grids: dict[tuple[int, tuple[Point, ...]], NavGrid] = field(default_factory=dict)

    @classmethod
    def read(cls, game: Extracted, stages: Iterable[int]) -> Map:
        stages = tuple(stages)
        return cls(stages, {n: _gates(game, n) for n in stages}, game)

    @classmethod
    def live(cls, s: Session, game: Extracted) -> Map:
        """The map row the session plays."""
        row = map_manager(s.mem).row
        return cls.read(game, read_map_table(game)[row])

    def path(self, start: int, goal: int, banned: Iterable[Gate] = ()) -> list[Gate]:
        """The exits from `start` to `goal`, fewest first, none of `banned`; [] when start is
        goal."""
        skip = set(banned)
        came: dict[int, Gate | None] = {start: None}
        todo = deque([start])
        while todo and goal not in came:
            n = todo.popleft()
            for gate in self.gates.get(n, []):
                if gate.target not in came and gate not in skip:
                    came[gate.target] = gate
                    todo.append(gate.target)
        if goal not in came:
            raise NoPath(f"no exits lead from st{start:03d} to st{goal:03d}")
        out: list[Gate] = []
        while (step := came[goal]) is not None:
            out.append(step)
            goal = step.stage
        return out[::-1]

    def grid(self, stage: int, climbs: Sequence[Point] = ()) -> NavGrid:
        """The stage's walk planner from its file: floor, walls, its climbable walls and the
        climb points set on it (or on its twin); read once."""
        key = (stage, tuple(climbs))
        if key not in self._grids:
            if self.game is None:
                raise NoPath("planning across stages needs the extracted game")
            floor = Floor.from_file(self.game, stage)
            try:
                walls = list(Floor.from_file(self.game, stage, WALL_CHUNK).triangles())
            except NotLoaded:
                walls = []
            self._grids[key] = terrain(floor, walls, [c for c in climbs if self._on(c, stage)])
        return self._grids[key]

    def _on(self, point: Point, stage: int) -> bool:
        try:
            return self.local(point.stage) == stage
        except LookupError:
            return False

    def route(
        self,
        stage: int,
        at: Vec3,
        goal: Point,
        climbs: Sequence[Point] = (),
        banned: Iterable[Gate] = (),
    ) -> list[Gate]:
        """The exits to take from `at` on `stage` to `goal`, the walk and climbs shortest:
        Dijkstra over (stage, where the hunter enters it), each stage flooded from its file.
        [] when the goal is on `stage` and walkable; NoPath when no way leads there."""
        target = self.local(goal.stage)
        gx, gy, gz = goal.at

        def done(x: float, y: float, z: float) -> bool:
            return math.hypot(x - gx, z - gz) <= 2 * STEP and abs(y - gy) < LEVEL

        skip = set(banned)
        tie = itertools.count()
        todo: list[tuple[float, int, int | None, Vec3, tuple[Gate, ...]]] = [
            (0.0, next(tie), stage, at, ())
        ]
        seen: set[tuple[int, int, int]] = set()
        while todo:
            cost, _, st, pos, path = heapq.heappop(todo)
            if st is None:
                return list(path)
            key = (st, int(pos[0] // STEP), int(pos[2] // STEP))
            if key in seen:
                continue
            seen.add(key)
            gates = [g for g in self.gates.get(st, []) if g not in skip and g.target in self.stages]
            reach, here = self.grid(st, climbs).flood(pos, gates, done if st == target else None)
            if here is not None:
                heapq.heappush(todo, (cost + here, next(tie), None, pos, path))
            for g, walked in reach.items():
                heapq.heappush(
                    todo, (cost + walked + CHANGE_COST, next(tie), g.target, g.dest, (*path, g))
                )
        raise NoPath(f"no walk or climb leads from st{stage:03d} to {goal.name} (st{target:03d})")

    def local(self, stage: int) -> int:
        """`stage` on this map: itself, or the stage of the same frame (`points.frame`: the
        other time of day)."""
        if stage in self.stages:
            return stage
        if self.game is not None:
            mine = points.frame(self.game, stage)
            for n in self.stages:
                if mine and points.frame(self.game, n) == mine:
                    return n
        raise LookupError(f"st{stage:03d} is not on this map ({_names(self.stages)})")


def _gates(game: Extracted, stage: int) -> list[Gate]:
    return [Gate.of(stage, e) for e in StageOverlay.read(game, stage).exits()]


def _names(stages: Iterable[int]) -> str:
    return " ".join(f"st{n:03d}" for n in stages)


# --- an area change on request ---


def change_area(
    s: Session,
    gate: Gate,
    *,
    target: int | None = None,
    dest: Vec3 | None = None,
    yaw: int | None = None,
    timeout: float = CHANGE_TIMEOUT,
) -> int:
    """Leave the stage through `gate`, to its target and landing or to those given; returns
    AREA_INDEX in the new stage. The game stops once, at AREA_CHANGE_POLL."""
    start = s.game.area_index
    record = Exit(s.mem, gate.address)
    original = s.mem.read(gate.address, a.STAGE_EXIT.size)
    client = s.client
    try:
        with client.breakpoint(a.AREA_CHANGE_POLL) as hits:
            hits.next(timeout)
            if target is not None:
                record.target = target
            if dest is not None:
                record.dest = dest
            if yaw is not None:
                record.yaw = yaw
            changer = AreaChange(s.mem, a.MONSTER_MANAGER_SINGLETON)
            changer.exit = gate.address
            changer.requests = changer.requests | 1
            Hunter(s.mem, a.PLAYER_ENTITY).exiting = 1
    finally:
        if client.status().stepping:
            client.resume()
    try:
        return area.wait_for_transition(s, start, timeout)
    except TimeoutError:
        if map_manager(s.mem).stage == gate.stage:
            s.mem.write(gate.address, original)
        raise


def goto(s: Session, point: Point, plan: Map, log: Log | None = None) -> Vec3:
    """Put the hunter on the floor at `point`, changing area first if it is on another stage.

    One area change, through any exit of this stage, with the record's target and landing
    rewritten to the point; the floor under it is then found by a teleport.
    """
    say = log or (lambda _: None)
    here = map_manager(s.mem).stage
    stage = plan.local(point.stage)
    x, y, z = point.at
    if stage != here:
        gates = plan.gates.get(here)
        if not gates:
            raise NoPath(f"st{here:03d} has no exit to leave by")
        gate = next((g for g in gates if g.target == stage), gates[0])
        landed = change_area(s, gate, target=stage, dest=point.at)
        say(f"st{here:03d} -> st{landed:03d}")
        if landed != stage:
            raise RuntimeError(f"asked for st{stage:03d}, landed in st{landed:03d}")
        s.wait(lambda: s.game.player.loaded, 10.0, "the hunter after the load")
    return rig.teleport(s, x, z, near=y)


# --- walks planned over the floor ---

Node = tuple[int, int, int]


@dataclass(frozen=True)
class Climb:
    """A ledge or a climbable wall the planner may take: walk to `foot`, climb facing
    `heading` (degrees), and stand on the floor at `top`."""

    foot: Vec3
    heading: float
    top: Vec3
    kind: str = "point"
    """"wall" (climbable material), "ledge" (a short unmarked step) or "point" (hand-set)."""

    @property
    def height(self) -> float:
        return self.top[1] - self.foot[1]


@dataclass(frozen=True)
class Drop:
    """A cliff edge the walk steps off, from `edge` down to `foot`: one way."""

    edge: Vec3
    foot: Vec3


Step = Vec3 | Climb | Drop
"""A planned walk: floor points to walk to in turn, the climbs and the drops between them."""


@dataclass(frozen=True)
class Face:
    """One flat climbable wall: its outward normal in x and z (the side the hunter climbs
    from), its middle at the wall, its width along the wall and its height span."""

    normal: XZ
    middle: XZ
    width: float
    span: tuple[float, float]


def faces(walls: Iterable[Triangle]) -> list[Face]:
    """The climbable walls among `walls`: material 9 or 10 on near-vertical triangles, those of
    one plane and touching joined into one face."""
    return _faces([t for t in walls if t.material in CLIMB_MATERIALS and _vertical(t)])


def ledges(floor: Floor, walls: Iterable[Triangle]) -> list[Climb]:
    """The steps a hunter climbs onto (`navigation.climb`): an unmarked near-vertical face
    between floor in front at its foot and floor behind at its top, a LEDGE above it. A climb
    every LEDGE_EVERY units along a face, LEDGE_EVERY / 2 in from its ends, square on to it;
    faces narrower than LEDGE_WIDTH (corners) none."""
    hi_rise = LEDGE[1]
    short = [
        t
        for t in walls
        if _vertical(t)
        and t.material not in CLIMB_MATERIALS
        and max(t.v0[1], t.v1[1], t.v2[1]) - min(t.v0[1], t.v1[1], t.v2[1]) <= hi_rise + LEVEL
    ]
    out: dict[tuple[int, int, int, int], Climb] = {}
    for f in _faces(short):
        if f.width < LEDGE_WIDTH:
            continue
        (nx, nz), (mx, mz), (bottom, top) = f.normal, f.middle, f.span
        along = (nz, -nx)
        k = max(1, int(f.width // LEDGE_EVERY))
        for i in range(k):
            off = (i + 0.5) / k * f.width - f.width / 2
            px, pz = mx + off * along[0], mz + off * along[1]
            for sx, sz in ((nx, nz), (-nx, -nz)):
                c = _ledge(floor, (px, pz), (sx, sz), bottom, top)
                if c is not None:
                    fx, _, fz = c.foot
                    tx, _, tz = c.top
                    key = (int(fx // STEP), int(fz // STEP), int(tx // STEP), int(tz // STEP))
                    out.setdefault(key, c)
    return list(out.values())


def _ledge(floor: Floor, at: XZ, out: XZ, bottom: float, top: float) -> Climb | None:
    """The climb up a face at `at` from the side `out` points to, if that side is a ledge."""
    (px, pz), (sx, sz) = at, out
    fx, fz, tx, tz = px + FOOT * sx, pz + FOOT * sz, px - TOP * sx, pz - TOP * sz
    feet = [y for y in floor.heights(fx, fz) if abs(y - bottom) < LEDGE_SLACK]
    tops = [y for y in floor.heights(tx, tz) if abs(y - top) < LEDGE_SLACK]
    if not feet or not tops or not LEDGE[0] < max(tops) - min(feet) <= LEDGE[1]:
        return None
    heading = math.degrees(math.atan2(-sx, -sz))
    return Climb((fx, min(feet), fz), heading, (tx, max(tops), tz), "ledge")


def _vertical(t: Triangle) -> bool:
    return abs(t.normal[1]) < VERTICAL


def _faces(tris: Sequence[Triangle]) -> list[Face]:
    """`tris` joined into flat faces: coplanar and touching (`_same_face`), transitively."""
    parent = list(range(len(tris)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    buckets: dict[tuple[int, int], list[int]] = {}
    for i, t in enumerate(tris):
        xs = [v[0] for v in (t.v0, t.v1, t.v2)]
        zs = [v[2] for v in (t.v0, t.v1, t.v2)]
        for bx in range(int(min(xs) // BUCKET), int(max(xs) // BUCKET) + 1):
            for bz in range(int(min(zs) // BUCKET), int(max(zs) // BUCKET) + 1):
                buckets.setdefault((bx, bz), []).append(i)
    for members in buckets.values():
        for k, i in enumerate(members):
            for j in members[k + 1 :]:
                if root(i) != root(j) and _same_face(tris[i], tris[j]):
                    parent[root(i)] = root(j)
    groups: dict[int, list[Triangle]] = {}
    for i, t in enumerate(tris):
        groups.setdefault(root(i), []).append(t)
    return [_face(g) for g in groups.values()]


def _face(g: Sequence[Triangle]) -> Face:
    nx = sum(t.normal[0] for t in g)
    nz = sum(t.normal[2] for t in g)
    n = math.hypot(nx, nz)
    normal = (nx / n, nz / n)
    along = (normal[1], -normal[0])
    verts = [v for t in g for v in (t.v0, t.v1, t.v2)]
    ts = [v[0] * along[0] + v[2] * along[1] for v in verts]
    cx, cz = (sum(v[0] for v in verts) / len(verts), sum(v[2] for v in verts) / len(verts))
    mid = (min(ts) + max(ts)) / 2 - (cx * along[0] + cz * along[1])
    middle = (cx + mid * along[0], cz + mid * along[1])
    span = (min(v[1] for v in verts), max(v[1] for v in verts))
    return Face(normal, middle, max(ts) - min(ts), span)


def _same_face(t: Triangle, u: Triangle) -> bool:
    """Coplanar (normals within ~10 degrees, the planes within 30 units) and touching."""
    dot = t.normal[0] * u.normal[0] + t.normal[2] * u.normal[2]
    if dot < 0.985:
        return False
    if abs(sum(t.normal[k] * u.v0[k] for k in range(3)) + t.d) > 30:
        return False
    return any(math.dist(a, b) < 60 for a in (t.v0, t.v1, t.v2) for b in (u.v0, u.v1, u.v2))


def wall_climbs(floor: Floor, walls: Iterable[Triangle]) -> list[Climb]:
    """A climb per climbable face whose foot and top have floor: the foot FOOT in front at the
    lowest level the wall rises above, the top TOP behind at the highest level it reaches."""
    out = []
    for f in faces(walls):
        (nx, nz), (mx, mz), (lo, hi) = f.normal, f.middle, f.span
        fx, fz = mx + FOOT * nx, mz + FOOT * nz
        feet = [y for y in floor.heights(fx, fz) if lo - LEVEL * 2 <= y <= hi - LEVEL]
        if not feet:
            continue
        foot = min(feet)
        tx, tz = mx - TOP * nx, mz - TOP * nz
        tops = [y for y in floor.heights(tx, tz) if foot + LEVEL < y <= hi + LEVEL * 2]
        if not tops:
            continue
        heading = math.degrees(math.atan2(-nx, -nz))
        out.append(Climb((fx, foot, fz), heading, (tx, max(tops), tz), "wall"))
    return out


class NavGrid:
    """The walkable floor of one stage as samples every `step` units, levels kept apart.

    Two neighbouring samples connect when the higher is at most RISE per unit walked above the
    lower one, or lower by anything (a drop); a climb joins the foot of a ledge to its top.
    Samples near a wall, under a roof, in `avoid` (other exits' triggers) or in `blocked` (where
    a walk got stuck) are never entered.
    """

    def __init__(
        self,
        floor: Floor,
        walls: Iterable[Triangle] = (),
        step: float = STEP,
        rise: float = RISE,
        avoid: Iterable[Gate] = (),
    ) -> None:
        self.floor, self.step, self.rise = floor, step, rise
        self.avoid = list(avoid)
        cx, cz = floor.cell
        nx, nz = floor.grid
        self.size = (int(nx * cx // step), int(nz * cz // step))
        self.blocked: set[tuple[int, int]] = set()
        self.walls: dict[tuple[int, int], list[tuple[float, float]]] = {}
        self.lines: dict[tuple[int, int], list[tuple[XZ, XZ, tuple[float, float]]]] = {}
        for t in walls:
            if abs(t.normal[1]) < STEEP:
                self._wall(t)
        self.climbs: dict[Node, list[tuple[Node, Climb]]] = {}
        self._raw: dict[tuple[int, int], list[float]] = {}
        self._levels: dict[tuple[int, int], list[float]] = {}

    def _wall(self, t: Triangle) -> None:
        corners = [(v[0], v[2]) for v in (t.v0, t.v1, t.v2)]
        ys = (t.v0[1], t.v1[1], t.v2[1])
        span = (min(ys), max(ys))
        lo = [int((min(c[k] for c in corners) - CLEARANCE) // self.step) for k in (0, 1)]
        hi = [int((max(c[k] for c in corners) + CLEARANCE) // self.step) for k in (0, 1)]
        for ix in range(lo[0], hi[0] + 1):
            for iz in range(lo[1], hi[1] + 1):
                if _triangle_distance(self.xz(ix, iz), corners) <= CLEARANCE:
                    self.walls.setdefault((ix, iz), []).append(span)
        edges = [(corners[k], corners[(k + 1) % 3]) for k in range(3)]
        a, b = max(edges, key=lambda e: math.dist(*e))
        for ix in range(lo[0], hi[0] + 1):
            for iz in range(lo[1], hi[1] + 1):
                self.lines.setdefault((ix, iz), []).append((a, b, span))

    def crosses(self, p: XZ, q: XZ, y: float) -> bool:
        """The straight walk p -> q at floor height `y` runs into a wall."""
        cells = {
            (
                int((p[0] + (q[0] - p[0]) * k / 8) // self.step),
                int((p[1] + (q[1] - p[1]) * k / 8) // self.step),
            )
            for k in range(9)
        }
        return any(
            lo < y + BODY[1] and hi > y + BODY[0] and _segments_cross(p, q, a, b)
            for c in cells
            for a, b, (lo, hi) in self.lines.get(c, [])
        )

    def xz(self, ix: int, iz: int) -> XZ:
        return (ix + 0.5) * self.step, (iz + 0.5) * self.step

    def raw(self, ix: int, iz: int) -> list[float]:
        """Floor heights of a sample, one per level."""
        key = (ix, iz)
        if key not in self._raw:
            x, z = self.xz(ix, iz)
            out: list[float] = []
            for y in sorted(self.floor.heights(x, z)):
                if not out or y - out[-1] > LEVEL:
                    out.append(y)
                else:
                    out[-1] = y
            self._raw[key] = out
        return self._raw[key]

    def walled(self, cell: tuple[int, int], y: float) -> bool:
        return any(lo < y + BODY[1] and hi > y + BODY[0] for lo, hi in self.walls.get(cell, []))

    def levels(self, ix: int, iz: int) -> list[float]:
        """The levels of a sample a hunter may stand on: none with floor above it within
        HEADROOM (a tent, a rock), none in an avoided trigger. One next to a wall (`walled`)
        is walked to only by dropping onto it."""
        key = (ix, iz)
        if key not in self._levels:
            x, z = self.xz(ix, iz)
            raw = self.raw(ix, iz)
            self._levels[key] = [
                y
                for y in raw
                if not any(y + LEVEL < h < y + HEADROOM for h in raw)
                and not any(g.holds(x, y, z) for g in self.avoid)
            ]
        return self._levels[key]

    def open(self, cell: tuple[int, int]) -> list[tuple[int, float]]:
        """(index, height) of the cell's levels a walk may enter: not blocked, off the walls."""
        if cell in self.blocked:
            return []
        return [(k, y) for k, y in enumerate(self.levels(*cell)) if not self.walled(cell, y)]

    def node(self, x: float, y: float, z: float, reach: int = 1) -> Node | None:
        """The walkable sample nearest (x, z) within `reach` samples, at the level nearest `y`."""
        ix, iz = int(x // self.step), int(z // self.step)
        found = []
        for dx in range(-reach, reach + 1):
            for dz in range(-reach, reach + 1):
                ys = self.open((ix + dx, iz + dz))
                if ys:
                    k, h = min(ys, key=lambda kh: abs(kh[1] - y))
                    sx, sz = self.xz(ix + dx, iz + dz)
                    found.append((math.hypot(sx - x, sz - z) + abs(h - y), (ix + dx, iz + dz, k)))
        return min(found)[1] if found else None

    def height(self, n: Node) -> float:
        return self.levels(n[0], n[1])[n[2]]

    def point(self, n: Node) -> Vec3:
        x, z = self.xz(n[0], n[1])
        return x, self.height(n), z

    def add_edge(self, climb: Climb) -> bool:
        """Join the samples at the climb's foot and top; False where either has none that
        reaches it without a wall between."""
        start, top = self._near(climb.foot), self._near(climb.top)
        if start is None or top is None:
            return False
        self.climbs.setdefault(start, []).append((top, climb))
        return True

    def _near(self, at: Vec3, reach: int = 2) -> Node | None:
        """The walkable sample nearest `at`, on its level, with no wall between them."""
        x, y, z = at
        ix, iz = int(x // self.step), int(z // self.step)
        found = []
        for dx in range(-reach, reach + 1):
            for dz in range(-reach, reach + 1):
                cell = (ix + dx, iz + dz)
                ys = self.open(cell)
                if not ys:
                    continue
                k, h = min(ys, key=lambda kh: abs(kh[1] - y))
                sx, sz = self.xz(*cell)
                if abs(h - y) < LEVEL and not self.crosses((sx, sz), (x, z), h):
                    found.append((math.hypot(sx - x, sz - z), (*cell, k)))
        return min(found)[1] if found else None

    def drop(self, climb: Climb) -> None:
        """Forget a climb that did not lift the hunter."""
        for k, edges in self.climbs.items():
            self.climbs[k] = [e for e in edges if e[1] != climb]

    def add_climb(self, foot: Vec3, heading: float, reach: float = 600.0) -> Climb | None:
        """Join the foot of a ledge to the first floor higher up along `heading` degrees;
        None where either end has no walkable sample."""
        if self.node(*foot, reach=2) is None:
            return None
        h, fy = math.radians(heading), foot[1]
        r = self.step / 2
        while r <= reach:
            x, z = foot[0] + r * math.sin(h), foot[2] + r * math.cos(h)
            ix, iz = int(x // self.step), int(z // self.step)
            ys = [y for y in self.levels(ix, iz) if y - fy > LEVEL / 2]
            if ys:
                tx, tz = self.xz(ix, iz)
                climb = Climb(foot, heading, (tx, min(ys), tz))
                return climb if self.add_edge(climb) else None
            r += self.step / 2
        return None

    def neighbours(self, n: Node) -> Iterable[tuple[Node, float]]:
        ix, iz, k = n
        y = self.height(n)
        for top, climb in self.climbs.get(n, []):
            yield top, CLIMB_COST + climb.height
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if not dx and not dz:
                    continue
                jx, jz = ix + dx, iz + dz
                if not (0 <= jx < self.size[0] and 0 <= jz < self.size[1]):
                    continue
                if (jx, jz) in self.blocked:
                    continue
                ys = self.levels(jx, jz)
                if not ys:
                    continue
                run = self.step * math.hypot(dx, dz)
                j = min(range(len(ys)), key=lambda i: abs(ys[i] - y))
                rise = ys[j] - y
                if rise > self.rise * run:
                    continue
                if rise >= -LEDGE[0] and self.walled((jx, jz), ys[j]):
                    continue  # a walk keeps off walls; a drop may land beside one
                yield (jx, jz, j), run

    def flood(
        self,
        start: Vec3,
        gates: Sequence[Gate],
        goal: Callable[[float, float, float], bool] | None = None,
    ) -> tuple[dict[Gate, float], float | None]:
        """The walked cost from `start` into each of `gates` (a trigger ends a walk into it)
        and to the first sample `goal` accepts; a gate or goal out of reach is left out."""
        first = self.node(*start, reach=2)
        if first is None:
            return {}, None
        cost = {first: 0.0}
        todo = [(0.0, first)]
        reached: dict[Gate, float] = {}
        found: float | None = None
        expanded = 0
        while todo and expanded < SEARCH:
            c, n = heapq.heappop(todo)
            if c > cost[n]:
                continue
            expanded += 1
            x, y, z = self.point(n)
            inside = [g for g in gates if g.holds(x, y, z)]
            for g in inside:
                reached.setdefault(g, c)
            if found is None and goal is not None and goal(x, y, z):
                found = c
            if len(reached) == len(gates) and (goal is None or found is not None):
                break
            if inside and n != first:
                continue
            for m, run in self.neighbours(n):
                if c + run < cost.get(m, math.inf):
                    cost[m] = c + run
                    heapq.heappush(todo, (c + run, m))
        return reached, found

    def search(
        self, start: Vec3, done: Callable[[float, float, float], bool], toward: XZ
    ) -> list[Node]:
        """A* from `start` to the first sample `done` accepts."""
        first = self.node(*start, reach=2)
        if first is None:
            raise NoPath(f"no floor under ({start[0]:.0f}, {start[2]:.0f})")
        tx, tz = toward

        def guess(n: Node) -> float:
            x, z = self.xz(n[0], n[1])
            return math.hypot(tx - x, tz - z)

        came: dict[Node, Node | None] = {first: None}
        cost = {first: 0.0}
        todo = [(guess(first), 0.0, first)]
        while todo and len(came) < SEARCH:
            _, g, n = heapq.heappop(todo)
            if g > cost[n]:
                continue
            if done(*self.point(n)):
                out = []
                at: Node | None = n
                while at is not None:
                    out.append(at)
                    at = came[at]
                return out[::-1]
            for m, run in self.neighbours(n):
                c = g + run
                if c < cost.get(m, math.inf):
                    cost[m], came[m] = c, n
                    heapq.heappush(todo, (c + guess(m), c, m))
        where = f"({start[0]:.0f}, {start[2]:.0f})"
        raise NoPath(f"no walk on the floor from {where} to ({tx:.0f}, {tz:.0f})")

    def clear(self, p: Vec3, q: Vec3) -> bool:
        """A straight walk from p to q crosses only samples the search could step between."""
        n = max(1, int(math.dist((p[0], p[2]), (q[0], q[2])) // (self.step / 2)))
        cell, y = (int(p[0] // self.step), int(p[2] // self.step)), p[1]
        for k in range(1, n + 1):
            x, z = p[0] + (q[0] - p[0]) * k / n, p[2] + (q[2] - p[2]) * k / n
            nxt = (int(x // self.step), int(z // self.step))
            if nxt == cell:
                continue
            ys = self.levels(*nxt)
            if nxt in self.blocked or not ys:
                return False
            h = min(ys, key=lambda v: abs(v - y))
            if h - y > self.rise * self.step * math.dist(cell, nxt):
                return False
            if h - y >= -LEDGE[0] and self.walled(nxt, h):
                return False
            cell, y = nxt, h
        return True

    def legs(self, path: Sequence[Vec3], longest: float = LEG) -> list[Vec3]:
        """`path` cut to the fewest straight legs `clear` allows, none longer than `longest`."""
        if len(path) < 2:
            return list(path)
        out, i = [path[0]], 0
        while i < len(path) - 1:
            j = len(path) - 1
            while j > i + 1 and (
                math.dist(path[i][::2], path[j][::2]) > longest or not self.clear(path[i], path[j])
            ):
                j -= 1
            out.append(path[j])
            i = j
        return out

    def steps(self, nodes: Sequence[Node], start: Vec3, end: Vec3 | None) -> list[Step]:
        """A searched path as steps: straight legs from `start`, a climb at each climb edge, a
        drop where it steps down more than a ledge, the last leg to `end` (the last sample's
        floor without one)."""
        runs: list[list[Vec3]] = [[start]]
        out: list[Step] = []
        for a_, b_ in zip(nodes, nodes[1:], strict=False):
            edge = next((c for top, c in self.climbs.get(a_, []) if top == b_), None)
            if edge is not None:
                runs[-1].append(edge.foot)
                out += self.legs(runs[-1])[1:]
                out.append(edge)
                runs.append([self.point(b_)])
            elif self.height(a_) - self.height(b_) > LEDGE[0]:
                runs[-1].append(self.point(a_))
                out += self.legs(runs[-1])[1:]
                out.append(Drop(self.point(a_), self.point(b_)))
                runs.append([self.point(b_)])
            else:
                runs[-1].append(self.point(b_))
        if end is not None:
            runs[-1][-1:] = [end] if len(runs[-1]) > 1 else [runs[-1][0], end]
        return out + self.legs(runs[-1])[1:]

    def block(self, x: float, z: float, heading: float, reach: float = 150.0) -> None:
        """Mark the samples just ahead of a stuck walk, `heading` radians from (x, z), and their
        neighbours: the next plan must go round, not through the cell beside."""
        bx, bz = x + reach * math.sin(heading), z + reach * math.cos(heading)
        ix, iz = int(bx // self.step), int(bz // self.step)
        here = (int(x // self.step), int(z // self.step))
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if (ix + dx, iz + dz) != here:
                    self.blocked.add((ix + dx, iz + dz))


def collision(s: Session) -> tuple[Floor, list[Triangle]]:
    """The floor and the walls of the stage on screen, read in one stop of the CPU."""
    with s.client.paused():
        stage = map_manager(s.mem).stage
        floor = Floor.read(s.mem, stage)
        try:
            walls = list(Floor.read(s.mem, stage, chunk=WALL_CHUNK).triangles())
        except NotLoaded:
            walls = []
    return floor, walls


def terrain(
    floor: Floor,
    walls: Sequence[Triangle],
    climbs: Iterable[Point] = (),
    avoid: Iterable[Gate] = (),
) -> NavGrid:
    """A stage's walk planner: its floor and walls, its climbable walls and ledges as climbs,
    and the hand-set climb points, which replace a found climb within OVERRIDE of them."""
    grid = NavGrid(floor, walls, avoid=avoid)
    hand = [p.at for p in climbs if p.heading is not None]
    for c in (*wall_climbs(floor, walls), *ledges(floor, walls)):
        if all(math.dist(c.foot, at) > OVERRIDE for at in hand):
            grid.add_edge(c)
    for p in climbs:
        if p.heading is not None:
            grid.add_climb(p.at, p.heading)
    return grid


def plan_walk(grid: NavGrid, start: Vec3, goal: Point | Gate) -> list[Step]:
    """The steps from `start` to `goal`: a point (to within two samples, then onto it) or into
    an exit's trigger."""
    if isinstance(goal, Gate):
        target = goal.spot(grid.floor, grid.step)
        if target is None:
            raise NoPath(f"no floor inside the exit to st{goal.target:03d}")
        nodes = grid.search(start, goal.holds, (target[0], target[2]))
        return grid.steps(nodes, start, None)
    gx, gy, gz = goal.at

    def done(x: float, y: float, z: float) -> bool:
        return math.hypot(x - gx, z - gz) <= 2 * grid.step and abs(y - gy) < LEVEL

    return grid.steps(grid.search(start, done, (gx, gz)), start, goal.at)


def climb(s: Session, step: Climb, tries: Sequence[tuple[float, float]] = CLIMB_TRIES) -> bool:
    """Climb from the foot, held as long as the height takes; each try starts from the foot
    (shifted along the wall) at a heading turned as `tries` says. At the game's own rate: a
    fast-forwarded climb missed its grab about half the time."""
    hold = 5.0 + max(step.height, 0.0) / CLIMB_SPEED
    h = math.radians(step.heading)
    with boot.own_speed(s):
        for turn, shift in tries:
            fx = step.foot[0] + shift * math.cos(h)
            fz = step.foot[2] - shift * math.sin(h)
            if nav.distance(nav.where(s), (fx, fz)) > CLIMB_AT:
                nav.walk_to(s, fx, fz, tolerance=CLIMB_AT, timeout=10.0, slow_radius=400.0)
            before, after = nav.climb(s, step.heading + turn, hold=hold)
            if after.y - before.y >= CLIMB_LIFT:
                return True
    return False


def _walk_stage(
    s: Session,
    goal: Point | Gate,
    plan: Map,
    climbs: Sequence[Point],
    *,
    tolerance: float,
    timeout: float,
    say: Log,
) -> nav.Walk:
    """Plan and walk to `goal` on the stage on screen, re-planning around where it got stuck.

    For an exit the walk ends as the area starts to change.
    """
    stage = map_manager(s.mem).stage
    start_area = s.game.area_index
    leaves = isinstance(goal, Gate)
    floor, walls = collision(s)
    others = [g for g in plan.gates.get(stage, []) if g != goal]
    grid = terrain(floor, walls, [c for c in climbs if plan._on(c, stage)], others)

    def left() -> bool:
        return not area.in_area(s) or s.game.area_index != start_area

    until = left if leaves else None
    result = nav.Walk(False, nav.where(s), math.inf, 0.0, "unplanned")
    for attempt in range(REPLANS + 1):
        p = nav.pose(s)
        steps = plan_walk(grid, (p.x, p.y, p.z), goal)
        say(f"  st{stage:03d}:{' re-planned' if attempt else ''} " + _describe(steps))
        for run in _runs(steps):
            if isinstance(run, Climb):
                if not climb(s, run):
                    say(f"  no lift climbing at {_xz(run.foot)}")
                    grid.drop(run)
                    break
                continue
            result = nav.walk_path(
                s,
                run,
                tolerance=tolerance,
                timeout=timeout,
                until=until,
                patience=10,
                max_detours=2,
            )
            if not result.reached or left():
                break
        else:
            if isinstance(goal, Gate) and not left():
                push_into(s, goal, until=left)
            if left() or not leaves:
                return result
        if left():
            return result
        x, z = result.at
        say(f"  {result.reason} at ({x:.0f}, {z:.0f}), {result.remaining:.0f} short")
        grid.block(x, z, nav.pose(s).yaw)
    return result


def _runs(steps: Sequence[Step]) -> list[list[XZ] | Climb]:
    """Steps grouped as walk_path takes them: runs of points (a drop is walked off, to its
    foot), climbs between."""
    out: list[list[XZ] | Climb] = []
    for step in steps:
        if isinstance(step, Climb):
            out.append(step)
            continue
        at = step.foot if isinstance(step, Drop) else step
        if out and isinstance(out[-1], list):
            out[-1].append((at[0], at[2]))
        else:
            out.append([(at[0], at[2])])
    return out


def _xz(p: Vec3) -> str:
    return f"({p[0]:.0f},{p[2]:.0f})"


def _describe(steps: Sequence[Step]) -> str:
    def one(st: Step) -> str:
        if isinstance(st, Climb):
            return f"{st.kind} {st.heading:.0f} up {st.height:.0f}"
        if isinstance(st, Drop):
            return f"drop {st.edge[1] - st.foot[1]:.0f}"
        return _xz(st)

    return " ".join(map(one, steps))


def push_into(s: Session, gate: Gate, until: Callable[[], object], seconds: float = 6.0) -> None:
    """Walk at the trigger's floor spot until `until`: a walk counts as arrived short of it."""
    spot = gate.spot(collision(s)[0])
    if spot is not None:
        nav.walk_to(s, spot[0], spot[2], tolerance=20.0, timeout=seconds, until=until)


def walk(
    s: Session,
    point: Point,
    plan: Map,
    climbs: Sequence[Point] = (),
    *,
    tolerance: float = 80.0,
    timeout: float = 60.0,
    log: Log | None = None,
) -> nav.Walk:
    """Walk to `point`: exit to exit while it is on another stage, then to the point.

    The stages come from `Map.route` (each stage's file: floor, walls, climbable walls),
    re-planned at every landing; `climbs` are the hand-set ledges the planner may take too
    (points of kind "climb"). An exit the live walk cannot reach is dropped and another way
    tried. A climb point as the goal is climbed on arrival.
    """
    say = log or (lambda _: None)
    goal = plan.local(point.stage)
    banned: set[Gate] = set()
    while (here := map_manager(s.mem).stage) != goal:
        if plan.game is None:
            gate = plan.path(here, goal, banned)[0]
        else:
            p = nav.pose(s)
            route = plan.route(here, (p.x, p.y, p.z), point, climbs, banned)
            say("route: " + " ".join(f"st{g.stage:03d}>st{g.target:03d}" for g in route))
            gate = route[0]
        start = s.game.area_index
        say(f"st{here:03d} -> st{gate.target:03d}")
        try:
            left = _walk_stage(s, gate, plan, climbs, tolerance=tolerance, timeout=timeout, say=say)
        except NoPath as e:
            say(f"  {e}: another way")
            banned.add(gate)
            continue
        if area.in_area(s) and s.game.area_index == start:
            say(f"  walked to the exit and stayed ({left.reason}): another way")
            banned.add(gate)
            continue
        area.wait_for_transition(s, start)
        s.wait(lambda: s.game.player.loaded, 10.0, "the hunter after the load")
    result = _walk_stage(s, point, plan, climbs, tolerance=tolerance, timeout=timeout, say=say)
    if result.reached and point.kind == "climb" and point.heading is not None:
        p = nav.pose(s)
        lifted = climb(s, Climb(point.at, point.heading, point.at))
        say(f"  climbed {point.heading:.0f} deg from y {p.y:.0f}: {'up' if lifted else 'no lift'}")
    return result
