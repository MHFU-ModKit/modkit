# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Textures panel: the bank as thumbnails with who wears each slot, and the import that
spends a slot (a PNG copied into the document's assets/, another stage's slot, a flat
colour). A texture edit lands in the running game at once."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from mhfu import files
from PySide6.QtCore import QSignalBlocker, QSize
from PySide6.QtGui import QIcon, QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QColorDialog,
    QFileDialog,
    QFrame,
    QListView,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mhfu_studio.ui import kit, theme

from ..core.shapes import next_asset_name
from .common import Gate, thumbnail

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

    from ..core.scene import MapScene, TextureImage
    from ..workspace import MapWorkspace

ZOOM = (48, 192, 72)
"""Thumbnail sides in pixels: smallest, largest, first."""
WIDE = 640
"""Wider than this, the import form sits beside the grid instead of above it."""
SIDE, SPIN = 330, 72
"""The import form's width beside the grid, a number field's least width."""
QWIDGETSIZE_MAX = (1 << 24) - 1
GRID_TIP = (
    "The section's texture bank: every picture its surfaces wear, one per slot, with how many"
    " mesh groups wear it. Click a slot to make it the one an import replaces."
)


def wearers(sc: MapScene) -> dict[int, list[str]]:
    """Per slot, the groups wearing it."""
    out: dict[int, list[str]] = {}
    for g in sc.groups:
        if g.texture is not None:
            out.setdefault(g.texture, []).append(g.label)
    return out


def describe(t: TextureImage) -> str:
    colours = f"{t.colours} colours" if t.colours else "direct colour"
    return f"{t.width}x{t.height}, {colours}"


