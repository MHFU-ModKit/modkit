import json
import struct

from mhfu import addresses as a
from mhfu import files
from mhfu.cli import main
from mhfu.em import census as cs
from mhfu.em import intel
from mhfu.memory import Image
from mhfu.mips import Code

BASE = 0x0010_0000
R = {"zero": 0, "a1": 5, "a2": 6, "t0": 8, "sp": 29, "ra": 31}


def addiu(rt, rs, v):
    return 0x09 << 26 | R[rs] << 21 | R[rt] << 16 | v & 0xFFFF


def jal(target):
    return 3 << 26 | (target >> 2) & 0x03FF_FFFF


def j(target):
    return 2 << 26 | (target >> 2) & 0x03FF_FFFF


NOP, RET = 0, R["ra"] << 21 | 0x08
PROLOGUE = addiu("sp", "sp", -0x10)


def va(i):
    return BASE + 4 * i


def fn(*body):
    return [PROLOGUE, *body, RET, NOP]


SPAWN = a.ATTACK_SPAWNERS
CODE = [
    *fn(jal(va(5)), NOP),  # 0 A calls B
    *fn(j(va(10)), NOP),  # 5 B tail-calls C
    *fn(jal(a.EFFECT_SPAWN), addiu("a1", "zero", 40), jal(va(17)), NOP),  # 10 C calls D
    *fn(jal(a.EFFECT_SPAWN), addiu("a1", "zero", 41)),  # 17 D, three calls from A
    # 22 E, which no one calls; its jal makes C a function start, as a j alone does not
    *fn(jal(va(10)), NOP, *(w for i in (1, 2, 3) for w in (jal(SPAWN), addiu("a2", "zero", i)))),
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
