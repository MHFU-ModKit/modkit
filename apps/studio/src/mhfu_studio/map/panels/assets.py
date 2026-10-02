# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Assets panel: the row's other sections, their objects and texture slots, to copy here."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

import numpy as np
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from mhfu_studio.shell.text import plain
from mhfu_studio.ui import kit

from ..core import shapes
from ..core.edit import OBJECT, EditError, Selection
from .common import Gate, thumbnail

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

    from ..core.scene import Array, Key, MapScene, MeshGroup, TextureImage
    from ..workspace import MapWorkspace

T = TypeVar("T")
OBJECTS = 60
"""The largest objects of a group offered, at least three vertices each."""
THUMB = 96


def object_label(g: MeshGroup, c: int) -> str:
    ids = g.component_vertices(c)
    size = g.positions[ids].max(0) - g.positions[ids].min(0)
    return (
        f"object {c}  {len(ids)} v  {len(g.component_faces(c))} f"
        f"  {size[0]:.0f}x{size[1]:.0f}x{size[2]:.0f}"
    )


def objects(g: MeshGroup) -> list[tuple[str, str]]:
    """(component, label) of the group's largest objects, largest first."""
    sizes = np.bincount(g.components, minlength=g.n_components)
    order = [int(c) for c in np.argsort(sizes)[::-1] if sizes[c] >= 3][:OBJECTS]
    return [(str(c), object_label(g, c)) for c in order]


def group_id(k: Key) -> str:
    return f"{k[0]}.{k[1]}"


def key_of(gid: str) -> Key:
    sub, vg = gid.split(".")
    return int(sub), int(vg)


def _same(a: object, b: object) -> bool:
    return a is b or (isinstance(a, int | str | tuple) and a == b)


