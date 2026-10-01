# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The real window: opt-in, it needs a display (on macOS a foreground process's main thread)."""

import os
from pathlib import Path

import pytest
from mhfu_studio.shell.app import Studio
from mhfu_studio.shell.testing import FakeWorkspace, run_window

pytestmark = pytest.mark.skipif(
    not os.environ.get("MHFU_UI_SMOKE"), reason="set MHFU_UI_SMOKE=1 to open a real window"
)


def test_window_draws_and_switches(tmp_path: Path) -> None:
    doc = tmp_path / "a.toml"
    doc.write_text("x y")
    studio = Studio(
        [FakeWorkspace("monster", ".pac"), FakeWorkspace("map", ".toml")],
        size=(900, 600),
        ini_folder=tmp_path,
    )
    assert studio.open(doc)

    def step(i: int) -> None:
        if i == 8:
            studio.switch("monster")

    got = run_window(studio, frames=16, step=step, out=tmp_path / "window.png")
    assert got.errors == [], got.errors
    assert got.frames >= 16 and studio.active.name == "monster"
    assert got.screen is not None, "the back buffer was never sampled"
    w, h = got.screen
    assert got.lit > w * h * 0.02, f"{got.lit} lit pixels of {w * h}: the window is black"
