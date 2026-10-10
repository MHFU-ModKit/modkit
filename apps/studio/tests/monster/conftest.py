# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Synthetic manifests, intel documents and PACs, and the real games and builds."""

import dataclasses
import typing
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from mhfu.em import intel
from mhfu.em.intel import HostSummary, SpeciesIntel
from mhfu_port import behaviour, build, manifest, mesh
from mhfu_port.data import Data
from mhfu_port.manifest import Manifest
from mhfu_port.mesh import Part, Skinned
from mhfu_studio.monster.core.scene import Scene
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
    return _pac()


@pytest.fixture
def carried_pac() -> bytes:
    """`synthetic_pac` as a carried build has it: joint 0's child is the root the engine moves
    by (joint 1), and slot 1 leaves the turn to YAW, so it carries none."""
    return _pac(carried=True)


def _pac(carried: bool = False) -> bytes:
    bones = [
        Bone(parent=-1, child=1 if carried else -1, position=(0.0, 0.0, 0.0), stream=0),
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
    model = mesh.build([Skinned(part, part.influences)], (256.0, 256.0, 256.0), tip=None)
    image = TmhImage(3, 2, 2, bytes(range(16)))
    turn = [Keyframe(0, 0), Keyframe(0 if carried else quantize("rot", 1.5707963), 10)]
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


# ---- a synthetic port: every panel branch without game data ----

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
#: as an overlay has them: sets from 0, records from 0 with record 0 (and here 1-5) blank
ATTACKS = {
    "present": True,
    "spawner": "0x4000",
    "join": "measured",
    "id_offsets": {"75": 0},
    "tables": [
        {
            "handle": "0x5000",
            "records": "0x6000",
            "volume_table": "0x7800",
            "primary": True,
            "sets": [
                {"index": 0, "va": "0x6F00", "spheres": [sphere(1)]},
                {"index": 1, "va": "0x6F10", "spheres": [sphere(1)]},
                {"index": 2, "va": "0x7000", "spheres": [sphere(2, radius=40.0), sphere(1)]},
                {"index": 3, "va": "0x7100", "spheres": [sphere(127, radius=50.0)]},
            ],
            "attacks": [
                *({"id": i} for i in range(6)),
                {"id": 6, "power": 64, "element": "0x10", "volume": 2, "raw": "0140"},
                {"id": 7, "power": 30, "volume": 3, "raw": "011e"},
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


def _workspace(
    gl: Any,
    doc: PortDocument,
    pac: bytes,
    intel: SpeciesIntel,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Any]:
    from mhfu_studio.monster.workspace import MonsterWorkspace

    monkeypatch.delenv("MHFU_DATA", raising=False)
    monkeypatch.delenv("MHP3RD_DATA", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    ws = MonsterWorkspace()
    ws.intel_cache[75] = intel
    ws.hosts = [HostSummary.of(intel)]
    ws.setup(gl)
    scene = Scene.from_bytes(pac, "t", manifest=doc.manifest, path=tmp_path / "t.bin")
    ws.load(scene, doc)
    ws.host_scenes[75] = Scene.from_bytes(pac, "em75")
    yield ws
    ws.close()


@pytest.fixture
def workspace(
    gl: Any,
    port_doc: PortDocument,
    synthetic_pac: bytes,
    intel75: SpeciesIntel,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Any]:
    """The workspace on a viewport, the port loaded, the host em75 its intel and its own PAC
    the synthetic one; no game data is looked for."""
    yield from _workspace(gl, port_doc, synthetic_pac, intel75, monkeypatch, tmp_path)


@pytest.fixture
def carried(
    gl: Any,
    port_doc: PortDocument,
    carried_pac: bytes,
    intel75: SpeciesIntel,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Any]:
    """`workspace` on `carried_pac`: a rig YAW turns."""
    yield from _workspace(gl, port_doc, carried_pac, intel75, monkeypatch, tmp_path)


@pytest.fixture
def zinogre_toml(ports: Path, tmp_path: Path) -> Path:
    """A copy of the Zinogre's manifest named only welcome_howl and the clips its moves play: the
    shipped names change as the owner names clips, so the tests keep their own."""
    m = manifest.load(ports / "zinogre.toml")
    howl = manifest.Clip(source=2, label="the howling he does when he notices you")
    used = {mv.clip for mv in m.moves.values()}
    m.clips = {"welcome_howl": howl, **{n: c for n, c in m.clips.items() if n in used}}
    return manifest.save(m, tmp_path / "zinogre.toml")


SIDES = ("front", "left", "right", "behind")
#: small kinds of the effect role and of the var, signal and sides param types; named apart
#: from the package's own, which are none of their business
NEW_KINDS = (
    behaviour.Kind(
        "t_count",
        "effect",
        "Test count",
        (
            behaviour.Param("counter", "var", "Counter", tip="which counter"),
            behaviour.Param("by", "int", "By", 1, tip="how much"),
        ),
        "Adds to a counter",
    ),
    behaviour.Kind(
        "t_flag",
        "effect",
        "Test flag",
        (behaviour.Param("flag", "var", "Flag", tip="which flag"),),
        "Sets a flag",
    ),
    behaviour.Kind(
        "t_signal",
        "event",
        "Test signal",
        (behaviour.Param("name", "signal", "Signal", tip="which signal"),),
        "A signal from Lua",
    ),
    behaviour.Kind(
        "t_side",
        "condition",
        "Test side",
        (behaviour.Param("sides", "sides", "Sides", choices=SIDES, tip="which sides"),),
        "The hunter stands on one of these sides",
    ),
)


@pytest.fixture
def new_kinds(monkeypatch: pytest.MonkeyPatch) -> tuple[behaviour.Kind, ...]:
    """`NEW_KINDS` in `behaviour.KINDS`, the effect role in `Role`, and the loader's checks of
    the new param types where the package has none yet. Ask for it before a panel is built."""
    roles = typing.get_args(behaviour.Role)
    if "effect" not in roles:
        monkeypatch.setattr(behaviour, "Role", typing.Literal[(*roles, "effect")])
    if not {"var", "signal", "sides"} <= set(typing.get_args(behaviour.ParamType)):
        real = behaviour._check_value

        def check(p: behaviour.Param, v: object, where: str) -> None:
            if p.type in ("var", "signal"):
                ok = isinstance(v, str) and behaviour.ID.fullmatch(v) is not None
                behaviour.need(ok, where, f"{v!r} is not a name")
            elif p.type == "sides":
                ok = isinstance(v, list) and bool(v) and all(k in p.choices for k in v)
                behaviour.need(ok, where, f"{v!r} is not a list of sides")
            else:
                real(p, v, where)

        monkeypatch.setattr(behaviour, "_check_value", check)
    for k in NEW_KINDS:
        monkeypatch.setitem(behaviour.KINDS, k.name, k)
    return NEW_KINDS


def _skip_without(cls: type, name: str) -> None:
    if name not in {f.name for f in dataclasses.fields(cls)}:
        pytest.skip(f"the package has no {cls.__name__}.{name} yet")


@pytest.fixture
def sever_field() -> None:
    """For a test that needs `Part.sever_below`."""
    _skip_without(manifest.Part, "sever_below")


@pytest.fixture
def rage_field() -> None:
    """For a test that needs `Behaviour.natural_rage`."""
    _skip_without(behaviour.Behaviour, "natural_rage")
