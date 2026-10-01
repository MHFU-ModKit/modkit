# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A workspace over a synthetic port: manifest, the shared 3-joint PAC and em75-shaped intel
with a chain, parts and attacks, so every panel branch runs without game data."""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from mhfu.em.intel import HostSummary, SpeciesIntel
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.document import PortDocument

PORT = """
[port]
name = "t"
host_species = 75
pac = "t.bin"

[source]
model = 5248

[clips.walk]
slot = 1
frames = 10
loop = true

[moves.charge]
main = 1
sub = 4
clip = "walk"

[parts.head]
index = 1

[[hurtbox]]
bone = 1
radius = 9.0
part = 1

[[hurtbox]]
bone = 40
radius = 9.0
part = 2

[[hitbox]]
bone = 2
radius = 30.0
set = 2

[[attack]]
id = 6
power = 40
volume = 2

[[effect]]
move = "charge"
frame = 3
id = 5
bone = 2
"""


def chain_pairs(pair: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        pair(
            1,
            4,
            a1=[1],
            attack_ids=[6],
            event_frames=[5, 40],
            window_frames=[8],
            next=[
                {"to": [[0, 3]], "guards": ["phase==3", "!collided", "budget spent"]},
                {"to": [[0, 6]], "guards": ["phase==3", "collided"], "mode": 1},
            ],
            prev=[],
        ),
        pair(1, 3, a1=[2], next=[{"to": [[0, 1], [0, 2]], "guards": ["phase==1"]}], prev=[]),
        pair(0, 3, a1=[9], next=[{"to": [[0, 1], [0, 2]], "guards": ["phase==1"]}], prev=[[1, 4]]),
        pair(0, 6, next=[{"to": [[0, 1], [0, 2]], "guards": []}], prev=[[1, 4]]),
        pair(0, 1, next=[], prev=[[0, 3]]),
        pair(0, 2, next=[], prev=[[0, 3]]),
        pair(3, 9, attack_ids=[7], next=[{"to": [[0, 1]], "guards": ["phase==2"]}], prev=[]),
        pair(3, 10, attack_ids=[7], next=[{"to": [[0, 1]], "guards": ["phase==2"]}], prev=[]),
    ]


def sphere(
    bone: int, part: int = 0, row: int = 0, radius: float = 20.0, **kw: Any
) -> dict[str, Any]:
    return {"bone": bone, "part": part, "hitzone_row": row, "radius": radius, **kw}


PARTS = {
    "present": True,
    "active_set": "0x1000",
    "sets": [
        {
            "va": "0x1000",
            "kind": "hurtbox",
            "species": [75],
            "spheres": [
                sphere(1, 1, 1),
                sphere(2, 2, 2, shape="capsule", b=[0, 0, 30]),
                sphere(125),
            ],
        },
        {"va": "0x2000", "kind": "hurtbox", "species": [76], "spheres": [sphere(1, 3, 3)]},
    ],
    "grid": {"present": True, "states": [{"va": "0x3000", "rows": [[i] * 10 for i in range(7)]}]},
}
ATTACKS = {
    "present": True,
    "spawner": "0x4000",
    "join": "measured",
    "id_offsets": {"75": 0},
    "tables": [
        {
            "handle": "0x5000",
            "records": "0x6000",
            "primary": True,
            "sets": [
                {"index": 2, "va": "0x7000", "spheres": [sphere(2, radius=40.0), sphere(1)]},
                {"index": 3, "va": "0x7100", "spheres": [sphere(127, radius=50.0)]},
            ],
            "attacks": [
                {"id": 6, "power": 64, "element": "0x10", "volume": 2},
                {"id": 7, "power": 30, "volume": 3},
            ],
        }
    ],
}


@pytest.fixture
def intel75(
    species: Callable[..., SpeciesIntel], make_pair: Callable[..., dict[str, Any]]
) -> SpeciesIntel:
    return species(
        chain_pairs(make_pair),
        chain={"hubs": [[0, 1], [0, 2]]},
        parts=PARTS,
        attacks=ATTACKS,
        unattributed_effects=[{"sites": [{"id": 5, "bone": 2, "frame": 10}]}],
    )


@pytest.fixture
def port_doc(tmp_path: Path) -> PortDocument:
    path = tmp_path / "t.toml"
    path.write_text(PORT, encoding="utf-8")
    return PortDocument.open(path)


@pytest.fixture
def workspace(
    imgui: Any,
    gl: Any,
    port_doc: PortDocument,
    synthetic_pac: bytes,
    intel75: SpeciesIntel,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Any]:
    """The workspace on a viewport, the port loaded, the host em75 its intel and its own PAC
    the synthetic one; no game data is looked for."""
    from mhfu_studio.monster.workspace import MonsterWorkspace

    monkeypatch.delenv("MHFU_DATA", raising=False)
    monkeypatch.delenv("MHP3RD_DATA", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    ws = MonsterWorkspace()
    ws.intel_cache[75] = intel75
    ws.hosts = [HostSummary.of(intel75)]
    ws.setup(gl)
    scene = Scene.from_bytes(
        synthetic_pac, "t", manifest=port_doc.manifest, path=tmp_path / "t.bin"
    )
    ws.load(scene, port_doc)
    ws.host_scenes[75] = Scene.from_bytes(synthetic_pac, "em75")
    yield ws
    ws.close()
