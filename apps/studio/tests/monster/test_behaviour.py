# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The behaviour graph as document edits, and what the canvas reads of it."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path

import pytest
from mhfu_port import behaviour as model
from mhfu_port import manifest
from mhfu_port.behaviour import KINDS, Block, Kind, Param, Rule
from mhfu_port.manifest import Manifest, ManifestError
from mhfu_studio.monster import behaviour as B
from mhfu_studio.monster import clips
from mhfu_studio.monster.document import PortDocument

BODY = """
[clips.walk]
slot = 1
frames = 10
[parts.head]
index = 1
[moves.charge]
main = 1
sub = 4
clip = "walk"
[moves.stamp]
clip = "walk"
after = "stamp_2"
[moves.stamp_2]
clip = "walk"
[moves.flinch_head]
clip = "walk"
[behaviour.blocks.b1]
kind = "played_for"
at = [260.0, 0.0]
frames = 20
play = ["stamp"]
[behaviour.moves.charge]
at = [0.0, 0.0]
during = ["b1"]
"""
Open = Callable[[str], PortDocument]


@pytest.fixture
def d(doc: Open) -> PortDocument:
    return doc(BODY)


def undone(d: PortDocument, before: Manifest) -> bool:
    d.undo()
    return d.manifest == before


def wired(m: Manifest) -> set[B.Wire]:
    return set(B.wires(m))


def test_every_kind_starts_valid(d: PortDocument) -> None:
    before = d.manifest
    for n, kind in enumerate(KINDS, 2):
        B.add_block(d, kind, (n, 2 * n))
    blocks = d.manifest.behaviour.blocks
    ids = dict(zip(KINDS, (f"b{n}" for n in range(2, len(KINDS) + 2)), strict=True))
    assert list(blocks) == ["b1", *ids.values()]
    n = list(KINDS).index("on_flinch") + 2
    assert blocks[ids["on_flinch"]].at == (float(n), float(2 * n))
    params = {k: blocks[i].params for k, i in ids.items()}
    assert params["host_state"] == {"mains": [0]}
    assert params["distance"] == {"lo": 0.0}, "the optional far end starts as none"
    assert params["played_for"] == {"frames": 1} and params["hunter_moving"] == {"way": "away"}
    assert params["on_flinch"] == {} and params["force"] == {} and params["mode"] == {"mode": 0}
    for _ in KINDS:
        d.undo()
    assert d.manifest == before


def test_add_refuses_an_unknown_kind(d: PortDocument) -> None:
    with pytest.raises(ManifestError, match="not a kind"):
        B.add_block(d, "nope", (0, 0))


def test_the_four_links(d: PortDocument) -> None:
    before = d.manifest
    B.add_block(d, "on_part_broken", (0, 100))
    B.add_block(d, "distance", (200, 100))
    made = d.manifest
    assert B.link(d, "b2", "out", "b3", "in") == "On part break feeds Hunter distance"
    assert d.manifest.behaviour.blocks["b2"].next == ["b3"] and undone(d, made)
    B.link(d, "b3", "out", B.move_id("flinch_head"), "play")
    assert d.manifest.behaviour.blocks["b3"].play == ["flinch_head"] and undone(d, made)
    B.link(d, B.move_id("stamp"), "while playing", "b3", "in")
    node = d.manifest.behaviour.moves["stamp"]
    assert node.during == ["b3"] and node.at == B.spots(made)["stamp"], "it stays where it sat"
    assert undone(d, made) and "stamp" not in d.manifest.behaviour.moves
    B.link(d, B.move_id("stamp_2"), "then", B.move_id("flinch_head"), "play")
    assert d.manifest.moves["stamp_2"].after == "flinch_head" and undone(d, made)
    d.undo()
    d.undo()
    assert d.manifest == before


def test_then_replaces_the_old_one(d: PortDocument) -> None:
    assert d.manifest.moves["stamp"].after == "stamp_2"
    B.link(d, B.move_id("stamp"), "then", B.move_id("flinch_head"), "play")
    assert d.manifest.moves["stamp"].after == "flinch_head"
    assert B.Wire("move:stamp", "then", "move:stamp_2", "play") not in wired(d.manifest)
    assert B.set_then(d, "stamp", None) == "stamp hands to no move"
    assert d.manifest.moves["stamp"].after is None


