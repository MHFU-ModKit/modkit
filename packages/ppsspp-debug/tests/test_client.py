# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import asyncio

import pytest
from ppsspp_debug import (
    AsyncClient,
    Breakpoint,
    DebuggerError,
    Disconnected,
    Hit,
    LogLine,
    Speed,
    Stepping,
    Unsupported,
    Watchpoint,
    _client,
)
from ppsspp_debug.testing import FakePPSSPP

BASE = 0x1000_0000

patched_only = pytest.mark.parametrize("fake", ["patched"], indirect=True)
hit_events_only = pytest.mark.parametrize("fake", ["hit_events"], indirect=True)


async def test_memory_round_trip(client):
    await client.write_u32(BASE, 0xDEADBEEF)
    assert await client.read_u32(BASE) == 0xDEADBEEF
    assert await client.read_u16(BASE + 2) == 0xDEAD
    assert await client.read(BASE, 2) == b"\xef\xbe"
    await client.write_f32(BASE + 4, 1.5)
    assert await client.read_f32(BASE + 4) == 1.5
    await client.write(BASE + 8, b"hi\0")
    assert await client.read_string(BASE + 8) == "hi"


async def test_error_reply_raises(client):
    with pytest.raises(DebuggerError, match="Invalid address"):
        await client.read_u32(0)


async def test_unknown_event_is_unsupported():
    async with FakePPSSPP() as stock, AsyncClient.connect(port=stock.port) as c:
        with pytest.raises(Unsupported):
            await c.speed()


@patched_only
async def test_patched_commands(client):
    assert await client.set_speed(200) == Speed(False, 200, 120)
    assert (await client.set_speed(fast_forward=True)).limit_fps == 0
    assert await client.set_speed() == await client.speed() == Speed(False, None, 60)
    await client.write_u8(BASE, 7)
    await client.save_state("/states/a.ppst")
    await client.write_u8(BASE, 0)
    await client.load_state("/states/a.ppst")
    assert await client.read_u8(BASE) == 7


@patched_only
async def test_save_screenshot(client, tmp_path):
    await client.save_screenshot(tmp_path / "a.png")
    assert (tmp_path / "a.png").read_bytes().startswith(b"\x89PNG")
    with pytest.raises(DebuggerError, match="write"):
        await client.save_screenshot(tmp_path / "missing" / "b.png")


async def test_save_screenshot_on_stock(tmp_path):
    async with FakePPSSPP() as stock, AsyncClient.connect(port=stock.port) as c:
        with pytest.raises(Unsupported):
            await c.save_screenshot(tmp_path / "a.png")


async def test_pause_and_resume(client, fake):
    assert (await client.pause()).pc == fake.pc
    assert await client.pause() == Stepping(fake.pc, fake.ticks, None, None, requested=True)
    await client.resume()
    assert not fake.stepping
    await client.resume()


async def test_paused_block(client, fake):
    async with client.paused():
        assert fake.stepping
    assert not fake.stepping
    await client.pause()
    async with client.paused():
        pass
    assert fake.stepping


async def test_requests_while_stopped(client, fake):
    await client.pause()
    fake.regs["a0"] = 5
    assert (await client.registers())["a0"] == 5
    await client.set_register("a1", 9)
    assert await client.register("a1") == 9
    await client.add_breakpoint(BASE)
    await client.remove_breakpoint(BASE)
    assert await client.read_u32(BASE) == 0


async def test_steps(client, fake):
    await client.pause()
    assert (await client.step_into()).pc == BASE + 4
    assert (await client.run_until(BASE + 0x40)).pc == BASE + 0x40


async def test_breakpoint_stops_and_is_removed(client, fake):
    async with client.breakpoint(BASE + 0x10) as hits:
        assert BASE + 0x10 in fake.breakpoints
        await fake.execute(BASE + 0x10)
        assert await hits.next(1) == Hit("exec", BASE + 0x10, BASE + 0x10, True, start=BASE + 0x10)
        assert (await client.status()).stepping
        await client.resume()
    assert not fake.breakpoints


