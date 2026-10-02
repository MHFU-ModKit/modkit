"""QUEST_PREP: stage edits in the village for the monsters a quest will spawn.

The registry is empty before the quest, so the left panel picks a species and the right panel
holds the staged edits; the writer writes each to every matching monster once it spawns. Without
a writer (`hud --read-only`) the edits are off.

    left / right   switch panel
    up / down      select; PgUp/PgDn jump 10 species
    ENTER          stage a size edit for the species, or toggle the selected edit
    + / -          adjust the selected edit
    T              cycle the edit's kind: size, species swap, HP
    X / DEL        remove the selected edit
    B              bind a species id (two hex digits) to a picker entry without one
    M              master switch for writing the edits
"""

from __future__ import annotations

from dataclasses import replace

import pygame

from .. import widgets as W
from ..assets import AssetLibrary
from ..calibration import Calibration
from ..edits import Edit, Kind, WriterStatus
from ..monster_db import PICKER_SPECIES
from ..state import GameSnapshot
from ..theme import CANVAS_H, CANVAS_W, C
from .base import Layout, Writer

R_HEADER = pygame.Rect(12, 12, CANVAS_W - 24, 50)
R_PICKER = pygame.Rect(12, 70, 360, CANVAS_H - 70 - 50)
R_EDITS = pygame.Rect(380, 70, CANVAS_W - 380 - 12, CANVAS_H - 70 - 50)
R_FOOTER = pygame.Rect(12, CANVAS_H - 40, CANVAS_W - 24, 28)
READ_ONLY = "writes off: started with --read-only"


