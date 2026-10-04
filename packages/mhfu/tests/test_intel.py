# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import json
import struct

import pytest
from mhfu import addresses as a
from mhfu import files, hitzone
from mhfu.cli import main
from mhfu.em import census as cs
from mhfu.em import intel
from mhfu.memory import Image
from mhfu.mips import Code
from modkit_testing import mips as asm

BASE = 0x0010_0000
PROLOGUE = asm.addiu("sp", "sp", -0x10)


def va(i):
    return BASE + 4 * i


def fn(*body):
    return [PROLOGUE, *body, asm.RET, asm.NOP]


SPAWN = a.ATTACK_SPAWNERS
CODE = [
    *fn(asm.jal(va(5)), asm.NOP),  # 0 A calls B
    *fn(asm.j(va(10)), asm.NOP),  # 5 B tail-calls C
    *fn(asm.jal(a.EFFECT_SPAWN), asm.li("a1", 40), asm.jal(va(17)), asm.NOP),  # 10 C calls D
    *fn(asm.jal(a.EFFECT_SPAWN), asm.li("a1", 41)),  # 17 D, three calls from A
    # 22 E, which no one calls; its jal makes C a function start, as a j alone does not
    *fn(
        asm.jal(va(10)), asm.NOP, *(w for i in (1, 2, 3) for w in (asm.jal(SPAWN), asm.li("a2", i)))
    ),
]


def code():
    blob = struct.pack(f"<{len(CODE)}I", *CODE)
    return Code(Image(blob, BASE), range(BASE, BASE + 4 * len(CODE)))


def test_reach():
    graph = intel.call_graph(code())
    assert graph[va(0)] == {va(5)} and graph[va(5)] == {va(10)} and graph[va(10)] == {va(17)}
    assert intel.reachable(graph, va(0), 2) == {va(0), va(5), va(10)}


def test_credit():
    c = intel.Credit.of(code())
    assert [e["id"] for e in c.effects_of(va(0))] == [40]
    assert c.effects_of(va(0))[0] == {
        "id": 40,
        "bone": None,
        "frame": None,
        "site": "0x0010002C",  # noaddr: a synthetic site
        "via": "biased",
        "fn": "0x00100028",  # noaddr: a synthetic function
    }
    (orphan,) = c.unattributed()
    assert orphan["fn"] == intel.hex32(va(17)) and [s["id"] for s in orphan["sites"]] == [41]
    assert c.attacks_of(va(0)) == ([], 0, 0)
    assert c.attacks_of(va(22)) == ([1, 2, 3], 3, 0) and c.attacks_credited


def test_census_block(tmp_path):
    absent = intel.census(None, "no census log given")
    assert not absent["present"] and absent["reason"] == "no census log given"
    log = tmp_path / "framework.log"
    log.write_text("[state] main=0 sub=1 (a1=5) t=1 d=1\n[state] main=0 sub=2 (a1=6) t=4 d=2\n")
    c, _ = cs.load(log)
    assert c is not None
    doc = intel.census(intel.Measured(c, log), "")
    assert doc["present"] and doc["transitions"] == 2 and doc["observed_pairs"] == 1


def test_game(game):
    task = game.overlay(files.GAME_TASK)
    tigrex = game.em(75)
    tigrex_code = Code(tigrex, tigrex.text)
    parts = intel.parts(task, tigrex, 75, "game_task")
    assert parts["active_set"] in {s["va"] for s in parts["sets"]}
    assert parts["grid"]["present"] and len(parts["grid"]["columns"]) == 10
    hits = intel.attacks(tigrex, tigrex_code, 75, intel.Credit.of(tigrex_code))
    assert hits["present"] and hits["join"] == "measured"
    assert sum(t["primary"] for t in hits["tables"]) == 1
    em01 = game.em(1)
    none = intel.attacks(em01, Code(em01, em01.text), 1, intel.Credit.of(Code(em01, em01.text)))
    assert not none["present"] and none["spawner"] is None


