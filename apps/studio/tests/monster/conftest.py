# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Synthetic manifests, intel documents and PACs, and the real games and builds."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mhfu.em import intel
from mhfu.em.intel import SpeciesIntel
from mhfu_port import build, manifest, mesh
from mhfu_port.data import Data
from mhfu_port.manifest import Manifest
from mhfu_port.mesh import Part, Skinned
from mhfu_studio.monster.document import PortDocument
from mhp_formats import Channel, Clip, Keyframe, Pac, Tmh, TmhImage, Track, fu
from mhp_formats.anim import quantize
from mhp_formats.skeleton import Bone, Skeleton

BASE = """
[port]
name = "t"
host_species = 75
pac = "t.bin"

[source]
model = 5248
em_id = 58

[build]
source_skeleton = true
skin = "source"
"""


@pytest.fixture
def make() -> Callable[[str], Manifest]:
    """A manifest from TOML appended to a minimal port."""
    return lambda body="": manifest.loads(BASE + body)


@pytest.fixture
def doc(tmp_path: Path) -> Callable[[str], PortDocument]:
    """A document over a manifest file in tmp_path."""

    def open_(body: str = "") -> PortDocument:
        path = tmp_path / "t.toml"
        path.write_text(BASE + body, encoding="utf-8")
        return PortDocument.open(path)

    return open_


def pair(main: int, sub: int, **over: Any) -> dict[str, Any]:
    d = {
        "main": main,
        "sub": sub,
        "handler": "0x100",
        "a1": [14],
        "ends_on": "clip",
        "event_frames": [],
        "window_frames": [],
        "budget": {"gated": False, "phase0_seeds": [], "post_hook_owns": None},
        "effects": [],
        "measured": None,
    }
    return d | over


@pytest.fixture
def species() -> Callable[..., SpeciesIntel]:
    """`species(pairs, census=False, **doc)`: an em75 document; `pair(m, s, **fields)` builds
    the pair records, and a measured block is `{"entered": n, "dwell_ticks": t}`."""

    def make_(pairs: list[dict[str, Any]], census: bool = False, **over: Any) -> SpeciesIntel:
        doc = {
            "schema": intel.SCHEMA,
            "host_species": 75,
            "main_states": [
                {"main": 0, "sub_states": 34, "enumerated": True},
                {"main": 1, "sub_states": 20, "enumerated": True},
                {"main": 5, "sub_states": None, "enumerated": False},
            ],
            "static": {"present": True},
            "census": {"present": census, "reason": "" if census else "no census"},
            "pairs": pairs,
        }
        return SpeciesIntel(doc | over)

    return make_


@pytest.fixture
def make_pair() -> Callable[..., dict[str, Any]]:
    return pair


def clip(frames: int, loop: int = 0, tracks: int = 2, value: int = 0) -> Clip:
    """`tracks` tracks, each one rotation channel keyed at 0 and `frames`."""
    keys = [Keyframe(0, 0), Keyframe(value, frames)]
    return Clip([Track([Channel(0x008, list(keys))]) for _ in range(tracks)], loop)


@pytest.fixture
def make_clip() -> Callable[..., Clip]:
    return clip


@pytest.fixture
def synthetic_pac() -> bytes:
    """An MHFU model PAC: 3 joints in two parts (0, 0, 1), one textured group of 3 vertices,
    a 2x2 texture; slot 1 plays on both parts and turns joint 1 by 90 degrees about Y by frame 10,
    slot 2 only on part 1."""
    bones = [
        Bone(parent=-1, position=(0.0, 0.0, 0.0), stream=0),
        Bone(parent=0, position=(0.0, 100.0, 0.0), stream=0),
        Bone(parent=1, position=(0.0, 0.0, 50.0), stream=1),
    ]
    skel = Skeleton(bones, [0, 3])
    verts = [(0.0, 100.0, 0.0), (0.0, 100.0, 50.0), (10.0, 100.0, 0.0)]
    part = Part(
        verts,
        [],
        [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
        [],
        [(0, 1, 2)],
        [[(1, 1.0)], [(2, 1.0)], [(1, 1.0)]],
        0,
    )
    model = mesh.build([Skinned(part, part.influences)], (256.0, 256.0, 256.0))
    image = TmhImage(3, 2, 2, bytes(range(16)))
    turn = [Keyframe(0, 0), Keyframe(quantize("rot", 1.5707963), 10)]
    body = Clip(
        [Track([Channel(0x008, [Keyframe(0, 0), Keyframe(0, 10)])]), Track([Channel(0x010, turn)])],
        1,
    )
    head = Clip([Track([Channel(0x008, [Keyframe(0, 0), Keyframe(0, 6)])])])
    anim = fu.Anim([[None, body, None], [None, None, None], [None, head, head]])
    entries = [skel.to_bytes(), model.to_bytes(), Tmh([image]).to_bytes(), anim.to_bytes()]
    return Pac(entries).to_bytes()


@pytest.fixture(scope="session")
def games(mhfu_data: Path, mhp3rd_data: Path) -> Data:
    return Data.find(mhfu_data, mhp3rd_data)


@pytest.fixture(scope="session")
def built(games: Data, ports: Path) -> Callable[[str], bytes]:
    """The port's model PAC, built once per session."""
    cache: dict[str, bytes] = {}

    def get(name: str) -> bytes:
        if name not in cache:
            cache[name] = build.build(manifest.load(ports / f"{name}.toml"), games).pac
        return cache[name]

    return get


@pytest.fixture(scope="session")
def em75(games: Data) -> SpeciesIntel:
    return SpeciesIntel(intel.build(intel.Game(games.fu), 75))
