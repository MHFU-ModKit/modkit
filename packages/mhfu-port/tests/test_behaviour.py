# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import dataclasses
from pathlib import Path

import pytest
from mhfu import addresses
from mhfu_port import behaviour as B
from mhfu_port import manifest as M
from mhfu_port import sequence
from mhfu_port.behaviour import Block, ManifestError, MoveNode, Rule

PORTS = Path(__file__).parents[3] / "ports"

BASE = """
[port]
name = "x"
host_species = 75
pac = "x.bin"
[source]
model = 100
[moves.m]
main = 1
sub = 4
anim = 17
[moves.n]
main = 1
sub = 5
anim = 18
[moves.o]
anim = 46
"""


def blk(kind, x=0, y=0, *, nxt=(), play=(), label="", **params):
    return Block(kind, (x, y), params, list(nxt), list(play), label)


def graph(blocks, moves=None, extra=""):
    m = M.loads(BASE + extra)
    m.behaviour = B.Behaviour(blocks, moves or {})
    return m


def plays(m):
    return [r.play for r in B.compile(m)]


# the schema


def test_kinds_are_the_events():
    events = {k for k, v in B.KINDS.items() if v.role == "event"}
    assert events == {f"on_{e}" for e in M.EVENTS} | {"on_signal"}
    assert [B.event_of(k) for k in ("on_flinch", "on_signal", "force")] == ["flinch", None, None]
    titles = {k: B.KINDS[f"on_{k}"].title for k in M.EVENTS}
    assert titles == {
        "noticed": "On notice",
        "combat_entered": "On combat start",
        "combat_left": "On combat end",
        "flinch": "On flinch",
        "part_broken": "On part break",
        "tail_cut": "On tail cut",
        "enraged": "On enraged",
        "calmed": "On calmed",
    }
    for event in M.EVENTS:
        has_part = bool(B.KINDS[f"on_{event}"].params)
        assert has_part == (event in M.PART_EVENTS)


def test_every_kind_has_a_rule_field():
    rest = {k for k, v in B.KINDS.items() if v.role != "event"}
    assert rest == set(B._APPLY) | set(B._CONDS) | set(B._EFFECTS)
    assert not set(B._APPLY) & B.REPEATS


def _sink(way):
    chain = [
        ("on_part_broken", {"part": 1}),
        ("host_state", {"mains": [1, 2]}),
        ("played_for", {"frames": 5}),
        ("distance", {"lo": 10, "hi": 20}),
        ("hunter_moving", {"way": way}),
        ("mode", {"mode": 3}),
        ("force", {}),
        ("cooldown", {"frames": 7}),
        ("limit", {"times": 2}),
    ]
    return B.compile(_chain(*(blk(kind, **params) for kind, params in chain)))[0]


def test_kinds_cover_the_rule():
    away, closer = _sink("away"), _sink("closer")
    assert (away.receding, away.closing, closer.receding, closer.closing) == (1, 0, 0, 1)
    base = Rule("")
    moved = {
        f.name
        for f in dataclasses.fields(Rule)
        if getattr(away, f.name) != getattr(base, f.name)
        or getattr(closer, f.name) != getattr(base, f.name)
    }
    ops = {"conds", "effects", "signal"}
    assert moved == {f.name for f in dataclasses.fields(Rule)} - {"from_move", "label"} - ops


def test_params_dont_shadow_a_field():
    fields = {f.name for f in dataclasses.fields(Block)}
    assert not {p.name for k in B.KINDS.values() for p in k.params} & fields


def test_kinds_have_titles_and_tips():
    for k in B.KINDS.values():
        assert k.title and k.tip, k.name
        assert all(p.title and p.tip for p in k.params), k.name
    assert B.KINDS["force"].params == ()


def test_defaults_fill_in():
    assert B.params(blk("distance")) == {"lo": 0.0}
    assert B.params(blk("distance", hi=9, lo=2)) == {"lo": 2, "hi": 9}


# paths


def test_a_chain_is_a_path():
    m = graph({"b1": blk("on_noticed", nxt=["b2"]), "b2": blk("force", play=["m"])})
    assert B.paths(m) == [B.Path(("b1", "b2"), "m", None)]
    assert B.compile(m) == [Rule("m", on="noticed", force=True)]


def test_fan_out_and_play_lists():
    m = graph(
        {
            "b1": blk("on_noticed", nxt=["b2", "b3"]),
            "b2": blk("force", play=["m", "n"]),
            "b3": blk("limit", times=1, play=["o"]),
        }
    )
    assert len(B.paths(m)) == 3
    assert sorted(p.play for p in B.paths(m)) == ["m", "n", "o"]


def test_a_block_that_plays_and_feeds():
    m = graph({"b1": blk("on_noticed", nxt=["b2"], play=["m"]), "b2": blk("force", play=["n"])})
    assert [(p.blocks, p.play) for p in B.paths(m)] == [(("b1",), "m"), (("b1", "b2"), "n")]


