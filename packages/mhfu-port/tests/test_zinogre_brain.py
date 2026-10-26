# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Zinogre's MHP3rd script spliced into em75 by ports/zinogre.toml's `[brain]`, and the tables
and moves module that go with it. Needs both games' files."""

import copy
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
from mhfu import addresses
from mhfu.em.script import brainfile, build, draw, edit, ir
from mhfu.em.script import vars as script_vars
from mhfu.em.script.budget import LIMIT
from mhfu_port import behaviour, fk, manifest, moves, sequence
from mhfu_port import build as builds
from mhfu_port.brain import charge, fatigue, own, p3, p3ops, splice
from mhfu_port.build import build as build_port
from mhfu_port.cli import main
from mhfu_port.cli.build import modules
from mhfu_port.data import Data
from mhfu_port.model import ANIMATION, SKELETON
from mhp_formats import fu
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Skeleton

PORT = Path(__file__).parents[3] / "ports" / "zinogre.toml"
OWN = addresses.OWN_MOVE_MAIN
TURN = (1, 2)
KEPT = {f"b{i}" for i in [*range(1, 7), *range(12, 22), 36]}
"""The hand-built rules that stay: the lunge's skids, the notice, the combat flag, the part breaks
and the roar signal."""


@pytest.fixture(scope="module")
def zinogre(data: Data) -> SimpleNamespace:
    """The donor, the host row, the manifest with its derived moves and the helpers' tables."""
    m, derived = own.expand(manifest.load(PORT), data)
    assert m.brain is not None
    ch, said = charge.table(m, data)
    return SimpleNamespace(
        donor=p3.lift(data.p3rd, m.brain.donor, m.brain.slot),
        host=build.lift_row(data.fu, 75, 75),
        overlay=data.fu.em(75),
        survey=script_vars.survey(build.lift_row(data.fu, 75, 75), data.fu.em(75)),
        m=m,
        derived=derived,
        slots=moves.slots(m),
        charge=ch,
        said=said,
    )


def splice_of(z: SimpleNamespace, b: manifest.Brain):
    named = z.charge.named if z.charge is not None else ()
    s = splice.splice(z.donor, z.host, b, z.survey, z.slots, z.overlay, named)
    return s, build.Built.of(z.host, s.edits)


def codes(found: Sequence[object]) -> Counter[str]:
    return Counter(f"{f.level} {f.code}" for f in found)  # type: ignore[attr-defined]


def test_the_manifest_carries_its_brain(zinogre):
    m = zinogre.m
    b = m.brain
    assert (b.donor, b.splice, b.entry, b.fatigue, b.charge) == (40, "base_3", "base_3", True, True)
    assert b.when == {"if_target_set": True, "if_target_state": True}
    assert manifest.loads(manifest.dumps(m)) == m
    assert len(b.pairs) == 107 and len(b.derived()) == len(own.pairs(b)) == 49


def test_a_donor_pair_plays_the_authored_move_where_it_is_the_donors(zinogre):
    b = zinogre.m.brain
    assert b.targets((1, 16)) == ["charge_up"] and b.targets((1, 15)) == ["p3_1_15"]
    assert b.targets((1, 2)) == ["break_howl"]
    assert [b.targets(p) for p in [(2, 3), (2, 4)]] == [["dash_stop"], ["dash"]]
    assert b.targets((7, 29)) == b.targets((7, 30)) == [TURN, "dash"]
    assert zinogre.m.moves["dash"].after is None


def test_a_wrapper_turns_and_plays_the_move_of_the_pair_it_leads_to(zinogre):
    b = zinogre.m.brain
    assert b.targets((7, 23)) == [TURN, "p3_7_0"] and b.targets((7, 0)) == ["p3_7_0"]
    assert b.targets((7, 110)) == [TURN, "p3_7_109"] and b.targets((7, 109)) == ["p3_7_109"]
    twins = [b.targets((2, 11)), b.targets((7, 5))]
    assert twins == [["p3_2_10"], ["p3_7_4"]]
    assert set(b.derived().values()) >= {(7, 0), (7, 109), (2, 10), (7, 4), (1, 4), (7, 13)}
    assert b.shared() <= set(b.derived().values()) and (7, 0) in b.shared()
    assert (7, 13) in b.shared() and b.targets((7, 38)) == [TURN, "p3_7_13"]


