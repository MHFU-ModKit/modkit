# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from collections.abc import Callable
from pathlib import Path

import pytest
from mhfu_studio.shell import places, settings
from mhfu_studio.ui.window import Window

Make = Callable[..., Window]


def test_places_are_saved_settings(
    make_window: Make, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MHFU_DATA", raising=False)
    w = make_window(show=False)
    (tmp_path / "g" / "data_files").mkdir(parents=True)
    assert w.studio.remember(places.MHFU, tmp_path / "g")
    assert w.settings.value("places/mhfu") == str(tmp_path / "g")
    assert places.find(places.MHFU).source == "saved"
    settings.store.put("places/mhfu", None)
    assert w.settings.value("places/mhfu") is None
