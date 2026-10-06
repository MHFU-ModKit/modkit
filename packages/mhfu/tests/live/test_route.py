# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import asyncio
import math

import pytest
from mhfu import addresses as a
from mhfu import points
from mhfu.live import rig, route
from mhfu.points import Point
from mhfu.stage import Triangle
from mhfu.structs import Screen

RECORD = a.RAM.start + 0x92_0000  # a STAGE_EXIT in `fake`
MAP_MANAGER = a.RAM.start + 0x91_0000


class Ground:
    """A floor of `height(x, z)` levels over a 4000-unit square."""

    cell, grid = (500, 500), (8, 8)

    def __init__(self, height=lambda x, z: [0.0]):
        self.height = height

    def heights(self, x, z):
        return list(self.height(x, z)) if 0 <= x < 4000 and 0 <= z < 4000 else []


def gate(stage=1, target=2, trigger=(3500.0, -100.0, 2000.0), radius=300.0, end=None):
    return route.Gate(stage, target, trigger, radius, 1000.0, (10.0, 0.0, 20.0), 0, RECORD, end)


def wall(x, z0, z1, top=500.0):
    """A wall in the plane x = `x` from z0 to z1."""
    v = ((x, 0.0, z0), (x, 0.0, z1), (x, top, z0))
    return Triangle(0, 0, 0, *v, (1.0, 0.0, 0.0), -x, 0)


def walked(steps):
    return [st for st in steps if not isinstance(st, route.Climb)]


def test_gate_holds_a_cylinder_and_a_capsule():
    g = gate()
    assert g.holds(3500, 0, 2250) and not g.holds(3500, 0, 2350)
    assert not g.holds(3500, 950, 2000)  # above the cylinder
    c = gate(end=(3500.0, 0.0, 3000.0))
    assert c.holds(3700, 0, 2800) and not c.holds(3900, 0, 2800)
    assert c.centre == (3500.0, 2500.0)


def test_gate_spot_finds_floor_inside():
    g = gate()
    assert g.spot(Ground()) == (3500.0, 0.0, 2000.0)
    hole = Ground(lambda x, z: [] if math.dist((x, z), (3500, 2000)) < 150 else [0.0])
    x, _, z = g.spot(hole)
    assert 150 <= math.dist((x, z), (3500, 2000)) <= 300
    assert g.spot(Ground(lambda x, z: [2000.0])) is None  # floor above the trigger


def test_map_path_and_bans():
    a12, a13, a24, a34 = gate(1, 2), gate(1, 3), gate(2, 4), gate(3, 4)
    plan = route.Map((1, 2, 3, 4), {1: [a12, a13], 2: [a24], 3: [a34], 4: []})
    assert plan.path(1, 4) == [a12, a24]
    assert plan.path(1, 4, banned=[a24]) == [a13, a34]
    assert plan.path(1, 1) == []
    with pytest.raises(route.NoPath):
        plan.path(1, 4, banned=[a24, a34])


def test_map_local_finds_the_twin(monkeypatch):
    frames = {97: ("snow",), 106: ("snow",), 50: ("desert",)}
    monkeypatch.setattr(points, "frame", lambda game, stage: frames[stage])
    plan = route.Map((97,), {97: [gate(97, 100)]}, game=object())
    assert plan.local(97) == 97
    assert plan.local(106) == 97  # the other time of day
    with pytest.raises(LookupError):
        plan.local(50)


def test_plan_goes_round_a_wall():
    grid = route.NavGrid(Ground(), [wall(2000.0, 0.0, 3000.0)])
    steps = route.plan_walk(grid, (1000.0, 0.0, 1000.0), Point("p", 1, (3000.0, 0.0, 1000.0)))
    assert steps[-1] == (3000.0, 0.0, 1000.0)
    assert max(z for _, _, z in walked(steps)) > 3000
    path = [(1000.0, 0.0, 1000.0), *walked(steps)]
    assert all(grid.clear(p, q) for p, q in zip(path, path[1:], strict=False))


def test_plan_into_an_exit_avoids_the_others():
    other = gate(target=3, trigger=(2000.0, -100.0, 2000.0), radius=600.0)
    grid = route.NavGrid(Ground(), avoid=[other])
    steps = route.plan_walk(grid, (500.0, 0.0, 2000.0), gate())
    x, y, z = steps[-1]
    assert gate().holds(x, y, z)
    assert all(not other.holds(*st) for st in walked(steps))


