# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The example mods in lupa, against the declared API."""

import math
import tomllib
from pathlib import Path
from typing import Any

import pytest
from mhfu import addresses as a

LUA = Path(__file__).parents[1] / "lua"
TIGREX, GIADROME, POPO = 0x4B, 0x4D, 0x46
ENT = a.RAM.start + 0x100000
QUEST = a.RAM.start + 0x200000
SPIN = 43


def run(lua: Any, name: str) -> Any:
    """Runs examples/<name>.lua; returns the fake `mhfu`."""
    lua.execute((LUA / "examples" / f"{name}.lua").read_text(encoding="utf-8"))
    return lua.globals().mhfu


def handler(m: Any, event: str) -> Any:
    return next(c[2] for c in m.calls.values() if c[1] == event)


def calls(m: Any, name: str) -> list[tuple[Any, ...]]:
    return [tuple(c.values())[1:] for c in m.calls.values() if c[1] == name]


def action(lua: Any, species: int = TIGREX) -> Any:
    return lua.table_from({"entity": ENT, "type": species, "action_id": 5})


def test_action_override_pulses(lua: Any) -> None:
    m = run(lua, "action_override")
    on_action = handler(m, "on_bigmonster_action")
    ctx = action(lua)
    clock = [90000]
    m.get_quest_timer = lambda: clock[0]
    assert on_action(ctx) == SPIN
    clock[0] -= 299
    assert on_action(ctx) is None
    clock[0] -= 1
    assert on_action(ctx) == SPIN
    assert "[action_override] a1 5 -> 43 (spin)" in m.logs.values()


def test_action_override_leaves_the_rest(lua: Any) -> None:
    m = run(lua, "action_override")
    on_action = handler(m, "on_bigmonster_action")
    assert on_action(action(lua, GIADROME)) is None
    m.entity_section = lambda _ent: 1  # not the player's section
    assert on_action(action(lua)) is None


def test_action_override_clears_the_freeze_gate(lua: Any) -> None:
    m = run(lua, "action_override")
    m.read_u32 = lambda _at: 0x10107
    handler(m, "on_bigmonster_action")(action(lua))
    assert calls(m, "write_u32") == [(ENT + a.ENTITY.FREEZE_GATE, 0x7)]


@pytest.mark.parametrize(
    ("monsters", "done"),
    [({TIGREX}, ["add"]), ({GIADROME}, ["replace", "add"]), ({POPO}, [])],
)
def test_second_monster_adds_a_tigrex(lua: Any, monsters: set[int], done: list[str]) -> None:
    m = run(lua, "second_monster")
    quest, did = set(monsters), []

    def replace(_q: int, old: int, new: int) -> bool:
        quest.discard(old)
        quest.add(new)
        did.append("replace")
        return True

    def add(_q: int, mon: int) -> bool:
        did.append("add")
        return mon == TIGREX

    m.quest_has = lambda _q, mon: mon in quest
    m.quest_replace_monster, m.quest_add_monster = replace, add
    handler(m, "on_quest_targets_building")(QUEST)
    assert did == done
    assert ("[second_monster] added a second Tigrex" in m.logs.values()) == bool(done)


def test_second_monster_logs_tigrex_spawns(lua: Any) -> None:
    m = run(lua, "second_monster")
    spawn = handler(m, "on_bigmonster_spawn")
    spawn(ENT, POPO, 1, 100)
    spawn(ENT, TIGREX, 2, 3200)
    assert list(m.logs.values())[-1] == "[second_monster] Tigrex in slot 2, hp 3200"


FAKE_PORT = """
local port = {}
function port.mod(name, fn)
  port.name = name
  fn({
    log = function(fmt, ...) mhfu.logs[#mhfu.logs + 1] = string.format(fmt, ...) end,
    define = function(spec)
      port.spec = spec
      return { brain = function(self, f) port.brain = f; return self end }
    end,
  })
end
package.loaded.mhfu_port = port
"""


