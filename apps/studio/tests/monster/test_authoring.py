# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Own moves through the workspace's commands, over the synthetic port."""

from __future__ import annotations

import math

import pytest
from mhfu_port import manifest
from mhfu_port.manifest import MOVE_ATTACKS, AttackWindow, ManifestError, Move, Steer
from mhfu_studio.monster import authoring
from mhfu_studio.monster.workspace import MonsterWorkspace


def own(ws: MonsterWorkspace, name: str = "stamp") -> Move:
    """The synthetic port's walk (anim 1, 10 frames) made an own move, selected."""
    ws.play_slot(1)
    ws.new_move(name)
    mv = ws.own_move()
    assert mv is not None and ws.move == name
    return mv


def drag(ws: MonsterWorkspace, lane: int, a: float, b: float) -> bool:
    assert ws.press_window(lane, a)
    ws.windows.move((a + b) / 2)
    return ws.windows.release(b)


def test_authored_through_the_commands(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.doc is not None
    states = [ws.manifest]
    own(ws)
    states.append(ws.manifest)
    ws.next_window_id = 6
    assert drag(ws, 0, 2.0, 5.2)
    states.append(ws.manifest)
    ws.next_window_id = 7
    assert drag(ws, 1, 9.0, 6.0), "drawn backwards"
    states.append(ws.manifest)
    assert ws.set_steer(turn="fixed")
    states.append(ws.manifest)
    assert ws.set_steer(angle=90.0)
    states.append(ws.manifest)
    assert ws.set_move(after="charge")
    states.append(ws.manifest)
    path = ws.doc.save()
    want = Move(
        clip="walk",
        attacks=[AttackWindow(6, 2, 5), AttackWindow(7, 6, 9)],
        steer=Steer("fixed", angle=90.0, frames=5),
        after="charge",
    )
    assert manifest.load(path).moves["stamp"] == want
    for st in reversed(states[:-1]):
        ws.doc.undo()
        ws.refresh()
        assert ws.manifest == st
    assert not ws.doc.can_undo() and ws.move is None, "the move went with its undo"
    for st in states[1:]:
        ws.doc.redo()
        ws.refresh()
        assert ws.manifest == st


def test_new_move_names_and_selects(workspace: MonsterWorkspace) -> None:
    ws = workspace
    ws.play_slot(1)
    ws.new_move()
    ws.play_slot(1)
    ws.new_move()
    m = ws.manifest
    assert m is not None and m.moves["walk"] == Move(clip="walk") and "walk_2" in m.moves
    assert ws.move == "walk_2" and ws.pair is None
    ws.play_slot(2)
    ws.new_move()
    assert ws.manifest is not None and ws.manifest.moves["clip_02"].clip == "clip_02", "named"


def test_rename_delete_and_users(workspace: MonsterWorkspace) -> None:
    ws = workspace
    own(ws)
    ws.rename_move("stomp")
    assert ws.move == "stomp" and ws.manifest is not None and "stomp" in ws.manifest.moves
    ws.select_move("charge")
    assert ws.pair == (1, 4) and ws.own_move() is None
    ws.set_move(after="stomp")
    assert "own move" in ws.message
    assert ws.doc is not None
    ws.doc.edit(lambda m: setattr(m.moves["charge"], "after", "stomp"))
    ws.select_move("stomp")
    ws.delete_move()
    assert "still used by moves.charge" in ws.message and ws.move == "stomp"
    ws.doc.edit(lambda m: setattr(m.moves["charge"], "after", None))
    ws.delete_move()
    assert ws.move is None and "stomp" not in ws.manifest.moves  # type: ignore[union-attr]


def test_steer_fields_go_together(workspace: MonsterWorkspace) -> None:
    ws = workspace
    own(ws)
    assert ws.set_steer(turn="hunter", rate=64)
    assert ws.own_move().steer == Steer("hunter", rate=64)  # type: ignore[union-attr]
    assert ws.set_steer(turn="fixed")
    s = ws.own_move().steer  # type: ignore[union-attr]
    assert (s.angle, s.frames, s.rate) == (0.0, 5, None), "the clip's AI frames, no rate"
    assert ws.set_steer(turn="away")
    s = ws.own_move().steer  # type: ignore[union-attr]
    assert (s.angle, s.frames) == (None, None)
    assert not ws.set_steer(angle=10.0) and "angle goes with turn = fixed" in ws.message


def test_window_edges_and_span(workspace: MonsterWorkspace) -> None:
    ws = workspace
    own(ws)
    ws.next_window_id = 6
    drag(ws, 0, 2.0, 6.0)
    drag(ws, 0, 2.0, 0.4)
    assert ws.own_move().attacks == [AttackWindow(6, 0, 6)]  # type: ignore[union-attr]
    drag(ws, 0, 6.0, 30.0)
    assert ws.own_move().attacks[0].end == 10, "the end stays in the clip"  # type: ignore[union-attr]
    drag(ws, 0, 5.0, 3.0)
    assert ws.own_move().attacks == [AttackWindow(6, 0, 10)], "no room to move"  # type: ignore[union-attr]
    drag(ws, 0, 10.0, 4.0)
    drag(ws, 0, 2.0, 7.0)
    assert ws.own_move().attacks == [AttackWindow(6, 5, 9)], "the span moves whole"  # type: ignore[union-attr]
    drag(ws, 0, 9.0, 2.0)
    assert ws.own_move().attacks == [AttackWindow(6, 5, 6)], "the end stays after the start"  # type: ignore[union-attr]


def test_a_click_adds_an_open_window(workspace: MonsterWorkspace) -> None:
    ws = workspace
    own(ws)
    ws.next_window_id = 7
    assert ws.press_window(0, 4.2) and ws.windows.release(4.2)
    mv = ws.own_move()
    assert mv is not None and mv.attacks == [AttackWindow(7, 4)] and ws.picked_window == 0
    assert authoring.live(mv.attacks, 3.9) == [] and authoring.live(mv.attacks, 9.0) == [0]
    drag(ws, 0, 10.0, 8.0)
    assert ws.own_move().attacks == [AttackWindow(7, 4, 8)], "its open end takes an end"  # type: ignore[union-attr]


def test_the_move_player_holds_four(workspace: MonsterWorkspace) -> None:
    ws = workspace
    own(ws)
    for i in range(MOVE_ATTACKS):
        drag(ws, i, i, i + 1)
    assert not ws.press_window(MOVE_ATTACKS, 2.0)
    assert f"holds {MOVE_ATTACKS} attacks" in ws.message
    assert ws.doc is not None
    with pytest.raises(ManifestError, match="holds"):
        authoring.add_window(ws.doc, "stamp", AttackWindow(6, 1))


def test_id_and_remove(workspace: MonsterWorkspace) -> None:
    ws = workspace
    own(ws)
    assert ws.window_id() == 6, "the base monster's first record with a hit"
    drag(ws, 0, 1.0, 3.0)
    assert ws.set_window(0, id=7) and ws.window_id() == 7
    assert ws.remove_window() and ws.own_move().attacks == []  # type: ignore[union-attr]


def test_live_attack_lights_its_group(workspace: MonsterWorkspace) -> None:
    ws = workspace
    own(ws)
    ws.next_window_id = 6
    drag(ws, 0, 2.0, 6.0)
    ws.show_attacks = True
    ws.sync()
    assert ws.vp is not None and ws.vp.attacks is not None
    assert ws.pair_sets() == [2] and ws.visible_sets() == [2]
    ws.seek(4.0)
    ws.light_live()
    assert ws.live_windows() == [0] and ws.vp.attacks.selected_group == 2
    ws.seek(8.0)
    ws.light_live()
    assert ws.live_windows() == [] and ws.vp.attacks.selected_group is None


def test_fixed_steer_turns_the_preview(workspace: MonsterWorkspace) -> None:
    ws = workspace
    own(ws)
    ws.set_steer(turn="fixed")
    ws.set_steer(angle=90.0)
    assert ws.vp is not None and ws.vp.actor is not None
    assert ws.vp.actor.steer == Steer("fixed", angle=90.0, frames=5)
    ws.select_move("charge")
    assert ws.vp.actor.steer is None, "a pair's move turns as its clip does"


def test_copy_lua_leaves_own_moves_out(workspace: MonsterWorkspace) -> None:
    from mhfu_studio.monster import actions

    ws = workspace
    own(ws)
    assert ws.manifest is not None
    lua = actions.lua_moves(ws.manifest)
    assert "charge = " in lua and "stamp" not in lua and "None" not in lua


def test_reveal_selects_an_own_move(workspace: MonsterWorkspace) -> None:
    ws = workspace
    own(ws)
    ws.select_move("charge")
    ws.reveal(("moves", "stamp"))
    assert ws.move == "stamp" and ws.take_focus() == "Moves"
    assert math.isclose(ws.vp.playback.phase, 0.0)  # type: ignore[union-attr]
