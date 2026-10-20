# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The operand bar under the Brain tab's graph: the selected node's or arm's operands, one control
each.

`ws.brain_bar` says what to offer (`brain_edit.Bar`); a pick's weights add to a whole draw, so they
are edited as one set, with an Even button. A control never changes the file itself: its gesture
runs through `studio.act`, one undo step, and a refusal puts the control back with its reason.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from mhfu.em.script import catalogue as cat
from mhfu.em.script import ir
from mhfu.em.script.build import Built
from mhfu.em.script.opcodes import Shape
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QSpinBox, QWidget

from mhfu_studio.monster import brain_edit
from mhfu_studio.monster.brain_edit import Bar, Choice, Control, Gesture
from mhfu_studio.ui import kit

if TYPE_CHECKING:
    from mhfu_studio.monster.workspace import MonsterWorkspace
    from mhfu_studio.shell.studio import Studio

PAIR_NAMES = ("main", "sub")
"""The operands that make a move pair, which one gesture sets together."""
WEIGHT = "weight"
"""A pick's arm operand: the weights of all its arms are one set (the pick row), not one each."""
NO_FILE = "Open a brain file to edit this script"
NO_NODE = "Select a node to edit its operands"


def rebalance(weights: Sequence[int], at: int, value: int, total: int = cat.DRAW) -> list[int]:
    """`weights` with weight `at` made `value` (within the draw) and the others sharing what is
    left, each keeping its share of it; they add up to `total`."""
    value = max(0, min(value, total))
    rest = [i for i in range(len(weights)) if i != at]
    left = total - value
    out = list(weights)
    out[at] = value
    if not rest:
        out[at] = total
        return out
    base = sum(weights[i] for i in rest)
    exact = [weights[i] * left / base if base else left / len(rest) for i in rest]
    whole = [int(x) for x in exact]
    by_fraction = sorted(range(len(rest)), key=lambda k: exact[k] - whole[k], reverse=True)
    for k in by_fraction[: left - sum(whole)]:
        whole[k] += 1
    for i, w in zip(rest, whole, strict=True):
        out[i] = w
    return out


@dataclass(frozen=True)
class Pick:
    """A pick the selection is, or is an arm of: its block and each arm's id and weight."""

    block: str
    arms: tuple[tuple[str, int], ...]


class _Programs:
    """The index of a build's program and the block each arm belongs to, made once per build."""

    def __init__(self) -> None:
        self._built: Built | None = None
        self._index: ir.Index | None = None
        self._blocks: dict[str, ir.Block] = {}

    def pick(self, built: Built, ref: str) -> Pick | None:
        if built is not self._built or self._index is None:
            program = built.program
            self._built, self._index = built, ir.Index(program)
            self._blocks = {
                a.id: n
                for r in program.routines()
                for n in ir.walk(r.body)
                if isinstance(n, ir.Block)
                for a in n.arms
            }
        try:
            node = self._index.node(ref)
        except KeyError:
            return None
        block = self._blocks.get(ref) if isinstance(node, ir.Arm) else node
        if not isinstance(block, ir.Block) or block.op.shape is not Shape.RANDOM:
            return None
        arms: list[tuple[str, int]] = []
        for a in block.arms:
            w = a.args.get("weight")
            if not isinstance(w, int):
                return None
            arms.append((a.id, w))
        return Pick(block.id, tuple(arms))