def test_fan_in():
    m = graph(
        {
            "b1": blk("on_noticed", nxt=["b3"]),
            "b2": blk("host_state", y=100, nxt=["b3"], mains=[1]),
            "b3": blk("cooldown", x=200, frames=9, play=["m"]),
        }
    )
    assert [p.blocks for p in B.paths(m)] == [("b1", "b3"), ("b2", "b3")]


def test_diamond():
    m = graph(
        {
            "b1": blk("on_noticed", nxt=["b2", "b3"]),
            "b2": blk("force", x=100, y=0, nxt=["b4"]),
            "b3": blk("limit", x=100, y=100, times=1, nxt=["b4"]),
            "b4": blk("cooldown", x=200, frames=9, play=["m"]),
        }
    )
    assert [p.blocks for p in B.paths(m)] == [("b1", "b2", "b4"), ("b1", "b3", "b4")]


def test_during_feeds_a_chain():
    m = graph(
        {"b1": blk("played_for", frames=20, play=["n"])},
        {"m": MoveNode((0, 0), ["b1"])},
    )
    assert B.paths(m) == [B.Path(("b1",), "n", "m")]
    assert B.compile(m) == [Rule("n", from_move="m", min_frames=20)]


def test_one_block_in_two_moves():
    m = graph(
        {"b1": blk("on_noticed", play=["o"])},
        {"m": MoveNode((0, 0), ["b1"]), "n": MoveNode((0, 100), ["b1"])},
    )
    assert [(p.during, p.play) for p in B.paths(m)] == [("m", "o"), ("n", "o")]
    assert [r.from_move for r in B.compile(m)] == ["m", "n"]
    assert B.loose(m) == []


def test_a_fed_event_is_not_a_source():
    m = graph({"b1": blk("host_state", mains=[1], nxt=["b2"]), "b2": blk("on_noticed", play=["m"])})
    assert [p.blocks for p in B.paths(m)] == [("b1", "b2")]


def test_labels_join():
    m = graph(
        {
            "b1": blk("on_noticed", nxt=["b2", "b3"], label="a"),
            "b2": blk("force", play=["m"], label="b"),
            "b3": blk("limit", times=2, play=["m"]),
        }
    )
    assert [r.label for r in B.compile(m)] == ["a; b", "a"]


# priority


def test_higher_on_the_canvas_first():
    m = graph(
        {
            "b1": blk("on_noticed", y=200, play=["m"]),
            "b2": blk("on_combat_left", y=0, play=["n"]),
            "b3": blk("on_tail_cut", y=100, play=["o"]),
        }
    )
    assert plays(m) == ["n", "o", "m"]
    m.behaviour.blocks["b1"].at = (0, -5)
    assert plays(m) == ["m", "n", "o"]


def test_left_wins_a_row():
    m = graph(
        {
            "b1": blk("on_noticed", x=300, play=["m"]),
            "b2": blk("on_combat_entered", x=100, play=["n"]),
        }
    )
    assert plays(m) == ["n", "m"]


def test_later_blocks_and_moves_break_ties():
    m = graph(
        {
            "b1": blk("on_noticed", nxt=["b2", "b3"]),
            "b2": blk("force", y=50, play=["m"]),
            "b3": blk("limit", y=10, times=1, play=["n"]),
            "b4": blk("on_combat_left", x=500, play=["m", "n"]),
        },
        {"n": MoveNode((0, 0)), "m": MoveNode((0, 100))},
    )
    assert [(p.blocks, p.play) for p in B.paths(m)] == [
        (("b1", "b3"), "n"),
        (("b1", "b2"), "m"),
        (("b4",), "n"),
        (("b4",), "m"),
    ]
    m.behaviour.moves["m"].at = (0, -1)
    assert [p.play for p in B.paths(m)][2:] == ["m", "n"]


# loose


def test_loose_is_a_finding_not_an_error():
    m = graph(
        {
            "b1": blk("played_for", frames=3, nxt=["b2"]),
            "b2": blk("cooldown", frames=3, play=["m"]),
            "b3": blk("on_noticed"),
            "b4": blk("on_combat_left", play=["n"]),
        }
    )
    assert B.loose(m) == ["b1", "b2", "b3"]
    assert plays(m) == ["n"]
    back = M.loads(M.dumps(m))
    assert back == m and B.loose(back) == ["b1", "b2", "b3"]


# path errors


def test_a_path_with_a_kind_twice():
    m = graph({"b1": blk("on_noticed", nxt=["b2"]), "b2": blk("force", nxt=["b3"])})
    m.behaviour.blocks["b3"] = blk("force", play=["m"])
    with pytest.raises(ManifestError, match="b1 > b2 > b3 plays m: has two 'Right away' blocks"):
        B.compile(m)


def test_a_path_with_two_events():
    m = graph({"b1": blk("on_noticed", nxt=["b2"]), "b2": blk("on_flinch", play=["o"])})
    with pytest.raises(ManifestError, match="more than one event"):
        B.compile(m)


