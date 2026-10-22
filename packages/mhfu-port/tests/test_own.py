# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from mhfu import hitbox
from mhfu.files import em_overlay
from mhfu_port import manifest, moves, p3hits, sequence
from mhfu_port.brain import own, spare
from mhfu_port.manifest import AttackWindow, Steer
from mhfu_port.p3hits import Hit, Length, Note, Plan, Step, Turn

PORTS = Path(__file__).parents[3] / "ports"
TEXT = """
schema = 2
[port]
name = "z"
host_species = 75
pac = "z.bin"
[source]
model = 5248
[clips.charge]
source = 61
[brain]
donor = 23
splice = "base_3"
entry = "base_12"
[brain.pairs]
"7,13" = "own"
"""
LENGTHS = {61: Length(382.0), 59: Length(144.0, True), 60: Length(252.0)}
HOSTS = {21: 7, 28: 8, 19: 15, 20: 7}


def sphere(at: float, radius: float) -> own.Prim:
    p = np.array([at, 0.0, 0.0])
    return p, p, radius


def test_overlap():
    assert own.overlap([sphere(0, 100)], [sphere(0, 100)]) == pytest.approx(1.0)
    assert own.overlap([sphere(0, 100)], [sphere(500, 100)]) == 0.0
    assert own.overlap([sphere(0, 100)], [sphere(0, 200)]) == pytest.approx(0.125, abs=0.01)
    assert own.overlap([], [sphere(0, 1)]) == 0.0


class Fake:
    """A walk that plans what it is given, and a matcher with fixed answers."""

    def __init__(self, plan: Plan, hosts=HOSTS, active=10) -> None:
        self.lengths = LENGTHS
        self.ms = SimpleNamespace(pairs={plan.pair: object()})
        self.hosts, self.active, self._plan = hosts, active, plan
        self.hits = SimpleNamespace(records=[])
        self.table = None  # no spare records to hand out
        self.binding = SimpleNamespace(joint_of={})
        self.bind = np.zeros((0, 3))

    def plan(self, pair: object) -> Plan:
        return self._plan

    def match(self, record: int) -> own.Match:
        host = self.hosts.get(record)
        return own.Match(record, host, 0.5 if host is not None else 0.1, False, 4)

    def attack(self, host: int) -> object:
        return SimpleNamespace(unknown_01=self.active, power=50)


def derive(plan: Plan, **kw):
    m = manifest.loads(TEXT)
    found: list = []
    fake = Fake(plan, **kw)
    d = own.Derivation(m, fake, lambda: fake, found.append)  # type: ignore[arg-type]
    d.run(plan.pair)
    d.finish()
    return m, found


def step(clip: int, hits=(), turns=(), ended="clip", ticks=10, start=0.0, stop=None) -> Step:
    return Step(clip, 4, start, ended, ticks, tuple(hits), tuple(turns), stop)


def plan(*steps: Step, **kw) -> Plan:
    return Plan((7, 13), steps, ((0, 0),), True, **kw)


def codes(found) -> list[str]:
    return [f.code for f in found]


def test_a_step_is_a_move():
    hits = [Hit(1, 21, 16, 135.0, 165.0), Hit(0, 28, 8, 138.4, 148.0)]
    p = plan(step(61, hits, [Turn(546, (10.0, 40.0))], ticks=191))
    m, found = derive(p)
    mv = m.moves["p3_7_13"]
    assert mv.own and mv.clip == "charge" and mv.length is None and mv.after is None
    assert mv.attacks == [
        AttackWindow(7, 135, 165, label="donor 21"),
        AttackWindow(8, 138, 148, label="donor 28"),
    ]
    assert mv.steer == Steer(turn="hunter", rate=546, walls=False, window=(10, 40))
    assert "p3_61" not in m.clips and codes(found) == ["P3_OWN_ACTIVE", "P3_OWN_GROUP"]
    manifest.check(m)


def test_a_chain_hands_on_and_a_loop_is_cut():
    carried = Hit(0, 19, 3, 0.0, 14.0, carried=True)
    p = plan(
        step(59, [Hit(0, 19, 3, 85.0, None)], ticks=72),
        step(60, [carried, Hit(1, 20, 16, 94.0, 124.0)], ticks=126),
    )
    m, found = derive(p)
    first, second = m.moves["p3_7_13"], m.moves["p3_7_13_2"]
    assert (first.clip, first.length, first.after) == ("p3_59", 72, "p3_7_13_2")
    assert first.attacks == [AttackWindow(15, 85, 144, label="donor 19")]
    assert (second.clip, second.length, second.after) == ("p3_60", None, None)
    assert [(a.id, a.frame, a.end) for a in second.attacks] == [(15, 0, 14), (7, 94, 124)]
    assert sequence.chain(m, "p3_7_13") == ["p3_7_13", "p3_7_13_2"]
    assert "P3_OWN_SPLIT" in codes(found)
    assert set(m.clips) == {"charge", "p3_59", "p3_60"}


