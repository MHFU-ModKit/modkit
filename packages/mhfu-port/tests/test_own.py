# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import dataclasses
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from mhfu import hitbox
from mhfu.files import em_overlay
from mhfu_port import manifest, moves, p3hits, p3motion, sequence
from mhfu_port.behaviour import Block, MoveNode
from mhfu_port.brain import own, spare
from mhfu_port.manifest import AttackWindow, Effect, Lunge, Steer
from mhfu_port.p3hits import Hit, Length, Note, Plan, Step, Turn

PORTS = Path(__file__).parents[3] / "ports"
Pair = tuple[int, int]
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
LENGTHS = {
    61: Length(382.0),
    59: Length(144.0, True),
    60: Length(252.0),
    62: Length(100.0),
    63: Length(100.0),
}
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

    def __init__(self, plan: Plan | Sequence[Plan], hosts=HOSTS, active=10) -> None:
        plans = [plan] if isinstance(plan, Plan) else list(plan)
        self.lengths, self.step = LENGTHS, p3hits.STEP_FRAMES
        self.plans = {p.pair: p for p in plans}
        self.ms = SimpleNamespace(pairs={p: p for p in self.plans})
        self.hosts, self.active = hosts, active
        self.hits = SimpleNamespace(records=[])
        self.table = None  # no spare records to hand out
        self.binding = SimpleNamespace(joint_of={})
        self.bind = np.zeros((0, 3))

    def plan(self, pair: Pair) -> Plan:
        return self.plans[pair]

    def match(self, record: int) -> own.Match:
        host = self.hosts.get(record)
        return own.Match(record, host, 0.5 if host is not None else 0.1, False, 4)

    def attack(self, host: int) -> object:
        return SimpleNamespace(unknown_01=self.active, power=50)


def derive(plan: Plan, spare: Sequence[int] = (), **kw):
    m = manifest.loads(TEXT)
    found: list = []
    fake = Fake(plan, **kw)
    d = own.Derivation(m, fake, lambda: fake, found.append, spare=lambda: list(spare))  # type: ignore[arg-type]
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
def test_a_step_with_no_frame_to_stop_on_stops_where_the_walks_cursor_was(ended, stop):
    """No frame, or one outside the clip or not after the start: 55 AI frames from 24."""
    m, _ = derive(plan(step(61, ended=ended, ticks=55, start=24.0, stop=stop)))
    assert spans(m) == (24, 134, None)


def test_a_cursor_past_the_clip_stops_at_its_end():
    m, _ = derive(plan(step(62, ended="pair", ticks=80, start=24.0)))
    assert spans(m) == (24, 100, None)


def test_a_loop_is_cut_by_length_not_by_a_cursor_it_wraps():
    m, _ = derive(plan(step(59, ended="pair", ticks=55, start=24.0)))
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


def moving(start: float = 0.0, ticks: int = 22, clip: int = 61, **motion) -> Step:
    return dataclasses.replace(step(clip, ended="cursor", ticks=ticks, start=start), **motion)


JUMP = tuple(float(v) for v in range(45))


def test_a_jump_plays_a_lifted_copy_of_its_clip_in_a_free_entry():
    hits = [Hit(0, 21, 0, 10.0, 30.0)]
    m, found = derive(
        plan(dataclasses.replace(moving(lift=JUMP), hits=tuple(hits))), spare=[90, 91]
    )
    mv = m.moves["p3_7_13"]
    assert (mv.clip, mv.start, mv.stop, mv.length) == ("p3_61_air", 0, 44, None)
    assert m.clips["p3_61_air"] == manifest.Clip(
        slot=90, source=61, start=0, frames=44, lift=list(JUMP)
    )
    assert m.clips["charge"].lift is None, "the whole clip stays as it was"
    assert [(a.id, a.frame, a.end) for a in mv.attacks] == [(7, 10, 30)]
    (f,) = [f for f in found if f.code == "P3_OWN_AIR"]
    assert f.level == "info" and "+44" in f.text and "entry 90" in f.text
    manifest.check(m)


def test_a_jump_stops_at_its_last_frame_at_rest_or_above_so_the_landing_takes_the_fall():
    sunk = (*JUMP[:40], -5.0, -9.0, -9.0, -9.0, -9.0)
    m, _ = derive(plan(moving(lift=sunk)), spare=[90])
    mv = m.moves["p3_7_13"]
    assert (mv.clip, mv.start, mv.stop, mv.length) == ("p3_61_air", 0, 39, None)
    cut = moving(start=100.0, lift=sunk)
    m, _ = derive(plan(cut), spare=[90])
    assert (m.moves["p3_7_13"].start, m.moves["p3_7_13"].stop) == (0, 39)  # counted from the copy
    manifest.check(m)


