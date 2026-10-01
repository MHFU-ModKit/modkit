# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Every panel under the fake imgui, through the states each branches on."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from mhfu_studio.monster.render.skeleton import project
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.workspace import View, discover

PANELS = ("Moves", "Timeline", "Scene", "View", "Joints", "Clips", "Parts", "Hitboxes", "Action")


def frame(ws: MonsterWorkspace, view: View | None = None) -> None:
    """One frame the way the shell runs it."""
    view = view or View((0.0, 0.0), (320, 240), False, False)
    ws.frame(1 / 60)
    ws.input(view)
    ws.overlay(view)
    for p in ws.panels():
        p.draw()
    assert ws.vp is not None
    ws.vp.draw()


def states(ws: MonsterWorkspace) -> list[Callable[[], None]]:
    """The old panel test's script: parts, a selected part, the host beside, adopt and edit a
    volume, the attack side, a pair, a set, adopt and edit, keep-only, unset, the host's
    levers, and browsing another overlay."""

    def parts_on() -> None:
        ws.show_parts = True
        ws.sync_hitboxes()

    def host_beside() -> None:
        ws.show_host = True
        ws.sync_reference()

    def adopt_parts() -> None:
        sess, host = ws.part_session, ws.host_parts()
        assert sess is not None and host is not None
        sess.adopt_grid(host.states)
        sess.adopt_volumes(host.spheres()[:8])
        ws.parts_source = "port"
        ws.sync()

    def edit_part() -> None:
        sess = ws.part_session
        assert sess is not None
        ws.select_volume(0)
        sess.edit_volume(0, shape="capsule", to=[0.0, 0.0, 100.0])

    def attacks_on() -> None:
        ws.show_attacks = True
        ws.sync_attacks()

    def adopt_set() -> None:
        sess, host = ws.attack_session, ws.host_attacks()
        assert sess is not None and host is not None and host.set(2) is not None
        sess.adopt_set(2, host.set(2).spheres, source="panel test")  # type: ignore[union-attr]
        ws.attacks_source = "port"
        ws.sync()

    def edit_attack() -> None:
        sess = ws.attack_session
        assert sess is not None
        ws.select_attack_volume(0)
        sess.edit_volume(0, shape="capsule", to=[0.0, 0.0, 100.0])
        sess.scale_volume(0, 2.0)
        sess.set_attack(6, power=40)

    def keep_only() -> None:
        sess = ws.attack_session
        assert sess is not None
        sess.keep_only(0)
        ws.select_attack_volume(0)

    def unset() -> None:
        ws.select_set(None)
        ws.sets_of_move_only = False

    def host_levers() -> None:
        ws.attacks_source = "host"
        ws.select_set(2)
        ws.sync_attacks()

    return [
        lambda: None,
        parts_on,
        lambda: ws.select_part(1),
        host_beside,
        adopt_parts,
        edit_part,
        attacks_on,
        lambda: ws.select_pair(1, 4),
        lambda: ws.select_set(2),
        adopt_set,
        edit_attack,
        keep_only,
        unset,
        host_levers,
        lambda: ws.browse_species(7),
    ]


def test_every_panel_through_the_states(workspace: MonsterWorkspace) -> None:
    ws = workspace
    *script, browse = states(ws)
    for step in script:
        step()
        frame(ws)
    assert ws.vp is not None and ws.vp.attacks is not None
    assert ws.vp.reference is not None and ws.vp.reference.attacks is not None
    assert ws.doc is not None and ws.doc.dirty and ws.doc.can_undo()
    browse()
    frame(ws)
    assert ws.vp.reference is None and "no host PAC for em07" in ws.message
    assert [p.label for p in ws.panels()] == list(PANELS)


def test_layout_registers_and_docks(imgui: Any, tmp_path: Path) -> None:
    from mhfu_studio.shell.app import Studio

    assert "monster" in discover(["monster"])
    ws = MonsterWorkspace()
    d = Studio([ws], ini_folder=tmp_path).docking(ws)
    docks = {w.label: w.dock_space_name for w in d.dockable_windows}
    assert len(docks) == 11 and docks["Findings"] == "Bottom" and docks["Moves"] == "MainDockSpace"
    assert docks["Action"] == "BottomRight" and docks["Clips"] == "Right"
    assert [s.new_dock for s in d.docking_splits] == ["Left", "Right", "Bottom", "BottomRight"]


def test_can_open(tmp_path: Path, synthetic_pac: bytes) -> None:
    ws = MonsterWorkspace()
    port, other, pac, junk = (tmp_path / n for n in ("a.toml", "b.toml", "c.bin", "d.bin"))
    port.write_text('[port]\nname = "x"\n')
    other.write_text("[stage]\nid = 98\n")
    pac.write_bytes(synthetic_pac)
    junk.write_bytes(b"\0" * 64)
    assert ws.can_open(port) and ws.can_open(pac)
    assert not ws.can_open(other) and not ws.can_open(junk) and not ws.can_open(tmp_path / "x.png")


def test_nothing_open_draws(imgui: Any, gl: Any) -> None:
    ws = MonsterWorkspace()
    ws.setup(gl)
    frame(ws)
    assert "no scene yet: open a port manifest or a monster PAC" in imgui.imgui.shown
    assert ws.document is None and not ws.animating() and ws.status() == ""
    ws.close()


