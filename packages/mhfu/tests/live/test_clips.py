# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu import addresses as a
from mhfu.cli import clips as cli
from mhfu.cli import main
from mhfu.live import Session, clips, rig
from mhfu.live.shell_anim import MAGIC, Op
from mhfu.structs import ACTION_INPUT_BASE, ACTION_INPUT_STEP, entry_clip

MON, PACK = a.RAM.start + 0x90_0000, a.RAM.start + 0x91_0000  # inside the fake's memory
E, B, CB, BR = a.ENTITY, a.CLIP_BLOCK, a.CLI_BRIDGE, a.CLI_BRIDGE_BLOCK
STREAMS, SLOTS = 6, 100
FILLED = {0: 58, 3: 164, 100: 202, 101: 228}
"""Entry -> frames, in every part."""


def clip_at(entry: int, part: int) -> int:
    """The fake pack's offset of the clip part `part` plays for `entry`."""
    return 0x1000 + 0x100 * (entry * 3 + part)


class Engine:
    """A Tigrex in registry slot 1 over a 3-part pack, cli_bridge.lua's side of the block, and
    an executor that dispatches a forced entry once, on a read of ANIM_INPUT, unless `parked`,
    and again whenever the action's phase cursor reads zero (a restart)."""

    def __init__(self, fake) -> None:
        self.fake = fake
        self.parked = False
        self.frames = dict(FILLED)
        fake.memory.extend(bytes(BR + 0x40 - fake.base - len(fake.memory)))
        fake.poke("I", a.ENTITY_REGISTRY + 4, MON)
        fake.poke("I", MON + E.VTABLE, a.TIGREX_VTABLE)
        fake.poke("B", MON + E.SPECIES, 0x4B)
        fake.poke("I", MON + E.ACTION_TABLE, PACK)
        fake.poke("3B", MON + E.PHASE, 4, 0, 0)
        table = 8 * (STREAMS + 1)
        for s in range(STREAMS):
            fake.poke("2I", PACK + 8 * s, SLOTS, table + 4 * SLOTS * s)
            fake.poke(f"{SLOTS}I", PACK + table + 4 * SLOTS * s, *[clips.EMPTY] * SLOTS)
        for e in FILLED:
            for k in range(3):
                stream, slot = entry_clip(e, k)
                fake.poke("I", PACK + table + 4 * (SLOTS * stream + slot), clip_at(e, k))
        self.force: int | None = None
        self.pending = False
        self.forced: list[int] = []
        fake.on_read.append(self.tick)

    def block(self, part: int) -> int:
        return MON + E.CLIP_BLOCKS + part * (B.size or 0)

    def tick(self, address: int) -> None:
        f = self.fake
        if address == BR + CB.ACK:
            magic, seq, cmd, _slot, arg = f.peek("5I", BR)
            if magic == MAGIC and f.peek("I", BR + CB.ACK) != (seq,):
                f.poke("I", BR + CB.ACK, seq)
                self.force = arg if cmd == Op.FORCE_ACTION else None
                self.pending = True
                self.forced.append(arg if cmd == Op.FORCE_ACTION else -1)
        if address == MON + E.ANIM_INPUT and self.force is not None:
            if (self.pending and not self.parked) or f.peek("B", MON + E.PHASE) == (0,):
                self.dispatch(self.force)
                self.pending = False
                f.poke("B", MON + E.PHASE, 1)
        for k in range(3):
            if address == self.block(k) + B.PHASE:
                (phase,) = f.peek("f", address)
                f.poke("f", address, phase + 1)

    def dispatch(self, entry: int) -> None:
        """Entries 24 and 25 drive the head alone, as em75's do."""
        f = self.fake
        parts = (1,) if entry in (24, 25) else range(3)
        for k in parts:
            f.poke(
                "H", MON + E.ANIM_INPUT + 2 * k, entry + ACTION_INPUT_BASE + k * ACTION_INPUT_STEP
            )
        if entry not in self.frames:
            return  # an empty entry keeps the clips
        for k in parts:
            at = self.block(k)
            f.poke("2f", at + B.PHASE, 0.0, 2.0)
            f.poke("f", at + B.END, float(self.frames[entry]))
            f.poke("I", at + B.NODE, PACK + clip_at(entry, k))


@pytest.fixture
def engine(fake):
    return Engine(fake)


def test_pack_reads_the_slot_tables(s, engine):
    pack = clips.Pack.read(s.mem, PACK)
    assert pack.holding(PACK + clip_at(100, 2)) == ((5, 0),)
    assert pack.entries() == sorted(FILLED)
    with pytest.raises(ValueError):
        clips.Pack.read(s.mem, MON)


