# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Qt window's fixtures. Qt loads lazily: where it cannot, the root conftest skips `ui/`."""

import contextlib
import gc
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

with contextlib.suppress(ImportError):
    from mhfu_studio.ui.app import prepare

    prepare()  # before pytest-qt's QApplication


@pytest.fixture(autouse=True)
def _collect() -> Iterator[None]:
    """Frees the last test's widgets: `theme.apply` cannot skip a deleted one it bound."""
    yield
    gc.collect()


@pytest.fixture(autouse=True)
def asked(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every unsaved-edits question answers "discard", so a teardown never blocks; the
    questions and warnings land here."""
    from mhfu_studio.ui import dialogs

    got: list[Any] = []

    def confirm(parent: object, names: list[str]) -> str:
        got.append(list(names))
        return "discard"

    monkeypatch.setattr(dialogs, "confirm_unsaved", confirm)
    monkeypatch.setattr(dialogs, "warn", lambda *a: got.append(a[1:]))
    return got


@pytest.fixture
def make_window(qtbot: Any, tmp_path: Path) -> Callable[..., Any]:
    """`make_window(*workspaces)`: a shown `Window` over them (two fakes by default), with
    its settings in `tmp_path`."""
    from mhfu_studio.shell.studio import Studio
    from mhfu_studio.ui.testing import FakeWorkspace
    from mhfu_studio.ui.window import Window
    from PySide6.QtCore import QSettings

    def make(*workspaces: Any, show: bool = True) -> Window:
        found = list(workspaces) or [FakeWorkspace("map", ".toml"), FakeWorkspace("monster")]
        settings = QSettings(str(tmp_path / "studio.ini"), QSettings.Format.IniFormat)
        w = Window(Studio(found), settings)
        qtbot.addWidget(w)
        if show:
            w.resize(1000, 700)
            w.show()
            qtbot.waitExposed(w)
        return w

    return make
