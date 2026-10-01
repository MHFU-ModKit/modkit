# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Moves graph's layout and state, without a toolkit; its canvas runs in ui/test_moves."""

from typing import Any

from mhfu.em.intel import SpeciesIntel
from mhfu_port.manifest import Move
from mhfu_studio.monster.panels import graph

MOVES = {
    "lunge": Move(1, 4, clip="c", after="lunge_stop"),
    "lunge_stop": Move(1, 3, clip="s"),
}


def test_moves_scope(intel75: SpeciesIntel) -> None:
    lay = graph.build(intel75, MOVES, None, "moves")
    assert not lay.empty and lay.note == ""
    layers = {k: n.layer for k, n in lay.nodes.items()}
    assert layers[(1, 4)] == layers[(1, 3)] == 0
    assert layers[(0, 3)] == layers[(0, 6)] == 1
    assert layers[(0, 1)] == layers[(0, 2)] == 2
    assert lay.nodes[(1, 4)].move == "lunge" and lay.nodes[(1, 4)].attacks
    assert lay.nodes[(0, 1)].hub and not lay.nodes[(0, 3)].hub
    arrows = {(a.src, a.dst): a for a in lay.arrows}
    assert arrows[(1, 4), (0, 3)].label == "!collided & run budget spent"
    assert arrows[(1, 4), (0, 6)].label == "collided"
    assert not any(a.src in ((0, 1), (0, 2)) for a in lay.arrows), "a hub is a terminal"
    ys = sorted(n.y for n in lay.nodes.values() if n.layer == 2)
    assert all(b - a >= graph.NODE_H for a, b in zip(ys, ys[1:], strict=False))
    assert lay.width > 0 and lay.height > 0


def test_selected_scope_has_its_predecessors_left(intel75: SpeciesIntel) -> None:
    lay = graph.build(intel75, MOVES, (0, 3), "selected")
    layers = {k: n.layer for k, n in lay.nodes.items()}
    assert layers[(1, 4)] == 0 and layers[(0, 3)] == 1 and layers[(0, 1)] == 2
    assert (0, 6) not in lay.nodes and lay.nodes[(0, 3)].entry


def test_a_pair_in_the_picture_is_not_rerooted(intel75: SpeciesIntel) -> None:
    lay = graph.build(intel75, MOVES, (0, 1), "moves")
    assert lay.nodes[(0, 1)].hub and not lay.nodes[(0, 1)].entry
    assert not any(a.src == (0, 1) for a in lay.arrows)
    assert set(lay.nodes) == set(graph.build(intel75, MOVES, None, "moves").nodes)
    other = graph.build(intel75, MOVES, (3, 9), "moves")
    assert other.nodes[(3, 9)].entry and other.nodes[(3, 9)].layer == 0


def test_info_and_walk_lines(intel75: SpeciesIntel) -> None:
    lay = graph.build(intel75, MOVES, None, "moves")
    lines = graph.info_lines(intel75, (1, 4), lay, MOVES)
    assert lines[0].startswith("(1,4)  lunge -> clip c  (after = lunge_stop)")
    assert any("-> (0,3)" in t and "run budget spent" in t for t in lines)
    assert any(t.startswith("entered from: the brain") for t in lines)
    assert any("never ends by itself" in t for t in graph.info_lines(intel75, (0, 1), lay, MOVES))
    assert graph.walk_line(intel75, (1, 4)).startswith("(1,4) ends -> (0,3) when")
    assert "never ends itself" in graph.walk_line(intel75, (0, 1))


def test_attacks_scope_groups_alike_pairs(intel75: SpeciesIntel) -> None:
    lay = graph.build(intel75, MOVES, None, "attacks")
    assert (3, 9) in lay.nodes and (3, 10) not in lay.nodes
    assert lay.nodes[(3, 9)].siblings == ((3, 10),) and "+1 alike" in lay.nodes[(3, 9)].lines[0]
    assert lay.nodes[(1, 4)].move == "lunge"


def test_no_intel_and_no_roots_are_said(intel75: SpeciesIntel, species: Any) -> None:
    assert graph.build(None, MOVES, None).note.startswith("no hand-off intel")
    plain = species([{"main": 1, "sub": 4, "measured": None}])
    assert graph.build(plain, MOVES, None).empty
    lay = graph.build(intel75, {}, None, "moves")
    assert lay.empty and "[moves] is empty" in lay.note
    assert "select a pair" in graph.build(intel75, {}, None, "selected").note


def test_em75_lays_the_zinogre_chain_out(em75: SpeciesIntel, ports: Any) -> None:
    from mhfu_port import manifest

    m = manifest.load(ports / "zinogre.toml")
    lay = graph.build(em75, m.moves, (1, 4), "moves")
    layers = {k: n.layer for k, n in lay.nodes.items()}
    assert layers[(1, 4)] == 0 and layers[(0, 3)] == 1 and layers[(0, 1)] == 2
    assert lay.nodes[(1, 4)].move == "lunge"
    big = graph.build(em75, m.moves, None, "attacks")
    assert 20 <= len(big.nodes) <= 60 and len(big.arrows) < 400


def test_drags_survive_a_rebuild_with_the_same_nodes(intel75: SpeciesIntel) -> None:
    g = graph.MoveGraph()
    lay = g.layout(intel75, MOVES, None)
    assert g.fresh
    g.fresh = False
    lay.nodes[(1, 4)].x += 40.0
    again = g.layout(intel75, MOVES, (1, 4))
    assert again is not lay and again.nodes[(1, 4)].x == lay.nodes[(1, 4)].x and not g.fresh
    assert g.layout(intel75, MOVES, (1, 4)) is again, "cached while nothing changes"
    g.relayout()
    assert g.layout(intel75, MOVES, (1, 4)).nodes[(1, 4)].x < lay.nodes[(1, 4)].x and g.fresh


def test_scope_change_drops_a_pick_it_hides(intel75: SpeciesIntel) -> None:
    g = graph.MoveGraph()
    g.layout(intel75, MOVES, None)
    g.picked = (0, 6)
    g.fresh = False
    g.set_scope("selected")
    g.set_scope("nonsense")
    assert g.scope == "selected"
    assert g.layout(intel75, MOVES, None).empty and g.picked is None and g.fresh


def test_node_kinds(intel75: SpeciesIntel) -> None:
    lay = graph.build(intel75, MOVES, None, "moves")
    kinds = {k: n.kind for k, n in lay.nodes.items()}
    assert kinds[(1, 4)] == "move" and kinds[(0, 1)] == "hub" and kinds[(0, 3)] == "plain"
    assert set(kinds.values()) <= set(graph.KINDS)