WALKS = {
    (2, 12): "p3_2_12",
    (2, 13): "p3_2_12",
    (2, 14): "p3_2_12",
    (2, 16): "p3_2_16",
    (2, 17): "p3_2_17",
    (2, 21): "p3_2_21",
    (2, 23): "p3_2_23",
    (2, 24): "p3_2_23",
    (2, 25): "p3_2_23",
}
"""The donor pairs that walk, with the move each plays (the host's (1, 0) parked live: D1)."""


def test_the_walks_play_own_moves_not_the_host_pair_1_0(zinogre):
    b = zinogre.m.brain
    assert not [k for k in b.pairs if (1, 0) in b.targets(manifest.pair_of(k))]
    assert {p: b.targets(p) for p in WALKS} == {p: [name] for p, name in WALKS.items()}


def chain_of(m: manifest.Manifest, name: str) -> list[tuple[object, ...]]:
    """What a derived move plays, step by step: clip, attacks, steer, length, start, stop."""
    out = []
    mv: manifest.Move | None = m.moves[name]
    while mv is not None:
        clip = m.clips[mv.clip].source if mv.clip else mv.anim
        attacks = tuple((a.id, a.frame, a.end) for a in mv.attacks)
        out.append((clip, attacks, mv.steer, mv.length, mv.start, mv.stop))
        mv = m.moves[mv.after] if mv.after else None
    return out


D2 = {"p3_7_17_3": 42, "p3_7_17_5": 24}
"""The steps that ended on a frame past their attack, live D2: `length` in AI frames stopped
them at cursor 54 of the 56 the attack needs. (7, 19)'s are the same moves."""


def test_the_d2_steps_start_where_the_donor_did_and_stop_past_their_attack(zinogre):
    for name, start in D2.items():
        mv = zinogre.m.moves[name]
        (attack,) = mv.attacks
        assert (mv.start, mv.length) == (start, None), name
        assert attack.frame <= attack.end <= mv.stop and mv.stop > attack.frame, name


def test_every_derived_window_lies_inside_its_move_s_frames(zinogre, module_text):
    _, built = module_text
    made = {n: mv for n, mv in zinogre.m.moves.items() if n.startswith("p3_")}
    assert sum(bool(mv.start) for mv in made.values()) == 26
    assert sum(bool(mv.stop) for mv in made.values()) == 40
    assert sum(mv.length is not None for mv in made.values()) == 2
    for name, mv in made.items():
        frames = built.layout.frames[moves.entry(zinogre.m, name, built.layout)]
        assert mv.start < frames and mv.stop <= frames, name
        assert not (mv.stop and mv.length is not None), name
        for a in mv.attacks:
            assert mv.start <= a.frame < a.end <= (mv.stop or frames), name


def air_clips(m: manifest.Manifest) -> dict[int, manifest.Clip]:
    """The lifted copies of jumps, by the donor clip they copy."""
    return {c.source: c for c in m.clips.values() if c.lift is not None and c.source is not None}


def test_the_jumps_are_lifted_copies_in_entries_nothing_else_holds(zinogre, data):
    m = zinogre.m
    air = air_clips(m)
    assert {s: max(c.lift) for s, c in air.items()} == pytest.approx({225: 644, 209: 300}, rel=0.01)
    lay = builds.layout(m, builds.donor(m, data), builds.host(m, data))
    assert not lay.unplaced
    for source, c in air.items():
        assert lay.entries[c.slot] == source and lay.cuts[c.slot] == c.cut
        assert lay.lifts[c.slot] == tuple(c.lift) and c.slot not in lay.partial
        assert lay.ids[source] != c.slot, "the whole clip keeps its own entry"
    assert sum(mv.own for mv in m.moves.values()) <= manifest.OWN_MOVES


