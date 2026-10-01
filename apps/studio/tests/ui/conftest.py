# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Qt tests never block on a question: see the root conftest's `asked`."""

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _no_prompts(asked: list[Any]) -> None:
    pass
