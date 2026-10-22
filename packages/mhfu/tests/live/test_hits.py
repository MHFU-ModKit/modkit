# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import json
import struct

import pytest
from mhfu import addresses as a
from mhfu.cli import main
from mhfu.entries import ACTION_INPUT_BASE
from mhfu.live import Session, hits, rig
from mhfu.structs import BigMonster
from ppsspp_debug import Hit

ENT = a.RAM.start + 0x90_0000  # the monster, in `fake`
PLAYER = int(a.PLAYER_ENTITY)
SPAWNER = int(a.TIGREX_ATTACK_SPAWN)
STATE, SETTER, HP = int(a.SET_AI_STATE), int(a.ATTACK_TABLE_SETTER), int(a.APPLY_HP_DELTA)
PRIMARY, OTHER, SHARED = 0x0900_1000, 0x0900_2000, 0x0900_3000  # noaddr: synthetic tables
TAGS = {PRIMARY: "", OTHER: "@t1"}
AREA = 109
START = 5_000_000


def bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def line(address: int, t: float, pair, args, entry=46, dist=300.0) -> Hit:
    """A line in `log_format` `t` seconds into the window, the monster `dist` west of the hunter."""
    words = [
        (START + round(t * 1e6)) % hits.WRAP,
        0x0900_0000,  # noaddr: $ra, not read
        pair[0] | pair[1] << 8,
        *args,
        ACTION_INPUT_BASE + entry,
        bits(1000.0),
        bits(2000.0),
        bits(1000.0 + dist),
        bits(2000.0),
    ]
    return Hit("exec", address, address, False, message=" ".join(f"{w:08x}" for w in words))


def state(t, pair, to, **kw):
    return line(STATE, t, pair, (ENT, to[0], to[1], 0), **kw)


def spawn(t, pair, attack, **kw):
    return line(SPAWNER, t, pair, (0, ENT, attack, 0), **kw)


def node(t, pair, attack, table, **kw):
    return line(SETTER, t, pair, (0x0900_9000, ENT, attack, table), **kw)  # noaddr: a node


def damage(t, pair, delta, **kw):
    return line(HP, t, pair, (PLAYER, delta & 0xFFFF_FFFF, 0, 0), **kw)


KINDS = {STATE: hits.STATE, SPAWNER: hits.SPAWN, SETTER: hits.NODE, HP: hits.HP}


def parsed(*raw):
    events = [hits.parse(h, START, ENT, KINDS) for h in raw]
    assert None not in events
    return events


def test_parse():
    ev = hits.parse(state(1.5, (0, 2), (1, 4), dist=400.0), START, ENT, KINDS)
    assert ev == hits.Event(hits.STATE, 1.5, (0, 2), 46, 400.0, to=(1, 4))
    assert hits.parse(spawn(2, (1, 4), 6), START, ENT, KINDS).id == 6
    n = hits.parse(node(2, (1, 4), 6, OTHER), START, ENT, KINDS)
    assert (n.id, n.table, n.pair) == (6, OTHER, (1, 4))
    assert hits.parse(damage(3, (1, 4), -77), START, ENT, KINDS).delta == -77
    assert hits.parse(damage(3, (1, 4), 20), START, ENT, KINDS).delta == 20
    assert hits.parse(spawn(2, (1, 4), 6, entry=-1000), START, ENT, KINDS).entry is None


def test_parse_wraps_the_clock():
    h = state(0.5, (0, 2), (1, 4))
    assert hits.parse(h, START - 2_000_000 + hits.WRAP, ENT, KINDS).t == pytest.approx(2.5)


def test_parse_skips_what_is_not_ours():
    own = line(HP, 1, (0, 2), (ENT, 0xFFFF_FFB3, 0, 0))  # the monster's own damage
    elsewhere = line(SPAWNER, 1, (0, 2), (0, ENT + 4, 6, 0))
    for bad in (own, elsewhere, Hit("exec", HP, HP, False, message="garbage")):
        assert hits.parse(bad, START, ENT, KINDS) is None
    assert (
        hits.parse(
            Hit("exec", 1, 1, False, message=state(1, (0, 2), (1, 4)).message), 0, ENT, KINDS
        )
        is None
    )