def _chain(*blocks, play="m", moves=None):
    """The blocks wired in a line, the last playing `play` (None: it plays nothing)."""
    ids = [f"b{i}" for i in range(1, len(blocks) + 1)]
    for here, there in zip(ids, ids[1:], strict=False):
        blocks[ids.index(here)].next.append(there)
    if play is not None:
        blocks[-1].play.append(play)
    return graph(dict(zip(ids, blocks, strict=True)), moves)


@pytest.mark.parametrize(
    ("m", "why"),
    [
        (_chain(blk("on_noticed"), blk("distance", lo=5, hi=5)), "dist needs 0 <= lo < hi"),
        (_chain(blk("on_noticed"), blk("distance", lo=9, hi=5)), "dist needs 0 <= lo < hi"),
        (_chain(blk("on_noticed"), blk("distance", lo=0, hi=0)), "dist needs 0 <= lo < hi"),
        (_chain(blk("on_noticed"), blk("mode", mode=1), play="o"), "mode is a pair's"),
        (_chain(blk("on_flinch"), play="m"), "on = flinch plays an own move"),
        (
            _chain(blk("played_for", frames=1), play="m", moves={"m": MoveNode((0, 0), ["b1"])}),
            "from and play are the same move",
        ),
    ],
)
def test_path_errors(m, why):
    M.check(m)  # the graph loads; its paths do not compile
    with pytest.raises(ManifestError, match=why):
        B.compile(m)


def test_refused_names_each_block_of_a_bad_path():
    m = graph({"b1": blk("on_noticed", nxt=["b2"]), "b2": blk("force", nxt=["b3"])})
    m.behaviour.blocks["b3"] = blk("force", play=["m"])
    m.behaviour.blocks["b4"] = blk("on_flinch", play=["o"])
    why = {i: "has two 'Right away' blocks" for i in ("b1", "b2", "b3")}
    assert B.refused(m) == why
    with pytest.raises(ManifestError, match=why["b1"]):
        B.compile(m)
    assert B.refused(graph({"b1": blk("on_noticed", play=["m"])})) == {}


def test_a_block_keeps_the_first_reason():
    m = graph({"b1": blk("on_noticed", nxt=["b2", "b3"], play=["m"])})
    m.behaviour.blocks["b2"] = blk("distance", lo=5, hi=5, play=["m"])
    m.behaviour.blocks["b3"] = blk("mode", mode=1, play=["o"])
    assert B.refused(m) == {
        "b1": "dist needs 0 <= lo < hi",
        "b2": "dist needs 0 <= lo < hi",
        "b3": "mode is a pair's: an own move enters its carrier",
    }


def test_the_seam_holds_so_many_paths():
    blocks = {f"b{i}": blk("on_noticed", y=i, play=["m"]) for i in range(M.SEAM_RULES)}
    assert len(B.compile(graph(blocks))) == M.SEAM_RULES
    blocks["more"] = blk("on_flinch", play=["o"])
    with pytest.raises(ManifestError, match=f"{M.SEAM_RULES + 1} paths, the seam holds"):
        B.compile(graph(blocks))


# editing


def test_new_id_is_the_lowest_free():
    b = B.Behaviour()
    assert B.new_id(b) == "b1"
    b.blocks |= {"b1": blk("force"), "b3": blk("force")}
    assert B.new_id(b) == "b2"
    del b.blocks["b1"]
    assert B.new_id(b) == "b1"


def _wired():
    return graph(
        {"b1": blk("on_noticed", play=["m", "o"]), "b2": blk("force", play=["n"])},
        {"m": MoveNode((0, 0), ["b2"]), "o": MoveNode((0, 9))},
    )


def test_uses():
    m = _wired()
    assert B.uses(m, "m") == ["block b1 plays it", "while m plays"]
    assert B.uses(m, "n") == ["block b2 plays it"]
    assert B.uses(m, "o") == ["block b1 plays it"]
    assert B.uses(graph({}), "m") == []


def test_rename_move():
    m = _wired()
    B.rename_move(m, "m", "k")
    assert m.behaviour.blocks["b1"].play == ["k", "o"]
    assert list(m.behaviour.moves) == ["k", "o"] and m.behaviour.moves["k"].during == ["b2"]
    with pytest.raises(ManifestError, match="already exists"):
        B.rename_move(m, "k", "o")


def test_drop_move():
    m = _wired()
    B.drop_move(m, "m")
    assert m.behaviour.blocks["b1"].play == ["o"] and list(m.behaviour.moves) == ["o"]
    assert B.uses(m, "m") == []


# conditions and effects

