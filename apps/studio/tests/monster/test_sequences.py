# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Sequences as document edits, over manifests of own moves."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pytest
from mhfu_port import continuity, sequence
from mhfu_port.manifest import Manifest, ManifestError, Move, Steer
from mhfu_studio.monster import sequences
from mhfu_studio.monster.document import PortDocument

LOOP = '[moves.topple_3]\nclip = "kick"'
BODY = """
[clips.walk]
slot = 1
frames = 10
label = "walks"
[clips.roar]
slot = 2
frames = 6
[clips.kick]
slot = 3
[moves.charge]
main = 1
sub = 4
clip = "walk"
after = "stamp"
[moves.stamp]
clip = "roar"
after = "stamp_2"
[moves.stamp_2]
clip = "kick"
[moves.topple]
clip = "walk"
carrier = [0, 3]
after = "topple_2"
[moves.topple_2]
clip = "roar"
after = "topple_3"
[moves.topple_3]
clip = "kick"
[moves.solo]
clip = "roar"
"""
Open = Callable[[str], PortDocument]


def chain(d: PortDocument, head: str = "topple") -> list[str]:
    return sequence.chain(d.manifest, head)


@pytest.fixture
def seq(doc: Open) -> PortDocument:
    return doc(BODY)


def undone(d: PortDocument, before: Manifest) -> bool:
    d.undo()
    return d.manifest == before


def test_append_in_the_middle(seq: PortDocument) -> None:
    before = seq.manifest
    msg = sequences.append_step(seq, "topple_2", "kick")
    assert msg == "moves.topple_4: a step after topple_2, playing kick"
    assert chain(seq) == ["topple", "topple_2", "topple_4", "topple_3"]
    step = seq.manifest.moves["topple_4"]
    assert (step.clip, step.carrier, step.steer) == ("kick", (0, 3), Steer(walls=False))
    names = list(seq.manifest.moves)
    assert names.index("topple_4") == names.index("topple_2") + 1, "it sits by its predecessor"
    assert undone(seq, before)


def test_append_at_the_end_and_to_a_lone_move(seq: PortDocument) -> None:
    sequences.append_step(seq, "topple_3", "walk")
    assert chain(seq)[-2:] == ["topple_3", "topple_4"]
    assert seq.manifest.moves["topple_4"].after is None
    sequences.append_step(seq, "solo", "walk")
    assert chain(seq, "solo") == ["solo", "solo_2"]
    assert seq.manifest.moves["solo_2"].carrier is None, "solo has none to copy"
    sequences.append_step(seq, "stamp_2", "walk")
    assert chain(seq, "charge")[-2:] == ["stamp_2", "charge_2"], "named after the head, a pair"


def test_append_keeps_a_loop(doc: Open) -> None:
    d = doc(BODY.replace(LOOP, LOOP + '\nafter = "topple_2"'))
    sequences.append_step(d, "topple_3", "walk")
    assert chain(d) == ["topple", "topple_2", "topple_3", "topple_4"]
    assert d.manifest.moves["topple_4"].after == "topple_2" and sequence.loops(d.manifest, "topple")


def test_append_refusals(seq: PortDocument) -> None:
    before = seq.manifest
    with pytest.raises(ManifestError, match="not an own move"):
        sequences.append_step(seq, "charge", "walk")
    with pytest.raises(ManifestError, match="no clip 'nope'"):
        sequences.append_step(seq, "solo", "nope")
    with pytest.raises(ManifestError, match="no move"):
        sequences.append_step(seq, "ghost", "walk")
    assert seq.manifest is before and not seq.can_undo()


def test_remove_relinks(seq: PortDocument) -> None:
    before = seq.manifest
    msg = sequences.remove_step(seq, "topple_2")
    assert msg == "moves.topple_2 removed; topple hands to topple_3"
    assert chain(seq) == ["topple", "topple_3"] and undone(seq, before)
    sequences.remove_step(seq, "topple_3")
    assert seq.manifest.moves["topple_2"].after is None


def test_remove_a_head_and_a_ring(doc: Open) -> None:
    d = doc(BODY)
    sequences.remove_step(d, "topple")
    assert sequence.heads(d.manifest) == ["charge", "topple_2", "solo"]
    ring = doc(
        '[clips.walk]\nslot = 1\n[clips.roar]\nslot = 2\n[moves.a]\nclip = "walk"\nafter = "b"\n'
        '[moves.b]\nclip = "roar"\nafter = "a"\n'
    )
    sequences.remove_step(ring, "b")
    assert ring.manifest.moves["a"].after is None, "a ring of two leaves a lone move"


def test_remove_refusals(seq: PortDocument) -> None:
    with pytest.raises(ManifestError, match="not an own move"):
        sequences.remove_step(seq, "charge")
    with pytest.raises(ManifestError, match="rides"):
        sequences.remove_step(seq, "stamp")  # the move before it is a pair move
    seq.edit(lambda m: m.moves.__setitem__("solo", Move(clip="roar", after="topple_3")))
    with pytest.raises(ManifestError, match="still used by .*solo"):
        sequences.remove_step(seq, "topple_3")  # two moves hand to it


def test_remove_takes_its_node_along(doc: Open) -> None:
    d = doc(BODY + "[behaviour.moves.topple_2]\nat = [10.0, 20.0]\n")
    sequences.remove_step(d, "topple_2")
    assert "topple_2" not in d.manifest.behaviour.moves
    d.undo()
    assert "topple_2" in d.manifest.behaviour.moves