class OperandBar(QWidget):
    """The bar for one node or arm of one row's script. `show_node` points it; `sync` brings it
    to what the workspace has now."""

    def __init__(self, ws: MonsterWorkspace, studio: Studio) -> None:
        super().__init__()
        self.ws, self.studio = ws, studio
        self._at: tuple[int, str | None] | None = None
        self._bar: Bar | None = None
        self._pick: Pick | None = None
        self._programs = _Programs()
        self._refusal = ""
        #: the controls of the bar shown, by what they edit
        self.fields: dict[str, QWidget] = {}
        self.weights: list[QSpinBox] = []
        self.total: kit.Alert | None = None
        self.title = kit.label("", role="heading", wrap=False)
        self.note = kit.Elided("", None, role="muted")
        self.body = QHBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(8)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(10)
        lay.addWidget(self.title)
        lay.addLayout(self.body)
        lay.addStretch(1)
        lay.addWidget(self.note, 2)
        self._show(None, None)

    # -- what the panel calls -- #

    def show_node(self, row: int, ref: str | None) -> None:
        """Offers the operands of the node or arm `ref` of `row`'s script (none for None)."""
        self._at = (row, ref)
        self._refusal = ""
        self.sync(force=True)

    def sync(self, *, force: bool = False) -> None:
        """After any change: the bar again from the workspace. A bar that is as it was is left
        alone, so a control being edited keeps its focus."""
        if self._at is None:
            self._show(None, None)  # nothing picked yet: whether the script takes edits
            return
        bar, pick = self._fetch(*self._at)
        if force or (bar, pick) != (self._bar, self._pick):
            self._show(bar, pick)
        else:
            self._say()

    # -- what is shown -- #

    def _fetch(self, row: int, ref: str | None) -> tuple[Bar | None, Pick | None]:
        if ref is None:
            return None, None
        built = self.ws.brain_built(row)  # takes a build that is done, which the bar then reads
        bar = self.ws.brain_bar(row, ref, self.ws.brain_header)
        return bar, None if bar is None or built is None else self._programs.pick(built, ref)

    def _show(self, bar: Bar | None, pick: Pick | None) -> None:
        """Makes every control again; the one that had the focus gets it back."""
        focused = next((k for k, w in self.fields.items() if w.hasFocus()), None)
        self._bar, self._pick = bar, pick
        self._clear()
        if bar is None:
            self.title.setText("")
            row = self.ws.brain_species if self._at is None else self._at[0]
            editable = row is not None and self.ws.brain_editable(row)
            self.note.setText(NO_NODE if editable else NO_FILE)
            self.note.setToolTip("")
            self.note.set_level(None)
            return
        self.title.setText(bar.title)
        pairs = bool(bar.pairs)
        if pairs:
            self._pairs(bar)
            for name in PAIR_NAMES:
                self._pair_spin(bar, name)
        for c in bar.controls:
            owned = (pairs and c.name in PAIR_NAMES) or (pick is not None and c.name == WEIGHT)
            if not owned:
                self._control(bar, c)
        if pick is not None:
            self._pick_row(pick)
        if bar.swaps:
            self._swaps(bar)
        self._say()
        if focused in self.fields:
            self.fields[focused].setFocus()

    def _clear(self) -> None:
        while (item := self.body.takeAt(0)) is not None:
            if (w := item.widget()) is not None:
                w.deleteLater()
        self.fields.clear()
        self.weights, self.total = [], None

    def _say(self) -> None:
        """The refusal beside the bar, else that an edit is still being built."""
        row = None if self._at is None else self._at[0]
        pending = row is not None and self.ws.brain_pending(row)
        text = self._refusal or ("updating…" if pending else "")
        self.note.setText(text)
        self.note.setToolTip(self._refusal)
        self.note.set_level("warning" if self._refusal else None)

    def _field(self, name: str, label: str, widget: QWidget) -> None:
        self.fields[name] = widget
        self.body.addWidget(kit.label(label, role="muted", wrap=False))
        self.body.addWidget(widget)

    def _pairs(self, bar: Bar) -> None:
        """The move's pair among the pairs the labels name, the current one first."""
        items = [(str(k), p.text) for k, p in enumerate(bar.pairs)]
        box = kit.choice(
            items,
            tip="The move this plays: the pairs the intel or the file names, then any pair by"
            " its numbers",
            on=lambda cid: self._set_pair(bar.pairs[int(cid)]),
            current="0",
        )
        self._field("pair", "Plays", box)

    def _pair_spin(self, bar: Bar, name: str) -> None:
        c = bar.control(name)
        spin = self._spin(c, lambda _: self._set_pair_numbers(bar))
        self._field(name, c.label, spin)

    def _control(self, bar: Bar, c: Control) -> None:
        if c.choices or not isinstance(c.value, int):
            self._field(c.name, c.label, self._choice(bar, c))
        else:
            self._field(c.name, c.label, self._spin(c, lambda v: self._apply(bar.set(c.name, v))))

    def _spin(self, c: Control, on: Callable[[int], object]) -> QSpinBox:
        value = c.value if isinstance(c.value, int) else 0
        return kit.integer(
            tip=f"{c.meaning}\nNow: {c.text}",
            value=max(c.lo, min(c.hi, value)),
            lo=c.lo,
            hi=c.hi,
            on=on,
        )

    def _choice(self, bar: Bar, c: Control) -> QComboBox:
        """What the operand can be: names to pick, or a number to type. The current value is
        listed first when the choices do not have it."""
        listed = list(c.choices)
        if all(ch.value != c.value for ch in listed):
            listed.insert(0, Choice(c.value, c.text))
        box = kit.choice(
            [(str(k), ch.text) for k, ch in enumerate(listed)],
            tip=f"{c.meaning}\nPick a name, or type a number\nNow: {c.text}",
            on=lambda cid: self._apply(bar.set(c.name, _value(listed[int(cid)]))),
            current=str(next(k for k, ch in enumerate(listed) if ch.value == c.value)),
        )
        box.setEditable(True)
        box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        edit = box.lineEdit()
        if edit is not None:
            edit.editingFinished.connect(partial(self._typed, bar, c, box, listed))
        return box

    def _typed(self, bar: Bar, c: Control, box: QComboBox, listed: Sequence[Choice]) -> None:
        """The user typed into a choice: a name of it, or a number in the operand's range."""
        text = box.currentText().strip()
        if not text or text == listed[box.currentIndex()].text:
            return  # unchanged, or a pick `kit.choice` already took
        hit = next((ch for ch in listed if ch.text.lower() == text.lower()), None)
        value = hit.value if hit is not None else _number(text, c.lo, c.hi)
        if value is None or isinstance(value, tuple):
            self._refusal = f"{c.label}: {text!r} is not a name or a number from {c.lo} to {c.hi}"
            self._show(self._bar, self._pick)
        else:
            self._apply(bar.set(c.name, value))

    def _swaps(self, bar: Bar) -> None:
        box = kit.choice(
            [("", f"Becomes… ({bar.title})"), *((e.name, e.title) for e in bar.swaps)],
            tip="Makes this node another opcode, keeping every operand",
            on=lambda op: self._apply(bar.swap(op)) if op else None,
            current="",
        )
        for k, e in enumerate(bar.swaps, 1):
            box.setItemData(k, e.meaning, Qt.ItemDataRole.ToolTipRole)
        self.fields["swap"] = box
        self.body.addWidget(box)

    def _pick_row(self, pick: Pick) -> None:
        """Each branch's weight and what is left of the draw; one edit moves the others."""
        self.weights = []
        for k, (_, w) in enumerate(pick.arms):
            spin = kit.integer(
                tip=f"Branch {k + 1}'s share of the draw; the other branches give or take the"
                " difference",
                value=w,
                lo=0,
                hi=cat.DRAW,
                on=partial(self._set_weight, pick, k),
            )
            self.weights.append(spin)
            self.fields[f"weight {k + 1}"] = spin
            self.body.addWidget(kit.label(str(k + 1), role="muted", wrap=False))
            self.body.addWidget(spin)
        self.total = kit.Alert("", None, role="muted", wrap=False)
        self.total.setToolTip("The draw is split into this many steps; the weights must fill it")
        self._total([w for _, w in pick.arms])
        even = kit.button(
            "Even", tip="Gives every branch of this pick the same weight", on=self._even
        )
        self.fields["total"], self.fields["even"] = self.total, even
        self.body.addWidget(self.total)
        self.body.addWidget(even)

    def _total(self, shares: Sequence[int]) -> None:
        if self.total is not None:
            got = sum(shares)
            self.total.setText(f"{got} of {cat.DRAW}")
            self.total.set_level(None if got == cat.DRAW else "warning")

    # -- the user's edits -- #

    def _set_pair(self, p: Choice) -> None:
        if (bar := self._bar) is not None and isinstance(p.value, tuple):
            self._apply(bar.pair(*p.value))

    def _set_pair_numbers(self, bar: Bar) -> None:
        main, sub = (self.fields[n] for n in PAIR_NAMES)
        if isinstance(main, QSpinBox) and isinstance(sub, QSpinBox):
            self._apply(bar.pair(main.value(), sub.value()))

    def _set_weight(self, pick: Pick, at: int, value: int) -> None:
        now = [w for _, w in pick.arms]
        shares = rebalance(now, at, value)
        if shares != now and self._apply(brain_edit.set_weights(pick.block, shares)):
            self._weights_now(shares)

    def _even(self) -> None:
        if self._pick is not None:
            self._apply(brain_edit.even_weights(self._pick.block))

    def _weights_now(self, shares: Sequence[int]) -> None:
        """What the spin boxes show after an edit that is still being built: the shares the
        edit made."""
        for spin, w in zip(self.weights, shares, strict=True):
            kit.put(spin, w)
        self._total(shares)

    def _apply(self, g: Gesture) -> bool:
        """One gesture, one undo step; whether it took. A refusal says why beside the bar and
        puts every control back; an edit that took leaves what the user set until the build
        shows it."""
        if self._at is None:
            return False
        row = self._at[0]
        done: list[bool] = []
        self.studio.act(g.label, lambda: done.append(self.ws.edit_brain(g, row)))()
        ok = bool(done and done[0])
        self._refusal = "" if ok else self.ws.message
        if ok:
            self._say()
        else:
            self._show(*self._fetch(*self._at))
        return ok


def _number(text: str, lo: int, hi: int) -> int | None:
    """The number a typed text starts with (`0x` for hex), when it is in range."""
    try:
        n = int(text.split()[0], 0)
    except (ValueError, IndexError):
        return None
    return n if lo <= n <= hi else None


def _value(c: Choice) -> ir.Value:
    """A choice's value as an operand: a number or a new routine's name."""
    if isinstance(c.value, tuple):
        raise ValueError(f"{c.text}: a pair is not an operand")
    return c.value
