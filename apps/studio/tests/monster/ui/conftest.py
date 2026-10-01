# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Every Qt test: no question blocks, and the GL context comes back (root conftest)."""

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _ui(asked: list[Any], gl_back: None) -> None:
    """No question blocks; the GL context comes back after the test's windows close."""
