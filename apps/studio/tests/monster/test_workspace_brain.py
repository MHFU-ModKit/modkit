# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The workspace over a brain file: opening, builds and the debounce, gestures, findings, Send,
Push, Follow and Count. Offscreen, with fakes for the game; the em75 tests need MHFU_DATA."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
from mhfu import files, inject
from mhfu.em.intel import SpeciesIntel
from mhfu.em.script import brainfile, build, ir
from mhfu.em.script import catalogue as cat
from mhfu.em.script.edit import EditError
from mhfu.files import Extracted
from mhfu.live.script import Step, Trace
from mhfu.writes import Plan, Pushed
from mhfu_port.data import Data
from mhfu_studio.monster import brain_edit as be
from mhfu_studio.monster import brain_game as bg
from mhfu_studio.monster import brain_graph
from mhfu_studio.monster import workspace as mw
from mhfu_studio.monster.brain import BrainView
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell import places
from mhfu_studio.shell.document import Document
from mhfu_studio.shell.findings import worst
from mhfu_studio.shell.studio import Studio
from ppsspp_debug import DebuggerError

from .conftest import end, enter, header, routine

PAC = f"file_{files.monster_pac(75):05d}.bin"


def lift(row: int = 75) -> ir.Program:
    """One routine, `enter 3 0`, as a game's row; its nodes lie inside the program's region."""
    items: list[ir.Item] = [
        routine("r_a", enter(3, 0), end(), va=0x2000),
        ir.Table("base_l", ["r_a"], 0x2100),
        header("h0", tuple(range(17)), base="base_l"),
    ]
    p = ir.Program(row, 0x2000, items, 0x2100)
    p.sha1 = "ab" * 20
    for k, n in enumerate(p.nodes()):
        n.id, n.pc = f"{row}:{0x2000 + 4 * k:08X}", 0x2000 + 4 * k
    return p


HUB = "75:00002000"


class Clock:
    """The debounce's clock: a test moves it."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class Inline:
    """An executor that runs a call as it is asked."""

    def submit(self, fn: Callable[..., Any], *args: Any) -> Future[Any]:
        done: Future[Any] = Future()
        try:
            done.set_result(fn(*args))
        except Exception as e:
            done.set_exception(e)
        return done

    def shutdown(self, *_: Any, **__: Any) -> None:
        pass


@pytest.fixture
def game(tmp_path: Path, synthetic_pac: bytes) -> Extracted:
    """An extraction with Tigrex's, its sibling 76's and a small monster's model PAC."""
    (tmp_path / "data_files").mkdir()
    for sp in (75, 76, 5):
        (tmp_path / "data_files" / f"file_{files.monster_pac(sp):05d}.bin").write_bytes(
            synthetic_pac
        )
    return Extracted(tmp_path)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test finds the owner's memory stick (`stick` makes one) or writes their cache."""
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", (str(tmp_path / "no-stick"),))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


@pytest.fixture
def ws(
    game: Extracted, intel75: SpeciesIntel, clock: Clock, synthetic_pac: bytes
) -> Iterator[MonsterWorkspace]:
    """The Tigrex PAC open on an empty brain file, over a fake decode of rows 75 and 76."""
    w = MonsterWorkspace(Data(game, game))
    w.intel_cache[75] = intel75
    w.decode_brain = lambda g, row: lift(row)
    w.scan_brain = lambda g: {75: 75, 76: 75}
    w.clock = clock
    w.open(game.path(files.monster_pac(75)))
    yield w
    w.close()


def settle(w: MonsterWorkspace, clock: Clock, row: int = 75) -> Any:
    """What `brain_built` gives once nothing is pending: the panel's timer, until the worker is
    done; the clock runs past the debounce each turn."""
    stop = time.monotonic() + 20
    while True:
        built = w.brain_built(row)
        if built is not None and not w.brain_pending(row):
            return built
        assert time.monotonic() < stop, "the worker did not finish"
        clock.t += mw.DEBOUNCE
        time.sleep(0.002)


def pair(w: MonsterWorkspace, main: int, sub: int) -> bool:
    return w.edit_brain(be.set_pair(HUB, main, sub))


# ---- opening


