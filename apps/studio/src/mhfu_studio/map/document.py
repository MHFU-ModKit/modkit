# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A map mod on disk: `map.toml` and one edit list per stage, plus the assets its ops name.

    [map]
    name = "snowy_camp"
    row = 11                      # the map row, so the editor knows the sections

    [[stage]]
    number = 98
    ops = "st098.json"            # the schema of `stage.ops`

Stages naming the same list (a night twin) share it: editing one edits both. The loaded
stage's list is its edit session's own, so undo, redo and dirty go through the session.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tomli_w
from mhfu.files import Extracted

from mhfu_studio.shell.findings import Finding
from mhfu_studio.stage import collision, mesh, textures
from mhfu_studio.stage import ops as O
from mhfu_studio.stage.file import StageFile

if TYPE_CHECKING:
    from .core.edit import EditSession

MANIFEST = "map.toml"
SCHEMA = 1
"""The version of `export.json`."""
Op = dict[str, Any]


class DocumentError(ValueError):
    """The document cannot be loaded at all."""


@dataclass
class StageEntry:
    number: int
    ops_file: str
    ops: list[Op] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"st{self.number:03d}"


def default_file(number: int) -> str:
    return f"st{number:03d}.json"


class MapDocument:
    """The shell's `Document`; `game` is the evidence the checks and the export need."""

    def __init__(
        self,
        name: str,
        row: int | None = None,
        directory: Path | None = None,
        description: str = "",
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.row = row
        self.directory = directory
        self.description = description
        #: the [map] table's other keys, kept for the round trip
        self.extra: dict[str, Any] = dict(extra or {})
        self.stages: list[StageEntry] = []
        self.session: EditSession | None = None
        self.game: Extracted | None = None
        self._files: dict[int, StageFile] = {}
        self._changes = 0
        self._saved = self._state()
        self._dirty_at: tuple[int, int, int] = (-1, 0, 0)
        self._dirty = False

    @classmethod
    def untitled(cls, row: int | None = None) -> MapDocument:
        return cls("untitled", row)

    @property
    def path(self) -> Path | None:
        return None if self.directory is None else self.directory / MANIFEST

    # the stages

    def stage(self, number: int) -> StageEntry | None:
        return next((s for s in self.stages if s.number == number), None)

    def ensure_stage(
        self, number: int, ops_file: str | None = None, ops: list[Op] | None = None
    ) -> StageEntry:
        """The stage's entry, added when missing: over `ops` (a session's own list), else the
        list another stage keeps in that file, else a new one."""
        s = self.stage(number)
        if s is None:
            file = ops_file or default_file(number)
            shared = next((t.ops for t in self.stages if t.ops_file == file), None)
            if ops is None:
                ops = shared if shared is not None else []
            s = StageEntry(number, file, ops)
            self.stages.append(s)
            self.touch()
        return s

    def shared_lists(self) -> dict[str, list[int]]:
        """Per ops file, the stages using it."""
        out: dict[str, list[int]] = {}
        for s in self.stages:
            out.setdefault(s.ops_file, []).append(s.number)
        return out

    def touch(self) -> None:
        """Something besides the session changed."""
        self._changes += 1

    # loading and saving

    @classmethod
    def load(cls, path: Path) -> MapDocument:
        """`map.toml`, or the folder holding it, and every list it names."""
        p = path / MANIFEST if path.is_dir() else path
        if not p.is_file():
            raise DocumentError(f"no {MANIFEST} at {p}")
        try:
            data = tomllib.loads(p.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise DocumentError(f"{p}: {e}") from None
        m = data.get("map")
        if not isinstance(m, dict) or "name" not in m:
            raise DocumentError(f"{p}: a [map] table with a name is required")
        row = m.get("row")
        if row is not None and (not isinstance(row, int) or isinstance(row, bool)):
            raise DocumentError(f"{p}: map.row must be an integer")
        extra = {k: v for k, v in m.items() if k not in ("name", "row", "description")}
        doc = cls(str(m["name"]), row, p.parent, str(m.get("description", "")), extra)
        lists: dict[str, list[Op]] = {}
        for i, s in enumerate(data.get("stage", [])):
            n = s.get("number") if isinstance(s, dict) else None
            if not isinstance(n, int) or isinstance(n, bool):
                raise DocumentError(f"{p}: [[stage]] {i} needs an integer `number`")
            if doc.stage(n) is not None:
                raise DocumentError(f"{p}: st{n:03d} is listed twice")
            file = str(s.get("ops", default_file(n)))
            if file not in lists:
                f = p.parent / file
                if not f.is_file():
                    raise DocumentError(f"{p}: st{n:03d} names {f}, which does not exist")
                try:
                    lists[file] = O.load(f)
                except ValueError as e:
                    raise DocumentError(str(e)) from None
            doc.stages.append(StageEntry(n, file, lists[file]))
        doc._saved = doc._state()
        return doc

    def save(self, path: Path | None = None) -> Path:
        """Writes the lists and `map.toml` into `path` (the folder, or its map.toml), or where
        the document came from. Returns the manifest's path."""
        if path is not None:
            directory = path.parent if path.name == MANIFEST or path.suffix == ".toml" else path
            if self.name == "untitled":
                self.name = directory.name
            self.directory = directory
        if self.directory is None:
            raise ValueError("the document has no folder yet: pick one (Save As, or Document)")
        d = self.directory
        d.mkdir(parents=True, exist_ok=True)
        for file, ops in {s.ops_file: s.ops for s in self.stages}.items():
            (d / file).parent.mkdir(parents=True, exist_ok=True)
            (d / file).write_text(json.dumps(ops, indent=1) + "\n", encoding="utf-8")
        p = d / MANIFEST
        p.write_bytes(tomli_w.dumps(self.manifest()).encode())
        if self.session is not None:
            self.session.base_dir = d
        self._saved = self._state()
        self._dirty_at = (-1, 0, 0)
        return p

    def manifest(self) -> dict[str, Any]:
        """`map.toml` as a dict."""
        table: dict[str, Any] = {"name": self.name}
        if self.row is not None:
            table["row"] = self.row
        if self.description:
            table["description"] = self.description
        table.update(self.extra)
        stages = [{"number": s.number, "ops": s.ops_file} for s in self.stages]
        return {"map": table, "stage": stages} if stages else {"map": table}

    def _state(self) -> str:
        lists = {s.ops_file: s.ops for s in self.stages}
        return json.dumps([self.manifest(), lists], sort_keys=True)

    @property
    def dirty(self) -> bool:
        s = self.session
        at = (self._changes, id(s), s.revision if s else 0)
        if at != self._dirty_at:
            self._dirty_at = at
            self._dirty = self._state() != self._saved
        return self._dirty

    # undo and redo: the loaded stage's session

    def can_undo(self) -> bool:
        return self.session is not None and self.session.can_undo()

    def can_redo(self) -> bool:
        return self.session is not None and self.session.can_redo()

    def undo(self) -> None:
        if self.session is not None:
            self.session.undo()

    def redo(self) -> None:
        if self.session is not None:
            self.session.redo()

    # checks and export

    def file(self, number: int) -> StageFile:
        """The shipped stage, read once."""
        if self.game is None:
            raise ValueError("no extracted game: set MHFU_DATA")
        if number not in self._files:
            self._files[number] = StageFile.read(self.game, number)
        return self._files[number]

    def findings(self) -> list[Finding]:
        """The op checks of `stage.ops` per stage (with the writers when the game is there),
        and the document's own."""
        out: list[Finding] = []
        if not self.stages and self.directory is not None:
            out.append(Finding("warning", "no-stages", "the document names no stage"))
        for file, users in self.shared_lists().items():
            if len(users) > 1:
                names = ", ".join(f"st{n:03d}" for n in users)
                out.append(Finding("info", "shared-list", f"{file} is used by {names}", file))
        if self.stages and self.game is None:
            out.append(
                Finding(
                    "warning",
                    "no-evidence",
                    "no extracted game: the checks that need a stage were skipped",
                )
            )
        for s in self.stages:
            sf = None
            if self.game is not None:
                try:
                    sf = self.file(s.number)
                except (OSError, ValueError) as e:
                    out.append(Finding("error", "no-stage", str(e), s.label, (s.number, None)))
            out += O.check(s.ops, base_dir=self.directory, stage=sf, where=s.label)
        return out

    def export(self, out_dir: Path) -> dict[str, Any]:
        """Per stage, the resident-safe bytes each half needs, and `export.json`:
        `stNNN_sub{0,2}.bin` (a mesh that would move a block is left out), the collision plan
        as `stNNN_collision.json`, the bank as `stNNN_sub1.bin`."""
        out_dir.mkdir(parents=True, exist_ok=True)
        base = self.directory or Path(".")
        manifest: dict[str, Any] = {"schema": SCHEMA, "name": self.name, "row": self.row}
        stages = []
        for s in self.stages:
            sf = self.file(s.number)
            rec: dict[str, Any] = {"stage": s.number, "ops": len(s.ops), "mesh": {}}
            rec["collision"] = rec["texture"] = None
            for sub, e in mesh.edits(sf, s.ops, base).items():
                name = f"{s.label}_sub{sub}.bin"
                if e.safe:
                    (out_dir / name).write_bytes(e.data)
                runs = e.runs() if len(e.data) == len(e.original) else []
                rec["mesh"][str(sub)] = {
                    "file": name if e.safe else None,
                    "safe": e.safe,
                    "bytes": sum(n for _, n in runs),
                    "runs": len(runs),
                    "applied": e.applied,
                    "size": len(e.data),
                }
            if any(O.touches_collision(o) for o in s.ops):
                plan = collision.serialise(collision.plan(sf, s.ops, base))
                name = f"{s.label}_collision.json"
                (out_dir / name).write_text(json.dumps(plan, indent=1), encoding="utf-8")
                cells = sum(len(v) for v in plan["cells"].values())
                rec["collision"] = {
                    "file": name,
                    "moved": len(plan["moved"]),
                    "added": len(plan["added"]),
                    "cells": cells,
                }
            if any(O.touches_textures(o) for o in s.ops):
                bank = textures.build(sf, s.ops, base)
                name = f"{s.label}_sub1.bin"
                (out_dir / name).write_bytes(bank.data)
                rec["texture"] = {"file": name, "size": len(bank.data)}
            stages.append(rec)
        manifest["stages"] = stages
        (out_dir / "export.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        return manifest
