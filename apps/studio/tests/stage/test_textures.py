# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

from mhfu.files import Extracted
from mhfu_studio.stage import textures as T
from mhfu_studio.stage.file import TEXTURES, StageFile
from mhp_formats.tmh import Tmh, TmhImage
from PIL import Image


def _images(data: bytes) -> list[TmhImage]:
    return Tmh.from_bytes(data).images


def test_rgb_keeps_alpha(st: StageFile, assets: Path):
    e = T.build(st, [{"op": "texture", "slot": 0, "rgb": [255, 0, 255]}], assets)
    assert len(e.data) == len(e.original) and e.applied == 1 and not e.findings
    before = _images(e.original)[0].decode()
    after = _images(e.data)[0].decode()
    assert after[3::4] == before[3::4]
    assert set(zip(after[0::4], after[1::4], after[2::4], strict=True)) == {(255, 0, 255)}
    assert _images(e.data)[1] == _images(e.original)[1]


def test_png_is_fitted(st: StageFile, assets: Path):
    Image.new("RGBA", (4, 4), (10, 200, 30, 255)).save(assets / "green.png")
    e = T.build(st, [{"op": "texture", "slot": 1, "png": "green.png"}], assets)
    img = _images(e.data)[1]
    assert (img.width, img.height) == (32, 8)
    assert set(zip(*(img.decode()[k::4] for k in range(4)), strict=True)) == {(10, 200, 30, 255)}


def test_keep_palette(st: StageFile, assets: Path):
    Image.new("RGBA", (16, 8), (3, 3, 250, 255)).save(assets / "blue.png")
    op = {"op": "texture", "slot": 0, "png": "blue.png", "keep_palette": True}
    e = T.build(st, [op], assets)
    old, new = _images(e.original)[0], _images(e.data)[0]
    assert new.clut == old.clut and new.pixels != old.pixels
    assert "palette kept" in e.log[0]


def test_from_another_stage(st: StageFile, game: Extracted, assets: Path):
    e = T.build(st, [{"op": "texture", "slot": 0, "from": {"stage": 2, "slot": 0}}], assets)
    donor = _images(StageFile.read(game, 2).entry(TEXTURES))[0]
    assert _images(e.data)[0].decode() == donor.decode()
    lone = StageFile(1, st.data)
    refused = T.build(lone, [{"op": "texture", "slot": 0, "from": {"stage": 2, "slot": 0}}], assets)
    assert refused.findings[0].code == "refused" and refused.data == refused.original


def test_refusals(st: StageFile, assets: Path):
    ops = [
        {"op": "texture", "slot": 5, "rgb": [1, 2, 3]},
        {"op": "texture", "slot": 0, "png": "none.png"},
        {"op": "texture", "slot": 1, "rgb": [1, 2, 3]},
        {"op": "move", "group": 0, "vertices": [0], "by": [1, 1, 1]},
    ]
    e = T.build(st, ops, assets)
    assert [(f.code, f.target) for f in e.findings] == [("refused", (1, 0)), ("refused", (1, 1))]
    assert e.applied == 1 and e.data != e.original
    assert T.build(st, ops[3:], assets).data == st.entry(TEXTURES)


def test_usage(st: StageFile):
    use = T.usage(st)
    assert use[0].groups == [(0, 0), (0, 2), (2, 0)] and use[1].groups == [(0, 1)]
    assert use[1].triangles == 3 * 2 * 3 + 1


def test_export(st: StageFile, tmp_path: Path):
    paths = T.export(st, tmp_path / "out")
    assert [p.name for p in paths] == ["st001_tex00.png", "st001_tex01.png"]
    with Image.open(paths[1]) as im:
        assert im.size == (32, 8)
    assert len(T.export(st, tmp_path / "one", slot=1)) == 1


def test_verify(game: Extracted):
    v = T.verify(game, [1, 2])
    assert (v.stages, v.images, v.exact, v.pixels, v.failed) == (2, 4, 4, 4, [])