class AssetsPanel(kit.Panel):
    def __init__(self, ws: MapWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        self.src: int | None = None
        self.gid: str | None = None
        self.obj: str | None = None
        self.into: str | None = None
        self.slot: str | None = None
        self._memos: dict[str, tuple[tuple[object, ...], Any]] = {}

        self.section = kit.choice(
            [],
            tip="The section to copy from: any section of the loaded one's row, itself too",
            on=lambda v: self._pick("src", int(v)),
        )
        self.counts = kit.label(role="muted")
        head = kit.Form()
        head.row("From", self.section)

        obj = kit.Section(
            "Copy an object",
            tip="A piece of scenery from that section, drawn by a group of this one",
        )
        self.group = kit.choice(
            [],
            tip="The mesh group to take an object from. A group draws all its triangles with"
            " one texture; each line shows its faces, its texture slot and its objects.",
            on=lambda v: self._pick("gid", v),
        )
        self.object = kit.choice(
            [],
            tip="An object of that group: one connected piece of mesh, largest first, with its"
            " vertices (v), faces (f) and size",
            on=lambda v: self._pick("obj", v),
        )
        self.target = kit.choice(
            [],
            tip="The group here that draws the copy. A group can only draw as many triangles as"
            " its free drawing slots hold (its budget); objects of it you select in the view"
            " give their slots up to the copy.",
            on=lambda v: self._pick("into", v),
        )
        form = kit.Form()
        form.row("Group", self.group)
        form.row("Object", self.object)
        form.row("Into group", self.target)
        self.need = kit.Alert(level="info")
        self.mismatch = kit.Alert()
        self.where = kit.label(role="muted")
        self.unsaved = kit.Alert(
            "Save the document first: the copy is written into its assets/ folder."
        )
        self.copy_button = kit.button(
            "Copy the object here",
            tip="Copies the object into the chosen group here, where the Add panel puts a new"
            " shape (the selection, the last click or a typed point) and turned by its"
            " rotation. The copy is saved as an OBJ file in the document's assets/ folder.",
            on=studio.act("copy object", self.copy),
            role="primary",
        )
        parts: tuple[QWidget, ...] = (form, self.need, self.mismatch, self.where, self.unsaved)
        for w in (*parts, self.copy_button):
            obj.body.addWidget(w)

        tex = kit.Section(
            "Copy a texture",
            tip="A picture from that section's texture bank, into a slot of this one",
        )
        self.slot_box = kit.choice(
            [],
            tip="A slot of that section's texture bank: one numbered picture, with its size",
            on=lambda v: self._pick("slot", v),
        )
        self.thumb = QLabel()
        self.thumb.setToolTip("The picture in that slot")
        self.goes = kit.label(role="muted")
        self.copy_tex = kit.button(
            "Copy the texture",
            tip="Replaces a slot of this section with that picture: everything here wearing the"
            " slot wears the new one. The running game shows a texture change at once.",
            on=studio.act("copy texture", self.copy_texture),
        )
        slots = kit.Form()
        slots.row("Slot", self.slot_box)
        for w in (slots, self.thumb, self.goes, self.copy_tex):
            tex.body.addWidget(w)

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        for part in (head, self.counts, obj, tex):
            lay.addWidget(part)
        lay.addStretch(1)
        self.obj_section, self.tex_section = obj, tex
        self.gate = Gate(page, "copy objects and textures into it")
        self.body.addWidget(self.gate)

    # ---- picks ------------------------------------------------------------------------ #

    def _pick(self, what: str, value: object) -> None:
        self.studio.act("pick", lambda: setattr(self, what, value))()

    def _memo(self, name: str, key: tuple[object, ...], fn: Callable[[], T]) -> T:
        """`fn()` again only when `key` changed (objects by identity)."""
        old = self._memos.get(name)
        if old is not None and len(old[0]) == len(key):
            if all(_same(a, b) for a, b in zip(old[0], key, strict=True)):
                got: T = old[1]
                return got
        v = fn()
        self._memos[name] = (key, v)
        return v

    # ---- what is picked --------------------------------------------------------------- #

    def other(self) -> MapScene | None:
        ws = self.ws
        if ws.scene is not None and self.src == ws.scene.stage:
            return ws.scene
        return ws.asset_scene(self.src) if self.src is not None else None

    def source(self) -> tuple[MeshGroup, int] | None:
        """The picked group and object of the other section."""
        other = self.other()
        if other is None or self.gid is None or self.obj is None:
            return None
        try:
            return other.group(*key_of(self.gid)), int(self.obj)
        except KeyError:
            return None

    def into_group(self) -> MeshGroup | None:
        sc = self.ws.scene
        if sc is None or self.into is None:
            return None
        try:
            return sc.group(*key_of(self.into))
        except KeyError:
            return None

    def sacrifice(self, tg: MeshGroup) -> Selection | None:
        sel = self.ws.selection
        return sel if tg.key in sel.vertices else None

    # ---- actions ---------------------------------------------------------------------- #

    def copy(self) -> None:
        """The picked object packed into the target group here."""
        ws, sess, src, tg = self.ws, self.ws.session, self.source(), self.into_group()
        if sess is None or src is None or tg is None or self.src is None:
            return
        if sess.base_dir is None:
            ws.message = "save the document first: the copy is written into its assets/ folder"
            return
        g, cid = src
        other = self.other()
        assert other is not None
        ids: Array = g.component_vertices(cid)
        at = tuple(ws.add.placement())
        v, t, uv = shapes.from_group(other, g.key, ids, at=at, rotate_y=ws.add.rotate)
        name = shapes.next_asset_name(sess.base_dir, f"st{self.src:03d}_g{g.vg_rec}")
        shapes.write_obj(sess.base_dir / name, v, t, uv, name.split("/")[-1].rsplit(".", 1)[0])
        colour = None
        if g.colours is not None:
            colour = [int(x) for x in g.colours[ids].mean(0).round()]
        uv_mode = "obj" if (g.texture == tg.texture and not g.untextured) else "planar"
        try:
            new = sess.add_mesh(
                tg.key, name, len(t), sacrifice=self.sacrifice(tg), uv=uv_mode, colour=colour
            )
        except EditError as e:
            ws.message = f"refused: {plain(str(e))}"
            (sess.base_dir / name).unlink(missing_ok=True)
            return
        ws.after_commit(sess.ops[-1:])
        ws.tools.kind = OBJECT
        ws.tools.select(new)

    def copy_texture(self) -> None:
        ws, sess = self.ws, self.ws.session
        if sess is None or self.src is None or self.slot is None:
            return
        ws.do(
            "texture",
            sess.import_texture,
            ws.tex_target,
            from_=(self.src, int(self.slot)),
            keep_palette=ws.tex_keep,
        )

    # ---- sync ------------------------------------------------------------------------- #

    def sync(self) -> None:
        ws = self.ws
        if not self.gate.check(ws):
            return
        sc, atlas = ws.scene, ws.atlas
        assert sc is not None and atlas is not None
        secs = [x for x in atlas.row(ws.row).sections if x.present] if ws.row is not None else []
        if not secs:
            self.gate.need("Nothing to copy from", "The loaded section's row has no section.")
            return
        if self.src not in [x.stage for x in secs]:
            self.src = secs[0].stage
        kit.refill(
            self.section,
            [
                (
                    str(x.stage),
                    f"st{x.stage:03d}  {x.name}{'  (loaded)' if x.stage == sc.stage else ''}",
                )
                for x in secs
            ],
            str(self.src),
        )
        other = self.other()
        self.obj_section.setVisible(other is not None)
        self.tex_section.setVisible(other is not None)
        if other is None:
            self.counts.setText(f"st{self.src:03d} could not be loaded.")
            return
        self.counts.setText(
            f"{len(other.groups)} groups, {sum(g.n_components for g in other.groups)} objects,"
            f" {len(other.textures)} textures"
        )
        self._sync_object(sc, other)
        self._sync_texture(other)

    def _sync_object(self, sc: MapScene, other: MapScene) -> None:
        ws, sess = self.ws, self.ws.session
        assert sess is not None
        groups = [g for g in other.groups if g.n_faces]
        ids = [group_id(g.key) for g in groups]
        if self.gid not in ids:
            self.gid = ids[0] if ids else None
        kit.refill(
            self.group,
            [
                (
                    group_id(g.key),
                    f"{g.label}  {g.n_faces} faces  tex {'-' if g.untextured else g.texture}"
                    f"  {g.n_components} obj",
                )
                for g in groups
            ],
            self.gid,
        )
        picked = other.group(*key_of(self.gid)) if self.gid is not None else None
        objs = self._memo(
            "objects", (picked,), lambda: objects(picked) if picked is not None else []
        )
        if self.obj not in [c for c, _ in objs]:
            self.obj = objs[0][0] if objs else None
        kit.refill(self.object, objs, self.obj)
        targets = [(group_id(g.key), g.label) for g in sc.groups]
        if self.into not in [k for k, _ in targets] and targets:
            add = ws.add.group  # the Add panel's group, which a selection sets
            self.into = targets[add if 0 <= add < len(targets) else 0][0]
        kit.refill(self.target, targets, self.into)
        src, tg = self.source(), self.into_group()
        ready = src is not None and tg is not None
        for w in (self.need, self.where, self.copy_button):
            w.setEnabled(ready)
        if src is None or tg is None:
            self.need.setText("This group has no object with faces.")
            self.mismatch.hide()
            return
        g, cid = src
        sac = self.sacrifice(tg)
        cap = self._memo("capacity", (tg, sess.revision, sac), lambda: sess.capacity(tg.key, sac))
        tris = self._memo("faces", (g, cid), lambda: len(g.component_faces(cid)))
        note = " (replacing the selection)" if sac else ""
        self.need.setText(f"Needs {tris} triangles; {cap} free in {tg.label}{note}.")
        self.need.set_level("error" if tris > cap else "info")
        self.mismatch.setText(
            f"It wears slot {g.texture} of st{self.src:03d}, which differs from this section's"
            f" slot {g.texture}: copy that texture too (below), or it wears {tg.label}'s."
        )
        differs = self._memo("mismatch", (g, sc.textures), lambda: _differs(g, sc, other))
        self.mismatch.setVisible(differs)
        at = ws.add.placement()
        self.where.setText(f"Lands at {at[0]:.0f}, {at[1]:.0f}, {at[2]:.0f}.")
        self.unsaved.setVisible(sess.base_dir is None)
        self.copy_button.setEnabled(sess.base_dir is not None and tris <= cap)

    def _sync_texture(self, other: MapScene) -> None:
        ws = self.ws
        slots = [(str(t.index), f"{t.index}  {t.width}x{t.height}") for t in other.textures]
        if self.slot not in [s for s, _ in slots]:
            self.slot = slots[0][0] if slots else None
        kit.refill(self.slot_box, slots, self.slot)
        t: TextureImage | None = other.texture(int(self.slot)) if self.slot is not None else None
        pix = self._memo("thumb", (t,), lambda: thumbnail(t, THUMB) if t is not None else None)
        if pix is not None:
            self.thumb.setPixmap(pix)
        else:
            self.thumb.clear()
        here = ws.scene.texture(ws.tex_target) if ws.scene else None
        size = f" ({here.width}x{here.height})" if here else ""
        self.goes.setText(
            f"Goes into slot {ws.tex_target}{size} here. Pick another slot in the Textures panel."
        )
        self.copy_tex.setText(f"Copy into slot {ws.tex_target}")
        self.copy_tex.setEnabled(t is not None)


def _differs(g: MeshGroup, here: MapScene, there: MapScene) -> bool:
    """The group's texture slot holds other pixels here than in its own section."""
    if g.texture is None or g.untextured:
        return False
    a, b = here.texture(g.texture), there.texture(g.texture)
    return a is None or b is None or not np.array_equal(a.rgba, b.rgba)
