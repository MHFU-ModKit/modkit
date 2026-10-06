# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import hashlib
from pathlib import Path

import pytest
from mhfu_port import build, constraints, layout, manifest, verify
from mhfu_port.cli import main
from mhfu_port.manifest import Build
from mhfu_port.mesh import Part
from mhfu_port.model import SKELETON, TEXTURES
from mhfu_port.rig import Rig
from mhp_formats import fu, p3rd, pmo
from mhp_formats.anim import CHANNEL_BITS, Channel, Clip, Keyframe, Track
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Bone, Skeleton

PORTS = Path(__file__).parents[3] / "ports"
BUILT = {
    "brute_tigrex": "254a6b55b6c26527cfe35fb4c981c3f57f156d362f03aea20ec8b0afdb7b22be",
    "zinogre": "b5bad40e29b03ab0542ccb9ffa056810d10cec1d54302368ea556887b3ecdb5c",
}
"""sha256 of each manifest's build: changes only with an intended change to the port."""
HEAD = '[port]\nname = "t"\nhost_species = 75\npac = "t.bin"\n[source]\nmodel = 5248\n'


def clip(tracks: int) -> Clip:
    return Clip([Track() for _ in range(tracks)])


def donor(bones: int, *tracks: int, em: int | None = None) -> build.Donor:
    skeleton = Skeleton([Bone(parent=i - 1) for i in range(bones)])
    clips = {i + 1: clip(n) for i, n in enumerate(tracks)}
    return build.Donor(p3rd.Pmo(), skeleton, None, clips, em)


@pytest.mark.parametrize(("em_id", "want"), [(None, 58), (40, 40), (-1, None)])
def test_em(em_id, want):
    text = HEAD + (f"em_id = {em_id}\n" if em_id is not None else "")
    assert build.em_of(manifest.loads(text)) == want


def test_em_unknown_model():
    assert build.em_of(manifest.loads(HEAD.replace("5248", "7"))) is None


def test_record_map():
    d = donor(6, 3, 3)
    assert build.record_map(d, Build()) == {2: 0, 3: 1, 4: 2}
    assert build.record_map(d, Build(bone_offset=0, skip_bones=[1])) == {0: 0, 2: 1, 3: 2}


def test_record_map_disagrees():
    with pytest.raises(ValueError, match="record count"):
        build.record_map(donor(6, 3, 4), Build())


@pytest.mark.parametrize(
    ("given", "records", "want"),
    [(None, {1: 0, 5: 3}, 6), (None, {}, 10), (4, {1: 0}, 4), (0, {}, 10), (12, {}, 10)],
)
def test_animated(given, records, want):
    assert build.animated(Build(animated=given), records, 10) == want


def anim(*widths: int) -> fu.Anim:
    streams: list[list[Clip | None]] = []
    for w in widths:
        streams += [[clip(w), None, clip(w)] if w else [None], [None]]
    return fu.Anim(streams)


def rig(streams: list[int], declared: int) -> Rig:
    parts = [k for k, width in enumerate(streams) for _ in range(width)]
    bones = [Bone(parent=i - 1, stream=k) for i, k in enumerate(parts)]
    skeleton = Skeleton(bones, params=[0, declared])
    return Rig(skeleton, [], [], streams, {}, 0, [])


def host() -> build.Host:
    entries = [b"skel", b"pmo", b"tmh", b"anim", b"rest"]
    return build.Host(Pac(entries, 16, b"tail"), Skeleton(), None, anim(2, 1))


def test_pac():
    h, new = host(), anim(2, 1)
    out = Pac.from_bytes(build.pac(h, rig([2, 1], 3), pmo.Pmo(), b"textures", new))
    assert out.entries[TEXTURES:] == [b"textures", new.to_bytes(), b"rest"]
    assert out.tail == b"tail"
    assert Skeleton.from_bytes(out.entries[SKELETON]).params == [0, 3]
    keep = Pac.from_bytes(build.pac(h, rig([2, 1], 3), pmo.Pmo(), None, new))
    assert keep.entries[TEXTURES] == b"tmh"


@pytest.mark.parametrize(("streams", "declared"), [([2, 1], 4), ([2, 2], 4)])
def test_pac_disagrees(streams, declared):
    with pytest.raises(constraints.ConstraintError):
        build.pac(host(), rig(streams, declared), pmo.Pmo(), None, anim(2, 1))