def window():
    """Two pair changes, a charge that hit within its node's window, a far spawn, a trip, a hit
    with no node near and a heal."""
    return parsed(
        state(1.0, (0, 2), (1, 4)),
        spawn(1.5, (1, 4), 6, dist=600),
        node(1.5, (1, 4), 6, PRIMARY, dist=600),
        damage(2.0, (1, 4), -77, dist=100),
        state(3.0, (1, 4), (0, 3)),
        spawn(3.5, (0, 3), 2, dist=900),
        node(3.5, (0, 3), 2, OTHER, dist=900),
        node(3.6, (0, 3), 5, SHARED, dist=900),
        damage(8.0, (0, 3), -9, dist=50),
        damage(9.0, (0, 3), -40, dist=50),
        damage(9.5, (0, 3), 20, dist=50),
    )


def tag(handle):
    return {**TAGS, SHARED: "@shared"}.get(handle, "?")


def test_census_accounts_per_pair():
    c = hits.census(window(), 10.0, tag=tag)
    assert sorted(c.pairs) == [(0, 2), (0, 3), (1, 4)]
    charge = c.pairs[1, 4]
    assert (charge.entries, charge.dwell_s) == (1, 2.0)
    assert (charge.spawns, charge.spawn_in_reach, dict(charge.spawn_ids)) == (1, 1, {6: 1})
    assert (charge.nodes, charge.node_in_reach, dict(charge.node_ids)) == (1, 1, {"6": 1})
    assert (charge.hits, charge.hits_in_reach, charge.damage) == (1, 1, 77)
    wait = c.pairs[0, 3]
    assert (wait.entries, wait.dwell_s) == (1, 7.0)
    assert (wait.spawns, wait.spawn_in_reach) == (1, 0)
    assert dict(wait.node_ids) == {"2@t1": 1, "5@shared": 1} and wait.node_in_reach == 0
    assert (wait.hits, wait.damage, wait.deltas) == (2, 49, [-9, -40])
    assert c.pairs[0, 2].dwell_s == 1.0 and c.pairs[0, 2].entries == 0  # held from the start
    assert dict(c.trips) == {(0, 3): 1}
    assert [(e.t, e.delta) for e in c.loose] == [(9.0, -40)]
    assert dict(c.fired) == {"state": 2, "spawn": 2, "node": 3, "hp": 4}
    assert (c.total("hits"), c.total("damage"), c.total("entries")) == (3, 126, 2)
    assert c.warnings == []


def test_reach_is_the_callers():
    c = hits.census(window(), 10.0, reach=1000.0, tag=tag)
    assert c.pairs[0, 3].spawn_in_reach == 1 and c.reach == 1000.0
    narrow = hits.census(window(), 10.0, reach=500.0, tag=tag)
    assert narrow.pairs[1, 4].spawn_in_reach == 0 and narrow.pairs[1, 4].hits_in_reach == 1


def test_a_hit_belongs_to_the_pair_that_built_the_node():
    # the move ended 1 s before the hunter was hit: the node outlived it
    c = hits.census(
        parsed(
            node(1.0, (3, 3), 1, PRIMARY),
            state(1.5, (3, 3), (0, 1)),
            damage(2.5, (0, 1), -50),
            damage(5.0, (0, 1), -50),
        ),
        6.0,
        tag=tag,
    )
    assert c.pairs[3, 3].damage == 50 and c.pairs[0, 1].damage == 50
    assert [e.t for e in c.loose] == [5.0]


def test_ids_fall_back_to_the_spawner():
    c = hits.census(parsed(spawn(1, (1, 4), 6), spawn(2, (1, 4), 6)), 3.0)
    assert dict(c.pairs[1, 4].ids) == {"6": 2}


def test_no_events_is_an_empty_census():
    c = hits.census([], 5.0)
    assert c.pairs == {} and c.total("hits") == 0
    assert [w.split(" ")[1] for w in c.warnings] == ["state", "spawn", "node", "hp"]


def test_warnings_name_each_breakpoint_that_logged_nothing():
    only_state = hits.census(parsed(state(1, (0, 2), (1, 4))), 5.0)
    assert [w.split(" ")[1] for w in only_state.warnings] == ["spawn", "node", "hp"]
    assert "no attack spawned in 5 s, or it did not take" in only_state.warnings[0]
    nodes = hits.census(parsed(state(1, (0, 2), (1, 4)), node(2, (1, 4), 6, PRIMARY)), 5.0)
    assert any("nodes were built but the spawner logged nothing" in w for w in nodes.warnings)
    spawns = hits.census(parsed(state(1, (0, 2), (1, 4)), spawn(2, (1, 4), 6, dist=10)), 5.0)
    assert any("no node was built" in w for w in spawns.warnings)
    assert any("1 spawn(s) in reach and not one HP change" in w for w in spawns.warnings)


