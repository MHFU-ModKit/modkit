# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Brain tab's model: a view of the AI script one header runs, and the workspace's decode."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from mhfu import files
from mhfu.em.intel import SpeciesIntel
from mhfu.em.script import ir, source
from mhfu.em.script.opcodes import BY_NAME
from mhfu.files import Extracted
from mhfu_port.data import Data
from mhfu_studio.monster import brain
from mhfu_studio.monster.brain import BrainView, History
from mhfu_studio.monster.core.scene import Scene
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell import places

# ---- a tiny program by hand: two headers, a list both share, a data item, a blob


def ins(name: str, **args: int) -> ir.Insn:
    values: dict[str, ir.Value] = dict(args)
    return ir.Insn(BY_NAME[name], values)


def enter(main: int, sub: int) -> ir.Insn:
    return ins("enter", main=main, sub=sub, gate=0)


def end(kind: int = 1) -> ir.Insn:
    return ins("end", kind=kind)


def routine(name: str, *body: ir.Node, va: int) -> ir.Routine:
    return ir.Routine(name, list(body), va=va)


def header(name: str, variants: tuple[int, ...], **lists: str | None) -> ir.Header:
    """A header whose fields are the lists named by `slot=table` (base, call, int_0x10, ...)."""
    names = ("base", "call", "walk", "int_0x10", "int_0x20", "stages")
    fields: list[ir.Entry] = [lists.get(n) for n in names]
    return ir.Header(name, fields, variants, va=0x3000)


def program() -> ir.Program:
    """r_a is in both headers' base lists; its `goto 1` leads to r_b for h0 and to r_d for h1.
    r_c is the shared call list's; r_i is h0's interrupt; r_x is in no list."""
    r_a = routine(
        "r_a",
        ir.Block(
            BY_NAME["if_latch"],
            {},
            [enter(3, 0), ins("call", routine=0)],
            [ir.Arm(1, {}, [enter(1, 4)])],
        ),
        ins("goto", routine=1),
        va=0x2000,
    )
    items: list[ir.Item] = [
        r_a,
        routine("r_b", enter(0, 3), end(), va=0x2040),
        routine("r_c", ins("wait", frames=5), end(), va=0x2060),
        routine("r_d", enter(7, 7), end(), va=0x2080),
        routine("r_i", end(0xFD), va=0x20A0),
        routine("r_x", end(), va=0x20C0),
        ir.Table("h0_base_l", ["r_a", "r_b"], 0x2100),
        ir.Table("h1_base_l", ["r_a", "r_d"], 0x2110),
        ir.Table("call_l", ["r_c", None], 0x2120),
        ir.Table("stages_l", ["d1"], 0x2130),
        ir.Table("int_l", ["r_i"], 0x2134),
        ir.Table("lonely_l", ["r_x"], 0x2138),
        ir.Blob(bytes(8), 0x2140),
        ir.Data("d0", bytes(range(20)), {"d0": 0, "d1": 16}, 0x2150),
        ir.Data("o0", bytes(4), {"o0": 0}, 0x2170),
        header(
            "h0", (0, 1, 2, 3), base="h0_base_l", call="call_l", int_0x10="int_l", stages="stages_l"
        ),
        header("h1", (4, 5, 6, 8), base="h1_base_l", call="call_l"),
    ]
    p = ir.Program(75, 0x2000, items)
    for k, n in enumerate(p.nodes()):
        n.id, n.pc = f"n{k}", 0x1000 + 4 * k
    return p


def heads(v: BrainView) -> list[str]:
    return [ln.text for ln in v.lines if ln.depth == 0]


def test_a_header_keeps_what_it_runs() -> None:
    p = program()
    assert heads(brain.view(p, "h0")) == [
        "row 75 at 0x00002000",
        "routine r_a",
        "routine r_b",
        "routine r_c",
        "routine r_i",
        "table h0_base_l: r_a r_b",
        "table call_l: r_c -",
        "table stages_l: d1",
        "table int_l: r_i",
        "data d0",
        "header h0 variants 0-3",
    ]
    assert heads(brain.view(p, "h1")) == [
        "row 75 at 0x00002000",
        "routine r_a",
        "routine r_c",
        "routine r_d",
        "table h1_base_l: r_a r_d",
        "table call_l: r_c -",
        "header h1 variants 4-6,8",
    ]