def test_build(game, tmp_path):
    doc = intel.build(intel.Game(game), 33)
    assert doc["schema"] == intel.SCHEMA and doc["host_species"] == 33
    assert not doc["census"]["present"] and doc["census"]["reason"]
    handled = [p for p in doc["pairs"] if p["handler"]]
    assert handled and all(p["measured"] is None for p in doc["pairs"])
    assert all(p["provenance"]["entered"] == intel.ABSENT for p in doc["pairs"])
    assert {p["ends_on"] for p in handled} <= {"clip", "clip+cursor", "budget", "cursor", "unknown"}
    assert intel.summarise(doc).startswith("em33.ovl")

    log = tmp_path / "framework.log"
    states = [(0, 0, 1), (99, 0, 3), (0, 0, 9)]
    log.write_text("".join(f"[state] main={m} sub={s} (a1=1) t={t} d=0\n" for m, s, t in states))
    c, _ = cs.load(log)
    assert c is not None
    doc = intel.build(intel.Game(game), 33, intel.Measured(c, log))
    pairs = {(p["main"], p["sub"]): p for p in doc["pairs"]}
    assert (
        pairs[0, 0]["measured"]["entered"] == 1
        and pairs[0, 0]["provenance"]["entered"] == "measured"
    )
    assert pairs[0, 1]["measured"]["entered"] == 0  # looked, and never saw it
    assert "not in the overlay's" in pairs[99, 0]["note"]  # only the census knows it


def test_cli(game, tmp_path, capsys):
    data = ["--data", str(game.root)]
    assert main(["intel", "33", "--out", str(tmp_path), "-q", *data]) == 0
    doc = json.loads((tmp_path / "em33.json").read_text())
    assert doc["census"]["reason"] == "no census log given (--log)"
    assert main(["effects", "33", "--census", *data]) == 0
    assert main(["attacks", "1", "33", "--census", *data]) == 0
    assert "no_spawner" in capsys.readouterr().out


# the reader


def pair(main, sub, **over):
    d = {
        "main": main,
        "sub": sub,
        "handler": "0x00100000",  # noaddr: synthetic
        "a1": [14, 15],
        "ends_on": "clip",
        "event_frames": [],
        "budget": {"gated": False, "phase0_seeds": [], "post_hook_owns": None},
        "effects": [],
        "measured": None,
    }
    return d | over


def doc(pairs, census=None, **over):
    return {
        "schema": intel.SCHEMA,
        "host_species": 75,
        "main_states": [
            {"main": 0, "sub_states": 34, "enumerated": True},
            {"main": 5, "sub_states": None, "enumerated": False},
        ],
        "static": {"present": True},
        "census": census or {"present": False, "reason": "no [state] lines"},
        "pairs": pairs,
        "unattributed_effects": [],
    } | over


def measured(entered, dwell=0.0, a1=(), note=""):
    return {"entered": entered, "dwell_ticks": dwell, "a1": list(a1), "note": note}


CENSUS = {"present": True, "transitions": 1613}


def test_unmeasured_is_not_zero():
    si = intel.SpeciesIntel(doc([pair(4, 15)]))
    p = si.pair(4, 15)
    assert p.entered is None and not p.measured and not p.never_entered
    assert not si.has_census and si.has_static and si.pair(0, 3) is None


def test_measured_zero():
    p = intel.SpeciesIntel(doc([pair(4, 15, measured=measured(0, note="0 of 1613"))], CENSUS))
    q = p.pair(4, 15)
    assert q.entered == 0 and q.never_entered and "1613" in q.note and p.has_census


def test_notes_apart():
    si = intel.SpeciesIntel(
        doc([pair(1, 12, handler=None, note="inline", measured=measured(0, note="census"))])
    )
    p = si.pair(1, 12)
    assert (p.note, p.static_note) == ("census", "inline")