def spans(m: manifest.Manifest, name: str = "p3_7_13") -> tuple[int, int, int | None]:
    mv = m.moves[name]
    return mv.start, mv.stop, mv.length


def test_the_frame_a_test_ended_a_step_at_is_its_stop():
    hits = [Hit(1, 21, 16, 56.0, 200.0), Hit(0, 28, 8, 10.0, 20.0)]
    m, _ = derive(plan(step(61, hits, ended="cursor", ticks=31, start=24.0, stop=86.0)))
    assert spans(m) == (24, 86, None)
    # clamped to [start, stop]; the one over before the start is dropped
    assert [(a.id, a.frame, a.end) for a in m.moves["p3_7_13"].attacks] == [(7, 56, 86)]
    manifest.check(m)


def test_a_loop_stops_where_the_donor_left_it():
    p = plan(step(59, [Hit(0, 19, 3, 68.0, None)], ended="cursor", ticks=57, stop=114.0))
    m, _ = derive(p)
    assert spans(m) == (0, 114, None) and m.moves["p3_7_13"].attacks[0].end == 114


def test_a_start_is_carried_and_the_clip_plays_on_to_its_end():
    m, found = derive(plan(step(61, ended="clip", ticks=100, start=18.0)))
    assert spans(m) == (18, 0, None) and "P3_OWN_START" not in codes(found)


@pytest.mark.parametrize(
    ("ended", "stop"), [("cursor", None), ("pair", None), ("cursor", 500.0), ("cursor", 24.0)]
)
def test_a_step_with_no_frame_to_stop_on_keeps_its_length(ended, stop):
    """No frame, or one outside the clip or not after the start."""
    m, _ = derive(plan(step(61, ended=ended, ticks=55, start=24.0, stop=stop)))
    assert spans(m) == (24, 0, 55)


def test_an_attack_with_no_host_record_is_left_out():
    p = plan(step(61, [Hit(0, 26, 3, 85.0, 95.0), Hit(1, 21, 0, 85.0, 90.0)]))
    m, found = derive(p)
    assert [a.id for a in m.moves["p3_7_13"].attacks] == [7]
    (f,) = [f for f in found if f.code == "P3_OWN_ATTACK"]
    assert f.level == "warning" and "26" in f.text and f.item == "p3_7_13"
    assert "no spare (the host has no attack table)" in f.text


def test_the_move_player_holds_four_attacks():
    hits = [Hit(i % 2, 21, 0, 10.0 * i, 10.0 * i + 5) for i in range(6)]
    m, found = derive(plan(step(61, hits)))
    assert len(m.moves["p3_7_13"].attacks) == 4 and "P3_OWN_ATTACK" in codes(found)


def test_a_window_past_the_hosts_active_frames():
    _, found = derive(plan(step(61, [Hit(0, 21, 0, 100.0, 140.0)])), active=10)
    assert "P3_OWN_ACTIVE" in codes(found)
    _, found = derive(plan(step(61, [Hit(0, 21, 0, 100.0, 105.0)])), active=10)
    assert "P3_OWN_ACTIVE" not in codes(found)


def test_notes_become_findings():
    notes = (
        Note("motion", "by code"),
        Note("branch", "the branch at 0x1 is taken"),
        Note("unresolved", "a clip is computed"),
    )
    _, found = derive(Plan((7, 13), (step(61),), ((2, 5),), False, notes, ((59, 60),)))
    assert codes(found) == [
        "P3_OWN_MOTION",
        "P3_OWN_BRANCH",
        "P3_OWN_PARTIAL",
        "P3_OWN_BRANCH",
        "P3_OWN_PARTIAL",
    ]
    _, found = derive(Plan((7, 13), (step(61),), ((2, 5),), True))
    assert codes(found) == ["P3_OWN_NEXT"]


def test_a_pair_with_no_clips_is_an_error():
    m, found = derive(Plan((7, 13), (), (), False))
    assert "p3_7_13" not in m.moves and found[-1].level == "error"
    assert found[-1].code == "P3_OWN_NOCLIPS"


def test_the_manifests_own_move_wins():
    m = manifest.loads(TEXT + '[moves.p3_7_13]\nclip = "charge"\n')
    assert own.expand(m, None)[0] is m  # type: ignore[arg-type]  # nothing to derive: no data read


def test_pairs():
    m = manifest.loads(TEXT)
    assert m.brain is not None and own.pairs(m.brain) == [(7, 13)]


def test_pairs_sees_own_in_a_list():
    m = manifest.loads(TEXT + '"7,14" = [[1, 2], "own"]\n"7,15" = [[1, 2], [1, 3]]\n')
    assert m.brain is not None and own.pairs(m.brain) == [(7, 13), (7, 14)]


