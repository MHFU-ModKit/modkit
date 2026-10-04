# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Every tab, context, overlay and sub-page draws from a synthetic snapshot."""

import pygame
import pytest
from mhfu_hud.app import TAB_AI_MOD, TAB_LIVE, TABS
from mhfu_hud.edits import Edit, Kind
from mhfu_hud.panels import section_text
from mhfu_hud.state import Context
from mhfu_hud.theme import CANVAS_W, STATUS_H, C

CONTEXTS = ["disconnected", "boot", "menu", "loading", "village", "camp", "quest"]


def warn_in_status_row(app) -> bool:
    return any(
        app.canvas.get_at((x, y))[:3] == C.WARN
        for x in range(CANVAS_W // 2, CANVAS_W)
        for y in range(STATUS_H)
    )


@pytest.mark.parametrize("name", CONTEXTS)
def test_tabs(app, reader, snaps, name, tmp_path):
    reader.snapshot = snaps[name]
    paths = app.shoot(tmp_path / name)
    assert [p.stem for p in paths] == list(TABS)
    for p in paths:
        assert pygame.image.load(str(p)).get_size() == app.canvas.get_size()


@pytest.mark.parametrize("name", CONTEXTS)
def test_forced(app, snaps, name):
    for key in (pygame.K_F3, pygame.K_F4, pygame.K_F2):
        app._handle_key(key, snaps[name], None)
        app._render(snaps[name])


def test_overlays(app, snaps):
    quest = snaps["quest"]
    q = app.layouts[Context.QUEST]
    app.tab = TAB_LIVE
    for sub in q.DETAIL_SUBTABS:
        q.detail_open, q.detail_subtab = True, sub
        app._render(quest)
    q.detail_open = False
    for bag in (True, False):
        q.bag_visible = bag
        app._render(quest)
    q.calib_mode = True
    app._render(quest)
    app.show_help = True
    app._render(quest)


def test_ai_mod(app, snaps):
    quest = snaps["quest"]
    app.tab = TAB_AI_MOD
    for key in (pygame.K_DOWN, pygame.K_PAGEDOWN, pygame.K_RIGHTBRACKET, pygame.K_HOME):
        assert app.ai_mod.handle_key(key, quest)
    app.ai_mod.handle_motion((300, 160), quest)  # over a cell: its doc as a tooltip
    app._render(quest)


def test_calibration_keys(app, reader, snaps):
    quest = snaps["quest"]
    q = app.layouts[Context.QUEST]
    q.calib_mode = True
    for key in (pygame.K_3, pygame.K_0, pygame.K_EQUALS, pygame.K_COMMA, pygame.K_t, pygame.K_f):
        assert q.handle_key(key, quest)
    assert reader.sections == [3, None]


def test_badge(app, writer, snaps):
    app._render(snaps["quest"])
    assert not warn_in_status_row(app)
    writer.stage(Edit.of(Kind.SIZE, 0x4B))
    app._render(snaps["quest"])
    assert warn_in_status_row(app)
    writer.toggle()
    app._render(snaps["quest"])
    assert not warn_in_status_row(app)


def test_section_text(snaps):
    assert [section_text(snaps[n]) for n in ("quest", "camp", "disconnected")] == ["1", "camp", "?"]
