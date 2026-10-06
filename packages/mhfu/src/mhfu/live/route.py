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
import math
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

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
from . import area, rig
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
"""What a climb counts as in walked units."""
CLIMB_FAN = (0.0, -12.0, 12.0, -25.0, 25.0)
"""Headings tried round a climb's own: whether circle grabs is sensitive to the angle."""
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
    """A ledge the planner may take: walk to `foot`, climb facing `heading` (degrees), and
    stand on the floor at `top`."""

    foot: Vec3
    heading: float
    top: Vec3


Step = Vec3 | Climb
"""A planned walk: floor points to walk to in turn, and the climbs between them."""


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
        for t in walls:
            if abs(t.normal[1]) < STEEP:
                self._wall(t)
        self.climbs: dict[Node, tuple[Node, Climb]] = {}
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
        """The walkable levels of a sample: none with floor above it within HEADROOM (a tent, a
        rock), none at a wall, none in an avoided trigger."""
        key = (ix, iz)
        if key not in self._levels:
            x, z = self.xz(ix, iz)
            raw = self.raw(ix, iz)
            self._levels[key] = [
                y
                for y in raw
                if not any(y + LEVEL < h < y + HEADROOM for h in raw)
                and not self.walled(key, y)
                and not any(g.holds(x, y, z) for g in self.avoid)
            ]
        return self._levels[key]

    def node(self, x: float, y: float, z: float, reach: int = 1) -> Node | None:
        """The walkable sample nearest (x, z) within `reach` samples, at the level nearest `y`."""
        ix, iz = int(x // self.step), int(z // self.step)
        found = []
        for dx in range(-reach, reach + 1):
            for dz in range(-reach, reach + 1):
                ys = self.levels(ix + dx, iz + dz)
                if ys and (ix + dx, iz + dz) not in self.blocked:
                    k = min(range(len(ys)), key=lambda i: abs(ys[i] - y))
                    sx, sz = self.xz(ix + dx, iz + dz)
                    found.append(
                        (math.hypot(sx - x, sz - z) + abs(ys[k] - y), (ix + dx, iz + dz, k))
                    )
        return min(found)[1] if found else None

    def height(self, n: Node) -> float:
        return self.levels(n[0], n[1])[n[2]]

    def point(self, n: Node) -> Vec3:
        x, z = self.xz(n[0], n[1])
        return x, self.height(n), z

    def add_climb(self, foot: Vec3, heading: float, reach: float = 600.0) -> Climb | None:
        """Join the foot of a ledge to the first floor higher up along `heading` degrees;
        None where either end has no walkable sample."""
        start = self.node(*foot, reach=2)
        if start is None:
            return None
        h, fy = math.radians(heading), foot[1]
        r = self.step / 2
        while r <= reach:
            x, z = foot[0] + r * math.sin(h), foot[2] + r * math.cos(h)
            ix, iz = int(x // self.step), int(z // self.step)
            ys = [y for y in self.levels(ix, iz) if y - fy > LEVEL / 2]
            if ys:
                top = (ix, iz, self.levels(ix, iz).index(min(ys)))
                climb = Climb(foot, heading, self.point(top))
                self.climbs[start] = (top, climb)
                return climb
            r += self.step / 2
        return None

    def neighbours(self, n: Node) -> Iterable[tuple[Node, float]]:
        ix, iz, k = n
        y = self.height(n)
        if n in self.climbs:
            yield self.climbs[n][0], CLIMB_COST
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
                if ys[j] - y <= self.rise * run:
                    yield (jx, jz, j), run

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
        """A searched path as steps: straight legs from `start`, a climb at each climb edge,
        the last leg to `end` (the last sample's floor without one)."""
        runs: list[list[Vec3]] = [[start]]
        out: list[Step] = []
        for a_, b_ in zip(nodes, nodes[1:], strict=False):
            edge = self.climbs.get(a_)
            if edge is not None and edge[0] == b_:
                runs[-1].append(edge[1].foot)
                out += self.legs(runs[-1])[1:]
                out.append(edge[1])
                runs.append([self.point(b_)])
            else:
                runs[-1].append(self.point(b_))
        if end is not None:
            runs[-1][-1:] = [end] if len(runs[-1]) > 1 else [runs[-1][0], end]
        return out + self.legs(runs[-1])[1:]

    def block(self, x: float, z: float, heading: float, reach: float = 150.0) -> None:
        """Mark the samples just ahead of a stuck walk, `heading` radians from (x, z)."""
        for r in (reach / 2, reach):
            bx, bz = x + r * math.sin(heading), z + r * math.cos(heading)
            self.blocked.add((int(bx // self.step), int(bz // self.step)))


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


def climb(s: Session, step: Climb, fan: Sequence[float] = CLIMB_FAN) -> bool:
    """Climb a ledge from its foot, turning the heading a little each failed try."""
    for turn in fan:
        before, after = nav.climb(s, step.heading + turn)
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
    grid = NavGrid(floor, walls, avoid=[g for g in plan.gates.get(stage, []) if g != goal])
    for c in climbs:
        if plan.local(c.stage) == stage and c.heading is not None:
            grid.add_climb(c.at, c.heading)

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
                    grid.climbs = {k: v for k, v in grid.climbs.items() if v[1] != run}
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
    """Steps grouped as walk_path takes them: runs of points, climbs between."""
    out: list[list[XZ] | Climb] = []
    for step in steps:
        if isinstance(step, Climb):
            out.append(step)
        elif out and isinstance(out[-1], list):
            out[-1].append((step[0], step[2]))
        else:
            out.append([(step[0], step[2])])
    return out


def _xz(p: Vec3) -> str:
    return f"({p[0]:.0f},{p[2]:.0f})"


def _describe(steps: Sequence[Step]) -> str:
    return " ".join(f"climb {st.heading:.0f}" if isinstance(st, Climb) else _xz(st) for st in steps)


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

    `climbs` are the ledges the planner may take (points of kind "climb"); an exit it cannot
    reach is dropped and another way tried. A climb point as the goal is climbed on arrival.
    """
    say = log or (lambda _: None)
    goal = plan.local(point.stage)
    banned: set[Gate] = set()
    while (here := map_manager(s.mem).stage) != goal:
        gate = plan.path(here, goal, banned)[0]
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
