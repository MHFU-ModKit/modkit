# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import json
from pathlib import Path

import pytest
from mhfu_studio.stage import ops as O
from mhfu_studio.stage.file import StageFile
from PIL import Image

GOOD = [
    {
        "op": "transform",
        "sub": 0,
        "group": 0,
        "vertices": [0, 1],
        "pivot": [0, 0, 0],
        "by": [1, 2, 3],
        "rotate": [0, 90, 0],
        "scale": [1, 1, 1],
        "matrix": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        "selection": {"kind": "group", "parts": [0]},
    },
    {
        "op": "move",
        "group": 0,
        "sphere": [0, 0, 0, 100],
        "by": [10, 0, 0],
        "solid": True,
        "collision": {"chunks": [1]},
    },
    {"op": "move", "group": None, "box": [0, 0, 0, 1, 1, 1], "by": [0, 5, 0]},
    {
        "op": "pack",
        "group": 0,
        "obj": "quad.obj",
        "prims": [0, 1],
        "uv": "planar",
        "colour": [10, 20, 30],
        "collapse": True,
        "reindex": True,
        "solid": "box",
        "chunk": None,
    },
    {"op": "sculpt", "group": 1, "obj": "tetra.obj", "first": 1, "collapse": False},
    {"op": "replace", "sub": 2, "group": 0, "obj": "quad.obj", "uv": "obj"},
    {"op": "clear", "group": 0, "prims": [3], "solid": True, "box": [0, 0, 0, 9, 9, 9]},
    {"op": "material", "group": 1, "rgba": [1, 2, 3, 4], "ambient": [5, 6, 7, 8], "texture": 1},
    {
        "op": "collision",
        "group": None,
        "chunk": 1,
        "tri": 0,
        "verts": [[0, 0, 0], [0, 0, 9], [9, 0, 0]],
        "flags": {"material": 10},
    },
    {"op": "collision", "chunk": 0, "tris": [0, 1], "delete": True},
    {"op": "collision", "group": None, "chunk": None, "add": [[[0, 0, 0], [0, 0, 9], [9, 0, 0]]]},
    {
        "op": "collision",
        "group": None,
        "solid_box": [0, 0, 0, 9, 9, 9],
        "inflate": 2,
        "flags": 0x10500,
    },
    {"op": "texture", "slot": 0, "png": "img.png", "keep_palette": True},
    {"op": "texture", "slot": 1, "from": {"stage": 2, "slot": 0}},
    {"op": "texture", "slot": 1, "rgb": [255, 0, 255]},
]


def _codes(ops: list[dict], **kw) -> list[str]:
    return [f.code for f in O.check(ops, **kw)]


def test_every_kind_passes():
    assert {o["op"] for o in GOOD} == set(O.KINDS)
    assert O.check(GOOD) == []


@pytest.mark.parametrize(
    ("op", "code"),
    [
        ({"op": "add", "group": 0, "obj": "x.obj"}, "retired-op"),
        ({"op": "climb", "group": None, "chunk": 0, "tri": 1}, "retired-op"),
        ({"op": "warp"}, "unknown-op"),
        ({"op": "move", "group": 0, "by": [1, 2]}, "bad-value"),
        ({"op": "move", "group": 0, "box": [0, 0, 0, 1, 1, 1]}, "no-offset"),
        ({"op": "move", "group": 0, "by": [0, 0, 0]}, "no-selector"),
        ({"op": "transform", "group": 0, "vertices": [0], "matrix": [1, 2, 3]}, "bad-value"),
        ({"op": "transform", "vertices": [0]}, "no-group"),
        ({"op": "transform", "group": None, "vertices": [0]}, "no-group"),
        ({"op": "pack", "group": 0}, "no-obj"),
        ({"op": "pack", "group": 0, "obj": "a.obj", "uv": "zero"}, "bad-value"),
        ({"op": "clear", "group": 0, "sub": 1}, "bad-sub"),
        ({"op": "clear", "group": -1}, "bad-value"),
        ({"op": "material", "group": 0, "texture": 300}, "bad-value"),
        ({"op": "collision", "group": 3, "tri": 0}, "not-collision-only"),
        ({"op": "collision", "chunk": 0}, "no-selector"),
        (
            {"op": "collision", "tris": [1, 2], "verts": [[0, 0, 0], [1, 0, 0], [0, 0, 1]]},
            "bad-value",
        ),
        ({"op": "collision", "tri": 1, "flags": {"material": 300}}, "bad-value"),
        ({"op": "collision", "tri": 1, "flags": {"colour": 3}}, "bad-value"),
        ({"op": "collision", "chunk": None, "tri": 1}, "bad-value"),
        ({"op": "texture", "png": "a.png"}, "no-slot"),
        ({"op": "texture", "slot": 0}, "no-source"),
        ({"op": "texture", "slot": 0, "png": "a.png", "rgb": [0, 0, 0]}, "no-source"),
        ({"op": "texture", "slot": 0, "rgb": [0, 0, 0], "group": 1}, "unknown-key"),
    ],
)
def test_faults(op: dict, code: str):
    assert code in _codes([op])


