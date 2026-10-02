"""The LIVE tab in a quest: the map with the monsters on it, vitals, timer, roster, bag. It
only reads; QUEST_PREP stages the edits.

[ ] cycle the selected monster, ENTER opens its detail page, C enters map calibration.
"""

from __future__ import annotations

import math

import pygame

from .. import item_db, panels
from .. import widgets as W
from ..assets import AssetLibrary
from ..calibration import Calibration
from ..panels import action_text, cell_label, fmt_value, state_text
from ..state import GameSnapshot, MonsterHUD, PlayerHUD
from ..theme import CANVAS_H, CANVAS_W, C
from .base import Layout, Reader

R_VITALS = pygame.Rect(12, 12, 300, 86)
R_PLAYER = pygame.Rect(318, 12, 424, 86)
R_TIMER = pygame.Rect(748, 12, 200, 86)
R_ROSTER = pygame.Rect(12, 110, 200, 278)
R_SHARP = pygame.Rect(12, 396, 200, 78)
R_MAP = pygame.Rect(224, 110, 510, 364)
R_DETAIL = pygame.Rect(746, 110, 202, 222)
R_BAG = pygame.Rect(746, 338, 202, 136)
R_RAW = pygame.Rect(12, 484, 936, 48)

MARKER_PX = 20
MARKER_RING_R = 12
PLAYER_MARKER_R = 9
ROSTER_ROW_H = 42

SHARPNESS_TIERS = (
    ("Red", (220, 64, 56)),
    ("Orange", (228, 132, 44)),
    ("Yellow", (232, 200, 72)),
    ("Green", (108, 196, 92)),
    ("Blue", (86, 156, 232)),
    ("White", (228, 232, 240)),
    ("Purple", (188, 116, 220)),
)
"""SHARPNESS_TIER 0..6: name and bar colour."""


