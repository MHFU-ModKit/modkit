# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.document import PortDocument
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.input import Key, Mod, Pointer
from mhfu_studio.shell.overlay import Recorder
from mhfu_studio.shell.workspace import Gesture, discover
from mhfu_studio.ui import kit
from mhfu_studio.ui.testing import elsewhere, gl_or_skip
from PySide6.QtWidgets import QDockWidget

DOCKS = (
    "Actions",
    "Clips",
    "Moves",
    "Scene",
    "View",
    "Joints",
    "Hitboxes",
    "Parts",
    "Behaviour",
    "Timeline",
)


def test_registers_its_docks() -> None:
    assert "monster" in discover(["monster"])
    ws = MonsterWorkspace()
    assert [d.label for d in ws.docks()] == list(DOCKS)
    shown = ["Actions", "Clips", "Hitboxes", "Timeline"]
    assert [d.label for d in ws.docks() if d.shown] == shown
    assert ws.tool_groups() == ()


def test_nothing_open() -> None:
    ws = MonsterWorkspace()
    o = Recorder()
    ws.paint(o)
    assert o.calls == [] and not ws.key(Key("Space")) and not ws.animating()
    assert ws.pointer(Pointer("press", 1, 1, (8, 8))) == Gesture.NONE
    assert ws.document is None and ws.status() == "" and "File > Open" in ws.hint()


def test_status_names_the_scene(workspace: MonsterWorkspace) -> None:
    assert workspace.status() == "t on Tigrex (em75)"


