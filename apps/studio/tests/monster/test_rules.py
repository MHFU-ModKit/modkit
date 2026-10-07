# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Rules through the workspace's commands, over the synthetic port."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

import pytest
from mhfu_port import manifest
from mhfu_port.manifest import EVENTS, SEAM_RULES, Manifest, ManifestError, Rule
from mhfu_studio.monster import rules
from mhfu_studio.monster.workspace import MonsterWorkspace


def stamp(ws: MonsterWorkspace) -> None:
    """The synthetic port's walk made the own move `stamp`, selected."""
    ws.play_slot(1)
    ws.new_move("stamp")
    assert ws.move == "stamp"


def refusal(m: Manifest, index: int, **fields: object) -> str:
    """What the manifest's own check says of rule `index` with `fields`."""
    bad = dataclasses.replace(m, rules=list(m.rules))
    bad.rules[index] = dataclasses.replace(bad.rules[index], **fields)
    with pytest.raises(ManifestError) as e:
        manifest.check(bad)
    return str(e.value)


def test_authored_through_the_commands(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.doc is not None
    stamp(ws)
    states = [ws.manifest]
    ws.new_rule()
    assert ws.picked_rule == 0
    states.append(ws.manifest)
    assert ws.set_rule(on="flinch", part=0, count=None)
    states.append(ws.manifest)
    ws.new_rule()
    assert ws.picked_rule == 1
    states.append(ws.manifest)
    assert ws.set_rule(on=None, from_move="charge", min_frames=20, dist=(0, 1200), count=None)
    states.append(ws.manifest)
    path = ws.doc.save()
    want = [
        Rule("stamp", on="flinch", part=0),
        Rule("stamp", from_move="charge", min_frames=20, dist=(0.0, 1200.0)),
    ]
    assert manifest.load(path).rules == want
    for st in reversed(states[:-1]):
        ws.doc.undo()
        ws.refresh()
        assert ws.manifest == st
    assert ws.picked_rule is None, "the rule went with its undo"
    for st in states[1:]:
        ws.doc.redo()
        ws.refresh()
        assert ws.manifest == st


def test_refusals_are_the_manifests(workspace: MonsterWorkspace) -> None:
    ws = workspace
    stamp(ws)
    ws.new_rule()
    m = ws.manifest
    assert m is not None
    for bad in ({"dist": (5.0, 1.0)}, {"part": 0}, {"receding": True, "closing": True}):
        assert not ws.set_rule(**bad)
        assert ws.message == refusal(m, 0, **bad) and ws.manifest is m, bad
    while len(ws.manifest.rules) < SEAM_RULES:
        ws.new_rule()
    ws.new_rule()
    assert f"the seam holds {SEAM_RULES}" in ws.message
    assert len(ws.manifest.rules) == SEAM_RULES


def test_fields_that_go_together(workspace: MonsterWorkspace) -> None:
    ws = workspace
    stamp(ws)
    ws.new_rule()
    ws.set_rule(on="flinch", part=1)
    ws.set_rule(on="noticed")
    assert ws.manifest.rules[0].part is None, "noticing names no part"
    ws.set_rule(play="charge", mode=2)
    ws.set_rule(play="stamp")
    assert ws.manifest.rules[0].mode == 0, "an own move enters its carrier"
    ws.delete_rule()
    assert ws.manifest.rules == [] and ws.picked_rule is None


def test_new_rule_needs_a_move(workspace: MonsterWorkspace) -> None:
    ws = workspace
    ws.new_rule()
    assert ws.manifest.rules == [Rule("charge", on="noticed", count=1)], "the first move"
    assert ws.doc is not None
    ws.doc.edit(lambda m: (m.rules.clear(), m.moves.clear(), m.effects.clear()))
    ws.refresh()
    ws.new_rule()
    assert ws.message == "make a move first: a rule plays one"


def test_takes_part() -> None:
    assert {e for e in EVENTS if rules.takes_part(e)} == {"flinch", "part_broken"}
    assert not rules.takes_part(None)


SKID = """
[clips.walk]
slot = 1

[moves.skid]
main = 1
sub = 4
clip = "walk"

[moves.stamp]
clip = "walk"

[moves.flinch_head]
anim = 3

[parts.head]
index = 0
"""


@pytest.mark.parametrize(
    ("rule", "words"),
    [
        (Rule("flinch_head", on="flinch", part=0), "on a flinch of the head, play flinch_head"),
        (
            Rule("stamp", from_move="skid", min_frames=20, dist=(0.0, 1200.0)),
            "after the skid for 20 frames, within 1200, play stamp",
        ),
        (
            Rule("skid", from_main=[0, 1], dist=(300.0, 900.0), closing=True, mode=1, count=2),
            "during main states 0 or 1, between 300 and 900 away, while the hunter closes in,"
            " play skid in mode 1, at most 2 times",
        ),
        (
            Rule("stamp", on="part_broken", part=3, receding=True, cooldown=60, count=1),
            "on a break of part 3, while the hunter moves away, play stamp, then wait 60 frames,"
            " once",
        ),
        (
            Rule("stamp", on="tail_cut", dist=(500.0, 1.0e9)),
            "on the tail cut, beyond 500, play stamp",
        ),
    ],
)
def test_sentence(make: Callable[[str], Manifest], rule: Rule, words: str) -> None:
    assert rules.sentence(rule, make(SKID)) == words
