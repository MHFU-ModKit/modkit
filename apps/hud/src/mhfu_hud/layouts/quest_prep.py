"""QUEST_PREP: stage edits in the village for the monsters a quest will spawn.

The registry is empty before the quest, so the left panel picks a species and the right panel
holds the staged edits; the reader writes each to every matching monster once it spawns.

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

import pygame

from .. import widgets as W
from ..assets import AssetLibrary
from ..calibration import Calibration
from ..edits import EditBank, StagedEdit
from ..monster_db import PICKER_SPECIES, species_name
from ..state import GameSnapshot
from ..theme import CANVAS_H, CANVAS_W, C
from .base import Layout, Reader

R_HEADER = pygame.Rect(12, 12, CANVAS_W - 24, 50)
R_PICKER = pygame.Rect(12, 70, 360, CANVAS_H - 70 - 50)
R_EDITS = pygame.Rect(380, 70, CANVAS_W - 380 - 12, CANVAS_H - 70 - 50)
R_FOOTER = pygame.Rect(12, CANVAS_H - 40, CANVAS_W - 24, 28)

SIZE_STEP = 0.05
HP_STEP = 10
EDIT_KINDS = ["size_species", "type_swap", "hp_slot"]


class QuestPrepLayout(Layout):
    name = "quest_prep"

    def __init__(
        self, assets: AssetLibrary, calib: Calibration, reader: Reader | None = None
    ) -> None:
        super().__init__(assets, calib)
        self.reader = reader
        self.zone = "picker"
        self.selected_species = 0
        self.selected_edit = 0
        self.custom_bytes: dict[int, int] = {}
        """Species ids bound with B, by picker index."""
        self._binding_input: str | None = None
        """The hex digits typed so far while binding; None when not binding."""

    # --- input ---

    def handle_key(self, key: int, snapshot: GameSnapshot) -> bool:
        bank = self.reader.edit_bank if self.reader else None
        if key == pygame.K_LEFT:
            self.zone = "picker"
            return True
        if key == pygame.K_RIGHT:
            self.zone = "edits"
            return True
        if self.zone == "picker":
            return self._picker_key(key, snapshot, bank)
        return self._edits_key(key, snapshot, bank)

    def _picker_key(self, key: int, snapshot: GameSnapshot, bank: EditBank | None) -> bool:
        n = len(PICKER_SPECIES)
        if self._binding_input is not None:
            ch = pygame.key.name(key)
            if len(ch) == 1 and ch in "0123456789abcdef":
                self._binding_input += ch
                if len(self._binding_input) == 2:
                    val = int(self._binding_input, 16)
                    self.custom_bytes[self.selected_species] = val
                    self._binding_input = None
                return True
            if key == pygame.K_ESCAPE:
                self._binding_input = None
                return True
            return True
        if key == pygame.K_UP:
            self.selected_species = (self.selected_species - 1) % n
            return True
        if key == pygame.K_DOWN:
            self.selected_species = (self.selected_species + 1) % n
            return True
        if key == pygame.K_PAGEUP:
            self.selected_species = (self.selected_species - 10) % n
            return True
        if key == pygame.K_PAGEDOWN:
            self.selected_species = (self.selected_species + 10) % n
            return True
        if key == pygame.K_RETURN:
            if bank is None:
                return True
            tb = self._effective_type_byte(self.selected_species)
            if tb is None:
                return True  # nothing to key on until a species id is bound
            name, _ = PICKER_SPECIES[self.selected_species]
            bank.add(
                StagedEdit(
                    kind="size_species",
                    selector_value=tb,
                    new_value=1.0,
                    label=f"size {name} (species 0x{tb:02X}) -> 1.00",
                )
            )
            self.selected_edit = len(bank.edits) - 1
            self.zone = "edits"
            return True
        if key == pygame.K_b:
            self._binding_input = ""
            return True
        if key == pygame.K_m and bank is not None:
            bank.toggle_master()
            return True
        return False

    def _edits_key(self, key: int, snapshot: GameSnapshot, bank: EditBank | None) -> bool:
        if bank is None or not bank.edits:
            if key == pygame.K_m and bank is not None:
                bank.toggle_master()
                return True
            return False
        n = len(bank.edits)
        if key == pygame.K_UP:
            self.selected_edit = (self.selected_edit - 1) % n
            return True
        if key == pygame.K_DOWN:
            self.selected_edit = (self.selected_edit + 1) % n
            return True
        e = bank.edits[self.selected_edit]
        if key == pygame.K_RETURN:
            bank.toggle(self.selected_edit)
            return True
        if key in (pygame.K_DELETE, pygame.K_x):
            bank.remove(self.selected_edit)
            if self.selected_edit >= len(bank.edits):
                self.selected_edit = max(0, len(bank.edits) - 1)
            return True
        if key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
            if e.kind in ("size_species", "size_slot"):
                e.new_value = round(e.new_value + SIZE_STEP, 3)
            elif e.kind == "hp_slot":
                e.new_value += HP_STEP
            elif e.kind == "type_swap":
                e.new_value = float((int(e.new_value) + 1) & 0xFF)
            e.reset_applied()
            self._refresh_label(e)
            return True
        if key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            if e.kind in ("size_species", "size_slot"):
                e.new_value = round(max(0.1, e.new_value - SIZE_STEP), 3)
            elif e.kind == "hp_slot":
                e.new_value = max(1, e.new_value - HP_STEP)
            elif e.kind == "type_swap":
                e.new_value = float((int(e.new_value) - 1) & 0xFF)
            e.reset_applied()
            self._refresh_label(e)
            return True
        if key == pygame.K_t:
            cur = EDIT_KINDS.index(e.kind) if e.kind in EDIT_KINDS else 0
            e.kind = EDIT_KINDS[(cur + 1) % len(EDIT_KINDS)]
            e.reset_applied()
            self._refresh_label(e)
            return True
        if key == pygame.K_m:
            bank.toggle_master()
            return True
        return False

    # --- helpers ---

    def _effective_type_byte(self, idx: int) -> int | None:
        _name, byte = PICKER_SPECIES[idx]
        if byte is not None:
            return byte
        return self.custom_bytes.get(idx)

    def _refresh_label(self, edit: StagedEdit) -> None:
        sel = edit.selector_value
        if edit.kind == "size_species":
            edit.label = f"size {species_name(sel)} (0x{sel:02X}) -> {edit.new_value:.2f}"
        elif edit.kind == "type_swap":
            tgt = int(edit.new_value) & 0xFF
            edit.label = (
                f"species {species_name(sel)} (0x{sel:02X}) -> {species_name(tgt)} (0x{tgt:02X})"
            )
        elif edit.kind == "hp_slot":
            edit.label = f"hp slot {sel} -> {int(edit.new_value)}"

    # --- render ---

    def render(self, surface: pygame.Surface, snapshot: GameSnapshot) -> None:
        surface.fill(C.BG)
        bank = self.reader.edit_bank if self.reader else None
        self._header(surface, snapshot, bank)
        self._picker(surface, bank)
        self._edits(surface, bank)
        self._footer(surface)

    def _header(
        self, surface: pygame.Surface, snapshot: GameSnapshot, bank: EditBank | None
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
        master = "ON" if (bank and bank.enabled) else "OFF"
        n_edits = len(bank.edits) if bank else 0
        W.text(
            surface,
            f"ctx={snapshot.context.value}   area_index={snapshot.area_index}   "
            f"AUTO-APPLY: {master}   {n_edits} edits queued",
            (R_HEADER.x + 12, R_HEADER.y + 28),
            size=12,
            color=C.TEXT_DIM,
        )

    def _picker(self, surface: pygame.Surface, bank: EditBank | None) -> None:
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
        row_h = 14
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

    def _edits(self, surface: pygame.Surface, bank: EditBank | None) -> None:
        title = "STAGED EDITS" + (" (selected)" if self.zone == "edits" else "")
        W.panel(surface, R_EDITS, title=title)
        if bank is None or not bank.edits:
            W.text(
                surface,
                "no edits queued — focus PICKER (←), select species, ENTER to stage",
                (R_EDITS.x + 12, R_EDITS.y + 34),
                size=12,
                color=C.TEXT_FAINT,
            )
            return
        row_h = 22
        for i, e in enumerate(bank.edits):
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
                f"applied {len(e.applied_to)}",
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
