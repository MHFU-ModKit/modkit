from pathlib import Path

import pytest
from mhfu.files import Extracted


@pytest.fixture(scope="session")
def game(mhfu_data: Path) -> Extracted:
    """The extracted MHFU EU game MHFU_DATA points at."""
    return Extracted.find(mhfu_data)