class QuestPrepLayout(Layout):
    name = "quest_prep"

    def __init__(
        self, assets: AssetLibrary, calib: Calibration, writer: Writer | None = None
    ) -> None:
        super().__init__(assets, calib)
        self.writer = writer
        self.zone = "picker"
        self.selected_species = 0
        self.selected_edit = 0
        self.custom_bytes: dict[int, int] = {}
        """Species ids bound with B, by picker index."""
        self._binding_input: str | None = None
        """The hex digits typed so far while binding; None when not binding."""

    # --- input ---

    def handle_key(self, key: int, snapshot: GameSnapshot) -> bool:
        if key == pygame.K_LEFT:
            self.zone = "picker"
            return True
        if key == pygame.K_RIGHT:
            self.zone = "edits"
            return True
        if key == pygame.K_m and self.writer is not None and self._binding_input is None:
            self.writer.toggle()
            return True
        if self.zone == "picker":
            return self._picker_key(key)
        return self._edits_key(key)

    def _picker_key(self, key: int) -> bool:
        n = len(PICKER_SPECIES)
        if self._binding_input is not None:
            ch = pygame.key.name(key)
            if len(ch) == 1 and ch in "0123456789abcdef":
                self._binding_input += ch
                if len(self._binding_input) == 2:
                    val = int(self._binding_input, 16)
                    self.custom_bytes[self.selected_species] = val
                    self._binding_input = None
            elif key == pygame.K_ESCAPE:
                self._binding_input = None
            return True
        steps = {pygame.K_UP: -1, pygame.K_DOWN: 1, pygame.K_PAGEUP: -10, pygame.K_PAGEDOWN: 10}
        if key in steps:
            self.selected_species = (self.selected_species + steps[key]) % n
            return True
        if key == pygame.K_RETURN:
            species = self._species_id(self.selected_species)
            if self.writer is not None and species is not None:
                self.writer.stage(Edit.of(Kind.SIZE, species))
                self.selected_edit = len(self.writer.status.staged) - 1
                self.zone = "edits"
            return True
        if key == pygame.K_b:
            self._binding_input = ""
            return True
        return False

    def _edits_key(self, key: int) -> bool:
        if self.writer is None:
            return False
        staged = self.writer.status.staged
        if not staged:
            return False
        n = len(staged)
        self.selected_edit = min(self.selected_edit, n - 1)
        if key in (pygame.K_UP, pygame.K_DOWN):
            self.selected_edit = (self.selected_edit + (1 if key == pygame.K_DOWN else -1)) % n
            return True
        e = staged[self.selected_edit].edit
        new: Edit | None
        if key == pygame.K_RETURN:
            new = replace(e, enabled=not e.enabled)
        elif key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
            new = e.stepped(1)
        elif key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            new = e.stepped(-1)
        elif key == pygame.K_t:
            new = e.next_kind()
        elif key in (pygame.K_DELETE, pygame.K_x):
            new = None
        else:
            return False
        self.writer.change(self.selected_edit, new)
        self.selected_edit = max(0, min(self.selected_edit, len(self.writer.status.staged) - 1))
        return True

    # --- helpers ---

    def _species_id(self, idx: int) -> int | None:
        _name, species = PICKER_SPECIES[idx]
        return species if species is not None else self.custom_bytes.get(idx)

    # --- render ---

    def render(self, surface: pygame.Surface, snapshot: GameSnapshot) -> None:
        surface.fill(C.BG)
        status = self.writer.status if self.writer else None
        self._header(surface, snapshot, status)
        self._picker(surface)
        self._edits(surface, status)
        self._footer(surface)

    def _header(
        self, surface: pygame.Surface, snapshot: GameSnapshot, status: WriterStatus | None
    ) -> None:
        W.panel(surface, R_HEADER)
        W.text(
            surface,
            "QUEST PREP — staged edits, written to game memory as monsters spawn",
            (R_HEADER.x + 12, R_HEADER.y + 6),
            size=16,
            color=C.ACCENT,
            bold=True,
        )
        if status is None:
            line, color = READ_ONLY, C.WARN
        else:
            master = "ON" if status.enabled else "OFF"
            line = f"AUTO-APPLY: {master}   {len(status.staged)} edits   {status.writes} writes"
            if status.error:
                line += f"   last error: {status.error}"
            color = C.TEXT_DIM
        W.text(
            surface,
            f"ctx={snapshot.context.value}   area_index={snapshot.area_index}   {line}",
            (R_HEADER.x + 12, R_HEADER.y + 28),
            size=12,
            color=color,
        )

    def _picker(self, surface: pygame.Surface) -> None:
        title = "SPECIES" + (" (selected)" if self.zone == "picker" else "")
        W.panel(surface, R_PICKER, title=title)
        # 'B' input mode banner
        if self._binding_input is not None:
            W.text(
                surface,
                f"BIND SPECIES ID: 0x{self._binding_input}__   (hex digits; ESC cancel)",
                (R_PICKER.x + 12, R_PICKER.y + 30),
                size=12,
                color=C.WARN,
                bold=True,
            )
        row_h = 16
        n = len(PICKER_SPECIES)
        max_rows = (R_PICKER.h - 50) // row_h
        start = max(0, min(self.selected_species - max_rows // 2, n - max_rows))
        for i in range(start, min(n, start + max_rows)):
            name, primary_byte = PICKER_SPECIES[i]
            bind_byte = self.custom_bytes.get(i)
            eff = primary_byte if primary_byte is not None else bind_byte
            y = R_PICKER.y + 50 + (i - start) * row_h
            row = pygame.Rect(R_PICKER.x + 4, y, R_PICKER.w - 8, row_h - 1)
            if i == self.selected_species and self.zone == "picker":
                pygame.draw.rect(surface, C.PANEL_HI, row, border_radius=2)
                pygame.draw.rect(surface, C.SELECT, row, width=1, border_radius=2)
            if eff is not None:
                byte_str = f"0x{eff:02X}" if primary_byte is not None else f"0x{eff:02X}*"
                color = C.TEXT if primary_byte is not None else C.WARN
            else:
                byte_str = "  ? "
                color = C.PLACEHOLDER
            W.text(surface, byte_str, (row.x + 6, row.y + 1), size=11, color=color, bold=True)
            W.text(surface, name, (row.x + 56, row.y + 1), size=11, color=C.TEXT)

    def _edits(self, surface: pygame.Surface, status: WriterStatus | None) -> None:
        title = "STAGED EDITS" + (" (selected)" if self.zone == "edits" else "")
        W.panel(surface, R_EDITS, title=title)
        if status is None or not status.staged:
            hint = "no edits queued — focus PICKER (←), select species, ENTER to stage"
            W.text(
                surface,
                READ_ONLY if status is None else hint,
                (R_EDITS.x + 12, R_EDITS.y + 34),
                size=12,
                color=C.TEXT_FAINT,
            )
            return
        row_h = 22
        for i, st in enumerate(status.staged):
            e = st.edit
            y = R_EDITS.y + 30 + i * row_h
            row = pygame.Rect(R_EDITS.x + 4, y, R_EDITS.w - 8, row_h - 2)
            if i == self.selected_edit and self.zone == "edits":
                pygame.draw.rect(surface, C.PANEL_HI, row, border_radius=3)
                pygame.draw.rect(surface, C.SELECT, row, width=1, border_radius=3)
            mark = "[x]" if e.enabled else "[ ]"
            mark_color = C.OK if e.enabled else C.TEXT_FAINT
            W.text(surface, mark, (row.x + 8, row.y + 4), size=12, color=mark_color, bold=True)
            W.text(surface, e.label, (row.x + 38, row.y + 4), size=12, color=C.TEXT)
            W.text(
                surface,
                f"applied {st.applied}",
                (row.right - 8, row.y + 4),
                size=11,
                color=C.TEXT_FAINT,
                align="right",
            )

    def _footer(self, surface: pygame.Surface) -> None:
        W.text(
            surface,
            "←/→ panel   ↑/↓ select   PgUp/Dn jump 10   "
            "ENTER stage/toggle   +/- value   T kind   "
            "X remove   B bind species   M master",
            (R_FOOTER.x + 4, R_FOOTER.y + 6),
            size=11,
            color=C.TEXT_FAINT,
        )