def test_buttons_edit_the_document(workspace: MonsterWorkspace, imgui: Any) -> None:
    ws = workspace
    doc = ws.doc
    assert doc is not None and ws.vp is not None and ws.scene is not None
    ws.play_slot(2)
    ws.name_buf, ws.label_buf = "strike", "the head comes down"
    imgui.imgui.click("stage")
    frame(ws)
    assert doc.dirty and doc.manifest.clips["strike"].slot == 2
    assert ws.scene.clip(2).names == ("strike",)
    imgui.imgui.click("save to t.toml")
    frame(ws)
    assert not doc.dirty and "saved" in ws.message
    doc.undo()
    frame(ws)
    assert ws.scene.clip(2).names == ()
    ws.vp.playback.seek(4.0)
    imgui.imgui.click("set impact = frame 4")
    frame(ws)
    assert doc.manifest.clips["strike"].impact_frame == 4 and "impact = frame 4" in ws.message


def test_bind_a_move(workspace: MonsterWorkspace, imgui: Any) -> None:
    ws = workspace
    ws.play_slot(1)
    ws.select_pair(1, 3)
    ws.bind_buf = "stop"
    imgui.imgui.click("bind as move")
    frame(ws)
    assert ws.manifest is not None and ws.manifest.moves["stop"].main == 1
    assert ws.move == "stop" and ws.alignment is not None


def test_reveal_follows_the_findings(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.doc is not None
    targets = {f.target for f in ws.doc.findings() if f.target is not None}
    assert ("hurtbox", 1) in targets
    for t in sorted(targets, key=str):
        ws.reveal(t)
    ws.reveal(("hurtbox", 1))
    assert ws.show_parts and ws.parts_source == "port" and ws.selected_volume == 1
    ws.reveal(("hitbox", 0))
    assert ws.show_attacks and ws.selected_set == 2 and ws.selected_attack_volume == 0
    ws.reveal(("moves", "charge"))
    assert ws.pair == (1, 4) and ws.vp is not None and ws.vp.clip is not None
    ws.reveal(("effect", 0))
    assert ws.vp.selected_joint == 2
    ws.reveal(("clips", "walk"))
    assert ws.edit_slot == 1
    ws.reveal(("hitzone", 0))
    assert ws.show_state == 0 and ws.parts_source == "port"
    frame(ws)
    assert ws.show_state is None
    ws.reveal("nonsense")


def test_click_picks_a_joint_and_labels_it(workspace: MonsterWorkspace, imgui: Any) -> None:
    ws = workspace
    vp = ws.vp
    assert vp is not None and vp.skeleton is not None
    view = View((10.0, 20.0), (320, 240), True, False)
    xy, ok = project(vp.camera, vp.skeleton.positions, view.size)
    j = int(np.flatnonzero(ok)[-1])
    imgui.imgui.io.mouse_pos.x, imgui.imgui.io.mouse_pos.y = 10.0 + xy[j][0], 20.0 + xy[j][1]
    imgui.imgui.is_mouse_clicked = lambda *a: True
    ws.input(view)
    picked = vp.selected_joint
    assert picked is not None
    ws.overlay(view)
    assert str(picked) in imgui.imgui.drawn
    ws.show_joint_ids = True
    ws.overlay(view)
    assert {str(i) for i in np.flatnonzero(ok)} <= set(imgui.imgui.drawn)


def test_playback_animates(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.vp is not None
    ws.play_slot(1, 0.0)
    assert not ws.animating()
    ws.vp.playback.play()
    assert ws.animating()
    for _ in range(4):
        ws.frame(1 / 30)
    assert ws.vp.frame > 0


def test_zinogre_through_the_states(
    imgui: Any,
    gl: Any,
    games: Any,
    ports: Path,
    em75: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The old panel test's subject: the built Zinogre on em75, the Tigrex beside it."""
    from mhfu.em.intel import HostSummary

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    ws = MonsterWorkspace(games)
    ws.intel_cache[75] = em75
    ws.intel_cache[7] = None
    ws.hosts = [HostSummary.of(em75)]
    ws.setup(gl)
    ws.open(ports / "zinogre.toml")
    try:
        for step in states(ws):
            step()
            frame(ws)
        assert ws.vp is not None and ws.vp.attacks is not None
        assert ws.vp.reference is not None and ws.vp.reference.scene.name == "em07"
        assert ws.vp.reference.attacks is not None
        assert ws.doc is not None and ws.doc.dirty
        cov = ws.vocabulary().coverage.counts()
        assert cov["CARRIED"] > 0 and cov["FILLER"] > 0
    finally:
        ws.close()


def test_survey_runs_in_the_background(tmp_path: Path, intel75: Any) -> None:
    import json
    import time

    (tmp_path / "em75.json").write_text(json.dumps(intel75.doc))
    ws = MonsterWorkspace(intel_root=tmp_path)
    deadline = time.monotonic() + 10.0
    while (got := ws.host_options()) is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert got is not None and [h.species for h in got] == [75]
    assert ws.host_options() is got
    ws.close()
