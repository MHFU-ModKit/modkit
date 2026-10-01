# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Document panel: new, open, save, check and export a map document.

Open and save are the studio's own (the File menu's); the findings list is the Findings dock.
Without game files it still opens and saves; nothing can load.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QFileDialog, QVBoxLayout, QWidget

from mhfu_studio.shell.findings import LEVELS
from mhfu_studio.shell.widgets import plain
from mhfu_studio.ui import dialogs, kit

from ..document import MapDocument
from .common import DATA_HINT, NO_DATA, fit

if TYPE_CHECKING:
    from mhfu_studio.shell.studio import Studio

    from ..workspace import MapWorkspace

DOC_TIP = (
    "A map mod is a folder: map.toml names the stages it changes, and each stage keeps its"
    " edits as a list in a .json file beside it, with the OBJ and PNG files they use in assets/."
)


def summary(manifest: dict[str, object]) -> str:
    """One line per exported stage: each mesh half safe or not, collision, textures."""
    parts = []
    stages = manifest.get("stages")
    for rec in stages if isinstance(stages, list) else []:
        bits = [f"st{rec['stage']:03d}:"]
        bits += [f"sub{s} {'ok' if m['safe'] else 'UNSAFE'}" for s, m in rec["mesh"].items()]
        if rec["collision"]:
            c = rec["collision"]
            bits.append(f"collision +{c['added']}/~{c['moved']}")
        if rec["texture"]:
            bits.append("textures")
        parts.append(" ".join(bits))
    return "\n".join(parts) or "nothing to export"


