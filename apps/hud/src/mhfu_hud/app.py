"""The pygame window: main loop, tab and layout switching, letterboxed scaling.

Layouts draw on a fixed canvas (CANVAS_W x CANVAS_H) that the window scales to fit; the window
never shrinks below it.
"""

from __future__ import annotations

from pathlib import Path

import pygame

from . import panels
from . import widgets as W
from .assets import AssetLibrary
from .calibration import Calibration
from .layouts import AIModLayout, QuestLayout, QuestPrepLayout, VillageLayout
from .layouts.base import Layout, Reader
from .reader import QUEST_MAP
from .state import Context, GameSnapshot
from .theme import CANVAS_H, CANVAS_W, C

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
    ("+ / -", "live edit selected monster size (+-0.05)"),
    ("PgUp / PgDn", "live edit selected monster species id (+-1)"),
    ("ENTER", "open / close monster detail page"),
    ("TAB", "in monster detail: cycle STATS -> AI"),
    ("M", "cycle map image (quest)"),
    ("", ""),
    ("arrows", "navigate quest-prep panels + rows"),
    ("+ / -", "adjust selected staged edit"),
    ("ENTER", "stage / toggle edit"),
    ("X / DEL", "remove staged edit"),
    ("M", "master enable auto-apply"),
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
        assets: Path | None = None,
        fullscreen: bool = False,
    ) -> None:
        self.reader = reader
        # not pygame.init(): its joystick subsystem races PPSSPP for the gamepad on macOS
        pygame.display.init()
        pygame.font.init()
        pygame.display.set_caption(WINDOW_TITLE)
        self._windowed_size = (CANVAS_W, CANVAS_H)
        self.fullscreen = fullscreen
        self.screen = self._make_window(self._windowed_size, fullscreen)
        self.canvas = pygame.Surface((CANVAS_W, CANVAS_H)).convert()
        self.clock = pygame.time.Clock()
        self.assets = AssetLibrary(assets)
        self.calib = calib or Calibration()
        self.layouts: dict[Context, Layout] = {
            Context.VILLAGE: VillageLayout(self.assets, self.calib),
            Context.QUEST: QuestLayout(self.assets, self.calib, reader=reader),
        }
        self.quest_prep = QuestPrepLayout(self.assets, self.calib, reader=reader)
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
        w, h = max(CANVAS_W, size[0]), max(CANVAS_H, size[1])
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
            self.ai_mod.render(self.canvas, snap)
        elif self.tab == TAB_QUEST_PREP:
            self.quest_prep.render(self.canvas, snap)
        else:
            layout = self.layouts.get(ctx)
            if layout is not None and (
                self.forced is not None or (snap.connected and ctx in GAMEPLAY)
            ):
                layout.render(self.canvas, snap)
            else:
                panels.draw_status_screen(self.canvas, snap)
        self._status_pill(snap, ctx)
        if self.show_help:
            self._draw_help()
        self._present()

    def _status_pill(self, snap: GameSnapshot, ctx: Context) -> None:
        dot = C.OK if snap.connected else C.ERR
        pygame.draw.circle(self.canvas, dot, (CANVAS_W // 2 - 142, 9), 5)
        tab = {TAB_AI_MOD: "[AI-MOD]", TAB_QUEST_PREP: "[PREP]"}.get(self.tab, "[LIVE]")
        parts = [ctx.value.upper(), tab]
        if self.forced is not None:
            parts.append("[FORCED]")
        parts += [
            f"{self.clock.get_fps():.0f}fps",
            f"poll {snap.poll_latency_ms:.0f}ms",
            "F1 help",
        ]
        W.text(
            self.canvas,
            "  ·  ".join(parts),
            (CANVAS_W // 2 - 128, 2),
            size=12,
            color=C.TEXT_DIM,
        )

    def _draw_help(self) -> None:
        veil = pygame.Surface((CANVAS_W, CANVAS_H), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 200))
        self.canvas.blit(veil, (0, 0))
        box = pygame.Rect(CANVAS_W // 2 - 300, 40, 600, 464)
        W.panel(self.canvas, box, fill=C.PANEL_HI, border=C.ACCENT)
        W.text(
            self.canvas,
            "MHFU LIVE HUD — CONTROLS",
            (box.x + 20, box.y + 16),
            size=18,
            color=C.ACCENT,
            bold=True,
        )
        y = box.y + 48
        for keys, desc in _HELP:
            if keys:
                W.text(self.canvas, keys, (box.x + 24, y), size=13, color=C.SELECT, bold=True)
                W.text(self.canvas, desc, (box.x + 180, y), size=13, color=C.TEXT)
            y += 14 if keys else 6
        W.text(
            self.canvas,
            "QUEST-PREP edits and the LIVE size keys write game memory; the rest only reads",
            (box.x + 20, box.bottom - 24),
            size=11,
            color=C.TEXT_FAINT,
        )

    def _present(self) -> None:
        ww, wh = self.screen.get_size()
        self._blit_scale = min(ww / CANVAS_W, wh / CANVAS_H)
        sw, sh = int(CANVAS_W * self._blit_scale), int(CANVAS_H * self._blit_scale)
        self._blit_off = ((ww - sw) // 2, (wh - sh) // 2)
        self.screen.fill((0, 0, 0))
        self.screen.blit(pygame.transform.smoothscale(self.canvas, (sw, sh)), self._blit_off)
        pygame.display.flip()

    def _to_canvas(self, pos: tuple[int, int]) -> tuple[float, float]:
        ox, oy = self._blit_off
        s = self._blit_scale or 1.0
        return (pos[0] - ox) / s, (pos[1] - oy) / s