def test_ported_brute_reports_section_changes(lua: Any) -> None:
    lua.execute(FAKE_PORT)
    m = run(lua, "ported_brute")
    port = lua.eval("package.loaded.mhfu_port")
    assert (port.name, port.spec.name, port.spec.fid) == ("ported_brute", "brute_tigrex", 6186)
    for state in (
        {"same_section": False, "dist": 9000},
        {"same_section": True, "dist": 2000.4},
        {"same_section": True, "dist": 1500},
        {"same_section": False, "dist": 4000},
    ):
        port.brain(lua.table_from(state))
    assert list(m.logs.values()) == [
        "[ported_brute] in your section, 2000 units away",
        "[ported_brute] left your section",
    ]


@pytest.mark.skipif(
    not (LUA / "lib" / "mhfu_port.lua").exists(), reason="lib/mhfu_port.lua not written yet"
)
def test_ported_brute_loads_on_mhfu_port(lua: Any) -> None:
    m = run(lua, "ported_brute")
    fid, pac, orig = calls(m, "inject_relocate")[0]
    assert fid == 6186
    assert pac.endswith("/brute_tigrex.bin") and orig.endswith("/file_06185.bin.orig")


def test_ported_zinogre_dashes_at_a_far_hunter(lua: Any, tmp_path: Path) -> None:
    (tmp_path / "zinogre_moves.lua").write_text(
        "return { moves = { dash = { entry = 20, carrier = { 0, 2 }, steer = { walls = true },"
        ' after = "dash_stop" }, dash_stop = { entry = 21 } }, rules = {} }\n'
    )
    lua.execute(f"package.path = package.path .. ';' .. {str(tmp_path / '?.lua')!r}")
    m = run(lua, "ported_zinogre")
    zin = lua.eval('require("mhfu_port").ports.zinogre')
    m.em_installed = lambda: True
    zin.ent, zin._native_armed = ENT, True
    state = {"native": True, "dist": 2000.0, "px": 1.0, "pz": 2.0, "tick": 100}
    zin._brain(lua.table_from({**state, "dist": 900.0}))
    zin._brain(lua.table_from(state))
    zin._brain(lua.table_from({**state, "tick": 110}))  # within the gap
    assert calls(m, "em_play") == [(ENT, 0, False)]


PORT = Path(__file__).parents[2] / "ports" / "zinogre.toml"
FOLLOWER_CLIPS = ("idle", "start_walk_forward", "stop_walk_forward", "walk_forwards_faster")
FOLLOWER_CLIPS += ("turn_right", "turn_left")
HUNTER = int(a.PLAYER_ENTITY) + a.ENTITY.TRANSLATION  # x at +0, z at +8
HUNTER_YAW = int(a.PLAYER_ENTITY) + a.ENTITY.YAW
SIZE = 0.3  # the follower's distances scale with it
# 150 deg/s, a quarter turn in 50 frames
WALK_RATE, TURN_RATE = 910, 327
SIDE, SLIDE, ARRIVE_MIN = 500 * SIZE, 570 * SIZE, 300 * SIZE  # beside the hunter; the stop
FAR, FASTER, PACE, TURN_DIST = 1200 * SIZE, 1800 * SIZE, 800 * SIZE, 240 * SIZE
V_FAST, RUN = 650 * SIZE, 174.0  # the faster walk's loop; the hunter's run, units/s


def zinogre_clips() -> dict[str, int]:
    """The entries `mhfu-port inject` writes to zinogre_clips.lua: a pinned clip's `slot`, else
    the entry of its MHP3rd id `source`, where a clip below the host's 123 entries sits."""
    clips = tomllib.loads(PORT.read_text(encoding="utf-8"))["clips"]
    return {name: clips[name].get("slot", clips[name].get("source")) for name in FOLLOWER_CLIPS}


CLIPS = zinogre_clips()
IDLE, START, STOP, FAST = (CLIPS[n] for n in FOLLOWER_CLIPS[:4])
TURN_R, TURN_L = CLIPS["turn_right"], CLIPS["turn_left"]