def test_a_jump_ends_by_no_wall_and_a_run_does():
    m, _ = derive(plan(moving(clip=59, lift=JUMP)), spare=[90])
    mv = m.moves["p3_7_13"]
    assert (mv.clip, mv.steer.walls, mv.length) == ("p3_59_air", False, None)
    m, _ = derive(plan(moving(clip=59, ticks=40)))
    assert (m.moves["p3_7_13"].steer.walls, m.moves["p3_7_13"].length) == (True, 40)


def test_a_copy_counts_its_frames_from_where_the_step_starts():
    hits = (Hit(0, 21, 0, 110.0, 130.0),)
    got = moving(
        start=100.0,
        lift=JUMP,
        hits=hits,
        lunge=p3motion.Lunge((104.0, 132.0), 400.0, 1800.0, -700.0, 1800.0),
        spreads=(p3motion.Spread(-0x4000, (110.0, 120.0)),),
        stop=None,
    )
    m, _ = derive(plan(got), spare=[90])
    mv = m.moves["p3_7_13"]
    assert (m.clips["p3_61_air"].start, mv.start) == (100, 0)
    assert [(a.frame, a.end) for a in mv.attacks] == [(10, 30)]
    assert mv.lunge == Lunge((4, 32), 400.0, 1800.0, -700.0, 1800.0)
    assert mv.steer == Steer(turn="fixed", angle=-90.0, walls=False, window=(10, 20))
    manifest.check(m)


def test_steps_with_the_same_height_share_one_copy():
    p = plan(moving(lift=JUMP))
    other = dataclasses.replace(p, pair=(7, 14))
    m = manifest.loads(TEXT)
    fake = Fake([p, other])
    found: list = []
    d = own.Derivation(m, fake, lambda: fake, found.append, spare=lambda: [90, 91, 92])  # type: ignore[arg-type]
    d.run((7, 13))
    d.run((7, 14))
    assert [n for n, c in m.clips.items() if c.lift is not None] == ["p3_61_air"]
    assert m.moves["p3_7_13"].clip == m.moves["p3_7_14"].clip == "p3_61_air"


def test_a_jump_with_no_free_entry_is_an_error_and_plays_the_clip_plain():
    m, found = derive(plan(moving(lift=JUMP)), spare=[])
    assert m.moves["p3_7_13"].clip == "charge" and not any(c.lift for c in m.clips.values())
    (f,) = [f for f in found if f.code == "P3_OWN_AIR"]
    assert f.level == "error" and "no free executor entry" in f.text


def test_a_jump_that_outruns_its_clip_keeps_no_height():
    m, found = derive(plan(moving(start=60.0, clip=62, lift=JUMP)), spare=[90])
    assert m.moves["p3_7_13"].clip == "p3_62"
    (f,) = [f for f in found if f.code == "P3_OWN_AIR"]
    assert f.level == "info" and "height is left out" in f.text


def test_travel_and_a_lunge_are_the_steps_and_a_held_tail_is_left_off():
    travel = ((0.0, 0.0), (1.0, 2.0), (3.0, 4.0), (3.0, 4.0), (3.0, 4.0))
    lunge = p3motion.Lunge((0.0, 32.0), 400.0, 1800.0, -700.0, 1800.0)
    m, _ = derive(plan(moving(travel=travel, lunge=lunge)))
    mv = m.moves["p3_7_13"]
    assert mv.travel == [(0.0, 0.0), (1.0, 2.0), (3.0, 4.0)]
    assert mv.lunge == Lunge((0, 32), 400.0, 1800.0, -700.0, 1800.0)
    manifest.check(m)
    m, _ = derive(plan(moving()))
    assert m.moves["p3_7_13"].travel is None and m.moves["p3_7_13"].lunge is None


def test_travel_past_the_clips_end_is_cut_there():
    long = tuple((float(k), 0.0) for k in range(60))  # clip 62 is 100 frames: 51 samples
    m, found = derive(plan(moving(clip=62, travel=long)))
    assert len(m.moves["p3_7_13"].travel) == 51
    assert any(f.code == "P3_OWN_MOTION" and "outruns" in f.text for f in found)