def test_the_air_roll_flies_and_lunges_as_the_donor_does(zinogre):
    m = zinogre.m
    roll = m.moves["p3_7_22_2"]
    assert (m.clips[roll.clip].source, roll.length, roll.start) == (225, None, 0)
    assert (roll.stop, roll.steer.walls) == (39, False), "the fall past rest is the landing's"
    assert roll.travel[-1] == pytest.approx((120, 852), rel=0.01)
    assert roll.lunge == manifest.Lunge((0, 32), 400.0, 1800.0, -700.0, 1800.0)
    assert [m.moves[n].clip for n in sequence.chain(m, "p3_7_22")][1] == roll.clip


def test_the_stamp_waits_16_or_39_frames_and_hops_for_18(zinogre):
    m = zinogre.m
    short, long = m.moves["p3_7_21"], m.moves[manifest.own_name((7, 86))]
    assert (short.start, short.stop, long.start, long.stop) == (
        46,
        78,
        0,
        78,
    )  # 16 and 39 AI frames
    hop = m.moves[short.after]
    assert (m.clips[hop.clip].source, hop.length, hop.stop) == (209, None, 36)
    assert not hop.steer.walls
    assert hop.travel[-1] == pytest.approx((83, 1401), rel=0.01, abs=1)


def test_a_step_no_test_ended_stops_where_the_walks_cursor_was(zinogre):
    """Clip 25 has 118 frames; the donor leaves it at 60. 30 AI frames stopped it at 54 live,
    the blend having held the cursor."""
    m = zinogre.m
    back = m.moves["p3_7_33"]
    assert (m.clips[back.clip].source, back.start, back.stop, back.length) == (25, 0, 60, None)


def test_only_a_loop_is_cut_by_length(zinogre, data):
    d = builds.donor(zinogre.m, data)
    cut = [n for n, mv in zinogre.m.moves.items() if n.startswith("p3_") and mv.length is not None]
    assert cut and all(d.clips[zinogre.m.clips[zinogre.m.moves[n].clip].source].loop for n in cut)


def test_every_hunter_turn_has_the_donors_rate(zinogre):
    hunters = {
        n: mv.steer.rate
        for n, mv in zinogre.m.moves.items()
        if n.startswith("p3_") and mv.steer.turn == "hunter"
    }
    assert hunters and None not in hunters.values()
    assert [hunters[n] for n in ("p3_7_12", "p3_7_86", "p3_7_89", "p3_7_150")] == [
        546,
        728,
        1365,
        1001,
    ]


def test_the_sidesteps_travel_across_their_window_and_turn_half_way(zinogre):
    m = zinogre.m
    left, right = m.moves["p3_2_16"], m.moves["p3_2_17"]
    assert left.travel[-1] == (-257.0, -525.0) and right.travel[-1] == (192.0, -525.0)
    assert left.steer == manifest.Steer(turn="fixed", angle=-180.0, walls=False, window=(40, 160))
    assert right.steer.angle == 180.0 and left.length is None


def test_the_built_clips_carry_the_jumps_height_on_joint_0(zinogre, module_text):
    _, built = module_text
    pac = Pac.from_bytes(built.pac)
    anim = fu.Anim.from_bytes(pac.entries[ANIMATION])
    skeleton = Skeleton.from_bytes(pac.entries[SKELETON])
    for source, peak in {225: 644, 209: 300}.items():
        c = air_clips(zinogre.m)[source]
        clip = fk.rig_clip(anim, c.slot, skeleton)
        assert clip is not None
        (height,) = [ch for ch in clip.tracks[0].channels if ch.bit == 0x80]
        assert max(k.value for k in height.keyframes) / 16 == pytest.approx(peak, rel=0.01)


