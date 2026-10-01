# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import json
import tomllib
from pathlib import Path

import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core.edit import EditSession, Selection, compose
from mhfu_studio.map.core.scene import MapScene
from mhfu_studio.map.document import DocumentError, MapDocument
from mhfu_studio.stage import mesh

OP = {
    "op": "transform",
    "sub": 0,
    "group": 1,
    "vertices": [0, 1, 2, 3],
    "pivot": [0, 0, 0],
    "by": [100.0, 0.0, 0.0],
    "rotate": [0, 0, 0],
    "scale": [1, 1, 1],
}


def test_round_trip(tmp_path: Path):
    doc = MapDocument("camp", 11, tmp_path / "camp", "the crate at the spawn", {"author": "x"})
    doc.ensure_stage(98).ops.append(OP)
    doc.ensure_stage(107, "st098.json")
    assert doc.stage(107).ops is doc.stage(98).ops and doc.dirty
    p = doc.save()
    assert not doc.dirty and not (tmp_path / "camp" / "st107.json").exists()
    assert tomllib.loads(p.read_text()) == {
        "map": {"name": "camp", "row": 11, "description": "the crate at the spawn", "author": "x"},
        "stage": [{"number": 98, "ops": "st098.json"}, {"number": 107, "ops": "st098.json"}],
    }
    back = MapDocument.load(tmp_path / "camp")
    assert (back.name, back.row, back.description, back.extra) == (
        "camp",
        11,
        doc.description,
        {"author": "x"},
    )
    assert [s.number for s in back.stages] == [98, 107] and back.stage(98).ops == [OP]
    assert back.stage(107).ops is back.stage(98).ops
    assert back.shared_lists() == {"st098.json": [98, 107]} and not back.dirty
    back.stage(107).ops.append(dict(OP))
    back.touch()
    assert back.dirty and len(back.stage(98).ops) == 2


def test_save_as(tmp_path: Path):
    doc = MapDocument.untitled(0)
    assert doc.path is None and not doc.dirty
    with pytest.raises(ValueError, match="no folder"):
        doc.save()
    p = doc.save(tmp_path / "village" / "map.toml")
    assert p == tmp_path / "village" / "map.toml" and doc.name == "village"
    assert MapDocument.load(p).row == 0


def test_load_refuses(tmp_path: Path):
    d = tmp_path / "x"
    d.mkdir()
    for text in (
        "[map]\nrow = 1\n",
        '[map]\nname = "x"\nrow = "one"\n',
        '[map]\nname = "x"\n[[stage]]\nnumber = 98\nops = "nope.json"\n',
        '[map]\nname = "x"\n[[stage]]\nnumber = 98\n[[stage]]\nnumber = 98\n',
        '[map]\nname = "x"\n[[stage]]\nops = "a.json"\n',
        "[map\n",
    ):
        (d / "map.toml").write_text(text)
        (d / "st098.json").write_text("[]")
        with pytest.raises(DocumentError):
            MapDocument.load(d)
    (d / "map.toml").write_text('[map]\nname = "x"\n[[stage]]\nnumber = 98\n')
    (d / "st098.json").write_text('{"a": 1')
    with pytest.raises(DocumentError):
        MapDocument.load(d)
    with pytest.raises(DocumentError):
        MapDocument.load(tmp_path / "missing")


def test_findings_without_evidence(tmp_path: Path):
    doc = MapDocument("v", 11, tmp_path / "v")
    doc.ensure_stage(98).ops.extend(
        [
            OP,
            {"op": "bogus"},
            {"op": "pack", "group": 9},
            {"op": "transform", "group": 1, "by": [1, 0, 0]},
            {"op": "texture"},
            {"op": "pack", "group": 9, "obj": "missing.obj"},
        ]
    )
    found = doc.findings()
    codes = {f.code for f in found}
    assert {
        "unknown-op",
        "no-obj",
        "no-selector",
        "no-slot",
        "missing-asset",
        "no-evidence",
    } <= codes
    bogus = next(f for f in found if f.code == "unknown-op")
    assert bogus.where == "st098 op 1" and bogus.target == (None, 1)
    empty = MapDocument("e", None, tmp_path / "e")
    assert [f.code for f in empty.findings()] == ["no-stages"]
    assert MapDocument.untitled().findings() == []


def test_findings_with_evidence(game: Extracted, doc_dir: Path):
    doc = MapDocument("v", 0, doc_dir)
    doc.game = game
    (doc_dir / "ball.obj").write_text("v 0 0 0\nv 1 0 0\nv 0 0 1\n" + "f 1 2 3\n" * 200)
    doc.ensure_stage(139).ops.extend(
        [
            OP,
            {"op": "transform", "sub": 0, "group": 1, "vertices": [99999], "by": [1, 0, 0]},
            {"op": "transform", "sub": 0, "group": 77, "vertices": [0], "by": [1, 0, 0]},
            {"op": "pack", "group": 1, "obj": "ball.obj"},
            {"op": "texture", "slot": 99, "rgb": [1, 2, 3]},
        ]
    )
    doc.ensure_stage(140)
    found = doc.findings()
    by_op = {
        f.target[1]: f.code for f in found if f.level == "error" and f.target and f.target[0] == 139
    }
    assert by_op == {1: "vertex-range", 2: "refused", 3: "did-not-fit", 4: "refused"}
    assert any(f.code == "no-stage" and f.target == (140, None) for f in found)
    assert "no-evidence" not in {f.code for f in found}


def test_dirty_follows_the_session(scene: MapScene, doc_dir: Path):
    doc = MapDocument("d", 0, doc_dir)
    entry = doc.ensure_stage(139)
    doc.save()
    sess = EditSession(scene, entry.ops, doc_dir)
    doc.session = sess
    assert not doc.dirty and not doc.can_undo()
    sess.apply_now(Selection.object(scene, (0, 1), 0), compose(by=(10, 0, 0)))
    assert doc.dirty and doc.can_undo() and len(entry.ops) == 1
    doc.undo()
    assert not doc.dirty and doc.can_redo() and not entry.ops
    doc.redo()
    doc.save()
    assert (
        not doc.dirty and json.loads((doc_dir / "st139.json").read_text())[0]["op"] == "transform"
    )


def test_export(game: Extracted, scene: MapScene, doc_dir: Path, tmp_path: Path):
    sess = EditSession(scene)
    sel = Selection.object(scene, (0, 1), 1)
    (op,) = sess.apply_now(sel, compose(pivot=sel.centroid(scene), by=(55, 19, -125)))
    op["solid"] = "box"
    doc = MapDocument("e", 0, doc_dir)
    doc.game = game
    doc.ensure_stage(139).ops.extend([op, {"op": "texture", "slot": 1, "rgb": [9, 9, 9]}])
    doc.save()
    out = tmp_path / "out"
    man = doc.export(out)
    (rec,) = man["stages"]
    assert rec["mesh"]["0"]["safe"] and rec["mesh"]["0"]["applied"] == 1 and "2" not in rec["mesh"]
    # the crate is flat: its box collider is a top and a bottom
    assert rec["collision"]["added"] == 4 and rec["texture"]["size"] == scene.sub_sizes[1]
    want = mesh.edit(scene.file, 0, doc.stage(139).ops, doc_dir).data
    assert (out / "st139_sub0.bin").read_bytes() == want
    col = json.loads((out / "st139_collision.json").read_text())
    assert len(col["added"]) == 4 and all(a["chunk"] in (0, 1) for a in col["added"])
    assert json.loads((out / "export.json").read_text())["name"] == "e"