CONDS = [
    ("monster_hp", {"lo": 20}, [("hp_at_least", 0, 20)]),
    ("monster_hp", {"hi": 30}, [("hp_below", 0, 30)]),
    ("monster_hp", {"lo": 20, "hi": 30}, [("hp_at_least", 0, 20), ("hp_below", 0, 30)]),
    ("monster_hp", {}, []),
    ("part_broken", {"part": 4}, [("broken", 4, 0)]),
    ("part_broken", {"part": 4, "state": "not broken"}, [("not_broken", 4, 0)]),
    ("rage", {}, [("enraged", 0, 0)]),
    ("rage", {"state": "calm"}, [("calm", 0, 0)]),
    ("hunter_side", {"sides": ["front"]}, [("side", 1, 0)]),
    ("hunter_side", {"sides": ["behind", "left", "left"]}, [("side", 10, 0)]),
    ("hunter_side", {"sides": list(B.SIDES)}, [("side", 15, 0)]),
    ("chance", {"percent": 25}, [("chance", 0, 25)]),
    ("counter_is", {"counter": "n"}, [("var_at_least", 0, 1)]),
    ("counter_is", {"counter": "n", "test": "below", "value": -3}, [("var_below", 0, -3)]),
    ("counter_is", {"counter": "n", "test": "exactly", "value": 2}, [("var_equal", 0, 2)]),
    ("flag_is", {"flag": "n"}, [("var_at_least", 0, 1)]),
    ("flag_is", {"flag": "n", "state": "clear"}, [("var_below", 0, 1)]),
]
EFFECTS = [
    ("counter_add", {"counter": "n"}, [("var_add", 0, 1)]),
    ("counter_add", {"counter": "n", "by": -2}, [("var_add", 0, -2)]),
    ("counter_set", {"counter": "n"}, [("var_set", 0, 0)]),
    ("counter_set", {"counter": "n", "to": 7}, [("var_set", 0, 7)]),
    ("flag_set", {"flag": "n"}, [("var_set", 0, 1)]),
    ("flag_set", {"flag": "n", "state": "clear"}, [("var_set", 0, 0)]),
    ("enrage", {}, [("enrage", 0, 0)]),
    ("calm", {}, [("calm", 0, 0)]),
]


@pytest.mark.parametrize(("kind", "params", "ops"), CONDS)
def test_a_condition_compiles(kind, params, ops):
    r = B.compile(_chain(blk("on_noticed"), blk(kind, **params)))[0]
    assert (r.conds, r.effects, r.signal) == (ops, [], None)


@pytest.mark.parametrize(("kind", "params", "ops"), EFFECTS)
def test_an_effect_compiles(kind, params, ops):
    r = B.compile(_chain(blk("on_noticed"), blk(kind, **params)))[0]
    assert (r.conds, r.effects, r.play) == ([], ops, "m")


def test_every_op_of_the_contract_is_made():
    assert {op for _, _, ops in CONDS for op, _, _ in ops} == set(addresses.EM_COND.names)
    assert {op for _, _, ops in EFFECTS for op, _, _ in ops} == set(addresses.EM_EFFECT.names)


def test_idle_and_any_time_are_main_states():
    assert B.compile(_chain(blk("idle")))[0].from_main == [B.IDLE_MAIN] == [0]
    assert B.compile(_chain(blk("any_time")))[0].from_main == list(B.MAIN_STATES)


def test_one_state_block_a_path():
    m = _chain(blk("idle"), blk("host_state", mains=[1]))
    with pytest.raises(ManifestError, match="b1 > b2 plays m: has more than one state block"):
        B.compile(m)
    with pytest.raises(ManifestError, match="has two 'Base monster idle' blocks"):
        B.compile(_chain(blk("idle"), blk("idle")))


def _repeat(n, kind, **params):
    return _chain(blk("on_noticed"), *[blk(kind, **params) for _ in range(n)])


def test_conditions_and_effects_repeat_within_the_caps():
    r = B.compile(_repeat(B.SEAM_CONDS, "chance", percent=50))[0]
    assert len(r.conds) == B.SEAM_CONDS == addresses.EM_RULE.CONDS.count
    r = B.compile(_repeat(B.SEAM_EFFECTS, "counter_add", counter="n"))[0]
    assert len(r.effects) == B.SEAM_EFFECTS == addresses.EM_RULE.EFFECTS.count


def test_the_caps_are_refused():
    n = B.SEAM_CONDS + 1
    with pytest.raises(ManifestError, match=f"{n} conditions, a rule holds {B.SEAM_CONDS}"):
        B.compile(_repeat(n, "chance", percent=50))
    n = B.SEAM_EFFECTS + 1
    with pytest.raises(ManifestError, match=f"{n} effects, a rule holds {B.SEAM_EFFECTS}"):
        B.compile(_repeat(n, "counter_add", counter="n"))


def test_the_caps_count_ops_not_blocks():
    three = _chain(blk("on_noticed"), blk("monster_hp", lo=5, hi=9), blk("chance", percent=5))
    assert len(B.compile(three)[0].conds) == 3
    blocks = [blk("monster_hp", lo=5, hi=9)]
    blocks += [blk("chance", percent=5) for _ in range(B.SEAM_CONDS - 1)]
    with pytest.raises(ManifestError, match=f"{B.SEAM_CONDS + 1} conditions"):
        B.compile(_chain(blk("on_noticed"), *blocks))


