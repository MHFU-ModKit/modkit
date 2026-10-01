# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Textures panel: the bank as thumbnails with who wears each slot, and the import that
spends a slot (a PNG copied into the document's assets/, another stage's slot, a flat
colour). A texture edit lands in the running game at once."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from mhfu_studio.shell.widgets import help_marker, pick_file

from ..core.shapes import next_asset_name

if TYPE_CHECKING:
    from ..workspace import MapWorkspace


class TexturesPanel:
    def __init__(self, ws: MapWorkspace) -> None:
        self.ws = ws
        self.zoom = 96
        self.from_stage = 0
        self.from_slot = 0
        self.rgb = [128, 128, 128]

    def draw(self) -> None:
        from imgui_bundle import imgui

        ws = self.ws
        sc, vp = ws.scene, ws.vp
        if sc is None or vp is None or vp.mesh is None:
            imgui.text("no section loaded")
            return
        _, self.zoom = imgui.slider_int("size", self.zoom, 48, 192)
        imgui.same_line()
        imgui.text(
            f"{len(sc.textures)} slots, {sc.sub_sizes[1]} bytes; every one is CLUT-indexed and"
            " no slot is spare"
        )
        self._import()
        worn: dict[int, list[str]] = {}
        for g in sc.groups:
            if g.texture is not None:
                worn.setdefault(g.texture, []).append(f"s{g.sub}g{g.vg_rec}")
        per_row = max(1, int(imgui.get_content_region_avail().x // (self.zoom + 12)))
        for i, t in enumerate(sc.textures):
            tex = vp.mesh.gl_texture(t.index)
            if tex is None:
                continue
            imgui.begin_group()
            side = float(self.zoom)
            imgui.image(imgui.ImTextureRef(int(tex.glo)), imgui.ImVec2(side, side))
            wearers = ", ".join(worn.get(t.index, ["nothing"]))
            if imgui.is_item_hovered():
                imgui.set_tooltip(
                    f"slot {t.index}  {t.width}x{t.height}  mode {t.mode}  {t.colours} colours\n"
                    f"worn by: {wearers}\n(click: the import target)"
                )
            if imgui.is_item_clicked():
                ws.tex_target = t.index
            mark = " <" if ws.tex_target == t.index else ""
            imgui.text(f"{t.index}  {t.width}x{t.height}/{t.colours}{mark}")
            imgui.text_disabled(" ".join(worn.get(t.index, ["-"]))[:18])
            imgui.end_group()
            if (i + 1) % per_row:
                imgui.same_line()

    def _import(self) -> None:
        from imgui_bundle import imgui

        ws = self.ws
        sc, sess = ws.scene, ws.session
        if sc is None or sess is None:
            return
        slot = ws.tex_target
        img = sc.texture(slot)
        size = f"  ({img.width}x{img.height})" if img else ""
        imgui.text(f"import into slot {slot}{size}:")
        imgui.same_line()
        if imgui.small_button("PNG..."):
            f = pick_file(f"A PNG for slot {slot}", ("PNG", "*.png"))
            if f:
                self.png(Path(f))
        imgui.same_line()
        _, ws.tex_keep = imgui.checkbox("keep palette", ws.tex_keep)
        imgui.same_line()
        help_marker(
            "re-index the new pixels through the slot's shipped CLUT instead of building a new"
            " palette: same colours, new arrangement."
        )
        imgui.set_next_item_width(90)
        _, self.from_stage = imgui.input_int("stage", self.from_stage)
        imgui.same_line()
        imgui.set_next_item_width(70)
        _, self.from_slot = imgui.input_int("slot", self.from_slot)
        imgui.same_line()
        if imgui.small_button("copy that slot here"):
            source = (self.from_stage, self.from_slot)
            ws.do("texture", sess.import_texture, slot, from_=source, keep_palette=ws.tex_keep)
        imgui.same_line()
        imgui.set_next_item_width(160)
        _, self.rgb = imgui.input_int3("rgb", self.rgb)
        imgui.same_line()
        if imgui.small_button("flat colour"):
            rgb = [max(0, min(255, int(v))) for v in self.rgb]
            ws.do("texture", sess.import_texture, slot, rgb=rgb)

    def png(self, source: Path) -> None:
        """Copies a PNG into the document's assets/ and spends the target slot on it."""
        ws = self.ws
        sess = ws.session
        if sess is None:
            return
        if sess.base_dir is None:
            ws.message = "save the document first: the image is copied into its assets/"
            return
        name = next_asset_name(sess.base_dir, f"tex{ws.tex_target}", ".png")
        dst = sess.base_dir / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dst)
        ws.do("texture", sess.import_texture, ws.tex_target, png=name, keep_palette=ws.tex_keep)
