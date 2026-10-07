# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Picking and joint labels through the workspace's pointer and paint hooks."""

import numpy as np
from mhfu_studio.monster.render.skeleton import C_SELECTED, project
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.input import Button, Pointer
from mhfu_studio.shell.overlay import Ink, Recorder
from mhfu_studio.shell.workspace import Gesture

SIZE = (320, 240)


def click(ws: MonsterWorkspace, x: float, y: float, to: tuple[float, float] | None = None) -> None:
    gx, gy = to or (x, y)
    held = ws.pointer(Pointer("press", x, y, SIZE, Button.LEFT, Button.LEFT))
    moved = ws.pointer(Pointer("move", gx, gy, SIZE, buttons=Button.LEFT))
    up = ws.pointer(Pointer("release", gx, gy, SIZE, Button.LEFT))
    assert held == moved == up == Gesture.NONE, "the camera keeps every drag"


def joint(ws: MonsterWorkspace) -> tuple[int, float, float]:
    """The last joint on screen and where."""
    assert ws.vp is not None and ws.vp.skeleton is not None
    xy, ok = project(ws.vp.camera, ws.vp.skeleton.positions, SIZE)
    j = int(np.flatnonzero(ok)[-1])
    return j, float(xy[j][0]), float(xy[j][1])


def test_click_picks_a_joint(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.vp is not None
    j, x, y = joint(ws)
    click(ws, x, y)
    assert ws.vp.selected_joint == j
    click(ws, x, y)
    assert ws.vp.selected_joint is None, "a second click lets go"


def test_drag_does_not_pick(workspace: MonsterWorkspace) -> None:
    j, x, y = joint(workspace)
    click(workspace, x, y, to=(x + 30, y))
    assert workspace.vp is not None and workspace.vp.selected_joint is None


def test_selected_joint_is_labelled(workspace: MonsterWorkspace) -> None:
    ws = workspace
    ws.turn.shown = False
    o = Recorder(SIZE)
    ws.paint(o)
    assert o.calls == []
    j, x, y = joint(ws)
    click(ws, x, y)
    o = Recorder(SIZE)
    ws.paint(o)
    assert o.texts() == [str(j)]
    _, at, _, color, *_ = o.calls[0]
    assert color == C_SELECTED and at == (x + 6.0, y - 7.0)


def test_joint_ids_label_every_joint(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.vp is not None and ws.vp.skeleton is not None
    ws.show_joint_ids = True
    ws.turn.shown = False
    o = Recorder(SIZE)
    ws.paint(o)
    _, ok = project(ws.vp.camera, ws.vp.skeleton.positions, SIZE)
    assert o.texts() == [str(i) for i in np.flatnonzero(ok)]
    assert {c[3] for c in o.calls} == {Ink.TEXT}


def test_a_port_volume_wins_over_a_joint(workspace: MonsterWorkspace) -> None:
    ws = workspace
    sess, host = ws.part_session, ws.host_parts()
    assert sess is not None and host is not None and ws.vp is not None
    sess.adopt_volumes(host.spheres()[:2])
    ws.show_parts, ws.parts_source = True, "port"
    ws.sync()
    ov = ws.vp.hitboxes
    assert ov is not None
    xy, ok = project(ws.vp.camera, ov.world_centres(), SIZE)
    i = int(np.flatnonzero(ok)[0])
    click(ws, float(xy[i][0]), float(xy[i][1]))
    assert ws.selected_volume == ov.index_of(ov.shown()[i])
    assert ws.vp.selected_joint is None