def test_report():
    c = hits.census(window(), 10.0, tag=tag)
    lines = hits.report(c).splitlines()
    assert lines[0].split() == [
        "pair",
        "entries",
        "dwell",
        "spawns",
        "in",
        "reach",
        "hits",
        "in",
        "reach",
        "damage",
        "ids",
    ]
    assert lines[1].split() == ["(0,2)", "0", "1.0s", "0", "0", "0", "0", "0", "-"]
    assert lines[3].split() == ["(1,4)", "1", "2.0s", "1", "1", "1", "1", "77", "6x1"]
    assert lines[2].split()[:9] == ["(0,3)", "1", "7.0s", "1", "0", "2", "2", "49", "2@t1x1"]
    assert lines[4].startswith("10 s (time), reach 800: 2 entries, 2 spawns (1 in reach, median")
    assert lines[4].endswith("3 hits for 126, 1 trip(s)")
    assert lines[5] == "lines: state 2, spawn 2, node 3, hp 4; 0 skipped, 0 dropped"
    assert len(lines) == 6
    assert hits.report(hits.census([], 5.0)).splitlines()[-1].startswith("! the hp breakpoint")


def test_json_round():
    c = hits.census(window(), 10.0, tag=tag)
    c.skipped = 2
    doc = json.loads(json.dumps(c.to_json()))
    assert doc["pairs"]["1,4"]["damage"] == 77 and doc["pairs"]["0,3"]["hit_deltas"] == [-40, -9]
    assert doc["pairs"]["0,3"]["node_ids"] == {"2@t1": 1, "5@shared": 1}
    assert doc["fired"] == {"state": 2, "spawn": 2, "node": 3, "hp": 4}
    assert doc["trips"] == {"0,3": 1} and doc["skipped"] == 2 and doc["warnings"] == []
    assert doc["events"][0] == {
        "k": "state",
        "t": 1.0,
        "pair": [0, 2],
        "entry": 46,
        "dist": 300.0,
        "to": [1, 4],
    }
    assert doc["loose_hits"][0]["delta"] == -40


def test_target_for_em75_needs_no_game():
    t = hits.target(int(a.TIGREX_VTABLE))
    assert t.spawner == SPAWNER and t.species == 75
    assert t.kinds == {STATE: "state", SPAWNER: "spawn", SETTER: "node", HP: "hp"}
    assert t.tag(PRIMARY) == "@09001000"  # noaddr: a synthetic handle
    with pytest.raises(hits.NoSpawner, match="not em75's.*--data"):
        hits.target(int(a.POPO_VTABLE))


def test_target_from_the_game(game):
    from mhfu.em.abi import Engine

    engine = Engine.load(game)
    t = hits.target(engine.owners[75].vtable.va, game)
    assert t.spawner == SPAWNER and t.species == 75
    assert sorted(set(t.tags.values())) == ["", "@t1", "@t2", "@t3", "@t4"]
    assert t.tag(0x09D6_1250) == "" and t.tag(0x09D6_1780) == "@t2"  # noaddr: em75's tables
    seven = hits.target(engine.owners[7].vtable.va, game)
    assert seven.species == 7 and seven.spawner != SPAWNER
    for species in (1, 33):
        with pytest.raises(hits.NoSpawner, match=f"em{species:02d} has no attack spawner"):
            hits.target(engine.owners[species].vtable.va, game)
    with pytest.raises(hits.NoSpawner, match="no species overlay owns"):
        hits.target(int(a.POPO_VTABLE), game)


# --- a window against a fake game ---


def emulated_clock(fake, script):
    """`cpu.evaluate` answers usec in steps of 0.25 s from START and plays `script(n)` on each
    call (as test_observe's)."""
    calls = [0]

    async def evaluate(ws, msg):
        n = calls[0] = calls[0] + 1
        await script(n)
        await fake._reply(ws, msg, uintValue=START + 250_000 * (n - 1), floatValue="0")

    fake._table["cpu.evaluate"] = evaluate


async def bkp(fake, address, h):
    await fake.log(f"BKP PC={address:08x}: {h.message}")


