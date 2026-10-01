# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Fixtures every studio test shares; game data comes from modkit-testing (`mhfu_data`)."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

PORTS = Path(__file__).parents[3] / "ports"


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
