# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Pytest plugin with the fixtures every modkit package's tests share."""

import os
from pathlib import Path

import pytest


def _game_data(var: str) -> Path:
    value = os.environ.get(var)
    if not value:
        pytest.skip(f"{var} is not set")
    path = Path(value).expanduser()
    if not path.is_dir():
        pytest.fail(f"{var}={value} is not a directory")
    return path


@pytest.fixture(scope="session")
def mhfu_data() -> Path:
    """Directory of extracted MHFU (ULES01213) DATA.BIN files, from MHFU_DATA."""
    return _game_data("MHFU_DATA")


@pytest.fixture(scope="session")
def mhp3rd_data() -> Path:
    """Directory of extracted MHP3rd DATA.BIN files, from MHP3RD_DATA."""
    return _game_data("MHP3RD_DATA")