def test_an_unknown_header() -> None:
    with pytest.raises(KeyError):
        brain.view(program(), "h9")


def kept(p: ir.Program, header: str, wanted: Callable[[str], bool]) -> str:
    """The source of the whole program, cut by top-level line to the groups `wanted` names."""
    text = "\n".join(ln.text for ln in source.render(p, header))
    return "\n".join(g for g in re.split(r"\n(?=\S)", text) if wanted(g.split("\n")[0]))


KEPT = {
    "h0": (
        "row ",
        "routine r_a",
        "routine r_b",
        "routine r_c",
        "routine r_i",
        "table h0_base_l",
        "table call_l",
        "table stages_l",
        "table int_l",
        "data d0",
        "header h0",
    ),
    "h1": (
        "row ",
        "routine r_a",
        "routine r_c",
        "routine r_d",
        "table h1_base_l",
        "table call_l",
        "header h1",
    ),
}


@pytest.mark.parametrize("name", ["h0", "h1"])
def test_the_view_is_the_kept_source_as_a_tree(name: str) -> None:
    p = program()
    v = brain.view(p, name)
    assert "\n".join(ln.text for ln in v.lines) == kept(p, name, lambda h: h.startswith(KEPT[name]))
    order: list[int] = []

    def walk(row: int) -> None:
        order.append(row)
        for child in v.children[row]:
            walk(child)

    for top in v.top:
        walk(top)
    assert order == list(range(len(v.lines)))
    for row, up in enumerate(v.parent):
        assert (v.lines[up].depth < v.lines[row].depth) if up >= 0 else v.lines[row].depth == 0
        assert (row in v.children[up]) if up >= 0 else (row in v.top)


def test_arms_sit_beside_their_block() -> None:
    v = brain.view(program(), "h0")
    (opener,) = (i for i, ln in enumerate(v.lines) if ln.text.strip() == "if_latch")
    (arm,) = (i for i, ln in enumerate(v.lines) if ln.text.strip() == "else")
    assert v.parent[arm] == v.parent[opener] and v.lines[arm].depth == v.lines[opener].depth
    assert [v.lines[i].text.strip() for i in v.children[opener]] == ["enter 3 0 0", "call 0"]


def test_row_of_is_the_node() -> None:
    p, v = program(), brain.view(program(), "h0")
    nodes = [n.id for n in p.nodes() if n.id in v.row_of]
    assert [v.lines[v.row_of[n]].node for n in nodes] == nodes
    assert v.row_of["n0"] < v.row_of["n1"] and "n13" not in v.row_of  # r_x's `end`


def test_the_outline_names_the_lists() -> None:
    v = brain.view(program(), "h0")
    named = {o.name: [(i.index, i.name) for i in o.items] for o in v.outline}
    assert named == {
        "base": [(0, "r_a"), (1, "r_b")],
        "call": [(0, "r_c"), (1, None)],
        "interrupt 0x10": [(0, "r_i")],
        "stages": [(0, "d1")],
    }
    base, call, interrupt, stages = v.outline
    assert [o.index for o in v.outline] == [0, 1, 3, 5]
    for item in (*base.items, call.items[0], interrupt.items[0]):
        assert item.row is not None and v.lines[item.row].text == f"routine {item.name}"
    assert call.items[1].row is None and stages.items[0].row is None  # empty; data


def test_find() -> None:
    v = brain.view(program(), "h0")
    enters = [i for i, ln in enumerate(v.lines) if ln.text.strip().startswith("enter")]
    assert len(enters) == 3 and v.find("ENTER") == enters
    assert v.find("  enter 3 0 0 ") == [enters[0]]
    assert v.find("") == [] and v.find("no such text") == []
    # a 0x query is also an address; any other number is text only
    row = v.row_of["n1"]
    assert v.lines[row].pc == 0x1004
    assert v.find("0x1004") == v.find("0X1004") == [row] and v.find("1004") == []
    assert v.find("0x2000") == v.find("routine r_a")