def test_static_fields():
    effects = [
        {"id": 24, "bone": 37, "frame": 56, "site": "0x10", "via": "framed", "fn": "0x20"},
        {"id": 79, "bone": 37, "frame": None},
    ]
    p = intel.PairIntel.read(
        pair(
            0,
            19,
            a1=[97],
            ends_on="budget",
            event_frames=[60.0, None],
            window_frames=[-5.0, 40.0, None],
            budget={"gated": True, "phase0_seeds": [150], "post_hook_owns": False},
            effects=effects,
        )
    )
    assert p.handler == 0x100000 and p.a1 == p.a1_static == (97,)
    assert p.fixed_event_frames == [60.0] and p.tested_frames == [-5.0, 40.0, 60.0]
    assert not p.ends_on_clip and p.budget.phase0_seeds == (150,)
    assert [str(e) for e in p.effects] == ["24@b37@f56", "79@b37"]
    assert p.a1_provenance == intel.STATIC


def test_measured_a1_wins():
    p = intel.PairIntel.read(pair(2, 8, a1=[14, 15], measured=measured(46, 23.7, [15])))
    assert p.a1 == (15,) and p.a1_static == (14, 15) and p.a1_provenance == intel.MEASURED


def test_bind():
    si = intel.SpeciesIntel(
        doc(
            [
                pair(2, 8, measured=measured(46, 23.7)),
                pair(4, 15, measured=measured(0)),
                pair(3, 6, measured=measured(120, 1.0)),
                pair(1, 12, handler=None),
            ],
            CENSUS,
        )
    )
    b = si.bindable(4, 15)
    assert not b and b.code == intel.BIND_NEVER_ENTERED and not b.overridden
    b = si.bindable(4, 15, override=True)
    assert b and b.overridden
    assert si.bindable(2, 8).code == intel.BIND_OK and not si.bindable(2, 8).unverified
    assert si.bindable(3, 6).code == intel.BIND_SHORT_DWELL and si.bindable(3, 6).unverified
    assert si.bindable(1, 12).code == intel.BIND_NO_HANDLER and si.bindable(1, 12)
    b = si.bindable(0, 99)
    assert not b and b.code == intel.BIND_NO_HANDLER and "34" in b.reason
    assert si.bindable(5, 2).code == intel.BIND_UNKNOWN_PAIR and si.bindable(5, 2).unverified


def test_bind_unmeasured():
    b = intel.SpeciesIntel(doc([pair(4, 15)])).bindable(4, 15)
    assert b and b.unverified and b.code == intel.BIND_UNMEASURED and "UNKNOWN" in b.reason


def test_parts():
    pt = intel.PartIntel.read(
        {
            "present": True,
            "active_set": "0x1000",
            "sets": [
                {
                    "va": "0x1000",
                    "kind": "hurtbox",
                    "spheres": [
                        {"bone": 2, "part": 1, "hitzone_row": 1, "radius": 97.0},
                        {"bone": 3, "part": 1, "hitzone_row": 2, "radius": 90.0},
                        {
                            "bone": 6,
                            "part": 4,
                            "hitzone_row": 5,
                            "radius": 65.0,
                            "shape": "capsule",
                            "a": [35, 0, 0],
                            "b": [330, 0, 0],
                            "flags": "0x101",
                        },
                    ],
                },
                {"va": "0x2000", "kind": "hurtbox", "spheres": [{"bone": 9, "radius": 1.0}]},
                {"va": "0x3000", "kind": "volume", "spheres": [{"bone": 10, "radius": 150.0}]},
            ],
            "grid": {
                "present": True,
                "columns": list(hitzone.COLUMNS),
                "column_provenance": {"cut": "disassembly", "thunder": "inferred: bit"},
                "states": [{"va": "0x4000", "rows": [[0, 75] + [0] * 7 + [110]] * 7}],
                "note": "the grid is SHARED",
            },
        }
    )
    assert len(pt.hurtboxes) == 2 and pt.capacity == 3 and pt.parts() == [1, 4]
    assert pt.rows_of_part(1) == [1, 2] and pt.bones_of_part(1) == [2, 3]
    cap = pt.spheres()[2]
    assert cap.is_capsule and cap.b == (330.0, 0.0, 0.0) and cap.flags == 0x101
    assert len(pt.all_spheres()) == 4
    assert pt.states[0].value(0, "cut") == 75 and pt.states[0].value(0, "ko") == 110
    assert pt.inferred_columns() == ["thunder"] and pt.grid_note
    empty = intel.SpeciesIntel({"host_species": 75}).parts
    assert not empty.present and empty.spheres() == [] and not empty.has_grid