@pytest.fixture
def quest(fake):
    """A Tigrex in the registry, the hunter in AREA with HP, and PPSSPP's own settings."""
    registry = [0] * 21
    registry[2] = ENT
    fake.poke("21I", a.ENTITY_REGISTRY, *registry)
    fake.poke("I", ENT + a.ENTITY.VTABLE, a.TIGREX_VTABLE)
    fake.poke("I", PLAYER + a.ENTITY.VTABLE, a.PLAYER_QUEST_VTABLE)
    fake.poke("H", a.AREA_INDEX, AREA)
    fake.poke("H", PLAYER + a.ENTITY.HP, 90)
    fake.poke("H", ENT + a.ENTITY.SECTION, AREA)
    return fake


def test_run_arms_four_breakpoints_and_counts_what_they_log(s, quest, monkeypatch):
    monkeypatch.setattr(hits, "DRAIN", 0.02)
    monster = BigMonster(s.mem, ENT)
    seen = {}

    async def script(n):
        if n == 2:
            seen.update({addr: bp for addr, bp in quest.breakpoints.items()})
            await bkp(quest, STATE, state(0.3, (0, 2), (1, 4)))
            await bkp(quest, SPAWNER, spawn(0.4, (1, 4), 6, dist=500))
            await bkp(quest, SETTER, node(0.4, (1, 4), 6, PRIMARY, dist=500))
            await bkp(quest, HP, damage(0.5, (1, 4), -77, dist=100))
            await bkp(quest, HP, line(HP, 0.5, (1, 4), (ENT, 0xFFFF_FFB3, 0, 0)))  # its own
            await quest.log(f"BKP PC={SPAWNER:08x}: garbage")

    emulated_clock(quest, script)
    c = hits.run(s, monster, hits.Target(SPAWNER, 75, TAGS), 2.0)
    assert sorted(seen) == sorted([STATE, SPAWNER, SETTER, HP])
    condition = f"a0 == {ENT:#x} || a1 == {ENT:#x} || a0 == {PLAYER:#x}"
    assert {bp["condition"] for bp in seen.values()} == {condition}
    assert not any(bp["enabled"] for bp in seen.values())
    assert not quest.breakpoints
    assert c.ended == "time" and c.seconds == pytest.approx(2.0, abs=0.3)
    assert c.pairs[1, 4].damage == 77 and c.pairs[1, 4].spawn_in_reach == 1
    assert dict(c.pairs[1, 4].node_ids) == {"6": 1}
    assert c.skipped == 2 and c.dropped == 0
    assert c.warnings == []


def test_run_ends_when_the_keeper_says(s, quest, monkeypatch):
    monkeypatch.setattr(hits, "DRAIN", 0.02)
    emulated_clock(quest, lambda n: _nothing())
    asked = []

    def keep():
        asked.append(1)
        return "dead" if len(asked) == 2 else "ok"

    c = hits.run(s, BigMonster(s.mem, ENT), hits.Target(SPAWNER), 30.0, keep=keep)
    assert c.ended == "dead" and len(asked) == 2 and c.seconds < 4.0
    assert not quest.breakpoints


async def _nothing():
    return None


def test_run_refuses_a_breakpoint_that_did_not_arm(s, quest, monkeypatch):
    monkeypatch.setattr(hits, "DRAIN", 0.02)

    async def only_one(ws, msg):
        armed = {"address": STATE, "enabled": False, "log": True, "condition": None}
        await quest._reply(ws, msg, breakpoints=[{**armed, "logFormat": None, "symbol": None}])

    quest._table["cpu.breakpoint.list"] = only_one
    emulated_clock(quest, lambda n: _nothing())
    with pytest.raises(RuntimeError, match="breakpoints not armed"):
        hits.run(s, BigMonster(s.mem, ENT), hits.Target(SPAWNER), 1.0)
    assert not quest.breakpoints


# --- the keeper ---


def keeper(s, quest, summoned, **kw):
    return hits.Keeper(s, BigMonster(s.mem, ENT), lambda: summoned.append(1), **kw)


def test_keeper_ends_the_window_when_the_hunter_dies_or_leaves(s, quest):
    k = keeper(s, quest, [])
    assert k() == "ok"
    quest.poke("H", PLAYER + a.ENTITY.HP, 0)
    assert [k(), k(), k()] == ["ok", "ok", "dead"]
    quest.poke("H", PLAYER + a.ENTITY.HP, 10)
    assert k() == "ok"  # three in a row, not three in all
    quest.poke("H", a.AREA_INDEX, AREA + 1)
    assert k() == "left"


