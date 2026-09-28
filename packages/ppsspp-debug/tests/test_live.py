"""Against a real PPSSPP; set PPSSPP_BINARY and PPSSPP_GAME. Run with -s to see the timings."""

import statistics
import time

import pytest
from ppsspp_debug import (
    AsyncClient,
    Client,
    DebuggerError,
    DockerEmulator,
    Unsupported,
    find_debuggers,
)
from websockets.sync.client import connect as raw_connect

USER_RAM = 0x0880_0000  # noaddr: the PSP's user memory, the same for every game


def report(text):
    print(f"\n[live] {text}", flush=True)


def ms(seconds):
    return f"{seconds * 1000:.1f} ms"


@pytest.fixture(scope="module")
def ppsspp(emulator):
    start = time.monotonic()
    port = emulator.port(60)
    listening = time.monotonic() - start
    with Client.connect(port=port, timeout=60) as c:
        connected = time.monotonic() - start
        game = c.wait_for_game(120)
        report(
            f"{c.server}: port {port} open after {listening:.2f}s, connected after "
            f"{connected:.2f}s, {game.id} booted after {time.monotonic() - start:.2f}s"
        )
        time.sleep(5)  # past the boot, into code that runs every frame
        yield c
        c.resume()


@pytest.fixture(scope="module")
def hot(ppsspp):
    """A word the game keeps writing, and the instruction writing it."""
    samples = []
    for _ in range(3):
        with ppsspp.watchpoint(USER_RAM, 0x40_0000, stop=False) as hits:
            samples.append({(h.pc, h.address) for h in (hits.next(10) for _ in range(300))})
        time.sleep(1)
    periodic = sorted(set.intersection(*samples))
    if not periodic:
        pytest.fail("no write repeats across three samples a second apart")
    report(f"{len(periodic)} writes repeat across three samples a second apart")
    return periodic[0]


@pytest.fixture(scope="module")
def fixed(ppsspp):
    """Whether this PPSSPP has the fixes the modkit patches into 1.20.4, told by game.speed.get."""
    try:
        ppsspp.speed()
    except Unsupported:
        return False
    return True


def test_finds_its_port(ppsspp, emulator):
    if isinstance(emulator, DockerEmulator):
        pytest.skip("the process runs in a container")
    assert emulator.port() in find_debuggers()


def test_no_subprotocol_needed(ppsspp):
    with raw_connect(f"ws://127.0.0.1:{ppsspp.port}/debugger", open_timeout=5) as ws:
        ws.send('{"event": "version", "ticket": 1, "name": "raw", "version": "0"}')
        assert '"PPSSPP"' in ws.recv(timeout=5)


def test_read_latency(ppsspp, hot):
    def median_read():
        times = []
        for _ in range(100):
            start = time.perf_counter()
            ppsspp.read_u32(hot[1])
            times.append(time.perf_counter() - start)
        return statistics.median(times)

    running = median_read()
    with ppsspp.paused():
        paused = median_read()
    start = time.perf_counter()
    ppsspp.read(USER_RAM, 0x40_0000)
    bulk = time.perf_counter() - start
    report(
        f"read_u32 median {ms(running)} while running, {ms(paused)} paused; "
        f"one 4 MiB read {ms(bulk)}"
    )


def test_pause_and_resume(ppsspp):
    start = time.perf_counter()
    ppsspp.pause()
    paused = time.perf_counter() - start
    assert ppsspp.status().stepping
    start = time.perf_counter()
    ppsspp.pause()
    again = time.perf_counter() - start
    start = time.perf_counter()
    ppsspp.resume()
    resumed = time.perf_counter() - start
    ticks = ppsspp.status().ticks
    time.sleep(0.2)
    assert ppsspp.status().ticks > ticks
    report(f"pause {ms(paused)}, pause while paused {ms(again)}, resume {ms(resumed)}")


def test_requests_while_stopped(ppsspp, hot):
    pc, address = hot
    with ppsspp.paused():
        start = time.perf_counter()
        regs = ppsspp.registers()
        ppsspp.read_u32(address)
        ppsspp.disasm(pc, 4)
        ppsspp.add_breakpoint(pc)
        ppsspp.remove_breakpoint(pc)
        took = time.perf_counter() - start
    assert len(regs) > 32
    report(f"registers, read, disasm, add and remove a breakpoint while stopped: {ms(took)}")


def test_breakpoint_stops_and_resumes(ppsspp, hot):
    start = time.perf_counter()
    with ppsspp.breakpoint(hot[0]) as hits:
        hit = hits.next(10)
        tripped = time.perf_counter() - start
        regs = ppsspp.registers()
        ppsspp.resume()
    assert hit.stopped and regs["pc"] == hot[0]
    assert not ppsspp.breakpoints()
    assert not ppsspp.status().stepping
    report(f"breakpoint armed to stopped {ms(tripped)}")