def test_the_rule_fields_stay_once_a_path():
    m = _chain(blk("on_noticed"), blk("played_for", frames=1), blk("played_for", frames=2))
    with pytest.raises(ManifestError, match="has two 'Played for at least' blocks"):
        B.compile(m)


@pytest.mark.parametrize(("lo", "hi"), [(30, 30), (40, 30), (0, 0)])
def test_monster_hp_needs_lo_under_hi(lo, hi):
    m = _chain(blk("on_noticed"), blk("monster_hp", lo=lo, hi=hi))
    M.check(m)
    with pytest.raises(ManifestError, match="b1 > b2 plays m: hp needs lo < hi"):
        B.compile(m)
    assert B.refused(m) == {"b1": "hp needs lo < hi", "b2": "hp needs lo < hi"}


@pytest.mark.parametrize(
    ("kind", "params", "why"),
    [
        ("counter_add", {"counter": "Bad"}, "is not a name"),
        ("counter_add", {"counter": "1up"}, "is not a name"),
        ("counter_add", {"counter": "a b"}, "is not a name"),
        ("counter_add", {"counter": ""}, "is not a name"),
        ("counter_add", {"counter": 3}, "is not a name"),
        ("counter_add", {}, "counter: missing"),
        ("flag_is", {"flag": "no-way"}, "is not a name"),
        ("on_signal", {"name": "Roar!"}, "is not a name"),
        ("on_signal", {}, "name: missing"),
        ("hunter_side", {"sides": []}, "is not a list of sides"),
        ("hunter_side", {"sides": ["up"]}, "is not a list of sides"),
        ("hunter_side", {"sides": "front"}, "is not a list of sides"),
        ("hunter_side", {}, "sides: missing"),
        ("chance", {"percent": 0}, "0 is under 1"),
        ("chance", {"percent": 101}, "101 is over 100"),
        ("monster_hp", {"lo": 101}, "101 is over 100"),
        ("monster_hp", {"hi": -1}, "-1 is under 0"),
        ("part_broken", {"part": 99}, "is not a part"),
        ("part_broken", {"part": 1, "state": "maybe"}, "expected one of"),
        ("counter_is", {"counter": "n", "value": 40000}, "40000 is over 32767"),
        ("counter_is", {"counter": "n", "test": "above"}, "expected one of"),
        ("counter_add", {"counter": "n", "by": 1.5}, "expected an integer"),
    ],
)
def test_bad_params(kind, params, why):
    with pytest.raises(ManifestError, match=why):
        M.check(graph({"b1": blk(kind, **params)}))


# no-play paths


def test_a_path_ending_at_an_effect_plays_nothing():
    m = _chain(blk("on_flinch"), blk("counter_add", counter="n"), play=None)
    assert B.paths(m) == [B.Path(("b1", "b2"), None, None)]
    assert B.compile(m) == [Rule(None, on="flinch", effects=[("var_add", 0, 1)])]
    assert B.loose(m) == [] and B.refused(m) == {}


def test_an_effect_that_plays_or_feeds_ends_no_path_of_its_own():
    m = _chain(blk("idle"), blk("counter_set", counter="n"))
    assert [p.play for p in B.paths(m)] == ["m"]
    m = _chain(blk("idle"), blk("counter_set", counter="n"), blk("chance", percent=9), play=None)
    assert B.paths(m) == [] and B.loose(m) == ["b1", "b2", "b3"]
    m.behaviour.blocks["b3"] = blk("counter_add", counter="n")
    m.behaviour.blocks["b2"].next = ["b3"]
    assert [p.blocks for p in B.paths(m)] == [("b1", "b2", "b3")]
    assert B.compile(m)[0].effects == [("var_set", 0, 0), ("var_add", 0, 1)]


def test_a_block_that_plays_and_ends_a_no_play_path_makes_both():
    m = graph(
        {
            "b1": blk("on_flinch", nxt=["b2", "b3"]),
            "b2": blk("counter_add", x=1, counter="n", play=["m"]),
            "b3": blk("counter_add", x=2, counter="n"),
        }
    )
    assert [(p.blocks, p.play) for p in B.paths(m)] == [(("b1", "b2"), "m"), (("b1", "b3"), None)]


def test_a_non_effect_end_without_a_move_is_loose():
    m = _chain(blk("on_flinch"), blk("force"), play=None)
    assert B.paths(m) == [] and B.loose(m) == ["b1", "b2"]


def test_a_move_can_feed_a_no_play_path():
    m = graph({"b1": blk("flag_set", flag="seen")}, {"m": MoveNode((0, 0), ["b1"])})
    assert B.paths(m) == [B.Path(("b1",), None, "m")]
    assert B.compile(m) == [Rule(None, from_move="m", effects=[("var_set", 0, 1)])]


def test_a_no_play_path_waits_and_counts_like_any():
    m = _chain(
        blk("on_flinch"),
        blk("cooldown", frames=30),
        blk("limit", times=2),
        blk("counter_add", counter="n"),
        play=None,
    )
    assert [(r.play, r.cooldown, r.count) for r in B.compile(m)] == [(None, 30, 2)]