def test_remove_refused_while_a_block_or_effect_names_it(doc: Open) -> None:
    d = doc(BODY + '[[rule]]\nplay = "topple_2"\non = "noticed"\n')
    with pytest.raises(ManifestError, match="still used by .*block b1 plays it"):
        sequences.remove_step(d, "topple_2")
    d = doc(BODY + '[[effect]]\nmove = "solo"\nframe = 1\nid = 5\nbone = 1\n')
    with pytest.raises(ManifestError, match="still used by effect 5"):
        sequences.remove_step(d, "solo")


def test_move_swaps_neighbours(seq: PortDocument) -> None:
    before = seq.manifest
    assert sequences.move_step(seq, "topple_2", 1) == "topple_2 is now step 3 of 3"
    assert chain(seq) == ["topple", "topple_3", "topple_2"]
    assert sorted(seq.manifest.moves) == sorted(before.moves), "no name changes"
    assert undone(seq, before)
    sequences.move_step(seq, "topple_2", -1)
    assert chain(seq, "topple_2") == ["topple_2", "topple", "topple_3"]
    assert sequence.heads(seq.manifest) == ["charge", "topple_2", "solo"]


def test_move_keeps_a_loop(doc: Open) -> None:
    d = doc(BODY.replace(LOOP, LOOP + '\nafter = "topple_2"'))
    sequences.move_step(d, "topple_2", 1)
    assert chain(d) == ["topple", "topple_3", "topple_2"]
    assert d.manifest.moves["topple_2"].after == "topple_3", "the loop still goes to step 2"


def test_move_refusals(seq: PortDocument) -> None:
    with pytest.raises(ManifestError, match="already the last step"):
        sequences.move_step(seq, "topple_3", 1)
    with pytest.raises(ManifestError, match="already the first step"):
        sequences.move_step(seq, "topple", -1)
    with pytest.raises(ManifestError, match="not an own move"):
        sequences.move_step(seq, "stamp", -1)  # the pair move's `after` would change
    with pytest.raises(ManifestError, match="already the last"):
        sequences.move_step(seq, "solo", 1)
    with pytest.raises(ManifestError, match="already"):
        sequences.move_step(seq, "topple_2", 2)


def test_split(seq: PortDocument) -> None:
    before = seq.manifest
    assert sequences.split(seq, "topple_2") == "topple_2 starts a sequence of its own"
    assert sequence.heads(seq.manifest) == ["charge", "topple", "topple_2", "solo"]
    assert chain(seq) == ["topple"] and chain(seq, "topple_2") == ["topple_2", "topple_3"]
    assert undone(seq, before)
    with pytest.raises(ManifestError, match="already starts"):
        sequences.split(seq, "topple")
    with pytest.raises(ManifestError, match="not an own move"):
        sequences.split(seq, "stamp")


def test_set_clip(seq: PortDocument) -> None:
    before = seq.manifest
    assert sequences.set_clip(seq, "topple_2", "walk") == "moves.topple_2 plays walk"
    assert seq.manifest.moves["topple_2"].clip == "walk" and undone(seq, before)
    with pytest.raises(ManifestError, match="no clip"):
        sequences.set_clip(seq, "topple_2", "nope")
    with pytest.raises(ManifestError, match="not an own move"):
        sequences.set_clip(seq, "charge", "roar")


def test_used_in(seq: PortDocument) -> None:
    m = seq.manifest
    assert sequences.used_in(m, "walk") == ["charge", "topple"]
    assert sequences.used_in(m, "roar") == ["stamp", "topple_2", "solo"]
    assert sequences.used_in(m, "kick") == ["stamp_2", "topple_3"]


def ends() -> continuity.Ends:
    """Clips 1, 2, 3 and 9 with one joint each: 2 starts where 1 ends, 3 a quarter turn off, 9 a
    half turn."""
    turn = np.array([[0.0, -1, 0], [1, 0, 0], [0, 0, 1]])
    half = np.diag([-1.0, 1, -1])
    eye = np.eye(3)
    first = np.stack([eye, eye, turn, half])[:, None]
    return continuity.Ends((1, 2, 3, 9), first, np.stack([eye] * 4)[:, None])


def test_pick_rows_rank_by_fit(seq: PortDocument) -> None:
    rows = sequences.pick_rows(seq.manifest, ends(), "walk")
    assert [r.name for r in rows] == ["walk", "roar", "kick"], "clips 1, 2, 3"
    assert [None if r.fit is None else round(r.fit) for r in rows] == [0, 0, 90]
    assert rows[0].used == ("charge", "topple") and rows[0].label == "walks"
    assert rows[1].frames == 6 and rows[1].used == ("stamp", "topple_2", "solo")
    worse = sequences.pick_rows(seq.manifest, ends(), "kick")
    assert [r.name for r in worse] == ["roar", "walk", "kick"], "kick ends where 1 and 2 begin"


def test_pick_rows_by_neighbours_and_unrated(seq: PortDocument) -> None:
    m = seq.manifest
    near = sequences.pick_rows(m, ends(), "kick", "near")
    assert [r.name for r in near] == ["roar", "walk", "kick"] and near[0].fit == pytest.approx(0)
    bare = sequences.pick_rows(m, None, "roar")
    assert [r.name for r in bare] == ["walk", "roar", "kick"] and {r.fit for r in bare} == {None}
    assert [r.name for r in sequences.pick_rows(m, None, "roar", "near")] == [
        "walk",
        "kick",
        "roar",
    ]
    assert [r.name for r in sequences.pick_rows(m, ends(), None)] == ["walk", "roar", "kick"]
