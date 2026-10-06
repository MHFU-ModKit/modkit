# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import json
import struct
from types import SimpleNamespace

import pytest
from mhfu import addresses as a
from mhfu.live import observe as ob
from mhfu.memory import Image
from mhfu.mips import Code
from ppsspp_debug import Hit

CODE = a.RAM.start + 0x50_0000  # the overlay's text, static only
VT = a.RAM.start + 0x10_0000  # its species vtable, in the stub eboot
ENT = a.RAM.start + 0x90_0000  # the monster, in `fake`
OUT1, OUT2, OUT3 = a.RAM.start + 0x6_0000, a.RAM.start + 0x6_0100, a.RAM.start + 0x6_0200


def jal(target: int) -> int:
    return (3 << 26) | ((target >> 2) & 0x3FF_FFFF)


def j(target: int) -> int:
    return (2 << 26) | ((target >> 2) & 0x3FF_FFFF)


WORDS = [jal(OUT2), 0, jal(OUT1), 0, jal(OUT2), 0, jal(CODE + 0x40), 0, j(OUT3), 0]
WORDS += [0] * (32 - len(WORDS))  # the AI step at CODE + 0x40: nops, as the fake disassembles


def engine() -> SimpleNamespace:
    text = Image(struct.pack(f"<{len(WORDS)}I", *WORDS), CODE)
    eboot = Image(bytes(0x100), VT)
    eboot.write_u32(VT + a.MONSTER_VTABLE.AI_STEP, CODE + 0x40)
    owner = SimpleNamespace(vtable=SimpleNamespace(va=VT))
    code = Code(text, range(CODE, CODE + 4 * len(WORDS)))
    return SimpleNamespace(owners={75: owner}, codes={75: code}, eboot=eboot)


def hit(callee: int, usec: int, ra: int, main: int, sub: int, *args: int) -> Hit:
    """A hit logged in `log_format`: a0..a3, then the register of a register call."""
    words = [usec, ra, main | sub << 8, *args, *[0] * (4 - len(args))]
    return Hit("exec", callee, callee, False, message=" ".join(f"{w:08x}" for w in words))


def bkp(callee: int, *fields: int) -> str:
    """The log line a breakpoint on `callee` writes in `log_format`."""
    return f"BKP PC={callee:08x}: {hit(callee, *fields).message}"


def snap(t: int, main: int, sub: int, **at: int) -> ob.Snapshot:
    data = bytearray(ob.SNAPSHOT_SIZE)
    data[a.ENTITY.MAIN_STATE], data[a.ENTITY.SUB_STATE] = main, sub
    for offset, value in at.items():
        data[int(offset[1:], 16)] = value
    return ob.Snapshot(t, bytes(data))


def test_outbound():
    code = engine().codes[75]
    assert ob.outbound(code) == {OUT2: (CODE, CODE + 0x10), OUT1: (CODE + 8,)}


def test_parse():
    call = ob.parse(hit(OUT1, 0x1000_0010, CODE + 0x10, 1, 4, ENT, 17, 0), 0x1000_0000)
    assert call == ob.Call(OUT1, CODE + 8, (1, 4), 0x10, (ENT, 17, 0, 0))
    assert ob.parse(hit(OUT1, 5, CODE, 0, 0), 0xFFFF_FFFF).t == 6  # the clock wraps
    assert ob.parse(Hit("exec", OUT1, OUT1, False, message="z_un_test"), 0) is None
    register = ob.parse(hit(CODE + 0x18, 9, 0, 1, 4, ENT, 0, 6, 1, OUT3), 0)
    assert (register.callee, register.site, register.args) == (OUT3, CODE + 0x18, (ENT, 0, 6, 1))


def test_parse_write():
    line = Hit("memory", ENT + 0x27E, None, False, message=f"00000010 {OUT1:08x} 00000401")
    w = ob.parse_write(line, 0, ENT, lambda pc: pc - 8)
    assert w == ob.Write(0x27E, OUT1, OUT1 - 8, (1, 4), 0x10)
    assert ob.parse_write(Hit("memory", ENT, None, False, message="x"), 0, ENT) is None


def test_indirect():
    words = [0x0320F809, 0, 0x03200008, 0, 0x03E00008, 0]  # jalr t9; jr t9; jr ra
    text = Image(struct.pack("<6I", *words), CODE)
    assert ob.indirect(Code(text, range(CODE, CODE + 24))) == {CODE: "t9", CODE + 8: "t9"}


def test_field_name():
    assert ob.field_name(a.ENTITY.POSITION) == "POSITION"
    assert ob.field_name(a.ENTITY.POSITION + 8) == "POSITION+0x8"
    assert ob.field_name(0x27C) == "+0x27C"


def test_writes_report():
    r = ob.Run(ENT, ())
    r.writes = [ob.Write(0x200, OUT1 + 4, OUT1, (1, 4), 1)] * 2
    r.writes.append(ob.Write(0x27C, OUT2, None, (0, 6), 2))
    text = ob.writes_report([r])
    assert f"(1,4) writes\n  POSITION: {OUT1:08X} - @{OUT1 + 4:08X} x2" in text
    assert f"(0,6) writes\n  +0x27C: ? @{OUT2:08X} x1" in text


