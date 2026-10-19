# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`mhfu brain` against a fake PPSSPP that holds em75's native script region."""

import asyncio
import struct
import threading
import time

import pytest
from mhfu import addresses as a
from mhfu.cli import brain, main
from mhfu.em import script
from mhfu.em.script import inject
from mhfu.live import Launcher, Session
from mhfu.views import layout
from mhfu.writes import Plan, Pushed
from ppsspp_debug.testing import FakePPSSPP

BASE = a.RAM.start + 0x1BB_0000  # the fake's first byte, below SCRIPT_HEADERS
SIZE = int(a.CLI_BRIDGE_BLOCK) - BASE
ENT = BASE + 0x40000
PUSH = int(a.SCRIPT_PUSH)
ROW = 75
ROW_AT = int(a.SCRIPT_HEADERS) + ROW * int(a.SPECIES.stride)


def u32s(*words):
    return struct.pack(f"<{len(words)}I", *words)


class Emu:
    """A FakePPSSPP over BASE up, on its own loop thread, for the blocking client."""

    def __init__(self, size=SIZE):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.fake = FakePPSSPP(base=BASE, size=size)
        self.run(self.fake.__aenter__())

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def put(self, address, data):
        self.fake.memory[address - BASE : address - BASE + len(data)] = data

    def get(self, address, size):
        return bytes(self.fake.memory[address - BASE : address - BASE + size])

    def tick(self):
        """Trip SCRIPT_TICK as soon as the code arms it."""

        def trip():
            until = time.monotonic() + 5
            while time.monotonic() < until:
                if a.SCRIPT_TICK in self.fake.breakpoints:
                    self.run(self.fake.execute(a.SCRIPT_TICK))
                    return
                time.sleep(0.005)

        thread = threading.Thread(target=trip, daemon=True)
        thread.start()
        return thread

    def close(self):
        self.run(self.fake.__aexit__(None, None, None))
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()
        self.loop.close()