ATTACKS = {
    "present": True,
    "spawner": "0x0010",
    "join": "measured",
    "id_offsets": {"75": 0, "76": 33},
    "tables": [
        {
            "handle": "0x10",
            "records": "0x20",
            "volume_table": "0x30",
            "primary": True,
            "sets": [
                {"index": 0, "va": "0x40", "spheres": [{"bone": 35, "radius": 180.0}]},
                {
                    "index": 1,
                    "va": "0x50",
                    "rigged": False,
                    "spheres": [{"bone": 126, "shape": "capsule", "radius": 1.0, "b": [0, 0, 1]}],
                },
                {
                    "index": 2,
                    "va": "0x60",
                    "spheres": [
                        {"bone": 10, "radius": 150.0},
                        {"bone": 125, "radius": 0.0},
                        {"bone": 43, "radius": 120.0},
                    ],
                },
            ],
            "attacks": [
                {"id": 0, "power": 0, "element": "0x00", "volume": 0, "raw": "00" * 24},
                {"id": 1, "power": 30, "element": "0x21", "volume": 0, "raw": "001e" + "00" * 22},
                {"id": 6, "power": 64, "element": "0x21", "volume": 2, "raw": "0040" + "00" * 22},
                {"id": 31, "power": 30, "element": "0x01", "volume": 2, "raw": "001e" + "00" * 22},
            ],
        },
        {"handle": "0x70", "records": "0x80", "volume_table": "0x90", "rigged": False},
    ],
}


def test_attacks():
    at = intel.AttackIntel.read(ATTACKS)
    assert at.primary is at.tables[0] and [r.id for r in at.attacks] == [1, 6, 31]
    assert at.attack(0).is_blank and at.attack(99) is None and at.attack(6).element == 0x21
    s2 = at.set(2)
    assert s2.capacity == 3 and s2.bones == [10, 43] and s2.rigged and not at.set(1).rigged
    assert at.capacity(7) is None
    assert at.sets_for([6, 31]) == [2] and [r.id for r in at.attacks_using(2)] == [6, 31]
    assert at.sets_for([0]) == []
    assert at.id_offset(81) is None and at.records_for([6], entity_species=81) == []
    assert at.records_for([1], entity_species=76) == []
    absent = intel.AttackIntel.read({"present": False, "reason": "no setter"})
    assert absent.reason == "no setter" and absent.primary is None and absent.sets == []


def test_hitting_with():
    si = intel.SpeciesIntel(
        doc(
            [pair(1, 4, attack_ids=[6, 31]), pair(3, 9, attack_ids=[1]), pair(0, 0)],
            attacks=ATTACKS,
        )
    )
    assert [(p.main, p.sub) for p in si.pairs_hitting_with(2)] == [(1, 4)]
    assert [(p.main, p.sub) for p in si.pairs_hitting_with(0)] == [(3, 9)]


def nxt(to, guards, mode=0):
    return {
        "site": "0x10",
        "kind": "enter",
        "mode": mode,
        "via": ["0x30"],
        "to": to,
        "guards": guards,
    }


