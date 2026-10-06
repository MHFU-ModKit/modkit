# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Push to game against a fake game: the plan written, read back, or refused with a reason."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from mhfu import addresses as a
from mhfu.em.intel import SpeciesIntel
from mhfu.memory import Memory
from mhfu_studio.monster import push
from mhfu_studio.monster import runtime as RT
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.document import PortDocument
from mhfu_studio.monster.workspace import MonsterWorkspace

REC = a.HIT_VOLUME.step


@dataclass
class Monster:
    species: int


class Sparse(Memory):
    """Zeroes everywhere; `drop` loses the writes to one address."""

    def __init__(self, drop: int = -1) -> None:
        self.bytes: dict[int, int] = {}
        self.drop = drop

    def read(self, address: int, size: int) -> bytes:
        return bytes(self.bytes.get(address + i, 0) for i in range(size))

    def write(self, address: int, data: bytes) -> None:
        if address != self.drop:
            self.bytes.update((address + i, b) for i, b in enumerate(data))


@dataclass
class Game:
    mem: Sparse
    species: list[int] = field(default_factory=lambda: [75])

    def monsters(self) -> dict[int, Monster]:
        return {i + 1: Monster(s) for i, s in enumerate(self.species)}


def memory(p: RT.Plan, drop: int = -1) -> Sparse:
    """The plan's guards in place, zeroes elsewhere."""
    mem = Sparse(drop)
    for w in p.writes:
        for at, b in w.guards:
            mem.write(at, b)
    return mem


@pytest.fixture
def ws(port_doc: PortDocument, synthetic_pac: bytes, intel75: SpeciesIntel) -> MonsterWorkspace:
    w = MonsterWorkspace()
    w.intel_cache[75] = intel75
    w.load(Scene.from_bytes(synthetic_pac, "t", manifest=port_doc.manifest), port_doc)
    return w


@pytest.fixture
def game(ws: MonsterWorkspace, monkeypatch: pytest.MonkeyPatch) -> Game:
    """The fake game `push.attach` hands over, never a real PPSSPP."""
    m, host, _ = ws._module()
    g = Game(memory(RT.plan(m, host)))

    @contextmanager
    def attach() -> Iterator[tuple[Any, str]]:
        yield g, "lane 9's game"

    monkeypatch.setattr(push, "attach", attach)
    return g


def test_push(ws: MonsterWorkspace, game: Game) -> None:
    assert ws.push_blocker() is None
    ws.push()
    assert ws.message.startswith("pushed 4 write(s), 202 bytes, into lane 9's game in ")
    assert ws.message.endswith("read back matches")
    assert game.mem.read(0x1000, 2 * REC) == RT.plan(*ws._module()[:2]).writes[0].data[: 2 * REC]


def test_push_refused(ws: MonsterWorkspace, game: Game) -> None:
    game.species = [77]
    ws.push()
    assert ws.message == (
        "push failed: no Tigrex (em75) in lane 9's game: its tables are loaded only while one is"
        " in the quest"
    )
    game.species = [75]
    game.mem.write(0x7000 + 2 * REC, b"\0\0")
    ws.push()
    assert (
        "push failed: the game does not hold the tables this plan was built against" in (ws.message)
        and "attack set 2: 0x00007050 reads 0000, the plan expects FFFF" in ws.message
    )
    assert game.mem.read(0x1000, REC) == bytes(REC), "nothing written"


def test_no_game(ws: MonsterWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    from mhfu.live import rig

    monkeypatch.setenv("MHFU_LANE", "9")
    monkeypatch.setattr(rig, "running", lambda launcher: False)
    ws.push()
    assert ws.message == "push failed: no game attached: no PPSSPP with its debugger runs on lane 9"


def test_mismatch(ws: MonsterWorkspace) -> None:
    m, host, _ = ws._module()
    p = RT.plan(m, host)
    done = push.apply(memory(p, drop=p.writes[1].at), p, "here")
    assert done.mismatched == [p.writes[1].what]
    assert "read back DIFFERS at attack set 2" in done.describe()


def test_blockers(ws: MonsterWorkspace) -> None:
    assert ws.doc is not None
    ws.intel_cache[75] = None
    ws.intel_errors[75] = "set MHFU_DATA"
    assert ws.push_blocker() == (
        "cannot push hitboxes or attacks. No attack data for Tigrex (em75): set MHFU_DATA."
    )
    assert (MonsterWorkspace().push_blocker() or "").startswith("no port open")


def test_stick_differs(ws: MonsterWorkspace, tmp_path: Path) -> None:
    m = ws._module()[0]
    assert push.stick_differs(m, tmp_path) == ""
    (tmp_path / "t_hit.lua").write_text('    id = "0badf00d",\n')
    assert "t_hit.lua (id 0badf00d) puts its own tables back" in push.stick_differs(m, tmp_path)
    (tmp_path / "t_hit.lua").write_text(f'    id = "{RT.content_id(m)}",\n')
    assert push.stick_differs(m, tmp_path) == ""