def test_diff_names_fields_and_words():
    before = snap(0, 0, 2, x1D5=1, x1D8=7)
    after = snap(1, 1, 4, x1D5=0, x1D8=9, x090=3)
    got = ob.diff(before.data, after.data, churn={0x090})
    assert [(c.name, c.before, c.after, c.churn) for c in got] == [
        ("+0x090", 0, 3, True),
        ("PHASE", 1, 0, False),
        ("+0x1D8", 7, 9, False),
        ("MAIN_STATE", 0, 1, False),
        ("SUB_STATE", 2, 4, False),
    ]


def test_pair_changes_mark_churn():
    snaps = [snap(0, 0, 2, x090=1), snap(1, 0, 2, x090=2), snap(2, 1, 4, x090=3, x414=60)]
    [change] = ob.pair_changes(snaps)
    assert (change.before.pair, change.after.pair) == ((0, 2), (1, 4))
    churning = {c.name for c in change.changes if c.churn}
    stable = {c.name for c in change.changes if not c.churn}
    assert churning == {"+0x090"}
    assert stable == {"MAIN_STATE", "SUB_STATE", "ACTION_BUDGET"}
    assert "(0,2) -> (1,4)" in ob.changes_report([change])


def run(targets, calls, transitions, pairs=((0, 2), (0, 2))) -> ob.Run:
    r = ob.Run(ENT, tuple(targets))
    r.snapshots = [snap(0, *pairs[0]), snap(3_000_000, *pairs[1])]
    r.calls = [ob.parse(h, 0) for h in calls]
    r.transitions = [ob.Transition.of(ob.parse(h, 0)) for h in transitions]
    r.seconds = 3.0
    return r


def test_by_pair_counts_per_entry_of_the_runs_that_traced():
    marks = [
        hit(a.SET_AI_STATE, 1_000_000, 0, 0, 2, ENT, 1, 4),
        hit(a.SET_AI_STATE, 2_000_000, 0, 1, 4, ENT, 0, 2),
    ]
    first = run([OUT1], [hit(OUT1, 1_500_000, CODE + 8, 1, 4, ENT, 17, 0)] * 3, marks)
    second = run([OUT2], [hit(OUT2, 1_500_000, CODE + 0x10, 1, 4)], marks)
    assert first.timeline() == [
        ((0, 2), 0, 1_000_000),
        ((1, 4), 1_000_000, 2_000_000),
        ((0, 2), 2_000_000, 3_000_000),
    ]
    pairs = {p.pair: p for p in ob.by_pair([first, second])}
    assert (pairs[1, 4].entries, pairs[1, 4].seconds) == (2, 2.0)
    [out1, out2] = pairs[1, 4].callees
    assert (out1.va, out1.count, out1.per_entry, out1.sites, out1.a1) == (
        OUT1,
        3,
        3.0,
        {CODE: 3},
        {17: 3},
    )
    assert (out2.va, out2.per_entry) == (OUT2, 1.0)
    text = ob.report([first, second])
    assert "(1,4)  2 entries, 2.0 s, 4 calls" in text
    assert "a1 17:3" in text
    assert text.endswith("sequence: (0,2) 1.0s -> (1,4) 1.0s -> (0,2) 1.0s")


def test_batches():
    assert ob.batches([1, 2, 3], None) == [(1, 2, 3)]
    assert ob.batches([1, 2, 3], 2) == [(1, 2), (3,)]


def setup_monster(fake) -> None:
    registry = [0] * 21
    registry[1] = ENT
    fake.poke("21I", a.ENTITY_REGISTRY, *registry)
    fake.poke("I", ENT + a.ENTITY.VTABLE, VT)
    fake.poke("BB", ENT + a.ENTITY.MAIN_STATE, 0, 2)


def emulated_clock(fake, script):
    """`cpu.evaluate` answers usec in steps of 0.25 s and plays `script(fake, n)` on each."""
    calls = [0]

    async def evaluate(ws, msg):
        n = calls[0] = calls[0] + 1
        await script(n)
        await fake._reply(ws, msg, uintValue=250_000 * n, floatValue="0")

    fake._table["cpu.evaluate"] = evaluate


def test_run_traces_and_snapshots(s, fake):
    setup_monster(fake)
    obs = ob.Observer(s, engine(), 75)
    assert list(obs.callees) == [OUT2, OUT1]

    async def script(n):
        if n == 3:
            assert {OUT1, OUT2, a.SET_AI_STATE} <= set(fake.breakpoints)
            await fake.log(
                f"BKP PC={a.SET_AI_STATE:08x}: " + hit(0, 750_000, 0, 0, 2, ENT, 1, 4).message
            )
            fake.poke("BB", ENT + a.ENTITY.MAIN_STATE, 1, 4)
            await fake.log(
                f"BKP PC={OUT1:08x}: " + hit(0, 800_000, CODE + 0x10, 1, 4, ENT, 17, 0).message
            )
            await fake.log(f"BKP PC={OUT1:08x}: garbage")

    emulated_clock(fake, script)
    r = obs.run(obs.callees, seconds=1.0, rate=4)
    assert not fake.breakpoints
    assert r.entity == ENT and r.seconds == pytest.approx(1.0, abs=0.3)
    assert [(c.callee, c.site, c.pair, c.args[1]) for c in r.calls] == [
        (OUT1, CODE + 8, (1, 4), 17)
    ]
    assert [(t.before, t.after) for t in r.transitions] == [((0, 2), (1, 4))]
    assert r.unparsed == 1
    assert r.snapshots[0].pair == (0, 2) and r.snapshots[-1].pair == (1, 4)
    assert r.stats and r.speed == pytest.approx(1.0)
    [change] = ob.pair_changes(r.snapshots)
    assert {c.name for c in change.changes} >= {"MAIN_STATE", "SUB_STATE"}