def place(dist: float, off: float = 0.0, yaw: int = 0) -> tuple[float, float]:
    """A point `dist` from an NPC at the origin facing `yaw`, `off` degrees toward its left."""
    bearing = math.radians(yaw * 360 / 0x10000 + off)
    return dist * math.sin(bearing), dist * math.cos(bearing)


class Follower:
    """village_follower over a stubbed village. tick() sets what npc_status, the hunter and the
    camera read, runs one 2 Hz tick and returns the (npc_play, npc_face) calls it made; its
    npc_arrive and npc_speed calls land in `arrived` and `speeds`, the clip speed in `speed`."""

    def __init__(self, lua: Any, *, settle: bool = True) -> None:
        fields = ", ".join(f"{k} = {v}" for k, v in CLIPS.items())
        lua.execute(f"package.loaded.zinogre_clips = {{ {fields} }}")
        self.lua, self.m = lua, lua.globals().mhfu
        run(lua, "village_follower")
        self.added = calls(self.m, "npc_add")
        self.status: Any = None
        self.hunter: dict[int, float] = {}
        self.hyaw = 0
        self.frames = 0
        self.arrived: list[tuple[Any, ...]] = []
        self.speeds: list[tuple[Any, ...]] = []
        self.speed = 1.0
        self.m.npc_status = lambda _slot: self.status
        self.m.read_f32 = lambda at: self.hunter.get(at, 0.0)
        self.m.read_u16 = lambda at: self.hyaw if at == HUNTER_YAW else 0
        lua.execute("mhfu.player_pos = function() return eye_x, 300.0, eye_z end")
        if settle:
            self.tick(frames=3)

    def tick(
        self,
        *,
        entry: int = IDLE,
        at: tuple[float, float] = (0.0, 180.0),
        npc: tuple[float, float] = (0.0, 0.0),
        yaw: int = 0,
        hyaw: float = 0.0,
        eye: tuple[float, float] | None = None,
        frames: int | None = None,
        shown: bool = True,
        **status: Any,
    ) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
        """at: the hunter, facing `hyaw` degrees; npc: the NPC, facing `yaw`; eye: the camera, by
        default 300 behind the hunter, where neither side is farther from it."""
        self.frames = self.frames + 15 if frames is None else frames
        self.hunter = {HUNTER: at[0], HUNTER + 8: at[1]}
        self.hyaw = round(hyaw * 0x10000 / 360) % 0x10000
        if eye is None:
            a = math.radians(hyaw)
            eye = (at[0] - 300 * math.sin(a), at[1] - 300 * math.cos(a))
        self.lua.globals().eye_x, self.lua.globals().eye_z = eye
        fields = {"object": ENT, "frames": self.frames, "entry": entry, "playing": True}
        dist = math.dist(at, npc)
        fields |= {"yaw": yaw, "x": npc[0], "y": 0.0, "z": npc[1], "dist": dist} | status
        self.status = self.lua.table_from(fields) if shown else None
        self.lua.execute("mhfu.calls = {}")
        self.lua.globals().mhfu_tick()
        self.arrived = calls(self.m, "npc_arrive")
        self.speeds = calls(self.m, "npc_speed")
        self.speed = self.speeds[-1][1] if self.speeds else self.speed
        return calls(self.m, "npc_play"), calls(self.m, "npc_face")

    def still(self, **kw: Any) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
        """Two ticks with nothing moving; the second's calls."""
        self.tick(**kw)
        return self.tick(**kw)

    def run(self, ticks: int, *, at: tuple[float, float], speed: float = RUN, **kw: Any) -> Any:
        """`ticks` ticks of a hunter running from `at` along +z; the last one's calls."""
        for k in range(ticks):
            out = self.tick(at=(at[0], at[1] + k * speed / 2), **kw)
        return out


