# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A scripted game for the live flows: a FakePPSSPP whose memory tests change on presses and
reads, served from its own loop thread, and a clock that jumps on sleep."""

import asyncio
import struct
import threading
from collections.abc import Callable

import pytest
from mhfu import addresses as a
from mhfu.live import Session, session
from mhfu.memory import Image
from ppsspp_debug.testing import FakePPSSPP

BASE = a.RAM.start + 0x80_0000  # user memory; the kernel partition below it is never read
SIZE = 0x180_0000


class Scripted(FakePPSSPP):
    """A fake PPSSPP with `on_press(button)` and `on_read(address)` hooks that change memory."""

    def __init__(self) -> None:
        super().__init__(base=BASE, size=SIZE)
        self.presses: list[str] = []
        self.on_press: list[Callable[[str], None]] = []
        self.on_read: list[Callable[[int], None]] = []

    def poke(self, fmt: str, address: int, *values: object) -> None:
        struct.pack_into("<" + fmt, self.memory, address - self.base, *values)

    def peek(self, fmt: str, address: int) -> tuple:
        return struct.unpack_from("<" + fmt, self.memory, address - self.base)

    async def _press(self, ws, msg):
        self.presses.append(msg["button"])
        for hook in self.on_press:
            hook(msg["button"])
        await self._reply(ws, msg)

    async def _read(self, ws, msg):
        for hook in self.on_read:
            hook(msg["address"])
        await super()._read(ws, msg)


class Clock:
    """monotonic() and sleep() for the session module: sleeping only moves the time on."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def ram() -> Image:
    """The same span of memory as `fake`, as an image."""
    return Image(bytes(SIZE), BASE)


@pytest.fixture
def fake():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    server = Scripted()
    asyncio.run_coroutine_threadsafe(server.__aenter__(), loop).result()
    yield server
    asyncio.run_coroutine_threadsafe(server.__aexit__(None, None, None), loop).result()
    loop.call_soon_threadsafe(loop.stop)
    thread.join()
    loop.close()


@pytest.fixture
def clock(monkeypatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(session, "time", c)
    return c


@pytest.fixture
def s(fake, clock):
    with Session.attach(fake.port, timeout=5) as live:
        yield live
