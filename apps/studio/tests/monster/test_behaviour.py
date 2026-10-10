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
from mhfu_port.behaviour import KINDS, Kind, Param, Rule
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
    assert params["any_time"] == params["idle"] == {} and params["rage"] == {"state": "enraged"}
    assert params["monster_hp"] == {"lo": 0} and params["chance"] == {"percent": 1}
    assert params["part_broken"] == {"part": 0, "state": "broken"}
    assert params["hunter_side"] == {"sides": ["front"]} and params["on_signal"] == {
        "name": "signal1"
    }
    counters = [params[k] for k in ("counter_is", "counter_add", "counter_set")]
    assert sorted(c["counter"] for c in counters) == ["counter1", "counter2", "counter3"]
    flags = [params[k] for k in ("flag_is", "flag_set")]
    assert sorted(f["flag"] for f in flags) == ["flag1", "flag2"], "a new name is never taken"
    assert params["counter_add"]["by"] == 1 and params["counter_set"]["to"] == 0
    assert params["flag_is"]["state"] == params["flag_set"]["state"] == "set"
    assert params["counter_is"] | {"counter": 0} == {"counter": 0, "test": "at least", "value": 1}
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
    titles = [t for t, _ in B.palette()]
    assert titles[:4] == ["Events", "State", "Conditions", "Modifiers"]
    assert titles[4:] in ([], ["Effects"])
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
    assert len(m.behaviour.blocks) == 36 and len(r.paths) == 16
    assert sorted(n for ns in r.priority.values() for n in ns) == list(range(1, 17))
    assert r.loose == [] and r.refused == {} and r.capped is None
    assert [p.blocks[-1] for p in r.paths if p.play is None] == ["b15", "b17", "b23"]
    assert model.names(m, "var") == ["flinches", "in_combat"]
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
    with pytest.raises(ManifestError, match="block b18 plays it"):
        clips.drop_move(z, "flinch_head")
    with pytest.raises(ManifestError, match="while lunge plays"):
        clips.drop_move(z, "lunge")
    clips.drop_move(z, "idle")
    assert "idle" not in z.manifest.moves


# ---- effects, names and sides ---------------------------------------------------------------- #


class Bare:
    """An `Edits` over a manifest that is never validated: the new param types' checks are the
    package's."""

    def __init__(self, m: Manifest) -> None:
        self.manifest = m

    def edit(self, change: Callable[[Manifest], object]) -> None:
        change(self.manifest)


def test_the_palette_has_an_effects_group(new_kinds: tuple[Kind, ...]) -> None:
    groups = dict(B.palette())
    assert list(groups) == ["Events", "State", "Conditions", "Modifiers", "Effects"]
    effects = {k.name for k in KINDS.values() if k.role == "effect"}
    assert effects >= {"counter_add", "flag_set", new_kinds[0].name, new_kinds[1].name}
    assert {k for k, _, _ in groups["Effects"]} == effects
    assert new_kinds[2].name in [k for k, _, _ in groups["Events"]]
    assert new_kinds[3].name in [k for k, _, _ in groups["Conditions"]]


def test_new_blocks_start_with_fresh_names_and_a_side(
    new_kinds: tuple[Kind, ...], make: Callable[[str], Manifest], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        KINDS,
        "t_pair",
        Kind(
            "t_pair",
            "event",
            "Pair",
            (Param("a", "signal", "A"), Param("b", "signal", "B")),
            "two signals",
        ),
    )
    d = Bare(make(""))
    for kind in ("t_count", "t_count", "t_flag", "t_signal", "t_side", "t_pair", "t_signal"):
        B.add_block(d, kind, (0, 0))
    got = [blk.params for blk in d.manifest.behaviour.blocks.values()]
    assert got == [
        {"counter": "counter1", "by": 1},
        {"counter": "counter2", "by": 1},
        {"flag": "flag1"},
        {"name": "signal1"},
        {"sides": ["front"]},
        {"a": "signal2", "b": "signal3"},
        {"name": "signal4"},
    ]
    assert B.fresh("x", {"x1", "x3"}) == "x2"


def test_sides_are_stored_in_the_order_of_the_choices(
    new_kinds: tuple[Kind, ...], make: Callable[[str], Manifest]
) -> None:
    d = Bare(make(""))
    B.add_block(d, "t_side", (0, 0))
    B.set_param(d, "b1", "sides", ("behind", "front", "front"))
    assert d.manifest.behaviour.blocks["b1"].params == {"sides": ["front", "behind"]}
    B.set_param(d, "b1", "sides", ())
    assert d.manifest.behaviour.blocks["b1"].params == {"sides": []}  # the loader refuses this
    B.add_block(d, "t_flag", (0, 0))
    B.set_param(d, "b2", "flag", "armor_2")
    assert d.manifest.behaviour.blocks["b2"].params == {"flag": "armor_2"}


def test_a_path_that_plays_nothing_ranks_on_its_last_block(
    d: PortDocument, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [model.Path(("b2", "b3"), None, None), model.Path(("b1",), "stamp", "charge")]
    monkeypatch.setattr(model, "paths", lambda m: paths)
    monkeypatch.setattr(model, "refused", lambda m: {})
    monkeypatch.setattr(model, "loose", lambda m: [])
    r = B.read(d.manifest)
    assert r.priority == {"b3": [1], "b1": [2]} and r.capped is None
