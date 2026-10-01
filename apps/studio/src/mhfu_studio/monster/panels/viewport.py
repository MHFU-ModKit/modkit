# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Over the viewport image: click-to-select (a shown volume wins over a joint) and joint indices.

The labels are imgui text projected with the frame's own camera, so they cannot drift from
their joints and stay one size at every zoom."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mhfu_studio.monster.render.skeleton import project
from mhfu_studio.shell.workspace import Gesture, View

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace

LEFT = 0


def pick(ws: MonsterWorkspace, view: View) -> Gesture:
    """A click selects the port's volume under it, else the joint; the drag stays the camera's."""
    from imgui_bundle import imgui

    vp = ws.vp
    if vp is None or vp.skeleton is None or not view.hovered:
        return Gesture.NONE
    if not imgui.is_mouse_clicked(LEFT) or imgui.is_mouse_dragging(LEFT):
        return Gesture.NONE
    io = imgui.get_io()
    x, y = view.mouse(io.mouse_pos.x, io.mouse_pos.y)
    cam, size = vp.camera, view.size
    if ws.show_attacks and ws.attacks_source == "port" and vp.attacks is not None:
        v = vp.attacks.pick(cam, size, x, y)
        i = None if v is None else vp.attacks.index_of(v)
        if i is not None:
            ws.select_attack_volume(None if ws.selected_attack_volume == i else i)
            return Gesture.NONE
    if ws.show_parts and ws.parts_source == "port" and vp.hitboxes is not None:
        v = vp.hitboxes.pick(cam, size, x, y)
        i = None if v is None else vp.hitboxes.index_of(v)
        if i is not None:
            ws.select_volume(None if ws.selected_volume == i else i)
            return Gesture.NONE
    hit = vp.skeleton.pick(cam, size, x, y)
    if hit is not None:
        vp.select_joint(None if vp.selected_joint == hit else hit)
    return Gesture.NONE


def joint_labels(ws: MonsterWorkspace, view: View) -> None:
    """Every joint's index with `show_joint_ids`, else only the selected one's."""
    from imgui_bundle import imgui

    vp = ws.vp
    sk = None if vp is None else vp.skeleton
    if vp is None or sk is None or (not ws.show_joint_ids and sk.selected is None):
        return
    xy, ok = project(vp.camera, sk.positions, view.size)
    draw = imgui.get_window_draw_list()
    plain = imgui.get_color_u32(imgui.ImVec4(0.80, 0.84, 0.92, 0.90))
    hot = imgui.get_color_u32(imgui.ImVec4(0.98, 0.30, 0.32, 1.0))
    ox, oy = view.origin
    for j in range(len(xy)):
        if not ok[j] or (not ws.show_joint_ids and j != sk.selected):
            continue
        pos = imgui.ImVec2(ox + float(xy[j][0]) + 6, oy + float(xy[j][1]) - 7)
        draw.add_text(pos, hot if j == sk.selected else plain, str(j))
