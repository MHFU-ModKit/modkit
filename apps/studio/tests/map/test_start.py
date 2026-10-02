# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The map on the start page: its areas, choosing one, the next steps, finding the game."""

from typing import Any

import numpy as np
import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.core.edit import OBJECT, Selection
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell import places
from mhfu_studio.shell.workspace import SEND_KEY, Job


def test_areas_and_choose(game: Extracted, atlas: Atlas) -> None:
    ws = MapWorkspace(game, atlas)
    assert ws.shown() is None
    areas, mods = ws.start()
    assert [(c.label, c.key, c.detail) for c in areas.choices] == [
        ("Pokke village", "area:139:0", "st139"),
        ("Snowy base camp", "area:98:0", "st098"),
    ]
    assert mods.browse is not None and mods.browse[0] == "Open a map mod…"
    ws.choose("area:98:0")
    assert ws.scene is not None and ws.scene.stage == 98 and ws.shown() == (None, 98)
    with pytest.raises(ValueError, match="no area"):
        ws.choose("st098")


def test_choose_without_the_game(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MHFU_DATA", raising=False)
    ws = MapWorkspace()
    assert ws.start()[0].choices == () and "Setup" in ws.start()[0].note
    with pytest.raises(ValueError, match="no MHFU extraction found .choose it under Setup"):
        ws.choose("area:139:0")


def test_locate(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MHFU_DATA", raising=False)
    ws = MapWorkspace()
    assert ws.game is None and "start page" in ws.data_error
    (tmp_path / "g" / "data_files").mkdir(parents=True)
    places.remember(places.MHFU, tmp_path / "g")
    ws.locate()  # an extraction without the map table
    assert ws.game is None and "does not read as the game" in ws.data_error


def test_setup_shows_what_was_chosen(gl: Any, game: Extracted, atlas: Atlas) -> None:
    ws = MapWorkspace(game, atlas)
    ws.choose("area:98:0")
    ws.setup(gl)
    assert ws.vp is not None and ws.vp.scene is ws.scene and ws.scene is not None
    assert ws.scene.stage == 98
    ws.close()


def test_next_steps(game: Extracted, atlas: Atlas) -> None:
    ws = MapWorkspace(game, atlas)
    assert ws.next_steps() == ()
    ws.choose("area:139:0")
    assert [s.done for s in ws.next_steps()] == [False, False, False]
    assert [s.key for s in ws.next_steps()] == ["", "W", SEND_KEY]
    ws.tools.select(Selection(OBJECT, {(0, 0): np.array([0, 1, 2], np.int64)}, {}))
    assert [s.done for s in ws.next_steps()] == [True, False, False]
    ws.apply_numeric((0.0, 40.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    assert [s.done for s in ws.next_steps()] == [True, True, False]
    job = ws.send()
    ws.ended(Job("other", ()), True)
    assert not ws.next_steps()[2].done
    job = ws.send()
    ws.ended(job, True)
    assert ws.next_steps()[2].done
    ws.apply_numeric((0.0, 40.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    assert not ws.next_steps()[2].done  # a later edit is not in the game yet