def test_follower_adds_a_small_zinogre(lua: Any) -> None:
    ((pac, opts),) = Follower(lua, settle=False).added
    assert pac.endswith("/mhfu_framework/inject/zinogre.bin") and (opts.size, opts.right) == (
        SIZE,
        SIDE,
    )


def test_follower_adds_once_per_boot(lua: Any) -> None:
    f = Follower(lua, settle=False)
    run(lua, "village_follower")  # a hot reload
    assert len(calls(f.m, "npc_add")) == 1 and lua.eval("type(mhfu_tick)") == "function"


def test_follower_starts_at_clip_speed_1(lua: Any) -> None:
    """C keeps the clip speed over a hot reload: each load sets it back."""
    f = Follower(lua, settle=False)
    run(lua, "village_follower")
    assert calls(f.m, "npc_speed") == [(0, 1), (0, 1)]


@pytest.mark.parametrize("fail", ["add", "clips"])
def test_follower_failure_does_nothing(lua: Any, fail: str) -> None:
    m = lua.globals().mhfu
    if fail == "add":
        lua.execute(f"package.loaded.zinogre_clips = {{ {', '.join(f'{k} = 1' for k in CLIPS)} }}")
        lua.execute('mhfu.npc_add = function() return nil, "extra RAM is short" end')
    run(lua, "village_follower")
    assert lua.eval("mhfu_tick") is None
    (line,) = m.logs.values()
    assert line.startswith("[village_follower] " + ("npc_add failed" if fail == "add" else "needs"))


@pytest.mark.parametrize("gone", [{"shown": False}, {"object": 0}])
def test_follower_waits_without_an_object(lua: Any, gone: dict[str, Any]) -> None:
    f = Follower(lua)
    assert f.tick(at=place(480), **gone) == ([], [])


def test_follower_stands_on_spawn(lua: Any) -> None:
    f = Follower(lua, settle=False)
    assert f.tick(frames=3, entry=0, at=place(480)) == ([(0, IDLE, 0)], [(0, "still")])


def stop(dist: float) -> tuple[Any, ...]:
    """The npc_arrive call that stops a walk `dist` short of its aim."""
    return (0, pytest.approx(dist, abs=0.5), STOP, 6, IDLE)


def test_follower_walks_to_a_far_hunter(lua: Any) -> None:
    f = Follower(lua)
    assert f.still(at=place(420)) == ([(0, START, 6)], [(0, "hunter", SIDE, 0, WALK_RATE)])
    assert f.arrived == [stop(SLIDE)] and f.speed == 1  # a still hunter: the clips' own speed
    assert list(f.m.logs.values())[-1] == "[village_follower] walk, 420 away"
    assert f.tick(entry=START, at=place(420), npc=(0, 50)) == ([], [])  # it walks on by itself
    assert f.arrived == [stop(SLIDE)]  # the stop, re-armed for where the hunter is now


@pytest.mark.parametrize(("moved", "play"), [(0.0, None), (90.0, None), (150.0, FAST)])
def test_follower_follows_a_hunter_who_moved_off(lua: Any, moved: float, play: Any) -> None:
    """Inside FAR: followed once the hunter moved MOVED from where it came to rest; at 300/s, its
    aim past PACE, on the faster walk."""
    f = Follower(lua)
    f.tick(at=(0.0, 180.0))  # at rest
    plays, _ = f.tick(at=(0.0, 180.0 + moved), npc=(0.0, moved - 90.0))
    assert [p[1] for p in plays] == ([] if play is None else [play])


def test_follower_keeps_to_its_side(lua: Any) -> None:
    """On the hunter's left (they face +z, so their left is +x), it aims left of them; when they
    turn about mid-walk, it is on their right and aims there."""
    f = Follower(lua)
    assert f.still(at=(0.0, 420.0), npc=(180.0, 0.0))[1] == [(0, "hunter", -SIDE, 0, WALK_RATE)]
    assert f.tick(entry=START, at=(0.0, 420.0), npc=(180.0, 60.0)) == ([], [])
    turned = f.tick(entry=START, at=(0.0, 420.0), hyaw=180.0, npc=(180.0, 120.0))
    assert turned == ([], [(0, "hunter", SIDE, 0, WALK_RATE)])
    assert f.tick(entry=START, at=(0.0, 420.0), hyaw=180.0, npc=(24.0, 180.0)) == ([], [])


