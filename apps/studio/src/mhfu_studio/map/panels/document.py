# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Document panel: new, open, save, validate and export a map document."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from mhfu_studio.shell.findings import LEVELS, Finding
from mhfu_studio.shell.widgets import LEVEL_COLORS, pick_folder, plain

from ..document import MapDocument

if TYPE_CHECKING:
    from ..workspace import MapWorkspace

SHOWN = 40


class DocumentPanel:
    def __init__(self, ws: MapWorkspace) -> None:
        self.ws = ws
        self.name = "my_map"
        self.message = ""
        self.found: list[Finding] = []

    def new(self, directory: Path, name: str) -> None:
        """A new document in `directory` holding the loaded section's list (not saved yet)."""
        ws = self.ws
        doc = MapDocument(name or directory.name, ws.row, directory)
        doc.game = ws.game
        if ws.scene is not None and ws.session is not None:
            doc.ensure_stage(ws.scene.stage, ops=ws.session.ops)
            doc.session = ws.session
            ws.session.base_dir = directory
        ws.doc = doc
        self.found = []
        self.message = f"new document {doc.name} in {directory} (not saved yet)"

    def open(self, path: Path) -> None:
        try:
            self.ws.open(path)
        except ValueError as e:
            self.message = plain(str(e))
            return
        self.found = []
        self.message = self.ws.message

    def save(self) -> None:
        try:
            p = self.ws.doc.save()
        except (ValueError, OSError) as e:
            self.message = plain(str(e))
            return
        self.message = f"saved {p} ({len(self.ws.doc.stages)} stage(s))"

    def validate(self) -> None:
        self.found = self.ws.doc.findings()
        counts = ", ".join(f"{sum(f.level == lv for f in self.found)} {lv}" for lv in LEVELS)
        self.message = f"{len(self.found)} finding(s): {counts}"

    def export(self, out: Path) -> None:
        doc = self.ws.doc
        if doc.directory is None:
            self.message = "save the document first (the export resolves assets against it)"
            return
        try:
            manifest = doc.export(out)
        except (ValueError, OSError) as e:
            self.message = f"export failed: {plain(str(e))}"
            return
        parts = []
        for rec in manifest["stages"]:
            bits = [f"st{rec['stage']:03d}:"]
            bits += [f"sub{s} {'ok' if m['safe'] else 'UNSAFE'}" for s, m in rec["mesh"].items()]
            if rec["collision"]:
                c = rec["collision"]
                bits.append(f"collision +{c['added']}/~{c['moved']}")
            if rec["texture"]:
                bits.append("textures")
            parts.append(" ".join(bits))
        self.message = f"exported to {out}: {'; '.join(parts) or 'nothing to export'}"

    def draw(self) -> None:
        from imgui_bundle import imgui

        ws = self.ws
        doc = ws.doc
        sc = ws.scene
        if doc.directory is None and not doc.stages:
            imgui.text_wrapped(
                "No document. A map mod is a folder: map.toml + one edit list per stage (+ the"
                " OBJ / PNG assets its ops name)."
            )
        else:
            imgui.text(f"{doc.name}  (row {doc.row})")
            imgui.text_disabled(str(doc.path) if doc.path else "(unsaved)")
            for s in doc.stages:
                loaded = sc is not None and s.number == sc.stage
                imgui.bullet_text(
                    f"{s.label}  {s.ops_file}  {len(s.ops)} op(s){'   <- loaded' if loaded else ''}"
                )
            if sc is not None and doc.stage(sc.stage) is None and ws.session is not None:
                if imgui.small_button(f"add st{sc.stage:03d} to the document"):
                    doc.ensure_stage(sc.stage, ops=ws.session.ops)
        imgui.separator()
        _, self.name = imgui.input_text("name", self.name)
        if imgui.button("new..."):
            d = pick_folder("Folder for the new map document")
            if d:
                self.new(Path(d), self.name)
        imgui.same_line()
        if imgui.button("open..."):
            d = pick_folder("Open a map document (its folder)")
            if d:
                self.open(Path(d))
        imgui.same_line()
        if imgui.button("save"):
            self.save()
        imgui.same_line()
        if imgui.button("validate"):
            self.validate()
        imgui.same_line()
        if imgui.button("export..."):
            d = pick_folder("Folder for the exported bytes")
            if d:
                self.export(Path(d))
        if self.message:
            imgui.text_wrapped(plain(self.message))
        for f in self.found[:SHOWN]:
            imgui.text_colored(imgui.ImVec4(*LEVEL_COLORS[f.level]), plain(str(f)))
