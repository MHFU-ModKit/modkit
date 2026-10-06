# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The viewport tools on synthetic pointer and key events, over the synthetic village."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core.atlas import Atlas
from mhfu_studio.map.core.edit import COLLISION, FACE, GROUP, OBJECT, Selection
from mhfu_studio.map.document import MapDocument
from mhfu_studio.map.tools import (
    GROUPS,
    LAYER,
    LOCAL,
    MOVE,
    OPTIONS,
    PICK,
    POINT,
    ROTATE,
    SCALE,
    SNAP,
    TOOL,
)
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.input import Button, Key, Mod, Pointer
from mhfu_studio.shell.manipulator import ARM_PX, Handle, hit, world_per_px
from mhfu_studio.shell.overlay import Ink, Recorder
from mhfu_studio.shell.studio import Studio
from mhfu_studio.shell.workspace import Gesture, registered

SIZE = (320, 200)
Pt = tuple[float, float]


@pytest.fixture
def ws(gl: Any, game: Extracted, atlas: Atlas) -> Iterator[MapWorkspace]:
    w = MapWorkspace(game, atlas)
    w.setup(gl)
    assert w.vp is not None
    w.vp.resize(SIZE)
    w.vp.camera.look("top")
    yield w
    w.close()


def ev(kind: str, at: Pt, button: Button = Button.LEFT, mods: Mod = Mod.NONE) -> Pointer:
    held = button if kind in ("press", "move") else Button.NONE
    changed = Button.NONE if kind == "move" else button
    return Pointer(kind, at[0], at[1], SIZE, button=changed, buttons=held, mods=mods)  # type: ignore[arg-type]


def click(w: MapWorkspace, at: Pt, mods: Mod = Mod.NONE) -> Gesture:
    got = w.pointer(ev("press", at, mods=mods))
    w.pointer(ev("release", at, mods=mods))
    return got


def drag(w: MapWorkspace, a: Pt, b: Pt, mods: Mod = Mod.NONE, rec: Recorder | None = None) -> None:
    w.pointer(ev("press", a, mods=mods))
    for t in (0.5, 1.0):
        w.pointer(ev("move", (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])), mods=mods))
    if rec is not None:
        w.paint(rec)
    w.pointer(ev("release", b, mods=mods))
    w.frame(0.0)


def screen(w: MapWorkspace, p: Any) -> Pt:
    assert w.vp is not None
    x, y, _ = w.vp.camera.project(np.asarray(p, np.float64), SIZE)[0]
    return float(x), float(y)


def crate(w: MapWorkspace) -> Pt:
    """The second crate's centre on screen."""
    assert w.scene is not None
    g = w.scene.group(0, 1)
    return screen(w, g.positions[g.component_vertices(1)].mean(0))


def arm(w: MapWorkspace, direction: Any, t: float = 0.7) -> tuple[Any, Pt]:
    """The pivot, and a point `t` of an arm's length from it along `direction`."""
    assert w.vp is not None
    pivot = w.tools.pose()[:3, 3]
    reach = ARM_PX * world_per_px(w.vp.camera, pivot, SIZE[1])
    return pivot, screen(w, pivot + t * reach * np.asarray(direction, np.float64))


def pick_crate(w: MapWorkspace, tool: str) -> None:
    assert w.scene is not None
    w.tools.select(Selection.object(w.scene, (0, 1), 1))
    w.set_tool(TOOL, tool)


def test_registered() -> None:
    assert registered()["map"] is MapWorkspace


def test_without_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MHFU_DATA", raising=False)
    w = MapWorkspace()
    assert w.atlas is None and "start page" in w.status() and "start page" in w.hud()
    assert w.pointer(ev("press", (5.0, 5.0))) == Gesture.NONE and not w.key(Key("F"))
    rec = Recorder(SIZE)
    w.paint(rec)
    assert rec.calls == []


