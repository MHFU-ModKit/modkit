"""AI_MOD: a read-only inspector of each monster's AI cells and its species row.

Three columns: the roster, the selected monster's cells (labelled by their addresses.toml field,
with the field's doc on hover), and its SPECIES_TABLE row. Every value comes from the snapshot;
nothing here writes.
"""

from __future__ import annotations

import textwrap

import pygame

from .. import widgets as W
from ..assets import AssetLibrary
from ..calibration import Calibration
from ..panels import action_text, cell_label, fmt_value, section_text, state_text
from ..state import Cell, GameSnapshot, MonsterHUD
from ..theme import CANVAS_H, CANVAS_W, C, font
from .base import Layout

R_HEADER = pygame.Rect(8, 8, CANVAS_W - 16, 44)
R_ROSTER = pygame.Rect(8, 60, 200, CANVAS_H - 84)
R_CELLS = pygame.Rect(216, 60, 440, CANVAS_H - 84)
R_SPECIES = pygame.Rect(664, 60, CANVAS_W - 672, CANVAS_H - 84)

ROW_H = 16
ROSTER_ROW_H = 32
PAGE = 10
FLOATS_PER_LINE = 4
LABEL_W = 190


class AIModLayout(Layout):
    name = "ai_mod"

    def __init__(self, assets: AssetLibrary, calib: Calibration) -> None:
        super().__init__(assets, calib)
        self.sel_ptr: int | None = None
        self.scroll = 0
        self._mouse: tuple[float, float] | None = None
        self._rows: list[tuple[pygame.Rect, Cell]] = []

    # --- selection ---

    def _selected(self, monsters: tuple[MonsterHUD, ...]) -> MonsterHUD | None:
        if not monsters:
            return None
        for m in monsters:
            if m.ptr == self.sel_ptr:
                return m
        self.sel_ptr = monsters[0].ptr
        return monsters[0]

    def _step(self, monsters: tuple[MonsterHUD, ...], delta: int) -> None:
        cur = self._selected(monsters)
        if cur is None:
            return
        i = monsters.index(cur)
        self.sel_ptr = monsters[(i + delta) % len(monsters)].ptr
        self.scroll = 0

    # --- input ---

    def handle_key(self, key: int, snapshot: GameSnapshot) -> bool:
        monsters = snapshot.monsters
        if key == pygame.K_RIGHTBRACKET:
            self._step(monsters, 1)
        elif key == pygame.K_LEFTBRACKET:
            self._step(monsters, -1)
        elif key == pygame.K_DOWN:
            self.scroll += 1
        elif key == pygame.K_UP:
            self.scroll = max(0, self.scroll - 1)
        elif key == pygame.K_PAGEDOWN:
            self.scroll += PAGE
        elif key == pygame.K_PAGEUP:
            self.scroll = max(0, self.scroll - PAGE)
        elif key == pygame.K_HOME:
            self.scroll = 0
        else:
            return False
        return True

    def handle_click(self, pos: tuple[float, float], snapshot: GameSnapshot) -> bool:
        if not R_ROSTER.collidepoint(pos):
            return False
        i = int((pos[1] - (R_ROSTER.y + 26)) // ROSTER_ROW_H)
        if 0 <= i < len(snapshot.monsters):
            self.sel_ptr = snapshot.monsters[i].ptr
            self.scroll = 0
            return True
        return False

    def handle_motion(self, pos: tuple[float, float], snapshot: GameSnapshot) -> bool:
        self._mouse = pos
        return False

    # --- render ---

    def render(self, surface: pygame.Surface, snapshot: GameSnapshot) -> None:
        surface.fill(C.BG)
        m = self._selected(snapshot.monsters)
        self._header(surface, snapshot, m)
        self._roster(surface, snapshot, m)
        self._cells(surface, m)
        self._species(surface, snapshot, m)
        W.text(
            surface,
            "] / [ monster   up/down PgUp/PgDn scroll   HOME top   hover a cell for its doc",
            (10, CANVAS_H - 18),
            size=10,
            color=C.TEXT_FAINT,
        )
        self._tooltip(surface)

    def _header(self, surface: pygame.Surface, snap: GameSnapshot, m: MonsterHUD | None) -> None:
        W.panel(surface, R_HEADER, fill=C.PANEL_HI, border=C.ACCENT)
        if m is None:
            title, color = "AI MOD  no monster loaded", C.PLACEHOLDER
        else:
            big = "  [BIG]" if m.big else ""
            title = (
                f"AI MOD  {m.name}  slot {m.slot}  species 0x{m.species:02X}  "
                f"vt 0x{m.vtable:08X}  at 0x{m.ptr:08X}{big}"
            )
            color = C.SELECT
        W.text(surface, title, (R_HEADER.x + 12, R_HEADER.y + 4), size=15, color=color, bold=True)
        W.text(
            surface,
            f"ctx {snap.context.value}  area {snap.area_index}  section {section_text(snap)}  "
            f"poll #{snap.poll_count}",
            (R_HEADER.x + 12, R_HEADER.y + 24),
            size=11,
            color=C.TEXT_DIM,
        )

    def _roster(self, surface: pygame.Surface, snap: GameSnapshot, sel: MonsterHUD | None) -> None:
        W.panel(surface, R_ROSTER, title="ROSTER")
        if not snap.monsters:
            W.text(
                surface,
                "no monsters loaded",
                (R_ROSTER.x + 12, R_ROSTER.y + 32),
                size=12,
                color=C.PLACEHOLDER,
            )
            return
        y = R_ROSTER.y + 26
        for m in snap.monsters:
            if y + ROSTER_ROW_H > R_ROSTER.bottom:
                break
            row = pygame.Rect(R_ROSTER.x + 4, y, R_ROSTER.w - 8, ROSTER_ROW_H - 2)
            if sel is not None and m.ptr == sel.ptr:
                pygame.draw.rect(surface, C.PANEL_HI, row, border_radius=3)
                pygame.draw.rect(surface, C.SELECT, row, width=1, border_radius=3)
            W.text(
                surface,
                f"{m.name[:14]:<14}  s{m.slot}",
                (row.x + 8, row.y + 2),
                size=11,
                color=C.WARN if m.big else C.TEXT,
                bold=True,
            )
            W.text(
                surface,
                f"{action_text(m)[:22]}  hp {m.hp}",
                (row.x + 8, row.y + 16),
                size=10,
                color=C.TEXT_FAINT,
            )
            y += ROSTER_ROW_H

    def _cells(self, surface: pygame.Surface, m: MonsterHUD | None) -> None:
        W.panel(surface, R_CELLS, title="AI CELLS")
        self._rows = []
        if m is None:
            return
        x, y = R_CELLS.x + 10, R_CELLS.y + 26
        summary = [
            ("move (main, sub)", state_text(m)),
            ("animating", action_text(m)),
            ("drawn", "yes" if m.drawn else "no (out of view or paused)"),
        ]
        if not m.big:
            summary.append(("herd", f"{len(m.herd)} members"))
        y = int(W.kv_rows(surface, (x, y), summary, size=11, line_h=ROW_H, key_w=LABEL_W))
        pygame.draw.line(surface, C.PANEL_BORDER, (x, y + 2), (R_CELLS.right - 10, y + 2))
        y += 8
        rows = R_CELLS.bottom - 8 - y
        visible = max(1, rows // ROW_H)
        self.scroll = min(self.scroll, max(0, len(m.cells) - visible))
        for cell in m.cells[self.scroll : self.scroll + visible]:
            rect = pygame.Rect(x - 4, y, R_CELLS.w - 12, ROW_H)
            self._rows.append((rect, cell))
            W.text(surface, cell_label(cell.field), (x, y), size=11, color=C.TEXT_DIM)
            W.text(surface, fmt_value(cell.field, cell.value), (x + LABEL_W, y), size=11)
            y += ROW_H
        if len(m.cells) > visible:
            W.text(
                surface,
                f"{self.scroll + 1}-{self.scroll + visible} of {len(m.cells)}",
                (R_CELLS.right - 10, R_CELLS.y + 6),
                size=10,
                color=C.TEXT_FAINT,
                align="right",
            )

    def _species(self, surface: pygame.Surface, snap: GameSnapshot, m: MonsterHUD | None) -> None:
        W.panel(surface, R_SPECIES, title="SPECIES ROW")
        if m is None:
            return
        x, y = R_SPECIES.x + 10, R_SPECIES.y + 26
        row = snap.species_rows.get(m.species)
        if row is None:
            W.text(surface, "not read yet", (x, y), size=11, color=C.PLACEHOLDER)
            return
        W.text(
            surface,
            f"species 0x{row.species:02X} at 0x{row.address:08X}",
            (x, y),
            size=10,
            color=C.TEXT_FAINT,
        )
        y += ROW_H
        for cell in row.cells:
            if y + ROW_H > R_SPECIES.bottom - 6:
                break
            rect = pygame.Rect(x - 4, y, R_SPECIES.w - 12, ROW_H)
            self._rows.append((rect, cell))
            W.text(surface, cell_label(cell.field), (x, y), size=11, color=C.TEXT_DIM)
            value = cell.value
            if isinstance(value, tuple) and len(value) > FLOATS_PER_LINE:
                for k in range(0, len(value), FLOATS_PER_LINE):
                    y += ROW_H
                    part = value[k : k + FLOATS_PER_LINE]
                    W.text(surface, fmt_value(cell.field, part), (x + 12, y), size=10)
            else:
                text = fmt_value(cell.field, value)
                W.text(surface, text, (R_SPECIES.right - 10, y), size=11, align="right")
            y += ROW_H

    def _tooltip(self, surface: pygame.Surface) -> None:
        """The hovered cell's field doc from addresses.toml."""
        if self._mouse is None:
            return
        mx, my = self._mouse
        hit = next((c for r, c in self._rows if r.collidepoint(mx, my)), None)
        if hit is None:
            return
        lines = textwrap.wrap(hit.field.doc, 64) or [""]
        f = font(11, False)
        pad, line_h = 6, 14
        w = max(f.size(s)[0] for s in lines) + 2 * pad
        tip = pygame.Rect(int(mx) + 14, int(my) + 14, w, 2 * pad + line_h * len(lines))
        if tip.right > CANVAS_W - 4:
            tip.right = int(mx) - 6
        if tip.bottom > CANVAS_H - 4:
            tip.bottom = int(my) - 6
        veil = pygame.Surface(tip.size, pygame.SRCALPHA)
        veil.fill((10, 14, 22, 235))
        surface.blit(veil, tip.topleft)
        pygame.draw.rect(surface, C.ACCENT, tip, width=1, border_radius=4)
        for i, line in enumerate(lines):
            W.text(surface, line, (tip.x + pad, tip.y + pad + i * line_h), size=11)
