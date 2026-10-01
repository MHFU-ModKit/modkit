# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from mhfu_studio.shell.document import History
from mhfu_studio.shell.findings import Finding, worst


def test_undo_redo_and_dirty() -> None:
    h = History([1])
    h.edit(lambda v: v.append(2))
    h.edit(lambda v: v.append(3))
    assert h.value == [1, 2, 3] and h.dirty
    h.undo()
    assert h.value == [1, 2]
    h.saved()
    h.redo()
    assert h.value == [1, 2, 3] and h.dirty
    h.undo()
    assert not h.dirty and h.can_redo()
    h.edit(lambda v: v.append(9))
    assert not h.can_redo() and h.value == [1, 2, 9]


def test_undo_keeps_snapshots_apart() -> None:
    h = History({"a": [1]})
    h.edit(lambda v: v["a"].append(2))
    h.undo()
    assert h.value == {"a": [1]}


def test_limit() -> None:
    h = History(0, limit=2)
    for i in range(1, 5):
        h.commit(i)
    h.undo()
    h.undo()
    h.undo()
    assert h.value == 2 and not h.can_undo()


def test_worst() -> None:
    assert worst([]) is None
    assert worst([Finding("info", "a", "x"), Finding("warning", "b", "y")]) == "warning"
    assert str(Finding("error", "c", "bad", where="clip 3")) == "error clip 3: bad [c]"