@pytest.mark.parametrize(("entry", "dist"), [(IDLE, FASTER + 180), (START, FASTER + 30)])
def test_follower_walks_faster_when_very_far(lua: Any, entry: int, dist: float) -> None:
    plays, _ = Follower(lua).still(entry=entry, at=place(dist))
    assert plays == [(0, FAST, 6)]


def test_follower_never_slows_a_fast_walk(lua: Any) -> None:
    assert Follower(lua).still(entry=FAST, at=place(FAR)) == ([], [])


@pytest.mark.parametrize("entry", [START, FAST])
def test_follower_arrives_by_the_slide(lua: Any, entry: int) -> None:
    """C stops it, the slide short of a still hunter's aim; the stop clip is not Lua's."""
    f = Follower(lua)
    hunter, aim_x = (0.0, 600.0), -SIDE  # facing +z: their right is -x
    assert f.still(entry=entry, at=hunter, npc=(aim_x, 600.0 - SLIDE + 1)) == ([], [])
    assert f.arrived == [stop(SLIDE)]
    assert f.tick(entry=STOP, at=hunter, npc=(aim_x, 600.0 - 60)) == ([], [])  # its `after`
    assert f.arrived == []
    assert f.tick(entry=IDLE, at=hunter, npc=(aim_x, 600.0), yaw=0x4000) == ([], [(0, "still")])


@pytest.mark.parametrize(
    ("was", "dist"),
    [
        (564.0, ARRIVE_MIN),  # walking on at 72/s, its aim draws off 144: never under the floor
        (588.0, SLIDE - 48),
        (636.0, SLIDE + 144),  # walking up to it: stops sooner
    ],
)
def test_follower_arrival_leads_the_hunter(lua: Any, was: float, dist: float) -> None:
    f = Follower(lua)
    f.tick(entry=START, at=(0.0, was), npc=(-SIDE, 300.0))
    assert f.tick(entry=START, at=(0.0, 600.0), npc=(-SIDE, 420.0))[0] == []
    assert f.arrived == [stop(dist)]


def order(f: Follower) -> list[str]:
    """The names of the calls the last tick made, in order; npc_speed's aside."""
    return [c[1] for c in f.m.calls.values() if c[1] != "npc_speed"]


def test_follower_arms_every_walk(lua: Any) -> None:
    """A walk, a faster one, a change of side: each faces and arms the stop after it."""
    f = Follower(lua)
    npc = (180.0, 0.0)  # on the left of a hunter facing +z
    f.still(at=(0.0, 420.0), npc=npc)
    assert order(f) == ["npc_play", "npc_face", "npc_arrive"]
    f.still(entry=START, at=(0.0, 570.0), npc=npc)
    assert order(f) == ["npc_play", "npc_face", "npc_arrive"]
    assert f.arrived == [stop(SLIDE)]
    f.tick(entry=FAST, at=(0.0, 570.0), hyaw=180.0, npc=npc)  # turned about: now on their right
    assert order(f) == ["npc_face", "npc_arrive"]


@pytest.mark.parametrize(
    ("was", "at", "walks"),
    [(344.0, 380.0, False), (416.0, 380.0, False), (420.0, 420.0, True)],
)
def test_follower_starts_no_walk_a_still_hunter_would_not(
    lua: Any, was: float, at: float, walks: bool
) -> None:
    """Its aim 230 away, inside PACE: walking off or up, the hunter starts no walk a still one
    would not (their turn swings the aim); 270 away, a still one does."""
    f = Follower(lua)
    f.tick(at=(0.0, 180.0))
    f.tick(at=(was, 0.0), yaw=0x4000)
    assert f.tick(at=(at, 0.0), yaw=0x4000)[0] == ([(0, START, 6)] if walks else [])