def test_a_no_play_path_refuses_force_and_mode():
    for extra in (blk("force"), blk("mode", mode=1)):
        m = _chain(blk("on_flinch"), extra, blk("counter_add", counter="n"), play=None)
        M.check(m)
        with pytest.raises(ManifestError, match="plays nothing: force and mode go with a move"):
            B.compile(m)
        assert set(B.refused(m)) == {"b1", "b2", "b3"}


def test_a_rule_that_plays_nothing_needs_an_effect():
    m = _chain(blk("on_flinch"), blk("chance", percent=5), play=None)
    with pytest.raises(ManifestError, match="plays nothing and has no effect block"):
        B._rule(m, B.Path(("b1", "b2"), None, None))


def test_no_play_paths_sort_with_the_rest():
    m = graph(
        {
            "b1": blk("on_noticed", y=100, play=["m"]),
            "b2": blk("on_flinch", y=0, nxt=["b3"]),
            "b3": blk("counter_add", x=300, y=0, counter="n"),
            "b4": blk("on_tail_cut", y=200, play=["n"]),
        }
    )
    assert plays(m) == [None, "m", "n"]


# the board


def test_vars_and_signals_number_by_sorted_name():
    m = graph(
        {
            "b1": blk("on_signal", y=0, name="roar", play=["m"]),
            "b2": blk("on_signal", y=50, name="alarm", play=["n"]),
            "b3": blk("counter_is", y=100, counter="zeta", nxt=["b4"]),
            "b4": blk("counter_add", x=300, y=100, counter="alpha"),
            "b5": blk("flag_set", y=500, flag="mid"),
        }
    )
    assert B.signals(m) == {"alarm": 0, "roar": 1}
    assert B.vars(m) == {"alpha": 0, "mid": 1, "zeta": 2}, "a loose block's name counts"
    assert [r.signal for r in B.compile(m)] == [1, 0]
    assert B.vars(graph({})) == B.signals(graph({})) == {}


def test_names_are_every_value_of_the_type_sorted(monkeypatch):
    kind = B.Kind("t_boss", "effect", "Boss", (B.Param("flag", "var", "Flag", "boss"),))
    monkeypatch.setitem(B.KINDS, "t_boss", kind)
    m = graph(
        {
            "b1": blk("counter_add", counter="hits"),
            "b2": blk("flag_set", flag="armor"),
            "b3": blk("counter_is", counter="hits"),
            "b4": blk("t_boss"),
            "b5": blk("on_signal", name="rage"),
            "b6": blk("hunter_side", sides=["front"]),
            "b7": blk("no_such_kind", name="x"),
        }
    )
    assert B.names(m, "var") == ["armor", "boss", "hits"], "a default counts"
    assert B.names(m, "signal") == ["rage"]
    assert B.names(m, "sides") == B.names(m, "int") == B.names(graph({}), "var") == []
    assert B.vars(m) == {"armor": 0, "boss": 1, "hits": 2} and B.signals(m) == {"rage": 0}


def test_a_name_is_one_index_everywhere():
    m = _chain(
        blk("on_flinch"),
        blk("counter_is", counter="b", test="below", value=3),
        blk("counter_add", counter="a"),
        blk("counter_set", counter="b", to=1),
        play=None,
    )
    r = B.compile(m)[0]
    assert B.vars(m) == {"a": 0, "b": 1}
    assert (r.conds, r.effects) == (
        [("var_below", 1, 3)],
        [("var_add", 0, 1), ("var_set", 1, 1)],
    )


def test_a_signal_rule():
    m = _chain(blk("on_signal", name="roar"))
    assert B.compile(m) == [Rule("m", signal=0)]
    with pytest.raises(ManifestError, match="more than one event block"):
        B.compile(_chain(blk("on_signal", name="roar"), blk("on_flinch")))


def test_the_board_holds_so_many():
    def many(n, kind, key, play=None):
        blocks = {}
        for i in range(n):
            blocks[f"e{i}"] = blk("on_flinch", y=i, nxt=[f"c{i}"])
            blocks[f"c{i}"] = blk(kind, x=300, y=i, **{key: f"v{i}"}, play=[play] if play else [])
        return graph(blocks)

    assert len(B.vars(many(B.BOARD_VARS, "counter_add", "counter"))) == B.BOARD_VARS == 16
    B.compile(many(B.BOARD_VARS, "counter_add", "counter"))
    n = B.BOARD_VARS + 1
    with pytest.raises(ManifestError, match=f"{n} counters and flags, the board holds 16"):
        B.compile(many(n, "flag_set", "flag"))
    sigs = {
        f"s{i}": blk("on_signal", y=i, name=f"s{i}", play=["m"]) for i in range(B.BOARD_SIGNALS)
    }
    B.compile(graph(sigs))
    sigs["more"] = blk("on_signal", y=99, name="more", play=["m"])
    with pytest.raises(ManifestError, match=f"{B.BOARD_SIGNALS + 1} signals, the board holds 16"):
        B.compile(graph(sigs))