class DocumentPanel(kit.Panel):
    def __init__(self, ws: MapWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        self.name = kit.text_field(
            tip="The map mod's name, saved in map.toml. A new document takes its folder's"
            " name until you change it here.",
            on=self._rename,
        )
        self.dirty = kit.label("unsaved", role="chip", wrap=False)
        self.dirty.setToolTip("The document has edits that are not saved yet")
        self.where = kit.label(role="muted", selectable=True)
        self.where.setToolTip(DOC_TIP)
        head = kit.Form()
        head.row("Name", kit.row(self.name, self.dirty))
        head.row("Folder", self.where)
        self.stages = kit.Items(
            tip="The stages (sections) the document changes and how many edits each holds."
            " Click one to load it.",
            empty="No stage yet: edit a section and it joins.",
        )
        self.stages.picked.connect(self._load)
        self.add = kit.button(
            "Add the loaded section",
            tip="Lists the loaded section in the document now, before it has any edit; a"
            " section joins on its own with its first edit",
            on=studio.act("add section", self._add),
        )

        files = kit.Section("File", tip=DOC_TIP)
        files.body.addWidget(
            kit.row(
                kit.button(
                    "New…",
                    tip="Starts a new document in a folder you pick, holding the loaded section's"
                    " edits. The old document's other sections are left behind, so save it first.",
                    on=self._new,
                    icon="ph.file-plus",
                ),
                kit.button(
                    "Open…",
                    tip="Opens a map document (its map.toml), as File > Open does",
                    on=self._open,
                    icon="ph.folder-open",
                ),
                stretch=True,
            )
        )
        files.body.addWidget(
            kit.row(
                kit.button(
                    "Save",
                    tip="Writes map.toml and the edit lists into the document's folder; asks for"
                    " a folder the first time",
                    on=studio.save,
                    role="primary",
                    icon="ph.floppy-disk",
                ),
                kit.button(
                    "Save As…",
                    tip="Writes the document into another folder and goes on with that one",
                    on=self._save_as,
                ),
                stretch=True,
            )
        )
        checks = kit.Section(
            "Check and export",
            tip="Find problems before the game does, then write the bytes a release ships",
        )
        checks.body.addWidget(
            kit.row(
                kit.button(
                    "Check",
                    tip="Runs every check on the document now: missing files, edits the game"
                    " would refuse, values out of range. The list is in the Findings panel.",
                    on=studio.act("check", self._check),
                    icon="ph.check-circle",
                ),
                kit.button(
                    "Export…",
                    tip="Writes, into a folder you pick, the changed bytes of every stage and an"
                    " export.json describing them: what a release of this map mod ships. A mesh"
                    " that would not fit in place is left out and marked UNSAFE.",
                    on=self._export,
                    icon="ph.export",
                ),
                stretch=True,
            )
        )
        self.note = kit.label(role="muted", selectable=True)
        checks.body.addWidget(self.note)

        self.nodata = kit.pill("warning", f"{NO_DATA}: no section can load. {DATA_HINT}")
        self.nodata.setWordWrap(True)
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        self.intro = kit.label(DOC_TIP, role="muted")
        lay.addWidget(self.nodata)
        lay.addWidget(self.intro)
        lay.addWidget(head)
        lay.addWidget(self.stages)
        lay.addWidget(self.add)
        lay.addWidget(files)
        lay.addWidget(checks)
        lay.addStretch(1)
        self.body.addWidget(page)

    # ---- actions ---------------------------------------------------------------------- #

    def new(self, directory: Path) -> None:
        """A document in `directory` holding the loaded section's list; not saved yet."""
        ws = self.ws
        doc = MapDocument(directory.name, ws.row, directory)
        doc.game = ws.game
        if ws.scene is not None and ws.session is not None:
            doc.ensure_stage(ws.scene.stage, ops=ws.session.ops)
            doc.session = ws.session
            ws.session.base_dir = directory
        ws.doc = doc
        self.studio.findings.stale()
        self.note.setText(f"New document {doc.name}: not saved yet.")

    def _new(self) -> None:
        if not self.studio.discard_ok(self.ws):
            return
        got = QFileDialog.getExistingDirectory(self, "A folder for the new map document")
        if got:
            self.studio.act("new document", lambda: self.new(Path(got)))()

    def _open(self) -> None:
        dialogs.open_document(self, self.studio)

    def _save_as(self) -> None:
        path = dialogs.ask_save_as(self, self.ws)
        if path is not None:
            self.studio.save(path)

    def _rename(self, name: str) -> None:
        doc = self.ws.doc
        name = name.strip()
        if name and name != doc.name:
            self.studio.act("rename", lambda: self._set_name(name))()

    def _set_name(self, name: str) -> None:
        self.ws.doc.name = name
        self.ws.doc.touch()

    def _add(self) -> None:
        ws = self.ws
        if ws.scene is not None and ws.session is not None:
            ws.doc.ensure_stage(ws.scene.stage, ops=ws.session.ops)

    def _load(self, stage: object) -> None:
        if isinstance(stage, int):
            self.studio.act(f"load st{stage:03d}", lambda: self.ws.load_stage(stage))()

    def check(self) -> str:
        """Runs the findings now; their counts."""
        f = self.studio.findings
        f.stale()
        found = f.get(self.ws.doc)
        counts = ", ".join(f"{sum(x.level == lv for x in found)} {lv}" for lv in LEVELS)
        return f"{len(found)} finding(s): {counts}. The list is in the Findings panel."

    def _check(self) -> None:
        self.note.setText(self.check())

    def export(self, out: Path) -> str:
        doc = self.ws.doc
        if doc.directory is None:
            return "Save the document first: the export reads its assets from its folder."
        try:
            manifest = doc.export(out)
        except (ValueError, OSError) as e:
            return f"Export failed: {plain(str(e))}"
        return f"Exported to {plain(str(out))}:\n{summary(manifest)}"

    def _export(self) -> None:
        got = QFileDialog.getExistingDirectory(self, "A folder for the exported bytes")
        if got:
            self.studio.act("export", lambda: self.note.setText(self.export(Path(got))))()

    # ---- sync ------------------------------------------------------------------------- #

    def sync(self) -> None:
        ws = self.ws
        self.nodata.setVisible(ws.atlas is None)
        doc, sc = ws.doc, ws.scene
        self.intro.setVisible(doc.directory is None and not doc.stages)
        if not self.name.hasFocus() and self.name.text() != doc.name:
            with QSignalBlocker(self.name):
                self.name.setText(doc.name)
        self.dirty.setVisible(doc.dirty)
        row = f"row {doc.row}" if doc.row is not None else "no row"
        self.where.setText(
            f"{plain(str(doc.directory))}  ({row})" if doc.directory else f"not saved yet ({row})"
        )
        loaded = sc.stage if sc is not None else None
        rebuilt = self.stages.set_items(
            [
                kit.Item(
                    f"{s.label}  {s.ops_file}  {len(s.ops)} edit(s)"
                    + ("  (loaded)" if s.number == loaded else ""),
                    s.number,
                    f"{s.label}'s edits live in {s.ops_file}. Click to load it.",
                )
                for s in doc.stages
            ]
        )
        if rebuilt:
            fit(self.stages)
        self.add.setVisible(
            sc is not None and ws.session is not None and doc.stage(sc.stage) is None
        )
        if sc is not None:
            self.add.setText(f"Add st{sc.stage:03d} to the document")