def test_keys_drive_the_transport(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.vp is not None
    ws.play_slot(1)
    pb = ws.vp.playback
    assert pb.playing and pb.phase == 0.0, "a picked clip plays from its first frame"
    assert "clip 1 walk \u00b7 Space pause \u00b7 \u2190 / \u2192 step a frame" in ws.hint()
    assert ws.key(Key("Space")) and not pb.playing
    assert ws.key(Key("Right")) and pb.phase == pb.speed and ws.vp.frame == pb.phase
    assert ws.key(Key("Left")) and pb.phase == 0.0
    ws.seek(6.0)
    assert ws.key(Key("Home")) and pb.phase == 0.0 and ws.vp.frame == 0.0
    assert not ws.key(Key("Space", Mod.CTRL)) and not ws.key(Key("Q"))


def test_playback_animates(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.vp is not None
    ws.play_slot(1)
    assert ws.animating()
    for _ in range(4):
        ws.frame(1 / 30)
    assert ws.vp.frame > 0


def test_the_host_restarts_with_the_port(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.vp is not None
    ws.select_pair(1, 4)
    ws.set_show_host(True)
    ref = ws.vp.reference
    assert ref is not None
    ws.play_slot(2)  # a one-shot of 6 frames
    for _ in range(5):
        ws.frame(1 / 30)
    pb = ws.vp.playback
    assert pb.at_end and not pb.playing and ref.playback.phase > 0
    ws.play_pause()
    assert pb.playing and pb.phase == 0.0 and ref.playback.phase == 0.0 and ref.frame == 0.0
    ws.frame(1 / 30)
    ws.play_slot(1)
    assert pb.phase == 0.0 and ref.playback.phase == 0.0


def test_reveal_follows_the_findings(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.doc is not None and ws.vp is not None
    targets = {f.target for f in ws.doc.findings() if f.target is not None}
    assert ("hurtbox", 1) in targets
    for t in sorted(targets, key=str):
        ws.reveal(t)
    ws.reveal(("hurtbox", 1))
    assert ws.show_parts and ws.parts_source == "port" and ws.selected_volume == 1
    assert ws.take_focus() == "Parts" and ws.take_focus() is None
    ws.reveal(("hitbox", 0))
    assert ws.selected_set == 2 and ws.selected_attack_volume == 0
    ws.reveal(("moves", "charge"))
    assert ws.pair == (1, 4) and ws.vp.clip is not None and ws.take_focus() == "Actions"
    ws.reveal(("effect", 0))
    assert ws.vp.selected_joint == 2
    ws.reveal(("clips", "walk"))
    assert ws.edit_slot == 1 and ws.take_focus() == "Clips"
    ws.reveal("nonsense")


def test_select_action_plays_from_the_start(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.vp is not None
    ws.play_slot(2)
    ws.seek(4.0)
    ws.select_action(1, 4)
    pb = ws.vp.playback
    assert (ws.pair, ws.move, ws.graph.picked) == ((1, 4), "charge", (1, 4))
    assert ws.vp.clip is not None and ws.vp.clip.slot == 1 and pb.phase == 0.0 and pb.playing
    ws.seek(5.0)
    ws.select_action(0, 3)
    assert ws.move is None and ws.vp.clip.slot == 1 and pb.phase == 0.0
    assert ws.message == "anim 9 is not in this build: the game finds no clip for (0,3)"
    assert [r.pair for r in ws.action_rows()][:2] == [(1, 4), (3, 9)]
    assert ws.action_rows() is ws.action_rows(), "made once per manifest"


def test_bind_names_the_move(workspace: MonsterWorkspace) -> None:
    ws = workspace
    ws.play_slot(1)
    ws.select_pair(1, 3)
    ws.bind_move("  stop ")
    assert ws.manifest is not None and ws.manifest.moves["stop"].main == 1
    ws.select_pair(0, 3)
    ws.bind_move()
    assert "move_0_3" in ws.manifest.moves and ws.move == "move_0_3"
    ws.play_slot(2)
    ws.bind_move("stop")
    assert ws.manifest.moves["stop"].clip == "clip_02" and ws.manifest.clips["clip_02"].slot == 2


def test_edit_set_opens_hitboxes(workspace: MonsterWorkspace) -> None:
    ws = workspace
    ws.edit_set(3)
    assert ws.show_attacks and ws.attacks_source == "host" and ws.selected_set == 3
    assert ws.take_focus() == "Hitboxes"
    assert ws.hint().endswith("hit group 3: pick one of its hitboxes in Hitboxes")
    ws.edit_set(2)
    assert ws.attacks_source == "port", "the port authors set 2"


def test_the_host_beside(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.vp is not None
    ws.select_pair(1, 4)
    ws.set_show_host(True)
    assert ws.vp.reference is not None and ws.host_clip == 1
    ws.browse_species(7)
    assert ws.vp.reference is None and "no model for em07" in ws.message
    ws.set_show_host(False)
    assert ws.vp.reference is None


def test_can_open(tmp_path: Path, synthetic_pac: bytes) -> None:
    ws = MonsterWorkspace()
    port, other, pac, junk = (tmp_path / n for n in ("a.toml", "b.toml", "c.bin", "d.bin"))
    port.write_text('[port]\nname = "x"\n')
    other.write_text("[stage]\nid = 98\n")
    pac.write_bytes(synthetic_pac)
    junk.write_bytes(b"\0" * 64)
    assert ws.can_open(port) and ws.can_open(pac)
    assert not ws.can_open(other) and not ws.can_open(junk) and not ws.can_open(tmp_path / "x.png")


def test_survey_runs_in_the_background(tmp_path: Path, intel75: Any) -> None:
    (tmp_path / "em75.json").write_text(json.dumps(intel75.doc))
    ws = MonsterWorkspace(intel_root=tmp_path)
    deadline = time.monotonic() + 10.0
    while (got := ws.host_options()) is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert got is not None and [h.species for h in got] == [75]
    assert ws.host_options() is got
    ws.close()


def test_window_builds_the_docks(
    make_window: Callable[..., Any],
    port_doc: PortDocument,
    synthetic_pac: bytes,
    intel75: Any,
    tmp_path: Path,
) -> None:
    """The real window: GL from Qt, every dock of this agent's built from its panel."""
    gl_or_skip()
    from mhfu_studio.monster.panels.action import ActionsPanel
    from mhfu_studio.monster.panels.timeline import TimelinePanel

    ws = MonsterWorkspace()
    ws.intel_cache[75] = intel75
    scene = Scene.from_bytes(
        synthetic_pac, "t", manifest=port_doc.manifest, path=tmp_path / "t.bin"
    )
    ws.load(scene, port_doc)
    w = make_window(ws)
    for label, cls in (("Timeline", TimelinePanel), ("Actions", ActionsPanel)):
        d = w.findChild(QDockWidget, f"monster/{label}")
        assert d is not None and isinstance(d.widget(), cls)
        assert kit.missing_tips(d.widget()) == []
    from mhfu_studio.monster.panels.behaviour import BehaviourPanel

    behaviour = w.findChild(QDockWidget, "monster/Behaviour")
    assert behaviour is not None and isinstance(behaviour.widget(), BehaviourPanel)
    assert not behaviour.isVisible() and kit.missing_tips(behaviour.widget()) == []
    actions = w.findChild(QDockWidget, "monster/Actions")
    assert actions is not None and actions.isVisible(), "in front of Clips"
    clips = w.findChild(QDockWidget, "monster/Clips")
    assert clips is not None and w.tabifiedDockWidgets(actions) == [clips]
    ws.focus("Clips")
    w.studio.changed()
    w.sync()
    assert clips.isVisible()
    ws.focus("Behaviour")
    w.studio.changed()
    w.sync()
    assert behaviour.isVisible() and behaviour.height() > 200


def test_window_shows_the_host(
    make_window: Callable[..., Any],
    port_doc: PortDocument,
    synthetic_pac: bytes,
    intel75: Any,
    tmp_path: Path,
) -> None:
    """Show the host beside through an action, as its tick box does, then paint."""
    gl_or_skip()
    ws = MonsterWorkspace()
    ws.intel_cache[75] = intel75
    scene = Scene.from_bytes(
        synthetic_pac, "t", manifest=port_doc.manifest, path=tmp_path / "t.bin"
    )
    ws.load(scene, port_doc)
    ws.host_scenes[75] = Scene.from_bytes(synthetic_pac, "em75")
    w = make_window(ws)
    w.view.grabFramebuffer()
    assert ws.vp is not None
    w.studio.act("pick", lambda: ws.select_pair(1, 4))()
    with elsewhere():
        w.studio.act("show host", lambda: ws.set_show_host(True))()
    w.view.grabFramebuffer()
    assert ws.vp.reference is not None and w.studio.errors == []


def test_deploy_needs_a_stick(workspace: MonsterWorkspace, monkeypatch: pytest.MonkeyPatch) -> None:
    def none() -> Path:
        raise FileNotFoundError("no PPSSPP memory stick in ~/x")

    monkeypatch.setattr(MonsterWorkspace, "mods_dir", staticmethod(none))
    assert workspace.exportable() and not MonsterWorkspace().exportable()
    with pytest.raises(FileNotFoundError, match="memory stick"):
        workspace.deploy_hit()


def test_attacks_resolve_and_export(
    workspace: MonsterWorkspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = workspace.host_attacks()
    assert host is not None and [r.id for r in host.attacks] == [6, 7]
    assert [r.id for r in host.attacks_using(2)] == [6]
    workspace.select_pair(1, 4)
    assert workspace.pair_sets() == [2]
    monkeypatch.chdir(tmp_path)
    workspace.export_hit()
    assert (tmp_path / "t_hit.lua").is_file() and "t_hit.lua (id " in workspace.message