async def test_watchpoint_stop(client, fake):
    async with client.watchpoint(BASE, 8) as hits:
        await fake.access(BASE + 4, 2, pc=BASE + 0x40)
        exact = Hit("memory", BASE + 4, BASE + 0x40, True, "write", 2, "CPU", start=BASE)
        assert await hits.next(1) == (
            exact if fake.hit_events else Hit("memory", BASE, BASE + 0x40, True, start=BASE)
        )
        assert fake.stepping
        await client.resume()


async def test_own_pause_is_not_a_hit(client, fake):
    async with client.watchpoint(BASE) as hits:
        await client.pause()
        with pytest.raises(TimeoutError):
            await hits.next(0.05)


async def test_breakpoint_stop_is_not_a_watchpoint_hit(client, fake):
    async with client.watchpoint(BASE + 0x100) as writes, client.breakpoint(BASE) as calls:
        await fake.execute(BASE)
        assert (await calls.next(1)).pc == BASE
        with pytest.raises(TimeoutError):
            await writes.next(0.05)


async def test_resume_answered_by_a_new_stop(client, fake):
    async def trip_again(ws, msg):
        await fake.stop()

    fake._table["cpu.resume"] = trip_again
    await client.pause()
    await client.resume()


async def test_held_log_lines_are_not_hits(client, fake):
    await fake.log(f"BKP PC={BASE:08x}: stale")
    async with client.breakpoint(BASE, stop=False) as hits:
        with pytest.raises(TimeoutError):
            await hits.next(0.1)


async def test_untaken_hit_does_not_leave_the_game_stopped(client, fake):
    async with client.breakpoint(BASE):
        await fake.execute(BASE)
    assert not fake.stepping
    async with client.breakpoint(BASE) as hits:
        await fake.execute(BASE)
        await hits.next(1)
    assert fake.stepping


async def test_watchpoint_removed_on_timeout(client, fake):
    with pytest.raises(TimeoutError):
        async with client.watchpoint(BASE, 4) as hits:
            await hits.next(0.05)
    assert not fake.watchpoints


async def test_other_stops_are_not_hits(client, fake):
    async with client.breakpoint(BASE) as hits:
        await fake.stop("cpu.breakpoint", BASE + 4)
        with pytest.raises(TimeoutError):
            await hits.next(0.05)


async def test_log_only_watchpoint(client, fake):
    async with client.watchpoint(BASE, 4, stop=False, log_format="{a0}") as hits:
        await fake.access(BASE + 2, 2, pc=BASE + 0x40)
        hit = await hits.next(1)
    assert hit == Hit("memory", BASE + 2, None, False, "write", 2, "CPU", "{a0}")
    assert not fake.stepping


async def test_log_only_breakpoint(client, fake):
    async with client.breakpoint(BASE, stop=False) as hits:
        await fake.execute(BASE)
        hit = await hits.next(1)
    assert (hit.pc, hit.stopped, hit.message) == (
        BASE,
        False,
        None if fake.hit_events else "z_un_test",
    )
    assert not fake.stepping


@hit_events_only
async def test_hit_events_need_no_log(client, fake):
    async with client.watchpoint(BASE, 8, stop=False) as hits:
        assert len(fake.connections) == 1 and not fake.disallowed["breakpoint"]
        await fake.access(BASE + 4, pc=BASE + 0x40)
        assert await hits.next(1) == Hit(
            "memory", BASE + 4, BASE + 0x40, False, "write", 4, "CPU", start=BASE
        )
    assert fake.disallowed["breakpoint"]


async def test_lists(client):
    await client.add_breakpoint(BASE, stop=False, log=True, condition="a0 == 1")
    await client.add_watchpoint(BASE, 2, change=True)
    assert await client.breakpoints() == [Breakpoint(BASE, False, True, "a0 == 1", None, None)]
    assert await client.watchpoints() == [
        Watchpoint(BASE, 2, False, True, True, True, False, None, None, None)
    ]