HANDS = {
    "p3_7_4": [212, 213, 209, 210],
    "p3_7_12": [203, 200, 257, 253],
    "p3_7_13": [204, 253, 258, 200],
    "p3_7_14": [32, 34, 221, 222, 223],
    "p3_7_16": [30, 34, 221, 222, 223],
    "p3_7_33": [25, 27, 225, 226],
    "p3_7_65": [237],
    "p3_7_67": [227, 230, 27, 225, 226],
    "p3_7_68": [202, 253, 258, 200, 257, 253],
    "p3_7_69": [201, 200, 257, 253, 258, 200],
    "p3_7_99": [231, 235, 212, 213, 209, 210],
    "p3_7_100": [213, 209, 210, 212, 213, 209, 210],
}
"""The donor clips each head whose handler hands over plays, in order, across the pairs."""


def test_the_chains_play_the_donor_s_clips_across_its_hand_overs(zinogre):
    m = zinogre.m
    for head, clips in HANDS.items():
        steps = sequence.chain(m, head)
        assert [m.clips[m.moves[n].clip].id for n in steps] == clips, head
    assert not [n for n in ("p3_7_237", "p3_7_5") if n in m.moves], "twins are the kept move"


def test_every_hand_over_is_followed_but_to_the_pair_with_no_clip(zinogre):
    (f,) = [f for f in zinogre.derived if f.code == "P3_OWN_NEXT"]
    assert f.item == "p3_7_65" and "pair 8,0, which the port does not play" in f.text
    assert zinogre.m.moves["p3_7_65"].after is None


def test_equal_tails_are_one_move(zinogre):
    m = zinogre.m
    assert sequence.chain(m, "p3_7_14")[-3:] == ["p3_7_0", "p3_7_0_2", "p3_7_0_3"]
    assert m.moves["p3_7_16"].after == "p3_7_14_2" == m.moves["p3_7_14"].after
    assert m.moves["p3_7_19"].after == "p3_7_17_2" == m.moves["p3_7_17"].after
    assert "p3_7_19_3" not in m.moves
    assert {"p3_1_6", "p3_1_15", "p3_1_17"} <= set(m.moves), "named moves stay apart"
    assert all(f.item in m.moves for f in zinogre.derived if f.item and f.item.startswith("p3_"))


LEADS = {
    "7,219": "p3_7_70",
    "7,220": "p3_7_71",
    "7,222": "p3_7_81",
    "7,223": "p3_7_82",
    "7,229": "p3_7_118",
    "7,230": "p3_7_119",
}
"""The pairs the turn wrappers (7, 72), (7, 73), (7, 83), (7, 84), (7, 120), (7, 121) lead to, with
the move each plays."""


def test_the_pairs_that_share_a_move_derive_the_same_one(zinogre, data):
    """A pair that names its twin's move would derive that very move itself, and the wrappers play
    the moves of twins of the pairs they lead to."""
    b = zinogre.m.brain
    twins = {k: v for k, v in b.pairs.items() if isinstance(v, str) and v in b.derived()}
    assert len(twins) == 11
    twins |= LEADS
    apart = manifest.load(PORT)
    apart.brain.pairs |= {k: manifest.OWN for k in twins}
    again, _ = own.expand(apart, data)
    for key, first in twins.items():
        name = manifest.own_name(manifest.pair_of(key))
        assert chain_of(again, name) == chain_of(again, first), key


def test_the_wrappers_play_the_moves_of_the_twins_they_lead_to(zinogre):
    b = zinogre.m.brain
    wrappers = {72: 70, 73: 71, 83: 81, 84: 82, 120: 118, 121: 119}
    assert {w: b.targets((7, w)) for w in wrappers} == {
        w: [TURN, manifest.own_name((7, p))] for w, p in wrappers.items()
    }
    assert all(b.pairs[f"7,{p}"] == manifest.OWN for p in wrappers.values())


