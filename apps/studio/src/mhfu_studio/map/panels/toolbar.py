# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Above the viewport: the tools, the pick kinds, gizmo space and snapping, undo and redo."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.edit import COLLISION, FACE, GROUP, OBJECT
from ..tools import MOVE, ROTATE, SCALE, SELECT

if TYPE_CHECKING:
    from ..workspace import MapWorkspace

TOOL_COLOR = (0.25, 0.5, 0.85, 1.0)
KIND_COLOR = (0.30, 0.55, 0.35, 1.0)


def draw(ws: MapWorkspace) -> None:
    from imgui_bundle import imgui

    t = ws.tools

    def button(label: str, on: bool, color: tuple[float, ...]) -> bool:
        if on:
            imgui.push_style_color(imgui.Col_.button.value, imgui.ImVec4(*color))
        hit = bool(imgui.small_button(label))
        if on:
            imgui.pop_style_color()
        imgui.same_line()
        return hit

    for label, tool, key in (
        ("Select", SELECT, "Q"),
        ("Move", MOVE, "W"),
        ("Rotate", ROTATE, "E"),
        ("Scale", SCALE, "R"),
    ):
        if button(f"{label} ({key})", t.tool == tool, TOOL_COLOR):
            t.set_tool(tool)
    imgui.text_disabled("|")
    imgui.same_line()
    for label, kind, key in (
        ("group", GROUP, "1"),
        ("object", OBJECT, "2"),
        ("face", FACE, "3"),
        ("collision", COLLISION, "4"),
    ):
        if button(f"{label} ({key})", t.kind == kind, KIND_COLOR):
            t.set_kind(kind)
    imgui.text_disabled("|")
    imgui.same_line()
    _, t.space_local = imgui.checkbox("local", t.space_local)
    imgui.same_line()
    _, t.snap = imgui.checkbox("snap", t.snap)
    imgui.same_line()
    if imgui.small_button("undo"):
        ws.undo()
    imgui.same_line()
    if imgui.small_button("redo"):
        ws.redo()
    imgui.same_line()
    imgui.text_disabled("  shift-click adds; drag a box in select mode; ESC clears")
