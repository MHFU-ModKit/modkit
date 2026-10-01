# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The workspace and its panels headless: the fake imgui scripts the clicks and keys."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.core.edit import COLLISION, OBJECT, CollisionSelection, Selection
from mhfu_studio.map.document import MapDocument
from mhfu_studio.map.tools import MOVE, SELECT
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell import gizmo
from mhfu_studio.shell.workspace import Gesture, View, registered

VIEW = View((0.0, 0.0), (320, 200), hovered=True, active=True)


@pytest.fixture
def ws(gl: Any, imgui: Any, game: Extracted, atlas: Atlas) -> Iterator[MapWorkspace]:
    w = MapWorkspace(game, atlas)
    w.setup(gl)
    assert w.vp is not None
    w.vp.resize((320, 200))
    yield w
    w.close()


def draw_all(w: MapWorkspace) -> None:
    w.frame(1 / 60)
    w.toolbar()
    assert w.vp is not None
    w.vp.draw()
    w.input(VIEW)
    w.overlay(VIEW)
    for p in w.panels():
        p.draw()


def panel(w: MapWorkspace, label: str) -> Any:
    return next(p.draw.__self__ for p in w.panels() if p.label == label)  # type: ignore[attr-defined]


def test_registered():
    assert registered()["map"] is MapWorkspace


