# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The operand bar over the same small script as the graph pane's tests, built by the real
workspace: what each kind of node offers, that an edit is one undo step, and that a refusal says
why and puts the control back."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from mhfu.em.intel import SpeciesIntel
from mhfu.em.script import catalogue as cat
from mhfu.em.script import ir
from mhfu_studio.monster import brain_edit as be
from mhfu_studio.monster.panels.operand_bar import (
    NO_FILE,
    NO_NODE,
    OperandBar,
    rebalance,
)
from mhfu_studio.monster.workspace import MonsterWorkspace
from mhfu_studio.shell.studio import Studio
from mhfu_studio.ui import kit
from PySide6.QtWidgets import QComboBox, QPushButton, QSpinBox

from ..test_workspace_brain import Clock, settle
from .test_brain_graph import (  # noqa: F401  (`_isolated` is autouse here too)
    ROW,
    _isolated,
    big_id,
    built,
    call_id,
    hub_id,
    node_of,
    pick_id,
    rich,
)


class Bench:
    """A workspace over `rich()`, a studio and a bar that syncs as the window would."""

    def __init__(self, qtbot: Any, ws: MonsterWorkspace, clock: Clock) -> None:
        self.ws, self.clock = ws, clock
        self.studio = Studio([ws])
        self.bar = OperandBar(ws, self.studio)
        self.studio.listen(self.bar.sync)
        qtbot.addWidget(self.bar)
        self.bar.resize(1300, 60)
        self.bar.show()
        qtbot.waitExposed(self.bar)
        self.program = rich()
        settle(ws, clock)

    def show(self, ref: str | None) -> None:
        self.bar.show_node(ROW, ref)

    def settle(self) -> None:
        settle(self.ws, self.clock)
        self.bar.sync()

    def combo(self, name: str) -> QComboBox:
        box = self.bar.fields[name]
        assert isinstance(box, QComboBox)
        return box

    def spin(self, name: str) -> QSpinBox:
        box = self.bar.fields[name]
        assert isinstance(box, QSpinBox)
        return box

    def pick(self, name: str, index: int) -> None:
        """The user picks entry `index` of a combo."""
        box = self.combo(name)
        box.setCurrentIndex(index)
        box.activated.emit(index)

    def undone(self) -> None:
        doc = self.ws.species_doc
        assert doc is not None and doc.dirty
        self.studio.undo()
        assert not doc.dirty and not doc.can_undo()
        self.settle()


@pytest.fixture
def bench(
    qtbot: Any, tmp_path: Path, synthetic_pac: bytes, intel75: SpeciesIntel
) -> Iterator[Bench]:
    ws, clock = built(tmp_path, synthetic_pac, intel75)
    yield Bench(qtbot, ws, clock)
    ws.close()


def switch_arm(p: ir.Program) -> str:
    sw = node_of(p, "base_1", lambda n: isinstance(n, ir.Block))
    block = p.item("base_1")
    assert isinstance(block, ir.Routine)
    return next(a.id for n in ir.walk(block.body) if n.id == sw for a in n.arms)  # type: ignore[union-attr]


# ---- an enter -------------------------------------------------------------------------------- #


def test_an_enter_offers_its_pair_with_the_names_the_intel_gives(bench: Bench) -> None:
    bench.show(hub_id(bench.program))
    labels = bench.ws.brain_pairs()
    assert labels  # the intel names the pairs it knows
    box = bench.combo("pair")
    texts = [box.itemText(i) for i in range(box.count())]
    assert texts[0] == cat.pair_text(3, 0, labels)  # the current one first
    assert texts[1:] == [cat.pair_text(*p, labels) for p in sorted(labels) if p != (3, 0)]
    assert all(labels[p] in t for p, t in zip(sorted(labels), texts[1:], strict=False) if labels[p])
    assert bench.bar.title.text() == "Move"
    assert (bench.spin("main").value(), bench.spin("sub").value()) == (3, 0)


def test_picking_a_pair_is_one_undo_step(bench: Bench) -> None:
    hub = hub_id(bench.program)
    bench.show(hub)
    box = bench.combo("pair")
    other = next(p for p in bench.ws.brain_pairs() if p != (3, 0))
    index = next(i for i, c in enumerate(bench.ws.brain_bar(ROW, hub).pairs) if c.value == other)  # type: ignore[union-attr]
    bench.pick("pair", index)
    assert bench.studio.message == "Set move"
    bar = bench.ws.brain_bar(ROW, hub)
    assert bar is not None and box.currentIndex() == index  # the pick stays while it builds
    bench.settle()
    new = bench.combo("pair")
    assert new.currentIndex() == 0 and new.itemText(0) == cat.pair_text(
        *other, bench.ws.brain_pairs()
    )
    assert (bench.spin("main").value(), bench.spin("sub").value()) == other
    bench.undone()
    assert bench.combo("pair").itemText(0) == cat.pair_text(3, 0, bench.ws.brain_pairs())