# --- with the games ---


def brute() -> manifest.Manifest:
    m = manifest.load(PORTS / "brute_tigrex.toml")
    assert m.brain is not None
    for key in ("7,13", "7,14", "7,15", "1,15", "2,19", "2,20", "2,23"):
        m.brain.pairs[key] = manifest.OWN
    return m


@pytest.fixture(scope="module")
def derived(data):
    return own.expand(brute(), data)


def windows(m: manifest.Manifest, name: str) -> list[tuple[int, int, int | None]]:
    return [(a.id, a.frame, a.end) for a in m.moves[name].attacks]


def test_brute_derives_its_seven_pairs(derived):
    m, found = derived
    assert not [f for f in found if f.level == "error"]
    manifest.check(m)
    own_moves = sorted(n for n, mv in m.moves.items() if mv.own and n.startswith("p3_"))
    assert own_moves == [
        "p3_1_15",
        "p3_2_19",
        "p3_2_19_2",
        "p3_2_19_3",
        "p3_2_20",
        "p3_2_23",
        "p3_7_13",
        "p3_7_14",
        "p3_7_14_2",
        "p3_7_15",
    ]
    assert sequence.chain(m, "p3_7_14") == ["p3_7_14", "p3_7_14_2"]


def test_brute_windows_lie_inside_their_spans(derived, data):
    m, _ = derived
    made = {n: mv for n, mv in m.moves.items() if n.startswith("p3_")}
    assert {n: (mv.start, mv.stop, mv.length) for n, mv in made.items() if mv.start or mv.stop} == {
        "p3_2_19_3": (6, 0, None)
    }
    assert {n: mv.length for n, mv in made.items() if mv.length is not None} == {
        "p3_2_19_2": 39,
        "p3_7_14": 72,
    }
    for name, mv in made.items():
        for a in mv.attacks:
            assert mv.start <= a.frame < a.end <= (mv.stop or 10_000), name


def test_brute_records_match_by_volume(data):
    m = brute()
    hits = p3hits.Walk.of(data.p3rd, 23, data.p3rd.read(m.source.anim)).hits
    matcher = own.Matcher(m, data, hits)
    got = {r: matcher.match(r) for r in (21, 28, 20, 27, 22, 19, 26)}
    assert {r: x.host for r, x in got.items()} == {
        21: 7,
        28: 8,
        20: 7,
        27: 8,
        22: 4,
        19: 15,
        26: None,
    }
    assert got[26].best == 25 and got[26].overlap == pytest.approx(0.12, abs=0.02)
    assert got[21].overlap == pytest.approx(0.49, abs=0.03) and not got[21].given


def test_brute_charge(derived):
    m, _ = derived
    charge = m.moves["p3_7_13"]
    assert charge.clip == "charge" and windows(m, "p3_7_13") == [(7, 135, 165), (8, 138, 148)]
    assert charge.steer == Steer(turn="hunter", rate=546, walls=False, window=(10, 40))


def test_brute_rush_and_leap(derived):
    m, found = derived
    feet = m.attacks[0].id  # donor record 26 plays its spare record
    assert [m.moves[n].clip for n in ("p3_7_14", "p3_7_14_2")] == ["p3_59", "p3_60"]
    assert windows(m, "p3_7_14") == [(15, 85, 144), (feet, 85, 144)]
    assert windows(m, "p3_7_14_2") == [(15, 0, 14), (feet, 0, 14), (7, 94, 124), (8, 96, 106)]
    assert m.moves["p3_7_14"].steer.rate == 364 and m.moves["p3_7_14"].length == 72
    assert m.moves["p3_7_14"].steer.walls and not m.moves["p3_7_14_2"].steer.walls
    assert windows(m, "p3_7_15") == [(4, 75, 100)]
    assert m.moves["p3_7_15"].steer == Steer(turn="hunter", rate=273, walls=False, window=(0, 20))
    assert not [f for f in found if f.code == "P3_OWN_ATTACK"]
    assert {f.item for f in found if f.code == "P3_OWN_BRANCH"} >= {"p3_7_14"}


def host_table(data):
    return hitbox.primary_table(hitbox.tables(data.fu.overlay(em_overlay(75))))


