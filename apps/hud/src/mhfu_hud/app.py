# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The pygame window: main loop, tab and layout switching, letterboxed scaling.

Layouts draw on a fixed body (CANVAS_W x CANVAS_H) under a status row; the window scales both to
fit and never shrinks below them.
"""

from __future__ import annotations

from pathlib import Path

import pygame

from . import panels
from . import widgets as W
from .assets import AssetLibrary
from .calibration import Calibration
from .layouts import AIModLayout, QuestLayout, QuestPrepLayout, VillageLayout
from .layouts.base import Layout, Reader, Writer
from .reader import QUEST_MAP
from .state import Context, GameSnapshot
from .theme import CANVAS_H, CANVAS_W, STATUS_H, C, font

WINDOW_TITLE = "MHFU Live HUD"
GAMEPLAY = {Context.VILLAGE, Context.QUEST}

# LIVE follows the game context, QUEST_PREP stages spawn edits, AI_MOD inspects the AI cells
TAB_LIVE = "live"
TAB_QUEST_PREP = "quest_prep"
TAB_AI_MOD = "ai_mod"
TABS = (TAB_LIVE, TAB_QUEST_PREP, TAB_AI_MOD)

_HELP = [
    ("F1", "toggle this help"),
    ("F2 / F3 / F4", "layout: auto / force village / force quest"),
    ("F11", "toggle fullscreen"),
    ("ESC", "close overlay, or quit"),
    ("TAB", "cycle tabs LIVE -> QUEST-PREP -> AI-MOD"),
    ("", ""),
    ("B", "toggle bag panel (quest)"),
    ("] / [", "select next / previous monster (quest)"),
    ("ENTER", "open / close monster detail page"),
    ("TAB", "in monster detail: cycle STATS -> AI"),
    ("M", "cycle map image (quest)"),
    ("", ""),
    ("arrows", "navigate quest-prep panels + rows"),
    ("+ / -", "adjust selected staged edit"),
    ("T", "staged edit kind: size / species / HP"),
    ("ENTER", "stage / toggle edit"),
    ("X / DEL", "remove staged edit"),
    ("M", "master switch for writing the edits"),
    ("", ""),
    ("] / [", "AI-MOD: next / previous monster"),
    ("up / down", "AI-MOD: scroll the cells"),
    ("", ""),
    ("C", "toggle calibration mode"),
    ("+  /  -", "calib: map scale"),
    (",  /  .", "calib: map rotation"),
    ("T", "calib: player-centered / fixed mode"),
    ("arrows", "calib: nudge map origin or village marker"),
    ("S", "calib: save calibration.json"),
]


class HUDApp:
    def __init__(
        self,
        reader: Reader,
        calib: Calibration | None = None,
        *,
        writer: Writer | None = None,
        assets: Path | None = None,
        fullscreen: bool = False,
    ) -> None:
        """`writer` None is `--read-only`: QUEST_PREP's edits are off."""
        self.reader = reader
        self.writer = writer
        # not pygame.init(): its joystick subsystem races PPSSPP for the gamepad on macOS
        pygame.display.init()
        pygame.font.init()
        pygame.display.set_caption(WINDOW_TITLE)
        self._windowed_size = (CANVAS_W, STATUS_H + CANVAS_H)
        self.fullscreen = fullscreen
        self.screen = self._make_window(self._windowed_size, fullscreen)
        self.canvas = pygame.Surface((CANVAS_W, STATUS_H + CANVAS_H)).convert()
        self.body = self.canvas.subsurface((0, STATUS_H, CANVAS_W, CANVAS_H))
        """What the layouts draw on."""
        self.clock = pygame.time.Clock()
        self.assets = AssetLibrary(assets)
        self.calib = calib or Calibration()
        self.layouts: dict[Context, Layout] = {
            Context.VILLAGE: VillageLayout(self.assets, self.calib),
            Context.QUEST: QuestLayout(self.assets, self.calib, reader=reader),
        }
        self.quest_prep = QuestPrepLayout(self.assets, self.calib, writer=writer)
        self.ai_mod = AIModLayout(self.assets, self.calib)
        self.forced: Context | None = None
        self.tab = TAB_LIVE
        self._tab_user_pinned = False
        self._last_quest_prep_eligible = False
        self.show_help = False
        self.running = True
        self._blit_off = (0, 0)
        self._blit_scale = 1.0

    # --- window ---

    def _make_window(self, size: tuple[int, int], fullscreen: bool) -> pygame.Surface:
        if fullscreen:
            return pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        w, h = max(CANVAS_W, size[0]), max(STATUS_H + CANVAS_H, size[1])
        return pygame.display.set_mode((w, h), pygame.RESIZABLE)

    def _toggle_fullscreen(self) -> None:
        self.fullscreen = not self.fullscreen
        self.screen = self._make_window(self._windowed_size, self.fullscreen)

    # --- loop ---

    def run(self) -> None:
        try:
            while self.running:
                snap = self.reader.snapshot
                self._absorb(snap)
                self._handle_events(snap)
                self._render(snap)
                self.clock.tick(60)
        finally:
            pygame.quit()

    def shoot(self, out: Path) -> list[Path]:
        """Render every tab once to `out/<tab>.png`."""
        out.mkdir(parents=True, exist_ok=True)
        snap = self.reader.snapshot
        paths = []
        for tab in TABS:
            self.tab = tab
            self._render(snap)
            path = out / f"{tab}.png"
            pygame.image.save(self.canvas, str(path))
            paths.append(path)
        return paths

    def _absorb(self, snap: GameSnapshot) -> None:
        """Keep the area_index pairs the reader learnt in the user's calibration."""
        if snap.learnt_sections and self.calib.learn(QUEST_MAP, snap.learnt_sections):
            self.calib.save()

    def _context(self, snap: GameSnapshot) -> Context:
        return self.forced if self.forced is not None else snap.context

    def _handle_events(self, snap: GameSnapshot) -> None:
        self._maybe_auto_switch_tab(snap)
        layout = self._active_layout(snap)
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                self.running = False
            elif ev.type == pygame.VIDEORESIZE and not self.fullscreen:
                self._windowed_size = (ev.w, ev.h)
                self.screen = self._make_window(self._windowed_size, False)
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                if layout is not None:
                    layout.handle_click(self._to_canvas(ev.pos), snap)
            elif ev.type == pygame.MOUSEMOTION:
                if layout is not None:
                    layout.handle_motion(self._to_canvas(ev.pos), snap)
            elif ev.type == pygame.KEYDOWN:
                self._handle_key(ev.key, snap, layout)

    def _quest_prep_eligible(self, snap: GameSnapshot) -> bool:
        """Outside a quest with an area index set: the engine prefetches the next quest's."""
        return snap.context != Context.QUEST and snap.area_index not in (0, -1)

    def _maybe_auto_switch_tab(self, snap: GameSnapshot) -> None:
        """Flip to QUEST_PREP when a quest is queued and back when it lapses, unless the user
        picked a tab; entering the quest clears that pick."""
        eligible = self._quest_prep_eligible(snap)
        if snap.context == Context.QUEST:
            self._tab_user_pinned = False
        if not self._tab_user_pinned:
            if eligible and not self._last_quest_prep_eligible:
                self.tab = TAB_QUEST_PREP
            elif not eligible and self._last_quest_prep_eligible:
                self.tab = TAB_LIVE
        self._last_quest_prep_eligible = eligible

    def _active_layout(self, snap: GameSnapshot) -> Layout | None:
        """The layout that takes keys and clicks: the tab's, or LIVE's for the context."""
        if self.tab == TAB_AI_MOD:
            return self.ai_mod
        if self.tab == TAB_QUEST_PREP:
            return self.quest_prep
        return self.layouts.get(self._context(snap))

    def _handle_key(self, key: int, snap: GameSnapshot, layout: Layout | None) -> None:
        if key == pygame.K_F1:
            self.show_help = not self.show_help
        elif key == pygame.K_F2:
            self.forced = None
        elif key == pygame.K_F3:
            self.forced = Context.VILLAGE
        elif key == pygame.K_F4:
            self.forced = Context.QUEST
        elif key == pygame.K_F11:
            self._toggle_fullscreen()
        elif self.show_help and key == pygame.K_ESCAPE:
            self.show_help = False
        # the layout sees TAB first: the quest detail page cycles its sub-pages with it
        elif layout is not None and layout.handle_key(key, snap):
            return
        elif key == pygame.K_TAB:
            i = TABS.index(self.tab) if self.tab in TABS else 0
            self.tab = TABS[(i + 1) % len(TABS)]
            self._tab_user_pinned = True
        elif key == pygame.K_ESCAPE:
            self.running = False

    # --- render ---

    def _render(self, snap: GameSnapshot) -> None:
        ctx = self._context(snap)
        if self.tab == TAB_AI_MOD:
            self.ai_mod.render(self.body, snap)
        elif self.tab == TAB_QUEST_PREP:
            self.quest_prep.render(self.body, snap)
        else:
            layout = self.layouts.get(ctx)
            if layout is not None and (
                self.forced is not None or (snap.connected and ctx in GAMEPLAY)
            ):
                layout.render(self.body, snap)
            else:
                panels.draw_status_screen(self.body, snap)
        self._status_row(snap, ctx)
        if self.show_help:
            self._draw_help()
        self._present()

    def _status_row(self, snap: GameSnapshot, ctx: Context) -> None:
        """Connection, context, tab and rates; a badge while edits would be written."""
        self.canvas.fill(C.BG, (0, 0, CANVAS_W, STATUS_H))
        mid = STATUS_H // 2
        pygame.draw.circle(self.canvas, C.OK if snap.connected else C.ERR, (14, mid), 5)
        tab = {TAB_AI_MOD: "[AI-MOD]", TAB_QUEST_PREP: "[PREP]"}.get(self.tab, "[LIVE]")
        parts = [ctx.value.upper(), tab]
        if self.forced is not None:
            parts.append("[FORCED]")
        parts += [f"{self.clock.get_fps():.0f}fps", f"poll {snap.poll_latency_ms:.0f}ms"]
        W.text(self.canvas, "  ·  ".join(parts), (26, mid - 8), size=12, color=C.TEXT_DIM)
        help_at = (CANVAS_W - 10, mid - 8)
        right = W.text(self.canvas, "F1 help", help_at, 12, C.TEXT_DIM, align="right")
        status = self.writer.status if self.writer else None
        if status is not None and status.active:
            n = sum(1 for st in status.staged if st.edit.enabled)
            label = f"WRITES ON · {n} edit{'s' * (n != 1)}"
            w, h = font(12, True).size(label)
            badge = pygame.Rect(0, 1, w + 12, STATUS_H - 2)
            badge.right = right.x - 14
            pygame.draw.rect(self.canvas, C.WARN, badge, border_radius=4)
            at = (badge.centerx, badge.y + (badge.h - h) // 2)
            W.text(self.canvas, label, at, 12, C.BG, bold=True, align="center", shadow=False)

    def _draw_help(self) -> None:
        veil = pygame.Surface((CANVAS_W, CANVAS_H), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 200))
        self.body.blit(veil, (0, 0))
        box = pygame.Rect(CANVAS_W // 2 - 300, 40, 600, 464)
        W.panel(self.body, box, fill=C.PANEL_HI, border=C.ACCENT)
        W.text(
            self.body,
            "MHFU LIVE HUD — CONTROLS",
            (box.x + 20, box.y + 16),
            size=18,
            color=C.ACCENT,
            bold=True,
        )
        y = box.y + 48
        for keys, desc in _HELP:
            if keys:
                W.text(self.body, keys, (box.x + 24, y), size=13, color=C.SELECT, bold=True)
                W.text(self.body, desc, (box.x + 180, y), size=13, color=C.TEXT)
            y += 14 if keys else 6
        W.text(
            self.body,
            "only QUEST-PREP's staged edits write game memory (none with --read-only)",
            (box.x + 20, box.bottom - 24),
            size=11,
            color=C.TEXT_FAINT,
        )

    def _present(self) -> None:
        ww, wh = self.screen.get_size()
        cw, ch = self.canvas.get_size()
        self._blit_scale = min(ww / cw, wh / ch)
        sw, sh = int(cw * self._blit_scale), int(ch * self._blit_scale)
        self._blit_off = ((ww - sw) // 2, (wh - sh) // 2)
        self.screen.fill((0, 0, 0))
        self.screen.blit(pygame.transform.smoothscale(self.canvas, (sw, sh)), self._blit_off)
        pygame.display.flip()

    def _to_canvas(self, pos: tuple[int, int]) -> tuple[float, float]:
        """A window position in the layouts' coordinates."""
        ox, oy = self._blit_off
        s = self._blit_scale or 1.0
        return (pos[0] - ox) / s, (pos[1] - oy) / s - STATUS_H