def test_tool_groups(ws: MapWorkspace) -> None:
    assert [g.id for g in ws.tool_groups()] == [TOOL, PICK, OPTIONS]
    tools = [t for g in GROUPS for t in g.tools]
    keys = [t.key for t in tools if t.key]
    assert keys == ["Q", "W", "E", "R", "P", "1", "2", "3", "4", "C"]
    assert all(t.tip and t.icon.startswith("ph.") for t in tools)
    assert ws.tool_on(TOOL, "select") and ws.tool_on(PICK, OBJECT)
    ws.set_tool(TOOL, ROTATE)
    ws.set_tool(PICK, FACE)
    ws.set_tool(OPTIONS, SNAP, True)
    ws.set_tool(OPTIONS, LOCAL, True)
    assert (ws.tools.tool, ws.tools.kind, ws.tools.snap, ws.tools.space_local) == (
        ROTATE,
        FACE,
        True,
        True,
    )
    assert ws.tool_on(TOOL, ROTATE) and ws.tool_on(OPTIONS, SNAP) and not ws.tool_on(TOOL, MOVE)
    ws.set_tool(OPTIONS, SNAP, False)
    assert not ws.tool_on(OPTIONS, SNAP)
    ws.set_tool(PICK, COLLISION)
    assert ws.vp is not None and ws.vp.show_collision


def test_gestures(ws: MapWorkspace) -> None:
    assert click(ws, (1.0, 1.0)) == Gesture.ORBIT  # a drag in the select tool is a box
    assert click(ws, (1.0, 1.0), Mod.ALT) == Gesture.NONE
    assert ws.pointer(ev("press", (1.0, 1.0), Button.RIGHT)) == Gesture.NONE
    assert ws.pointer(ev("wheel", (1.0, 1.0), Button.NONE)) == Gesture.NONE
    ws.set_tool(TOOL, MOVE)
    assert click(ws, (1.0, 1.0)) == Gesture.NONE  # off the gizmo a drag orbits


def test_click_and_shift(ws: MapWorkspace) -> None:
    at = crate(ws)
    click(ws, at)
    assert ws.selection.parts == {(0, 1): [1]} and ws.selected_group is None
    assert ws.tools.last_pick is not None
    click(ws, at, Mod.SHIFT)
    assert ws.selection.empty
    ws.set_tool(PICK, GROUP)
    click(ws, at)
    assert ws.selected_group == (0, 1)
    assert ws.vp is not None and ws.vp.mesh is not None
    ws.vp.mesh.show_backdrop = False  # from above the far quad is under every corner
    click(ws, (1.0, 1.0))
    assert ws.selection.empty


def test_hover(ws: MapWorkspace) -> None:
    ws.pointer(ev("move", crate(ws), Button.NONE))
    assert ws.tools.hover is not None
    ws.pointer(ev("leave", (0.0, 0.0), Button.NONE))
    assert ws.tools.hover is None


def test_box(ws: MapWorkspace) -> None:
    rec = Recorder(SIZE)
    drag(ws, (0.0, 0.0), (float(SIZE[0]), float(SIZE[1])), rec=rec)
    assert ("rect", (0.0, 0.0), (320.0, 200.0), Ink.BOX, None) in rec.calls
    assert (0, 1) in ws.selection.vertices and ws.selection.kind == OBJECT
    assert ws.tools.box is None
    ws.set_tool(PICK, COLLISION)
    drag(ws, (0.0, 0.0), (float(SIZE[0]), float(SIZE[1])))
    assert len(ws.col_sel) == 6
    click(ws, crate(ws))
    assert ws.col_sel.tris and ws.col_sel.tris[0][0] == 1
    click(ws, (1.0, 1.0))
    assert ws.col_sel.empty


def test_collision_layer(ws: MapWorkspace) -> None:
    vp = ws.vp
    assert vp is not None and not vp.show_collision and not ws.tool_on(OPTIONS, LAYER)
    ws.set_tool(PICK, COLLISION)
    assert vp.show_collision and ws.tool_on(OPTIONS, LAYER)
    ws.set_tool(PICK, OBJECT)
    assert not vp.show_collision  # put back
    ws.set_tool(OPTIONS, LAYER, True)
    ws.set_tool(PICK, COLLISION)
    ws.set_tool(PICK, FACE)
    assert vp.show_collision
    ws.set_tool(PICK, COLLISION)
    ws.set_tool(OPTIONS, LAYER, False)  # chosen in the mode: it outlives the mode
    ws.set_tool(PICK, OBJECT)
    assert not vp.show_collision


