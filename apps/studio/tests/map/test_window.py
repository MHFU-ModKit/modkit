# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The map workspace in the real window: opt-in, it needs a display and the game."""

import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from mhfu.files import Extracted
from mhfu_studio.map.core.edit import CollisionSelection, Selection
from mhfu_studio.map.workspace import MapWorkspace
from mhfu_studio.shell.app import Studio
from mhfu_studio.shell.testing import run_window

pytestmark = pytest.mark.skipif(
    not os.environ.get("MHFU_UI_SMOKE"), reason="set MHFU_UI_SMOKE=1 to open a real window"
)


def test_map_in_the_window(mhfu_data: Path, tmp_path: Path):
    ws = MapWorkspace(Extracted.find(mhfu_data))
    studio = Studio([ws], size=(1100, 720), ini_folder=tmp_path)
    got: dict[str, Any] = {"stages": []}

    def step(i: int) -> None:
        sc = ws.scene
        if sc is not None and (not got["stages"] or got["stages"][-1] != sc.stage):
            got["stages"].append(sc.stage)
        sel, doc = panel(ws, "Selection"), panel(ws, "Document")
        if i == 2:
            ws.load_stage(98, row=11)
            assert ws.vp is not None
            ws.vp.show_collision = ws.vp.show_lattice = True
        elif i == 3:
            assert ws.scene is not None and ws.session is not None
            ws.tools.select(Selection.object(ws.scene, (0, 11), 17))
            got["start"] = ws.selection.positions(ws.scene).mean(0)
            sel.by = [500.0, 0.0, 0.0]
            sel.apply()
            got["ops"] = len(ws.session.ops)
        elif i == 4:
            studio.undo()
            got["undone"] = len(ws.session.ops) if ws.session else -1
            studio.redo()
        elif i == 5:
            doc.new(tmp_path / "doc", "smoke")
            got["saved"] = studio.save()
            ws.load_stage(99)
            ws.load_stage(98)
            assert ws.session is not None and ws.scene is not None
            got["replayed"] = len(ws.session.ops)
            ids = ws.session.ops[0]["vertices"]
            g = ws.scene.group(0, 11)
            got["moved"] = float(g.positions[ids].mean(0)[0] - got["start"][0])
        elif i == 6:
            assert ws.scene is not None and ws.session is not None
            g = ws.scene.group(0, 11)
            big = int(np.argsort(np.bincount(g.components))[::-1][3])
            ws.tools.select(Selection.object(ws.scene, (0, 11), big))
            got["faces"] = g.n_faces
            ws.add.add("box", {"size": (300.0, 300.0, 300.0)}, g, ws.selection)
            got["added"] = ws.selection.n_faces(ws.scene)
            ws.remove_selected(solid=False)
            got["after_add_remove"] = len(ws.session.ops)
        elif i == 7:
            assert ws.scene is not None
            wall = ws.scene.chunk(0)
            tri = int(np.nonzero(np.abs(wall.normals[:, 1]) < 0.34)[0][0])
            ws.tools.set_kind("collision")
            ws.tools.select_collision(CollisionSelection([(0, tri)]))
            panel(ws, "Collision").climb(True, 10)
            got["climb"] = str(ws.scene.chunk(0).klass[tri])
        elif i == 9:
            ws.load_stage(139, row=0)
            assert ws.vp is not None and ws.vp.mesh is not None
            ws.vp.mesh.mode = 3
            ws.vp.stand_at_entry()
        elif i == 11:
            # docked behind a tab a panel is not drawn: draw each with the real imgui
            ws.tools.select(Selection.group(ws.scene, (0, 11)) if ws.scene else Selection())
            for p in ws.panels():
                p.draw()
            got["panels"] = len(ws.panels())

    smoke = run_window(studio, frames=14, step=step, out=tmp_path / "window.png")
    assert smoke.errors == [], smoke.errors
    assert got["stages"][:2] == [139, 98] and got["stages"][-1] == 139
    assert got["ops"] == 1 and got["undone"] == 0
    assert got["saved"] and got["replayed"] == 1 and abs(got["moved"] - 500.0) < 2.0
    assert got["added"] == 12 and got["after_add_remove"] == 3
    assert got["climb"] == "climb" and got["panels"] == 10
    assert smoke.screen is not None
    w, h = smoke.screen
    assert smoke.lit > 0.3 * w * h, f"{smoke.lit} lit pixels of {w * h}"


def panel(ws: MapWorkspace, label: str) -> Any:
    return next(p.draw.__self__ for p in ws.panels() if p.label == label)  # type: ignore[attr-defined]


def warp(x: float, y: float) -> bool:
    """Moves the real pointer through the bundle's own GLFW: the backend polls the cursor every
    frame, so an injected position alone is overwritten. Focus first, or the backend reports
    the mouse nowhere; the injected position covers a window that cannot take focus."""
    import ctypes

    import imgui_bundle
    from imgui_bundle import hello_imgui, imgui

    here = Path(imgui_bundle.__file__).parent
    libs = [here / n for n in ("libglfw.3.dylib", "libglfw.so.3", "glfw3.dll")]
    found = [p for p in libs if p.exists()]
    if not found:
        return False
    lib = ctypes.CDLL(str(found[0]))
    win = ctypes.c_void_p(hello_imgui.get_glfw_window_address())
    lib.glfwFocusWindow.argtypes = [ctypes.c_void_p]
    lib.glfwFocusWindow(win)
    lib.glfwSetCursorPos.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_double]
    lib.glfwSetCursorPos(win, float(x), float(y))
    imgui.get_io().add_mouse_pos_event(float(x), float(y))
    return True


def test_gizmo_drag(mhfu_data: Path, tmp_path: Path):
    """A real ImGuizmo translate: the press must reach the gizmo, not the capture button."""
    from imgui_bundle import imgui

    ws = MapWorkspace(Extracted.find(mhfu_data))
    studio = Studio([ws], size=(1100, 720), ini_folder=tmp_path)
    views: list[Any] = []
    real_input = ws.input

    def recording(view: Any) -> Any:
        views.append(view)
        return real_input(view)

    ws.input = recording  # type: ignore[method-assign]
    got: dict[str, Any] = {}

    def step(i: int) -> None:
        io = imgui.get_io()
        if i == 2:
            ws.load_stage(98, row=11)
        elif i == 3:
            assert ws.scene is not None
            ws.tools.select(Selection.object(ws.scene, (0, 11), 17))
            ws.tools.set_tool("move")
            ws.frame_selection()
        elif i >= 5 and views and ws.scene is not None and ws.vp is not None:
            if i == 5:
                got["start"] = ws.selection.centroid(ws.scene)
                v = views[-1]
                px = ws.vp.camera.project(got["start"], v.size)[0]
                got["px"] = (v.origin[0] + px[0], v.origin[1] + px[1])
            x, y = got["px"]
            got["warped"] = warp(x + 40 * max(0, min(i - 8, 2)), y)
            if i == 7:
                io.add_mouse_button_event(0, True)
            elif i == 11:
                io.add_mouse_button_event(0, False)
            elif i == 14:
                assert ws.session is not None
                got["ops"] = len(ws.session.ops)
                got["moved"] = float(np.linalg.norm(ws.selection.centroid(ws.scene) - got["start"]))

    smoke = run_window(studio, frames=16, step=step)
    assert smoke.errors == [], smoke.errors
    if not got.get("warped"):
        pytest.skip("could not move the pointer through GLFW; the drag needs it")
    assert got["ops"] == 1 and got["moved"] > 10.0, got