def test_the_numbers_set_any_pair(bench: Bench) -> None:
    hub = hub_id(bench.program)
    bench.show(hub)
    bench.spin("sub").setValue(9)
    assert bench.studio.message == "Set move"
    bench.settle()
    assert (bench.spin("main").value(), bench.spin("sub").value()) == (3, 9)
    assert bench.combo("pair").itemText(0) == cat.pair_text(3, 9, bench.ws.brain_pairs())
    bench.undone()


# ---- a call, a case, an op ------------------------------------------------------------------- #


def test_a_call_offers_the_routines_of_its_list(bench: Bench) -> None:
    call = call_id(bench.program)
    bench.show(call)
    box = bench.combo("routine")
    assert [box.itemText(i) for i in range(box.count())] == ["0  call_0", "1  empty"]
    assert box.currentIndex() == 0
    bench.pick("routine", 1)
    assert bench.studio.message == "Set Routine"
    bench.settle()
    assert bench.combo("routine").currentText() == "1  empty"
    bench.undone()
    assert bench.combo("routine").currentText() == "0  call_0"


def test_a_case_value_is_a_number_or_a_name(bench: Bench) -> None:
    arm = switch_arm(bench.program)
    bench.show(arm)
    assert bench.bar.title.text().endswith("arm")
    box = bench.combo("section")
    assert box.currentText() == "0" and box.findText("any") >= 0
    box.lineEdit().setText("7")  # type: ignore[union-attr]
    box.lineEdit().editingFinished.emit()  # type: ignore[union-attr]
    bench.settle()
    assert bench.combo("section").currentText() == "7"
    bar = bench.ws.brain_bar(ROW, arm)
    assert bar is not None and bar.control("section").value == 7
    bench.undone()
    bench.pick("section", bench.combo("section").findText("any"))
    bench.settle()
    assert bench.combo("section").currentText() == "any"
    bench.undone()


def test_a_value_that_is_neither_is_refused_in_words(bench: Bench) -> None:
    bench.show(switch_arm(bench.program))
    box = bench.combo("section")
    for text in ("seven", "70000", "-1"):
        box.lineEdit().setText(text)  # type: ignore[union-attr]
        box.lineEdit().editingFinished.emit()  # type: ignore[union-attr]
        assert "is not a name or a number from 0 to 65535" in bench.bar.note.text()
        assert bench.combo("section").currentText() == "0"  # put back
        box = bench.combo("section")
    doc = bench.ws.species_doc
    assert doc is not None and not doc.dirty


def test_an_op_swaps_for_one_that_keeps_its_operands(bench: Bench) -> None:
    big = big_id(bench.program)
    bench.show(big)
    bar = bench.ws.brain_bar(ROW, big)
    assert bar is not None and bar.swaps
    box = bench.combo("swap")
    assert box.count() == len(bar.swaps) + 1 and box.currentIndex() == 0
    assert box.itemData(1, 3) == bar.swaps[0].meaning  # the tip of each is what it means
    bench.pick("swap", 1)
    bench.settle()
    after = bench.ws.brain_bar(ROW, big)
    assert after is not None and after.title == bar.swaps[0].title != bar.title
    assert bench.bar.title.text() == after.title
    bench.undone()


# ---- a pick ---------------------------------------------------------------------------------- #


def test_a_pick_shows_its_weights_and_what_is_left(bench: Bench) -> None:
    bench.show(pick_id(bench.program))
    assert [s.value() for s in bench.bar.weights] == [8, 16, 8]
    assert bench.bar.total is not None and bench.bar.total.text() == "32 of 32"
    assert bench.bar.total.level is None
    assert isinstance(bench.bar.fields["even"], QPushButton)


def test_a_weight_moves_the_others_and_is_one_step(bench: Bench) -> None:
    pick = pick_id(bench.program)
    bench.show(pick)
    bench.bar.weights[0].setValue(16)
    assert bench.studio.message == "Weights"
    assert [s.value() for s in bench.bar.weights] == [16, 11, 5]  # the rest keep their share
    assert bench.bar.total.text() == "32 of 32"  # type: ignore[union-attr]
    bench.settle()
    assert [s.value() for s in bench.bar.weights] == [16, 11, 5]
    box = bench.ws.brain_view(ROW)
    assert box is not None
    bench.undone()
    assert [s.value() for s in bench.bar.weights] == [8, 16, 8]


def test_even_gives_every_branch_a_share(bench: Bench) -> None:
    pick = pick_id(bench.program)
    bench.show(pick)
    bench.bar.fields["even"].click()  # type: ignore[attr-defined]
    assert bench.studio.message == "Even"
    bench.settle()
    assert [s.value() for s in bench.bar.weights] == [11, 11, 10]
    bench.undone()


def test_an_arm_of_a_pick_shows_the_picks_weights_too(bench: Bench) -> None:
    box = bench.ws.brain_projection(ROW)
    assert box is not None
    pick = pick_id(bench.program)
    arm = next(
        p.arm
        for p in bench.ws.brain_routine_graph(ROW, "base_0").nodes[pick].ports  # type: ignore[union-attr]
    )
    bench.show(arm)
    assert [s.value() for s in bench.bar.weights] == [8, 16, 8]
    assert "weight" not in bench.bar.fields  # one weight cannot move alone