def test_what_fought_the_brain_is_gone(zinogre):
    m = zinogre.m
    assert not [n for n, mv in m.moves.items() if mv.claim is not None]
    assert set(m.behaviour.blocks) == KEPT
    assert not {"flinches", "in_combat"} - set(
        behaviour.vars(m)
    ) or "flinches" not in behaviour.vars(m)
    plays = {p for blk in m.behaviour.blocks.values() for p in blk.play}
    assert plays == {
        "lunge_stop",
        "notice_howl",
        "flinch_head",
        "topple_left",
        "topple_right",
        "break_howl",
    }
    assert m.moves["topple_left_3"].after == "stamp" and "stamp" in m.moves


def test_zinogre_splices_into_em75(zinogre):
    s, built = splice_of(zinogre, zinogre.m.brain)
    assert not [f for f in s.findings if f.level == "error"], s.findings
    assert built.ok, built.findings
    assert built.budget.worst is not None and built.budget.worst.count <= min(LIMIT, 150)
    grown = len(built.image.data) - (built.lift.end - built.lift.base)
    assert 8000 < grown < 14000 and 50 <= len(s.routines) <= 65
    got = codes(s.findings)
    assert got["info P3_SPLIT"] == 10 and got["warning P3_FOLDED"] == 3
    assert got["info P3_HISTORY"] == 1 and got["warning P3_APPROX"] == 1
    assert not got["warning P3_PAIR_UNUSED"] and not got["warning P3_WHEN_IGNORED"]
    assert {k for k in got if k.startswith(("error", "warning"))} == {
        "warning P3_FOLDED",
        "warning P3_APPROX",
    }
    folded = " ".join(f.text for f in s.findings if f.code == "P3_FOLDED")
    assert "switch_global_540 taken as answer 2" in folded
    assert "switch_query 2 taken as answer 0" in folded and "switch_query 0" not in folded


def inner_of(arm: ir.Arm) -> ir.Block | None:
    """The random an arm opens with."""
    first = arm.body[0] if arm.body else None
    return first if isinstance(first, ir.Block) and first.op.name == "random" else None


def reached(outer: ir.Block) -> int:
    """How many leaf arms of a split random some draw of the 32 takes."""
    hit = set()
    for v in range(draw.DRAW):
        k = draw.take(draw.weights(outer) or [], v)
        inner = inner_of(outer.arms[k])
        hit.add((k, None if inner is None else draw.take(draw.weights(inner) or [], v)))
    return len(hit)


def test_zinogre_splits_reach_every_donor_arm(zinogre):
    s, built = splice_of(zinogre, zinogre.m.brain)
    nodes = [n for i in s.edits.inserts.values() for n in edit.everything(i.nodes)]
    nodes += [n for r in s.edits.routines.values() for n in edit.everything(r)]
    outers = [
        n
        for n in nodes
        if isinstance(n, ir.Block) and n.op.name == "random" and any(map(inner_of, n.arms))
    ]
    live = sorted(int(f.text.split()[2]) for f in s.findings if f.code == "P3_SPLIT")
    assert len(live) == len(outers) == 10
    assert sorted(reached(o) for o in outers) == live
    got = codes(built.findings)
    assert built.ok and got["warning CORRELATED_DRAW"] == 15 and not got["warning ARM_UNREACHABLE"]


def queries(built: build.Built) -> Counter[int]:
    return Counter(
        n.args["arg"]
        for r in built.program.routines()
        for n in ir.walk(r.body)
        if isinstance(n, ir.Block) and n.op.name == "switch_query"
    )


def test_zinogre_keeps_the_charge_queries(zinogre):
    _, built = splice_of(zinogre, zinogre.m.brain)
    got = queries(built)
    assert set(got) == {0, 6} and got[0] > 100 and got[6] > 70


