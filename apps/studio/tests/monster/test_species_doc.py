# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The species document: a brain file's undo, save and revert, its place, and the build cache."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mhfu.em.intel import SpeciesIntel
from mhfu.em.script import brainfile, build, ir
from mhfu.em.script.brainfile import BrainFile, BrainFileError
from mhfu.em.script.build import Built
from mhfu.em.script.editset import EditSet, set_args
from mhfu.files import Extracted
from mhfu_studio.monster import species_doc
from mhfu_studio.monster.brain import NoScript
from mhfu_studio.monster.species_doc import Builds, SpeciesDocument
from mhfu_studio.shell.document import Document

from .conftest import end, enter, header, routine

SERVES = {75: 75, 76: 75, 88: 75}


@pytest.fixture(autouse=True)
def brains(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The brains folder is tmp_path's: no test reads or writes the checkout's."""
    monkeypatch.setenv("MHFU_BRAINS", str(tmp_path / "brains"))
    return tmp_path / "brains"


def lift(row: int = 75) -> ir.Program:
    """One routine, `enter 3 0`, as a game's row."""
    items: list[ir.Item] = [
        routine("r_a", enter(3, 0), end(), va=0x2000),
        ir.Table("base_l", ["r_a"], 0x2100),
        header("h0", tuple(range(17)), base="base_l"),
    ]
    p = ir.Program(row, 0x2000, items)
    p.sha1 = "ab" * 20
    for k, n in enumerate(p.nodes()):
        n.id, n.pc = f"{row}:{0x1000 + 4 * k:08X}", 0x1000 + 4 * k
    return p


def retune(p: ir.Program, sub: int) -> Callable[[BrainFile], None]:
    """An edit of row `p.row`: its `enter` plays `sub`."""
    nid = next(n.id for n in p.nodes() if isinstance(n, ir.Insn) and n.op.name == "enter")

    def change(bf: BrainFile) -> None:
        es = bf.edits(p.row) or EditSet.of(p)
        set_args(es, p, nid, sub=sub)
        bf.put(es)

    return change


@pytest.fixture
def doc() -> SpeciesDocument:
    return SpeciesDocument.for_overlay(75)


def test_a_document(doc: SpeciesDocument) -> None:
    assert isinstance(doc, SpeciesDocument)
    d: Document = doc  # the shell's protocol
    assert d.path is not None and not d.dirty and d.findings() == []


# ---- open, save, undo


def test_save_and_open(doc: SpeciesDocument, brains: Path) -> None:
    p = lift()
    doc.edit(retune(p, 2))
    doc.edit(lambda bf: bf.pairs.__setitem__((3, 0), "roar"))
    path = doc.save()
    assert path == brains / "em75.toml" and not doc.dirty
    again = SpeciesDocument.open(path)
    assert again.brainfile == doc.brainfile and again.path == path
    assert again.brainfile.pairs == {(3, 0): "roar"}
    assert again.brainfile.edits(75) is not None
    assert path.read_text() == brainfile.dumps(doc.brainfile)


def test_undo_redo_revert(doc: SpeciesDocument) -> None:
    p = lift()
    assert not doc.can_undo() and not doc.can_redo()
    doc.edit(retune(p, 2))
    assert doc.dirty and doc.can_undo()
    doc.undo()
    assert not doc.dirty and doc.brainfile.edits(75) is None and doc.can_redo()
    doc.redo()
    assert doc.dirty and doc.brainfile.edits(75) is not None
    doc.save()
    doc.edit(retune(p, 5))
    doc.revert()
    assert not doc.dirty
    es = doc.brainfile.edits(75)
    assert es is not None and es.nodes[next(iter(es.nodes))].args == {"sub": 2}
    doc.undo()  # revert is one more step
    assert doc.dirty and doc.brainfile.edits(75) is not None
    doc.revert()
    doc.revert()  # nothing to revert
    assert not doc.dirty


def test_a_refused_edit(doc: SpeciesDocument) -> None:
    def twice(bf: BrainFile) -> None:
        bf.put(EditSet(75, "", also=[76]))
        bf.put(EditSet(76, ""))

    with pytest.raises(BrainFileError, match="row 76 is in the sections"):
        doc.edit(twice)
    assert not doc.dirty and not doc.can_undo() and not doc.brainfile.rows


def test_a_failing_change_keeps_the_state(doc: SpeciesDocument) -> None:
    def boom(bf: BrainFile) -> None:
        bf.pairs[1, 1] = "half"
        raise RuntimeError("no")

    with pytest.raises(RuntimeError):
        doc.edit(boom)
    assert doc.brainfile.pairs == {} and not doc.dirty and doc.version == 0


def test_version_counts_up(doc: SpeciesDocument) -> None:
    p = lift()
    seen = [doc.version]
    doc.edit(retune(p, 2))
    seen.append(doc.version)
    doc.undo()
    seen.append(doc.version)
    doc.redo()
    seen.append(doc.version)
    doc.revert()
    seen.append(doc.version)
    assert seen == sorted(set(seen))  # strictly up, so undo never returns to an old number
    doc.save()
    assert doc.version == seen[-1]


def test_a_snapshot_is_one_state(doc: SpeciesDocument) -> None:
    v0, bf0 = doc.snapshot()
    doc.edit(retune(lift(), 2))
    v1, bf1 = doc.snapshot()
    assert v1 > v0 and bf1 is doc.brainfile and bf0 is not bf1
    assert bf0.edits(75) is None  # the old state is not touched by the edit


def test_no_path() -> None:
    d = SpeciesDocument(brainfile.new(75))
    with pytest.raises(BrainFileError, match="no path"):
        d.save()


def test_save_as(doc: SpeciesDocument, tmp_path: Path) -> None:
    doc.edit(retune(lift(), 2))
    there = doc.save(tmp_path / "elsewhere" / "tigrex.toml")
    assert there.is_file() and doc.path == there and not doc.dirty


def test_a_file_that_will_not_read_back(doc: SpeciesDocument) -> None:
    doc.brainfile.rows[76] = EditSet(75, "")  # the file's own check says so
    (found,) = doc.findings()
    assert (found.level, found.code) == ("error", "BRAIN_FILE") and "row 76" in found.message
    assert found.where == "em75.toml"


# ---- a species' document


def test_a_species_starts_empty(brains: Path) -> None:
    d = SpeciesDocument.for_species(76, SERVES)
    assert d.path == brains / "em75.toml" and not brains.exists()
    assert (d.overlay, d.brainfile.label, d.brainfile.rows) == (75, "Tigrex", {})
    assert not d.dirty and d.findings() == []
    d.edit(retune(lift(76), 2))
    assert d.save() == brains / "em75.toml"  # the first save makes folder and file
    assert SpeciesDocument.for_species(88, SERVES).brainfile.edits(76) is not None


def test_an_overlay_has_its_file(brains: Path) -> None:
    brainfile.save(brainfile.new(75, "kept"), brains / "em75.toml")
    d = SpeciesDocument.for_overlay(75)
    assert d.brainfile.label == "kept"
    other = SpeciesDocument.for_overlay(1)  # another overlay, another file
    assert other.path == brains / "em01.toml" and other.overlay == 1


def test_a_given_folder(tmp_path: Path) -> None:
    d = SpeciesDocument.for_overlay(75, tmp_path / "mine")
    assert d.path == tmp_path / "mine" / "em75.toml"


def test_a_row_no_overlay_serves() -> None:
    assert species_doc.overlay_of(76, SERVES) == 75
    with pytest.raises(NoScript, match="row 5"):
        species_doc.overlay_of(5, SERVES)
    with pytest.raises(NoScript):
        SpeciesDocument.for_species(5, SERVES)


def test_a_bad_file_is_not_opened(brains: Path) -> None:
    brains.mkdir()
    (brains / "em75.toml").write_text("schema = 9\n")
    with pytest.raises(BrainFileError, match="schema"):
        SpeciesDocument.for_overlay(75)


# ---- the build cache


class Spy:
    """Counts `build.compile` calls, which can wait on `gate`."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls = 0
        self.gate: threading.Event | None = None
        self.inside = threading.Event()
        real = build.compile

        def compile_(bf: BrainFile, game: Extracted | None, row: int, **kw: Any) -> Built:
            self.calls += 1
            if self.gate is not None:
                self.inside.set()
                assert self.gate.wait(10)
            return real(bf, game, row, **kw)

        monkeypatch.setattr(build, "compile", compile_)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> Spy:
    return Spy(monkeypatch)


def test_builds_hit_and_miss(doc: SpeciesDocument, spy: Spy) -> None:
    p, builds = lift(), Builds(doc)
    assert builds.latest(75) is None and builds.current(75) is None
    first = builds.get(75, p)
    assert first.ok and first.id is not None and first.row == 75 and spy.calls == 1
    assert builds.get(75, p) is first and spy.calls == 1  # the same version
    assert builds.current(75) is first
    doc.edit(retune(p, 2))
    assert builds.current(75) is None and builds.latest(75) is first  # stale, still shown
    second = builds.get(75, p)
    assert spy.calls == 2 and second.id != first.id
    assert builds.latest(75) is second and builds.current(75) is second
    assert builds.get(75, p) is second and spy.calls == 2
    doc.undo()  # back to the empty file: a new version, so a build
    third = builds.get(75, p)
    assert spy.calls == 3 and third.id == first.id and builds.current(75) is third


def test_an_edit_of_another_row_is_a_hit(doc: SpeciesDocument, spy: Spy) -> None:
    p75, p88, builds = lift(75), lift(88), Builds(doc)
    doc.edit(retune(p88, 2))
    b88 = builds.get(88, p88)
    b75 = builds.get(75, p75)
    assert spy.calls == 2
    doc.edit(retune(p75, 3))  # row 75's edits; row 88's are as they were
    assert builds.current(88) is b88  # no work to know it
    assert builds.get(88, p88) is b88 and spy.calls == 2
    assert builds.current(75) is None
    assert builds.get(75, p75) is not b75 and spy.calls == 3
    doc.edit(lambda bf: bf.pairs.__setitem__((3, 0), "roar"))  # no row's edits
    assert builds.get(88, p88) is b88 and builds.get(75, p75).row == 75 and spy.calls == 3


def test_a_name_is_a_new_build(doc: SpeciesDocument, spy: Spy) -> None:
    p, builds = lift(), Builds(doc)
    first = builds.get(75, p)
    doc.edit(lambda bf: bf.put(EditSet(75, p.sha1, names={"r_a": "start"})))
    second = builds.get(75, p)  # the build keeps its edit set, so a name is a new build
    assert second is not first and second.id == first.id and spy.calls == 2


def test_another_lift_or_intel_builds_again(
    doc: SpeciesDocument, spy: Spy, intel75: SpeciesIntel
) -> None:
    p, builds = lift(), Builds(doc)
    first = builds.get(75, p)
    again = builds.get(75, lift())  # the same program, another decode
    assert again is not first and again.lift is not first.lift and spy.calls == 2
    withintel = builds.get(75, again.lift, intel75)
    assert withintel is not again and spy.calls == 3
    assert builds.get(75, again.lift, intel75) is withintel and spy.calls == 3


def test_the_checks_of_compile_hold(doc: SpeciesDocument, intel75: SpeciesIntel) -> None:
    builds = Builds(doc)
    with pytest.raises(ValueError, match="row 75's, not row 76's"):
        builds.get(76, lift(75))
    other = SpeciesDocument.for_overlay(1)
    with pytest.raises(ValueError, match="em75's, the file em01's"):
        Builds(other).get(75, lift(75), intel75)
    assert builds.latest(76) is None


def test_the_gui_reads_while_a_worker_builds(doc: SpeciesDocument, spy: Spy) -> None:
    p, builds = lift(), Builds(doc)
    first = builds.get(75, p)
    doc.edit(retune(p, 2))
    spy.gate = threading.Event()
    got: list[Built] = []
    worker = threading.Thread(target=lambda: got.append(builds.get(75, p)))
    worker.start()
    assert spy.inside.wait(10)
    assert builds.latest(75) is first and builds.current(75) is None  # not blocked
    doc.edit(retune(p, 3))  # the GUI goes on editing meanwhile
    spy.gate.set()
    worker.join(10)
    (done,) = got
    assert done.id != first.id
    assert builds.latest(75) is done  # kept, but the document has moved on
    assert builds.current(75) is None
    newest = builds.get(75, p)
    assert newest.id not in (first.id, done.id) and builds.current(75) is newest


def test_an_older_build_never_replaces_a_newer(doc: SpeciesDocument, spy: Spy) -> None:
    p, builds = lift(), Builds(doc)
    doc.edit(retune(p, 2))
    spy.gate = threading.Event()
    got: list[Built] = []
    worker = threading.Thread(target=lambda: got.append(builds.get(75, p)))
    worker.start()
    assert spy.inside.wait(10)
    doc.edit(retune(p, 3))
    held, spy.gate = spy.gate, None
    newest = builds.get(75, p)  # a second caller finishes first
    held.set()
    worker.join(10)
    assert builds.latest(75) is newest and got[0] is not newest


# ---- the real game


def test_a_real_em75_edit_builds(mhfu_data: Path, doc: SpeciesDocument) -> None:
    game = Extracted.find(mhfu_data)
    p = build.lift_row(game, 75, 75)
    builds = Builds(doc)
    empty = builds.get(75, p)
    assert empty.ok and empty.container is not None and empty.program is p
    nid = next(
        n.id
        for n in p.nodes()
        if isinstance(n, ir.Insn) and n.op.name == "enter" and n.args.get("sub") != 1
    )

    def change(bf: BrainFile) -> None:
        es = bf.edits(75) or EditSet.of(p)
        set_args(es, p, nid, sub=1)
        bf.put(es)

    doc.edit(change)
    edited = builds.get(75, p)
    assert edited.ok and edited.id != empty.id and edited.program is not p
    assert not [f for f in edited.findings if f.level == "error"]
    path = doc.save()
    assert SpeciesDocument.open(path).brainfile == doc.brainfile
    doc.undo()
    assert builds.get(75, p).id == empty.id