def test_a_ledge_needs_a_climb():
    ledge = Ground(lambda x, z: [0.0] if x < 2000 else [300.0])
    grid = route.NavGrid(ledge)
    goal = Point("top", 1, (3000.0, 300.0, 1000.0))
    with pytest.raises(route.NoPath):
        route.plan_walk(grid, (1000.0, 0.0, 1000.0), goal)
    climb = grid.add_climb((1950.0, 0.0, 1000.0), 90.0)
    assert climb is not None and climb.top[1] == 300.0
    steps = route.plan_walk(grid, (1000.0, 0.0, 1000.0), goal)
    k = steps.index(climb)
    assert steps[k - 1] == (1950.0, 0.0, 1000.0) and steps[-1] == goal.at


def test_a_drop_is_walked():
    ledge = Ground(lambda x, z: [0.0] if x < 2000 else [300.0])
    steps = route.plan_walk(
        route.NavGrid(ledge), (3000.0, 300.0, 1000.0), Point("low", 1, (1000.0, 0.0, 1000.0))
    )
    assert steps[-1] == (1000.0, 0.0, 1000.0)


def test_a_roof_is_not_walked_under():
    tent = Ground(lambda x, z: [0.0, 500.0] if 1500 < x < 2500 and z < 3000 else [0.0])
    grid = route.NavGrid(tent)
    assert grid.levels(40, 20) == [] and grid.levels(10, 20) == [0.0]
    steps = route.plan_walk(grid, (1000.0, 0.0, 1000.0), Point("p", 1, (3000.0, 0.0, 1000.0)))
    assert max(z for _, _, z in walked(steps)) >= 3000


@pytest.fixture
def area_change(fake, monkeypatch):
    """A game in stage 1 whose area changes when the request is taken at AREA_CHANGE_POLL."""
    fake.poke("I", a.MAP_MANAGER_PTR, MAP_MANAGER)
    fake.poke("H", MAP_MANAGER + a.MAP_MANAGER.STAGE, 1)
    fake.poke("H", a.AREA_INDEX, 1)
    fake.poke("B", a.SCREEN_STATE, Screen.IN_AREA)
    fake.poke("I", a.PLAYER_ENTITY + a.ENTITY.VTABLE, a.PLAYER_QUEST_VTABLE)
    fake.poke("H", RECORD + a.STAGE_EXIT.TARGET, 2)
    add, resume = fake._table["cpu.breakpoint.add"], fake._table["cpu.resume"]

    async def add_breakpoint(ws, msg):
        await add(ws, msg)
        if msg["address"] == a.AREA_CHANGE_POLL:
            stop = fake.execute(msg["address"])
            asyncio.get_running_loop().call_later(0.05, asyncio.ensure_future, stop)

    async def resume_(ws, msg):
        await resume(ws, msg)
        manager = a.MONSTER_MANAGER_SINGLETON
        (requests,) = fake.peek("I", manager + a.MONSTER_MANAGER.REQUESTS)
        (exit_,) = fake.peek("I", manager + a.MONSTER_MANAGER.EXIT)
        (exiting,) = fake.peek("B", a.PLAYER_ENTITY + a.HUNTER.EXITING)
        if requests & 1 and exit_ and exiting:
            (target,) = fake.peek("H", exit_ + a.STAGE_EXIT.TARGET)
            fake.poke("H", a.AREA_INDEX, target)
            fake.poke("H", MAP_MANAGER + a.MAP_MANAGER.STAGE, target)
            fake.poke("I", manager + a.MONSTER_MANAGER.REQUESTS, 0)

    monkeypatch.setitem(fake._table, "cpu.breakpoint.add", add_breakpoint)
    monkeypatch.setitem(fake._table, "cpu.resume", resume_)
    return fake


@pytest.mark.parametrize("fake", [True], indirect=True)
def test_change_area(s, area_change):
    assert route.change_area(s, gate(), target=3, dest=(1.0, 2.0, 3.0), yaw=0x4000) == 3
    assert area_change.peek("H", RECORD + a.STAGE_EXIT.TARGET) == (3,)
    assert area_change.peek("3f", RECORD + a.STAGE_EXIT.DEST) == (1.0, 2.0, 3.0)
    assert area_change.peek("H", RECORD + a.STAGE_EXIT.YAW) == (0x4000,)
    assert not area_change.stepping and not area_change.breakpoints


@pytest.mark.parametrize("fake", [True], indirect=True)
def test_goto_another_stage(s, area_change, monkeypatch):
    landed = []

    def teleport(s, x, z, near=None):
        landed.append((x, z))
        return x, near, z

    monkeypatch.setattr(rig, "teleport", teleport)
    plan = route.Map((1, 2), {1: [gate()], 2: []})
    assert route.goto(s, Point("p", 2, (100.0, 5.0, 200.0)), plan) == (100.0, 5.0, 200.0)
    assert area_change.peek("H", RECORD + a.STAGE_EXIT.TARGET) == (2,)
    assert landed == [(100.0, 200.0)]


