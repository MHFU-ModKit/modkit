import asyncio
import struct
import threading
from collections.abc import Iterator

import pytest
from mhfu import addresses as a
from mhfu import files
from mhfu import stage as S
from mhfu.cli import main
from ppsspp_debug.testing import FakePPSSPP

HEAP = a.USER_RAM + 0x10_0000
SPOTS = a.USER_RAM + 0x20_0000
SPAWNS = a.USER_RAM + 0x30_0000
POPO = a.USER_RAM + 0x40_0000


class Game:
    """A FakePPSSPP over the user partition on its own loop thread, for the blocking client."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.fake = FakePPSSPP(base=a.USER_RAM, size=a.USER_RAM_END - a.USER_RAM)
        self.run(self.fake.__aenter__())

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def put(self, address: int, data: bytes) -> None:
        at = address - a.USER_RAM
        self.fake.memory[at : at + len(data)] = data

    def close(self) -> None:
        self.run(self.fake.__aexit__(None, None, None))
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()
        self.loop.close()


def u32(v: int) -> bytes:
    return struct.pack("<I", v)


@pytest.fixture
def live(monkeypatch) -> Iterator[Game]:
    monkeypatch.delenv("MHFU_DATA", raising=False)
    g = Game()
    g.put(a.MAP_MANAGER_PTR, u32(HEAP))
    g.put(HEAP + a.MAP_MANAGER.ROW, u32(11))
    g.put(HEAP + a.MAP_MANAGER.STAGE, struct.pack("<H", 98))
    g.put(a.PLAYER_ENTITY + a.ENTITY.POSITION, struct.pack("<3f", 1000, 0, 2000))
    g.put(a.RESOURCE_TABLE, u32(HEAP + 0x1000))
    slot = struct.pack("<HHII", 2, files.engine_id(files.stage_pac(98)), HEAP + 0x8000, 0)
    g.put(HEAP + 0x1000, slot)
    for k in range(10):
        x, r = (S.SPOT_END_X, 0.0) if k % 5 == 4 else (1000.0 + k, 200.0)
        g.put(SPOTS + 0x18 * k, struct.pack("<4f4H", x, 0, 2000, r, k - k // 5, 6, 3, 2))
    g.put(a.GATHER_SPOT_CURRENT, u32(SPOTS + 0x18))
    for k, (hp, ent) in enumerate(((54, 2), (S.NO_HP, S.NO_ENTITY))):
        rec = struct.pack("<I3f6H", 7, 5000, 50, 7000, k, 2, 100, hp, ent, 0)
        g.put(SPAWNS + 0x40 * k, rec)
    g.put(a.ENTITY_REGISTRY + 4 * 3, u32(POPO))
    g.put(POPO, u32(a.POPO_VTABLE))
    g.put(POPO + a.ENTITY.HP, struct.pack("<H", 54))
    yield g
    g.close()


def test_spots(live, capsys):
    assert main(["stage", "spots", "--port", str(live.fake.port)]) == 0
    out = capsys.readouterr().out
    assert "stage st098 (row 11)" in out
    assert f"{files.stage_pac(98)}@{HEAP + 0x8000:08X}" in out
    assert f"at 0x{HEAP + 0x8000:08X}" in out
    assert f"8 slots at 0x{SPOTS:08X}" in out
    assert f"{SPOTS + 0x18:08X}   1" in out and "the hunter is here" in out


def test_spawns(live, capsys):
    assert main(["stage", "spawns", "--port", str(live.fake.port)]) == 0
    out = capsys.readouterr().out
    assert f"2 records from 0x{SPAWNS:08X}" in out
    assert f"popo       {POPO:08X}" in out and "hp=54" in out
    assert "1 records belong to other areas" in out


def test_ids(game, capsys):
    assert main(["stage", "ids", "--data", str(game.root)]) == 0
    out = capsys.readouterr().out
    assert "st046_4d" in out and "267 overlays, 282 PACs" in out


def test_maps_exits_surfaces(game, capsys):
    data = ["--data", str(game.root)]
    assert main(["stage", "maps", *data]) == 0
    assert "row 11 n=10 entry=st098" in capsys.readouterr().out
    assert main(["stage", "exits", "98", "--check", *data]) == 0
    out = capsys.readouterr().out
    assert "-> st099" in out and "flagged: 0" in out
    assert main(["stage", "exits", *data]) == 0
    assert "555 exits over 226 stages" in capsys.readouterr().out
    assert main(["stage", "surfaces", "98", *data]) == 0
    assert "id 7" in capsys.readouterr().out
    assert main(["stage", "surfaces", *data]) == 0
    assert "0x0080" in capsys.readouterr().out