def test_ancestors_and_targets() -> None:
    p = program()
    v = brain.view(p, "h0")
    inner = v.find("enter 3 0 0")[0]
    up = v.ancestors(inner)
    assert [v.lines[i].text.strip() for i in up] == ["routine r_a", "if_latch"]
    assert v.ancestors(up[0]) == [] and v.ancestors(inner) == [*v.ancestors(up[1]), up[1]]
    goto, call = v.find("goto")[0], v.find("call 0")[0]
    r_c = next(r for r in p.routines() if r.name == "r_c")
    assert v.target_row(call) == v.row_of[r_c.body[0].id]
    assert v.lines[v.target_row(goto) or 0].text.strip() == "enter 0 3 0"
    other = brain.view(p, "h1")  # the same line leads where this header's list sends it
    assert other.lines[other.target_row(other.find("goto")[0]) or 0].text.strip() == "enter 7 7 0"
    assert v.target_row(inner) is None


def test_enters_by_row() -> None:
    v = brain.view(program(), "h0")
    assert {v.lines[r].text.strip(): pair for r, pair in v.enters.items()} == {
        "enter 3 0 0": (3, 0),
        "enter 1 4 0": (1, 4),
        "enter 0 3 0": (0, 3),
    }
    assert v.labels == {}


def test_the_headers_in_words() -> None:
    p = program()
    assert [(v.header, v.words) for v in brain.variants(p)] == [
        ("h0", "Map types 0-3"),
        ("h1", "Map types 4-6, 8"),
    ]
    assert brain.variants(p)[1].variants == (4, 5, 6, 8) and brain.variants(p)[0].va == 0x3000
    assert brain.default_header(p) == "h0"  # of equals, the first
    assert brain.map_words(tuple(range(17))) == "Every map type"
    assert brain.map_words((5,)) == "Map type 5" and brain.map_words(()) == "No map type"
    assert brain.map_words((0, 1, 2, 3, 4, 6)) == "Map types 0-4, 6"


def test_the_default_header_runs_most_map_types() -> None:
    p = program()
    p.headers()[1].variants = (0, 1, 2, 3, 4, 5)
    assert brain.default_header(p) == "h1"


def test_history() -> None:
    h = History()
    assert h.current is None and not h.can_back and not h.can_forward
    assert h.back() is None and h.forward() is None
    for row in (4, 9, 9, 2):
        h.go(row)
    assert h.rows == [4, 9, 2] and h.current == 2 and h.can_back and not h.can_forward
    assert (h.back(), h.back(), h.back()) == (9, 4, None) and not h.can_back
    assert (h.forward(), h.current, h.can_forward) == (9, 9, True)
    h.go(7)  # what was ahead is gone
    assert h.rows == [4, 9, 7] and not h.can_forward and h.forward() is None
    h.clear()
    assert h.current is None and h.rows == []


def test_the_history_is_bounded() -> None:
    h = History()
    for row in range(brain.LIMIT + 30):
        h.go(row)
    assert len(h.rows) == brain.LIMIT and h.rows[0] == 30 and h.current == brain.LIMIT + 29
    for _ in range(brain.LIMIT - 1):
        assert h.back() is not None
    assert h.back() is None and h.current == 30


# ---- the workspace: decoding on the brain worker, caching, labels


@pytest.fixture
def decoded() -> list[tuple[Path, int]]:
    return []


@pytest.fixture
def ws(
    tmp_path: Path, intel75: SpeciesIntel, decoded: list[tuple[Path, int]]
) -> Iterator[MonsterWorkspace]:
    """A workspace on an empty game folder whose decode is `program()`, noting what it decoded."""
    game = Extracted(tmp_path)
    w = MonsterWorkspace(Data(game, game))
    w.intel_cache[75] = intel75

    def decode(g: Extracted, row: int) -> ir.Program:
        decoded.append((g.root, row))
        p = program()
        p.row = row
        return p

    w.decode_brain = decode
    yield w
    w.close()


def wait(call: Callable[[], Any], seconds: float = 10.0) -> Any:
    """What `call` returns once it is not None: the panel's poll, a sync at a time."""
    end_at = time.monotonic() + seconds
    while (got := call()) is None:
        assert time.monotonic() < end_at, "the worker did not finish"
        time.sleep(0.005)
    return got