CHAIN = doc(
    [
        pair(
            1,
            4,
            next=[
                nxt([[0, 3]], ["phase==3", "!collided", "budget spent"]),
                nxt([[0, 6]], ["phase==3", "collided"], 1),
            ],
        ),
        pair(0, 3, next=[nxt([[0, 1], [0, 2]], ["phase==1"])], prev=[[1, 4]]),
        pair(0, 6, next=[], prev=[[1, 4]]),
        pair(0, 1, next=[], prev=[[0, 3]]),
        pair(0, 2, next=[], prev=[[0, 3]]),
        pair(9, 9),
    ],
    chain={"hubs": [[0, 1], [0, 2]]},
)


def test_chain():
    si = intel.SpeciesIntel(CHAIN)
    assert si.has_chain and si.hubs == [(0, 1), (0, 2)]
    p = si.pair(1, 4)
    assert p.successors == [(0, 3), (0, 6)] and p.ends_itself
    e = p.next[0]
    assert e.phase == 3 and e.via == (0x30,) and e.raw_reason == "!collided & budget spent"
    assert e.reason == "!collided & run budget spent"
    assert str(e) == "(0,3)  [phase==3 & !collided & run budget spent]"
    assert si.pair(0, 6).ends_itself is False and si.pair(9, 9).ends_itself is None
    assert [(q.main, q.sub) for q in si.predecessors(0, 3)] == [(1, 4)]
    walk = [(q.main, q.sub) for q in si.chain_from(1, 4)]
    assert walk == [(1, 4), (0, 3), (0, 6), (0, 1), (0, 2)]
    assert [(q.main, q.sub) for q in si.chain_from(0, 1)] == [(0, 1)]
    assert [(q.main, q.sub) for q in si.entries()] == [(1, 4)] and si.chain_from(7, 7) == []


def test_guards():
    d = intel.describe_guard
    assert d("+0x280!=0") == "reaction pending" and d("+0x280==0") == "no reaction pending"
    assert d("+0x324==1002") == "playing a1 2" and d("+0x324!=1017") == "not playing a1 17"
    assert d("+0x414<=0") == "frame budget spent" and d("!budget spent") == "run budget left"
    assert d("+0xBE==0") == "clip done" and d("+0x637==1") == "run budget armed"
    assert d("+0x29A==99") == "section==99" and d("+0x6DB!=0") == "+0x6DB!=0"
    assert d("phase==3") == "phase==3"


def test_summary(tmp_path):
    path = tmp_path / "em75.json"
    path.write_text(json.dumps(doc([pair(0, 1, event_frames=[40.0]), pair(1, 2, handler=None)])))
    si = intel.SpeciesIntel.load(path)
    assert si.source == str(path) and len(si) == 2
    h = intel.HostSummary.of(si)
    assert (h.pairs, h.handled, h.timed, h.free_timing, h.opaque_mains) == (2, 1, 1, 1, 1)


@pytest.fixture(scope="module")
def em75(game):
    return intel.SpeciesIntel(intel.build(intel.Game(game), 75))


def test_em75_parts(em75):
    pt = em75.parts
    assert pt.active is not None and pt.capacity == 42
    assert pt.rows_of_part(6) == [5] and pt.rows_of_part(1) == [1, 2]
    assert pt.parts() == list(range(8)) and all(pt.bones_of_part(p) for p in pt.parts())
    assert {"thunder", "ko"} <= set(pt.inferred_columns()) and "cut" not in pt.inferred_columns()
    assert pt.n_states == 2 and pt.states[0].value(0, "cut") == 75 and pt.grid_note


def test_em75_charge(em75):
    at = em75.attacks
    assert at.join == "measured" and 6 in em75.pair(1, 4).attack_ids
    assert at.sets_for(em75.pair(1, 4).attack_ids) == [2]
    assert at.attack(6).power == 64 and at.set(2).capacity == 10
    assert at.set(2).bones == [2, 4, 10, 18, 34, 41, 42, 43]
    assert at.id_offsets == {75: 0, 76: 33, 88: 70}
    assert sum(not t.primary for t in at.tables) == 4
