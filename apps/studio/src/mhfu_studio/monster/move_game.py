# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Play move in game: the port's clips and moves modules onto the memory stick as `mhfu-port
inject` writes them, then the running port asked for one of its own moves by name."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from mhfu import inject
from mhfu_port import build, layout
from mhfu_port.data import Data
from mhfu_port.manifest import Manifest

from mhfu_studio.monster import runtime

if TYPE_CHECKING:
    from mhfu.live.session import Session


def deploy(m: Manifest, games: Data, mods: Path) -> list[Path]:
    """`m` built as it is, its modules written into `mods/lib`, and `mhfu_port.lua` kept in
    step; what was written. The game re-runs a changed module."""
    from mhfu_port.cli.build import _modules  # the writer `mhfu-port inject` uses

    out = _modules(m, build.build(m, games), mods / layout.LIB, games)
    lib = runtime.sync_library(mods, runtime.library())
    return out if lib is None else [*out, lib]


def play_own(s: Session, name: str) -> bool:
    """`mhfu.live.moves.play_own`: False when no port rides the big monster or it has no own
    move `name`. The seam the tests fake."""
    from mhfu.live import moves

    fn = getattr(moves, "play_own", None)
    if fn is None:
        raise LookupError("this modkit's mhfu.live.moves has no play_own")
    return bool(fn(s, name))


class Running:
    """Whether a PPSSPP is there to ask (`$MHFU_LANE`'s, else one on this machine), from a
    process scan made at most every `ttl` seconds."""

    def __init__(self, ttl: float = 2.0) -> None:
        self.ttl = ttl
        self._seen = (-ttl, False)

    def __call__(self) -> bool:
        at, seen = self._seen
        now = time.monotonic()
        if now - at >= self.ttl:
            from ppsspp_debug import emulators

            lane = inject.lane()
            seen = bool(lane.processes() if lane is not None else emulators())
            self._seen = (now, seen)
        return seen