def test_goto_this_stage_teleports(s, fake, monkeypatch):
    fake.poke("I", a.MAP_MANAGER_PTR, MAP_MANAGER)
    fake.poke("H", MAP_MANAGER + a.MAP_MANAGER.STAGE, 1)
    monkeypatch.setattr(rig, "teleport", lambda s, x, z, near=None: (x, near, z))
    monkeypatch.setattr(route, "change_area", lambda *a, **kw: pytest.fail("changed area"))
    plan = route.Map((1,), {1: [gate()]})
    assert route.goto(s, Point("p", 1, (1.0, 2.0, 3.0)), plan) == (1.0, 2.0, 3.0)


def quad(x, z0, z1, y0, y1, normal_x=-1.0, material=0):
    """Two triangles of a wall in the plane x = `x`, its normal along x."""
    a, b, c, d = (x, y0, z0), (x, y0, z1), (x, y1, z1), (x, y1, z0)
    n = (normal_x, 0.0, 0.0)
    return [
        Triangle(0, material, 0, a, b, c, n, -normal_x * x, 0),
        Triangle(0, material, 0, a, c, d, n, -normal_x * x, 0),
    ]


def cliff(height):
    """Floor 0 west of x = 2000, `height` east of it."""
    return Ground(lambda x, z: [0.0] if x < 2000 else [height])


def test_climbable_wall_is_a_climb():
    walls = quad(2000.0, 900.0, 1100.0, -50.0, 900.0, material=10)
    (face,) = route.faces(walls)
    assert face.normal == pytest.approx((-1.0, 0.0)) and face.middle == pytest.approx((2000, 1000))
    (c,) = route.wall_climbs(cliff(900.0), walls)
    assert c.foot == pytest.approx((1900.0, 0.0, 1000.0)) and c.heading == pytest.approx(90)
    assert c.top == pytest.approx((2150.0, 900.0, 1000.0)) and c.height == 900
    assert route.wall_climbs(cliff(900.0), quad(2000.0, 900.0, 1100.0, -50.0, 900.0)) == []


def test_ledge_is_a_climb_and_a_tall_wall_is_not():
    found = route.ledges(cliff(250.0), quad(2000.0, 0.0, 600.0, 0.0, 250.0))
    assert found and all(c.heading == pytest.approx(90) for c in found)
    assert {round(c.top[1]) for c in found} == {250}
    assert route.ledges(cliff(900.0), quad(2000.0, 0.0, 600.0, 0.0, 900.0)) == []
    assert route.ledges(cliff(60.0), quad(2000.0, 0.0, 600.0, 0.0, 60.0)) == []  # walked


def test_terrain_climbs_the_cliff():
    walls = quad(2000.0, 0.0, 4000.0, -50.0, 900.0)
    walls += quad(2000.0, 900.0, 1100.0, -50.0, 900.0, material=10)
    grid = route.terrain(cliff(900.0), walls)
    steps = route.plan_walk(grid, (1000.0, 0.0, 1000.0), Point("top", 1, (3000.0, 900.0, 1000.0)))
    (c,) = [st for st in steps if isinstance(st, route.Climb)]
    assert c.heading == pytest.approx(90) and steps[-1] == (3000.0, 900.0, 1000.0)


def test_flood_reaches_exits_and_the_goal():
    low, high = gate(target=2, trigger=(500.0, -100.0, 500.0)), gate(target=3)
    grid = route.NavGrid(cliff(900.0))
    reach, found = grid.flood((1000.0, 0.0, 1000.0), [low, high], lambda x, y, z: x < 300)
    assert set(reach) == {low} and found is not None  # high is up the cliff
    assert reach[low] == pytest.approx(math.dist((1050, 1050), (750, 750)), abs=200)


def test_route_takes_the_climb_not_the_fewest_exits():
    """1 -> 2 is up an unclimbable cliff; 1 -> 3 -> 2 walks round. The route goes round."""
    up = gate(1, 2, trigger=(3500.0, 800.0, 2000.0))
    via = gate(1, 3, trigger=(500.0, -100.0, 500.0))
    on = gate(3, 2, trigger=(500.0, -100.0, 500.0))
    on = route.Gate(3, 2, on.trigger, on.radius, on.height, (3000.0, 900.0, 3000.0), 0, RECORD)
    plan = route.Map((1, 2, 3), {1: [up, via], 2: [], 3: [on]}, game=None)
    plan._grids = {(1, ()): route.NavGrid(cliff(900.0)), (2, ()): route.NavGrid(cliff(900.0))}
    plan._grids[(3, ())] = route.NavGrid(Ground())
    goal = Point("g", 2, (3500.0, 900.0, 3500.0))
    assert plan.route(1, (1000.0, 0.0, 1000.0), goal) == [via, on]
    with pytest.raises(route.NoPath):
        plan.route(1, (1000.0, 0.0, 1000.0), goal, banned=[via])
