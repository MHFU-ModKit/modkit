# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The real window on the built Zinogre: opt-in, it needs a display (on macOS a foreground
process's main thread; from a background shell the window never gets a frame)."""

import os
import time
from pathlib import Path
from typing import Any

import pytest
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.app import Studio
from mhfu_studio.shell.testing import run_window

pytestmark = pytest.mark.skipif(
    not os.environ.get("MHFU_UI_SMOKE"), reason="set MHFU_UI_SMOKE=1 to open a real window"
)


def studio_on_zinogre(tmp_path: Path, games: Any, ports: Path, em75: Any) -> Studio:
    ws = MonsterWorkspace(games)
    ws.intel_cache[75] = em75
    studio = Studio([ws], size=(1000, 700), ini_folder=tmp_path)
    assert studio.open(ports / "zinogre.toml"), studio.message
    return studio


def test_window_draws_every_panel(tmp_path: Path, games: Any, ports: Path, em75: Any) -> None:
    studio = studio_on_zinogre(tmp_path, games, ports, em75)
    ws = studio.active
    assert isinstance(ws, MonsterWorkspace)

    def step(i: int) -> None:
        if i == 2:
            ws.show_parts = True
            ws.sync_hitboxes()
        elif i == 4:
            ws.select_part(1)
        elif i == 5:
            ws.show_host = True
            ws.sync_reference()
        elif i == 7:
            ws.show_attacks = True
            ws.select_pair(1, 4)
            ws.sync_attacks()
        elif i == 9:
            ws.select_set(2)

    got = run_window(studio, frames=16, step=step, out=tmp_path / "window.png")
    assert got.errors == [], got.errors
    assert got.screen is not None, "the back buffer was never sampled"
    w, h = got.screen
    assert got.lit > w * h * 0.02, f"{got.lit} lit pixels of {w * h}: the window is black"


def test_playback_holds_idling_off(tmp_path: Path, games: Any, ports: Path, em75: Any) -> None:
    """hello_imgui idles to 9 fps three seconds after the last input, which is right for a still
    pose and wrong for playback: measured past the idle window, not read off the flag."""
    from imgui_bundle import hello_imgui

    studio = studio_on_zinogre(tmp_path, games, ports, em75)
    ws = studio.active
    assert isinstance(ws, MonsterWorkspace) and ws.vp is None
    idling = hello_imgui.RunnerParams().fps_idling
    hold, idle_fps = idling.time_active_after_last_event, idling.fps_idle
    seen: dict[str, Any] = {"t0": None, "late": 0, "mark": None}

    def step(i: int) -> None:
        if ws.vp is None or ws.vp.actor is None:
            return
        now = time.perf_counter()
        if seen["t0"] is None:
            ws.vp.playback.loop = True
            ws.vp.playback.play()
            seen["t0"] = now
        elif now - seen["t0"] >= hold and seen["mark"] is None:
            seen["mark"] = now
        elif seen["mark"] is not None:
            seen["late"] += 1
            if now - seen["t0"] >= hold + 2.5:
                seen["elapsed"] = now - seen["mark"]
                seen["idling"] = bool(hello_imgui.get_runner_params().fps_idling.enable_idling)
                hello_imgui.get_runner_params().app_shall_exit = True

    got = run_window(studio, frames=100_000, step=step)
    assert got.errors == [], got.errors
    assert seen.get("idling") is False, "idling was left on while the transport played"
    assert seen["late"] / seen["elapsed"] > idle_fps * 2
