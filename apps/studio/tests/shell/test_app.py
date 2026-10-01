# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mhfu_studio.shell.app import Findings, IdleHold, Studio, camera_input
from mhfu_studio.shell.camera import OrbitCamera
from mhfu_studio.shell.findings import Finding
from mhfu_studio.shell.testing import FakeBundle, FakeWorkspace, chord, fake_imgui
from mhfu_studio.shell.workspace import Gesture, View


@pytest.fixture
def fake() -> Iterator[FakeBundle]:
    with fake_imgui() as f:
        yield f


@pytest.fixture
def studio(tmp_path: Path) -> Studio:
    return Studio(
        [FakeWorkspace("monster", ".pac"), FakeWorkspace("map", ".toml")], ini_folder=tmp_path
    )


def doc_file(tmp_path: Path, name: str = "a.toml", text: str = "x y") -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


def test_open_picks_the_workspace(studio: Studio, tmp_path: Path) -> None:
    assert studio.open(doc_file(tmp_path))
    assert studio.active.name == "map" and studio.active.document is not None
    assert not studio.open(tmp_path / "b.png") and "nothing here opens" in studio.message
    assert not studio.open(tmp_path / "gone.pac") and "could not open" in studio.message
    with pytest.raises(ValueError, match="repeat"):
        Studio([FakeWorkspace("a"), FakeWorkspace("a")])
    with pytest.raises(ValueError, match="no workspaces"):
        Studio([])


def test_save_undo_redo(studio: Studio, tmp_path: Path) -> None:
    studio.open(doc_file(tmp_path))
    ws = studio.active
    doc = ws.document
    assert isinstance(ws, FakeWorkspace) and doc is not None
    doc.edit("z")
    studio.undo()
    assert doc.history.value == ["x", "y"] and ws.log[-1] == ("refresh", None)
    studio.redo()
    studio.redo()
    assert studio.message == "nothing to redo"
    assert studio.save(tmp_path / "b.toml") and not doc.dirty
    assert doc.path == tmp_path / "b.toml"


def test_layouts(fake: FakeBundle, studio: Studio, tmp_path: Path) -> None:
    p = studio.runner_params()
    assert p.ini_filename == str(tmp_path / "mhfu_studio.ini")
    layouts = [p.docking_params, *p.alternative_docking_layouts]
    assert [d.layout_name for d in layouts] == ["monster", "map"]
    labels = [w.label for w in layouts[0].dockable_windows]
    assert labels == ["Viewport", "Findings", "Items"]
    docks = {w.label: w.dock_space_name for w in layouts[0].dockable_windows}
    assert docks == {"Viewport": "MainDockSpace", "Findings": "Bottom", "Items": "Left"}


def test_frames(fake: FakeBundle, studio: Studio, gl: Any, tmp_path: Path) -> None:
    studio.ctx = gl
    studio.open(doc_file(tmp_path))
    ws = studio.active
    assert isinstance(ws, FakeWorkspace)
    doc = ws.doc
    assert doc is not None
    doc.found = [Finding("error", "bad", "it broke", where="item 1", target=1)]
    hi, im = fake.hello_imgui, fake.imgui
    hi.layout = "monster"  # remembered from the last run; the opened file wins
    im.io.display_framebuffer_scale.x = 2.0

    def script(i: int) -> None:
        if i == 1:
            im.click("error item 1: it broke [bad]")
            doc.edit("z")
            im.press("mod_ctrl", "z")
        if i == 2:
            im.click("Save")
            im.press("mod_ctrl", "mod_shift", "z")
        if i == 3:
            im.click("monster")

    hi.before_frame = script
    hi.frames = 5
    hi.run(studio.runner_params())
    assert studio.errors == []
    assert ("reveal", 1) in ws.log
    assert doc.history.value == ["x", "y", "z"] and doc.saved_to == [doc.path]
    assert hi.layout == "monster" and studio.active.name == "monster"
    assert ws.vp is None  # closed at exit
    assert any("yaw" in t for t in im.drawn)
    assert "error item 1: it broke [bad]##finding0" in im.shown
    assert ws.panel_frames == 3


def test_viewport_follows_the_panel(fake: FakeBundle, studio: Studio, gl: Any) -> None:
    studio.ctx = gl
    fake.imgui.io.display_framebuffer_scale.x = 2.0
    studio._viewport_window()
    vp = studio.active.viewport
    assert vp is not None and vp.target.size == (1280, 800)
    studio.close()


def test_panel_errors_are_kept(fake: FakeBundle, studio: Studio) -> None:
    def boom() -> None:
        raise RuntimeError("bad panel")

    studio.guard("Items", boom)()
    studio.guard("Items", boom)()
    assert len(studio.errors) == 1 and studio.errors[0][0] == "Items"
    assert "bad panel" in studio.message


def test_open_dialog(fake: FakeBundle, studio: Studio, tmp_path: Path) -> None:
    fake.portable_file_dialogs.answer = [str(doc_file(tmp_path, "c.pac"))]
    fake.imgui.press("mod_ctrl", "o")
    studio._frame()
    assert studio.active.name == "monster" and studio.active.document is not None
    fake.portable_file_dialogs.answer = [str(tmp_path / "d.pac")]
    studio.ask_save_as()
    studio._frame()
    assert (tmp_path / "d.pac") == studio.active.document.path


def test_findings_throttle() -> None:
    f = Findings()
    doc = SimpleNamespace(findings=lambda: [Finding("info", "i", "m")])
    assert len(f.get(doc, now=0.0)) == 1
    calls = []
    doc.findings = lambda: calls.append(1) or []
    f.get(doc, now=0.5)
    assert calls == []
    f.get(doc, now=1.5)
    assert calls == [1]
    f.stale()
    f.get(doc, now=1.6)
    assert calls == [1, 1]
    doc.findings = lambda: 1 / 0
    f.stale()
    assert f.get(doc)[0].code == "check-failed"
    assert f.get(None) == []


def test_idle_hold() -> None:
    idling = SimpleNamespace(enable_idling=True)
    hold = IdleHold()
    hold.sync(idling, True)
    assert not idling.enable_idling
    hold.sync(idling, True)
    hold.sync(idling, False)
    assert idling.enable_idling
    idling.enable_idling = False
    hold.sync(idling, False)
    assert not idling.enable_idling


def test_camera_input(fake: FakeBundle) -> None:
    im = fake.imgui
    cam = OrbitCamera()
    im.drags = {0: (10.0, 0.0), 1: (0.0, 5.0)}
    im.io.mouse_wheel = 1.0
    view = View((0.0, 0.0), (100, 100), hovered=True, active=True)
    yaw, target, d = cam.yaw, cam.target.copy(), cam.distance
    camera_input(cam, view, Gesture.ORBIT)
    assert cam.yaw == yaw and not (cam.target == target).all() and cam.distance < d
    camera_input(cam, view, Gesture.NONE)
    assert cam.yaw != yaw


def test_chord_names(fake: FakeBundle) -> None:
    im = fake.imgui
    assert str(im.Key.mod_ctrl | im.Key.z) == chord("mod_ctrl", "z")