@pytest.fixture(autouse=True)
def no_launcher_env(monkeypatch):
    for name in ("MHFU_LAUNCHER", "MHFU_PPSSPP", "MHFU_ISO", "MHFU_CONTAINER", "MHFU_LANE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def emu(monkeypatch):
    e = Emu()
    launched = []

    def launch(launcher, **kw):
        launched.append((launcher, kw))
        return Session.attach(e.fake.port, timeout=5)

    monkeypatch.setattr(Session, "launch", launch)
    e.launched = launched
    yield e
    e.close()


@pytest.fixture(scope="module")
def em75(game):
    sc = script.Scripts(game, ROW)
    prog = script.lift.lift(sc, ROW)
    [header] = prog.headers()
    pcs = sorted(i.pc for r in sc.listing(header.va).routines.values() for i in r.insns)
    return sc, prog, header.va, pcs


def play(emu, em75, *, words=True, monster=True):
    """The game in a quest: em75's native region, the row's header words, a Tigrex running it."""
    sc, prog, va, pcs = em75
    emu.put(prog.base, sc.ovl.read(prog.base, prog.end - prog.base))
    if words:
        emu.put(ROW_AT, u32s(*[va] * 17))
    if monster:
        raw = bytearray(0x800)
        for field, values in (
            (a.ENTITY.SPECIES, (ROW,)),
            (a.ENTITY.SCRIPT_HEADER, (va,)),
            (a.ENTITY.SCRIPT_PCS, tuple(pcs[:4])),
            (a.ENTITY.SCRIPT_CONTEXT, (0x00,)),
        ):
            layout(field.type).pack_into(raw, field, *values)
        emu.put(ENT, bytes(raw))
        emu.put(int(a.ENTITY_REGISTRY), u32s(0, ENT))


def test_push_status_restore(emu, em75, game, capsys):
    play(emu, em75)
    data = ["--data", str(game.root), "--lane", "1"]
    assert main(["brain", "status", "--lane", "1"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "SCRIPT_PUSH: nothing pushed"
    assert out[1].startswith(f"slot  1  species  {ROW}  0x{ENT:08X}  native  context 0x00 pc 0x")

    ticking = emu.tick()
    assert main(["brain", "push", "75", *data]) == 0
    ticking.join()
    out = capsys.readouterr().out
    assert "stopped at SCRIPT_TICK" in out and "read back matches" in out
    assert f"entity 0x{ENT:08X} rebased" in out
    assert not emu.fake.stepping and not emu.fake.breakpoints

    assert main(["brain", "status", "--lane", "1"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert (
        out[0].startswith(f"SCRIPT_PUSH: row {ROW} id ")
        and f"image at 0x{PUSH + 0x40:08X}" in out[0]
    )
    assert out[1] == f"row {ROW} runs it on map types 0-16"
    assert "  pushed  " in out[2]

    ticking = emu.tick()
    assert main(["brain", "restore", "75", "--lane", "1"]) == 0
    ticking.join()
    assert "read back matches" in capsys.readouterr().out
    assert main(["brain", "status", "--lane", "1"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "SCRIPT_PUSH: nothing pushed" and "  native  " in out[1]
    assert {launcher for launcher, _ in emu.launched} == {Launcher(lane=1)}
    assert all(kw == {"stop_on_exit": False} for _, kw in emu.launched)


def test_push_a_sibling_row(emu, em75, game, capsys):
    play(emu, em75, monster=False)
    argv = ["brain", "push", "75", "--row", "76", "--data", str(game.root), "--lane", "1"]
    assert main(argv) == 1
    assert "none of row 76's header words" in capsys.readouterr().err


def test_push_outside_a_quest(emu, em75, game, capsys):
    play(emu, em75, words=False, monster=False)
    assert main(["brain", "push", "75", "--data", str(game.root)]) == 1
    assert "mhfu brain: none of row 75's header words" in capsys.readouterr().err
    assert emu.get(PUSH, 16) == bytes(16)


def test_restore_without_a_push(emu, em75, capsys):
    play(emu, em75)
    assert main(["brain", "restore", "75", "--timeout", "0.2"]) == 1
    assert "mhfu brain: nothing is pushed" in capsys.readouterr().err


def test_a_failed_read_back_is_a_failed_command(emu, em75, game, monkeypatch, capsys):
    play(emu, em75, monster=False)
    monkeypatch.setattr(inject, "push", lambda *_, **__: Pushed(Plan(()), 0.0, ["row 75 headers"]))
    assert main(["brain", "push", "75", "--data", str(game.root)]) == 1
    assert "read back DIFFERS at row 75 headers" in capsys.readouterr().out


def test_no_game(monkeypatch, capsys):
    def launch(launcher, **kw):
        raise TimeoutError("no game")

    monkeypatch.setattr(Session, "launch", launch)
    assert main(["brain", "status"]) == 1
    assert "mhfu brain: no game" in capsys.readouterr().err


def test_status_without_the_64_mb_mode(monkeypatch, capsys):
    short = Emu(size=PUSH - BASE)
    monkeypatch.setattr(
        Session, "launch", lambda *_, **__: Session.attach(short.fake.port, timeout=5)
    )
    try:
        assert main(["brain", "status"]) == 0
        assert "PPSSPP's 64 MB mode" in capsys.readouterr().out
    finally:
        short.close()


def test_spans():
    assert brain.spans([0, 1, 2, 5, 7, 8, 16]) == "0-2, 5, 7-8, 16"
    assert brain.spans([3]) == "3" and brain.spans([]) == ""


def test_lines():
    box = inject.Head(
        ROW, 0xA1B2C3D4, PUSH + 0x40, (0x40, 8), (0x48, 0), (0x48, 0), (0x48, 0), (0x48, 0)
    )
    monster = inject.Monster(2, ENT, ROW, True, 0x20, None)
    now = inject.Status(True, box, False, {ROW: (0, 1, 2, 9)}, (monster,))
    assert brain.lines(now) == [
        f"SCRIPT_PUSH: row {ROW} id A1B2C3D4, 72 bytes, image at 0x{PUSH + 0x40:08X}",
        f"row {ROW} runs it on map types 0-2, 9",
        f"slot  2  species  {ROW}  0x{ENT:08X}  pushed  context 0x20 pc -",
    ]
    assert brain.lines(inject.Status(True, None, True, {}, ())) == [
        "SCRIPT_PUSH: holds something that is no script container"
    ]
