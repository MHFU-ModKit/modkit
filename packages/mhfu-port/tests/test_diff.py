# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import json

import numpy as np
from mhfu import files
from mhfu.files import Extracted
from mhfu_port import cli
from mhfu_port.diff import diff
from mhp_formats import fu, pmo
from mhp_formats.anim import Channel, Clip, Keyframe, Track
from mhp_formats.pac import Pac
from mhp_formats.psp.vtype import BITS8, BITS16, VertexType, Vertices, quantize_vertices
from mhp_formats.skeleton import Bone, Skeleton

POINTS = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0), (0.0, 0.0, 10.0)]
TRIANGLES = [(0, 1, 2), (0, 2, 3), (0, 3, 4)]
VTYPE = VertexType(texture=BITS16, position=BITS16, weight=BITS8, weight_count=2)


def build(
    points=POINTS,
    triangles=TRIANGLES,
    weights=None,
    parents=(-1, 0),
    loop_start=0.0,
    value=100,
    tail=b"",
    textures=b"tex",
    uvs=None,
) -> bytes:
    weights = weights or [(1.0, 0.0)] * len(points)
    uvs = uvs or [(x / 10, y / 10) for x, y, _ in points]
    vertices = quantize_vertices(VTYPE, points, (100.0,) * 3, uvs=uvs, weights=weights)
    group = pmo.Group(
        pmo.Block.build(vertices, triangles), 0, [pmo.BoneSlot(0, 0), pmo.BoneSlot(1, 1)]
    )
    model = pmo.Pmo([pmo.Mesh([group], [0])], [pmo.Material()], (100.0,) * 3, 100.0)
    skeleton = Skeleton([Bone(parent=p) for p in parents], [0, len(parents)])
    clip = Clip([Track([Channel(0x008, [Keyframe(0, 0), Keyframe(value, 10)])])], 1, loop_start)
    anim = fu.Anim([[clip, None], [None, None]])
    return Pac(
        [skeleton.to_bytes(), model.to_bytes(), textures, anim.to_bytes()], 16, tail
    ).to_bytes()


def kinds(old: bytes, new: bytes) -> dict[str, int]:
    """Items per model finding kind."""
    out: dict[str, int] = {}
    for f in diff(old, new).findings:
        if not f.layout:
            out[f.kind] = out.get(f.kind, 0) + f.count
    return out


def test_same():
    report = diff(build(), build())
    assert not report
    assert report.text() == "no differences"


def test_restrip_is_layout():
    order = [3, 0, 4, 2, 1]
    points = [POINTS[i] for i in order]
    at = {old: new for new, old in enumerate(order)}
    triangles = [(at[b], at[c], at[a]) for a, b, c in reversed(TRIANGLES)]
    report = diff(build(), build(points, triangles))
    assert report
    assert all(f.layout for f in report.findings)


def test_triangle_kinds():
    new = [(0, 2, 1), (0, 3, 4), (0, 3, 4)]
    assert kinds(build(), build(triangles=new)) == {
        "pmo.triangles.mirrored": 1,
        "pmo.triangles.duplicated": 1,
        "pmo.triangles.missing": 1,
    }


def test_added():
    old = [TRIANGLES[0], TRIANGLES[2]]
    assert kinds(build(triangles=old), build()) == {"pmo.triangles.added": 1}


def test_moved_vertex():
    points = [*POINTS[:4], (0.0, 0.5, 10.0)]
    report = diff(build(), build(points, uvs=[(x / 10, y / 10) for x, y, _ in POINTS]))
    [found] = [f for f in report.findings if not f.layout]
    assert (found.kind, found.where, found.count) == ("pmo.positions", (0,), 1)


def test_scale_drift():
    points = [(x * 1.0005, y, z) for x, y, z in POINTS]
    uvs = [(x / 10, y / 10) for x, y, _ in POINTS]
    [found] = diff(build(), build(points, uvs=uvs)).findings
    assert (found.kind, found.count) == ("pmo.positions", 2)
    assert "(1.00 steps)" in found.detail


def test_extra_vertex():
    points, triangles = [*POINTS, (5.0, 5.0, 5.0)], [*TRIANGLES, (2, 1, 5)]
    assert kinds(build(), build(points, triangles)) == {
        "pmo.vertices": 1,
        "pmo.triangles.added": 1,
    }


def test_weights():
    weights = [(1.0, 0.0)] * 4 + [(0.0, 1.0)]
    assert kinds(build(), build(weights=weights)) == {"pmo.weights": 1}


def test_skeleton():
    [found] = diff(build(), build(parents=(-1, -1))).findings
    assert (found.kind, found.where) == ("skeleton.parent", (1,))


def test_animation():
    report = diff(build(), build(loop_start=4.0, value=99))
    assert report.counts() == {"anim.loop_start": 1, "anim.keyframes": 1}
    assert list(report.clips()) == [(0, 0)]


def test_clip_gone():
    old, new = Pac.from_bytes(build()), Pac.from_bytes(build())
    anim = fu.Anim.from_bytes(new.entries[3])
    anim.streams[0][0] = None
    new.entries[3] = anim.to_bytes()
    assert diff(old.to_bytes(), new.to_bytes()).counts() == {"anim.clip.missing": 1}


def test_pac_and_textures():
    report = diff(build(), build(tail=bytes(16), textures=b"TEX"))
    assert report.counts() == {"pac.tail": 1, "tmh": 1}


def test_text_limit():
    text = diff(build(parents=(-1, 0, 1)), build(parents=(-1, -1, 0))).text(limit=1)
    assert text.splitlines() == [
        "2 model findings, 0 layout-only",
        "model:",
        "  skeleton.parent: 2 findings, 2 items",
        "    b1: 0 -> -1",
        "    ... 1 more",
    ]


def test_cli(tmp_path, capsys):
    old, new = tmp_path / "old.bin", tmp_path / "new.bin"
    old.write_bytes(build())
    new.write_bytes(build(parents=(-1, -1)))
    assert cli.main(["diff", str(old), str(new), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"] == {"skeleton.parent": 1}


def test_native_restrip(mhfu_data):
    """Every block of the Tigrex with its vertices shuffled and re-stripped: layout only."""
    raw = Extracted.find(mhfu_data).read(files.monster_pac(75))
    pac = Pac.from_bytes(raw)
    model = pmo.Pmo.from_bytes(pac.entries[1])
    rng = np.random.default_rng(0)
    for group in model.groups():
        v = group.block.vertices
        order = [int(i) for i in rng.permutation(len(v))]
        at = {old: new for new, old in enumerate(order)}
        rows = [[r[i] for i in order] if r else r for r in (v.weight, v.texture, v.normal)]
        colors = [v.color[i] for i in order] if v.color else v.color
        shuffled = Vertices(
            v.vtype, rows[0], rows[1], colors, rows[2], [v.position[i] for i in order]
        )
        triangles = [(at[a], at[b], at[c]) for a, b, c in group.block.triangles()]
        group.block = pmo.Block.build(shuffled, [t for t in triangles if len(set(t)) == 3])
    pac.entries[1] = model.to_bytes()
    assert not diff(raw, raw)
    report = diff(raw, pac.to_bytes())
    assert report
    assert all(f.layout for f in report.findings)
