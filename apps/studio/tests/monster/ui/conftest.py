# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Every Qt test: no question blocks, and the GL context comes back (root conftest). Also the
game as the Behaviour tab's tests fake it."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

import pytest
from mhfu.live.brain import UNLIMITED, Brain, Slot
from mhfu_port import behaviour as model
from mhfu_port.manifest import Manifest
from mhfu_studio.monster import rule_game as rg

CFG = 0x1000


class FakeGame:
    """The game: it runs the rules of `m` as sent, fired `fired` times each; `calls` is where
    each read was told the brain was."""

    def __init__(self, m: Manifest) -> None:
        self.rules = model.compile(m)
        self.fired = [0] * len(self.rules)
        self.ticks = 100
        self.calls: list[int | None] = []
        #: a read to answer with instead of the brain, and whether the game runs other rules
        self.answer: Callable[[], rg.Read] | None = None
        self.other_build = False

    def __call__(
        self, attach: Callable[[], AbstractContextManager[Any]], cfg: int | None
    ) -> rg.Read:
        self.calls.append(cfg)
        if self.answer is not None:
            return self.answer()
        slots = tuple(
            Slot(
                k,
                rg.kind(r.on),
                -1 if r.signal is None else r.signal,
                bool(r.play),
                r.min_frames,
                r.cooldown + (7 if self.other_build else 0),
                n,
                UNLIMITED,
                0,
            )
            for k, (r, n) in enumerate(zip(self.rules, self.fired, strict=True))
        )
        return rg.Read(CFG, Brain(CFG, self.ticks, slots))


@pytest.fixture(autouse=True)
def _ui(asked: list[Any], gl_back: None) -> None:
    """No question blocks; the GL context comes back after the test's windows close."""