def test_follower_idles_after_a_clip_that_ended_alone(lua: Any) -> None:
    assert Follower(lua).tick(entry=STOP, playing=False)[0] == [(0, IDLE, 6)]
    assert Follower(lua).tick(entry=77)[0] == [(0, IDLE, 6)]  # a clip nobody asked for


@pytest.mark.parametrize("dist", [180.0, 420.0])
@pytest.mark.parametrize(("off", "clip"), [(90.0, TURN_L), (-90.0, TURN_R), (170.0, TURN_L)])
def test_follower_turns_to_a_hunter_behind(lua: Any, dist: float, off: float, clip: int) -> None:
    """Positive is the way YAW grows, toward the Zinogre's left, where turn_left turns."""
    f = Follower(lua)
    at = place(dist, off)
    assert f.still(at=at) == ([(0, clip, 6, IDLE)], [(0, "hunter", 0, 0, TURN_RATE)])
    faced = round(off * 0x10000 / 360) % 0x10000
    assert f.tick(entry=clip, at=at, yaw=faced) == ([], [])  # the clip plays
    walk = [(0, START, 6)] if dist > FAR else []  # its `after` took over, facing the hunter
    assert f.tick(entry=IDLE, at=at, yaw=faced)[0] == walk


def test_follower_measures_the_turn_across_the_yaw_wrap(lua: Any) -> None:
    f = Follower(lua)
    assert f.still(yaw=0xF000, at=place(180, 45.0, 0xF000))[0] == []  # 45 degrees off
    assert f.still(yaw=0xF000, at=place(180, -60.0, 0xF000))[0] == [(0, TURN_R, 6, IDLE)]
    assert f.still(yaw=0xF000, at=place(180, 60.0, 0xF000))[0] == [(0, TURN_L, 6, IDLE)]


@pytest.mark.parametrize(
    ("entry", "dist", "off", "play"),
    [
        (IDLE, FAR - 1, 0.0, None),
        (IDLE, FAR + 1, 0.0, START),
        (IDLE, 180.0, 49.0, None),
        (IDLE, 180.0, -51.0, TURN_R),
        (IDLE, TURN_DIST - 10, 170.0, None),  # on top of the hunter their bearing is noise
        (IDLE, 420.0, 60.0, TURN_L),  # turn first, then walk
        (START, FASTER - 1, 0.0, None),
        (START, FASTER + 1, 0.0, FAST),
    ],
)
def test_follower_thresholds(
    lua: Any, entry: int, dist: float, off: float, play: int | None
) -> None:
    plays, _ = Follower(lua).still(entry=entry, at=place(dist, off))
    assert [p[1] for p in plays] == ([] if play is None else [play])


def test_follower_never_starts_a_walk_it_would_stop(lua: Any) -> None:
    """Far from a hunter who moved, but with its aim, SIDE toward it, near: no step-and-stop."""
    f = Follower(lua)
    f.tick(at=(0.0, 180.0))
    assert f.still(at=(390.0, 0.0), yaw=0x4000)[0] == []  # the aim is 240 away
    assert f.still(at=(420.0, 0.0), yaw=0x4000)[0] == [(0, START, 6)]  # 270


def test_follower_does_not_flicker_between_states(lua: Any) -> None:
    f = Follower(lua)
    for dist in (354.0, 180.0, 270.0, 159.0, 300.0):  # a still hunter: idle holds in the band
        assert f.still(entry=IDLE, at=place(dist))[0] == []
    for dist in (354.0, 180.0, 159.0, 270.0, 300.0):  # and a walk holds away from the aim
        assert f.still(entry=START, at=place(dist))[0] == []


