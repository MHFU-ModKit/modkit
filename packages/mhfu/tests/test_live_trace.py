# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`script.trace` against a fake debugger: the log lines of the dispatch breakpoint, counted."""

import asyncio
import threading
import time

import pytest
from mhfu import addresses as a
from mhfu.live import Session, script
from mhfu.live.script import Step, Trace
from ppsspp_debug import DebuggerError, Hit, SyncStream
from ppsspp_debug.testing import FakePPSSPP

BASE = a.RAM.start + 0x10_0000
ENT = BASE + 0x4000
START = 1_000_000
PCS = [BASE + 0x100, BASE + 0x108, BASE + 0x110]


def line(t: int, pc: int, opcode: int) -> str:
    """What the dispatch breakpoint logs `t` microseconds after the trace began."""
    return f"BKP PC={a.AI_SCRIPT_DISPATCH:08x}: {START + t:08x} {pc:08x} {opcode:08x}"


class Emu:
    """A FakePPSSPP on its own loop thread, whose `usec` is `clock` and moves on `tick` a call."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.fake = FakePPSSPP(base=BASE, size=0x1000)
        self.run(self.fake.__aenter__())
        self.clock, self.tick, self.fail_at = START, 0, None
        self.calls = 0
        self.fake._table["cpu.evaluate"] = self._evaluate

    async def _evaluate(self, ws, msg):
        self.calls += 1
        if self.calls == self.fail_at:
            return await self.fake._fail(ws, msg, "Invalid expression")
        now, self.clock = self.clock, self.clock + self.tick
        await self.fake._reply(ws, msg, uintValue=now, floatValue="0")

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def feed(self, *lines: str) -> threading.Thread:
        """Log `lines` once the code has armed the dispatch breakpoint."""

        def go() -> None:
            until = time.monotonic() + 5
            while time.monotonic() < until:
                if a.AI_SCRIPT_DISPATCH in self.fake.breakpoints:
                    for text in lines:
                        self.run(self.fake.log(text, "JIT"))
                    return
                time.sleep(0.005)

        thread = threading.Thread(target=go, daemon=True)
        thread.start()
        return thread

    def close(self) -> None:
        self.run(self.fake.__aexit__(None, None, None))
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()
        self.loop.close()


@pytest.fixture
def emu(monkeypatch):
    monkeypatch.setattr(script, "DRAIN", 0.02)
    e = Emu()
    yield e
    e.close()


@pytest.fixture
def s(emu):
    with Session.attach(emu.fake.port, timeout=5) as live:
        yield live


def test_counts_by_pc(emu, s):
    feeding = emu.feed(
        line(100_000, PCS[0], 5),
        line(200_000, PCS[1], 0x39),
        line(300_000, PCS[0], 5),
        line(900_000, PCS[2], 0xFF),
        line(1_000_000, PCS[0], 5),  # the end of the window, still in it
        line(1_000_001, PCS[1], 5),  # past it
        line(1_200_000, PCS[1], 5),
    )
    got = script.trace(s, ENT, 1.0)
    feeding.join()
    assert got.counts == {PCS[0]: 3, PCS[1]: 1, PCS[2]: 1}
    assert got.steps[:2] == (Step(100_000, PCS[0], 5), Step(200_000, PCS[1], 0x39))
    assert [p.t for p in got.steps] == [100_000, 200_000, 300_000, 900_000, 1_000_000]
    assert [p.opcode for p in got.steps] == [5, 0x39, 5, 0xFF, 5]
    assert (got.entity, got.seconds, got.dropped) == (ENT, 1.0, 0)


def test_arms_the_dispatch_for_the_entity_and_removes_it(emu, s):
    seen = {}

    def spy():
        until = time.monotonic() + 5
        while time.monotonic() < until:
            if (bp := emu.fake.breakpoints.get(a.AI_SCRIPT_DISPATCH)) is not None:
                seen.update(bp)
                emu.run(emu.fake.log(line(2_000_000, PCS[0], 5), "JIT"))
                return
            time.sleep(0.005)

    thread = threading.Thread(target=spy, daemon=True)
    thread.start()
    script.trace(s, ENT, 1.0)
    thread.join()
    assert seen["condition"] == f"s3 == {ENT:#x}"
    assert seen["logFormat"] == "{usec} {s1} {v0}" == script.DISPATCH_FORMAT
    assert seen["log"] and not seen["enabled"]  # logs and never stops the game
    assert not emu.fake.breakpoints and not emu.fake.stepping


def test_a_quiet_monster_ends_by_the_clock(emu, s):
    emu.tick = 400_000
    got = script.trace(s, ENT, 1.0)
    assert got.steps == () and got.counts == {} and got.dropped == 0
    assert not emu.fake.breakpoints


def test_the_clock_wraps(emu, s):
    emu.clock = (1 << 32) - 100_000
    feeding = emu.feed(
        f"BKP PC={a.AI_SCRIPT_DISPATCH:08x}: {50_000:08x} {PCS[0]:08x} {5:08x}",
        f"BKP PC={a.AI_SCRIPT_DISPATCH:08x}: {2_000_000:08x} {PCS[0]:08x} {5:08x}",
    )
    got = script.trace(s, ENT, 1.0)
    feeding.join()
    assert got.steps == (Step(150_000, PCS[0], 5),)


def test_dropped_counts_unreadable_lines_and_the_streams_overflow(emu, s, monkeypatch):
    monkeypatch.setattr(SyncStream, "dropped", property(lambda self: 7))
    feeding = emu.feed(
        line(100_000, PCS[0], 5),
        f"BKP PC={a.AI_SCRIPT_DISPATCH:08x}: not a step",
        f"BKP PC={a.AI_SCRIPT_DISPATCH:08x}: 1 2",
        line(2_000_000, PCS[0], 5),
    )
    got = script.trace(s, ENT, 1.0)
    feeding.join()
    assert got.counts == {PCS[0]: 1} and got.dropped == 7 + 2


def test_removed_after_an_error_in_the_game(emu, s):
    emu.fail_at = 2  # the clock read of the first quiet wait
    with pytest.raises(DebuggerError):
        script.trace(s, ENT, 1.0)
    assert not emu.fake.breakpoints and not emu.fake.stepping


def test_a_game_that_does_not_run_times_out(emu, s, monkeypatch):
    now = iter(range(0, 1000, 100))
    monkeypatch.setattr(Session, "now", staticmethod(lambda: next(now)))
    with pytest.raises(TimeoutError, match="1 emulated s in 14 s"):
        script.trace(s, ENT, 1.0)
    assert not emu.fake.breakpoints


def test_step_reads_hex_and_ignores_the_rest():
    hit = Hit("exec", BASE, BASE, False, message=f"{1_000_000:08x} {PCS[0]:08x} 00000039")
    assert script._step(hit, 900_000) == Step(100_000, PCS[0], 0x39)
    assert script._step(Hit("exec", BASE, BASE, False, message="x y z"), 0) is None
    assert script._step(Hit("exec", BASE, BASE, False, message=None), 0) is None
    assert script._step(Hit("exec", BASE, BASE, False, message="1 2 3 4"), 0) is None


def test_counts():
    steps = (Step(1, 10, 5), Step(2, 12, 6), Step(3, 10, 5))
    assert Trace(ENT, 1.0, steps, 0).counts == {10: 2, 12: 1}
    assert Trace(ENT, 1.0, (), 0).counts == {}
