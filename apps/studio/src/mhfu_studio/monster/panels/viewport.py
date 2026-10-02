# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Joint indices over the viewport, projected with the frame's own camera so they cannot drift
from their joints; picking and the gizmo are `monster.tools`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mhfu_studio.monster.render.skeleton import C_SELECTED, project
from mhfu_studio.shell.overlay import Ink, Overlay

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace


def joint_labels(ws: MonsterWorkspace, o: Overlay) -> None:
    """Every joint's index with `show_joint_ids`, else only the selected one's, in its colour."""
    vp = ws.vp
    sk = None if vp is None else vp.skeleton
    if vp is None or sk is None or (not ws.show_joint_ids and sk.selected is None):
        return
    xy, ok = project(vp.camera, sk.positions, o.size)
    for j in range(len(xy)):
        if ok[j] and (ws.show_joint_ids or j == sk.selected):
            at = (float(xy[j][0]) + 6.0, float(xy[j][1]) - 7.0)
            o.text(at, str(j), C_SELECTED if j == sk.selected else Ink.TEXT)