@pytest.mark.parametrize(
    ("wire", "why"),
    [
        (("move:stamp", "while playing", "move:charge", "play"), "cannot feed"),
        (("move:stamp", "then", "b1", "in"), "cannot feed"),
        (("b1", "out", "b1", "in"), "leads back"),
        (("b1", "out", "move:stamp", "play"), "already feeds"),
        (("b9", "out", "b1", "in"), "no block b9"),
        (("b1", "out", "move:nope", "play"), "no move 'nope'"),
        (("move:stamp", "then", "move:stamp", "play"), "itself"),
    ],
)
def test_a_wire_no_graph_holds_is_refused(
    d: PortDocument, wire: tuple[str, str, str, str], why: str
) -> None:
    before = d.manifest
    with pytest.raises(ManifestError, match=why):
        B.link(d, *wire)
    assert d.manifest is before


def test_unlink_clears_each_kind(d: PortDocument) -> None:
    before = d.manifest
    for wire in B.wires(before):
        B.unlink(d, *wire)
    assert B.wires(d.manifest) == []
    for _ in range(len(B.wires(before))):
        d.undo()
    assert d.manifest == before
    with pytest.raises(ManifestError, match="does not feed"):
        B.unlink(d, "b1", "out", B.move_id("flinch_head"), "play")
    with pytest.raises(ManifestError, match="does not hand to"):
        B.unlink(d, B.move_id("stamp"), "then", B.move_id("flinch_head"), "play")


def test_set_param_and_its_undo(d: PortDocument) -> None:
    before = d.manifest
    B.set_param(d, "b1", "frames", 30)
    assert d.manifest.behaviour.blocks["b1"].params == {"frames": 30} and undone(d, before)
    B.add_block(d, "distance", (0, 0))
    B.set_param(d, "b2", "hi", 800)
    assert d.manifest.behaviour.blocks["b2"].params == {"lo": 0.0, "hi": 800.0}
    B.set_param(d, "b2", "hi", None)
    assert d.manifest.behaviour.blocks["b2"].params == {"lo": 0.0}
    B.add_block(d, "host_state", (0, 0))
    B.set_param(d, "b3", "mains", (5, 1, 5))
    assert d.manifest.behaviour.blocks["b3"].params == {"mains": [1, 5]}
    B.add_block(d, "on_flinch", (0, 0))
    B.set_param(d, "b4", "part", 1)
    assert d.manifest.behaviour.blocks["b4"].params == {"part": 1}


def test_set_param_refusals(d: PortDocument) -> None:
    before = d.manifest
    with pytest.raises(ManifestError, match="cannot be none"):
        B.set_param(d, "b1", "frames", None)
    with pytest.raises(ManifestError, match="no 'hi'"):
        B.set_param(d, "b1", "hi", 3)
    with pytest.raises(ManifestError, match="under 1"):
        B.set_param(d, "b1", "frames", 0)
    assert B.set_param(d, "b1", "frames", 20) == "Played for at least: unchanged"
    assert d.manifest is before, "no edit, no undo step"


def test_notes_and_places(d: PortDocument) -> None:
    before = d.manifest
    B.set_label(d, "b1", "  just past you ")
    assert d.manifest.behaviour.blocks["b1"].label == "just past you" and undone(d, before)
    B.set_label(d, B.move_id("stamp"), "the stamp")
    assert d.manifest.moves["stamp"].label == "the stamp" and undone(d, before)
    assert B.set_label(d, "b1", "") == "Played for at least: unchanged"
    B.move_nodes(
        d, {"b1": (300.04, 1.0), B.move_id("stamp"): (5.0, 6.0), B.move_id("charge"): (7.0, 8.0)}
    )
    m = d.manifest
    assert m.behaviour.blocks["b1"].at == (300.0, 1.0)
    assert m.behaviour.moves["stamp"].at == (5.0, 6.0), "a move's first drag writes its node"
    assert m.behaviour.moves["charge"].at == (7.0, 8.0) and m.behaviour.moves["charge"].during == [
        "b1"
    ]
    assert undone(d, before)
    assert B.move_nodes(d, {"b1": (260.0, 0.0)}) == "unchanged" and d.manifest is before


