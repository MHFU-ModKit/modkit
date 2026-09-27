import asyncio
import threading

import pytest
from ppsspp_debug import AsyncClient, Client
from ppsspp_debug.testing import FakePPSSPP

BASE = 0x1000_0000


class Served:
    """A FakePPSSPP on its own loop thread, so a blocking client can talk to it."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.fake = FakePPSSPP()
        self.run(self.fake.__aenter__())

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def close(self) -> None:
        self.run(self.fake.__aexit__(None, None, None))
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()
        self.loop.close()


@pytest.fixture
def served():
    s = Served()
    yield s
    s.close()


def test_round_trip(served):
    with Client.connect(port=served.fake.port) as c:
        c.write_u32(BASE, 42)
        assert c.read_u32(BASE) == 42
        assert c.server == "PPSSPP v1.20.4-fake"
    assert c.closed


def test_breakpoint_hits(served):
    with Client.connect(port=served.fake.port) as c:
        with c.breakpoint(BASE) as hits:
            served.run(served.fake.execute(BASE))
            assert hits.next(1).stopped
            c.resume()
        assert not served.fake.breakpoints


def test_events_iterate(served):
    with Client.connect(port=served.fake.port) as c, c.events("log") as events:
        served.run(served.fake.log("x"))
        assert next(events).message == "x"


def test_call_from_its_own_loop_raises(served):
    with Client.connect(port=served.fake.port) as c:

        async def inside():
            c.status()

        with pytest.raises(RuntimeError, match="deadlock"):
            c._loop.run(inside())


def test_every_async_method_is_mirrored():
    public = {n for n, v in vars(AsyncClient).items() if not n.startswith("_") and callable(v)}
    assert public <= set(vars(Client))