def test_zinogre_reads_tired_from_gauge_a(zinogre):
    _, built = splice_of(zinogre, zinogre.m.brain)
    tests = [
        n for r in built.program.routines() for n in ir.walk(r.body) if isinstance(n, ir.Block)
    ]
    assert any(n.op.name == "if_gauge_a_low" for n in tests)
    assert not any(n.op.name in ("if_tired", "if_stamina_low") for n in tests)


def test_zinogre_walks_enter_their_own_moves(zinogre):
    s, _ = splice_of(zinogre, zinogre.m.brain)
    for pair, name in WALKS.items():
        assert s.uses[pair, name] > 0 and not [t for (p, t) in s.uses if p == pair and t != name]
    nodes = [n for i in s.edits.inserts.values() for n in edit.everything(i.nodes)]
    nodes += [n for r in s.edits.routines.values() for n in edit.everything(r)]
    pairs = {
        (n.args["main"], n.args["sub"])
        for n in nodes
        if isinstance(n, ir.Insn) and n.op.name == "enter"
    }
    assert (1, 0) not in pairs and set(WALKS.values()) <= set(s.slots)


def test_zinogre_enters_its_own_moves(zinogre):
    s, built = splice_of(zinogre, zinogre.m.brain)
    own_enters = {
        n.args["sub"]
        for r in built.program.routines()
        for n in ir.walk(r.body)
        if isinstance(n, ir.Insn) and n.op.name == "enter" and n.args["main"] == OWN
    }
    assert own_enters == {zinogre.slots[n] for _, n in s.uses if isinstance(n, str)}
    assert {"p3_7_0", "p3_7_12", "p3_1_19", "charge_up", "dash", "break_howl"} <= set(s.slots)


def test_zinogre_without_the_charge_helper_is_never_charged(zinogre):
    plain = copy.deepcopy(zinogre.m.brain)
    plain.charge = False
    s, built = splice_of(zinogre, plain)
    assert built.ok and not queries(built)
    assert not [f for f in s.findings if f.level == "error"]
    folded = Counter(f.text.split(" taken")[0] for f in s.findings if f.code == "P3_FOLDED")
    assert folded == {
        "if_target_set": 1,
        "switch_query 0": 4,
        "switch_query 2": 1,
        "switch_query 6": 2,
    }
    assert len(s.routines) < 40  # the charged picker and what only it calls are gone


def expanded(brain_: p3.Brain, nodes: Sequence[p3.Node]) -> list[tuple[int, int]]:
    """The pairs a routine enters, with each call replaced by its routine's."""
    out: list[tuple[int, int]] = []
    for n in p3.walk(nodes):
        if isinstance(n, p3.Insn) and n.op.name == "enter":
            out.append((n.args["main"], n.args["sub"]))
        elif isinstance(n, p3.Insn) and n.op.name == "call":
            name = brain_.entry(p3ops.CALL, n.args["routine"])
            assert name is not None
            out += expanded(brain_, brain_.routines[name].body)
    return out


def test_zinogre_turns_then_moves(zinogre):
    b = zinogre.m.brain
    s, _ = splice_of(zinogre, b)
    turned = [manifest.pair_of(k) for k in b.pairs if len(b.targets(manifest.pair_of(k))) == 2]
    assert len(turned) == 38
    for pair in turned:
        move = b.targets(pair)[-1]
        assert s.uses[pair, TURN] > 0 and s.uses[pair, move] == s.uses[pair, TURN]
    for label, name in s.routines.items():
        got = Counter(
            (n.args["main"], n.args["sub"])
            for n in ir.walk(s.edits.routines[label])
            if isinstance(n, ir.Insn) and n.op.name == "enter"
        )
        want: Counter[tuple[int, int]] = Counter()
        for pair in expanded(zinogre.donor, zinogre.donor.routines[name].body):
            for t in b.targets(pair):
                want[(OWN, zinogre.slots[t]) if isinstance(t, str) else t] += 1
        assert got == want, label