def test_delete_cleans_every_reference(d: PortDocument) -> None:
    B.add_block(d, "distance", (0, 0))
    B.add_block(d, "on_noticed", (0, 50))
    B.link(d, "b1", "out", "b2", "in")
    B.link(d, "b3", "out", "b1", "in")
    B.link(d, B.move_id("stamp"), "while playing", "b2", "in")
    full = d.manifest
    msg = B.delete(d, ["b2"])
    m = d.manifest
    assert msg == "1 block deleted" and "b2" not in m.behaviour.blocks
    assert m.behaviour.blocks["b1"].next == [] and m.behaviour.blocks["b3"].next == ["b1"]
    assert m.behaviour.moves["stamp"].during == []
    assert undone(d, full)


def test_a_move_node_is_not_deleted_here(d: PortDocument) -> None:
    with pytest.raises(ManifestError, match="Moves dock"):
        B.delete(d, [B.move_id("stamp")])
    assert B.delete(d, ["b1", B.move_id("stamp")]).endswith("delete them in the Moves dock")
    assert "stamp" in d.manifest.moves and "b1" not in d.manifest.behaviour.blocks
    with pytest.raises(ManifestError, match="no block b7"):
        B.delete(d, ["b7"])


def test_a_gesture_is_one_undo_step(d: PortDocument) -> None:
    B.add_block(d, "on_noticed", (0, 50))
    B.link(d, "b2", "out", "b1", "in")
    before = d.manifest
    g = B.Gesture(d)
    g.run(lambda e: B.unlink(e, "b2", "out", "b1", "in"))
    g.run(lambda e: B.link(e, "b2", "out", B.move_id("flinch_head"), "play"))
    assert d.manifest is before, "nothing is applied before the commit"
    assert (
        g.commit() == "On notice no longer feeds Played for at least; On notice feeds flinch_head"
    )
    assert d.manifest.behaviour.blocks["b2"].next == [] and d.manifest.behaviour.blocks[
        "b2"
    ].play == ["flinch_head"]
    assert undone(d, before)


def test_a_refused_edit_refuses_the_gesture(d: PortDocument) -> None:
    before = d.manifest
    g = B.Gesture(d)
    g.run(lambda e: B.set_label(e, "b1", "kept?"))
    g.run(lambda e: B.link(e, "b1", "out", "b1", "in"))
    g.run(lambda e: B.set_label(e, "b1", "after"))
    with pytest.raises(ManifestError, match="leads back"):
        g.commit()
    assert d.manifest is before


def test_the_users_example(d: PortDocument) -> None:
    B.add_block(d, "on_part_broken", (0, 100))
    B.set_param(d, "b2", "part", d.manifest.parts["head"].index)
    B.link(d, "b2", "out", B.move_id("flinch_head"), "play")
    rules = model.compile(d.manifest)
    assert Rule("flinch_head", on="part_broken", part=1) in rules
    assert len(rules) == 2


def test_moves_without_a_node_sit_in_a_column_in_sequence_order(d: PortDocument) -> None:
    x = 260.0 + model.COLUMN
    want = {
        n: (x, float(k * model.ROW)) for k, n in enumerate(["stamp", "stamp_2", "flinch_head"], 1)
    }
    assert B.spots(d.manifest) == {"charge": (0.0, 0.0), **want}
    B.move_nodes(d, {B.move_id("stamp"): (1.0, 2.0)})
    assert B.spots(d.manifest) == {"charge": (0.0, 0.0), **want, "stamp": (1.0, 2.0)}


def test_what_the_graph_says_of_itself(d: PortDocument) -> None:
    B.add_block(d, "on_noticed", (0, 50))
    B.add_block(d, "cooldown", (0, 100))
    B.add_block(d, "cooldown", (0, 150))
    B.link(d, "b2", "out", "b3", "in")
    B.link(d, "b3", "out", "b4", "in")
    B.link(d, "b4", "out", B.move_id("flinch_head"), "play")
    r = B.read(d.manifest)
    assert [p.play for p in r.paths] == ["stamp", "flinch_head"]
    assert r.priority == {"b1": [1], "b4": [2]} and r.loose == []
    assert set(r.refused) == {"b2", "b3", "b4"}
    assert all("two 'Then wait' blocks" in why for why in r.refused.values())
    assert r.capped is None