def test_levels_and_where():
    (f,) = O.check([{"op": "material", "group": 0}], where="st098")
    assert (f.level, f.code, f.where, f.target) == ("warning", "no-change", "st098 op 0", (None, 0))
    assert O.check([{"op": "move", "group": 0, "box": [0] * 6, "by": [0] * 3, "huh": 1}])[
        0
    ].level == ("warning")


def test_missing_assets(tmp_path: Path):
    (tmp_path / "quad.obj").write_text("")
    ops = [
        {"op": "pack", "group": 0, "obj": "quad.obj"},
        {"op": "texture", "slot": 0, "png": "no.png"},
    ]
    assert _codes(ops, base_dir=tmp_path) == ["missing-asset"]


def test_halves():
    assert [O.sub_of(o) for o in GOOD[:6]] == [0, 0, None, 0, 0, 2]
    assert O.sub_of(GOOD[8]) is None and O.sub_of({"op": "clear", "group": 0, "sub": 1}) is None
    assert [O.touches_collision(o) for o in GOOD[:6]] == [True, True, True, True, False, False]
    assert O.touches_collision(GOOD[8]) and not O.touches_collision(GOOD[12])
    assert O.touches_textures(GOOD[12]) and not O.touches_textures(GOOD[0])


def test_parse_and_load(tmp_path: Path):
    assert O.parse({"ops": [{"op": "texture"}]}) == [{"op": "texture"}]
    assert O.parse({"op": "texture"}) == [{"op": "texture"}]
    with pytest.raises(ValueError):
        O.parse([1, 2])
    path = tmp_path / "st001.json"
    path.write_text(json.dumps(GOOD))
    assert O.load(path) == GOOD
    path.write_text("[{")
    with pytest.raises(ValueError):
        O.load(path)


def test_vertex_range(st: StageFile, assets: Path):
    ops = [
        {"op": "transform", "group": 1, "vertices": [0, 15, 16, 40], "by": [1, 0, 0]},
        {"op": "move", "group": 1, "vertices": [3], "by": [1, 0, 0]},
        {"op": "move", "group": 1, "sphere": [0, 0, 0, 1], "by": [1, 0, 0]},
    ]
    (f,) = O.check(ops, base_dir=assets, stage=st)
    assert (f.level, f.code, f.target) == ("error", "vertex-range", (1, 0))
    assert f.message == "2 vertex ids past g1's 16 vertices"


def test_resized(st: StageFile, assets: Path):
    Image.new("RGBA", (16, 8)).save(assets / "fits.png")
    Image.new("RGBA", (4, 4)).save(assets / "small.png")
    ops = [
        {"op": "texture", "slot": 0, "png": "fits.png"},
        {"op": "texture", "slot": 0, "png": "small.png"},
    ]
    (f,) = O.check(ops, base_dir=assets, stage=st)
    assert (f.level, f.code, f.target) == ("info", "resized", (1, 1))
    assert f.message == "small.png is 4x4; slot 0 is 16x8, so it is resized"


def test_evidence(st: StageFile, assets: Path):
    ops = [
        {"op": "transform", "group": 7, "vertices": [0]},
        {"op": "collision", "chunk": 1, "tri": 99, "flags": {"material": 10}},
        {"op": "texture", "slot": 9, "rgb": [1, 2, 3]},
        {"op": "pack", "group": 1, "obj": "tetra.obj", "prims": [3]},
    ]
    found = O.check(ops, base_dir=assets, stage=st)
    assert [(f.code, f.target) for f in found] == [
        ("refused", (1, 0)),
        ("did-not-fit", (1, 3)),
        ("no-triangle", (1, 1)),
        ("refused", (1, 2)),
    ]
    assert _codes([{"op": "warp"}, *ops], stage=st) == ["unknown-op"]