def test_watchpoint_stops_and_resumes(ppsspp, hot):
    with ppsspp.watchpoint(hot[1], 4) as hits:
        hit = hits.next(10)
        pc = ppsspp.registers()["pc"]
        stopped_at = ppsspp.disasm(pc)[0]
        ppsspp.resume()
    assert hit.stopped and not ppsspp.watchpoints()
    if pc != hit.pc:  # an access in a delay slot stops the CPU on its branch
        report(f"stopped at {pc:08x} {stopped_at}, the access at {hit.pc:08x}")
        assert hit.pc == pc + 4 and stopped_at.name[0] in "bj"


def test_stop_says_why(ppsspp, hot, fixed):
    with ppsspp.events("cpu.stepping") as stops, ppsspp.watchpoint(hot[1], 4) as hits:
        hits.next(10)
        stop = stops.next(1)
        ppsspp.resume()
    why = ("memory.breakpoint", hot[1]) if fixed else (None, None)
    assert (stop.reason, stop.related_address) == why


def test_log_reaches_every_connection(ppsspp, hot, fixed):
    with ppsspp.breakpoint(hot[0], stop=False) as calls:
        calls.next(10)
        with raw_connect(f"ws://127.0.0.1:{ppsspp.port}/debugger", open_timeout=5):
            pass
        if fixed:
            calls.next(5)
        else:  # stock: the closing connection took the log with it
            with pytest.raises(TimeoutError):
                calls.next(2)


def test_log_only_hits_keep_the_game_running(ppsspp, hot):
    with ppsspp.watchpoint(hot[1], 4, stop=False) as writes:
        start = time.perf_counter()
        seen = [writes.next(10) for _ in range(5)]
        took = time.perf_counter() - start
        assert not ppsspp.status().stepping
    assert {h.address for h in seen} == {hot[1]} and all(h.pc for h in seen)
    with ppsspp.breakpoint(hot[0], stop=False, log_format="a0={a0}") as calls:
        assert calls.next(10).message.startswith("a0=")
    report(f"log-only watchpoint: 5 hits in {ms(took)}, game kept running")


def test_armed_watchpoint_cost(ppsspp, hot):
    def speed():
        time.sleep(2)  # PPSSPP averages the rate, so let earlier stops age out
        return ppsspp.frame_stats().speed

    base = speed()
    with ppsspp.watchpoint(hot[0], 4):  # code is never written, so this never trips
        armed = speed()
    report(f"speed {base:.2f}x unarmed, {armed:.2f}x with one idle watchpoint armed")


def test_press_counts_frames(ppsspp):
    start = time.perf_counter()
    ppsspp.press("note", frames=30)
    report(f"press for 30 frames returned after {ms(time.perf_counter() - start)}")


def test_screenshot_during_play(ppsspp):
    try:
        shot = ppsspp.screenshot()
    except DebuggerError as e:
        report(f"screenshot during play: {e.message}")
        pytest.xfail(e.message)
    assert shot.png.startswith(b"\x89PNG")
    report(f"screenshot during play: {shot.width}x{shot.height}")


def test_screenshot_while_stopped(ppsspp):
    with ppsspp.paused():
        try:
            shot = ppsspp.screenshot()
        except DebuggerError as e:
            report(f"screenshot while stopped: {e.message}")
            pytest.xfail(e.message)
    assert shot.png.startswith(b"\x89PNG")
    report(f"screenshot while stopped: {shot.width}x{shot.height}, {len(shot.png)} bytes")


def test_speed(ppsspp):
    try:
        doubled = ppsspp.set_speed(200)
    except Unsupported:
        report("PPSSPP without game.speed.*: speed() raises Unsupported")
        return
    assert doubled.limit_fps == 120
    assert ppsspp.set_speed() == ppsspp.speed() and ppsspp.speed().limit_fps == 60


def test_savestate_round_trip(ppsspp, emulator, tmp_path):
    # a path as the emulator sees it
    state = "/tmp/live.ppst" if isinstance(emulator, DockerEmulator) else tmp_path / "live.ppst"
    with ppsspp.paused():
        before = ppsspp.read(USER_RAM, 4)
        start = time.perf_counter()
        try:
            ppsspp.save_state(state)
        except Unsupported:
            report("stock PPSSPP: savestate.* raise Unsupported")
            return
        saved = time.perf_counter() - start
        ppsspp.write(USER_RAM, bytes(b ^ 0xFF for b in before))
        start = time.perf_counter()
        ppsspp.load_state(state)
        loaded = time.perf_counter() - start
        assert ppsspp.read(USER_RAM, 4) == before
    report(f"savestate written in {ms(saved)}, loaded in {ms(loaded)}")


async def test_async_client_alongside(ppsspp, hot):
    async with AsyncClient.connect(port=ppsspp.port) as c:
        assert (await c.status()).ticks > 0
        assert await c.read(hot[1], 4)