def test_the_new_blocks_survive_a_round_trip():
    m = graph(
        {
            "b1": blk("on_signal", name="roar", nxt=["b2"]),
            "b2": blk("hunter_side", x=300, sides=["left", "behind"], nxt=["b3"]),
            "b3": blk("counter_is", x=600, counter="n", test="exactly", value=-4, nxt=["b4"]),
            "b4": blk("counter_add", x=900, counter="n", by=2, play=["m"]),
        }
    )
    back = M.loads(M.dumps(m))
    assert back == m and B.compile(back) == B.compile(m)


# schema 1

V1 = (
    """
schema = 1
"""
    + BASE
    + """
[[rule]]
from_main = [1, 0]
min_frames = 30
dist = [100, 700]
closing = true
mode = 2
cooldown = 90
count = 3
play = "m"
label = "close in"

[[rule]]
from = "o"
dist = [0, 500]
play = "n"

[[rule]]
from = "o"
count = 1
play = "m"

[[rule]]
on = "flinch"
part = 2
force = true
play = "o"
label = "flinch"

[[rule]]
from = "m"
on = "noticed"
dist = [50, 1.0e9]
receding = true
play = "n"
"""
)
V1_RULES = [
    Rule(
        "m",
        from_main=[1, 0],
        min_frames=30,
        dist=(100.0, 700.0),
        closing=True,
        mode=2,
        cooldown=90,
        count=3,
        label="close in",
    ),
    Rule("n", from_move="o", dist=(0.0, 500.0)),
    Rule("m", from_move="o", count=1),
    Rule("o", on="flinch", part=2, force=True, label="flinch"),
    Rule("n", on="noticed", from_move="m", dist=(50.0, B.UNLIMITED_DIST), receding=True),
]


def test_v1_keeps_the_rules_in_order():
    m = M.loads(V1)
    assert B.compile(m) == V1_RULES
    assert B.loose(m) == []
    back = M.dumps(m)
    assert back.startswith("schema = 2\n") and "[[rule]]" not in back
    assert B.compile(M.loads(back)) == V1_RULES


def test_migrate_lays_a_chain_a_row():
    m = M.loads(V1)
    b = m.behaviour
    first = [blk_.at for blk_ in b.blocks.values() if blk_.at[0] == B.LEFT]
    assert first == [(B.LEFT, i * B.ROW) for i in range(5)]
    assert [x.kind for x in list(b.blocks.values())[:6]] == [
        "host_state",
        "played_for",
        "distance",
        "hunter_moving",
        "mode",
        "cooldown",
    ]
    assert b.blocks["b1"].label == "close in" and b.blocks["b2"].label == ""
    assert b.moves["o"].during == ["b8", "b9"] and b.moves["o"].at[0] == 0
    assert b.moves["n"].at[0] == B.LEFT + 7 * B.COLUMN


def test_migrate_grid_clears_the_nodes():
    """The studio draws a block up to 241 by 174 and a move 355 by 77."""
    assert B.COLUMN > 241 and B.ROW > 174 and B.LEFT > 355


def test_migrate_order_is_the_old_order_at_the_seam():
    rules = [{"play": "m", "on": "noticed", "cooldown": i + 1} for i in range(M.SEAM_RULES)]
    got = [r.cooldown for r in B.compile(_graph_of(rules))]
    assert got == list(range(1, M.SEAM_RULES + 1))


def _graph_of(rules):
    m = M.loads(BASE)
    m.behaviour = B.migrate(rules)
    return m


@pytest.mark.parametrize(
    ("rules", "why"),
    [
        (
            [{"play": "m", "on": "noticed"}, {"play": "n", "from": "m"}],
            r"rule\[1\]: from 'm' alone",
        ),
        ([{"play": "m"}], r"rule\[0\]: needs from, from_main or on"),
        ([{"play": "m", "on": "noticed", "typo": 1}], r"rule\[0\]: unknown key\(s\) typo"),
        ([{"on": "noticed"}], r"rule\[0\]: play: missing"),
    ],
)
def test_migrate_refuses_what_it_cannot_carry(rules, why):
    with pytest.raises(ManifestError, match=why):
        B.migrate(rules)


def test_migrate_has_no_conds_effects_or_signal():
    for key, value in (("conds", [["chance", 0, 5]]), ("effects", []), ("signal", 0)):
        rule = {"play": "m", "on": "noticed", key: value}
        with pytest.raises(ManifestError, match=rf"rule\[0\]: unknown key\(s\) {key}"):
            B.migrate([rule])


def test_migrate_nothing():
    assert B.migrate([]) == B.Behaviour()


# the ports