async def test_broadcasts_off_unless_streamed(client, fake):
    off = {"logger": True, "game": True, "input": True, "stepping": False}
    assert fake.disallowed == off | ({"breakpoint": True} if fake.hit_events else {})
    async with client.events("game.start") as events:
        assert not fake.disallowed["game"]
        await fake.broadcast({"event": "game.start", "game": None})
        assert (await events.next(1)).name == "game.start"
    assert fake.disallowed["game"]


async def test_log_has_its_own_connection(client, fake):
    async with client.events("log") as events:
        assert len(fake.connections) == 2
        await fake.log("hello")
        assert await events.next(1) == LogLine("00:00:000", "", "hello", 1, "MEMMAP")
    async with asyncio.timeout(1):
        while len(fake.connections) > 1:
            await asyncio.sleep(0.01)


async def test_log_survives_other_connections(client, fake):
    other = await AsyncClient.connect(port=fake.port)
    await other.close()
    async with client.events("log") as events:
        await fake.log("hello")
        assert (await events.next(1)).message == "hello"


async def test_stream_drops_oldest(client, fake, monkeypatch):
    monkeypatch.setattr(_client, "QUEUE_LIMIT", 3)
    async with client.events("log") as events:
        for i in range(5):
            await fake.log(str(i))
        await client.status()
        assert events.dropped == 2
        assert (await events.next(1)).message == "2"


async def test_input(client, fake):
    loop = asyncio.get_running_loop()
    start = loop.time()
    await client.press("cross", frames=6)
    assert loop.time() - start >= 0.09
    await client.hold("up", "ltrigger")
    await client.release()
    await client.analog(0.5, -1)
    with pytest.raises(ValueError):
        await client.analog(2, 0)
    sent = [m for m in fake.received if m["event"].startswith("input.")]
    assert sent[1]["buttons"] == {"up": True, "ltrigger": True}
    assert sent[2]["buttons"]["rtrigger"] is False


async def test_game(client, fake):
    fake.game = None
    asyncio.get_running_loop().call_later(
        0.3, setattr, fake, "game", {"id": "TEST00001", "version": "1.01", "title": "Later"}
    )
    assert (await client.wait_for_game(2)).id == "TEST00001"
    fake.speed = 120
    assert (await client.frame_stats()).speed == pytest.approx(2)
    assert (await client.screenshot()).png.startswith(b"\x89PNG")


async def test_disasm_and_evaluate(client):
    lines = await client.disasm(BASE, 3)
    assert [(i.address, str(i)) for i in lines] == [(BASE + 4 * n, "nop") for n in range(3)]
    assert await client.evaluate("0x10") == 16


async def test_request_reaches_any_event(client):
    assert (await client.request("cpu.status"))["stepping"] is False


async def test_connect_waits_out_a_slow_handshake():
    async with FakePPSSPP(handshake_delay=0.5) as slow:
        async with AsyncClient.connect(port=slow.port, timeout=5) as c:
            assert c.server == "PPSSPP v1.20.4-fake"


async def test_connect_retries_until_listening(unused_tcp_port):
    late = FakePPSSPP()
    late.port = unused_tcp_port

    async def start_later():
        await asyncio.sleep(0.5)
        await late.__aenter__()

    starting = asyncio.create_task(start_later())
    async with AsyncClient.connect(port=unused_tcp_port, timeout=5) as c:
        assert c.port == unused_tcp_port
    await starting
    await late.__aexit__(None, None, None)


async def test_connect_gives_up(unused_tcp_port):
    with pytest.raises(ConnectionError, match="RemoteDebuggerOnStartup"):
        await AsyncClient.connect(port=unused_tcp_port, timeout=0.3)


async def test_disconnect_fails_pending_requests():
    server = FakePPSSPP()
    async with server:
        c = await AsyncClient.connect(port=server.port)
        server.stepping = True
        pending = asyncio.create_task(c.frame_stats())
        await asyncio.sleep(0.05)
    with pytest.raises(Disconnected):
        await pending
    assert c.closed
