# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import math

import pytest
from mhfu import addresses as a
from mhfu.live import navigation as nav

PLAYER_AT = a.PLAYER_ENTITY + a.ENTITY.TRANSLATION


def stick(fake):
    """The last stick position sent."""
    for msg in reversed(fake.received):
        if msg["event"] == "input.analog.send":
            return msg["x"], msg["y"]
    return 0.0, 0.0


class World:
    """The player walks along the stick at `speed` units/s under a camera at `yaw` radians;
    `blocked(x, z)` walls off ground. Time is the test clock, so walks advance on sleeps."""

    def __init__(self, s, fake, clock, x=0.0, z=0.0, yaw=0.0, speed=140.0, blocked=None, dead=0.0):
        self.fake, self.clock = fake, clock
        self.speed, self.dead = speed, dead
        self.blocked = blocked or (lambda x, z: False)
        self.then, self.stick = clock.now, (0.0, 0.0)
        self.look(yaw)
        self.place(x, z)
        fake.on_read.append(lambda address: address == PLAYER_AT and self.advance())
        send = s.client.analog

        def analog(x, y):
            self.advance()
            self.stick = (x, y)
            send(x, y)

        s.client.analog = analog

    def look(self, yaw):
        self.fake.poke("3f", a.CAM_VIEW_EYE, 0.0, 0.0, 0.0)
        self.fake.poke("3f", a.CAM_LOOK_AT, math.sin(yaw), 0.0, math.cos(yaw))
        self.yaw = yaw

    def place(self, x, z, heading=None):
        self.x, self.z = x, z
        self.fake.poke("3f", PLAYER_AT, x, 0.0, z)
        if heading is not None:
            rotation = a.PLAYER_ENTITY + a.ENTITY.ROTATION
            self.fake.poke("f", rotation + 8 * 4, math.sin(heading))
            self.fake.poke("f", rotation + 10 * 4, math.cos(heading))

    def advance(self):
        dt, self.then = self.clock.now - self.then, self.clock.now
        magnitude = math.hypot(*self.stick)
        if magnitude <= self.dead:
            return
        heading = self.yaw - math.atan2(*self.stick)
        step = self.speed * magnitude * dt
        x, z = self.x + step * math.sin(heading), self.z + step * math.cos(heading)
        if self.blocked(x, z):
            x, z = self.x, self.z
        self.place(x, z, heading)


@pytest.mark.parametrize("yaw", [0.0, 1.0, -2.5])
def test_stick_solve(yaw):
    for octant in range(8):
        angle = octant * math.pi / 4 - math.pi
        sx, sy = nav.stick_for(angle, yaw)
        walked = yaw - math.atan2(sx, sy)
        assert math.isclose(math.cos(walked), math.cos(angle), abs_tol=1e-9)
        assert math.isclose(math.sin(walked), math.sin(angle), abs_tol=1e-9)


def test_walk_arrives_under_a_turned_camera(s, fake, clock):
    World(s, fake, clock, yaw=1.0)
    walk = nav.walk_to(s, 1000.0, 500.0)
    assert walk.reached and walk.reason == "arrived"
    assert nav.distance(nav.where(s), (1000.0, 500.0)) <= 60.0
    assert stick(fake) == (0.0, 0.0)


def test_walk_follows_a_camera_that_turns(s, fake, clock):
    world = World(s, fake, clock)
    fake.on_read.append(lambda addr: clock.now > 2 and world.yaw == 0 and world.look(2.0))
    assert nav.walk_to(s, 0.0, 1500.0).reached


def test_walk_blocked(s, fake, clock):
    World(s, fake, clock, blocked=lambda x, z: x > 300)
    walk = nav.walk_to(s, 1000.0, 0.0)
    assert (walk.reached, walk.reason, walk.detours) == (False, "blocked", 4)
    assert walk.remaining > 600
    assert stick(fake) == (0.0, 0.0)


def test_walk_timeout(s, fake, clock):
    World(s, fake, clock, speed=10.0)
    walk = nav.walk_to(s, 5000.0, 0.0, timeout=5.0)
    assert walk.reason == "timeout" and not walk.reached


def test_path_releases_a_failed_leg(s, fake, clock):
    World(s, fake, clock, blocked=lambda x, z: x > 300)
    walk = nav.walk_path(s, [(1000.0, 0.0), (1000.0, 1000.0)])
    assert walk.reason == "blocked"
    assert stick(fake) == (0.0, 0.0)


def test_face(s, fake, clock):
    World(s, fake, clock).place(0.0, 0.0, heading=0.0)
    assert nav.face(s, 1000.0, 0.0)
    assert abs(math.degrees(nav.pose(s).yaw) - 90) <= 25
    assert stick(fake) == (0.0, 0.0)


def test_creep_stops_on_until(s, fake, clock):
    World(s, fake, clock)
    assert nav.creep_to(s, 500.0, 0.0, until=lambda s: nav.where(s)[0] > 200)
    assert stick(fake) == (0.0, 0.0)


def test_creep_gives_up_wedged(s, fake, clock):
    World(s, fake, clock, blocked=lambda x, z: x > 100)
    start = clock.now
    assert not nav.creep_to(s, 500.0, 0.0, until=lambda s: False)
    assert clock.now - start < 5


def test_push_holds_a_heading(s, fake, clock):
    World(s, fake, clock, yaw=0.7)
    p = nav.push(s, -90.0, 2.0)
    assert p.x < -200 and abs(p.z) < 1
    assert stick(fake) == (0.0, 0.0)


def test_climb_grabs_with_circle(s, fake, clock):
    World(s, fake, clock)
    before, after = nav.climb(s, 0.0, hold=1.0)
    assert fake.presses == ["circle"]
    assert after.z > before.z
    assert stick(fake) == (0.0, 0.0)


def test_detour_is_a_distance_under_fast_forward(s, fake, clock):
    world = World(s, fake, clock, speed=1400.0, blocked=lambda x, z: x > 300)
    widest = []
    fake.on_read.append(lambda addr: widest.append(abs(world.z)))
    nav.walk_to(s, 1000.0, 0.0, max_detours=1)
    assert 0 < max(widest) < nav.DETOUR_DISTANCE + 0.12 * 1400


def test_path_stops_on_until(s, fake, clock):
    World(s, fake, clock)
    walk = nav.walk_path(s, [(1000.0, 0.0), (1000.0, 1000.0)], until=lambda: clock.now > 3)
    assert walk.reason == "until" and walk.at[1] < 1
    assert stick(fake) == (0.0, 0.0)


def test_walk_arrives_past_the_dead_zone(s, fake, clock):
    World(s, fake, clock, dead=0.5)  # a quest hunter does not move on a stick of 0.5 or less
    walk = nav.walk_to(s, 1000.0, 0.0)
    assert walk.reached and walk.remaining <= 60.0
