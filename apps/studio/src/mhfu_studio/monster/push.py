# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Push a port's hit tables into the running game through the debugger: `runtime.plan`, the
writes the memory stick's module carries, written and read back. Nothing goes on the stick.

Every guard is read before anything is written, and one that fails refuses the whole push. A
record lever applies from the next attack a move spawns (`ATTACK_TABLE_SETTER` copies the
record then); a volume or grid edit from the next hit. A reload of the overlay (the next quest)
puts the game's own tables back.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from mhfu.memory import Memory, Unmapped
from mhfu.structs import Game
from mhfu_port.manifest import Manifest

from . import runtime, species
from .runtime import Host, Plan, Write

ATTACH_TIMEOUT = 5.0
"""Seconds to wait for the debugger before a push gives up."""


class Refused(ValueError):
    """Why nothing was written."""


@dataclass
class Pushed:
    plan: Plan
    seconds: float
    """Guards, writes and read-back."""
    mismatched: list[str] = field(default_factory=list)
    """Writes whose read-back is not what was written."""
    where: str = ""

    def describe(self) -> str:
        w = self.plan.writes
        head = (
            f"pushed {len(w)} write(s), {self.plan.size} bytes, into {self.where or 'the game'}"
            f" in {self.seconds:.2f} s"
        )
        if self.mismatched:
            return f"{head}; read back DIFFERS at {', '.join(self.mismatched)}"
        notes = "".join(f"; {n}" for n in self.plan.notes)
        return f"{head}; read back matches{notes}"


def failing(mem: Memory, writes: tuple[Write, ...]) -> str | None:
    """The first guard that does not hold, in words; None when all do."""
    seen: dict[tuple[int, int], bytes] = {}
    for w in writes:
        for at, want in w.guards:
            key = (at, len(want))
            if key not in seen:
                try:
                    seen[key] = mem.read(at, len(want))
                except Unmapped:
                    seen[key] = b""
            if seen[key] != want:
                got = seen[key].hex().upper() or "nothing"
                return f"{w.what}: 0x{at:08X} reads {got}, the plan expects {want.hex().upper()}"
    return None


def apply(mem: Memory, p: Plan, where: str = "") -> Pushed:
    """Every write of `p`, all or nothing, then read back."""
    t0 = time.monotonic()
    why = failing(mem, p.writes)
    if why is not None:
        raise Refused(
            f"the game does not hold the tables this plan was built against ({why}): another"
            " overlay, or a relocated one"
        )
    for w in p.writes:
        mem.write(w.at, w.data)
    bad = [w.what for w in p.writes if mem.read(w.at, len(w.data)) != w.data]
    return Pushed(p, time.monotonic() - t0, bad, where)


def present(game: Game, host: Host) -> bool:
    """A monster of the host species is in the registry, so its overlay is loaded."""
    return any(e.species == host.species for e in game.monsters().values())


@contextmanager
def attach() -> Iterator[tuple[Game, str]]:
    """The game in `$MHFU_LANE`'s lane, else the PPSSPP on this machine; never starts one."""
    from mhfu.live.rig import running
    from mhfu.live.session import Launcher, Session

    launcher = Launcher.from_env()
    where = f"lane {launcher.lane}" if launcher.lane is not None else "this machine"
    if not running(launcher):
        hint = "" if launcher.lane is not None else " (set MHFU_LANE for a lane's)"
        raise Refused(f"no game attached: no PPSSPP with its debugger runs on {where}{hint}")
    try:
        s = Session.launch(launcher, stop_on_exit=False, timeout=ATTACH_TIMEOUT)
    except (ConnectionError, TimeoutError, OSError) as e:
        raise Refused(f"no game attached on {where}: {e}") from None
    with s:
        yield s.game, f"{where}'s game"


def to_game(m: Manifest, host: Host | None, source: str = "") -> Pushed:
    """`m`'s plan into the running game; `Refused` (or `ManifestError`) says why not."""
    p = runtime.plan(m, host, source)
    assert host is not None
    with attach() as (game, where):
        if not present(game, host):
            raise Refused(
                f"no {species.label(host.species)} in {where}: its tables are loaded only"
                " while one is in the quest"
            )
        return apply(game.mem, p, where)


ID = re.compile(r'^\s*id = "([0-9a-f]{8})",', re.M)


def stick_differs(m: Manifest, mods: Path) -> str:
    """A note when the stick carries this port's module with other tables: in a framework boot
    it puts its own back while the port is in the area."""
    path = mods / runtime.module_name(m)
    try:
        found = ID.search(path.read_text(encoding="utf-8"))
    except OSError:
        return ""
    if found is None or found[1] == runtime.content_id(m):
        return ""
    return (
        f"; the memory stick's {path.name} (id {found[1]}) puts its own tables back while the"
        " port is in the area: Send to game to put these there too"
    )
