# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Map panel: every row and its sections in walking order; a click loads a section alone."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mhfu_studio.shell.widgets import plain

from ..core.atlas import ROW_NAMES

if TYPE_CHECKING:
    from ..workspace import MapWorkspace

ERROR = (1.0, 0.5, 0.4, 1.0)


class BrowserPanel:
    def __init__(self, ws: MapWorkspace) -> None:
        self.ws = ws

    def draw(self) -> None:
        from imgui_bundle import imgui

        ws = self.ws
        if ws.load_error:
            imgui.text_colored(imgui.ImVec4(*ERROR), plain(ws.load_error))
        if ws.atlas is None:
            imgui.text_wrapped(ws.data_error)
            return
        cur = ws.scene.stage if ws.scene else None
        for r in ws.atlas.live_rows():
            flags = imgui.TreeNodeFlags_.default_open.value if r.index == ws.row else 0
            named = r.name if r.index in ROW_NAMES else ""
            label = f"row {r.index:2d}  {named + '  ' if named else ''}({len(r.sections)} sections)"
            if not imgui.tree_node_ex(f"{label}##row{r.index}", flags):
                continue
            for s in r.sections:
                selected = s.stage == cur and r.index == ws.row
                tag = "entry " if s.is_entry else "      "
                stub = "" if s.present else "  (placeholder)"
                name = f"{tag}[{s.slot}] st{s.stage:03d}  {s.name}{stub}"
                if not s.present:
                    imgui.begin_disabled()
                if imgui.selectable(f"{name}##s{r.index}_{s.stage}", selected)[0]:
                    ws.load_stage(s.stage, row=r.index)
                if not s.present:
                    imgui.end_disabled()
            imgui.tree_pop()
        if ws.scene is not None:
            imgui.separator()
            imgui.text(f"exits of st{ws.scene.stage:03d}:")
            for e in ws.scene.exits:
                if imgui.small_button(f"-> st{e.target:03d} {e.target_name}##exit{e.index}"):
                    ws.load_stage(e.target)
            arrivals = ws.vp.arrivals if ws.vp else []
            if arrivals:
                names = ", ".join(sorted({f"st{f:03d}" for f, _ in arrivals}))
                imgui.text(f"arrive here from: {names}")
