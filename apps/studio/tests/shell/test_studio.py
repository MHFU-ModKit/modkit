# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path

import pytest
from mhfu_studio.shell.studio import Studio, doc_name
from mhfu_studio.shell.testing import FakeDocument, FakeWorkspace


@pytest.fixture
def studio() -> Studio:
    return Studio([FakeWorkspace("map", ".toml"), FakeWorkspace("monster", ".pac")])


def dirty(studio: Studio, name: str = "map", path: Path | None = None) -> FakeWorkspace:
    ws = studio.workspace(name)
    assert isinstance(ws, FakeWorkspace)
    ws.doc = FakeDocument(path)
    ws.doc.edit("x")
    return ws


def answer(studio: Studio, got: str) -> list[list[str]]:
    asked: list[list[str]] = []
    studio.ask_discard = lambda names: asked.append(names) or got
    return asked


def doc_file(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_text("a b")
    return p


def test_open_asks_for_the_target(studio: Studio, tmp_path: Path) -> None:
    dirty(studio, "monster")
    asked = answer(studio, "cancel")
    assert studio.open(doc_file(tmp_path, "a.toml")) and asked == []
    ws = dirty(studio)
    assert not studio.open(doc_file(tmp_path, "b.toml")) and studio.message == ""
    assert asked == [["untitled map"]] and ws.doc is not None and ws.doc.path is None


def test_open_discards(studio: Studio, tmp_path: Path) -> None:
    dirty(studio)
    answer(studio, "discard")
    assert studio.open(doc_file(tmp_path, "a.toml"))
    assert doc_name(studio.active) == "a.toml"


def test_open_saves_first(studio: Studio, tmp_path: Path) -> None:
    ws = dirty(studio)
    old = ws.doc
    answer(studio, "save")
    assert not studio.open(doc_file(tmp_path, "a.toml"))  # untitled, and nobody to ask
    assert "not saved" in studio.message and ws.doc is old
    studio.ask_path = lambda w: None
    assert not studio.open(doc_file(tmp_path, "a.toml")) and studio.message == ""
    studio.ask_path = lambda w: tmp_path / f"{w.name}.toml"
    assert studio.open(doc_file(tmp_path, "a.toml"))
    assert old is not None and old.saved_to == [tmp_path / "map.toml"]


def test_discard_ok_names_every_dirty_one(studio: Studio, tmp_path: Path) -> None:
    assert studio.discard_ok(*studio.workspaces)
    dirty(studio, "map", tmp_path / "m.toml")
    dirty(studio, "monster", tmp_path / "k.pac")
    assert studio.discard_ok(*studio.workspaces)  # no hook: nobody to ask
    asked = answer(studio, "save")
    assert studio.discard_ok(*studio.workspaces)
    assert asked == [["m.toml", "k.pac"]]
    assert not any(w.document is not None and w.document.dirty for w in studio.workspaces)


def test_save_asks_for_a_path(studio: Studio, tmp_path: Path) -> None:
    ws = dirty(studio)
    studio.ask_path = lambda w: tmp_path / "x.toml"
    assert studio.save() and ws.doc is not None and ws.doc.path == tmp_path / "x.toml"


def test_workspace_message(studio: Studio) -> None:
    ws = studio.active
    studio.act("edit", lambda: setattr(ws, "message", "move refused: too far"))()
    assert studio.message == "move refused: too far"
    studio.message = "saved"
    studio.changed()
    assert studio.message == "saved"
    studio.act("clear", lambda: setattr(ws, "message", ""))()
    assert studio.message == "saved"
    other = studio.workspace("monster")
    other.message = "elsewhere"
    studio.changed()
    assert studio.message == "saved"
    studio.switch("monster")
    assert studio.message == "elsewhere"


def test_repeated_refusal_shows(studio: Studio) -> None:
    ws = studio.active
    refuse = studio.act("move", lambda: setattr(ws, "message", "move refused: too far"))
    refuse()
    studio.save()
    assert studio.message == "nothing to save"
    refuse()
    assert studio.message == "move refused: too far" and ws.said == 2