def test_a_window_is_cut_to_the_frames_the_move_plays_and_its_total_by_the_share_left():
    lunge = p3motion.Lunge((0.0, 30.0), 400.0, 1800.0, -800.0, 1000.0)
    spread = p3motion.Spread(0x4000, (0.0, 60.0))
    m, found = derive(plan(moving(start=6.0, ticks=100, lunge=lunge, spreads=(spread,))))
    mv = m.moves["p3_7_13"]
    assert mv.lunge == Lunge((6, 30), 400.0, 1800.0, -640.0, 800.0)  # 24 frames of the 30
    assert (mv.steer.window, mv.steer.angle) == ((6, 60), 81.0)  # 54 of 60 frames of 90 degrees
    assert sum("is cut to the frames it plays" in f.text for f in found) == 2
    manifest.check(m)


def test_a_window_the_move_never_plays_is_left_out():
    past = p3motion.Lunge((150.0, 160.0), 400.0, 1800.0, -800.0, 1000.0)
    m, found = derive(
        plan(moving(clip=62, lunge=past, spreads=(p3motion.Spread(100, (150.0, 160.0)),)))
    )
    mv = m.moves["p3_7_13"]
    assert mv.lunge is None and mv.steer.turn == "clip"
    assert sum("falls outside the frames it plays" in f.text for f in found) == 2


def test_a_fixed_turn_is_the_steps_and_beats_a_turn_toward_the_hunter():
    spread = p3motion.Spread(0x2666, (60.0, 98.0))
    m, found = derive(plan(moving(ticks=60, spreads=(spread,), turns=(Turn(546, (10.0, 40.0)),))))
    s = m.moves["p3_7_13"].steer
    assert (s.turn, s.window, round(s.angle or 0)) == ("fixed", (60, 98), 54)
    assert any("kept the fixed" in f.text for f in found)
    manifest.check(m)
    whole = p3motion.Spread(0x10000, (0.0, 10.0))  # a whole turn is no turn the player can make
    m, found = derive(plan(moving(ticks=60, spreads=(whole,))))
    assert m.moves["p3_7_13"].steer.turn == "clip" and any("left out" in f.text for f in found)


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


# --- hand-overs ---


def hand(pair: Pair, *steps: Step, to: Pair = (0, 0), complete: bool = True) -> Plan:
    return Plan(pair, steps, (to,), complete)


def chained(plans: Sequence[Plan], pairs: str = "", heads=((7, 13),), tables: str = ""):
    """The heads derived from a fake walk over `plans`; `pairs` and `tables` are added to TEXT."""
    m = manifest.loads(TEXT + pairs + tables)
    found: list = []
    fake = Fake(plans)
    d = own.Derivation(m, fake, lambda: fake, found.append, heads)  # type: ignore[arg-type]
    for h in heads:
        d.run(h)
    d.finish()
    return m, found, d


def next_findings(found) -> list:
    return [f for f in found if f.code == "P3_OWN_NEXT"]


def test_a_handler_hands_over_to_its_pairs_move():
    m, found, _ = chained([hand((7, 13), step(61), to=(7, 14)), hand((7, 14), step(59))])
    assert sequence.chain(m, "p3_7_13") == ["p3_7_13", "p3_7_14"]
    assert m.moves["p3_7_14"].clip == "p3_59" and m.moves["p3_7_14"].after is None
    assert not next_findings(found)
    manifest.check(m)


def test_a_hand_over_goes_on_through_a_third_pair():
    plans = [
        hand((7, 13), step(61), to=(7, 14)),
        hand((7, 14), step(62), step(63), to=(7, 15)),
        hand((7, 15), step(59)),
    ]
    m, found, _ = chained(plans)
    chain = sequence.chain(m, "p3_7_13")
    assert chain == ["p3_7_13", "p3_7_14", "p3_7_14_2", "p3_7_15"]
    assert [m.moves[n].clip for n in chain] == ["charge", "p3_62", "p3_63", "p3_59"]
    assert not next_findings(found)


def test_a_follow_up_the_manifest_names_is_that_move():
    plans = [hand((7, 13), step(61), to=(7, 14)), hand((7, 20), step(62))]
    m, found, d = chained(plans, '"7,14" = "p3_7_20"\n"7,20" = "own"\n', [(7, 13), (7, 20)])
    assert sequence.chain(m, "p3_7_13") == ["p3_7_13", "p3_7_20"] and "p3_7_14" not in m.moves
    assert d.made == ["p3_7_13", "p3_7_20"] and not next_findings(found)
    m, _, _ = chained(plans[:1], '"7,14" = "dash"\n', tables='[moves.dash]\nclip = "charge"\n')
    assert m.moves["p3_7_13"].after == "dash" and "p3_7_14" not in m.moves