class TexturesPanel(kit.Panel):
    def __init__(self, ws: MapWorkspace, studio: Studio) -> None:
        super().__init__(scroll=False)
        self.ws, self.studio = ws, studio
        self.rgb = (128, 128, 128)
        self._worn: tuple[object, dict[int, list[str]]] | None = None
        self._iconed: tuple[object, int] | None = None
        self._stage: int | None = None

        self.target = kit.label(role="title")
        self.picture = kit.button(
            "Picture…",
            tip="Replaces the slot with a PNG you pick. It is copied into the document's"
            " assets/ folder and turned into the slot's size and colour count.",
            on=self._png,
            icon="ph.image",
        )
        self.flat = kit.button(
            "Flat colour…",
            tip="Fills the slot with one colour you pick: every surface wearing it turns that"
            " colour, tinted by its lighting",
            on=self._flat,
            icon="ph.paint-bucket",
        )
        self.keep = kit.check(
            "Keep palette",
            tip="Draws the new picture with the slot's own colours (its palette) instead of"
            " making new ones: same colours, new arrangement. Off, the picture brings its own.",
            on=lambda on: studio.act("keep palette", lambda: setattr(ws, "tex_keep", on))(),
        )
        self.from_stage = kit.integer(
            tip="The stage (stNNN) to take a slot from. The Assets panel shows the row's"
            " sections and their slots as pictures.",
            lo=min(files.STAGES),
            hi=max(files.STAGES),
        )
        self.from_slot = kit.integer(tip="The slot of that stage to copy", lo=0, hi=255)
        self.copy = kit.button(
            "Copy that slot here",
            tip="Replaces the slot with that stage's slot",
            on=studio.act("copy slot", self.copy_slot),
        )
        self.unsaved = kit.pill(
            "warning",
            "Save the document first to import a picture: it is copied into its assets/ folder.",
        )
        self.unsaved.setWordWrap(True)
        self.info = kit.label(role="muted")
        self.zoom = kit.Slider(
            *ZOOM,
            tip="How big the thumbnails are",
            decimals=0,
            on=lambda _v: self._icons(force=True),
        )
        for box in (self.from_stage, self.from_slot):
            box.setMinimumWidth(SPIN)
        form = kit.Form()
        slot = kit.label("slot", role="muted", wrap=False)
        form.row("From stage", kit.row(self.from_stage, slot, self.from_slot, stretch=True))
        form.row("", self.copy)
        form.row("Thumbnails", self.zoom)
        side = QWidget()
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.addWidget(self.target)
        lay.addWidget(kit.row(self.picture, self.flat, stretch=True))
        lay.addWidget(self.keep)
        lay.addWidget(form)
        lay.addWidget(self.unsaved)
        lay.addWidget(self.info)
        lay.addStretch(1)
        self.side = QScrollArea()
        self.side.setWidgetResizable(True)
        self.side.setFrameShape(QFrame.Shape.NoFrame)
        self.side.setWidget(side)
        self.side.setMinimumWidth(SIDE)
        self.side.setMaximumWidth(SIDE + 60)

        self.grid = kit.Items(tip=GRID_TIP, empty="This section has no textures.")
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setWordWrap(True)
        self.grid.setUniformItemSizes(True)
        self.grid.picked.connect(self._pick)

        page = QWidget()
        self.split = QBoxLayout(QBoxLayout.Direction.LeftToRight, page)
        self.split.setContentsMargins(0, 0, 0, 0)
        self.split.addWidget(self.side)
        self.split.addWidget(self.grid, 1)
        self.gate = Gate(page, "see its textures")
        self.body.addWidget(self.gate)

    def resizeEvent(self, e: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(e)
        wide = e.size().width() > WIDE
        d = QBoxLayout.Direction
        self.split.setDirection(d.LeftToRight if wide else d.TopToBottom)
        self.side.setMaximumWidth(SIDE + 60 if wide else QWIDGETSIZE_MAX)

    # ---- actions ---------------------------------------------------------------------- #

    def _pick(self, slot: object) -> None:
        if isinstance(slot, int):
            self.studio.act(f"pick slot {slot}", lambda: setattr(self.ws, "tex_target", slot))()

    def png(self, source: Path) -> None:
        """Copies a PNG into the document's assets/ and spends the target slot on it."""
        ws, sess = self.ws, self.ws.session
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

    def _png(self) -> None:
        got, _ = QFileDialog.getOpenFileName(
            self, f"A PNG for slot {self.ws.tex_target}", "", "PNG images (*.png)"
        )
        if got:
            self.studio.act("import picture", lambda: self.png(Path(got)))()

    def copy_slot(self) -> None:
        ws, sess = self.ws, self.ws.session
        if sess is not None:
            source = (self.from_stage.value(), self.from_slot.value())
            keep = ws.tex_keep
            ws.do("texture", sess.import_texture, ws.tex_target, from_=source, keep_palette=keep)

    def fill(self, rgb: tuple[int, int, int]) -> None:
        ws, sess = self.ws, self.ws.session
        if sess is not None:
            self.rgb = rgb
            ws.do("texture", sess.import_texture, ws.tex_target, rgb=list(rgb))

    def _flat(self) -> None:
        r, g, b = self.rgb
        got = QColorDialog.getColor(
            theme.color((r / 255, g / 255, b / 255, 1.0)),
            self,
            f"A flat colour for slot {self.ws.tex_target}",
        )
        if got.isValid():
            rgb = (got.red(), got.green(), got.blue())
            self.studio.act("flat colour", lambda: self.fill(rgb))()

    # ---- sync ------------------------------------------------------------------------- #

    def sync(self) -> None:
        ws = self.ws
        if not self.gate.check(ws):
            return
        sc, sess = ws.scene, ws.session
        assert sc is not None and sess is not None
        if sc.stage != self._stage:  # a section loaded: copy from it unless told otherwise
            self._stage = sc.stage
            with QSignalBlocker(self.from_stage):
                self.from_stage.setValue(sc.stage)
        slot = ws.tex_target
        t = sc.texture(slot)
        self.target.setText(f"Slot {slot}: {describe(t)}" if t else f"Slot {slot}: not in the bank")
        with QSignalBlocker(self.keep):
            self.keep.setChecked(ws.tex_keep)
        self.picture.setEnabled(sess.base_dir is not None)
        self.unsaved.setVisible(sess.base_dir is None)
        self.info.setText(
            f"{len(sc.textures)} slots, {sc.sub_sizes[1]:,} bytes. Every slot is in use and the"
            " bank cannot grow, so an import replaces one."
        )
        if self._worn is None or self._worn[0] is not sc.groups:
            self._worn = (sc.groups, wearers(sc))
        worn = self._worn[1]
        rebuilt = self.grid.set_items(
            [
                kit.Item(
                    f"{x.index}  {x.width}x{x.height}\n"
                    + (f"{len(worn[x.index])} group(s)" if x.index in worn else "unused"),
                    x.index,
                    f"Slot {x.index}: {describe(x)}, mode {x.mode}\n"
                    f"Worn by: {', '.join(worn.get(x.index, ['nothing']))}\n"
                    "Click to make it the slot an import replaces.",
                )
                for x in sc.textures
            ]
        )
        self._icons(force=rebuilt)
        row = next((i for i, x in enumerate(sc.textures) if x.index == slot), -1)
        if self.grid.currentRow() != row:
            with QSignalBlocker(self.grid):
                self.grid.setCurrentRow(row)

    def _icons(self, *, force: bool = False) -> None:
        """Thumbnails for the grid, made again only when the pixels or the size change."""
        sc = self.ws.scene
        if sc is None:
            return
        side = round(self.zoom.value())
        last = self._iconed
        if not force and last is not None and last[0] is sc.textures and last[1] == side:
            return
        self._iconed = (sc.textures, side)
        self.grid.setIconSize(QSize(side, side))
        self.grid.setGridSize(QSize(side + 28, side + 44))
        for i, t in enumerate(sc.textures):
            it = self.grid.item(i)
            if it is not None:
                pix, icon = thumbnail(t, side), QIcon()
                for mode in (QIcon.Mode.Normal, QIcon.Mode.Selected):  # no selection tint
                    icon.addPixmap(pix, mode)
                it.setIcon(icon)