def test_keeper_leaves_the_monster_alone_without_engage(s, quest):
    summoned = []
    quest.poke("H", ENT + a.ENTITY.SECTION, AREA + 1)
    assert keeper(s, quest, summoned)() == "ok" and summoned == []


def test_keeper_brings_it_back_when_far_out_of_section_or_idle(s, quest):
    summoned = []
    k = keeper(s, quest, summoned, engage=True)
    quest.poke("3f", ENT + a.ENTITY.POSITION, 400.0, 0.0, 0.0)
    assert k() == "ok" and summoned == []  # near, out of combat: not yet
    quest.poke("3f", ENT + a.ENTITY.POSITION, 3000.0, 0.0, 0.0)
    quest.poke("B", ENT + a.ENTITY.COMBAT_MODE, 1)
    k()
    assert len(summoned) == 1 and k.summons == 1  # in combat and far
    quest.poke("3f", ENT + a.ENTITY.POSITION, 400.0, 0.0, 0.0)
    k()
    assert len(summoned) == 1  # in combat and near
    quest.poke("H", ENT + a.ENTITY.SECTION, AREA + 1)
    k()
    assert len(summoned) == 2  # out of the section
    quest.poke("H", ENT + a.ENTITY.SECTION, AREA)
    quest.poke("B", ENT + a.ENTITY.COMBAT_MODE, 0)
    for _ in range(hits.Keeper.IDLE - 1):
        k()
    assert len(summoned) == 2
    k()
    assert len(summoned) == 3  # out of combat for IDLE looks


# --- the command ---


@pytest.fixture
def cli(quest, clock, monkeypatch, tmp_path):
    monkeypatch.setattr(hits, "DRAIN", 0.02)
    monkeypatch.setattr(rig, "running", lambda launcher: True)
    monkeypatch.setattr(Session, "launch", lambda *a, **kw: Session.attach(quest.port, timeout=5))
    monkeypatch.delenv("MHFU_DATA", raising=False)

    async def script(n):
        if n == 3:
            await bkp(quest, STATE, state(0.3, (0, 2), (1, 4)))
            await bkp(quest, SPAWNER, spawn(0.4, (1, 4), 6, dist=500))
            await bkp(quest, SETTER, node(0.4, (1, 4), 6, PRIMARY, dist=500))
            await bkp(quest, HP, damage(0.5, (1, 4), -77, dist=100))

    emulated_clock(quest, script)
    return lambda *argv: main(["hits", "--lane", "1", "--seconds", "1.5", *argv])


def test_cli(cli, quest, capsys, tmp_path):
    out = tmp_path / "hits.json"
    assert cli("--json", str(out), "--hp", "0") == 0
    text = capsys.readouterr()
    assert "monster slot 2 at" in text.err
    lines = text.out.splitlines()
    assert lines[0].split()[:2] == ["pair", "entries"]
    charge = next(row.split() for row in lines if row.startswith("(1,4)"))
    assert charge[:2] == ["(1,4)", "1"] and charge[-2:] == [
        "77",
        "6@09001000x1",
    ]  # no game: a handle
    assert "1 spawns (1 in reach, median spawn distance 500)" in lines[-2]
    doc = json.loads(out.read_text())
    assert doc["pairs"]["1,4"]["damage"] == 77 and doc["ended"] == "time"
    assert not quest.breakpoints


def test_cli_holds_the_hunters_hp(cli, quest):
    assert cli("--hp", "500") == 0
    assert quest.peek("2H", PLAYER + a.ENTITY.HP_CAP) == (500, 500)
    assert quest.peek("H", PLAYER + a.ENTITY.MAX_HP) == (500,)


def test_cli_needs_a_game_that_runs(cli, monkeypatch, capsys):
    monkeypatch.setattr(rig, "running", lambda launcher: False)
    assert cli() == 1
    assert "mhfu hits: no game running in lane 1" in capsys.readouterr().err


def test_cli_says_why_there_is_no_spawner(cli, quest, capsys):
    quest.poke("I", ENT + a.ENTITY.VTABLE, a.POPO_VTABLE)
    assert cli() == 1
    assert "mhfu hits:" in capsys.readouterr().err and not quest.breakpoints


def test_cli_exit_status_says_the_window_was_cut(cli, quest, capsys):
    quest.poke("H", PLAYER + a.ENTITY.HP, 0)
    emulated_clock(quest, lambda n: _nothing())
    assert cli("--hp", "0", "--seconds", "30") == 1
    assert "(dead)" in capsys.readouterr().out
