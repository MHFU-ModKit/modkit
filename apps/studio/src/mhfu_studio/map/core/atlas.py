# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The map table as rows of sections, entry area first; the village is row 0.

Names come only from the game: three rows and the snowy bank are labelled, everything else
is `row N` / `stNNN`. Day/night twins share their collision bytes, paired by hash, not by an
offset that holds for one map only.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field

from mhfu import files
from mhfu import stage as S
from mhfu.files import Extracted

from mhfu_studio.stage.file import COLLISION, StageFile

PLACEHOLDER = 2048
"""A stub stage is this many bytes or fewer."""

ROW_NAMES: dict[int, str] = {
    0: "Pokke village & town",
    11: "Snowy Mountains (day)",
    21: "Snowy Mountains (night)",
}

STAGE_NAMES: dict[int, str] = {
    139: "Pokke village",
    66: "Pokke village (twin of st139)",
    98: "Snowy base camp",
    99: "Snowy area 1",
    95: "Snowy area 2",
    94: "Snowy area 3",
    92: "Snowy area 4",
    93: "Snowy area 5",
    100: "Snowy area 6",
    96: "Snowy area 7",
    97: "Snowy area 8",
    6: "Snowy secret area",
    107: "Snowy base camp (night)",
    108: "Snowy area 1 (night)",
    104: "Snowy area 2 (night)",
    103: "Snowy area 3 (night)",
    101: "Snowy area 4 (night)",
    102: "Snowy area 5 (night)",
    109: "Snowy area 6 (night)",
    105: "Snowy area 7 (night)",
    106: "Snowy area 8 (night)",
    15: "Snowy secret area (night)",
}

ORPHAN_STAGES = (126, 198, 199, 200)
"""Complete maps no row references: a cut section and three unconnected arenas."""


def stage_name(stage: int) -> str:
    return STAGE_NAMES.get(stage, f"st{stage:03d}")


def stage_title(stage: int) -> str:
    """`st139  Pokke village`, or `st140` alone for a stage the game gives no name."""
    name = STAGE_NAMES.get(stage)
    return f"st{stage:03d}  {name}" if name else f"st{stage:03d}"


def row_name(row: int) -> str:
    return ROW_NAMES.get(row, f"row {row}")


@dataclass
class Section:
    """A stage in a row; `slot` is its index there (0 the entry area)."""

    stage: int
    slot: int
    row: int
    name: str
    is_entry: bool
    present: bool = True
    """The file is a real map, not a placeholder."""
    size: int = 0


@dataclass
class MapRow:
    index: int
    name: str
    sections: list[Section] = field(default_factory=list)

    @property
    def entry(self) -> Section | None:
        return self.sections[0] if self.sections else None

    @property
    def stages(self) -> list[int]:
        return [s.stage for s in self.sections]

    def slot_of(self, stage: int) -> int | None:
        return next((s.slot for s in self.sections if s.stage == stage), None)


class Atlas:
    """The rows of `game_sub.ovl` (or `table`), read once; overlays are read on demand."""

    def __init__(self, game: Extracted, table: Sequence[Sequence[int]] | None = None) -> None:
        self.game = game
        self.rows: list[MapRow] = []
        self._by_stage: dict[int, list[tuple[int, int]]] = {}
        self._overlays: dict[int, S.StageOverlay | None] = {}
        for i, stages in enumerate(S.read_map_table(game) if table is None else table):
            row = MapRow(i, row_name(i))
            for slot, n in enumerate(stages):
                size = self.size(n)
                row.sections.append(
                    Section(n, slot, i, stage_name(n), slot == 0, size > PLACEHOLDER, size)
                )
                self._by_stage.setdefault(n, []).append((i, slot))
            self.rows.append(row)

    def size(self, stage: int) -> int:
        try:
            return self.game.path(files.stage_pac(stage)).stat().st_size
        except (OSError, ValueError):
            return 0

    def row(self, index: int) -> MapRow:
        return self.rows[index]

    @property
    def village(self) -> MapRow:
        return self.rows[0]

    def live_rows(self) -> list[MapRow]:
        """Rows with at least one section."""
        return [r for r in self.rows if r.sections]

    def rows_of(self, stage: int) -> list[tuple[int, int]]:
        """(row, slot) of every row naming the stage."""
        return list(self._by_stage.get(stage, ()))

    def section(self, stage: int, row: int | None = None) -> Section | None:
        hits = [h for h in self.rows_of(stage) if row is None or h[0] == row]
        if not hits:
            return None
        r, slot = hits[0]
        return self.rows[r].sections[slot]

    def overlay(self, stage: int) -> S.StageOverlay | None:
        if stage not in self._overlays:
            try:
                self._overlays[stage] = S.StageOverlay.read(self.game, stage)
            except (OSError, ValueError):
                self._overlays[stage] = None
        return self._overlays[stage]

    def arrivals(self, stage: int) -> list[tuple[int, S.Exit]]:
        """(from stage, exit) of every exit in the stage's rows that lands in it: an exit's
        `dest` is in the target's frame, so only these can be drawn here."""
        out = []
        seen: set[int] = set()
        for r, _ in self.rows_of(stage):
            for s in self.rows[r].sections:
                if s.stage == stage or s.stage in seen or not s.present:
                    continue
                seen.add(s.stage)
                ov = self.overlay(s.stage)
                out += [(s.stage, e) for e in (ov.exits() if ov else []) if e.target == stage]
        return out

    def unreferenced(self) -> list[int]:
        """Stage files that exist and no row names."""
        return [n for n in files.STAGES[1:] if n not in self._by_stage and self.size(n) > 0]

    def twins(self) -> dict[int, list[int]]:
        """Per stage, the others sharing its collision bytes."""
        groups: dict[str, list[int]] = {}
        for r in self.rows:
            for s in r.sections:
                if not s.present:
                    continue
                try:
                    data = StageFile.read(self.game, s.stage).entry(COLLISION)
                except (OSError, ValueError):
                    continue
                members = groups.setdefault(hashlib.sha1(data).hexdigest(), [])
                if s.stage not in members:
                    members.append(s.stage)
        return {m: [n for n in ms if n != m] for ms in groups.values() for m in ms}

    def describe(self, row: int | None = None) -> str:
        lines = []
        for r in [self.rows[row]] if row is not None else self.live_rows():
            lines.append(f"row {r.index:2d}  {r.name:<28} {len(r.sections)} sections")
            for s in r.sections:
                entry = "entry " if s.is_entry else "      "
                stub = "" if s.present else "(placeholder)"
                lines.append(f"   [{s.slot:2d}] {stage_title(s.stage):<33} {entry}{stub}")
        return "\n".join(lines)
