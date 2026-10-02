# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Collision panel: chunks and classes with their counts, the inspector of the collision
selection (flags, vertices, climbable, delete) and new collision triangles."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QCheckBox, QSpinBox, QVBoxLayout, QWidget

from mhfu_studio.shell.camera import Bounds
from mhfu_studio.shell.text import plain
from mhfu_studio.ui import kit, theme

from ..core.edit import COLLISION, CollisionSelection
from ..core.scene import CLASSES, MapScene
from ..render.overlays import CLASS_COLORS
from .common import Gate

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

    from ..render.overlays import CollisionOverlay
    from ..workspace import MapWorkspace

CLASS_TIPS = {
    "floor": "Ground the hunter stands and walks on",
    "wall": "Walls and ceilings the hunter bumps into",
    "climb": "Walls the hunter can climb: material 9 or 10 on a near-vertical triangle",
    "sink": "Ground the hunter sinks into, such as deep snow or mud",
    "wade": "Ground the hunter wades through, such as shallow water",
}
CHUNKS = (("auto", "By slope"), ("0", "Walls (chunk 0)"), ("1", "Floor (chunk 1)"))
FLAG_TIPS = (
    "Surface: which row of the area's surface table applies (sinking, sliding and the like)",
    "Material: the footstep sound and effects; 9 or 10 on a steep triangle makes it climbable",
    "Exclude: a bit mask; queries sharing a bit pass through the triangle",
)
FLAG_HI = (255, 255, 65535)
NEW_TIP = (
    "Added triangles need no room in the file: they are linked into the game's collision grid"
    " one cell at a time. Chunk 1 is what the hunter stands on, chunk 0 what he walks into."
)


def flags_of(fields: list[int]) -> dict[str, int]:
    return {"surface": fields[0], "material": fields[1], "exclude": fields[2]}


def _flag_boxes() -> list[QSpinBox]:
    return [kit.integer(tip=tip, lo=0, hi=hi) for tip, hi in zip(FLAG_TIPS, FLAG_HI, strict=True)]