def settled(ws: MonsterWorkspace, row: int) -> Any:
    """The view of `row`, or why there is none, once the worker is done."""
    return wait(lambda: ws.brain(row) or ws.brain_error(row) or None)


def test_a_decode_runs_on_the_worker(ws: MonsterWorkspace, decoded: list[tuple[Path, int]]) -> None:
    assert ws.brain(75) is None  # started
    v = wait(lambda: ws.brain(75))
    assert isinstance(v, BrainView) and v.row == 75 and v.header == "h0"
    assert ws.brain(75) is v and ws.brain(75, "h0") is not None
    assert len(decoded) == 1 and ws.brain_error(75) == ""
    assert ws._pool is None  # the survey's pool is not the brain's


def test_another_header_does_not_decode_again(
    ws: MonsterWorkspace, decoded: list[tuple[Path, int]]
) -> None:
    first = wait(lambda: ws.brain(75))
    assert [v.header for v in ws.brain_headers(75)] == ["h0", "h1"]
    assert ws.brain(75, "h1") is None
    second = wait(lambda: ws.brain(75, "h1"))
    assert second.header == "h1" and second is not first
    assert second.lines != first.lines and len(decoded) == 1
    assert wait(lambda: ws.brain(76)).row == 76 and len(decoded) == 2


def test_a_header_the_row_has_not_is_the_default(ws: MonsterWorkspace) -> None:
    assert wait(lambda: ws.brain(75, "h9")).header == "h0"
    assert ws.brain(75, "h9") is ws.brain(75)


def test_enter_lines_are_labelled_by_what_plays(ws: MonsterWorkspace) -> None:
    v = wait(lambda: ws.brain(75))
    by_text = {v.lines[r].text.strip(): text for r, text in v.labels.items()}
    assert by_text == {
        "enter 1 4 0": "anim 1",  # the intel's anim for the pair
        "enter 0 3 0": "anim 9",
        "enter 3 0 0": "keeps the last clip",  # a pair the intel has not
    }
    assert wait(lambda: ws.brain(75, "h1")).labels  # a view of its own is labelled too


def test_another_species_is_read_from_its_own_intel(ws: MonsterWorkspace) -> None:
    assert ws.plays(1, 4, species=75).text() == "anim 1"
    assert ws.plays(1, 4, species=75).slot == 1
    assert ws.plays(1, 4, species=7).text() == "keeps the last clip"  # no intel for it
    assert ws.plays(1, 4).text() == ws.plays(1, 4, species=None).text()


def test_the_host_plays_through_its_port(
    ws: MonsterWorkspace, port_doc: Any, synthetic_pac: bytes
) -> None:
    ws.load(Scene.from_bytes(synthetic_pac, "t", manifest=port_doc.manifest), port_doc)
    assert ws.host_species == 75
    assert ws.plays(1, 4, species=75) == ws.plays(1, 4)
    assert ws.plays(1, 4, species=75).move == "charge"  # the port's own, not the host's anim


def test_a_sibling_row_is_labelled_from_its_overlay(ws: MonsterWorkspace) -> None:
    ws.scan_brain = lambda game: {75: 75, 76: 75, 88: 75}
    assert not wait(lambda: ws.brain(76)).labels  # no map of rows yet: no overlay known
    assert ws.brain_rows() is None
    assert wait(ws.brain_rows) == {75: 75, 76: 75, 88: 75}
    assert wait(lambda: ws.brain(88)).labels


def test_the_rows_are_scanned_once(ws: MonsterWorkspace) -> None:
    scans: list[Path] = []

    def scan(game: Extracted) -> dict[int, int]:
        scans.append(game.root)
        return {75: 75}

    ws.scan_brain = scan
    assert ws.brain_rows() is None
    assert wait(ws.brain_rows) == {75: 75} and ws.brain_rows() == {75: 75}
    assert len(scans) == 1


def test_a_row_no_overlay_serves(ws: MonsterWorkspace, decoded: list[tuple[Path, int]]) -> None:
    def none(game: Extracted, row: int) -> ir.Program:
        decoded.append((game.root, row))
        raise brain.NoScript(row)

    ws.decode_brain = none
    assert ws.brain(5) is None
    settled(ws, 5)
    assert ws.brain_error(5) == "Bullfango (em05) has no AI script the studio reads"
    assert ws.brain(5) is None and ws.brain(5, "h1") is None
    assert len(decoded) == 1  # the error is kept
    assert ws.brain_error(75) == ""  # for that row only