# ---- refusal and the empty bar --------------------------------------------------------------- #


def test_a_refusal_says_why_and_puts_the_control_back(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = hub_id(bench.program)
    bench.show(hub)
    asked: list[str] = []

    def refuse(g: be.Gesture, row: int | None = None) -> bool:
        asked.append(g.label)
        bench.ws.message = "Set move: not now"
        return False

    monkeypatch.setattr(bench.ws, "edit_brain", refuse)
    bench.spin("sub").setValue(9)
    assert asked == ["Set move"]
    assert bench.spin("sub").value() == 0  # back to what the build says
    assert bench.bar.note.text() == "Set move: not now" and bench.bar.note.level == "warning"
    assert bench.studio.message == "Set move: not now"
    bench.show(hub)  # looking again clears it
    assert bench.bar.note.text() == "" and bench.bar.note.level is None


def test_a_gesture_the_model_refuses_leaves_the_file_alone(bench: Bench) -> None:
    pick = pick_id(bench.program)
    bench.show(pick)
    doc = bench.ws.species_doc
    assert doc is not None
    bench.bar.weights[0].setValue(8)  # the weight it has: nothing to do
    assert not doc.dirty and bench.bar.note.text() == ""
    bench.bar._apply(be.set_weights(pick, [20, 20, 20]))  # adds up to 60
    assert not doc.dirty and "add up to 60" in bench.bar.note.text()
    assert [s.value() for s in bench.bar.weights] == [8, 16, 8]


def test_a_refused_weight_leaves_the_spins_as_they_were(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench.show(pick_id(bench.program))
    monkeypatch.setattr(bench.ws, "edit_brain", lambda g, row=None: False)
    bench.bar.weights[0].setValue(16)  # the rest would be [11, 5]
    assert [s.value() for s in bench.bar.weights] == [8, 16, 8]
    assert bench.bar.total is not None and bench.bar.total.text() == "32 of 32"


def test_there_is_nothing_to_edit_without_a_node_or_a_file(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench.show(None)
    assert bench.bar.note.text() == NO_NODE and bench.bar.fields == {}
    bench.show("not-a-node")
    assert bench.bar.note.text() == NO_NODE
    monkeypatch.setattr(bench.ws, "brain_editable", lambda row: False)
    bench.show(hub_id(bench.program) + "x")
    assert bench.bar.note.text() == NO_FILE
    bench.show(hub_id(bench.program))
    assert "pair" in bench.bar.fields  # a bar the workspace gives is shown


# ---- sync ------------------------------------------------------------------------------------ #


def test_a_sync_that_changes_nothing_keeps_the_controls(bench: Bench) -> None:
    bench.show(hub_id(bench.program))
    box, spin = bench.bar.fields["pair"], bench.bar.fields["sub"]
    bench.bar.sync()
    bench.bar.sync()
    assert bench.bar.fields["pair"] is box and bench.bar.fields["sub"] is spin


def test_the_bar_follows_a_change_made_elsewhere(bench: Bench) -> None:
    hub = hub_id(bench.program)
    bench.show(hub)
    assert bench.ws.edit_brain(be.set_pair(hub, 1, 4), ROW)
    bench.bar.sync()
    assert bench.bar.note.text() == "updating…"
    bench.settle()
    assert (bench.spin("main").value(), bench.spin("sub").value()) == (1, 4)
    assert bench.bar.note.text() == ""
    bench.ws.species_doc.undo()  # type: ignore[union-attr]
    bench.ws.refresh()
    bench.settle()
    assert (bench.spin("main").value(), bench.spin("sub").value()) == (3, 0)


def test_the_bar_goes_when_its_node_does(bench: Bench) -> None:
    call = call_id(bench.program)
    bench.show(call)
    assert "routine" in bench.bar.fields
    assert bench.ws.edit_brain(be.delete(call), ROW)
    bench.settle()
    assert bench.bar.fields == {} and bench.bar.note.text() == NO_NODE


def test_every_control_has_a_tip(bench: Bench) -> None:
    p = bench.program
    for ref in (hub_id(p), call_id(p), pick_id(p), big_id(p), switch_arm(p)):
        bench.show(ref)
        assert bench.bar.fields
        assert kit.missing_tips(bench.bar) == [], ref


# ---- the weights a pick keeps ---------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("weights", "at", "value", "want"),
    [
        ([16, 16], 0, 20, [20, 12]),
        ([16, 16], 1, 0, [32, 0]),
        ([8, 16, 8], 0, 16, [16, 11, 5]),
        ([8, 16, 8], 2, 32, [0, 0, 32]),
        ([8, 16, 8], 1, 99, [0, 32, 0]),  # no more than the draw
        ([0, 0, 32], 2, 8, [12, 12, 8]),  # nothing to take a share from: split evenly
        ([32], 0, 5, [32]),  # one branch is all of it
        ([10, 10, 10, 2], 3, 0, [11, 11, 10, 0]),
    ],
)
def test_the_others_share_what_is_left(
    weights: list[int], at: int, value: int, want: list[int]
) -> None:
    got = rebalance(weights, at, value)
    assert got == want and sum(got) == cat.DRAW