def part(weighted: bool) -> Part:
    return Part([(0.0, 0.0, 0.0)], [], [], [], [], [[(1, 1.0)] if weighted else []], 0)


def binding(mode: build.Mode, bone_of: dict[int, int | None] | None = None) -> build.Binding:
    return build.Binding(mode, rig([2, 1], 3), bone_of or {}, {}, frozenset())


@pytest.mark.parametrize(
    ("asked", "mode", "weighted", "host_model", "want"),
    [
        ("auto", "source_skeleton", True, False, "source"),
        ("auto", "retarget", True, False, "auto"),
        ("source", "retarget", True, False, "source"),
        ("source", "source_skeleton", False, False, "auto"),
        ("transfer", "retarget", True, True, "transfer"),
        ("transfer", "retarget", True, False, "auto"),
    ],
)
def test_skin(monkeypatch, asked, mode, weighted, host_model, want):
    called: list[str] = []
    for name in ("source", "auto", "transfer"):
        monkeypatch.setattr(build.skins, name, lambda *a, n=name, **k: called.append(n) or [])
    monkeypatch.setattr(build.skins, "weld", lambda *a, **k: called.append("weld"))
    h = build.Host(Pac(), Skeleton(), pmo.Pmo() if host_model else None, fu.Anim())
    _, used = build.skin([part(weighted)], Build(skin=asked), binding(mode), h)
    assert used == want
    assert called == (["auto", "weld"] if want == "auto" else [want])


def test_binding_retarget(monkeypatch):
    monkeypatch.setattr(build.rigs, "from_host", lambda host: rig([2, 1], 3))
    match = {0: 5, 1: None, 2: 5, 3: None}
    monkeypatch.setattr(build.retarget, "match", lambda donor, host: match)
    b = build.binding(Build(), donor(6, 3), host(), 3)
    assert (b.mode, b.bone_of, b.joint_of, b.dead) == ("retarget", match, {5: 0}, {1})


def test_animation(monkeypatch):
    loc_y = next(bit for bit, kind in CHANNEL_BITS.items() if kind == ("loc", 1))
    lifted = Clip([Track([Channel(loc_y, [Keyframe(0, 0)])]), Track()])
    d = donor(3, 2)
    d.clips[1] = lifted
    seen = {}
    monkeypatch.setattr(build.motion, "build", lambda *a: seen.update(args=a) or fu.Anim())
    bind = binding("retarget", {0: 1, 1: None, 2: 2})
    build.animation(d, host(), bind, {1: 0, 2: 1}, layout.Layout({4: 1}), 2.0)
    clips, entries, _, streams, track_of, keep = seen["args"]
    assert (entries, streams, track_of, keep) == ({4: 1}, [2, 1], {0: 0, 2: 1}, True)
    assert clips[1].tracks[0].channels[0].keyframes[0].value == 32


@pytest.mark.parametrize("name", sorted(BUILT))
def test_port(data, name):
    built = build.build(manifest.load(PORTS / f"{name}.toml"), data)
    assert (built.summary.mode, built.summary.skin) == ("source_skeleton", "source")
    assert built.summary.size == len(built.pac)
    assert built.summary.placed == built.summary.clips == len(built.layout.entries)
    assert len(list(verify.Port(built.pac).distinct())) == built.summary.clips
    assert hashlib.sha256(built.pac).hexdigest() == BUILT[name]


def test_cli(data, tmp_path):
    toml = PORTS / "brute_tigrex.toml"
    games = ["--data", str(data.fu.root), "--p3rd-data", str(data.p3rd.root)]
    out = tmp_path / "built" / "out.bin"
    out.parent.mkdir()
    assert main(["build", str(toml), "-o", str(out), *games]) == 0
    lib = tmp_path / "lib"
    assert main(["inject", str(toml), "--dir", str(tmp_path), "--lib", str(lib), *games]) == 0
    m = manifest.load(toml)
    port = m.port
    assert (tmp_path / port.pac).read_bytes() == out.read_bytes()
    assert (tmp_path / port.orig).read_bytes() == data.fu.read(port.host_frame)
    module = layout.module_name(m)
    assert (lib / module).read_text() == (out.parent / module).read_text()
    assert "  charge = 61,  -- MHP3rd 61\n" in (lib / module).read_text()
