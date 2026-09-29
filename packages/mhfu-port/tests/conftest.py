# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

import pytest
from mhfu_port.data import Data


@pytest.fixture(scope="session")
def data(mhfu_data: Path, mhp3rd_data: Path) -> Data:
    """Both extracted games, from MHFU_DATA and MHP3RD_DATA."""
    return Data.find(mhfu_data, mhp3rd_data)
