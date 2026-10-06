# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
from mhfu.live.clips import Played
from mhfu_studio.monster import clip_game, clips
from mhfu_studio.monster.panels.clips import ClipsPanel
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit, theme


def build(ws: MonsterWorkspace, qtbot: Any) -> ClipsPanel:
    studio = Studio([ws])
    p = ClipsPanel(ws, studio)
    qtbot.addWidget(p)
    studio.listen(p.sync)
    p.sync()
    return p


def test_empty(qtbot: Any) -> None:
    p = build(MonsterWorkspace(), qtbot)
    assert p.pages.currentWidget() is p.pages.empty and kit.missing_tips(p) == []


def test_loaded(workspace: MonsterWorkspace, qtbot: Any) -> None:
    p = build(workspace, qtbot)
    assert p.pages.currentWidget() is p.pages.page and kit.missing_tips(p) == []
    assert p.table.rowCount() == 2 and p.count.text() == "2 clips, 1 looping"
    assert not p.kind_row.isVisibleTo(p) and p.notes.isVisibleTo(p)


def test_travel_fits_a_dock(workspace: MonsterWorkspace, qtbot: Any) -> None:
    p = build(workspace, qtbot)
    p.resize(317, 700)
    p.show()
    t, h = p.table, p.table.horizontalHeader()
    assert t.horizontalHeaderItem(3).text() == "Travel" and t.item(0, 2).text().endswith("loop")
    assert h.sectionPosition(3) + h.sectionSize(3) <= t.viewport().width()


def test_pick_name_save(workspace: MonsterWorkspace, qtbot: Any) -> None:
    ws, p = workspace, build(workspace, qtbot)
    assert ws.doc is not None and ws.scene is not None and ws.vp is not None
    p.table.cellClicked.emit(1, 0)
    assert ws.vp.clip is not None and ws.vp.clip.slot == 2 and ws.edit_slot == 2
    assert p.name.text() == "clip_02" and p.table.currentRow() == 1
    p.name.setText("strike")
    p.label.setText("the head comes down")
    p.apply.click()
    assert ws.doc.dirty and ws.doc.manifest.clips["strike"].slot == 2
    assert ws.scene.clip(2).names == ("strike",)
    p.studio.save()
    assert not ws.doc.dirty


def test_outside_pick_shows(workspace: MonsterWorkspace, qtbot: Any) -> None:
    ws, p = workspace, build(workspace, qtbot)
    ws.play_slot(1)
    p.sync()
    assert p.table.currentRow() == 0 and p.name.text() == "walk"


def test_filter(workspace: MonsterWorkspace, qtbot: Any) -> None:
    p = build(workspace, qtbot)
    p.filter.setText("walk")
    assert workspace.clip_filter == "walk" and p.table.rowCount() == 1


def test_filler_is_loud(workspace: MonsterWorkspace, qtbot: Any) -> None:
    ws = workspace
    cov = clips.Coverage(has_source=True)
    cov.slots[1] = clips.SlotCoverage(1, clips.CARRIED, 10, True)
    cov.slots[2] = clips.SlotCoverage(2, clips.FILLER, 6, False)
    ws._coverage, ws._vocab = (cov, []), None
    p = build(ws, qtbot)
    assert p.filler.isVisibleTo(p) and "idle copies" in p.filler.text()
    assert p.kinds[clips.FILLER][1].text() == "1 idle copy"
    assert not p.kinds[clips.HOST][1].isVisibleTo(p), "a kind nobody has is not listed"
    item = p.table.item(1, 0)
    assert item is not None and item.foreground().color() == theme.level("warning")
    assert "idle copy" in item.toolTip()
    p.filter.setText("idle")
    assert p.table.rowCount() == 1, "the filter reads the kind's words"
    p.filter.setText("")
    p.table.cellClicked.emit(1, 0)
    assert p.why.property("level") == "warning"


# ---- the Zinogre: every clip of the original by MHP3rd id ----


@pytest.fixture
def zinogre(games: Any, qtbot: Any, zinogre_toml: Path) -> ClipsPanel:
    path = zinogre_toml
    ws = MonsterWorkspace(games)
    ws.intel_cache[75] = None
    ws.open(path)
    return build(ws, qtbot)


def ids(p: ClipsPanel) -> list[str]:
    return [p.table.item(r, 0).text() for r in range(p.table.rowCount())]


def test_every_clip(zinogre: ClipsPanel) -> None:
    p = zinogre
    assert p.table.rowCount() == 102 and not p.table.isColumnHidden(0)
    named = len(p.ws.doc.manifest.clips)
    assert p.count.text() == f"102 clips in 3 streams, {named} named"
    assert kit.missing_tips(p) == []
    assert ids(p)[:3] == ["1", "2", "4"] and p.table.item(101, 1).text() == "56"
    p.filter.setText("stream 2")
    assert p.table.rowCount() == 39
    p.filter.setText("unnamed")
    assert p.table.rowCount() == 102 - named


def test_name_and_next(zinogre: ClipsPanel) -> None:
    """Return in Shows applies and plays the next clip, its name ready to type over."""
    p, ws = zinogre, zinogre.ws
    p.table.setCurrentCell(0, 0)
    assert ws.edit_clip == 1 and p.name.text() == "clip_01" and p.anim.value() == 1
    p.name.setText("idle")
    p.label.setText("stands and breathes")
    p.label.returnPressed.emit()
    idle = ws.doc.manifest.clips["idle"]
    assert (idle.slot, idle.id, idle.label) == (None, 1, "stands and breathes")
    assert ws.edit_clip == 2 and p.name.text() == "welcome_howl"
    assert p.label.text() == "the howling he does when he notices you"
    p.name.setText("welcome howl")
    p.label.returnPressed.emit()
    assert ws.edit_clip == 2 and "not a name" in ws.message, "a refusal stays on the clip"


def test_place(zinogre: ClipsPanel) -> None:
    p, ws = zinogre, zinogre.ws
    row = ids(p).index("248")
    p.table.cellClicked.emit(row, 0)
    assert ws.edit_clip == 248 and p.anim.value() == 50
    p.anim.setValue(7)
    p.place.click()
    assert ws.message == "clip 7 anim 7 -> anim 50; clip 248 anim 50 -> anim 7"
    assert p.table.item(row, 1).text() == "7" and ws.doc.manifest.clips["clip_07"].source == 248


def test_play_in_game(zinogre: ClipsPanel, monkeypatch: pytest.MonkeyPatch) -> None:
    p, ws, sent = zinogre, zinogre.ws, []

    def force(s: object, entry: int, species: int | None) -> clip_game.Held:
        sent.append((entry, species))
        return clip_game.Held(3, Played(entry, (), True, False, 0.1, (1, 0)))

    monkeypatch.setattr(clip_game, "force", force)
    ws.game_session = lambda: nullcontext(None)  # type: ignore[assignment,arg-type,return-value]
    p.table.cellClicked.emit(ids(p).index("248"), 0)
    p.in_game.click()
    assert sent == [(50, 75)] and ws.message.startswith("anim 50 held on monster 3 until Release")