def test_a_block_that_plays_twice_shows_both_places(d: PortDocument) -> None:
    d.edit(lambda m: m.behaviour.blocks["b1"].play.append("flinch_head"))
    r = B.read(d.manifest)
    assert sorted(p.play for p in r.paths) == ["flinch_head", "stamp"]
    assert r.priority == {"b1": [1, 2]}


def test_spread_keeps_the_order(d: PortDocument) -> None:
    B.add_block(d, "on_noticed", (100, 50))
    before = d.manifest
    assert B.spread(d) == "3 nodes spread out"
    b = d.manifest.behaviour
    assert b.blocks["b1"].at == (390.0, 0.0) and b.blocks["b2"].at == (150.0, 85.0)
    assert b.moves["charge"].at == (0.0, 0.0)
    assert undone(d, before)


def test_spread_keeps_every_priority(ports: Path) -> None:
    z = PortDocument(manifest.load(ports / "zinogre.toml"))
    order = [(p.blocks, p.play, p.during) for p in model.paths(z.manifest)]
    rules = model.compile(z.manifest)
    for _ in range(3):
        B.spread(z)
        assert [(p.blocks, p.play, p.during) for p in model.paths(z.manifest)] == order
    assert model.compile(z.manifest) == rules


def test_spread_keeps_a_tie_a_tie(d: PortDocument) -> None:
    B.add_block(d, "on_noticed", (260.0, 0.0))
    B.add_block(d, "on_flinch", (100.0, 0.0))

    def order() -> list[tuple[tuple[str, ...], str]]:
        return [(p.blocks, p.play) for p in model.paths(d.manifest)]

    before = order()
    B.spread(d)
    assert order() == before


def test_the_cap(d: PortDocument) -> None:
    def many(m: Manifest) -> None:
        for k in range(model.SEAM_RULES + 1):
            m.behaviour.blocks[f"e{k}"] = model.Block("on_noticed", (0.0, float(k)), play=["stamp"])

    d.edit(many)
    assert (
        B.read(d.manifest).capped
        == f"{model.SEAM_RULES + 2} paths, the seam holds {model.SEAM_RULES}"
    )


def test_the_palette_offers_every_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    offered = [k for _, kinds in B.palette() for k, _, _ in kinds]
    assert sorted(offered) == sorted(KINDS)
    assert [t for t, _ in B.palette()] == ["Events", "State", "Conditions", "Modifiers"]
    tips = {k: (t, tip) for _, kinds in B.palette() for k, t, tip in kinds}
    assert tips["force"] == (KINDS["force"].title, KINDS["force"].tip)
    monkeypatch.setitem(
        KINDS,
        "wounded",
        Kind("wounded", "condition", "Wounded", (Param("hp", "float", "HP"),), "hurt"),
    )
    assert "wounded" in [k for _, kinds in B.palette() for k, _, _ in kinds]


def test_the_zinogre_reads(ports: Path) -> None:
    m = manifest.load(ports / "zinogre.toml")
    r = B.read(m)
    assert len(m.behaviour.blocks) == 15 and len(r.paths) == 7
    assert sorted(n for ns in r.priority.values() for n in ns) == list(range(1, 8))
    assert r.loose == [] and r.refused == {} and r.capped is None
    assert min(b.at[0] for b in m.behaviour.blocks.values()) == model.LEFT
    assert {w.dst for w in B.wires(m) if w.dst_port == "play"} >= {"move:flinch_head"}
    assert len(B.spots(m)) == len(m.moves)