def test_hidden_collision_is_not_picked(ws: MapWorkspace) -> None:
    ws.set_tool(PICK, COLLISION)
    click(ws, crate(ws))
    assert ws.col_sel.tris
    ws.set_tool(TOOL, MOVE)
    assert ws.tools.gizmo_visible
    ws.set_tool(OPTIONS, LAYER, False)
    assert not ws.tools.gizmo_visible and ws.tools.col_hover is None
    ws.set_tool(TOOL, "select")
    click(ws, crate(ws))
    ws.pointer(ev("move", crate(ws), Button.NONE))
    assert ws.col_sel.empty and ws.tools.col_hover is None
    drag(ws, (0.0, 0.0), (float(SIZE[0]), float(SIZE[1])))
    assert ws.col_sel.empty
    ws.set_tool(OPTIONS, LAYER, True)
    click(ws, crate(ws))
    assert ws.col_sel.tris


def test_alt_drag_orbits(ws: MapWorkspace) -> None:
    drag(ws, (0.0, 0.0), (300.0, 190.0), Mod.ALT)
    assert ws.selection.empty and ws.tools.box is None


def test_move(ws: MapWorkspace) -> None:
    assert ws.scene is not None and ws.session is not None and ws.vp is not None
    pick_crate(ws, MOVE)
    start = ws.selection.centroid(ws.scene)
    _, a = arm(ws, (1, 0, 0))
    assert hit(ws.vp.camera, SIZE, ws.tools.pose(), "translate", *a) == Handle("axis", 0)
    _, b = arm(ws, (1, 0, 0), 1.7)
    assert ws.pointer(ev("press", a)) == Gesture.ALL
    ws.pointer(ev("move", b))
    assert ws.session.previewing and ws.tools.dragging
    rec = Recorder(SIZE)
    ws.paint(rec)
    assert "polygon" in rec.kinds() and any(t.startswith("X ") for t in rec.texts())
    ws.pointer(ev("release", b))
    ws.frame(0.0)
    assert len(ws.session.ops) == 1 and not ws.session.previewing
    moved = ws.selection.centroid(ws.scene) - start
    assert moved[0] > 100.0 and abs(moved[1]) < 1.0 and abs(moved[2]) < 1.0
    assert ws.session.ops[0]["by"][0] == pytest.approx(moved[0], abs=1.0)
    assert "applied: group 1: moved by" in ws.message and ws.document.dirty
    assert np.allclose(ws.tools.pose()[:3, 3], ws.selection.centroid(ws.scene))
    ws.document.undo()
    ws.refresh()
    ws.frame(0.0)
    assert not ws.session.ops and np.allclose(ws.selection.centroid(ws.scene), start, atol=1.0)
    assert "applied" not in ws.message


def test_snap(ws: MapWorkspace) -> None:
    assert ws.session is not None
    pick_crate(ws, MOVE)
    ws.set_tool(OPTIONS, SNAP, True)
    _, a = arm(ws, (1, 0, 0))
    _, b = arm(ws, (1, 0, 0), 1.33)
    drag(ws, a, b)
    by = ws.session.ops[0]["by"][0]
    assert by != 0 and by % 50.0 == pytest.approx(0.0, abs=1e-6)


def test_rotate(ws: MapWorkspace) -> None:
    assert ws.session is not None
    pick_crate(ws, ROTATE)
    s = 1 / np.sqrt(2)
    _, a = arm(ws, (s, 0, s), 1.0)
    _, b = arm(ws, (s, 0, -s), 1.0)
    drag(ws, a, b)
    assert abs(ws.session.ops[0]["rotate"][1]) == pytest.approx(90.0, abs=2.0)


def test_scale(ws: MapWorkspace) -> None:
    assert ws.scene is not None and ws.session is not None
    pick_crate(ws, SCALE)
    lo, hi = ws.selection.bounds(ws.scene)
    _, a = arm(ws, (1, 0, 0), 1.0)
    _, b = arm(ws, (1, 0, 0), 2.0)
    drag(ws, a, b)
    assert ws.session.ops[0]["scale"][0] == pytest.approx(2.0, abs=0.05)
    lo2, hi2 = ws.selection.bounds(ws.scene)
    assert (hi2 - lo2)[0] == pytest.approx(2 * (hi - lo)[0], rel=0.05)


