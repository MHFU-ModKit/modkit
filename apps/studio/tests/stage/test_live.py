# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The live push against fake memory: a loader that puts a PAC down and fixes it up as the
game does, and a fake PPSSPP for the watchpoint the catch waits on."""

import asyncio
import json
import struct
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from mhfu import addresses as a
from mhfu import files
from mhfu.files import Extracted
from mhfu.memory import Image, Live, Memory, Space
from mhfu_studio.cli import main
from mhfu_studio.stage import collision as C
from mhfu_studio.stage import live
from mhfu_studio.stage.file import TEXTURES, StageFile
from mhp_formats.fu.stage import BASE, GRID_POINTER, TERM, TRI_SIZE, TRIS_POINTER
from ppsspp_debug import Client
from ppsspp_debug.testing import FakePPSSPP

PAC = a.USER_RAM + 0x10_0000
TABLE = a.MAP_MANAGER_PTR + 0x1000
MANAGER = a.MAP_MANAGER_PTR + 0x400

OPS = [
    {"op": "move", "group": 0, "vertices": [0, 1, 2], "by": [0, 40, 0]},
    {"op": "move", "group": None, "box": [-1, -1, -1, 751, 1, 1001], "by": [600, 0, 0]},
    {"op": "collision", "group": None, "solid_box": [100, 0, 100, 300, 200, 300]},
    {"op": "collision", "group": None, "chunk": 0, "tri": 1, "flags": {"material": 10}},
    {"op": "texture", "slot": 1, "rgb": [9, 9, 9]},
]


def load(mem: Memory, sf: StageFile, pac: int = PAC, loaded: bool = True) -> None:
    """Put the PAC down as the loader does: bytes, then every collision offset a pointer."""
    mem.write(pac, sf.data)
    for c, hits in enumerate(sf.chunks):
        at = pac + sf.chunk_offset(c)
        base = at + BASE
        tris = base + mem.u32(at + TRIS_POINTER)
        mem.write_u32(at + GRID_POINTER, base + mem.u32(at + GRID_POINTER))
        mem.write_u32(at + TRIS_POINTER, tris)
        grid = mem.u32(at + GRID_POINTER)
        for cell, head in enumerate(hits.heads()):
            mem.write_u32(grid + 4 * cell, base + head)
            run = base + head
            while (w := mem.u32(run)) != TERM:
                mem.write_u32(run, tris + w)
                run += 4
    mem.write_u32(a.RESOURCE_TABLE, TABLE)
    slot = struct.pack(
        "<HHII", 2 if loaded else 0, files.engine_id(files.stage_pac(sf.number)), pac, 0
    )
    mem.write(TABLE, slot)
    mem.write_u32(a.MAP_MANAGER_PTR, MANAGER)
    mem.write_u16(MANAGER + a.MAP_MANAGER.STAGE, sf.number)


def walk(mem: Memory, sf: StageFile, pac: int = PAC) -> list[list[set[bytes]]]:
    """The live broadphase: per chunk, per cell, the records its list points at."""
    out = []
    for c, hits in enumerate(sf.chunks):
        at = pac + sf.chunk_offset(c)
        grid = mem.u32(at + GRID_POINTER)
        cells = []
        for cell in range(len(hits.cells)):
            run, found = mem.u32(grid + 4 * cell), set()
            while (w := mem.u32(run)) != TERM:
                found.add(mem.read(w, TRI_SIZE))
                run += 4
            cells.append(found)
        out.append(cells)
    return out


def offline(plan: C.Plan) -> list[list[set[bytes]]]:
    coll = plan.collision()
    return [[{h.tris[t].to_bytes() for t in run} for run in h.cells] for h in coll.chunks]


@pytest.fixture
def mem() -> Space:
    return Space(
        [
            Image(bytes(0x2000), a.MAP_MANAGER_PTR),
            Image(bytes(0x10000), PAC),
            Image(bytes(0x10000), a.STAGE_SCRATCH),
        ]
    )


@pytest.fixture
def push(st: StageFile, assets: Path) -> live.Push:
    return live.prepare(st, OPS, assets)


def test_pac_address(mem: Space, st: StageFile):
    assert live.pac_address(mem, 1) is None
    load(mem, st, loaded=False)
    assert live.pac_address(mem, 1) is None
    load(mem, st)
    assert live.pac_address(mem, 1) == PAC and live.pac_address(mem, 2) is None


def test_prepare(push: live.Push):
    assert [b.name for b in push.mesh] == ["sub 0"] and push.textures and push.collision
    assert push.textures.offset == push.stage.table[TEXTURES][0]
    assert len(push.patches) == 3 and not push.findings
    assert live.shadowed(push) == push.mesh[0].size


def test_describe_and_defaults(st: StageFile, assets: Path, push: live.Push):
    lines = live.describe(push)
    assert lines[0].startswith("sub 0: 1 runs") and "12 added" in lines[-1]
    lone = live.prepare(StageFile(1, st.data), OPS, assets)
    assert live.default_catch(lone) == live.QUEST_CATCH
    assert live.default_catch(live.prepare(st, OPS[1:], assets)) == 0.0
    assert live.climbs(push.collision.plan) == 1


def test_bytes_patch(mem: Space, st: StageFile, push: live.Push):
    load(mem, st)
    b = push.mesh[0]
    assert not b.intact(mem, PAC)
    b.apply(mem, PAC)
    assert b.verify(mem, PAC) and b.intact(mem, PAC)
    assert mem.read(PAC + b.offset, len(b.data)) == b.data


def test_collision_lands_as_planned(mem: Space, st: StageFile, push: live.Push, tmp_path: Path):
    load(mem, st)
    shipped = walk(mem, st)
    patch = push.collision
    assert patch is not None and patch.scratch_free(mem)
    patch.apply(mem, PAC)
    assert walk(mem, st) == offline(patch.plan) != shipped
    assert patch.intact(mem, PAC)
    assert not patch.scratch_free(mem)
    patch.save_undo(tmp_path / "undo.json", PAC)
    assert json.loads((tmp_path / "undo.json").read_text())["pac"] == PAC
    assert live.undo(mem, tmp_path / "undo.json") == len(patch.undo)
    assert walk(mem, st) == shipped and not patch.intact(mem, PAC)
    patch.apply(mem, PAC)
    live.restore(mem, st, log=lambda _: None)
    assert walk(mem, st) == shipped
    assert mem.read(PAC, len(st.data)) != st.data  # the fixup stays


def test_restore_writes_the_file_back(mem: Space, st: StageFile, push: live.Push):
    load(mem, st)
    pristine = mem.read(PAC, len(st.data))
    for p in push.patches:
        p.apply(mem, PAC)
    assert mem.read(PAC, len(st.data)) != pristine
    live.restore(mem, st, log=lambda _: None)
    assert mem.read(PAC, len(st.data)) == pristine


def test_not_this_stage(mem: Space, st: StageFile, push: live.Push):
    load(mem, st)
    mem.write_u32(PAC + st.chunk_offset(1) + TRIS_POINTER, 0)
    assert push.collision is not None
    with pytest.raises(live.NotThisStage):
        push.collision.apply(mem, PAC)


def test_hold_reapplies_after_a_reload(mem: Space, st: StageFile, push: live.Push):
    load(mem, st)
    for p in push.patches:
        p.apply(mem, PAC)
    lines: list[str] = []
    done = threading.Event()

    def reload() -> None:
        time.sleep(0.15)
        load(mem, st)
        done.set()

    threading.Thread(target=reload).start()
    moves, again = live.hold(mem, 1, push.patches, 0.5, lines.append, period=0.01)
    assert done.is_set() and moves == 0 and again == 3
    assert all(p.intact(mem, PAC) for p in push.patches)
    assert walk(mem, st) == offline(push.collision.plan)


# --- the catch, against a fake PPSSPP ---


class Game:
    """A FakePPSSPP over the user partition, on its own loop thread for the blocking client."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.fake = FakePPSSPP(base=a.USER_RAM, size=a.USER_RAM_END - a.USER_RAM)
        self.run(self.fake.__aenter__())

    def run(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=10)

    def close(self) -> None:
        self.run(self.fake.__aexit__(None, None, None))
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=10)
        self.loop.close()

    def until(self, test: Callable[[], bool], timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while not test():
            assert time.monotonic() < deadline, "timed out"
            time.sleep(0.01)


@pytest.fixture
def ppsspp(st: StageFile) -> Iterator[tuple[Game, Client, Live]]:
    game = Game()
    client = Client.connect(port=game.fake.port, timeout=10)
    mem = Live(client)
    load(mem, st)
    yield game, client, mem
    client.close()
    game.close()


def _tail(push: live.Push) -> int:
    b = push.mesh[0]
    return PAC + b.offset + len(b.original) - 4


def test_catch_lands_on_the_reload(ppsspp, st: StageFile, push: live.Push):
    game, client, mem = ppsspp
    caught: list[bool] = []
    t = threading.Thread(
        target=lambda: caught.append(live.catch(client, mem, push, 5, lambda _: None, 0.05))
    )
    t.start()
    game.until(lambda: bool(game.fake.watchpoints))
    assert list(game.fake.watchpoints) == [(_tail(push), 4)]
    game.run(game.fake.access(_tail(push)))
    t.join(timeout=10)
    assert caught == [True] and not game.fake.stepping and not game.fake.watchpoints
    assert push.mesh[0].verify(mem, PAC)


def test_catch_skips_another_stage(ppsspp, st: StageFile, push: live.Push):
    game, client, mem = ppsspp
    caught: list[bool] = []
    t = threading.Thread(
        target=lambda: caught.append(live.catch(client, mem, push, 1.0, lambda _: None, 0.05))
    )
    t.start()
    game.until(lambda: bool(game.fake.watchpoints))
    ident = PAC + st.table[0][0] + live.IDENT_AT
    mem.write(ident, b"\xee" * live.IDENT_SIZE)
    game.run(game.fake.access(_tail(push)))
    t.join(timeout=10)
    assert caught == [False] and not game.fake.stepping and not game.fake.watchpoints
    assert not push.mesh[0].verify(mem, PAC)


def test_push_command(ppsspp, st: StageFile, game: Extracted, assets: Path, capsys):
    fake_game, client, mem = ppsspp
    ops = assets / "st001.json"
    ops.write_text(json.dumps(OPS))
    base = ["map", "push", "--stage", "1", "--ops", str(ops), "--data", str(game.root)]
    port = ["--port", str(fake_game.fake.port)]
    pristine = mem.read(PAC, len(st.data))
    assert main([*base, "--catch", "0", "--dry"]) == 0
    assert mem.read(PAC, len(st.data)) == pristine and "runs" in capsys.readouterr().out
    assert main([*base, *port, "--catch", "0"]) == 0
    out = capsys.readouterr().out
    assert "verified True" in out and "undo in" in out
    p = live.prepare(st, OPS, assets)
    assert all(b.verify(mem, PAC) for b in [*p.mesh, p.textures])
    assert walk(mem, st) == offline(p.collision.plan)
    undo = assets / ".inject" / "st001_collision_undo.json"
    assert main([*base, *port, "--undo", str(undo)]) == 0
    assert walk(mem, st) == walk(Image(pristine, PAC), st)
    assert main([*base, *port, "--restore"]) == 0
    assert mem.read(PAC, len(st.data)) == pristine


def test_edit_command(game: Extracted, assets: Path, capsys):
    ops = assets / "st001.json"
    ops.write_text(json.dumps(OPS))
    out = assets / "out"
    assert (
        main(["map", "edit", "1", "--ops", str(ops), "--data", str(game.root), "--out", str(out)])
        == 0
    )
    names = sorted(p.name for p in out.iterdir())
    assert names == ["st001_collision.json", "st001_sub0.bin", "st001_sub1.bin"]
    assert "resident-safe" in capsys.readouterr().out
    ops.write_text(json.dumps([{"op": "add", "group": 0, "obj": "quad.obj"}]))
    assert main(["map", "edit", "1", "--ops", str(ops), "--data", str(game.root)]) == 1
    assert "retired-op" in capsys.readouterr().out