def test_renaming_a_move_keeps_its_paths(ports: Path) -> None:
    z = PortDocument(manifest.load(ports / "zinogre.toml"))
    before = model.compile(z.manifest)
    clips.rename_move(z, "stamp", "stomp")
    clips.rename_move(z, "lunge", "lunge_fwd")
    assert {"stomp", "lunge_fwd"} <= set(z.manifest.behaviour.moves)
    names = {"stomp": "stamp", "lunge_fwd": "lunge"}
    back = [
        dataclasses.replace(
            r, play=names.get(r.play, r.play), from_move=names.get(r.from_move or "", r.from_move)
        )
        for r in model.compile(z.manifest)
    ]
    assert back == before and B.read(z.manifest).loose == []
    z.undo()
    z.undo()
    assert model.compile(z.manifest) == before


def test_a_move_in_use_is_not_deleted(ports: Path) -> None:
    z = PortDocument(manifest.load(ports / "zinogre.toml"))
    with pytest.raises(ManifestError, match="block b12 plays it"):
        clips.drop_move(z, "flinch_head")
    with pytest.raises(ManifestError, match="while lunge plays"):
        clips.drop_move(z, "lunge")
    clips.drop_move(z, "idle")
    assert "idle" not in z.manifest.moves


# rules: what a trigger's rows edit


def trigger(m: Manifest, key: str) -> model.Trigger:
    return next(t for t in model.triggers(m) if t.key == key)


def two_rules(d: PortDocument) -> PortDocument:
    """The `d` rule, and a second one from charge's while-playing port below it."""
    d.edit(
        lambda m: (
            m.behaviour.blocks.update(
                {
                    "b2": model.Block(
                        "played_for", (260.0, 200.0), {"frames": 60}, play=["stamp_2"]
                    ),
                    "b3": model.Block(
                        "played_for", (260.0, 400.0), {"frames": 90}, play=["flinch_head"]
                    ),
                }
            ),
            m.behaviour.moves["charge"].during.extend(["b2", "b3"]),
        )
    )
    return d


def plays(m: Manifest) -> list[str]:
    return [p.play for p in model.paths(m)]


def test_open_rules_are_the_chains_that_play_nothing_yet(d: PortDocument) -> None:
    assert B.open_rules(d.manifest) == []
    d.edit(
        lambda m: m.behaviour.blocks.update(
            {
                "b2": model.Block("on_noticed", (260.0, 200.0), next=["b3"]),
                "b3": model.Block("force", (460.0, 200.0)),
            }
        )
    )
    [rule] = B.open_rules(d.manifest)
    assert (rule.blocks, rule.play, rule.during) == (("b2", "b3"), "", None)
    assert model.trigger_key(d.manifest, rule) == "on_noticed"


def test_a_new_rule_for_an_event_is_checked_last_and_plays_nothing(d: PortDocument) -> None:
    before = d.manifest
    B.add_rule(d, trigger(d.manifest, "on_flinch"))
    blk = d.manifest.behaviour.blocks["b2"]
    assert (blk.kind, blk.params, blk.at) == (
        "on_flinch",
        {},
        (float(model.LEFT), float(model.ROW)),
    )
    assert model.paths(d.manifest) == model.paths(before), "no move yet: no rule yet"
    [rule] = B.open_rules(d.manifest)
    assert rule.blocks == ("b2",) and model.trigger_key(d.manifest, rule) == "on_flinch"
    B.set_play(d, "b2", None, "flinch_head")
    assert plays(d.manifest) == ["stamp", "flinch_head"], "it ranks last"
    assert model.compile(d.manifest)[-1].play == "flinch_head"
    d.undo()
    d.undo()
    assert d.manifest == before


def test_a_new_rule_for_a_state_has_the_states_values(d: PortDocument) -> None:
    B.add_rule(d, trigger(d.manifest, "host_state"))
    assert d.manifest.behaviour.blocks["b2"].params == {"mains": [0]}


def test_a_new_rule_for_a_move_hangs_from_its_while_playing_port(d: PortDocument) -> None:
    B.add_rule(d, trigger(d.manifest, "during:charge"))
    m = d.manifest
    assert m.behaviour.blocks["b2"].kind == "played_for" and m.behaviour.moves["charge"].during == [
        "b1",
        "b2",
    ]
    assert [model.trigger_key(m, p) for p in B.open_rules(m)] == ["during:charge"]