class CollisionPanel(kit.Panel):
    def __init__(self, ws: MapWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        shown = kit.Section("What is shown", tip="Which collision the view draws; edits nothing")
        self.show_layer = kit.check(
            "Show collision",
            tip="Draws the invisible collision over the mesh, coloured by what it does",
            on=self._view(lambda vp, on: setattr(vp, "show_collision", on)),
        )
        self.chunk_box = QWidget()
        self.chunk_lay = QVBoxLayout(self.chunk_box)
        self.chunk_lay.setContentsMargins(0, 0, 0, 0)
        self.chunk_checks: dict[int, QCheckBox] = {}
        self.classes = {
            k: kit.check(k, tip=CLASS_TIPS[k], on=self._view(self._class_setter(k)))
            for k in CLASSES
        }
        for k, box in self.classes.items():
            r, g, b = CLASS_COLORS[k]
            px = QPixmap(12, 12)
            px.fill(theme.color((r, g, b, 1.0)))
            box.setIcon(QIcon(px))
        self.fill = kit.Slider(
            0.0,
            1.0,
            0.28,
            tip="How solid the collision's faces look",
            on=self._view(lambda vp, v: setattr(vp.collision, "fill_alpha", v)),
        )
        self.edge = kit.Slider(
            0.0,
            1.0,
            0.55,
            tip="How solid the collision's edges look",
            on=self._view(lambda vp, v: setattr(vp.collision, "edge_alpha", v)),
        )
        looks = kit.Form()
        looks.row("Faces", self.fill)
        looks.row("Edges", self.edge)
        shown_widgets: list[QWidget] = [
            self.show_layer,
            self.chunk_box,
            *self.classes.values(),
            looks,
        ]
        for w in shown_widgets:
            shown.body.addWidget(w)
        self.climb_text = kit.label()
        self.frame_climb = kit.button(
            "Frame them",
            tip="Points the camera at every climbable wall of the section",
            on=studio.act("frame climbable", self._frame_climb),
            icon="ph.frame-corners",
        )
        self.select_climb = kit.button(
            "Select them",
            tip="Selects every climbable triangle, to inspect or change them below",
            on=studio.act("select climbable", self._select_climb),
        )
        self.surface = kit.label(role="muted", selectable=True)
        shown.body.addWidget(self.climb_text)
        shown.body.addWidget(kit.row(self.frame_climb, self.select_climb, stretch=True))
        shown.body.addWidget(self.surface)
        lay.addWidget(shown)

        picked = kit.Section("Selected triangles", tip="The collision triangles you clicked")
        self.hint = kit.label(
            "Press 4, or pick Collision on the toolbar, to click collision triangles in the view.",
            role="muted",
        )
        self.what = kit.label(role="title")
        self.info = kit.label(role="muted", selectable=True)
        self.corners = kit.Form()
        self.verts = [
            kit.Vec3(tip=f"Corner {i} of the triangle, in map units", step=10.0) for i in range(3)
        ]
        for i, v in enumerate(self.verts):
            self.corners.row(f"Corner {i}", v)
        self.apply_verts = kit.button(
            "Apply corners",
            tip="Moves the triangle's corners to these positions as one edit",
            on=studio.act("collision corners", self._apply_verts),
        )
        self.flip = kit.button(
            "Flip",
            tip="Swaps two corners so the triangle faces the other way; the game tells floors"
            " from walls by which side faces up",
            on=studio.act("flip collision", self._flip),
            icon="ph.swap",
        )
        self.one = QWidget()
        one = QVBoxLayout(self.one)
        one.setContentsMargins(0, 0, 0, 0)
        one.addWidget(self.corners)
        one.addWidget(kit.row(self.apply_verts, self.flip, stretch=True))
        self.flags = _flag_boxes()
        flags = kit.Form()
        for text, spin in zip(("Surface", "Material", "Exclude"), self.flags, strict=True):
            flags.row(text, spin)
        self.apply_flags = kit.button(
            "Apply flags",
            tip="Gives every selected triangle these surface, material and exclude values",
            on=studio.act("collision flags", self._apply_flags),
            role="primary",
        )
        self.climb10 = kit.button(
            "Climbable",
            tip="Makes the selected walls climbable like vines and ivy (material 10)",
            on=studio.act("climbable", lambda: self.climb(True, 10)),
        )
        self.climb9 = kit.button(
            "Rock wall",
            tip="Makes the selected walls climbable like a rock face (material 9)",
            on=studio.act("rock wall", lambda: self.climb(True, 9)),
        )
        self.unclimb = kit.button(
            "Not climbable",
            tip="Sets the selected triangles' material back to plain (0)",
            on=studio.act("not climbable", lambda: self.climb(False)),
        )
        self.delete = kit.button(
            "Delete",
            tip="Removes the selected triangles: the hunter walks through where they were",
            on=studio.act("delete collision", self._delete),
            role="danger",
            icon="ph.trash",
        )
        self.warn = kit.Alert()
        self.edit = QWidget()
        edit = QVBoxLayout(self.edit)
        edit.setContentsMargins(0, 0, 0, 0)
        edit.addWidget(flags)
        edit.addWidget(kit.row(self.apply_flags, self.delete, stretch=True))
        edit.addWidget(kit.row(self.climb10, self.climb9, self.unclimb, stretch=True))
        edit.addWidget(self.warn)
        for part in (self.hint, self.what, self.info, self.one, self.edit):
            picked.body.addWidget(part)
        lay.addWidget(picked)

        new = kit.Section("New collision", tip=NEW_TIP)
        form = kit.Form()
        self.add_chunk = kit.choice(
            CHUNKS,
            tip="Where new triangles go: the floor chunk or the wall chunk, or sorted by their"
            " slope",
            on=lambda _k: None,
        )
        self.add_flags = _flag_boxes()
        form.row("Chunk", self.add_chunk)
        for text, spin in zip(("Surface", "Material", "Exclude"), self.add_flags, strict=True):
            form.row(text, spin)
        self.from_faces = kit.button(
            "Faces to collision",
            tip="Every face selected in the view (pick kind 3) becomes a collision triangle"
            " with these flags. To make a rock face climbable: select its faces, material 10,"
            " chunk 0.",
            on=studio.act("faces to collision", self._from_faces),
        )
        self.inflate = kit.number(
            tip="Grows the box by this much on every side, in map units", lo=0.0, step=10.0
        )
        self.box = kit.button(
            "Box collider",
            tip="A solid box around what is selected in the view, so the hunter cannot walk"
            " through it",
            on=studio.act("box collider", self._box),
            icon="ph.bounding-box",
        )
        new.body.addWidget(form)
        new.body.addWidget(self.from_faces)
        new.body.addWidget(kit.row(self.box, kit.label("grown by", role="muted"), self.inflate))
        lay.addWidget(new)
        lay.addStretch(1)
        self.gate = Gate(page, "see and edit its collision")
        self.body.addWidget(self.gate)
        self._scene: MapScene | None = None
        self._seed: tuple[object, ...] | None = None

    # view settings

    def _view(self, fn: Callable[..., object]) -> Callable[[object], None]:
        """A control's slot that changes how the view draws the collision."""

        def run(value: object) -> None:
            vp = self.ws.vp

            def go() -> None:
                if vp is not None and vp.collision is not None:
                    fn(vp, value)

            self.studio.act("collision view", go)()

        return run

    @staticmethod
    def _class_setter(k: str) -> Callable[..., None]:
        def set_class(vp: object, on: bool) -> None:
            col: CollisionOverlay = vp.collision  # type: ignore[attr-defined]
            col.set_class(k, on)

        return set_class

    def _chunk(self, index: int) -> Callable[[bool], None]:
        return self._view(lambda vp, on: vp.collision.show_chunk.__setitem__(index, on))

    def _frame_climb(self) -> None:
        sc, vp = self.ws.scene, self.ws.vp
        if sc is not None and vp is not None and sc.climbable():
            pts = np.concatenate([sc.chunk(c).verts[t] for c, t in sc.climbable()])
            vp.camera.frame(Bounds.of(pts))

    def _select_climb(self) -> None:
        sc = self.ws.scene
        if sc is not None:
            self.ws.tools.set_kind(COLLISION)
            self.ws.tools.select_collision(CollisionSelection(list(sc.climbable())))

    # edits

    def climb(self, on: bool, material: int = 10) -> None:
        ws = self.ws
        if ws.session is None or ws.col_sel.empty:
            return
        warnings = ws.session.climb_warnings(ws.col_sel.tris) if on else []
        ws.do("climb", ws.session.set_climbable, list(ws.col_sel.tris), on, material)
        if warnings:
            ws.message += "   [!] " + warnings[0]

    def _apply_verts(self) -> None:
        ws = self.ws
        if ws.session is not None and len(ws.col_sel) == 1:
            c, t = ws.col_sel.tris[0]
            verts = [v.value() for v in self.verts]
            ws.do("collision", ws.session.collision_set, c, [t], verts=verts)

    def _flip(self) -> None:
        ws, sc = self.ws, self.ws.scene
        if ws.session is not None and sc is not None and len(ws.col_sel) == 1:
            c, t = ws.col_sel.tris[0]
            v = sc.chunk(c).verts[t]
            flipped = [v[0].tolist(), v[2].tolist(), v[1].tolist()]
            ws.do("collision", ws.session.collision_set, c, [t], verts=flipped)

    def _apply_flags(self) -> None:
        ws = self.ws
        if ws.session is None:
            return
        fl = flags_of([b.value() for b in self.flags])
        for c, ts in ws.col_sel.by_chunk().items():
            ws.do("collision", ws.session.collision_set, c, ts, flags=fl)

    def _delete(self) -> None:
        ws = self.ws
        if ws.session is not None and not ws.col_sel.empty:
            ws.do("delete", ws.session.collision_delete, list(ws.col_sel.tris))

    def _new_args(self) -> tuple[dict[str, int], int | None]:
        k = self.add_chunk.currentData()
        chunk = None if k in (None, "auto") else int(k)
        return flags_of([b.value() for b in self.add_flags]), chunk

    def _from_faces(self) -> None:
        ws, sc = self.ws, self.ws.scene
        if ws.session is None or sc is None or ws.selection.empty:
            return
        tris = [
            sc.group(*k).positions[sc.group(*k).triangles[f]].tolist()
            for k, faces in ws.selection.faces(sc).items()
            for f in faces
        ]
        fl, chunk = self._new_args()
        ws.do("collision", ws.session.collision_add, tris, flags=fl, chunk=chunk)

    def _box(self) -> None:
        ws, sc = self.ws, self.ws.scene
        if ws.session is None or sc is None or ws.selection.empty:
            return
        lo, hi = ws.selection.bounds(sc)
        fl, chunk = self._new_args()
        ws.do(
            "collision",
            ws.session.collision_box,
            lo.tolist(),
            hi.tolist(),
            flags=fl,
            chunk=chunk,
            inflate=self.inflate.value(),
        )

    # sync

    def sync(self) -> None:
        ws = self.ws
        if not self.gate.check(ws):
            return
        sc, vp = ws.scene, ws.vp
        if sc is None or vp is None or vp.collision is None:
            self.gate.need("No view yet", "The collision shows here once the 3D view is up.")
            return
        col = vp.collision
        if sc is not self._scene:
            self._scene = sc
            self._build_chunks(sc)
        kit.put(self.show_layer, vp.show_collision)
        for c in sc.collision:
            box = self.chunk_checks[c.index]
            box.setText(
                f"Chunk {c.index}: {'floor' if c.index == 1 else 'walls and ceilings'},"
                f" {c.n_triangles} triangles"
            )
            kit.put(box, col.show_chunk.get(c.index, True))
        for k, box in self.classes.items():
            n = sum(int(((c.klass == k) & c.alive).sum()) for c in sc.collision)
            box.setText(f"{k}  ({n})")
            kit.put(box, col.show_class.get(k, True))
        self.fill.set(col.fill_alpha)
        self.edge.set(col.edge_alpha)
        climb = sc.climbable()
        self.climb_text.setText(
            f"{len(climb)} climbable triangles (material 9 or 10, near-vertical)"
            if climb
            else "No climbable wall in this section"
        )
        self.frame_climb.setEnabled(bool(climb))
        self.select_climb.setEnabled(bool(climb))
        table = sc.surface_table
        self.surface.setText(
            "Surface table: " + " ".join(f"0x{v:02X}" for v in table) if table else ""
        )
        self.surface.setVisible(bool(table))
        self._sync_picked(sc)
        mesh = ws.selection
        n = mesh.n_faces(sc) if not mesh.empty else 0
        self.from_faces.setText(
            f"Turn {n} selected faces into collision" if n else "Faces to collision"
        )
        self.from_faces.setEnabled(n > 0)
        self.box.setEnabled(not mesh.empty)

    def _build_chunks(self, sc: MapScene) -> None:
        for box in self.chunk_checks.values():
            self.chunk_lay.removeWidget(box)
            box.deleteLater()
        self.chunk_checks = {}
        for c in sc.collision:
            box = kit.check(
                f"Chunk {c.index}",
                tip="Shows or hides this chunk; chunk 1 is the floor the hunter stands on,"
                " chunk 0 the walls and ceilings he bumps into. The game finds its triangles"
                f" through a grid of {c.grid[0]} x {c.grid[1]} cells, {c.cell[0]} units wide.",
                on=self._chunk(c.index),
            )
            self.chunk_checks[c.index] = box
            self.chunk_lay.addWidget(box)

    def _sync_picked(self, sc: MapScene) -> None:
        ws = self.ws
        sel = ws.col_sel
        assert ws.session is not None
        self.hint.setVisible(ws.tools.kind != COLLISION)
        self.what.setText(sel.describe(sc))
        self.edit.setVisible(not sel.empty)
        self.one.setVisible(len(sel) == 1)
        if sel.empty:
            self.info.setText("")
            return
        c0, t0 = sel.tris[0]
        ch = sc.chunk(c0)
        if len(sel) == 1:
            n = ch.normals[t0]
            added = "" if t0 < ch.n_shipped else " (added)"
            upright = "near-vertical" if ch.vertical(t0) else f"flat (|n.y| {abs(n[1]):.2f})"
            height = float(ch.verts[t0][:, 1].max() - ch.verts[t0][:, 1].min())
            self.info.setText(
                f"Chunk {c0} triangle {t0}{added}: {ch.klass[t0]}\n"
                f"normal ({n[0]:.2f} {n[1]:.2f} {n[2]:.2f}), plane {ch.plane_d[t0]:.0f}\n"
                f"{upright}, {height:.0f} tall, cells {ch.cells_of(t0)[:8]}"
            )
        else:
            mats = sorted({int(sc.chunk(c).material[t]) for c, t in sel.tris})
            self.info.setText(f"{len(sel)} triangles; materials {mats}")
        flags = [int(ch.surface_id[t0]), int(ch.material[t0]), int(ch.exclude[t0])]
        seed = (tuple(sel.tris), tuple(flags), ch.verts[t0].tobytes())
        if seed != self._seed:
            self._seed = seed
            for spin, v in zip(self.flags, flags, strict=True):
                kit.put(spin, v)
            for vec, row in zip(self.verts, ch.verts[t0], strict=True):
                vec.set([float(x) for x in row])
        warnings = [plain(w) for w in ws.session.climb_warnings(sel.tris)[:4]]
        self.warn.setText("\n".join(warnings))
        self.warn.setVisible(bool(warnings))