def test_run_engine_indirect_and_writes(s, fake):
    setup_monster(fake)
    e = engine()
    e.zone = lambda va: "em" if CODE <= va < CODE + 0x80 else None
    obs = ob.Observer(s, e, 75)
    obs.indirect = {CODE + 0x18: "t9"}

    async def script(n):
        if n == 3:
            assert fake.breakpoints[OUT3]["condition"] == f"t0 == {ENT:#x}"
            assert "{t9}" in fake.breakpoints[CODE + 0x18]["logFormat"]
            assert (ENT + 0x278, 4) in fake.watchpoints
            await fake.log(bkp(OUT3, 600_000, CODE + 0x24, 1, 4, 7, 0, 0, ENT))
            await fake.log(bkp(CODE + 0x18, 650_000, 0, 1, 4, ENT, 0, 6, 1, OUT2))
            await fake.log(f"CHK Write32(CPU) at {ENT + 0x278:08x}: 000AAE60 {CODE + 4:08x} 0401")

    emulated_clock(fake, script)
    r = obs.run(
        seconds=1.0, rate=0, indirect=obs.indirect, engine={OUT3: "t0"}, writes=[(0x278, 4)]
    )
    assert not fake.breakpoints and not fake.watchpoints
    assert sorted((c.callee, c.site) for c in r.calls) == [(OUT2, CODE + 0x18), (OUT3, CODE + 0x1C)]
    assert r.writes == [ob.Write(0x278, CODE + 4, CODE, (1, 4), 450_000)]  # from the first usec
    assert r.traced() == {OUT2, OUT3}
    with pytest.raises(ValueError, match="both"):
        obs.run([OUT3], seconds=1, engine=[OUT3])


def test_run_refuses_another_overlay(s, fake):
    setup_monster(fake)
    e = engine()
    e.codes[75] = Code(Image(struct.pack("<32I", *([1] * 32)), CODE), range(CODE, CODE + 0x80))
    with pytest.raises(ValueError, match="not the overlay loaded"):
        ob.Observer(s, e, 75).run(seconds=1)


def test_cost_arms_nothing_for_zero(s, fake):
    setup_monster(fake)
    obs = ob.Observer(s, engine(), 75)
    armed = []

    async def script(n):
        armed.append(len(fake.breakpoints))

    emulated_clock(fake, script)
    [none, two] = ob.cost(obs, [0, 2], seconds=0.5)
    assert (none.breakpoints, two.breakpoints) == (0, 3)
    assert max(armed) == 3 and 0 in armed


def test_cli_trace(s, fake, monkeypatch, capsys, tmp_path):
    from contextlib import contextmanager

    from mhfu.cli import main
    from mhfu.cli import observe as cli

    setup_monster(fake)
    obs = ob.Observer(s, engine(), 75)
    monkeypatch.setattr(cli, "_observer", contextmanager(lambda args: (yield obs)))

    async def script(n):
        if n == 2:
            await fake.log(bkp(OUT1, 600_000, CODE + 0x10, 0, 2))

    emulated_clock(fake, script)
    out = tmp_path / "trace.json"
    args = ["observe", "trace", "--seconds", "1", "--callees", f"ACT_SET,{OUT1:#x}"]
    assert main([*args, "--json", str(out)]) == 0
    text = capsys.readouterr().out
    assert f"(0,2)  1 entries, 1.0 s, 1 calls\n  {OUT1:08X}      1" in text
    assert "run 1: 2 callee(s), speed 1.000x" in text
    data = json.loads(out.read_text())
    assert data["runs"][0]["targets"] == [a.ACT_SET, OUT1]
    assert data["pairs"][0]["callees"][0]["va"] == OUT1


def test_layout():
    from mhfu.views import layout

    assert layout("u16[3]").size == 6 and layout("vec3").size == 12
    assert layout("bytes") is None and layout("QUEST_TARGET[2]") is None


def test_cli_engine_and_writes_specs():
    from mhfu.cli import observe as cli

    assert cli._engine(f"{OUT1:#x}@t0, ACT_SET") == {OUT1: "t0", a.ACT_SET: "a0"}
    assert cli._writes("POSITION,YAW,0x27C,0x27E:1") == [
        (a.ENTITY.POSITION, 12),
        (a.ENTITY.YAW, 2),
        (0x27C, 4),
        (0x27E, 1),
    ]