def test_turn_is_kept(ws: MapWorkspace) -> None:
    assert ws.scene is not None and ws.session is not None
    pick_crate(ws, ROTATE)
    s = 1 / np.sqrt(2)
    _, a = arm(ws, (s, 0, s), 1.0)
    _, b = arm(ws, (s, 0, -s), 1.0)
    drag(ws, a, b)
    turn = ws.tools.pose()[:3, :3]
    assert abs(turn[2, 0]) == pytest.approx(1.0, abs=0.05)  # local x now runs along world z
    ws.set_tool(TOOL, SCALE)
    _, a = arm(ws, turn[:, 0], 1.0)
    _, b = arm(ws, turn[:, 0], 2.0)
    assert ws.vp is not None
    assert hit(ws.vp.camera, SIZE, ws.tools.pose(), "scale", *a) == Handle("axis", 0)
    drag(ws, a, b)
    assert ws.session.ops[-1]["scale"] == pytest.approx([1.0, 1.0, 2.0], abs=0.05)
    assert np.allclose(ws.tools.pose()[:3, :3], turn)
    ws.document.undo()
    ws.refresh()
    assert np.allclose(ws.tools.pose()[:3, :3], np.eye(3))


def test_new_selection_is_unturned(ws: MapWorkspace) -> None:
    assert ws.scene is not None
    pick_crate(ws, MOVE)
    ws.apply_numeric((0, 0, 0), (0, 90, 0), (1, 1, 1))
    ws.frame(0.0)
    assert not np.allclose(ws.tools.pose()[:3, :3], np.eye(3))
    ws.tools.select(Selection.object(ws.scene, (0, 1), 0))
    assert np.allclose(ws.tools.pose()[:3, :3], np.eye(3))


def test_collision_gizmo(ws: MapWorkspace) -> None:
    assert ws.scene is not None and ws.session is not None
    ws.set_tool(PICK, COLLISION)
    click(ws, crate(ws))
    before = ws.scene.chunk(1).verts[ws.col_sel.tris[0][1]].copy()
    ws.set_tool(TOOL, MOVE)
    _, a = arm(ws, (0, 0, 1))
    _, b = arm(ws, (0, 0, 1), 1.5)
    drag(ws, a, b)
    op = ws.session.ops[0]
    assert op["op"] == "collision" and "verts" in op
    after = ws.scene.chunk(1).verts[op["tri"]]
    assert (after[:, 2] - before[:, 2]).min() > 50.0


def test_escape(ws: MapWorkspace) -> None:
    assert ws.scene is not None and ws.session is not None
    pick_crate(ws, MOVE)
    start = ws.selection.centroid(ws.scene)
    _, a = arm(ws, (1, 0, 0))
    _, b = arm(ws, (1, 0, 0), 1.7)
    ws.pointer(ev("press", a))
    ws.pointer(ev("move", b))
    assert ws.key(Key("Escape"))
    assert not ws.session.previewing and not ws.tools.dragging
    assert ws.pointer(ev("release", b)) == Gesture.NONE
    ws.frame(0.0)
    assert not ws.session.ops and not ws.selection.empty
    assert np.allclose(ws.selection.centroid(ws.scene), start)
    assert ws.key(Key("Escape")) and ws.selection.empty
    ws.pointer(ev("press", (1.0, 1.0)))
    ws.set_tool(TOOL, "select")
    ws.pointer(ev("press", (1.0, 1.0)))
    ws.pointer(ev("move", (100.0, 100.0)))
    assert ws.tools.box is not None and ws.key(Key("Escape")) and ws.tools.box is None


def test_keys(ws: MapWorkspace) -> None:
    assert ws.scene is not None and ws.session is not None and ws.vp is not None
    pick_crate(ws, "select")
    target = ws.vp.camera.target.copy()
    assert ws.key(Key("F"))
    assert not np.allclose(ws.vp.camera.target, target)
    assert not ws.key(Key("F", Mod.CTRL)) and not ws.key(Key("Q"))
    assert ws.key(Key("Delete"))
    assert ws.session.ops[-1]["op"] == "clear" and ws.selection.empty
    ws.set_tool(PICK, COLLISION)
    click(ws, crate(ws))
    c, t = ws.col_sel.tris[0]
    assert ws.key(Key("Backspace"))
    assert not ws.scene.chunk(c).alive[t]


