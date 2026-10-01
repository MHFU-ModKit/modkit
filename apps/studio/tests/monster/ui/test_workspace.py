# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.document import PortDocument
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.input import Key, Mod, Pointer
from mhfu_studio.shell.overlay import Recorder
from mhfu_studio.shell.workspace import Gesture, discover
from mhfu_studio.ui import kit
from mhfu_studio.ui.testing import gl_or_skip
from PySide6.QtWidgets import QDockWidget

DOCKS = ("Timeline", "Moves", "Action", "Scene", "View", "Joints", "Clips", "Parts", "Hitboxes")


def test_registers_its_docks() -> None:
    assert "monster" in discover(["monster"])
    ws = MonsterWorkspace()
    assert [d.label for d in ws.docks()] == list(DOCKS)
    assert ws.tool_groups() == ()


def test_nothing_open() -> None:
    ws = MonsterWorkspace()
    o = Recorder()
    ws.paint(o)
    assert o.calls == [] and not ws.key(Key("Space")) and not ws.animating()
    assert ws.pointer(Pointer("press", 1, 1, (8, 8))) == Gesture.NONE
    assert ws.document is None and ws.status() == ""


def test_status_names_the_scene(workspace: MonsterWorkspace) -> None:
    assert workspace.status() == "t   host em75"


def test_keys_drive_the_transport(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.vp is not None
    ws.play_slot(1, 0.0)
    pb = ws.vp.playback
    assert ws.key(Key("Space")) and pb.playing
    assert ws.key(Key("Space")) and not pb.playing
    assert ws.key(Key("Right")) and pb.phase == pb.speed and ws.vp.frame == pb.phase
    assert ws.key(Key("Left")) and pb.phase == 0.0
    ws.seek(6.0)
    assert ws.key(Key("Home")) and pb.phase == 0.0 and ws.vp.frame == 0.0
    assert not ws.key(Key("Space", Mod.CTRL)) and not ws.key(Key("Q"))


def test_playback_animates(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.vp is not None
    ws.play_slot(1, 0.0)
    assert not ws.animating()
    ws.play_pause()
    assert ws.animating()
    for _ in range(4):
        ws.frame(1 / 30)
    assert ws.vp.frame > 0


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
    assert ws.pair == (1, 4) and ws.vp.clip is not None and ws.take_focus() == "Action"
    ws.reveal(("effect", 0))
    assert ws.vp.selected_joint == 2
    ws.reveal(("clips", "walk"))
    assert ws.edit_slot == 1 and ws.take_focus() == "Clips"
    ws.reveal("nonsense")


def test_bind_names_the_move(workspace: MonsterWorkspace) -> None:
    ws = workspace
    ws.play_slot(1)
    ws.select_pair(1, 3)
    ws.bind_move("  stop ")
    assert ws.manifest is not None and ws.manifest.moves["stop"].main == 1
    ws.select_pair(0, 3)
    ws.bind_move()
    assert "move_0_3" in ws.manifest.moves and ws.move == "move_0_3"


def test_edit_set_opens_hitboxes(workspace: MonsterWorkspace) -> None:
    ws = workspace
    ws.edit_set(3)
    assert ws.show_attacks and ws.attacks_source == "host" and ws.selected_set == 3
    assert ws.take_focus() == "Hitboxes"
    ws.edit_set(2)
    assert ws.attacks_source == "port", "the port authors set 2"


def test_the_host_beside(workspace: MonsterWorkspace) -> None:
    ws = workspace
    assert ws.vp is not None
    ws.select_pair(1, 4)
    ws.set_show_host(True)
    assert ws.vp.reference is not None and ws.host_clip == 1
    ws.browse_species(7)
    assert ws.vp.reference is None and "no host PAC for em07" in ws.message
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
    from mhfu_studio.monster.panels.action import ActionPanel
    from mhfu_studio.monster.panels.moves import MovesPanel
    from mhfu_studio.monster.panels.timeline import TimelinePanel

    ws = MonsterWorkspace()
    ws.intel_cache[75] = intel75
    scene = Scene.from_bytes(
        synthetic_pac, "t", manifest=port_doc.manifest, path=tmp_path / "t.bin"
    )
    ws.load(scene, port_doc)
    w = make_window(ws)
    for label, cls in (("Timeline", TimelinePanel), ("Moves", MovesPanel), ("Action", ActionPanel)):
        d = w.findChild(QDockWidget, f"monster/{label}")
        assert d is not None and isinstance(d.widget(), cls)
        assert kit.missing_tips(d.widget()) == []
    ws.select_pair(1, 4)
    ws.focus("Moves")
    w.studio.changed()
    w.sync()
    moves = w.findChild(QDockWidget, "monster/Moves")
    assert moves is not None and moves.isVisible()
