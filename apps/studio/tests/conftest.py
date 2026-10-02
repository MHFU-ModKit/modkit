# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Fixtures every studio test shares; game data comes from modkit-testing (`mhfu_data`).

Qt tests live in directories named `ui/` and are not collected where Qt cannot load (CI has
neither a display nor Qt's system libraries) or pytest-qt is not installed (`--no-group qt`).
"""

import importlib.util
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

PORTS = Path(__file__).parents[3] / "ports"


def _qt_loads() -> bool:
    if importlib.util.find_spec("pytestqt") is None:
        return False
    try:
        import PySide6.QtWidgets  # noqa: F401
    except ImportError:
        return False
    return True


if _qt_loads():
    # no windows on screen unless asked: offscreen draws every widget, but has no GL, so the
    # few tests of a real GL window skip; MHFU_UI_DISPLAY=1 runs them on the display
    if not os.environ.get("MHFU_UI_DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_QPA_OFFSCREEN_NO_GLX", "1")  # else it takes GLX from $DISPLAY
    from mhfu_studio.ui.app import prepare

    prepare()  # GL 3.3 core for every QOpenGLWidget: before pytest-qt makes the QApplication
else:
    collect_ignore_glob = ["ui/*", "*/ui/*"]


@pytest.fixture(autouse=True)
def _places(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No saved setting, guess or memory-stick variable of the user's reaches a test."""
    from mhfu import inject
    from mhfu_studio.shell import places, settings

    monkeypatch.setattr(settings, "store", settings.Memory())
    monkeypatch.setattr(places, "GUESS_FROM", tmp_path / "guess")
    monkeypatch.delenv(inject.MEMSTICK_ENV, raising=False)


@pytest.fixture(scope="session")
def gl() -> Iterator[Any]:
    """A headless moderngl context, or a skip that says why there is none."""
    try:
        from mhfu_studio.shell.context import ContextError, headless
    except ImportError as e:
        pytest.skip(f"no GL layer: {e}")
    try:
        ctx = headless()
    except ContextError as e:
        pytest.skip(str(e))
    yield ctx
    ctx.release()


@pytest.fixture(scope="session")
def ports() -> Path:
    """modkit's ports/ directory of port manifests."""
    return PORTS


# ---- Qt (use from a `ui/` directory, where Qt is known to load) ----


@pytest.fixture
def gl_back() -> Iterator[None]:
    """After a test that showed a GL view, the context current before it is current again."""
    from mhfu_studio.shell.context import borrowed

    with borrowed():
        yield


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every unsaved-edits question answers "discard" (Revert: yes), so a teardown never
    blocks; the questions and warnings land here."""
    from mhfu_studio.ui import dialogs

    got: list[Any] = []

    def confirm(parent: object, names: list[str]) -> str:
        got.append(list(names))
        return "discard"

    monkeypatch.setattr(dialogs, "confirm_unsaved", confirm)
    monkeypatch.setattr(dialogs, "confirm_revert", lambda parent, name: got.append(name) or True)
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