def test_zinogre_brain_file_round_trip(zinogre, data, tmp_path):
    s, built = splice_of(zinogre, zinogre.m.brain)
    bf = brainfile.new(75)
    bf.put(s.edits)
    path = brainfile.save(bf, tmp_path / "em75.toml")
    again = build.compile(brainfile.load(path), data.fu, 75, lift=zinogre.host)
    assert again.id == built.id


def test_the_charge_table(zinogre):
    ch = zinogre.charge
    assert ch is not None
    assert ch.ups == {
        "p3_1_6": (10, 15, 15, 20),
        "p3_1_15": (25, 35, 40, 55),
        "charge_up": (40, 55, 65, 100),
        "p3_1_17": (55, 75, 100, 100),
        "p3_1_3": (50, 50, 50, 50),
        "p3_1_20": (50, 50, 50, 50),
    }
    assert ch.transform == "p3_1_4" and (ch.chip_gauge, ch.chip_durability) == (5, 5)
    assert (ch.cooldown_up, ch.cooldown_out) == (420, 900)
    assert ch.discharge == ((4, 16), (4, 29), (4, 30), (4, 31))
    assert zinogre.said == [], "every charge-up pair has its own move"


def test_the_fatigue_table(zinogre, data):
    fat, found = fatigue.table(zinogre.m, data)
    assert fat is not None and not [f for f in found if f.level == "error"]
    assert fat.pairs[4, 16] == 200 and {"p3_7_12", "p3_7_94", "p3_7_85"} <= set(fat.own)


@pytest.fixture(scope="module")
def module_text(zinogre, data, tmp_path_factory):
    where = tmp_path_factory.mktemp("zinogre")
    m = zinogre.m  # built as the CLI does: after its own moves are derived
    built = build_port(m, data)
    modules(m, built, where, data)
    return (where / moves.module_name(m)).read_text(), built


def test_the_moves_module_carries_fatigue_and_charge(module_text):
    text, _ = module_text
    lines = {line.split(" = ")[0].strip(): line for line in text.splitlines()}
    assert "fatigue" in lines and "calm = true" in lines["fatigue"]
    assert lines["charge"].strip() == (
        "charge = { ups = { charge_up = { 40, 55, 65, 100 }, p3_1_15 = { 25, 35, 40, 55 }, "
        "p3_1_17 = { 55, 75, 100, 100 }, p3_1_20 = { 50, 50, 50, 50 }, "
        "p3_1_3 = { 50, 50, 50, 50 }, p3_1_6 = { 10, 15, 15, 20 } }, "
        'transform = "p3_1_4", chip_gauge = 5, chip_durability = 5, cooldown_up = 420, '
        "cooldown_out = 900, discharge = { { 4, 16 }, { 4, 29 }, { 4, 30 }, { 4, 31 } } },"
    )


def test_the_own_moves_fit_the_framework(zinogre, module_text):
    """The slots and the pools the registry holds: sharing equal runs is what fits the port."""
    _, built = module_text
    names = [n for n, mv in zinogre.m.moves.items() if mv.own]
    held = moves.pools(zinogre.m, built.layout)
    assert len(names) == 125
    assert (len(held.keys), len(held.samples), len(held.lunges)) == (4218, 1052, 10)
    assert not moves.problems(zinogre.m, built.layout, moves.records(Data.find().fu, 75))


def test_the_cli_splices_the_port(data, capsys):
    args = ["--data", str(data.fu.root), "--p3rd-data", str(data.p3rd.root)]
    assert main(["splice", str(PORT), *args]) == 0
    out = capsys.readouterr().out
    assert "routines added" in out and "worst run" in out
    assert "  charge: charge_up 40/55/65/100, p3_1_15 25/35/40/55, " in out
    assert "  pair 2,12 -> own p3_2_12 (slot " in out and "  pair 2,25 -> own p3_2_23 (slot " in out
    assert "  pair 7,23 -> 1,2  x" in out and "  pair 7,23 -> own p3_7_0 (slot " in out