def test_a_monster_pac_takes_its_overlays_file(ws: MonsterWorkspace, game: Extracted) -> None:
    d = ws.species_doc
    assert d is not None and d.overlay == 75 and d.path == places.brain_file(75)
    assert not d.dirty and d.brainfile.rows == {} and not d.path.exists()  # empty, in memory
    assert ws.document is d and ws.doc is None and ws.manifest is None
    assert isinstance(ws.document, mw.SpeciesDocument) and ws.brain_species == 75
    doc: Document = ws.document  # the shell's protocol: undo, save, dirty
    assert doc.findings() == []
    assert ws.send_label == mw.SEND_BRAIN and ws.take_focus() == "Brain"
    assert ws.shown() == (d.path, ws.scene.name if ws.scene else "") and ws.status().endswith(
        "brain"
    )


def test_the_overlays_file_is_read_when_there_is_one(
    game: Extracted, tmp_path: Path, intel75: SpeciesIntel
) -> None:
    bf = brainfile.new(75, "Tigrex")
    bf.pairs[3, 0] = "roar"
    brainfile.save(bf, places.brain_file(75))
    w = MonsterWorkspace(Data(game, game))
    w.scan_brain = lambda g: {75: 75, 76: 75}
    try:
        w.open(game.path(files.monster_pac(76)))  # a sibling row is served by em75
        assert w.species_doc is not None and w.species_doc.brainfile.pairs == {(3, 0): "roar"}
        assert w.brain_species == 76 and w.brain_editable(76) and w.brain_editable(75)
        assert not w.brain_editable(1)
    finally:
        w.close()


def test_a_monster_nothing_serves_has_no_file(game: Extracted) -> None:
    w = MonsterWorkspace(Data(game, game))
    w.scan_brain = lambda g: {75: 75}
    try:
        w.open(game.path(files.monster_pac(5)))
        assert w.species_doc is None and w.document is None and w.send_label == mw.SEND_HITBOXES
        assert not w.brain_editable(5) and w.brain_built(5) is None
    finally:
        w.close()


def test_a_file_that_does_not_read_leaves_the_monster(game: Extracted) -> None:
    path = places.brain_file(75)
    path.parent.mkdir(parents=True)
    path.write_text('schema = 1\noverlay = "x"\n')
    w = MonsterWorkspace(Data(game, game))
    w.scan_brain = lambda g: {75: 75}
    try:
        w.open(game.path(files.monster_pac(75)))
        assert w.species_doc is None and w.scene is not None
        assert "its brain file was not read" in w.message and "overlay" in w.message
    finally:
        w.close()


def test_a_species_file_opens_on_its_monster(game: Extracted, intel75: SpeciesIntel) -> None:
    path = places.brain_file(75)
    brainfile.save(brainfile.new(75), path)
    w = MonsterWorkspace(Data(game, game))
    try:
        assert w.can_open(path)
        w.open(path)
        assert w.species_doc is not None and w.species_doc.path == path and w.doc is None
        assert w.scene is not None and w.scene.path == game.path(files.monster_pac(75))
        assert w.brain_species == 75 and w.take_focus() == "Brain"
    finally:
        w.close()


def test_what_a_brain_file_is(tmp_path: Path, ports: Path) -> None:
    w = MonsterWorkspace()
    odd = tmp_path / "map.toml"
    odd.write_text("[stage]\nn = 1\n")
    broken = tmp_path / "em75.toml"
    broken.write_text("schema = 1\noverlay = 75\n[row\n")
    assert not w.can_open(odd) and not w.can_open(broken)
    bad = tmp_path / "em01.toml"
    bad.write_text('schema = 1\noverlay = "one"\n')
    assert w.can_open(bad) and w.can_open(ports / "zinogre.toml")
    assert mw.toml_kind(bad) == "brain" and mw.toml_kind(ports / "zinogre.toml") == "port"