def test_follower_starts_over_on_a_respawn(lua: Any) -> None:
    f = Follower(lua)
    assert f.still(at=place(420))[0] == [(0, START, 6)]
    assert f.tick(entry=START, at=place(420))[0] == []
    assert f.tick(object=0) == ([], [])  # the village unloaded
    assert f.tick(frames=3, entry=START, at=place(420)) == ([(0, IDLE, 0)], [(0, "still")])
    assert f.tick(at=place(420))[0] == [(0, START, 6)]  # now it follows again
    # a load between two ticks shows only as the frame count starting over
    assert f.tick(entry=START, frames=2, at=place(420))[0] == [(0, IDLE, 0)]


# --- keeping pace with a hunter who keeps moving --------------------------------------------------


def test_follower_walks_faster_to_keep_pace(lua: Any) -> None:
    """On the walk, its aim past PACE and the hunter running on: the faster walk at once."""
    f = Follower(lua)
    for k, gap in enumerate((PACE - 10, PACE - 10, PACE + 10)):
        hz = 300.0 + k * RUN / 2
        plays, _ = f.tick(entry=START, at=(0.0, hz), npc=(-SIDE, hz - gap))
    assert plays == [(0, FAST, 6)]
    assert list(f.m.logs.values())[-1].startswith("[village_follower] walk faster")


def test_follower_walks_on_for_a_still_hunter_past_pace(lua: Any) -> None:
    assert (
        Follower(lua).still(entry=START, at=(0.0, 600.0), npc=(-SIDE, 600.0 - PACE - 10))[0] == []
    )


def test_follower_starts_a_runner_on_the_faster_walk(lua: Any) -> None:
    """From rest beside them: once their aim is past PACE, two ticks into their run."""
    f = Follower(lua)
    f.still(at=(0.0, 0.0), npc=(-SIDE, -60.0))
    assert f.run(3, at=(0.0, 0.0), npc=(-SIDE, -60.0))[0] == []
    assert f.tick(at=(0.0, 1.5 * RUN), npc=(-SIDE, -60.0))[0] == [(0, FAST, 6)]


@pytest.mark.parametrize(
    ("behind", "speed"),
    [
        (SLIDE + RUN / 4, 0.9),  # its hold: their pace on the faster walk, 174 / 195
        (SLIDE + RUN / 4 + 100, 1.15),  # 50 units/s more to close 100
        (600.0, 1.4),  # never above SPEED_MAX
        (40.0, 0.7),  # nor under SPEED_MIN: the arrival stops it if it gets that close
    ],
)
def test_follower_paces_a_runner(lua: Any, behind: float, speed: float) -> None:
    """The faster walk `behind` its aim with the hunter running straight on."""
    f = Follower(lua)
    f.run(2, at=(0.0, 0.0), entry=FAST, npc=(-SIDE, -behind))
    f.tick(entry=FAST, at=(0.0, RUN), npc=(-SIDE, RUN - behind))
    assert f.speed == pytest.approx(speed, abs=0.026)


def test_follower_slows_to_the_clips_own_speed(lua: Any) -> None:
    """The hunter stops: back to speed 1 on the walk; on idle as well, after the arrival."""
    f = Follower(lua)
    f.run(3, at=(0.0, 0.0), entry=FAST, npc=(-SIDE, -400.0))
    assert f.speed == 1.4
    f.tick(entry=FAST, at=(0.0, RUN), npc=(-SIDE, -300.0))
    assert f.speeds == [(0, 1)]
    f.run(3, at=(0.0, 0.0), entry=FAST, npc=(-SIDE, -400.0))
    f.tick(entry=IDLE, at=(0.0, RUN), npc=(-SIDE, RUN))
    assert f.speeds == [(0, 1)]


def test_follower_turns_on_the_spot_only_for_a_still_hunter(lua: Any) -> None:
    """A hunter running off behind it: no turn clip, the walk's face turns it."""
    f = Follower(lua)
    f.still(at=(0.0, -100.0))
    plays, faces = f.run(3, at=(0.0, -100.0), speed=-RUN)
    assert plays == [(0, FAST, 6)] and faces[0][1] == "hunter"


