# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import asyncio
import threading

import pytest
from ppsspp_debug import Client
from ppsspp_debug.testing import FakePPSSPP

BASE = 0x1000_0000


async def test_trace_arms_and_removes(client, fake):
    points = [BASE, BASE + 0x10, BASE + 0x20]
    async with client.trace(points, condition="ra != 0", log_format="{ra}") as hits:
        assert sorted(fake.breakpoints) == points
        assert {b["condition"] for b in fake.breakpoints.values()} == {"ra != 0"}
        assert not any(b["enabled"] for b in fake.breakpoints.values())
        await fake.execute(BASE + 0x10)
        await fake.execute(BASE + 4)  # not traced
        await fake.execute(BASE)
        got = [await hits.next(1), await hits.next(1)]
    assert [(h.pc, h.message) for h in got] == [(BASE + 0x10, "{ra}"), (BASE, "{ra}")]
    assert not fake.breakpoints
    assert not fake.stepping


async def test_trace_logs_writes(client, fake):
    async with client.trace([BASE], writes=[(BASE + 0x200, 12)], log_format="{pc}") as hits:
        assert list(fake.watchpoints) == [(BASE + 0x200, 12)]
        assert not any(w["enabled"] or w["read"] for w in fake.watchpoints.values())
        await fake.access(BASE + 0x208, pc=BASE + 0x40)
        await fake.access(BASE + 0x20C, pc=BASE + 0x44)  # outside the span: no watchpoint
        await fake.execute(BASE)
        got = [await hits.next(1), await hits.next(1)]
    assert [(h.kind, h.address) for h in got] == [("memory", BASE + 0x208), ("exec", BASE)]
    assert not fake.watchpoints and not fake.breakpoints


async def test_trace_retranslates_after_arming(client, fake):
    # PPSSPP retranslates only the last breakpoint's address; a watchpoint change drops all code
    added = []
    original = fake._table["memory.breakpoint.add"]

    async def add(ws, msg):
        added.append((msg["address"], msg["size"], bool(fake.breakpoints)))
        await original(ws, msg)

    fake._table["memory.breakpoint.add"] = add
    points = [BASE, BASE + 0x40]
    async with client.trace(points, log_format="x"):
        assert not fake.watchpoints
    [(address, size, after_breakpoints)] = added
    assert address in points and size == 1 and after_breakpoints


async def test_trace_keeps_a_stopped_cpu_stopped(client, fake):
    await client.pause()
    async with client.trace([BASE], log_format="x"):
        assert fake.stepping
    assert fake.stepping and not fake.breakpoints


async def test_trace_removed_on_error(client, fake):
    with pytest.raises(TimeoutError):
        async with client.trace([BASE, BASE + 4], log_format="x") as hits:
            await hits.next(0.05)
    assert not fake.breakpoints


def test_blocking_trace():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    def run(coro):
        return asyncio.run_coroutine_threadsafe(coro, loop).result()

    fake = FakePPSSPP()
    run(fake.__aenter__())
    try:
        with Client.connect(port=fake.port) as c:
            with c.trace([BASE], log_format="a") as hits:
                run(fake.execute(BASE))
                assert hits.next(1).message == "a"
            assert not fake.breakpoints
    finally:
        run(fake.__aexit__(None, None, None))
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