def test_the_warmup_reads_the_file_off_the_gui_thread(
    game: Extracted, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = MonsterWorkspace(Data(game, game))
    scans: list[threading.Thread] = []

    def scan(g: Extracted) -> dict[int, int]:
        scans.append(threading.current_thread())
        return {75: 75}

    w.scan_brain = scan
    pac = game.path(files.monster_pac(75))
    try:
        warm = w.warmup(pac)
        assert warm is not None and warm.done is not None
        warm.done(warm.run())
        monkeypatch.setattr(Scene, "from_path", None)  # open reads nothing more
        monkeypatch.setattr(w, "scan_brain", None)
        w.open(pac)
        assert w.species_doc is not None and len(scans) == 1
        assert w.brain_rows() == {75: 75}  # the scan is kept
        assert w.warmup(game.path(files.monster_pac(5)).with_name("x.pac")) is None
    finally:
        w.close()


def test_a_port_keeps_its_own_document(
    ws: MonsterWorkspace, port_doc: Any, synthetic_pac: bytes
) -> None:
    ws.load(Scene.from_bytes(synthetic_pac, "t", manifest=port_doc.manifest), port_doc)
    assert ws.species_doc is None and ws.document is port_doc and ws.send_label == mw.SEND_HITBOXES
    assert not ws.brain_editable(75) and ws.brain_built(75) is None
    assert not ws.edit_brain(be.set_pair(HUB, 1, 4)) and "no brain file" in ws.message


def test_the_monsters_on_the_start_page(game: Extracted, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MHFU_DATA", str(game.root))
    shelf = MonsterWorkspace().start()[1]
    assert shelf.title == "Monsters" and len(shelf.choices) == len(files.EM_SPECIES)
    tigrex = next(c for c in shelf.choices if c.label == "Tigrex")
    assert tigrex.path == game.path(files.monster_pac(75)) and tigrex.detail == "em75"
    assert [c.label for c in shelf.choices] == sorted(c.label for c in shelf.choices)
    monkeypatch.delenv("MHFU_DATA")
    empty = MonsterWorkspace().start()[1]
    assert empty.choices == () and "MHFU extraction" in empty.note and empty.needs


# ---- builds


def test_the_first_build_starts_at_once(ws: MonsterWorkspace, clock: Clock) -> None:
    assert ws.brain_built(75) is None and ws.brain_pending(75)  # the decode, on the worker
    built = settle(ws, clock)
    assert built.ok and built.row == 75 and built.lift is ws.brain_lift(75)
    assert ws.brain_built(75) is built and not ws.brain_pending(75)
    view = ws.brain_view(75)
    assert isinstance(view, BrainView) and view.header == "h0" and view.edited == frozenset()
    assert ws.brain_view(75) is view and ws.brain_view(75, "h9") is view  # one per build
    assert view.labels and ws.brain_error(75) == ""


def test_a_row_the_file_does_not_edit_shows_the_games(ws: MonsterWorkspace, clock: Clock) -> None:
    assert not ws.brain_editable(88) and ws.brain_built(88) is None
    assert ws.brain_view(88) is None  # the native view: started
    assert ws.brain_projection(88) is None and ws.brain_outline(88) is None


def test_a_gesture_builds_once_after_the_debounce(
    ws: MonsterWorkspace, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = settle(ws, clock)
    runs: list[tuple[Any, Any]] = []
    real = build.compile

    def counting(*a: Any, **kw: Any) -> Any:
        runs.append((kw["lift"], kw["intel"]))
        return real(*a, **kw)

    monkeypatch.setattr(build, "compile", counting)
    for sub in (1, 2, 4):  # a change under the debounce is no reason to build
        assert pair(ws, 1, sub), ws.message
        clock.t += mw.DEBOUNCE / 3
        assert ws.brain_built(75) is first and ws.brain_pending(75)
    assert runs == []
    clock.t += mw.DEBOUNCE
    ws.brain_built(75)  # due: the build is asked
    got = settle(ws, clock)
    assert got is not first and got.id != first.id
    assert len(runs) == 1  # the same lift and intel objects as the first build's
    assert runs[0][0] is first.lift and runs[0][1] is ws.intel_cache[75]
    assert ws.brain_view(75).edited
    assert ws.brain_projection(75) is ws.brain_projection(75)


def test_the_latest_build_shows_while_the_next_computes(
    ws: MonsterWorkspace, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = settle(ws, clock)
    view = ws.brain_view(75)
    gate, started = threading.Event(), threading.Event()
    real = build.compile

    def slow(*a: Any, **kw: Any) -> Any:
        started.set()
        assert gate.wait(10)
        return real(*a, **kw)

    monkeypatch.setattr(build, "compile", slow)
    assert pair(ws, 1, 4)
    clock.t += 2 * mw.DEBOUNCE
    assert ws.brain_built(75) is first
    assert started.wait(10)
    for _ in range(3):  # computing: the build before stays
        assert ws.brain_built(75) is first and ws.brain_view(75) is view and ws.brain_pending(75)
    gate.set()
    got = settle(ws, clock)
    assert got is not first and ws.brain_view(75) is not view


def test_a_change_of_another_row_or_the_pairs_builds_nothing(
    ws: MonsterWorkspace, clock: Clock
) -> None:
    built = settle(ws, clock)
    assert ws.species_doc is not None
    ws.species_doc.edit(lambda bf: bf.pairs.__setitem__((3, 0), "roar"))
    clock.t += 1
    assert ws.brain_built(75) is built and not ws.brain_pending(75)
    assert ws.brain_pairs()[3, 0] == "roar" and ws.brain_pairs()[1, 4] == "anim 1"
    proj = ws.brain_projection(75)
    assert proj is not None and proj.words == ws.brain_words(75)
    assert proj.words.pairs == ws.brain_pairs()


def test_names_rename_without_a_build(ws: MonsterWorkspace, clock: Clock) -> None:
    built = settle(ws, clock)
    assert ws.edit_brain(be.name_var(4, "in_hunt_area"), 75)
    assert ws.edit_brain(be.name_range(cat.BEYOND, "out of reach"), 75)
    clock.t += 1
    assert ws.brain_built(75) is built and not ws.brain_pending(75)
    words = ws.brain_words(75)
    assert (words.vars, words.ranges) == ({4: "in_hunt_area"}, {cat.BEYOND: "out of reach"})
    proj = ws.brain_projection(75)
    assert proj is not None and proj.words == words
    assert not ws.edit_brain(be.name_var(10, "x"), 75) and "vars: 10" in ws.message
    assert not ws.edit_brain(be.name_var(4, "x" * 41), 75) and "one line" in ws.message


def test_an_undone_edit_is_the_build_before(ws: MonsterWorkspace, clock: Clock) -> None:
    first = settle(ws, clock)
    assert pair(ws, 1, 4)
    second = settle(ws, clock)
    assert second is not first and second.id != first.id
    assert ws.species_doc is not None
    ws.species_doc.undo()
    ws.refresh()
    assert ws.brain_pending(75)
    assert settle(ws, clock).id == first.id  # the empty edit set builds the game's bytes


def test_a_failed_build_says_why_and_is_not_retried(
    ws: MonsterWorkspace, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = settle(ws, clock)

    def broken(*a: Any, **kw: Any) -> Any:
        raise ValueError("the intel is another species'")

    monkeypatch.setattr(build, "compile", broken)
    assert pair(ws, 1, 4)
    settle_end = time.monotonic() + 10
    while ws.brain_pending(75):
        clock.t += 1
        ws.brain_built(75)
        assert time.monotonic() < settle_end
        time.sleep(0.002)
    assert ws.brain_built(75) is first
    assert "building the script failed (the intel is another species')" in ws.brain_error(75)
    assert [f.code for f in ws.document.findings()] == ["BUILD"]


def test_the_budget_is_worked_out_when_asked(ws: MonsterWorkspace, clock: Clock) -> None:
    def budget() -> str:
        stop = time.monotonic() + 10
        while (b := ws.brain_budget(75)) is None:
            assert time.monotonic() < stop
            time.sleep(0.002)
        return mw.budget_text(b)

    built = settle(ws, clock)
    assert built.runs is None  # nothing edited, nothing computed yet
    assert ws.brain_budget(75) is None  # asked: on the worker
    assert budget().endswith(" of 999") and built.runs is not None  # the worker made it
    assert pair(ws, 1, 4)
    settle(ws, clock)
    assert budget().endswith(" of 999") and ws.brain_budget(88) is None


# ---- gestures


def test_a_gesture_is_one_undo_step(ws: MonsterWorkspace, clock: Clock) -> None:
    settle(ws, clock)
    doc = ws.species_doc
    assert doc is not None and not doc.can_undo()
    assert pair(ws, 1, 4) and ws.message == be.set_pair(HUB, 1, 4).label and doc.dirty
    es = doc.brainfile.edits(75)
    assert es is not None and es.nodes[HUB].args == {"main": 1, "sub": 4}
    assert doc.can_undo() and not doc.can_redo()
    doc.undo()
    assert doc.brainfile.edits(75) is None and not doc.dirty and doc.can_redo()
    doc.redo()
    es = doc.brainfile.edits(75)
    assert es is not None and es.nodes[HUB].args == {"main": 1, "sub": 4}
    doc.undo()
    assert not doc.can_undo()  # exactly one step


def test_a_refused_gesture_commits_nothing(ws: MonsterWorkspace, clock: Clock) -> None:
    settle(ws, clock)
    doc = ws.species_doc
    assert doc is not None
    before = brainfile.dumps(doc.brainfile)
    version = doc.version
    assert not ws.edit_brain(be.delete("75:DEADBEEF"))
    assert "no node 75:DEADBEEF" in ws.message and ws.message.startswith("Delete")
    with pytest.raises(EditError):
        be.delete("75:DEADBEEF").edit(doc.brainfile.copy(), 75, lift())
    assert brainfile.dumps(doc.brainfile) == before
    assert not doc.dirty and not doc.can_undo() and doc.version == version


def test_a_gesture_that_changes_nothing_commits_nothing(ws: MonsterWorkspace, clock: Clock) -> None:
    settle(ws, clock)
    doc = ws.species_doc
    assert doc is not None
    assert not pair(ws, 3, 0)  # the game's own pair
    assert ws.message == f"{be.set_pair(HUB, 3, 0).label}: nothing changes"
    assert not doc.dirty and not doc.can_undo() and doc.brainfile.edits(75) is None
    assert pair(ws, 1, 4) and not pair(ws, 1, 4)
    doc.undo()
    assert not doc.can_undo() and not doc.dirty


def test_a_gesture_over_new_code_waits_for_the_view(ws: MonsterWorkspace, clock: Clock) -> None:
    settle(ws, clock)
    assert ws.edit_brain(be.insert_entry(be.Place.after(HUB), be.PALETTE["Moves"][0]))
    assert ws.brain_pending(75)
    doc = ws.species_doc
    assert doc is not None
    version = doc.version
    assert not pair(ws, 1, 4)  # the view lags and the file has new code: the ids may move
    assert "still building" in ws.message and doc.version == version
    settle(ws, clock)
    assert pair(ws, 1, 4)
    assert not ws.edit_brain(be.delete(HUB), row=1) and "no brain file" in ws.message


def test_names_come_from_the_file_not_the_build(ws: MonsterWorkspace, clock: Clock) -> None:
    settle(ws, clock)
    assert ws.edit_brain(be.rename_routine("r_a", "Opening"))
    assert ws.edit_brain(be.set_note(HUB, "a roar"))
    assert ws.brain_names(75) == {"r_a": "Opening"} and ws.brain_notes(75) == {HUB: "a roar"}
    outline = ws.brain_outline(75)  # still the build before: the names are the file's
    assert outline is not None and outline.rows("r_a")[0].label == "Opening"
    plan = ws.brain_plan(75, "r_a")
    assert plan is not None and plan.crumbs[0].label == "Opening (r_a)"
    assert ws.brain_plan(75, "nope") is None and ws.brain_plan(75, "r_a", "nope") is None
    g = ws.brain_routine_graph(75, "r_a")
    assert g is not None and ws.brain_routine_graph(75, "nope") is None


def test_the_bar_of_a_node(ws: MonsterWorkspace, clock: Clock) -> None:
    settle(ws, clock)
    bar = ws.brain_bar(75, HUB)
    assert bar is not None and {c.name for c in bar.controls} >= {"main", "sub"}
    assert any("anim 1" in c.text for c in bar.pairs)  # the labels are the intel's
    assert pair(ws, 1, 4) and ws.brain_bar(75, "75:NOPE") is None
    assert ws.brain_lift(75) is not None and ws.brain_bar(88, HUB) is None


# ---- findings


def test_findings_come_from_the_build_and_reveal_lands_on_the_node(
    ws: MonsterWorkspace, clock: Clock
) -> None:
    settle(ws, clock)
    assert pair(ws, 7, 7)  # a pair the species' intel has no record of
    settle(ws, clock)
    found = ws.document.findings()
    (f,) = [f for f in found if f.code == "PAIR_UNKNOWN"]
    assert f.level == "warning" and f.target == ("node", HUB) and f.where == "r_a"
    assert f.fix == mw.FIXES["PAIR_UNKNOWN"] and "(7, 7)" in f.message
    assert worst(found) == "warning"
    ws.reveal(f.target, f.focus)
    assert ws.brain_focus == HUB and ws.brain_routine == "r_a" and ws.brain_header == "h0"
    assert ws.brain_scope == brain_graph.scope_of(ws.brain_routine_graph(75, "r_a"), HUB)
    assert ws.take_focus() == "Brain" and ws.brain_focused == 1
    ws.reveal(("routine", "r_a"))
    assert ws.brain_focus is None and ws.brain_routine == "r_a" and ws.brain_focused == 2
    ws.reveal(("node", "u9.9"))  # not in this script: nothing moves
    assert ws.brain_focused == 2 and "not in this script" in ws.message
    doc = ws.species_doc
    assert doc is not None
    doc.undo()
    ws.refresh()
    settle(ws, clock)
    assert [f.code for f in doc.findings()] == []


def test_findings_read_the_cache_only(
    ws: MonsterWorkspace, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    settle(ws, clock)
    assert pair(ws, 7, 7)
    settle(ws, clock)
    monkeypatch.setattr(build, "compile", None)  # a build here would raise
    monkeypatch.setattr(mw.script_graph.Projection, "overview", None)
    assert [f.code for f in ws.document.findings()] == ["PAIR_UNKNOWN"]


# ---- Send and Push


@pytest.fixture
def stick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "PSP"
    (root / inject.MODS_SUBDIR).mkdir(parents=True)
    monkeypatch.setattr(inject, "MEMSTICK_ROOTS", (str(root),))
    return root


def test_send_writes_both_files(ws: MonsterWorkspace, clock: Clock, stick: Path) -> None:
    assert "still building" in (ws.send_blocker() or "")
    settle(ws, clock)
    assert pair(ws, 1, 4)
    assert "still building" in (ws.send_blocker() or "")
    built = settle(ws, clock)
    assert ws.send_blocker() is None
    assert not ws.next_steps()[2].done
    ws.send()
    box = stick / inject.BRAIN_SUBDIR / inject.brain_filename(75)
    mod = stick / inject.MODS_SUBDIR / inject.brain_module(75)
    assert ws.message == mw.SENT and box.read_bytes() == built.container
    assert f"{built.id:08X}" in mod.read_text()
    assert [s.done for s in ws.next_steps()] == [False, True, True]
    assert pair(ws, 1, 5)
    settle(ws, clock)
    assert not ws.next_steps()[2].done  # a later build is not the sent one


def test_nothing_goes_while_the_build_has_errors(
    ws: MonsterWorkspace, clock: Clock, stick: Path
) -> None:
    settle(ws, clock)
    assert pair(ws, 9, 0)  # main past 0..7
    built = settle(ws, clock)
    assert not built.ok
    why = ws.send_blocker()
    assert why is not None and why.startswith("the script has errors") and "main 9" in why
    assert ws.brain_push_blocker() == why
    ws.send()
    assert ws.message.startswith("not sent: the script has errors")
    assert not (stick / inject.BRAIN_SUBDIR).exists()
    pushes: list[Any] = []
    ws.push_script = lambda *a: pushes.append(a)
    ws.push_brain()
    assert ws.message.startswith("not pushed: the script has errors") and pushes == []
    assert [f.level for f in ws.document.findings()][0] == "error"


def test_send_needs_a_stick(ws: MonsterWorkspace, clock: Clock, tmp_path: Path) -> None:
    settle(ws, clock)
    assert pair(ws, 1, 4)
    settle(ws, clock)
    why = ws.send_blocker()
    assert why is not None and "memory stick" in why
    ws.send()
    assert ws.message.startswith("not sent:")


def test_push_runs_the_build_in_the_game(ws: MonsterWorkspace, clock: Clock) -> None:
    settle(ws, clock)
    assert pair(ws, 1, 4)
    built = settle(ws, clock)
    sessions: list[object] = []
    calls: list[Any] = []
    ws.game_session = lambda: nullcontext(sessions.append(1) or "session")

    def push(s: Any, p: ir.Program, img: Any) -> Pushed:
        calls.append((s, p, img))
        return Pushed(Plan((), ()), 0.01, where="the game")

    ws.push_script = push
    assert ws.brain_push_blocker() is None
    ws.push_brain()
    assert ws.message == mw.PUSHED and sessions == [1]
    assert calls == [("session", built.program, built.image)]
    ws.push_script = lambda *a: Pushed(Plan((), ()), 0.01, ["the container"])
    ws.push_brain()
    assert "read back DIFFERS at the container" in ws.message

    def fail(*a: Any) -> Pushed:
        raise DebuggerError("script", "not attached")

    ws.push_script = fail
    ws.push_brain()
    assert ws.message == "push failed: script: not attached"


def test_send_and_push_wait_for_the_first_build(ws: MonsterWorkspace) -> None:
    assert ws.brain_push_blocker() == "the script is still building; try again in a moment"


# ---- Follow and Count


def test_follow_reads_through_seen_and_marks_the_edited_build(
    ws: MonsterWorkspace, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    settle(ws, clock)
    asked: list[Any] = []

    def read(attach: Any, row: int, known: Any, seen: Any) -> bg.Read:
        asked.append((row, known, seen))
        return bg.Read(row, {}, None)

    ws._pool = Inline()
    ws.read_follow = read
    ws.game_running = lambda: True
    ws.follow.clock = clock
    ws.follow.set(True)
    seen_by: list[Any] = []
    real_poll, real_place = ws.follow.poll, ws.follow.place
    monkeypatch.setattr(
        ws.follow, "poll", lambda *a, **kw: seen_by.append(("poll", a[-1])) or real_poll(*a, **kw)
    )
    monkeypatch.setattr(
        ws.follow,
        "place",
        lambda *a, **kw: seen_by.append(("place", a[-1])) or real_place(*a, **kw),
    )
    view = ws.brain_view(75)
    ws.poll_follow(view)
    assert asked == [(75, None, ws.follow.seen)] and asked[0][2] is ws.follow.seen
    assert seen_by[-1] == ("poll", None)  # nothing edited: the game's own script
    ws.place_follow(view)
    assert seen_by[-1] == ("place", None)
    assert pair(ws, 1, 4)
    edited = settle(ws, clock)
    edited_view = ws.brain_view(75)
    assert edited_view is not view
    clock.t += 5
    ws.poll_follow(edited_view)
    assert seen_by[-1][0] == "poll" and seen_by[-1][1] is edited
    ws.place_follow(edited_view)
    assert seen_by[-1][1] is edited
    ws.place_follow(view)  # a view of an older build marks as the game's own
    assert seen_by[-1] == ("place", None)
    ws.place_follow(None)
    assert seen_by[-1] == ("place", None)


def counted(w: MonsterWorkspace) -> list[Any]:
    """A trace of the Tigrex' `enter` three times, on the first node's address."""
    p = w.brain_lift(75)
    assert p is not None
    hub = next(n for n in p.nodes() if n.id == HUB)
    assert hub.pc is not None
    return [Step(10 * k, hub.pc, 0x30) for k in range(3)]


def test_count_runs_on_the_followers_worker(ws: MonsterWorkspace, clock: Clock) -> None:
    settle(ws, clock)
    assert isinstance(ws.count, bg.Count) and ws.count.follow is ws.follow
    assert not ws.count_brain(5) and "follow a running monster first" in ws.message
    ws.follow.target = bg.Target(0x1234, bg.NATIVE, None, None)
    traced: list[Any] = []
    threads: list[threading.Thread] = []
    ws.game_session = lambda: nullcontext("session")

    def trace(s: Any, entity: int, seconds: float) -> Trace:
        traced.append((s, entity, seconds))
        threads.append(threading.current_thread())
        return Trace(entity, seconds, tuple(counted(ws)), 0)

    ws.trace_script = trace
    assert ws.count_brain(5)
    assert ws.follow.paused and ws.count.busy
    stop = time.monotonic() + 10
    while ws.count.busy and not ws.poll_count():
        assert time.monotonic() < stop
        time.sleep(0.002)
    assert traced == [("session", 0x1234, 5)] and threading.main_thread() not in threads
    assert not ws.follow.paused
    got = ws.count.result
    assert got is not None and got.nodes[HUB] == 3 and got.id is None
    ws.browse_brain(76)  # going somewhere else drops the count
    assert ws.count.result is None


def test_a_count_of_an_edited_build_needs_that_build_running(
    ws: MonsterWorkspace, clock: Clock
) -> None:
    settle(ws, clock)
    assert pair(ws, 1, 4)
    built = settle(ws, clock)
    ws.follow.target = bg.Target(0x1234, bg.BUILD, built.id, 0x4000)
    ws.game_session = lambda: nullcontext("session")
    ws.trace_script = lambda s, e, sec: Trace(e, sec, (), 0)
    assert ws.count_brain(5)
    stop = time.monotonic() + 10
    while ws.count.busy and not ws.poll_count():
        assert time.monotonic() < stop
        time.sleep(0.002)
    assert "no instruction ran" in ws.count.status
    ws.follow.target = bg.Target(0x1234, bg.BUILD, (built.id or 0) + 1, 0x4000)
    assert not ws.count_brain(5) and "older build" in ws.message


def test_a_new_document_forgets_the_count(ws: MonsterWorkspace, game: Extracted) -> None:
    ws.count.result = bg.Counted(1, 1.0, None, 0, 0, 0, {}, {}, {}, frozenset())
    ws.open(game.path(files.monster_pac(76)))
    assert ws.count.result is None and ws.brain_focus is None


# ---- save and revert


def test_save_and_revert(ws: MonsterWorkspace, clock: Clock) -> None:
    settle(ws, clock)
    assert pair(ws, 1, 4)
    path = places.brain_file(75)
    assert not path.exists()
    ws.save()
    assert path.is_file() and ws.message == f"saved {path}" and not ws.document.dirty
    assert pair(ws, 1, 5)
    ws.revert()
    assert ws.message == "back to the file on disk" and not ws.document.dirty
    es = ws.species_doc.brainfile.edits(75)
    assert es is not None and es.nodes[HUB].args == {"main": 1, "sub": 4}


def test_the_shell_drives_the_document(ws: MonsterWorkspace, clock: Clock) -> None:
    st = Studio([ws])
    settle(ws, clock)
    st.act("pair", lambda: pair(ws, 7, 7))()
    doc = ws.species_doc
    assert doc is not None and doc.dirty and st.message == be.set_pair(HUB, 7, 7).label
    settle(ws, clock)
    assert [f.code for f in st.findings.get(ws.document)] == ["PAIR_UNKNOWN"]
    st.undo()
    assert not doc.dirty and st.message == "undo"
    st.redo()
    assert doc.dirty and st.can_revert()
    assert st.save() and not doc.dirty and doc.path is not None and doc.path.is_file()
    assert not st.can_revert()
    st.undo()
    assert doc.dirty and st.can_revert()
    st.ask_revert = lambda name: True
    st.revert()
    assert not doc.dirty and "em75.toml" in st.message


# ---- the real Tigrex


@pytest.fixture(scope="module")
def tigrex(mhfu_data: Path) -> Extracted:
    return Extracted.find(mhfu_data)


def real(tigrex: Extracted, intel75: SpeciesIntel, clock: Clock) -> MonsterWorkspace:
    w = MonsterWorkspace(Data(tigrex, tigrex))
    w.intel_cache[75] = intel75
    w.clock = clock
    return w


def test_em75_opens_edits_finds_and_sends(
    tigrex: Extracted, intel75: SpeciesIntel, clock: Clock, stick: Path
) -> None:
    w = real(tigrex, intel75, clock)
    try:
        pac = tigrex.path(files.monster_pac(75))
        warm = w.warmup(pac)
        assert warm is not None and warm.done is not None
        warm.done(warm.run())
        w.open(pac)
        d = w.species_doc
        assert d is not None and d.overlay == 75 and not d.dirty and d.path == places.brain_file(75)
        assert w.brain_rows() is not None and w.brain_editable(76)
        first = settle(w, clock)
        view = w.brain_view(75)
        assert view is not None and len(view.lines) > 1000 and not view.edited
        p = w.brain_lift(75)
        assert p is not None
        base = p.item("base_0")
        assert isinstance(base, ir.Routine)
        hub = next(n for n in ir.walk(base.body) if isinstance(n, ir.Insn) and n.op.name == "enter")
        assert w.edit_brain(be.set_pair(hub.id, 7, 7))
        assert not w.edit_brain(be.set_pair(hub.id, 7, 7))  # already so
        built = settle(w, clock)
        edited = w.brain_view(75)
        assert edited is not None and edited is not view and edited.row_of[hub.id] in edited.edited
        (f,) = [f for f in d.findings() if f.code == "PAIR_UNKNOWN"]
        assert f.target == ("node", hub.id)
        w.reveal(f.target)
        assert w.brain_focus == hub.id and w.brain_routine == "base_0"
        arm = next(x for r in p.routines() for x in ir.walk(r.body) if isinstance(x, ir.Arm))
        bar = w.brain_bar(75, arm.id)
        assert bar is not None and bar.title.endswith("arm") and w.brain_bar(75, hub.id) is not None
        outline = w.brain_outline(75)
        assert outline is not None and outline.rows("base_0")[0].edited
        proj = w.brain_projection(75)
        assert proj is not None and proj is w.brain_projection(75)
        assert w.send_blocker() is None
        w.send()
        assert w.message == mw.SENT
        assert (
            stick / inject.BRAIN_SUBDIR / inject.brain_filename(75)
        ).read_bytes() == built.container
        d.undo()
        assert not d.can_undo()  # the second set_pair was no step
        w.refresh()
        assert settle(w, clock).id == first.id
        stop = time.monotonic() + 30
        while (b := w.brain_budget(75)) is None:
            assert time.monotonic() < stop
            time.sleep(0.01)
        assert mw.budget_text(b).endswith(" of 999") and b.worst is not None
    finally:
        w.close()