def test_a_new_rule_for_a_kind_with_by_carries_the_value(
    d: PortDocument, monkeypatch: pytest.MonkeyPatch
) -> None:
    named = Kind(
        "on_x",
        "event",
        "On x",
        (Param("name", "choice", "Name", choices=("a", "b")),),
        "x",
        lambda v: v["name"],
        by="name",
    )
    monkeypatch.setitem(KINDS, "on_x", named)
    B.add_rule(d, model.Trigger("on_x:b", "event", "on_x", "b"))
    assert d.manifest.behaviour.blocks["b2"].params == {"name": "b"}
    assert [model.trigger_key(d.manifest, p) for p in B.open_rules(d.manifest)] == ["on_x:b"]


def test_a_block_goes_in_after_another_and_a_play_moves_to_the_new_end(d: PortDocument) -> None:
    before = d.manifest
    B.insert_block(d, "b1", "cooldown", None, "stamp")
    blocks = d.manifest.behaviour.blocks
    assert (blocks["b1"].next, blocks["b1"].play) == (["b2"], [])
    assert (blocks["b2"].kind, blocks["b2"].next, blocks["b2"].play) == ("cooldown", [], ["stamp"])
    assert [p.blocks for p in model.paths(d.manifest)] == [("b1", "b2")]
    B.insert_block(d, "b1", "distance", "b2", None)
    blocks = d.manifest.behaviour.blocks
    assert (
        blocks["b1"].next == ["b3"]
        and blocks["b3"].next == ["b2"]
        and blocks["b3"].kind == "distance"
    )
    assert [p.blocks for p in model.paths(d.manifest)] == [("b1", "b3", "b2")]
    d.undo()
    d.undo()
    assert d.manifest == before


def test_an_insert_refuses_what_is_not_a_block_of_a_rule(d: PortDocument) -> None:
    with pytest.raises(ManifestError, match="not a block a rule holds"):
        B.insert_block(d, "b1", "on_noticed", None, "stamp")
    with pytest.raises(ManifestError, match="does not play"):
        B.insert_block(d, "b1", "cooldown", None, "stamp_2")
    with pytest.raises(ManifestError, match="no block b9"):
        B.insert_block(d, "b1", "cooldown", "b9", None)


def chain(d: PortDocument) -> PortDocument:
    """b1 -> b2 -> b3, b3 plays stamp."""
    B.insert_block(d, "b1", "cooldown", None, "stamp")
    B.insert_block(d, "b2", "limit", None, "stamp")
    return d


def test_a_block_in_a_rule_closes_the_chain_up(d: PortDocument) -> None:
    chain(d)
    mid = d.manifest
    B.drop_block(d, "b2")
    blocks = d.manifest.behaviour.blocks
    assert "b2" not in blocks and blocks["b1"].next == ["b3"] and blocks["b3"].play == ["stamp"]
    assert undone(d, mid)
    B.drop_block(d, "b3")
    blocks = d.manifest.behaviour.blocks
    assert blocks["b2"].next == [] and blocks["b2"].play == ["stamp"] and "b3" not in blocks
    assert [p.blocks for p in model.paths(d.manifest)] == [("b1", "b2")]


def test_the_first_block_of_a_rule_goes_with_the_rule(d: PortDocument) -> None:
    with pytest.raises(ManifestError, match="goes with the rule"):
        B.drop_block(d, "b1")


def test_a_rule_goes_with_the_blocks_only_it_ran_through(d: PortDocument) -> None:
    before = chain(d).manifest
    B.drop_rule(d, ("b1", "b2", "b3"), "stamp")
    assert d.manifest.behaviour.blocks == {}
    assert d.manifest.behaviour.moves["charge"].during == []
    d.undo()
    assert d.manifest == before


def test_a_rule_that_shares_its_start_leaves_the_shared_blocks(d: PortDocument) -> None:
    d.edit(
        lambda m: (
            m.behaviour.blocks["b1"].next.append("b2")
            or m.behaviour.blocks.__setitem__(
                "b2", model.Block("cooldown", (460.0, 0.0), {"frames": 5}, play=["stamp_2"])
            )
        )
    )
    assert [p.blocks for p in model.paths(d.manifest)] == [("b1",), ("b1", "b2")]
    B.drop_rule(d, ("b1", "b2"), "stamp_2")
    blocks = d.manifest.behaviour.blocks
    assert list(blocks) == ["b1"] and blocks["b1"].next == [] and blocks["b1"].play == ["stamp"]
    B.drop_rule(d, ("b1",), "stamp")
    assert d.manifest.behaviour.blocks == {} and d.manifest.behaviour.moves["charge"].during == []