@pytest.mark.parametrize("entry", [STOP, TURN_L])
def test_follower_stop_gives_way_to_a_runner(lua: Any, entry: int) -> None:
    f = Follower(lua)
    assert f.run(3, at=(0.0, 0.0), entry=entry, npc=(-SIDE, -60.0))[0] == []  # aim inside PACE
    assert f.tick(entry=entry, at=(0.0, 1.5 * RUN), npc=(-SIDE, -60.0))[0] == [(0, FAST, 6)]
    assert Follower(lua).still(entry=entry, at=(0.0, 600.0), npc=(-SIDE, 0.0)) == ([], [])


def test_follower_no_pace_for_a_still_hunter(lua: Any) -> None:
    """Toward a still hunter it walks at the clips' own speed, however far."""
    f = Follower(lua)
    f.still(entry=FAST, at=(0.0, 1000.0), npc=(-SIDE, 0.0))
    f.tick(entry=FAST, at=(0.0, 1000.0), npc=(-SIDE, 100.0))
    assert f.speeds == [] and f.speed == 1


# a hunter at (0, 600) facing +z; their right is -x, their left +x
CAM_RIGHT = (-300.0, 600.0)  # the camera off their right: the left aim is the far one


def test_follower_prefers_the_side_away_from_the_camera(lua: Any) -> None:
    f = Follower(lua)
    walk = f.still(at=(0.0, 600.0), npc=(0.0, 180.0), eye=CAM_RIGHT)
    assert walk[1] == [(0, "hunter", -SIDE, 0, WALK_RATE)]
    walk = Follower(lua).still(at=(0.0, 600.0), npc=(0.0, 180.0), eye=(300.0, 600.0))
    assert walk[1] == [(0, "hunter", SIDE, 0, WALK_RATE)]


def test_follower_crosses_behind_the_hunter_to_the_far_side(lua: Any) -> None:
    """Over on the near side but more behind the hunter than beside them: it goes round."""
    walk = Follower(lua).still(at=(0.0, 600.0), npc=(-180.0, 180.0), eye=CAM_RIGHT)
    assert walk[1] == [(0, "hunter", -SIDE, 0, WALK_RATE)]


def test_follower_keeps_the_near_side_it_is_well_over(lua: Any) -> None:
    """Beside the hunter on the camera's side, it never cuts across them."""
    yaw = round(math.degrees(math.atan2(420, 60)) * 0x10000 / 360)  # facing the hunter
    walk = Follower(lua).still(at=(0.0, 600.0), npc=(-420.0, 540.0), yaw=yaw, eye=CAM_RIGHT)
    assert walk[1] == [(0, "hunter", SIDE, 0, WALK_RATE)]


def test_follower_side_holds_near_the_camera_line(lua: Any) -> None:
    """Both aims about as far from the camera, and it near the hunter's line: the side holds."""
    f = Follower(lua)
    eye = (-36.0, 300.0)  # nearly behind the hunter: the aims differ by under CAM
    assert f.still(at=(0.0, 600.0), npc=(24.0, 180.0), eye=eye)[1][-1][2] == SIDE
    f.tick(entry=START, at=(0.0, 600.0), npc=(-24.0, 240.0), eye=eye)
    assert f.tick(entry=START, at=(0.0, 600.0), npc=(30.0, 270.0), eye=eye) == ([], [])


def test_follower_side_holds_through_the_walk(lua: Any) -> None:
    """Gone for the far side, it does not flip back as it crosses the hunter's line."""
    f = Follower(lua)
    f.still(at=(0.0, 600.0), npc=(-180.0, 180.0), eye=CAM_RIGHT)
    for npc in ((-120.0, 300.0), (-36.0, 420.0), (48.0, 510.0)):
        assert f.tick(entry=START, at=(0.0, 600.0), npc=npc, eye=CAM_RIGHT) == ([], [])