def test_labels(ws: MapWorkspace, game: Extracted, synth: Any) -> None:
    assert ws.vp is not None
    ws.vp.set_scene(synth.build(game))  # the one with exits and spheres
    ws.vp.camera.look("top")
    rec = Recorder(SIZE)
    ws.paint(rec)
    texts = [c for c in rec.calls if c[0] == "text"]
    assert any("\u2192 Snowy base camp (st098)" in c[2] for c in texts)
    assert any("sphere 24" in c[2] for c in texts)
    exit_label = next(c for c in texts if "(st098)" in c[2])
    assert exit_label[3][:3] == pytest.approx((1.0, 0.55, 0.15))
    ws.vp.show_labels = False
    rec = Recorder(SIZE)
    ws.paint(rec)
    assert rec.texts() == []


def test_hud_and_hint(ws: MapWorkspace) -> None:
    assert ws.hud() == "Pokke village (st139)"
    assert ws.hint().startswith("Click an object to select it")
    pick_crate(ws, MOVE)
    assert ws.hint().startswith("1 object in group 1: drag a handle to move it \u00b7 E rotate")
    assert "Del remove" in ws.hint()
    ws.set_tool(PICK, COLLISION)
    assert ws.hint().startswith("Click a collision triangle to move it")


def test_revert_opens_the_file_again(ws: MapWorkspace, doc_dir: Path) -> None:
    move = {"op": "transform", "sub": 0, "group": 1, "vertices": [0, 1, 2], "by": [0, 9, 0]}
    d = MapDocument("d", 0, doc_dir)
    d.ensure_stage(139).ops.append(move)
    d.save()
    ws.open(doc_dir)
    assert ws.session is not None
    ws.session.push([move])
    assert ws.doc.dirty
    Studio([ws]).revert()
    assert not ws.doc.dirty and ws.session is not None and len(ws.session.ops) == 1


def test_open_and_reveal(ws: MapWorkspace, doc_dir: Path) -> None:
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
    assert ws.can_open(doc_dir / "map.toml") and not ws.can_open(doc_dir / "a.pac")
    ws.reveal((98, 0))
    assert ws.scene.stage == 98 and ws.selection.n_vertices == 3
    assert ws.message.startswith("Snowy base camp (st098), edit 1: ")
    assert ws.take_focus() == "Selection" and ws.take_focus() is None
    ws.reveal((98, 1))
    assert ws.col_sel.tris == [(0, 1)] and ws.tools.kind == COLLISION
    assert ws.take_focus() == "Collision"
    ws.reveal("nothing")
    assert ws.scene.stage == 98


def test_point_tool(ws: MapWorkspace) -> None:
    assert ws.scene is not None
    ws.set_tool(TOOL, POINT)
    assert "Click the ground" in ws.hint() and not ws.tools.gizmo_visible
    assert click(ws, screen(ws, (1200.0, 0.0, 1500.0))) == Gesture.NONE
    (p,) = ws.doc.points
    assert p.stage == ws.scene.stage and p.at == pytest.approx((1200.0, 0.0, 1500.0), abs=5.0)
    assert ws.point == p.name and ws.points_here() == [p] and ws.doc.dirty
    rec = Recorder(SIZE)
    ws.paint(rec)
    assert p.name in rec.texts() and "circle" in rec.kinds()
    assert ws.place_point(None) is None  # a click on nothing
    assert len(ws.doc.points) == 1 and "no ground" in ws.message
    assert ws.edit_point(p.name, name="ledge", kind="climb") is None
    assert ws.doc.point("ledge").heading == 0.0 and ws.point == "ledge"
    assert ws.edit_point("ledge", kind="point") is None
    assert ws.doc.point("ledge").heading is None
    ws.place_point((0.0, 0.0, 0.0))
    assert "already named" in (ws.edit_point("point_1", name="ledge") or "")
    ws.remove_point("ledge")
    assert [q.name for q in ws.doc.points] == ["point_1"]
