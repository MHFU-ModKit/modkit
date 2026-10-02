# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A finding clicked lands on the Parts or Hitboxes control that fixes it."""

from typing import Any

from mhfu_port.manifest import Hitbox
from mhfu_studio.monster import validate as V
from mhfu_studio.monster.panels.hitboxes import HitboxesPanel
from mhfu_studio.monster.panels.parts import PartsPanel
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.findings import Finding
from mhfu_studio.shell.studio import Studio


def shown(cls: Any, ws: MonsterWorkspace, qtbot: Any) -> Any:
    p = cls(ws, Studio([ws]))
    qtbot.addWidget(p)
    p.resize(320, 260)
    p.show()
    qtbot.waitExposed(p)
    p.sync()
    return p


def finding(ws: MonsterWorkspace, code: str) -> Finding:
    assert ws.doc is not None
    return next(f for f in ws.doc.findings() if f.code == code)


def test_an_unnamed_part_lands_on_its_name(workspace: MonsterWorkspace, qtbot: Any) -> None:
    ws = workspace
    f = finding(ws, "HURTBOX_PART_UNNAMED")
    assert f.target == ("hurtbox", 1) and f.focus == V.PART_NAME
    p = shown(PartsPanel, ws, qtbot)
    ws.reveal(f.target, f.focus)
    assert ws.take_focus() == "Parts" and ws.selected_part == 2
    p.sync()
    assert ws.landing == "" and p.name_row.isVisibleTo(p)
    qtbot.waitUntil(lambda: p.focusWidget() is p.part_name)


def test_a_group_over_capacity_lands_on_its_row(workspace: MonsterWorkspace, qtbot: Any) -> None:
    ws = workspace
    sess = ws.attack_session
    assert sess is not None
    for _ in range(2):
        sess.add_volume(Hitbox(bone=1, radius=5.0, set=2))
    f = finding(ws, "HITBOX_OVER_CAPACITY")
    assert f.target == ("set", 2) and f.focus == V.HIT_GROUP
    p = shown(HitboxesPanel, ws, qtbot)
    ws.reveal(f.target, f.focus)
    assert ws.selected_set == 2 and ws.attacks_source == "port"
    p.sync()
    row = p.sets.currentRow()
    assert row >= 0 and p.sets.item(row, 0).text() == "2"
    qtbot.waitUntil(lambda: p.focusWidget() is p.sets)


def test_an_empty_attack_opens_more(workspace: MonsterWorkspace, qtbot: Any) -> None:
    ws = workspace
    assert ws.attack_session is not None
    ws.attack_session.set_attack(7, power=None)
    f = finding(ws, "ATTACK_EMPTY")
    p = shown(HitboxesPanel, ws, qtbot)
    ws.reveal(f.target, f.focus)
    assert ws.selected_set == 3, "the base monster's group for attack 7"
    p.sync()
    assert p.more.toggle.isChecked() and p.records.rowCount() == 1
    qtbot.waitUntil(lambda: p.focusWidget() is p.records)


def test_a_joint_off_the_rig_lands_on_the_joint(workspace: MonsterWorkspace, qtbot: Any) -> None:
    ws = workspace
    f = finding(ws, "HURTBOX_BONE_RANGE")
    p = shown(PartsPanel, ws, qtbot)
    ws.reveal(f.target, f.focus)
    p.sync()
    assert p.form_box.isVisibleTo(p)
    qtbot.waitUntil(lambda: p.focusWidget() is p.form.bone)