class QuestLayout(Layout):
    name = "quest"

    DETAIL_SUBTABS = ("stats", "ai")

    def __init__(
        self, assets: AssetLibrary, calib: Calibration, reader: Reader | None = None
    ) -> None:
        super().__init__(assets, calib)
        self.reader = reader
        self.selected = 0
        self.detail_open = False
        self.detail_subtab = "stats"
        self.calib_mode = False
        self.bag_visible = True
        """With the bag hidden, DETAIL grows into its space."""
        self.map_slug = "snowy_mountains"
        self._maps = self.assets.map_slugs() or [self.map_slug]
        if self.map_slug not in self._maps:
            self._maps.insert(0, self.map_slug)
        self._marker_hits: list[tuple[pygame.Rect, int]] = []
        self._map_blit: tuple[pygame.Rect, float] | None = None
        """The map image's on-screen rect and scale, for native-pixel projection."""
        self._detail_rect = R_DETAIL
        self._bag_rect: pygame.Rect | None = R_BAG

    # --- input ---

    def handle_key(self, key: int, snapshot: GameSnapshot) -> bool:
        n = len(snapshot.monsters)
        # TAB cycles the detail sub-pages only while the detail page is open
        if key == pygame.K_TAB and self.detail_open:
            i = self.DETAIL_SUBTABS.index(self.detail_subtab)
            self.detail_subtab = self.DETAIL_SUBTABS[(i + 1) % len(self.DETAIL_SUBTABS)]
            return True
        if key in (pygame.K_RIGHTBRACKET, pygame.K_LEFTBRACKET):
            if n:
                step = 1 if key == pygame.K_RIGHTBRACKET else -1
                self.selected = (self.selected + step) % n
            return True
        if key == pygame.K_RETURN:
            self.detail_open = not self.detail_open
            return True
        if key == pygame.K_b:
            self.bag_visible = not self.bag_visible
            return True
        if key == pygame.K_c:
            self.calib_mode = not self.calib_mode
            return True
        if key == pygame.K_m:
            i = self._maps.index(self.map_slug) if self.map_slug in self._maps else -1
            self.map_slug = self._maps[(i + 1) % len(self._maps)]
            return True
        if self.calib_mode:
            return self._calib_key(key)
        return False

    def _calib_key(self, key: int) -> bool:
        q = self.calib.quest
        if key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
            q["scale"] *= 1.1
        elif key in (pygame.K_MINUS, pygame.K_KP_MINUS):
            q["scale"] /= 1.1
        elif key == pygame.K_COMMA:
            q["rot_deg"] -= 5
        elif key == pygame.K_PERIOD:
            q["rot_deg"] += 5
        elif key == pygame.K_t:
            q["mode"] = "fixed" if q["mode"] == "player_centered" else "player_centered"
        elif key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN):
            dx, dz = {
                pygame.K_LEFT: (-50, 0),
                pygame.K_RIGHT: (50, 0),
                pygame.K_UP: (0, -50),
                pygame.K_DOWN: (0, 50),
            }[key]
            q["origin_x"] += dx
            q["origin_z"] += dz
        elif key == pygame.K_h:
            q["facing_offset_deg"] = float(q.get("facing_offset_deg", 0.0)) - 5.0
        elif key == pygame.K_j:
            q["facing_offset_deg"] = float(q.get("facing_offset_deg", 0.0)) + 5.0
        elif key == pygame.K_f:
            q["facing_flip"] = not bool(q.get("facing_flip", False))
        elif key == pygame.K_s:
            self.calib.save()
        elif pygame.K_1 <= key <= pygame.K_8:
            if self.reader is not None:
                self.reader.set_section_override(key - pygame.K_0)
        elif key == pygame.K_0:
            if self.reader is not None:
                self.reader.reset_section_tracking()
        else:
            return False
        return True

    def handle_click(self, pos: tuple[float, float], snapshot: GameSnapshot) -> bool:
        for rect, idx in self._marker_hits:
            if rect.collidepoint(pos):
                self.selected = idx
                return True
        if R_ROSTER.collidepoint(pos):
            idx = int((pos[1] - (R_ROSTER.y + 26)) // ROSTER_ROW_H)
            if 0 <= idx < len(snapshot.monsters):
                self.selected = idx
                return True
        return False

    # --- render ---

    def render(self, surface: pygame.Surface, snapshot: GameSnapshot) -> None:
        surface.fill(C.BG_QUEST)
        monsters = snapshot.monsters
        self.selected = self.selected % len(monsters) if monsters else 0

        panels.draw_vitals(surface, R_VITALS, snapshot.player)
        self._player_panel(surface, snapshot)
        self._timer_panel(surface, snapshot)
        self._map_panel(surface, snapshot)
        self._roster_panel(surface, snapshot)
        self._sharpness_panel(surface, snapshot)
        if self.bag_visible:
            self._detail_rect, self._bag_rect = R_DETAIL, R_BAG
        else:
            self._detail_rect = pygame.Rect(
                R_DETAIL.x, R_DETAIL.y, R_DETAIL.w, R_BAG.bottom - R_DETAIL.y
            )
            self._bag_rect = None
        self._detail_panel(surface, snapshot)
        if self._bag_rect is not None:
            self._bag_panel(surface, snapshot, self._bag_rect)
        panels.draw_raw_strip(surface, R_RAW, snapshot)

        if self.detail_open and monsters:
            self._detail_overlay(surface, monsters[self.selected], snapshot.player)
        if self.calib_mode:
            self._calib_hint(surface)

    # --- panels ---

    def _player_panel(self, surface: pygame.Surface, snapshot: GameSnapshot) -> None:
        """The player's world position and heading, to check map anchors against."""
        W.panel(surface, R_PLAYER, title="PLAYER")
        p = snapshot.player
        x, y0 = R_PLAYER.x + 12, R_PLAYER.y + 26
        col, col2 = R_PLAYER.x + 156, R_PLAYER.x + 296
        if not p.loaded:
            W.text(surface, "not loaded", (x, y0 + 14), size=13, color=C.PLACEHOLDER)
            return
        heading = "  -" if p.facing_rad is None else f"{math.degrees(p.facing_rad) % 360:5.1f}°"
        W.stat_block(surface, (x, y0), "WORLD X", f"{p.pos.x:.0f}", size_value=20)
        W.stat_block(surface, (col, y0), "WORLD Z", f"{p.pos.z:.0f}", size_value=20)
        W.stat_block(surface, (col2, y0), "WORLD Y", f"{p.pos.y:.0f}", size_value=20)
        sub_y = y0 + 44
        W.text(surface, "HEADING", (x, sub_y), size=10, color=C.TEXT_DIM, bold=True)
        W.text(surface, heading, (x + 60, sub_y - 1), size=13, color=C.TEXT, bold=True)
        W.text(surface, "MAP SEC", (col, sub_y), size=10, color=C.TEXT_DIM, bold=True)
        sec = W.text(
            surface,
            panels.section_text(snapshot),
            (col + 60, sub_y - 1),
            size=13,
            color=C.TEXT,
            bold=True,
        )
        W.text(
            surface,
            f"({snapshot.tracked_section_source}, idx {snapshot.area_index})",
            (sec.right + 10, sub_y - 1),
            size=11,
            color=C.TEXT_FAINT,
        )

    def _timer_panel(self, surface: pygame.Surface, snapshot: GameSnapshot) -> None:
        W.panel(surface, R_TIMER, title="QUEST TIME")
        frames = snapshot.quest_timer_frames
        running = 0 < frames < 60 * 60 * 100
        W.text(
            surface,
            panels.fmt_timer(frames) if running else "--:--",
            (R_TIMER.centerx, R_TIMER.y + 28),
            size=34,
            color=C.TEXT if running else C.PLACEHOLDER,
            bold=True,
            align="center",
        )
        W.text(
            surface,
            f"{frames} frames",
            (R_TIMER.centerx, R_TIMER.y + 66),
            size=11,
            color=C.TEXT_FAINT,
            align="center",
        )

    def _sharpness_panel(self, surface: pygame.Surface, snapshot: GameSnapshot) -> None:
        W.panel(surface, R_SHARP, title="SHARPNESS")
        p = snapshot.player
        cur, mx, tier = p.sharpness, p.sharpness_max, p.sharpness_tier
        bar_rect = (R_SHARP.x + 12, R_SHARP.y + 32, R_SHARP.w - 24, 18)
        if cur is None or mx is None or mx <= 0:
            W.placeholder_box(surface, bar_rect, "")
            W.text(
                surface,
                "not loaded" if not p.loaded else "no live read",
                (R_SHARP.centerx, R_SHARP.y + 56),
                size=11,
                color=C.PLACEHOLDER,
                align="center",
            )
            return
        name, color = SHARPNESS_TIERS[tier] if tier is not None else ("?", SHARPNESS_TIERS[2][1])
        W.bar(surface, bar_rect, cur / mx, color)
        W.text(surface, name, (R_SHARP.x + 14, R_SHARP.y + 54), size=14, color=color, bold=True)
        W.text(
            surface,
            f"{cur} / {mx}",
            (R_SHARP.right - 14, R_SHARP.y + 54),
            size=14,
            color=C.TEXT,
            bold=True,
            align="right",
        )

    def _map_panel(self, surface: pygame.Surface, snapshot: GameSnapshot) -> None:
        W.panel(surface, R_MAP, fill=C.BG)
        W.text(
            surface,
            f"MAP — {self.map_slug.replace('_', ' ').title()}",
            (R_MAP.x + 10, R_MAP.y + 6),
            size=12,
            color=C.ACCENT,
            bold=True,
        )
        sec = snapshot.tracked_section
        if sec is not None:
            n = len(self.calib.section_anchors(self.map_slug, sec))
            mode = {0: "no anchors", 1: "1-anchor", 2: "2-anchor"}.get(n, "affine")
            status = f"sec {sec} ({snapshot.tracked_section_source})  {mode}"
        else:
            q = self.calib.quest
            status = f"sec {panels.section_text(snapshot)}  ({q['mode']}  x{q['scale']:.3f})"
        W.text(
            surface,
            status,
            (R_MAP.right - 10, R_MAP.y + 6),
            size=11,
            color=C.TEXT_FAINT,
            align="right",
        )

        img_box = pygame.Rect(R_MAP.x + 6, R_MAP.y + 24, R_MAP.w - 12, R_MAP.h - 30)
        img = self.assets.map_image(self.map_slug)
        self._map_blit = None
        scaled = None
        if img is not None:
            scale = W.fit_scale(img.get_size(), img_box.size)
            scaled = self.assets.scaled(img, (img.get_width() * scale, img.get_height() * scale))
        if img is not None and scaled is not None:
            blit_rect = scaled.get_rect(center=img_box.center)
            surface.blit(scaled, blit_rect)
            self._map_blit = (blit_rect, W.fit_scale(img.get_size(), img_box.size))
        else:
            W.placeholder_box(surface, img_box, "no map image in the assets folder")
        self._draw_entities(surface, img_box, snapshot)

    def _project(
        self, box: pygame.Rect, x: float, z: float, player: PlayerHUD, section: int | None
    ) -> tuple[float, float]:
        """A world x/z on screen: through the section's own anchors (sections have their own
        world frames) when the section and the image are known, else player-centred."""
        if self._map_blit and section is not None:
            img_xy = self.calib.world_to_image_section(self.map_slug, section, x, z)
            if img_xy is not None:
                rect, scale = self._map_blit
                return rect.left + img_xy[0] * scale, rect.top + img_xy[1] * scale
        return self.calib.world_to_map(box, x, z, player.pos.x, player.pos.z)

    def _draw_entities(self, surface: pygame.Surface, box: pygame.Rect, snap: GameSnapshot) -> None:
        self._marker_hits = []
        prev_clip = surface.get_clip()
        surface.set_clip(box)
        player, section = snap.player, snap.tracked_section

        def clamp(p: tuple[float, float]) -> tuple[float, float]:
            return (
                max(box.left + 4, min(box.right - 4, p[0])),
                max(box.top + 4, min(box.bottom - 4, p[1])),
            )

        hit_r = MARKER_RING_R + 6
        for idx, m in enumerate(snap.monsters):
            mx, my = clamp(self._project(box, m.pos.x, m.pos.z, player, section))
            self._blit_monster(surface, m, (mx, my), idx == self.selected)
            hit = pygame.Rect(mx - hit_r, my - hit_r, 2 * hit_r, 2 * hit_r)
            self._marker_hits.append((hit, idx))

        ppx, ppy = clamp(self._project(box, player.pos.x, player.pos.z, player, section))
        W.marker(
            surface, (ppx, ppy), self._player_heading(player), C.PLAYER, radius=PLAYER_MARKER_R
        )
        surface.set_clip(prev_clip)
        if not snap.monsters:
            W.text(
                surface,
                "no monsters loaded",
                box.center,
                size=14,
                color=C.TEXT_FAINT,
                align="center",
            )

    def _player_heading(self, player: PlayerHUD) -> float | None:
        """The marker's heading with the calibration's offset and flip; None draws a dot."""
        if player.facing_rad is None:
            return None
        q = self.calib.quest
        h = -player.facing_rad if q.get("facing_flip") else player.facing_rad
        return h + math.radians(float(q.get("facing_offset_deg", 0.0)))

    def _blit_monster(
        self, surface: pygame.Surface, m: MonsterHUD, center: tuple[float, float], selected: bool
    ) -> None:
        cx, cy = int(center[0]), int(center[1])
        grow = 1.6 if m.big else 1.0  # big monsters dominate the map, as on the in-game one
        r = int(MARKER_RING_R * grow)
        icon_px = int(MARKER_PX * grow)
        ring = C.SELECT if selected else (C.WARN if m.big else C.MONSTER)
        pygame.draw.circle(surface, (0, 0, 0, 120), (cx, cy), r)
        pygame.draw.circle(surface, ring, (cx, cy), r, width=2)
        icon = self.assets.scaled(self.assets.monster_icon(m.icon_slug), (icon_px, icon_px))
        if icon is not None:
            surface.blit(icon, icon.get_rect(center=(cx, cy)))
        else:
            pygame.draw.circle(surface, C.MONSTER_DIM, (cx, cy), r - 2)
            W.text(
                surface, m.name[:1], (cx, cy - 7), size=14, color=C.TEXT, bold=True, align="center"
            )
        if selected or m.big:
            W.text(
                surface,
                m.name,
                (cx, cy - r - 10),
                size=11,
                color=C.SELECT if selected else C.WARN,
                bold=True,
                align="center",
            )

    def _roster_panel(self, surface: pygame.Surface, snapshot: GameSnapshot) -> None:
        W.panel(surface, R_ROSTER, title="MONSTERS")
        monsters = snapshot.monsters
        if not monsters:
            W.text(
                surface,
                "none loaded",
                (R_ROSTER.x + 12, R_ROSTER.y + 34),
                size=13,
                color=C.TEXT_FAINT,
            )
            return
        # big first; `selected` keeps indexing snapshot.monsters so clicks stay right
        groups = (
            ("BIG", [i for i, m in enumerate(monsters) if m.big]),
            ("SMALL", [i for i, m in enumerate(monsters) if not m.big]),
        )
        y = R_ROSTER.y + 26
        for label, group in groups:
            if not group:
                continue
            W.text(surface, label, (R_ROSTER.x + 12, y - 2), size=10, color=C.ACCENT, bold=True)
            y += 12
            for i in group:
                m = monsters[i]
                if y + ROSTER_ROW_H > R_ROSTER.bottom:
                    break
                row = pygame.Rect(R_ROSTER.x + 6, y, R_ROSTER.w - 12, ROSTER_ROW_H - 4)
                if i == self.selected:
                    pygame.draw.rect(surface, C.PANEL_HI, row, border_radius=4)
                    pygame.draw.rect(surface, C.SELECT, row, width=1, border_radius=4)
                icon = self.assets.scaled(self.assets.monster_icon(m.icon_slug), (30, 30))
                if icon is not None:
                    surface.blit(icon, (row.x + 4, row.y + 4))
                W.text(surface, m.name, (row.x + 40, row.y + 4), size=13, color=C.TEXT, bold=True)
                size = "?" if m.render_scale is None else f"{m.render_scale:.2f}x"
                W.text(
                    surface,
                    f"slot {m.slot}  ·  size {size}",
                    (row.x + 40, row.y + 21),
                    size=10,
                    color=C.TEXT_FAINT,
                )
                W.bar(
                    surface,
                    (row.x + 40, row.y + 33, row.w - 46, 4),
                    m.hp / max(1, m.hp_max),
                    C.MONSTER,
                )
                W.text(surface, str(m.hp), (row.right - 6, row.y + 4), size=12, align="right")
                y += ROSTER_ROW_H
            y += 4

    def _bag_panel(
        self, surface: pygame.Surface, snapshot: GameSnapshot, rect: pygame.Rect
    ) -> None:
        """PLAYER_BAG as a 6x4 grid: known items by icon, the rest by id, each with its count."""
        W.panel(surface, rect, title="BAG")
        W.text(
            surface,
            f"poll #{snapshot.poll_count}",
            (rect.right - 8, rect.y + 6),
            size=10,
            color=C.TEXT_FAINT,
            align="right",
        )
        bag = snapshot.player.bag
        cols, rows, pad = 6, 4, 4
        slot_w = (rect.w - (cols + 1) * pad) // cols
        slot_h = (rect.h - 22 - (rows + 1) * pad) // rows
        prev = surface.get_clip()
        for i in range(cols * rows):
            x = rect.x + pad + (i % cols) * (slot_w + pad)
            y = rect.y + 22 + pad + (i // cols) * (slot_h + pad)
            cell = pygame.Rect(x, y, slot_w, slot_h)
            slot = bag[i] if i < len(bag) else None
            if slot is None or slot.empty:
                pygame.draw.rect(surface, C.PANEL, cell, border_radius=3)
                pygame.draw.rect(surface, C.PANEL_HI, cell, width=1, border_radius=3)
                continue
            pygame.draw.rect(surface, C.PANEL_HI, cell, border_radius=3)
            pygame.draw.rect(surface, C.ACCENT, cell, width=1, border_radius=3)
            surface.set_clip(cell.clip(prev))  # nothing spills into a neighbour
            _name, slug = item_db.identify(slot.item_id)
            size = min(slot_w - 4, slot_h - 4)
            icon = self.assets.scaled(self.assets.item_icon(slug), (size, size))
            if icon is not None:
                surface.blit(icon, icon.get_rect(center=cell.center))
            else:
                W.text(
                    surface,
                    f"{slot.item_id:X}",
                    (cell.centerx, cell.y + 1),
                    size=9,
                    color=C.TEXT_DIM,
                    align="center",
                )
            at = (cell.right - 2, cell.bottom - 12)
            W.text(surface, f"x{slot.count}", at, 9, C.SELECT, bold=True, align="right")
            surface.set_clip(prev)

    def _detail_panel(self, surface: pygame.Surface, snapshot: GameSnapshot) -> None:
        rect = self._detail_rect
        W.panel(surface, rect, title="DETAIL")
        monsters = snapshot.monsters
        if not monsters:
            W.text(
                surface, "select a monster", (rect.x + 12, rect.y + 34), size=12, color=C.TEXT_FAINT
            )
            W.text(
                surface,
                "[ ] to cycle  ·  B toggles bag",
                (rect.x + 12, rect.y + 52),
                size=11,
                color=C.TEXT_FAINT,
            )
            return
        m = monsters[self.selected]
        x = rect.x + 12
        icon_rect = pygame.Rect(x, rect.y + 28, 44, 44)
        icon = self.assets.scaled(self.assets.monster_icon(m.icon_slug), icon_rect.size)
        if icon is not None:
            surface.blit(icon, icon_rect)
        else:
            W.placeholder_box(surface, icon_rect, "")
        prev = surface.get_clip()
        surface.set_clip(rect.inflate(-8, -4).clip(prev))  # long values stop at the border
        right = icon_rect.right + 10
        W.text(surface, m.name, (right, rect.y + 28), size=14, color=C.SELECT, bold=True)
        bar = pygame.Rect(right, rect.y + 52, rect.right - 10 - right, 14)
        W.bar(surface, bar, m.hp / max(1, m.hp_max), C.MONSTER)
        W.text(surface, f"HP {m.hp}", (bar.centerx, bar.y), size=10, bold=True, align="center")
        dist = self._distance(m, snapshot.player)
        size = "?" if m.render_scale is None else f"{m.render_scale:.3f}x"
        rows = [
            ("Slot", m.slot),
            ("Species", f"0x{m.species:02X}"),
            ("Size", size),
            ("Animating", action_text(m)),
            ("Move", state_text(m)),
            ("Dist", "-" if dist is None else f"{dist:.0f}"),
            ("Pointer", f"0x{m.ptr:08X}"),
            ("Entity ID", f"0x{m.entity_id:02X}"),
            ("World X", f"{m.pos.x:.0f}"),
            ("World Z", f"{m.pos.z:.0f}"),
        ]
        top, line_h, footer = rect.y + 82, 16, rect.bottom - 18
        fit = max(0, (footer - top) // line_h)
        W.kv_rows(surface, (x, top), rows[:fit], size=11, line_h=line_h, key_w=84)
        surface.set_clip(prev)
        W.text(
            surface,
            "ENTER details  ·  B bag",
            (rect.centerx, footer),
            size=10,
            color=C.TEXT_FAINT,
            align="center",
        )

    # --- overlays ---

    def _detail_overlay(self, surface: pygame.Surface, m: MonsterHUD, player: PlayerHUD) -> None:
        self.dim(surface, pygame.Rect(0, 0, CANVAS_W, CANVAS_H), 170)
        box = pygame.Rect(CANVAS_W // 2 - 280, CANVAS_H // 2 - 200, 560, 400)
        W.panel(surface, box, fill=C.PANEL_HI, border=C.SELECT)
        W.text(
            surface,
            f"{m.name}  —  {self.detail_subtab.upper()}",
            (box.x + 20, box.y + 14),
            size=18,
            color=C.SELECT,
            bold=True,
        )
        chip_x = box.x + 20
        for tab in self.DETAIL_SUBTABS:
            active = tab == self.detail_subtab
            chip = W.text(
                surface,
                tab.upper(),
                (chip_x, box.y + 40),
                size=10,
                color=C.SELECT if active else C.TEXT_DIM,
                bold=active,
            )
            chip_x = chip.right + 18
        W.text(
            surface,
            "TAB switch  ·  ENTER/ESC close",
            (box.right - 20, box.bottom - 24),
            size=11,
            color=C.TEXT_DIM,
            align="right",
        )
        if self.detail_subtab == "stats":
            self._detail_stats(surface, box, m, player)
        else:
            self._detail_ai(surface, box, m)

    def _detail_stats(
        self, surface: pygame.Surface, box: pygame.Rect, m: MonsterHUD, player: PlayerHUD
    ) -> None:
        icon = self.assets.scaled(self.assets.monster_icon(m.icon_slug), (140, 140))
        if icon is not None:
            surface.blit(icon, (box.x + 24, box.y + 60))
        else:
            W.placeholder_box(surface, (box.x + 24, box.y + 60, 140, 140), "no icon")
        hp_max = max(1, m.hp_max)
        W.bar(surface, (box.x + 24, box.y + 214, 140, 16), m.hp / hp_max, C.MONSTER)
        W.text(surface, f"HP  {m.hp} / {hp_max}", (box.x + 24, box.y + 236), size=12)
        dist = self._distance(m, player)
        size = "?" if m.render_scale is None else f"{m.render_scale:.4f}"
        rows = [
            ("Name", m.name),
            ("Registry slot", m.slot),
            ("Entity", f"0x{m.ptr:08X}"),
            ("Vtable", f"0x{m.vtable:08X}"),
            ("Species", f"0x{m.species:02X}"),
            ("Entity ID", f"0x{m.entity_id:02X}"),
            ("Render scale", size),
            ("Animating", action_text(m)),
            ("Move (main, sub)", state_text(m)),
            ("Position", f"{m.pos.x:.1f}, {m.pos.y:.1f}, {m.pos.z:.1f}"),
            ("Distance", "-" if dist is None else f"{dist:.0f} units"),
        ]
        W.kv_rows(surface, (box.x + 190, box.y + 62), rows, size=13, line_h=24, key_w=150)

    def _detail_ai(self, surface: pygame.Surface, box: pygame.Rect, m: MonsterHUD) -> None:
        """The AI cells in two columns; AI_MOD lists them with their docs."""
        line_h, key_w = 18, 160
        per_col = (box.h - 110) // line_h
        cols = (box.x + 24, box.x + 290)
        for i, cell in enumerate(m.cells[: 2 * per_col]):
            x = cols[i // per_col]
            y = box.y + 62 + (i % per_col) * line_h
            W.text(surface, cell_label(cell.field), (x, y), size=11, color=C.TEXT_DIM)
            W.text(surface, fmt_value(cell.field, cell.value)[:14], (x + key_w, y), size=11)

    def _calib_hint(self, surface: pygame.Surface) -> None:
        box = pygame.Rect(CANVAS_W // 2 - 320, CANVAS_H - 116, 640, 76)
        W.panel(surface, box, fill=C.PANEL_HI, border=C.ACCENT)
        W.text(
            surface, "MAP CALIBRATION", (box.x + 16, box.y + 8), size=12, color=C.ACCENT, bold=True
        )
        W.text(
            surface,
            "+/-: scale   , .: rotate   T: mode   arrows: origin (fixed)",
            (box.x + 16, box.y + 26),
            size=12,
        )
        q = self.calib.quest
        flip = "ON" if q.get("facing_flip") else "off"
        W.text(
            surface,
            f"H/J: heading offset ({q.get('facing_offset_deg', 0):.0f}°)  F: flip ({flip})   "
            "1-8: set section   0: re-snap   S: save   C: exit",
            (box.x + 16, box.y + 46),
            size=12,
        )

    # --- helpers ---

    @staticmethod
    def _distance(m: MonsterHUD, player: PlayerHUD) -> float | None:
        if not player.loaded:
            return None
        return math.hypot(m.pos.x - player.pos.x, m.pos.z - player.pos.z)
