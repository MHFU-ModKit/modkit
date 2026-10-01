# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Over the viewport: click-to-select (a shown port volume wins over a joint) and joint indices,
projected with the frame's own camera so they cannot drift from their joints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mhfu_studio.monster.render.skeleton import C_SELECTED, project
from mhfu_studio.shell.overlay import Ink, Overlay

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace


def pick(ws: MonsterWorkspace, x: float, y: float, size: tuple[int, int]) -> None:
    """Selects the port's volume under `(x, y)`, else the joint; a second click deselects."""
    vp = ws.vp
    if vp is None or vp.skeleton is None:
        return
    cam = vp.camera
    if ws.show_attacks and ws.attacks_source == "port" and vp.attacks is not None:
        v = vp.attacks.pick(cam, size, x, y)
        i = None if v is None else vp.attacks.index_of(v)
        if i is not None:
            ws.select_attack_volume(None if ws.selected_attack_volume == i else i)
            return
    if ws.show_parts and ws.parts_source == "port" and vp.hitboxes is not None:
        v = vp.hitboxes.pick(cam, size, x, y)
        i = None if v is None else vp.hitboxes.index_of(v)
        if i is not None:
            ws.select_volume(None if ws.selected_volume == i else i)
            return
    hit = vp.skeleton.pick(cam, size, x, y)
    if hit is not None:
        vp.select_joint(None if vp.selected_joint == hit else hit)


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
