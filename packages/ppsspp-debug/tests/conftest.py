import os
from collections.abc import AsyncIterator, Iterator

import pytest
from ppsspp_debug import AsyncClient, LocalEmulator
from ppsspp_debug.testing import FakePPSSPP


@pytest.fixture
async def fake() -> AsyncIterator[FakePPSSPP]:
    async with FakePPSSPP(patched=True) as server:
        yield server


@pytest.fixture
async def client(fake: FakePPSSPP) -> AsyncIterator[AsyncClient]:
    async with AsyncClient.connect(port=fake.port, request_timeout=1) as c:
        yield c


@pytest.fixture(scope="session")
def emulator() -> Iterator[LocalEmulator]:
    """A real PPSSPP booting PPSSPP_GAME, when PPSSPP_BINARY and PPSSPP_GAME are set."""
    binary, game = os.environ.get("PPSSPP_BINARY"), os.environ.get("PPSSPP_GAME")
    if not (binary and game):
        pytest.skip("set PPSSPP_BINARY and PPSSPP_GAME to run against a real PPSSPP")
    with LocalEmulator(binary, game) as emu:
        yield emu