def test_a_follow_up_that_is_mapped_to_own_or_authored_is_derived_or_used():
    plans = [hand((7, 13), step(61), to=(7, 14)), hand((7, 14), step(59), to=(7, 15))]
    plans += [hand((7, 15), step(62))]
    m, _, _ = chained(plans, '"7,14" = "own"\n')
    assert sequence.chain(m, "p3_7_13") == ["p3_7_13", "p3_7_14", "p3_7_15"]
    m, found, _ = chained(plans, '"7,14" = "own"\n', tables="[moves.p3_7_14]\nclip = 'charge'\n")
    assert sequence.chain(m, "p3_7_13") == ["p3_7_13", "p3_7_14"], "the authored move is used"
    assert "p3_7_15" not in m.moves and not next_findings(found)


@pytest.mark.parametrize(
    ("value", "what"), [("[1, 2]", "host pair 1,2"), ('[[1, 2], "own"]', "several moves")]
)
def test_a_hand_over_the_manifest_plays_as_a_host_pair_is_not_followed(value, what):
    plans = [hand((7, 13), step(61), to=(7, 14)), hand((7, 14), step(59))]
    m, found, _ = chained(plans, f'"7,14" = {value}\n')
    assert m.moves["p3_7_13"].after is None and "p3_7_14" not in m.moves
    (f,) = next_findings(found)
    assert f.level == "info" and f.item == "p3_7_13" and what in f.text and "7,14" in f.text


@pytest.mark.parametrize(
    ("follow", "why"),
    [
        (hand((7, 14), step(59), complete=False), "the walk gave up before its handler ended"),
        (hand((7, 14)), "its handler plays no clip"),
        (hand((7, 14), step(999)), "it plays clip 999, which the donor lacks"),
        (None, "the donor has no such pair"),
    ],
)
def test_a_follow_up_the_port_cannot_play_is_not_followed(follow, why):
    m, found, _ = chained([hand((7, 13), step(61), to=(7, 14)), *([follow] if follow else [])])
    assert m.moves["p3_7_13"].after is None and "p3_7_14" not in m.moves
    (f,) = next_findings(found)
    assert f"pair 7,14, which the port does not play: {why}" in f.text and f.item == "p3_7_13"
    assert not [f for f in found if f.level == "error"], "the follow-up is not an error"


def test_a_follow_up_with_a_move_that_is_no_own_move_is_not_followed():
    plans = [hand((7, 13), step(61), to=(7, 14))]
    tables = "[moves.p3_7_14]\nmain = 1\nsub = 2\nclip = 'charge'\n"
    m, found, _ = chained(plans, '"7,14" = "own"\n', tables=tables)
    assert m.moves["p3_7_13"].after is None
    assert "moves.p3_7_14 is no own move" in next_findings(found)[0].text


def test_a_handler_that_ends_in_several_pairs_is_not_followed():
    p = Plan((7, 13), (step(61),), ((7, 14), (7, 15)), True)
    m, found, _ = chained([p])
    assert m.moves["p3_7_13"].after is None
    assert "one of pairs 7,14, 7,15" in next_findings(found)[0].text


def test_a_ring_is_cut_where_it_closes():
    plans = [hand((7, 13), step(61), to=(7, 14)), hand((7, 14), step(59), to=(7, 13))]
    m, found, _ = chained(plans)
    assert sequence.chain(m, "p3_7_13") == ["p3_7_13", "p3_7_14"]
    assert m.moves["p3_7_14"].after is None
    (f,) = next_findings(found)
    assert f.item == "p3_7_14" and "pair 7,13: following it would close a ring" in f.text
    m, found, _ = chained([hand((7, 13), step(61), to=(7, 13))])
    assert m.moves["p3_7_13"].after is None and "close a ring" in next_findings(found)[0].text
    manifest.check(m)


def test_a_ring_through_three_pairs_is_cut_at_the_last_link():
    plans = [
        hand((7, 13), step(61), to=(7, 14)),
        hand((7, 14), step(59), to=(7, 15)),
        hand((7, 15), step(62), to=(7, 14)),
    ]
    m, found, _ = chained(plans)
    assert sequence.chain(m, "p3_7_13") == ["p3_7_13", "p3_7_14", "p3_7_15"]
    assert m.moves["p3_7_15"].after is None and "close a ring" in next_findings(found)[0].text