def test_an_unfinished_rule_goes_too(d: PortDocument) -> None:
    B.add_rule(d, trigger(d.manifest, "on_noticed"))
    B.drop_rule(d, ("b2",), None)
    assert list(d.manifest.behaviour.blocks) == ["b1"]


def test_the_move_a_rule_plays_is_changed_added_and_taken_away(d: PortDocument) -> None:
    before = d.manifest
    B.set_play(d, "b1", "stamp", "stamp_2")
    assert d.manifest.behaviour.blocks["b1"].play == ["stamp_2"]
    B.set_play(d, "b1", "stamp_2", None)
    assert d.manifest.behaviour.blocks["b1"].play == [] and [
        p.blocks for p in B.open_rules(d.manifest)
    ] == [("b1",)]
    B.set_play(d, "b1", None, "flinch_head")
    assert d.manifest.behaviour.blocks["b1"].play == ["flinch_head"]
    for _ in range(3):
        d.undo()
    assert d.manifest == before
    with pytest.raises(ManifestError, match="no longer plays"):
        B.set_play(d, "b1", "stamp_2", "flinch_head")
    with pytest.raises(ManifestError, match="no move"):
        B.set_play(d, "b1", "stamp", "nope")


def test_a_move_cannot_be_played_twice_by_a_block(d: PortDocument) -> None:
    B.set_play(d, "b1", None, "stamp_2")
    with pytest.raises(ManifestError, match="one twice"):
        B.set_play(d, "b1", "stamp_2", "stamp")


def test_a_rule_takes_another_rules_place(d: PortDocument) -> None:
    two_rules(d)
    rules = trigger(d.manifest, "during:charge").rules
    assert plays(d.manifest) == ["stamp", "stamp_2", "flinch_head"]
    paths = [p for _, p in rules]
    before = d.manifest
    B.move_rule(d, paths, 2, 0)
    assert plays(d.manifest) == ["flinch_head", "stamp", "stamp_2"]
    blocks = d.manifest.behaviour.blocks
    assert [blocks[i].at for i in ("b1", "b2", "b3")] == [
        (260.0, 200.0),
        (260.0, 400.0),
        (260.0, 0.0),
    ]
    assert d.manifest.behaviour.moves["charge"].at == before.behaviour.moves["charge"].at
    assert undone(d, before)
    B.move_rule(d, paths, 0, 1)
    assert plays(d.manifest) == ["stamp_2", "stamp", "flinch_head"]


def test_a_move_to_the_same_place_is_no_change(d: PortDocument) -> None:
    two_rules(d)
    paths = [p for _, p in trigger(d.manifest, "during:charge").rules]
    before = d.manifest
    assert B.move_rule(d, paths, 1, 1) == "unchanged"
    assert d.manifest is before


def test_a_rule_that_would_keep_its_place_is_refused(d: PortDocument) -> None:
    two_rules(d)
    d.edit(lambda m: setattr(m.behaviour.blocks["b2"], "at", (260.0, 0.0)))  # level with b1
    paths = [p for _, p in trigger(d.manifest, "during:charge").rules]
    before = d.manifest
    with pytest.raises(ManifestError, match="would not change the rule's rank"):
        B.move_rule(d, paths, 1, 0)
    assert d.manifest is before


def test_rules_that_share_their_first_block_cannot_be_reordered(d: PortDocument) -> None:
    d.edit(lambda m: m.behaviour.blocks["b1"].play.append("stamp_2"))
    paths = [p for _, p in trigger(d.manifest, "during:charge").rules]
    assert len(paths) == 2
    with pytest.raises(ManifestError, match="same block"):
        B.move_rule(d, paths, 0, 1)


