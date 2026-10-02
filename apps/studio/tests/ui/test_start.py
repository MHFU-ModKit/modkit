# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from collections.abc import Callable, Hashable, Sequence
from pathlib import Path
from typing import Any

import pytest
from mhfu_studio.shell import places, settings
from mhfu_studio.shell.testing import FakeDocument
from mhfu_studio.shell.workspace import Choice, Shelf, Step, Warmup
from mhfu_studio.ui import dialogs, kit, steps
from mhfu_studio.ui.start import StartPage
from mhfu_studio.ui.testing import FakeWorkspace
from mhfu_studio.ui.window import Window
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton

Make = Callable[..., Window]


class Fresh(FakeWorkspace):
    """Nothing to show until "a" is chosen; two steps, the first done once a document is open."""

    def __init__(self, name: str = "map", suffix: str = ".toml") -> None:
        super().__init__(name, suffix)
        self.warm: Warmup | None = None

    def shown(self) -> Hashable | None:
        return None if self.doc is None else self.doc.path

    def start(self) -> Sequence[Shelf]:
        a = Choice("Area A", "Loads area A", key="a", detail="st001", group="Map one")
        return (Shelf("Pick", (a,), "More in the Areas panel", ("Open…", "Opens a file")),)

    def choose(self, key: str) -> None:
        self.doc = FakeDocument(Path(key))

    def warmup(self, path: Path) -> Warmup | None:
        return self.warm

    def next_steps(self) -> Sequence[Step]:
        if self.doc is None:
            return ()
        return (Step("Pick it", bool(self.doc.history.value)), Step("Send", key="Ctrl+Return"))


@pytest.fixture
def none(monkeypatch: pytest.MonkeyPatch) -> None:
    for p in places.PLACES:
        monkeypatch.delenv(p.env, raising=False)
    monkeypatch.setattr(places.inject, "MEMSTICK_ROOTS", ())


def page(w: Window) -> StartPage:
    w.sync()
    assert w.stage.currentWidget() is w.start_page
    return w.start_page


def test_places_are_saved_settings(
    make_window: Make, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MHFU_DATA", raising=False)
    w = make_window(show=False)
    (tmp_path / "g" / "data_files").mkdir(parents=True)
    assert w.studio.remember(places.MHFU, tmp_path / "g")
    assert w.settings.value("places/mhfu") == str(tmp_path / "g")
    assert places.find(places.MHFU).source == "saved"
    settings.store.put("places/mhfu", None)
    assert w.settings.value("places/mhfu") is None


def test_shows_in_the_views_place(make_window: Make, none: None) -> None:
    w = make_window(Fresh(), FakeWorkspace("monster"))
    p = page(w)
    assert kit.missing_tips(p) == [] and set(p.choose) == {"mhfu", "mhp3rd", "memstick"}
    text = [lb.text() for lb in p.findChildren(QLabel)]
    assert "No MHFU extraction found" in text
    assert "Maps and monsters need the MHFU extraction." in text
    assert [t.label.text() for t in p.tiles] == ["Area A"] and "Map one" in text
    w.studio.switch("monster")
    w.sync()
    assert w.stage.currentWidget() is w.view  # a workspace that always has a view


def test_choose_a_folder(
    make_window: Make,
    none: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    asked: list[Any],
) -> None:
    w = make_window(Fresh())
    p = page(w)
    (tmp_path / "g" / "data_files").mkdir(parents=True)
    monkeypatch.setattr(dialogs, "ask_folder", lambda parent, place: tmp_path / "g")
    p.choose["mhfu"].click()
    assert w.settings.value("places/mhfu") == str(tmp_path / "g") and asked == []
    p = page(w)
    labels = [lb.text() for lb in p.findChildren(QLabel)]
    assert any(t.endswith("/g (chosen)") for t in labels)
    monkeypatch.setattr(dialogs, "ask_folder", lambda parent, place: tmp_path / "nope")
    p.choose["mhp3rd"].click()
    assert asked and asked[-1][0] == "Not used" and w.settings.value("places/mhp3rd") is None


def test_pick_back_and_recent(make_window: Make, none: None, tmp_path: Path) -> None:
    w = make_window(Fresh())
    page(w).tiles[0].click()
    w.sync()
    assert w.stage.currentWidget() is w.view and w.studio.active.document is not None
    w.start_action.trigger()
    back = [b for b in page(w).findChildren(QPushButton) if b.text().startswith("Back to")]
    assert len(back) == 1
    back[0].click()
    w.sync()
    assert w.stage.currentWidget() is w.view
    doc = tmp_path / "d" / "x.toml"
    doc.parent.mkdir()
    doc.write_text("a")
    assert w.studio.open(doc)
    w.start_action.trigger()
    assert [t.label.text() for t in page(w).tiles] == ["Area A", "d/x.toml"]


def test_waits_for_the_warmup(make_window: Make, none: None, tmp_path: Path) -> None:
    ws = Fresh()
    gate: list[bool] = []
    ws.warm = Warmup("Warming it up", lambda: gate)
    w = make_window(ws)
    doc = tmp_path / "x.toml"
    doc.write_text("a")
    assert not w.studio.open_later(doc)
    p = page(w)
    assert p.busy.isVisibleTo(p) and w.studio.opening is not None
    w.studio.wait()
    w.sync()
    assert w.stage.currentWidget() is w.view and ws.document is not None


def test_next_steps(make_window: Make, none: None, qtbot: Any) -> None:
    ws = Fresh()
    w = make_window(ws, FakeWorkspace("monster"))
    assert not w.steps.isVisibleTo(w)
    w.studio.choose(Choice("A", "", key="a"))
    w.sync()
    assert w.steps.isVisibleTo(w) and kit.missing_tips(w.steps) == []
    texts = [lb.text() for lb in w.steps.labels]
    assert texts[0] == "1 Pick it" and texts[1].startswith("2 Send (")
    assert w.steps.labels[0].property("role") == "next"
    ws.add_item()
    w.sync()
    assert w.steps.labels[0].text() == f"{steps.TICK} Pick it"
    assert w.steps.labels[1].property("role") == "next"
    w.steps.close_button.click()
    w.sync()
    assert not w.steps.isVisibleTo(w) and not w.steps_action.isChecked()
    assert w.settings.value("steps/map") == "hidden"
    w.steps_action.trigger()
    w.sync()
    assert w.steps.isVisibleTo(w) and w.settings.value("steps/map") is None
    qtbot.keyClick(w.view, Qt.Key.Key_Escape)  # the view keeps its keys
