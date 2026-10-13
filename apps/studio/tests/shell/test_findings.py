# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The findings' own clock: how often a re-check runs, and what a slow one does to it."""

from __future__ import annotations

import pytest
from mhfu_studio.shell import studio
from mhfu_studio.shell.findings import Finding
from mhfu_studio.shell.studio import SLOW_COST, SLOW_EVERY, Findings


class Clock:
    """`time.monotonic`, moved by the checks themselves."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class Doc:
    """A document whose check costs `cost` seconds on `clock`."""

    def __init__(self, clock: Clock, cost: float) -> None:
        self.clock, self.cost, self.calls = clock, cost, 0

    def findings(self) -> list[Finding]:
        self.calls += 1
        self.clock.t += self.cost
        return []


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(studio.time, "monotonic", c)
    return c


def run(fs: Findings, doc: Doc, secs: float, step: float = 0.25) -> list[float]:
    """The times in the next `secs` seconds (the window ticks every `step`) a check ran."""
    ran, t = [], 0.0
    while t <= secs:
        before = doc.calls
        fs.get(doc, now=t)
        if doc.calls > before:
            ran.append(t)
        t += step
    return ran


def test_a_cheap_check_repeats_every_second(clock: Clock) -> None:
    doc = Doc(clock, 0.005)
    assert run(Findings(), doc, 5) == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]


def test_a_check_dearer_than_a_tenth_of_its_interval_waits_longer_as_before(clock: Clock) -> None:
    doc = Doc(clock, 0.04)
    assert SLOW_COST > 0.04 and run(Findings(), doc, 3) == [0.0, 1.0, 2.0, 3.0]


def test_a_slow_check_is_not_repeated_for_a_minute(clock: Clock) -> None:
    doc = Doc(clock, 0.4)
    assert SLOW_COST < 0.4
    ran = run(Findings(), doc, 130)
    assert ran == [0.0, SLOW_EVERY, 2 * SLOW_EVERY]


def test_a_change_still_re_checks_at_once_however_slow_the_last_was(clock: Clock) -> None:
    fs, doc = Findings(), Doc(clock, 0.4)
    fs.get(doc, now=0.0)
    fs.get(doc, now=10.0)
    assert doc.calls == 1
    fs.stale()
    fs.get(doc, now=10.25)
    assert doc.calls == 2


def test_a_cheap_check_after_a_slow_one_brings_the_interval_back(clock: Clock) -> None:
    fs, doc = Findings(), Doc(clock, 0.4)
    fs.get(doc, now=0.0)
    doc.cost = 0.005  # the cache is warm
    fs.stale()
    fs.get(doc, now=1.0)
    assert doc.calls == 2
    fs.get(doc, now=1.5)
    fs.get(doc, now=2.0)
    assert doc.calls == 3


def test_another_document_is_checked_at_once(clock: Clock) -> None:
    fs = Findings()
    fs.get(Doc(clock, 0.4), now=0.0)
    other = Doc(clock, 0.4)
    fs.get(other, now=1.0)
    assert other.calls == 1


def test_a_check_that_fails_says_so_and_a_slow_failure_backs_off_too(clock: Clock) -> None:
    class Bad(Doc):
        def findings(self) -> list[Finding]:
            self.calls += 1
            self.clock.t += self.cost
            raise ValueError("no")

    fs, bad = Findings(), Bad(clock, 0.4)
    assert [f.code for f in fs.get(bad, now=0.0)] == ["check-failed"]
    fs.get(bad, now=5.0)
    assert bad.calls == 1
    fs.get(bad, now=SLOW_EVERY)
    assert bad.calls == 2
