# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The start page's controller side: when it shows, choices, recents, resume, warm-ups."""

import threading
from collections.abc import Hashable, Sequence
from pathlib import Path

import pytest
from mhfu_studio.shell import places, settings
from mhfu_studio.shell.studio import Studio
from mhfu_studio.shell.testing import FakeDocument, FakeWorkspace
from mhfu_studio.shell.workspace import Choice, Job, Shelf, Warmup


class Fresh(FakeWorkspace):
    """Nothing worth showing until a document opens; `key` "a" opens one, a warm-up first."""

    def __init__(self, name: str = "map", suffix: str = ".toml") -> None:
        super().__init__(name, suffix)
        self.gate = threading.Event()
        self.warmed: list[object] = []
        self.ends: list[tuple[Job, bool]] = []
        self.need: places.Place | None = None

    def shown(self) -> Hashable | None:
        return None if self.doc is None else self.doc.path

    def start(self) -> Sequence[Shelf]:
        return (Shelf("Things", (Choice("A", "Opens a", key="a"),)),)

    def choose(self, key: str) -> None:
        if key != "a":
            raise ValueError(f"no {key}")
        self.doc = FakeDocument(Path(key))

    def open(self, path: Path) -> None:
        if self.need is not None:
            places.path(self.need)
        super().open(path)

    def warmup(self, path: Path) -> Warmup | None:
        if path.suffix != ".slow":
            return None
        return Warmup("warming", lambda: self.gate.wait(5) and "hot", self.warmed.append)

    def ended(self, job: Job, ok: bool) -> None:
        self.ends.append((job, ok))


@pytest.fixture
def studio() -> Studio:
    return Studio([Fresh(), Fresh("monster", ".slow")])


def doc(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name
    p.write_text("x y")
    return p


def test_shows_while_nothing_does(studio: Studio, tmp_path: Path) -> None:
    assert studio.on_start()
    assert studio.choose(Choice("A", "", key="a")) and not studio.on_start()
    studio.show_start()
    assert studio.on_start()
    studio.workspace("map").doc = FakeDocument(tmp_path / "b")  # something else came up
    assert not studio.on_start()
    studio.show_start()
    studio.show_start(False)
    assert not studio.on_start()


def test_a_refused_choice_says_why(studio: Studio) -> None:
    assert not studio.choose(Choice("B", "", key="b"))
    assert studio.message == "could not open B: no b" and studio.on_start()


def test_recent_and_resume(studio: Studio, tmp_path: Path) -> None:
    a, b = doc(tmp_path, "a.toml"), doc(tmp_path, "b.toml")
    assert studio.open(a) and studio.open(b) and studio.open(a)
    assert studio.recent() == [a.resolve(), b.resolve()]
    assert settings.store.get("last/map") == f"open:{a.resolve()}"
    b.unlink()
    assert studio.recent() == [a.resolve()]
    again = Studio([Fresh(), Fresh("monster", ".slow")])
    again.resume()
    assert again.active.document is not None and again.active.document.path == a.resolve()
    again.resume()  # once a run
    studio.choose(Choice("A", "", key="a"))
    assert settings.store.get("last/map") == "choose:a"
    third = Studio([Fresh(), Fresh("monster", ".slow")])
    third.resume()
    assert not third.on_start()


def test_first_run_stays(studio: Studio) -> None:
    studio.resume()
    assert studio.on_start() and studio.message == ""


def test_open_after_the_warmup(studio: Studio, tmp_path: Path) -> None:
    slow = doc(tmp_path, "m.slow")
    assert not studio.open_later(slow)
    ws = studio.workspace("monster")
    assert studio.active is ws and studio.on_start() and studio.message == "warming…"
    assert studio.opening is not None and not studio.open_later(slow)
    assert studio.message.startswith("busy: opening m.slow")
    studio.poll()
    assert studio.opening is not None and ws.document is None
    ws.gate.set()  # type: ignore[attr-defined]
    studio.wait()
    assert studio.opening is None and ws.warmed == ["hot"]  # type: ignore[attr-defined]
    assert ws.document is not None and not studio.on_start()
    assert studio.open_later(doc(tmp_path, "c.toml"))  # no warm-up: at once


def test_missing_place_opens_the_start_page(
    studio: Studio, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MHP3RD_DATA", raising=False)
    ws = studio.workspace("map")
    ws.need = places.MHP3RD  # type: ignore[attr-defined]
    studio.switch("monster")
    assert not studio.open(doc(tmp_path, "a.toml"))
    assert studio.active is ws and studio.on_start()
    assert studio.message == (
        "could not open a.toml: no MHP3rd extraction found (choose it under Setup on the start"
        " page)"
    )


def test_jobs_end_in_their_workspace(studio: Studio) -> None:
    job = Job("x", ("--help",))
    studio.start(job)
    assert studio.workspace("map").ends == [(job, True)]  # type: ignore[attr-defined]
