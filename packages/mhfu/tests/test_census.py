from mhfu.em import census as cs

LOG = """\
[lua] loaded mod brute_dmg.lua
[state] main=0 sub=1 (a1=5) t=10 d=1
[brute] t=10 sec=100/100 SAME out=1 in=0 bc0=1 eng=1.0 node=0x0 hp=2400 d=500
[brute] t=11 sec=100/100 SAME out=1 in=0 bc0=1 eng=1.0 node=0x0 hp=2400 d=510
[brute] t=12 sec=101/100 out=1 in=0 bc0=1 eng=1.0 node=0x0 hp=2400 d=9000
[state] main=2 sub=2 (a1=7) t=30 d=2
[brute] t=31 sec=100/100 SAME out=2 in=0 bc0=1 eng=1.0 node=0x0 hp=2400 d=700
[state] main=0 sub=1 (a1=6) t=31 d=3
[state] main=4 sub=15 (a1=9) t=200 d=4 FORCED
[state] main=0 sub=1 (a1=5) t=201 d=5
"""


def test_parse():
    c = cs.parse(LOG.splitlines())
    assert c.transitions == 5
    assert c.dwell == {(0, 1): [20], (2, 2): [1], (4, 15): [1]}  # 31 -> 200 is a gap
    assert c.anims[0, 1] == {5}
    assert c.moved == {(0, 1): [10]}  # t=12 is cross-section


def test_measured():
    c = cs.parse(LOG.splitlines())
    held = cs.measured(c, (0, 1))
    assert (held["entered"], held["dwell_ticks"], held["move_per_tick"]) == (1, 20.0, 10.0)
    assert "note" not in held and cs.verdict(c, (0, 1)) == "holds, still"
    short = cs.measured(c, (2, 2))
    assert short["move_per_tick"] is None and "unmeasured" in short["note"]
    assert cs.verdict(c, (2, 2)).startswith("short")
    never = cs.measured(c, (9, 9))
    assert never["entered"] == 0 and never["a1"] == [] and "0 of 5" in never["note"]
    assert cs.verdict(c, (9, 9)) == "never entered"


def test_load(tmp_path):
    c, why = cs.load(tmp_path / "missing.log")
    assert c is None and "no log" in why
    empty = tmp_path / "framework.log"
    empty.write_text("[lua] nothing here\n")
    c, why = cs.load(empty)
    assert c is None and "0 [state] line(s)" in why
    empty.write_text(LOG)
    c, why = cs.load(empty, since=LOG.index("[state] main=4"))
    assert c is not None and why == "" and c.dwell == {(4, 15): [1]}