def test_a_decode_that_fails_says_so(ws: MonsterWorkspace) -> None:
    def bad(game: Extracted, row: int) -> ir.Program:
        raise ValueError("a routine does not end")

    ws.decode_brain = bad
    settled(ws, 75)
    assert (
        ws.brain_error(75)
        == "reading the AI script of Tigrex (em75) failed (a routine does not end)"
    )


def test_without_a_game(monkeypatch: pytest.MonkeyPatch) -> None:
    for p in places.PLACES:
        monkeypatch.delenv(p.env, raising=False)
    w = MonsterWorkspace()
    assert w.brain(75) is None and w.brain_rows() is None and w.brain_headers(75) == []
    assert w.brain_note and w.brain_error(75) == w.brain_note
    assert w._brain_pool is None  # nothing was started


def test_the_worker_stops_with_the_workspace(ws: MonsterWorkspace) -> None:
    wait(lambda: ws.brain(75))
    assert ws._brain_pool is not None
    ws.close()
    assert ws._brain_pool is None


# ---- which species the tab shows


def test_the_species_of_a_port(ws: MonsterWorkspace, port_doc: Any, synthetic_pac: bytes) -> None:
    assert ws.brain_species is None
    ws.load(Scene.from_bytes(synthetic_pac, "t", manifest=port_doc.manifest), port_doc)
    assert ws.brain_species == 75
    ws.browse_brain(76)
    assert ws.brain_species == 76
    ws.load(Scene.from_bytes(synthetic_pac, "t", manifest=port_doc.manifest), port_doc)
    assert ws.brain_species == 75  # a new document picks again


def test_the_species_of_a_monster_pac(
    ws: MonsterWorkspace, synthetic_pac: bytes, tmp_path: Path
) -> None:
    name = f"file_{files.monster_pac(75):05d}.bin"
    ws.load(Scene.from_bytes(synthetic_pac, "tigrex", path=tmp_path / name))
    assert ws.doc is None and ws.brain_species == 75
    ws.browse_brain(1)
    assert ws.brain_species == 1
    ws.load(Scene.from_bytes(synthetic_pac, "mine", path=tmp_path / "mine.bin"))
    assert ws.brain_species is None
    ws.load(Scene.from_bytes(synthetic_pac, "unsaved"))
    assert ws.brain_species is None


# ---- the game


@pytest.fixture(scope="module")
def game(mhfu_data: Path) -> Extracted:
    return Extracted.find(mhfu_data)


@pytest.fixture(scope="module")
def served(game: Extracted) -> dict[int, int]:
    return brain.rows(game)


def test_every_overlay_serves_its_own_row(served: dict[int, int]) -> None:
    assert all(served[sp] == sp for sp in files.EM_SPECIES)
    assert {served[r] for r in (75, 76, 88)} == {75}  # em75 serves its siblings' rows
    assert all(0 < row < files.SPECIES_ROWS for row in served)


def test_em75_has_one_header_and_its_enters(game: Extracted) -> None:
    p = brain.decode(game, 75)
    (only,) = brain.variants(p)
    assert only.words == "Every map type" and only.va is not None
    v = brain.view(p, only.header)
    assert v.header == "h0" and len(v.lines) > 1000
    assert v.find("enter 3 0 0") and len(v.enters) > 100


def test_a_sibling_row_decodes_from_its_overlay(game: Extracted) -> None:
    p = brain.decode(game, 76)
    assert p.row == 76 and brain.view(p, brain.default_header(p)).lines
    with pytest.raises(brain.NoScript):
        brain.decode(game, 5)


def test_a_workspace_labels_the_real_em75(
    game: Extracted, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    w = MonsterWorkspace(Data(game, game))
    try:
        v = wait(lambda: w.brain(75), 120)
        assert w.intel_of(75) is not None, w.intel_errors
        assert v.labels and set(v.labels) <= set(v.enters)
        assert any(text.startswith("anim ") for text in v.labels.values())
    finally:
        w.close()
