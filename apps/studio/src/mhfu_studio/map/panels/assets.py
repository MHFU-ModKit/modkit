# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Assets panel: the row's other sections, their objects and texture slots, to copy here."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from mhfu_studio.shell.widgets import plain

from ..core import shapes
from ..core.edit import OBJECT, EditError, Selection
from ..core.scene import Array, MapScene, MeshGroup

if TYPE_CHECKING:
    from ..workspace import MapWorkspace

OK, SHORT = (0.5, 0.9, 0.5, 1.0), (1.0, 0.5, 0.4, 1.0)
OBJECTS = 60
"""The largest objects of a group offered, at least three vertices each."""


class AssetsPanel:
    def __init__(self, ws: MapWorkspace) -> None:
        self.ws = ws
        self.stage = 0
        self.group = 0
        self.object = 0
        self.slot = 0
        self.into = 0

    def draw(self) -> None:
        from imgui_bundle import imgui

        ws = self.ws
        sc, sess = ws.scene, ws.session
        if sc is None or sess is None or ws.row is None or ws.atlas is None:
            imgui.text("no section loaded")
            return
        secs = [x for x in ws.atlas.row(ws.row).sections if x.present]
        if not secs:
            imgui.text("the row has no other section")
            return
        labels = [
            f"st{x.stage:03d}  {x.name}{'  (loaded)' if x.stage == sc.stage else ''}" for x in secs
        ]
        self.stage = max(0, min(self.stage, len(secs) - 1))
        _, self.stage = imgui.combo("section", self.stage, labels)
        src = secs[self.stage].stage
        other = sc if src == sc.stage else ws.asset_scene(src)
        if other is None:
            imgui.text(f"st{src:03d} could not be loaded")
            return
        imgui.text(
            f"{len(other.groups)} groups, {sum(g.n_components for g in other.groups)} objects,"
            f" {len(other.textures)} textures"
        )
        groups = [g for g in other.groups if g.n_faces]
        if not groups:
            return
        self.group = max(0, min(self.group, len(groups) - 1))
        names = [
            f"{g.label}  {g.n_faces} faces  tex {'-' if g.untextured else g.texture}"
            f"  {g.n_components} obj"
            for g in groups
        ]
        _, self.group = imgui.combo("group##asset", self.group, names)
        g = groups[self.group]
        sizes = np.bincount(g.components, minlength=g.n_components)
        order = [int(c) for c in np.argsort(sizes)[::-1] if sizes[c] >= 3][:OBJECTS]
        if not order:
            imgui.text_disabled("no object with faces")
            return
        self.object = max(0, min(self.object, len(order) - 1))
        _, self.object = imgui.combo("object", self.object, [object_label(g, c) for c in order])
        cid = order[self.object]
        ids = g.component_vertices(cid)
        n_tris = len(g.component_faces(cid))
        self.into = max(0, min(self.into, len(sc.groups) - 1))
        _, self.into = imgui.combo("into group here", self.into, [x.label for x in sc.groups])
        tg = sc.groups[self.into]
        sacrifice = ws.selection if tg.key in ws.selection.vertices else None
        cap = sess.capacity(tg.key, sacrifice)
        note = " (replacing the selection)" if sacrifice else ""
        imgui.text_colored(
            imgui.ImVec4(*(OK if n_tris <= cap else SHORT)),
            f"needs {n_tris} triangles; {cap} available in {tg.label}{note}",
        )
        if g.texture is not None and not g.untextured:
            here, there = sc.texture(g.texture), other.texture(g.texture)
            if here is None or there is None or not np.array_equal(here.rgba, there.rgba):
                imgui.text_wrapped(
                    plain(
                        f"it wears slot {g.texture} of st{src:03d}, which is not this section's"
                        f" slot {g.texture}: copy the texture too (below), or accept"
                        f" {tg.label}'s texels"
                    )
                )
        if imgui.button("copy the object here"):
            self.copy(other, g, ids, tg, sacrifice, src)
        imgui.separator()
        imgui.text(f"textures of st{src:03d}")
        if other.textures:
            self.slot = max(0, min(self.slot, len(other.textures) - 1))
            slots = [f"{t.index}  {t.width}x{t.height}" for t in other.textures]
            _, self.slot = imgui.combo("slot##asset", self.slot, slots)
            source = other.textures[self.slot]
            imgui.text_disabled(
                f"-> slot {ws.tex_target} here (click a slot in Textures to change)"
            )
            if imgui.button(f"copy that texture into slot {ws.tex_target}"):
                ws.do(
                    "texture",
                    sess.import_texture,
                    ws.tex_target,
                    from_=(src, source.index),
                    keep_palette=ws.tex_keep,
                )

    def copy(
        self,
        other: MapScene,
        g: MeshGroup,
        ids: Array,
        tg: MeshGroup,
        sacrifice: Selection | None,
        src: int,
    ) -> None:
        """A native object of another section, packed into a group here."""
        ws = self.ws
        sess = ws.session
        if sess is None:
            return
        if sess.base_dir is None:
            ws.message = "save the document first: the copy is written into its assets/ folder"
            return
        at = tuple(ws.add.placement())
        v, t, uv = shapes.from_group(other, g.key, ids, at=at, rotate_y=ws.add.rotate)
        name = shapes.next_asset_name(sess.base_dir, f"st{src:03d}_g{g.vg_rec}")
        shapes.write_obj(sess.base_dir / name, v, t, uv, name.split("/")[-1].rsplit(".", 1)[0])
        colour = None
        if g.colours is not None:
            colour = [int(x) for x in g.colours[ids].mean(0).round()]
        uv_mode = "obj" if (g.texture == tg.texture and not g.untextured) else "planar"
        try:
            new = sess.add_mesh(
                tg.key, name, len(t), sacrifice=sacrifice, uv=uv_mode, colour=colour
            )
        except EditError as e:
            ws.message = f"refused: {e}"
            (sess.base_dir / name).unlink(missing_ok=True)
            return
        ws.after_commit(sess.ops[-1:])
        ws.tools.kind = OBJECT
        ws.tools.select(new)


def object_label(g: MeshGroup, c: int) -> str:
    ids = g.component_vertices(c)
    lo, hi = g.positions[ids].min(0), g.positions[ids].max(0)
    size = hi - lo
    return (
        f"object {c}  {len(ids)} v  {len(g.component_faces(c))} f"
        f"  {size[0]:.0f}x{size[1]:.0f}x{size[2]:.0f}"
    )