def test_each_pair_is_derived_once():
    plans = [hand((7, 13), step(61), to=(7, 14)), hand((7, 14), step(59))]
    for heads in ([(7, 13), (7, 14)], [(7, 14), (7, 13)]):
        m, _, d = chained(plans, '"7,14" = "own"\n', heads)
        assert sorted(d.made) == ["p3_7_13", "p3_7_14"]
        assert m.moves["p3_7_13"].after == "p3_7_14"


# --- sharing ---


def own_moves(extra: str = "", **after: str | None) -> manifest.Manifest:
    """Own moves on the same clip, by name with the move each hands to."""
    body = "".join(
        f'[moves.{n}]\nclip = "charge"\n' + (f'after = "{a}"\n' if a else "")
        for n, a in after.items()
    )
    return manifest.loads(TEXT + body + extra)


def test_equal_tails_make_equal_predecessors_one_move():
    m = own_moves(a1="b1", b1=None, a2="b2", b2=None)
    dropped = own.share(m, ["a1", "b1", "a2", "b2"])
    assert dropped == {"a2": "a1", "b2": "b1"} and set(m.moves) == {"a1", "b1"}
    assert m.moves["a1"].after == "b1"
    manifest.check(m)


def test_moves_that_differ_in_any_field_but_the_label_stay_apart():
    m = own_moves(a="c", b="c", c=None, d="c", e="c2", c2=None)
    m.moves["a"].label, m.moves["b"].label = "MHP3rd 7,1", "MHP3rd 7,2"
    m.moves["d"].start = 5
    m.moves["c2"].length = 3
    dropped = own.share(m, ["a", "b", "c", "d", "e", "c2"])
    assert dropped == {"b": "a"} and set(m.moves) == {"a", "c", "d", "e", "c2"}
    assert m.moves["a"].label == "MHP3rd 7,1", "the first derived is kept"


def test_a_chain_of_equal_steps_merges_to_a_fixed_point():
    names = {f"{h}{k}": (f"{h}{k + 1}" if k < 4 else None) for h in "xy" for k in range(1, 5)}
    m = own_moves(**names)
    dropped = own.share(m, list(names))
    assert dropped == {f"y{k}": f"x{k}" for k in range(1, 5)} and len(m.moves) == 4


def test_an_anchored_move_is_the_one_kept():
    m = own_moves(a1="b1", b1=None, a2="b2", b2=None, x="a2")
    assert own.share(m, ["a1", "b1", "a2", "b2"]) == {"a1": "a2", "b2": "b1"}
    assert m.moves["x"].after == "a2" and set(m.moves) == {"a2", "b1", "x"}
    assert m.moves["a2"].after == "b1", "its `after` follows the tail's merge"


def test_two_anchored_moves_stay_apart():
    m = own_moves(a1="b", a2="b", b=None, x="a1", y="a2", a3="b")
    assert own.share(m, ["a1", "a2", "b", "a3"]) == {"a3": "a1"}
    assert set(m.moves) == {"a1", "a2", "b", "x", "y"}


@pytest.mark.parametrize("how", ["pairs", "play", "node", "effect", "after"])
def test_what_names_a_move_from_outside_anchors_it(how):
    m = own_moves(a="c", b="c", c=None, outside="b")
    assert m.brain is not None
    if how == "pairs":
        m.brain.pairs["7,14"] = "b"
    elif how == "play":
        m.behaviour.blocks["k"] = Block("any_time", (0.0, 0.0), play=["b"])
    elif how == "node":
        m.behaviour.moves["b"] = MoveNode((0.0, 0.0))
    elif how == "effect":
        m.effects.append(Effect("b", 1, 2, 3))
    if how != "after":
        m.moves["outside"].after = None
    assert own.anchored(m, ["a", "b", "c"]) == {"b"}
    assert own.share(m, ["a", "b", "c"]) == {"a": "b"}


def test_a_ring_of_equal_moves_terminates():
    m = own_moves(a="b", b="a")
    assert own.share(m, ["a", "b"]) == {} and set(m.moves) == {"a", "b"}
    m = own_moves(a="b", b="c", c="a", d="e", e="f", f="d")
    assert own.share(m, list("abcdef")) == {} and len(m.moves) == 6


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
    assert not [f for f in found if f.code == "P3_OWN_NEXT"], "every handler ends at rest"


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
