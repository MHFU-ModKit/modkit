# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import os
from collections.abc import AsyncIterator, Iterator

import pytest
from ppsspp_debug import AsyncClient, DockerEmulator, LocalEmulator
from ppsspp_debug.testing import FakePPSSPP


@pytest.fixture(params=["stock", "patched", "hit_events"])
async def fake(request: pytest.FixtureRequest) -> AsyncIterator[FakePPSSPP]:
    mode = request.param
    async with FakePPSSPP(patched=mode == "patched", hit_events=mode == "hit_events") as server:
        yield server


@pytest.fixture
async def client(fake: FakePPSSPP) -> AsyncIterator[AsyncClient]:
    async with AsyncClient.connect(port=fake.port, request_timeout=1) as c:
        yield c


@pytest.fixture(scope="session")
def emulator() -> Iterator[LocalEmulator | DockerEmulator]:
    """A real PPSSPP cold booting PPSSPP_GAME: PPSSPP_BINARY, or the rig's PPSSPP_CONTAINER."""
    game, binary = os.environ.get("PPSSPP_GAME"), os.environ.get("PPSSPP_BINARY")
    container = os.environ.get("PPSSPP_CONTAINER")
    if not (game and (binary or container)):
        pytest.skip("set PPSSPP_GAME and PPSSPP_BINARY or PPSSPP_CONTAINER for a real PPSSPP")
    if container:
        emu: LocalEmulator | DockerEmulator = DockerEmulator(container, game=game)
        emu.restart()
    else:
        assert binary
        emu = LocalEmulator(binary, game)
    with emu:
        yield emu
