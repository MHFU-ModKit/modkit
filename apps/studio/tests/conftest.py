# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Fixtures every studio test shares; game data comes from modkit-testing (`mhfu_data`).

Qt tests live in directories named `ui/` and are not collected where Qt cannot load (CI has
neither a display nor Qt's system libraries) or pytest-qt is not installed (`--no-group qt`).
"""

import importlib.util
import os
import sys
from collections.abc import Iterator
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
    # a display where there is one (GL tests need it); widgets alone render offscreen
    if sys.platform != "darwin" and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
else:
    collect_ignore_glob = ["ui/*", "*/ui/*"]


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


@pytest.fixture
def imgui() -> Iterator[Any]:
    """`imgui_bundle` swapped for `shell.testing`'s scripted fake: panels without a window."""
    from mhfu_studio.shell.testing import fake_imgui

    with fake_imgui() as fake:
        yield fake
