# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A map workspace on a viewport and a Studio over it; Qt tests never block on a question."""

from collections.abc import Iterator
from typing import Any

import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.studio import Studio


@pytest.fixture(autouse=True)
def _no_prompts(asked: list[Any]) -> None:
    pass


@pytest.fixture
def ws(gl: Any, game: Extracted, atlas: Atlas) -> Iterator[MapWorkspace]:
    w = MapWorkspace(game, atlas)
    w.setup(gl)
    assert w.vp is not None
    w.vp.resize((320, 200))
    yield w
    w.close()


@pytest.fixture
def studio(ws: MapWorkspace) -> Studio:
    return Studio([ws])
