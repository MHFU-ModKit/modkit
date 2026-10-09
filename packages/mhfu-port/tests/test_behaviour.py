# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import dataclasses
from pathlib import Path

import pytest
from mhfu_port import behaviour as B
from mhfu_port import manifest as M
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


def blk(kind, x=0, y=0, *, to=(), play=(), label="", **params):
    return Block(kind, (x, y), params, list(to), list(play), label)


def graph(blocks, moves=None, extra=""):
    m = M.loads(BASE + extra)
    m.behaviour = B.Behaviour(blocks, moves or {})
    return m


def plays(m):
    return [r.play for r in B.compile(m)]


# the schema


def test_kinds_are_the_events():
    events = {k for k, v in B.KINDS.items() if v.role == "event"}
    assert events == {f"on_{e}" for e in M.EVENTS}
    assert [B.event_of(k) for k in ("on_flinch", "force")] == ["flinch", None]
    titles = {k: B.KINDS[f"on_{k}"].title for k in M.EVENTS}
    assert titles == {
        "noticed": "On notice",
        "combat_entered": "On combat start",
        "combat_left": "On combat end",
        "flinch": "On flinch",
        "part_broken": "On part break",
        "tail_cut": "On tail cut",
    }
    for event in M.EVENTS:
        has_part = bool(B.KINDS[f"on_{event}"].params)
        assert has_part == (event in M.PART_EVENTS)


def test_every_kind_has_a_rule_field():
    rest = {k for k, v in B.KINDS.items() if v.role != "event"}
    assert rest == set(B._APPLY)


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
    assert moved == {f.name for f in dataclasses.fields(Rule)} - {"from_move", "label"}


def test_params_dont_shadow_a_field():
    fields = {f.name for f in dataclasses.fields(Block)}
    assert not {p.name for k in B.KINDS.values() for p in k.params} & fields


def test_kinds_have_titles_and_tips():
    for k in B.KINDS.values():
        assert k.title and k.tip, k.name
        assert all(p.title and p.tip for p in k.params), k.name
    assert B.KINDS["force"].params == ()


def test_defaults_fill_in():
    assert B._params(blk("distance")) == {"lo": 0.0}
    assert B._params(blk("distance", hi=9, lo=2)) == {"lo": 2, "hi": 9}


# paths


def test_a_chain_is_a_path():
    m = graph({"b1": blk("on_noticed", to=["b2"]), "b2": blk("force", play=["m"])})
    assert B.paths(m) == [B.Path(("b1", "b2"), "m", None)]
    assert B.compile(m) == [Rule("m", on="noticed", force=True)]


def test_fan_out_and_play_lists():
    m = graph(
        {
            "b1": blk("on_noticed", to=["b2", "b3"]),
            "b2": blk("force", play=["m", "n"]),
            "b3": blk("limit", times=1, play=["o"]),
        }
    )
    assert len(B.paths(m)) == 3
    assert sorted(p.play for p in B.paths(m)) == ["m", "n", "o"]


def test_a_block_that_plays_and_feeds():
    m = graph({"b1": blk("on_noticed", to=["b2"], play=["m"]), "b2": blk("force", play=["n"])})
    assert [(p.blocks, p.play) for p in B.paths(m)] == [(("b1",), "m"), (("b1", "b2"), "n")]


def test_fan_in():
    m = graph(
        {
            "b1": blk("on_noticed", to=["b3"]),
            "b2": blk("host_state", y=100, to=["b3"], mains=[1]),
            "b3": blk("cooldown", x=200, frames=9, play=["m"]),
        }
    )
    assert [p.blocks for p in B.paths(m)] == [("b1", "b3"), ("b2", "b3")]


def test_diamond():
    m = graph(
        {
            "b1": blk("on_noticed", to=["b2", "b3"]),
            "b2": blk("force", x=100, y=0, to=["b4"]),
            "b3": blk("limit", x=100, y=100, times=1, to=["b4"]),
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
    m = graph({"b1": blk("host_state", mains=[1], to=["b2"]), "b2": blk("on_noticed", play=["m"])})
    assert [p.blocks for p in B.paths(m)] == [("b1", "b2")]


def test_labels_join():
    m = graph(
        {
            "b1": blk("on_noticed", to=["b2", "b3"], label="a"),
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
            "b1": blk("on_noticed", to=["b2", "b3"]),
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
            "b1": blk("played_for", frames=3, to=["b2"]),
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
    m = graph({"b1": blk("on_noticed", to=["b2"]), "b2": blk("force", to=["b3"])})
    m.behaviour.blocks["b3"] = blk("force", play=["m"])
    with pytest.raises(ManifestError, match="b1 > b2 > b3 plays m: has two 'Right away' blocks"):
        B.compile(m)


def test_a_path_with_two_events():
    m = graph({"b1": blk("on_noticed", to=["b2"]), "b2": blk("on_flinch", play=["o"])})
    with pytest.raises(ManifestError, match="more than one event"):
        B.compile(m)


def _chain(*blocks, play="m", moves=None):
    ids = [f"b{i}" for i in range(1, len(blocks) + 1)]
    for here, there in zip(ids, ids[1:], strict=False):
        blocks[ids.index(here)].next.append(there)
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
        "flinch_head",
        on="flinch",
        part=0,
        label="a flinch of the head: his own tumble, never the host's (4,x)",
    ),
    Rule("break_howl", on="part_broken", label="a part breaks"),
    Rule(
        "notice_howl",
        on="noticed",
        force=True,
        label="the notice: his howl at once, forced, so it does not wait for combat",
    ),
]


def test_zinogre_compiles_to_its_rules():
    m = M.load(PORTS / "zinogre.toml")
    assert B.compile(m) == ZINOGRE
    assert B.loose(m) == []


def test_brute_has_no_rules():
    assert B.compile(M.load(PORTS / "brute_tigrex.toml")) == []