def test_brute_hind_feet_get_a_spare_record(derived, data):
    m, found = derived
    (a,) = m.attacks
    assert a.id in range(34, 107) and a.id != 70 and a.id not in spare.RULED[75]
    assert (a.kind, a.label) == (2, "donor 26") and a.volume is not None and a.volume >= 18
    feet = [h for h in m.hitboxes if h.set == a.volume]
    assert sorted(h.bone for h in feet) == [24, 29], "the donor's two volumes, on the port's joints"
    assert {(h.radius, h.shape) for h in feet} == {(130.0, "sphere")}
    # power 25 scaled by the median of 1.0, 0.8, 0.67 and 0.42, the matched records' ratios
    assert a.power == 18 and a.active == 59, "its node is held from frame 85 to the clip's end"
    (note,) = [f for f in found if f.code == "P3_OWN_SPARE"]
    assert "donor record 26" in note.text and f"spare record {a.id}" in note.text
    t = host_table(data)
    assert len(t.volumes[a.volume].volumes) >= len(feet) and any(t.attacks[a.id].raw)
    manifest.check(m)


def test_brute_pairs_without_attacks(derived):
    m, _ = derived
    for name in ("p3_1_15", "p3_2_20", "p3_2_23", "p3_2_19", "p3_2_19_2", "p3_2_19_3"):
        assert m.moves[name].attacks == []
    assert [m.moves[n].clip for n in ("p3_2_19", "p3_2_19_2", "p3_2_19_3")] == [
        "p3_32",
        "p3_107",
        "p3_108",
    ]


def test_an_attacks_override_beats_the_match(data):
    m = brute()
    assert m.brain is not None
    m.brain.attacks["26"] = 25
    out, found = own.expand(m, data)
    assert [a.id for a in out.moves["p3_7_14"].attacks] == [15, 25]
    assert not [f for f in found if f.code in ("P3_OWN_ATTACK", "P3_OWN_SPARE")]
    assert out.attacks == [] and out.hitboxes == [], "a chosen record needs no spare"


def test_slots_follow_the_expanded_manifest(derived):
    m, _ = derived
    slots = moves.slots(m)
    assert len(slots) == len([n for n, mv in m.moves.items() if mv.own])
    assert slots["p3_7_13"] == sorted(slots).index("p3_7_13")


def zinogre(pairs: list[tuple[int, int]]) -> manifest.Manifest:
    """The Zinogre port with a `[brain]` of its own, every pair `own`."""
    m = manifest.load(PORTS / "zinogre.toml")
    m.brain = manifest.Brain(40, "base_3", "base_12", pairs={f"{a},{b}": "own" for a, b in pairs})
    return m


def test_zinogre_derives_without_an_error(data):
    walk = p3hits.Walk.of(data.p3rd, 40, data.p3rd.read(manifest.Source(model=5339).anim))
    keys = [k for k, p in walk.ms.pairs.items() if p.attacks][:8]
    m, found = own.expand(zinogre(keys), data)
    assert not [f for f in found if f.level == "error"]
    manifest.check(m)
    made = [mv for n, mv in m.moves.items() if n.startswith("p3_")]
    assert len(made) >= len(keys) and sum(len(mv.attacks) for mv in made) >= len(keys)


UNMATCHED = [(7, 2), (7, 3), (7, 8), (7, 9), (7, 15), (7, 40), (7, 162), (7, 163)]
"""Zinogre's pairs that play the nine donor records no Tigrex record matches (4, 7, 18, 34, 35,
36, 37, 43, 44); all its 111 pairs with attacks play 27 records, 154 hits."""


def test_zinogre_unmatched_records_get_spares(data):
    m, found = own.expand(zinogre(UNMATCHED), data)
    assert not [f for f in found if f.level == "error" or f.code == "P3_OWN_ATTACK"]
    made = [a for a in m.attacks if a.label.startswith("donor ")]
    assert sorted(int(a.label.split()[1]) for a in made) == [4, 7, 18, 34, 35, 36, 37, 43, 44]
    assert len([f for f in found if f.code == "P3_OWN_SPARE"]) == 9
    ids = [a.id for a in made]
    authored = {6, 31}  # its own [[attack]] levers
    assert len(set(ids)) == 9 and not set(ids) & {*spare.RULED[75], 70, *authored}
    t = host_table(data)
    for a in made:
        assert (
            a.volume is not None and a.volume >= 18 and a.volume != 2 and any(t.attacks[a.id].raw)
        )
        boxes = [h for h in m.hitboxes if h.set == a.volume]
        assert 1 <= len(boxes) <= len(t.volumes[a.volume].volumes), f"set {a.volume}"
    assert {a.kind for a in made} == {1, 2}, "record 18 is the one weak hit"
    manifest.check(m)


def test_a_pair_the_donor_lacks_stops_the_build(data):
    m = brute()
    assert m.brain is not None
    m.brain.pairs["9,99"] = manifest.OWN
    out, found = own.expand(m, data)
    assert [f.code for f in found if f.level == "error"] == ["P3_OWN_NOCLIPS"]
    with pytest.raises(manifest.ManifestError, match="P3_OWN_NOCLIPS"):
        own.expanded(m, data)
    assert "p3_9_99" not in out.moves