ZINOGRE = [
    Rule(
        "lunge_stop",
        from_move="lunge",
        min_frames=15,
        dist=(250.0, 1000.0),
        receding=True,
        cooldown=30,
        label="just past you -> skid",
    ),
    Rule("lunge_stop", from_move="lunge", min_frames=75, cooldown=30, label="run budget by time"),
    Rule(
        "stamp",
        from_move="lunge_stop",
        min_frames=20,
        dist=(0.0, 1200.0),
        cooldown=150,
        label="the hunter close once the skid has stood -> stamp",
    ),
    Rule(
        "stamp",
        from_move="dash",
        min_frames=10,
        dist=(0.0, 800.0),
        label="the hunter close during the dash -> stamp",
    ),
    Rule(
        "notice_howl",
        on="noticed",
        force=True,
        label="the notice: his howl at once, forced, so it does not wait for combat",
    ),
    Rule(
        None,
        on="combat_entered",
        effects=[("var_set", 1, 1)],
        label="combat start -> set in_combat",
    ),
    Rule(
        None, on="combat_left", effects=[("var_set", 1, 0)], label="combat end -> clear in_combat"
    ),
    Rule("flinch_head", on="part_broken", part=0, label="horns break -> head flinch"),
    Rule("topple_left", on="part_broken", part=4, label="left foreleg breaks -> topple left"),
    Rule("topple_right", on="part_broken", part=6, label="right foreleg breaks -> topple right"),
    Rule("break_howl", on="part_broken", label="a part breaks"),
    Rule(None, on="flinch", effects=[("var_add", 0, 1)], label="count the flinches"),
    Rule(
        "charge_up",
        from_main=[0],
        conds=[("var_at_least", 1, 1), ("var_at_least", 0, 2)],
        effects=[("var_set", 0, 0), ("enrage", 0, 0)],
        label="2nd flinch -> charge up and enrage",
    ),
    Rule(
        "dash",
        from_main=[0],
        dist=(1500.0, B.UNLIMITED_DIST),
        cooldown=300,
        conds=[("var_at_least", 1, 1)],
        label="hunter far -> dash",
    ),
    Rule(
        "notice_howl",
        from_main=[0],
        conds=[("var_at_least", 1, 1), ("hp_below", 0, 30)],
        count=1,
        label="below 30% HP -> howl",
    ),
    Rule("notice_howl", signal=0, label="a mod's roar -> howl"),
]


def test_zinogre_compiles_to_its_rules():
    m = M.load(PORTS / "zinogre.toml")
    assert B.compile(m) == ZINOGRE
    assert B.loose(m) == [] and B.refused(m) == {}
    assert B.vars(m) == {"flinches": 0, "in_combat": 1} and B.signals(m) == {"roar": 0}
    old = ZINOGRE[:4]
    assert all(not r.conds and not r.effects and r.signal is None for r in old)
    assert len(ZINOGRE) <= B.SEAM_RULES
    idle = [r for r in ZINOGRE if r.from_main == [0]]
    assert len(idle) == 3 and all(("var_at_least", 1, 1) in r.conds for r in idle)
    notice = next(i for i, r in enumerate(ZINOGRE) if r.on == "noticed")
    assert notice < ZINOGRE.index(idle[0]), "the notice scans before the idle paths"


def test_zinogre_topples_end_in_the_stamp():
    m = M.load(PORTS / "zinogre.toml")
    for side in ("left", "right"):
        steps = sequence.chain(m, f"topple_{side}")
        assert steps == [f"topple_{side}", f"topple_{side}_2", f"topple_{side}_3", "stamp"]
        assert all(m.moves[n].own and not m.moves[n].steer.walls for n in steps[:-1])
        assert m.moves[steps[1]].length == 78 and m.moves[steps[1]].carrier is None
    assert sum(mv.own for mv in m.moves.values()) <= M.OWN_MOVES


def test_zinogre_cuts_its_tail_below_half():
    m = M.load(PORTS / "zinogre.toml")
    assert [n for n, p in m.parts.items() if p.sever_below] == ["tail"]
    assert m.parts["tail"].severable and m.parts["tail"].sever_below == 50
    assert m.behaviour.natural_rage


def test_enrage_and_calm_play_nothing_alone():
    m = _chain(blk("on_combat_entered"), blk("enrage"), play=None)
    assert B.compile(m) == [Rule(None, on="combat_entered", effects=[("enrage", 0, 0)])]
    assert B.KINDS["enrage"].role == B.KINDS["calm"].role == "effect"
    assert not B.KINDS["enrage"].params and not B.KINDS["calm"].params
    with pytest.raises(ManifestError, match="unknown param"):
        M.check(graph({"b1": blk("calm", by=1)}))


def test_natural_rage_is_on_unless_the_graph_says():
    m = graph({})
    assert m.behaviour.natural_rage and "natural_rage" not in M.dumps(m)
    m.behaviour.natural_rage = False
    assert "[behaviour]\nnatural_rage = false" in M.dumps(m)
    assert M.loads(M.dumps(m)).behaviour.natural_rage is False


def test_brute_has_no_rules():
    assert B.compile(M.load(PORTS / "brute_tigrex.toml")) == []