def test_a_reorder_leaves_the_other_triggers_ranks_alone(ports: Path) -> None:
    z = PortDocument(manifest.load(ports / "zinogre.toml"))
    before = model.compile(z.manifest)
    paths = [p for _, p in trigger(z.manifest, "during:lunge").rules]
    B.move_rule(z, paths, 0, 0)
    assert model.compile(z.manifest) == before, "a no-op changes nothing"
    B.move_rule(z, paths, 0, 1)
    after = model.compile(z.manifest)
    assert after[0] == before[1] and after[1] == before[0] and after[2:] == before[2:]
    z.undo()
    assert model.compile(z.manifest) == before


def test_a_rule_made_and_taken_away_leaves_the_zinogres_rules_alone(ports: Path) -> None:
    z = PortDocument(manifest.load(ports / "zinogre.toml"))
    before = model.compile(z.manifest)
    B.add_rule(z, trigger(z.manifest, "on_flinch"))
    new = next(i for i in z.manifest.behaviour.blocks if i not in {f"b{k}" for k in range(1, 16)})
    assert model.compile(z.manifest) == before, "no move yet"
    B.set_play(z, new, None, "flinch_head")
    after = model.compile(z.manifest)
    assert after[:-1] == before and after[-1].play == "flinch_head" and after[-1].on == "flinch"
    B.insert_block(z, "b12", "cooldown", None, "flinch_head")
    B.drop_block(z, "b16" if new != "b16" else "b17")
    B.drop_rule(z, (new,), "flinch_head")
    assert model.compile(z.manifest) == before


def lit(d: PortDocument, *ids: str) -> B.Focus | None:
    return B.focus(d.manifest, model.paths(d.manifest), ids)


def test_focus_lights_every_rule_through_a_node_and_the_moves_they_hand_to(d: PortDocument) -> None:
    def more(m: Manifest) -> None:
        b = m.behaviour
        b.blocks["b2"] = Block("on_flinch", (260.0, 160.0), play=["stamp"])
        b.blocks["b3"] = Block("on_noticed", (260.0, 320.0), play=["flinch_head"])
        b.blocks["b4"] = Block("distance", (260.0, 480.0), {"lo": 0.0})

    d.edit(more)
    stamp, step, charge = (B.move_id(n) for n in ("stamp", "stamp_2", "charge"))
    f = lit(d, "b2")
    assert f is not None and f.nodes == {"b2", stamp, step}
    assert f.wires == {B.Wire("b2", "out", stamp, "play"), B.Wire(stamp, "then", step, "play")}
    f = lit(d, stamp)
    assert f is not None and f.nodes == {"b1", "b2", stamp, step, charge}
    assert B.Wire(charge, "while playing", "b1", "in") in f.wires
    assert B.Wire("b3", "out", B.move_id("flinch_head"), "play") not in f.wires
    f = lit(d, step)
    assert f is not None and f.nodes == {"b1", "b2", stamp, step, charge}, "a move reached by then"


def test_focus_keeps_a_node_on_no_rule_and_a_chain_whole(d: PortDocument) -> None:
    def chain(m: Manifest) -> None:
        m.behaviour.blocks["b2"] = Block("on_noticed", (260.0, 160.0), next=["b3"])
        m.behaviour.blocks["b3"] = Block("cooldown", (460.0, 160.0), {"frames": 5}, play=["stamp"])
        m.behaviour.blocks["b4"] = Block("distance", (260.0, 320.0), {"lo": 1.0})

    d.edit(chain)
    f = lit(d, "b4")
    assert f is not None and f.nodes == {"b4"} and not f.wires
    f = lit(d, "b2")
    assert f is not None and {"b2", "b3"} <= f.nodes and B.Wire("b2", "out", "b3", "in") in f.wires
    assert "b1" not in f.nodes
    assert lit(d) is None


def test_focus_reads_a_ring_of_moves_once(d: PortDocument) -> None:
    d.edit(lambda m: setattr(m.moves["stamp_2"], "after", "stamp"))
    stamp, step = B.move_id("stamp"), B.move_id("stamp_2")
    f = lit(d, "b1")
    assert f is not None and {stamp, step} <= f.nodes
    assert {B.Wire(stamp, "then", step, "play"), B.Wire(step, "then", stamp, "play")} <= f.wires