def test_play(s, engine):
    p = clips.play(s, 100)
    assert (p.dispatched, p.kicked) == (True, False)
    assert [q.held for q in p.parts] == [((1, 0),), ((3, 0),), ((5, 0),)]
    assert all(q.resolved and q.moved and q.end == 202 for q in p.parts)
    assert clips.verdict(p, clips.Expect((1, 3, 5), 202)) == []
    assert clips.verdict(p, clips.Expect((1, 3, 5), 200)) == [
        f"part {k} is 202 frames, the build 200" for k in range(3)
    ]
    assert engine.forced == [100]


def test_play_restarts_a_parked_action(s, engine):
    engine.parked = True
    p = clips.play(s, 3)
    assert (p.dispatched, p.kicked) == (True, True)
    assert p.parts[0].end == 164


def test_play_again_restarts_at_once(s, engine):
    clips.play(s, 3)
    engine.parked = True
    start = s.now()
    p = clips.play(s, 3)
    assert (p.dispatched, p.kicked) == (True, True) and p.parts[0].resolved
    assert s.now() - start < 1.0


def test_an_empty_entry_keeps_the_clip(s, engine):
    clips.play(s, 3)
    p = clips.play(s, 104)
    assert p.dispatched and [q.held for q in p.parts] == [((0, 3),), ((2, 3),), ((4, 3),)]
    assert clips.verdict(p, None) == [
        f"part {k} keeps stream {2 * k} slot 3: stream {2 * k + 1} slot 4 is empty"
        for k in range(3)
    ]


def test_a_head_only_entry(s, engine):
    clips.play(s, 3)
    p = clips.play(s, 25)
    assert p.dispatched and [q.taken for q in p.parts] == [False, True, False]
    assert clips.verdict(p, None) == [
        "part 0 was not dispatched",
        "part 1 keeps stream 2 slot 3: stream 2 slot 25 is empty",
        "part 2 was not dispatched",
    ]


def test_no_dispatch(s, engine):
    engine.parked = True
    engine.fake.on_read.insert(0, lambda at: engine.fake.poke("B", MON + E.PHASE, 4))
    p = clips.play(s, 3, timeout=1.0)
    assert not p.dispatched and "no dispatch" in clips.verdict(p, None)


def test_sweep_releases(s, engine):
    assert [p.entry for p in clips.sweep(s, [0, 101])] == [0, 101]
    assert engine.forced == [0, 101, -1]


def test_read_expect(tmp_path):
    path = tmp_path / "slots.csv"
    path.write_text(
        "slot,driven_by,pairs,host_streams,port_streams,host_frames,port_frames,shared,source,clip\n"
        "0,,0,,0 2 4,,58,1,same 211,\n"
        '100,"(1,13)",1,,1 3 5,,202,1,same 248,\n'
        '104,"(3,2)",1,,,,,,,\n'
    )
    assert clips.read_expect(path) == {
        0: clips.Expect((0, 2, 4), 58),
        100: clips.Expect((1, 3, 5), 202),
    }


def test_deploy(tmp_path):
    stick = tmp_path / "PSP"
    (stick / "PLUGINS/mhfu_framework/inject").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="mhfu-port inject"):
        clips.deploy("zin", stick=stick)
    (stick / "PLUGINS/mhfu_framework/inject/zin.bin").write_bytes(b"pac")
    lib = stick / "PLUGINS/mhfu_framework/mods/lib"
    lib.mkdir(parents=True)
    (lib / "zin_clips.lua").write_text("return {}\n")
    wrote = clips.deploy("zin", stick=stick)
    assert sorted(p.name for p in wrote) == ["cli_bridge.lua", "mhfu_port.lua", "zin_rig.lua"]
    rider = (stick / "PLUGINS/mhfu_framework/mods/zin_rig.lua").read_text()
    assert "species = 75, replace = { 77 }" in rider
    assert 'orig = "file_06185.bin.orig"' in rider and "fid = 6186" in rider


def test_cli(fake, engine, clock, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(rig, "running", lambda launcher: True)
    monkeypatch.setattr(Session, "launch", lambda *a, **kw: Session.attach(fake.port, timeout=5))
    expect, out = tmp_path / "slots.csv", tmp_path / "sweep.csv"
    rows = [f"{e},{' '.join(map(str, range(e // 100, 6, 2)))},{n}" for e, n in FILLED.items()]
    expect.write_text("\n".join(["slot,port_streams,port_frames", *rows]) + "\n")
    engine.frames[101] = 200
    argv = ["--lane", "3", "--expect", str(expect), "--out", str(out)]
    assert main(["clips", "sweep", *argv]) == 1
    text = capsys.readouterr().out.splitlines()
    assert text[-2].startswith(" 101  200/200/200") and "the build 228" in text[-2]
    assert text[-1] == "3/4 entries right on every part"
    assert out.read_text().splitlines()[0] == ",".join(cli.FIELDS)
    assert len(out.read_text().splitlines()) == 1 + 4 * 3
    assert main(["clips", "play", "100", "--lane", "3"]) == 0
    assert "part 1: stream 3 slot 0, 202 frames" in capsys.readouterr().out
    assert cli.entries(["3", "100-102"]) == [3, 100, 101, 102]