def test_without_data(imgui: Any, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MHFU_DATA", raising=False)
    w = MapWorkspace()
    assert w.atlas is None and "MHFU_DATA" in w.status() and not w.load_stage(98)
    for p in w.panels():
        p.draw()
    assert "MHFU_DATA" in w.hud()


def test_opens_on_the_village(ws: MapWorkspace, imgui: Any):
    assert ws.scene is not None and ws.scene.stage == 139 and ws.row == 0
    assert "st139 Pokke village" in ws.status() and ws.document.path is None
    assert [(s.name, s.ratio) for s in ws.layout()] == [
        ("Left", 0.2),
        ("Right", 0.24),
        ("Bottom", 0.24),
    ]
    assert [p.label for p in ws.panels()][:4] == ["Map", "View", "Document", "Game"]
    draw_all(ws)
    assert "Select (Q)" in imgui.imgui.shown and ws.hud().startswith("st139  Pokke village")
    assert ws.can_open(Path("x/map.toml")) and not ws.can_open(Path("x/a.pac"))


def test_keys_and_gestures(ws: MapWorkspace, imgui: Any):
    im = imgui.imgui
    assert ws.input(VIEW) == Gesture.ORBIT  # a left-drag in the select tool is a box
    im.io.key_alt = True
    assert ws.input(VIEW) == Gesture.NONE
    im.io.key_alt = False
    im.press("w")
    ws.input(VIEW)
    assert ws.tools.tool == MOVE and ws.input(VIEW) == Gesture.NONE
    im.press("_4")
    ws.input(VIEW)
    assert ws.tools.kind == COLLISION and ws.vp is not None and ws.vp.show_collision
    im.press("q")
    im.press("_2")
    ws.input(VIEW)
    assert (ws.tools.tool, ws.tools.kind) == (SELECT, OBJECT)
    im.io.want_text_input = True
    im.press("w")
    ws.input(VIEW)
    assert ws.tools.tool == SELECT


def test_pick_and_box(ws: MapWorkspace):
    assert ws.vp is not None and ws.scene is not None
    ws.vp.camera.look("top")
    g = ws.scene.group(0, 1)
    centre = g.positions[g.component_vertices(1)].mean(0)
    x, y, _ = ws.vp.camera.project(centre, VIEW.size)[0]
    ws.tools.click((x, y), VIEW.size, shift=False)
    assert ws.selection.parts == {(0, 1): [1]} and ws.selected_group is None
    ws.tools.click((x, y), VIEW.size, shift=True)
    assert ws.selection.empty
    ws.tools.box_select((0, 0, *VIEW.size), VIEW.size, shift=False)
    assert (0, 1) in ws.selection.vertices and ws.selection.kind == OBJECT
    ws.tools.set_kind(COLLISION)
    ws.tools.box_select((0, 0, *VIEW.size), VIEW.size, shift=False)
    assert len(ws.col_sel) == 6
    ws.tools.click((x, y), VIEW.size, shift=False)
    assert ws.col_sel.tris and ws.col_sel.tris[0][0] == 1
    ws.tools.click((1.0, 1.0), VIEW.size, shift=False)
    assert ws.col_sel.empty


def test_gizmo_commit(ws: MapWorkspace, monkeypatch: pytest.MonkeyPatch):
    assert ws.scene is not None
    ws.tools.select(Selection.object(ws.scene, (0, 1), 1))
    ws.tools.set_tool(MOVE)
    assert ws.tools.gizmo_visible and not ws.wants_mouse()
    start = ws.selection.centroid(ws.scene)
    poses = iter([(0.0, False), (0.0, True), (300.0, True), (300.0, False)])

    def manipulate(cam: Any, view: View, matrix: np.ndarray, op: str, **kw: Any) -> Any:
        dx, using = next(poses)
        m = matrix.copy()
        m[0, 3] = start[0] + dx
        return gizmo.Manipulation(m, using, True)

    monkeypatch.setattr(gizmo, "manipulate", manipulate)
    assert ws.input(VIEW) == Gesture.ORBIT | Gesture.PAN
    ws.input(VIEW)
    ws.input(VIEW)
    assert ws.session is not None and ws.session.previewing
    ws.input(VIEW)
    assert len(ws.session.ops) == 1 and ws.session.ops[0]["by"][0] == pytest.approx(300.0)
    ws.frame(0.0)
    assert ws.selection.centroid(ws.scene)[0] == pytest.approx(start[0] + 300.0, abs=1.0)
    assert ws.document.stage(139) is not None and ws.document.dirty
    assert "applied: transform" in ws.message
    ws.document.undo()
    ws.refresh()
    ws.frame(0.0)
    assert not ws.session.ops


def test_open_and_reveal(ws: MapWorkspace, game: Extracted, doc_dir: Path):
    d = MapDocument("d", 0, doc_dir)
    d.ensure_stage(98).ops.extend(
        [
            {"op": "transform", "sub": 0, "group": 1, "vertices": [0, 1, 2], "by": [0, 9, 0]},
            {"op": "collision", "group": None, "chunk": 0, "tri": 1, "flags": {"material": 10}},
        ]
    )
    d.save()
    ws.open(doc_dir)
    assert ws.document.directory == doc_dir and ws.scene is not None and ws.scene.stage == 139
    assert ws.session is not None and ws.session.base_dir == doc_dir and not ws.session.ops
    ws.reveal((98, 0))
    assert ws.scene.stage == 98 and ws.selection.n_vertices == 3 and "op 0" in ws.message
    ws.reveal((98, 1))
    assert ws.col_sel.tris == [(0, 1)] and ws.tools.kind == COLLISION
    ws.reveal("nothing")
    ws.reveal((None, 0))
    ws.reveal((131, 0))
    assert ws.scene.stage == 98
    ws.load_stage(98)
    assert len(ws.session.ops) == 2 and "2 op(s) replayed" in ws.message
    assert not ws.load_stage(131) and ws.load_error


def test_panels_edit(ws: MapWorkspace, imgui: Any, doc_dir: Path):
    im = imgui.imgui
    sc, sess = ws.scene, ws.session
    assert sc is not None and sess is not None
    ws.tools.select(Selection.object(sc, (0, 1), 1))
    sel = panel(ws, "Selection")
    sel.by = [100.0, 0.0, 0.0]
    im.click("apply")
    sel.draw()
    assert len(sess.ops) == 1 and sess.ops[0]["by"] == [100.0, 0.0, 0.0]
    im.click("reset fields")
    sel.draw()
    assert sel.by == [0.0, 0.0, 0.0]
    doc_panel = panel(ws, "Document")
    doc_panel.new(doc_dir, "made")
    im.click("save")
    doc_panel.draw()
    assert (doc_dir / "map.toml").is_file() and not ws.document.dirty
    assert ws.document.stage(139) is not None and ws.document.stage(139).ops is sess.ops
    add = panel(ws, "Add")
    ws.tools.select(Selection.object(sc, (0, 1), 1))
    im.click("add")
    add.draw()
    assert sess.ops[-1]["op"] == "pack" and (doc_dir / "assets" / "box_1.obj").is_file()
    assert "added box_1.obj" in add.message and ws.selection.n_vertices == 24
    im.click("remove the selection")
    add.draw()
    assert sess.ops[-1]["op"] == "clear"
    col = panel(ws, "Collision")
    ws.tools.set_kind(COLLISION)
    ws.tools.select_collision(CollisionSelection([(0, 1)]))
    im.click("climbable (10)")
    col.draw()
    assert ws.scene is not None and (0, 1) in ws.scene.climbable()
    im.click("delete")
    col.draw()
    assert not ws.scene.chunk(0).alive[1]
    tex = panel(ws, "Textures")
    ws.tex_target = 1
    im.click("flat colour")
    tex.draw()
    assert sess.ops[-1] == {"op": "texture", "slot": 1, "rgb": [128, 128, 128]}
    groups = panel(ws, "Groups")
    ws.select_group((0, 1))
    groups.draw()
    groups.slot = 0
    im.click("apply material")
    groups.draw()
    assert sess.ops[-1]["op"] == "material" and sess.ops[-1]["texture"] == 0
    im.click("validate")
    doc_panel.draw()
    assert "finding(s)" in doc_panel.message
    doc_panel.export(doc_dir / "out")
    assert (doc_dir / "out" / "export.json").is_file() and "exported" in doc_panel.message
    draw_all(ws)


def test_toolbar_and_view(ws: MapWorkspace, imgui: Any):
    im = imgui.imgui
    im.click("Rotate (E)", "face (3)", "undo")
    ws.toolbar()
    assert (ws.tools.tool, ws.tools.kind) == ("rotate", "face") and "nothing to undo" in ws.message
    assert ws.vp is not None
    im.click("hunter's eye")
    panel(ws, "View").draw()
    assert ws.vp.camera.pitch == 0.0
    panel(ws, "Map").draw()
    assert "exits of st139:" in im.shown


def test_game_panel(ws: MapWorkspace, doc_dir: Path, monkeypatch: pytest.MonkeyPatch):
    import subprocess

    game = panel(ws, "Game")
    game.draw()
    assert game.catch == 0.0  # the village: no catch
    ws.document.save(doc_dir)
    assert ws.session is not None
    ws.session.ops.append(
        {"op": "collision", "group": None, "chunk": 0, "tri": 0, "flags": {"material": 9}}
    )
    cmd = game.command(["--collision"])
    assert cmd[3:6] == ["map", "inject", str(doc_dir)] and "--hold" in cmd
    assert cmd[cmd.index("--stage") + 1] == "139" and "--data" in cmd
    started: list[list[str]] = []

    class Proc:
        stdout = iter(["line one\n"])

        def __init__(self, cmd: list[str], **kw: Any) -> None:
            started.append(cmd)

        def poll(self) -> int:
            return 0

        def wait(self) -> int:
            return 0

    monkeypatch.setattr(subprocess, "Popen", Proc)
    game.run(["--mesh"])
    assert started and started[0][-1] == "--mesh"
    for _ in range(100):
        if "[exit 0]" in game.lines:
            break
        import time

        time.sleep(0.01)
    assert game.lines[-2:] == ["line one", "[exit 0]"]
