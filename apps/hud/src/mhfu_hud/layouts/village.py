"""The LIVE tab in the village: the background art with the hunter's marker, vitals and raw
readouts. The marker's spot on the art is a calibration value (C, then arrows, [ ], S)."""

from __future__ import annotations

import math

import pygame

from .. import panels
from .. import widgets as W
from ..assets import AssetLibrary
from ..calibration import Calibration
from ..state import GameSnapshot, PlayerHUD
from ..theme import CANVAS_H, CANVAS_W, C
from .base import Layout


class VillageLayout(Layout):
    name = "village"

    def __init__(self, assets: AssetLibrary, calib: Calibration) -> None:
        super().__init__(assets, calib)
        self.calib_mode = False

    def handle_key(self, key: int, snapshot: GameSnapshot) -> bool:
        v = self.calib.village
        if key == pygame.K_c:
            self.calib_mode = not self.calib_mode
            return True
        if not self.calib_mode:
            return False
        step = 0.01
        if key == pygame.K_LEFT:
            v["nx"] = max(0.0, v["nx"] - step)
        elif key == pygame.K_RIGHT:
            v["nx"] = min(1.0, v["nx"] + step)
        elif key == pygame.K_UP:
            v["ny"] = max(0.0, v["ny"] - step)
        elif key == pygame.K_DOWN:
            v["ny"] = min(1.0, v["ny"] + step)
        elif key == pygame.K_LEFTBRACKET:
            v["facing_offset_deg"] -= 5
        elif key == pygame.K_RIGHTBRACKET:
            v["facing_offset_deg"] += 5
        elif key == pygame.K_s:
            self.calib.save()
        else:
            return False
        return True

    def render(self, surface: pygame.Surface, snapshot: GameSnapshot) -> None:
        full = pygame.Rect(0, 0, CANVAS_W, CANVAS_H)
        self.blit_cover(surface, self.assets.background("village"), full)
        self.dim(surface, full, 96)
        W.text(surface, "POKKE VILLAGE", (16, 8), size=14, color=C.ACCENT, bold=True)
        self._marker(surface, snapshot.player)
        panels.draw_vitals(surface, (12, 28, 300, 86), snapshot.player)
        self._time_panel(surface, (748, 28, 200, 86))
        self._info_panel(surface, (12, 392, 320, 140), snapshot)
        panels.draw_raw_strip(surface, (344, 476, 604, 56), snapshot)
        if self.calib_mode:
            self._calib_hint(surface)

    def _marker(self, surface: pygame.Surface, player: PlayerHUD) -> None:
        v = self.calib.village
        cx, cy = v["nx"] * CANVAS_W, v["ny"] * CANVAS_H
        heading = None
        if player.facing_rad is not None:
            heading = player.facing_rad + math.radians(v["facing_offset_deg"])
        glow = pygame.Surface((60, 60), pygame.SRCALPHA)
        pygame.draw.circle(glow, (96, 184, 236, 70), (30, 30), 26)
        surface.blit(glow, (cx - 30, cy - 30))
        W.marker(surface, (cx, cy), heading, C.PLAYER, radius=12)
        W.text(surface, "YOU", (cx, cy + 18), size=11, color=C.PLAYER, bold=True, align="center")

    def _time_panel(self, surface: pygame.Surface, rect: tuple[int, int, int, int]) -> None:
        W.panel(surface, rect, title="TIME")
        x, y, w, _ = rect
        W.placeholder_box(surface, (x + 12, y + 30, w - 24, 44), "no quest clock here")

    def _info_panel(
        self, surface: pygame.Surface, rect: tuple[int, int, int, int], snapshot: GameSnapshot
    ) -> None:
        W.panel(surface, rect, title="HUNTER")
        p = snapshot.player
        facing = "-" if p.facing_rad is None else f"{math.degrees(p.facing_rad):.0f}°"
        weapon = "-" if p.weapon_drawn is None else ("drawn" if p.weapon_drawn else "sheathed")
        rows = [
            ("World X/Z", f"{p.pos.x:.0f}, {p.pos.z:.0f}"),
            ("World Y", f"{p.pos.y:.0f}"),
            ("Facing", facing),
            ("Weapon", weapon),
            ("Sub-area", snapshot.map_subsection),
            ("Screen", snapshot.screen_state),
        ]
        W.kv_rows(surface, (rect[0] + 12, rect[1] + 28), rows, size=13, line_h=18, key_w=110)

    def _calib_hint(self, surface: pygame.Surface) -> None:
        box = pygame.Rect(CANVAS_W // 2 - 230, CANVAS_H - 70, 460, 40)
        W.panel(surface, box, fill=C.PANEL_HI, border=C.ACCENT)
        W.text(
            surface,
            "CALIBRATION — arrows: move marker   [ ]: heading   S: save   C: exit",
            box.inflate(-20, -16).topleft,
            size=12,
            color=C.ACCENT,
        )
