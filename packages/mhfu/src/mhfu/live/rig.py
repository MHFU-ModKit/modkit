# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A live experiment set up without a human: the game booted fast or loaded from a savestate,
the player and a big monster put where the test needs them, the player kept alive.

    with Rig.open(Launcher(lane=1), state=6) as rig:     # PPSSPP_STATE/ULES01213_1.01_6.ppst
        rig.teleport(6400, 7800)
        tigrex = rig.summon(distance=600)
        with rig.pin_hp():
            ...

Every write to a monster here is one-shot: a big monster maintained per tick stops fighting.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from ppsspp_debug import DebuggerError, Disconnected, Lane, find_debuggers

from .. import addresses as a
from ..stage import Floor, map_manager
from ..structs import DRAW_GATE, BigMonster, Player
from . import boot, quests, survival
from .session import Launcher, Session

Vec3 = tuple[float, float, float]
Log = Callable[[str], None]

GAME_VERSION = "1.01"
"""The disc version PPSSPP puts in a savestate's name, after GAME_ID."""
TURN = 0x10000
"""ENTITY.YAW units in a full turn."""
SUMMON_DISTANCE = 600.0
"""Inside a Tigrex's charge reach (645), outside its body."""
BEARING_STEP = 30.0
"""Degrees a summon turns when the floor ends along its bearing."""
DRAWN_FOR = 0.5
"""Seconds SKIP_DRAW must stay clear before a summon counts as drawn."""


class OffFloor(ValueError):
    """No floor of the player's stage under the point."""


def running(launcher: Launcher) -> bool:
    """An emulator runs in the lane, the container, or on this machine outside a lane."""
    if launcher.docker:
        return launcher.docker_emulator().running()
    if launcher.lane is not None:
        return bool(Lane(launcher.lane).debuggers())
    return bool(find_debuggers())


def state_file(slot: int, lane: int) -> Path:
    """Savestate slot `slot` on a lane's memory stick (the PPSSPP menu numbers it slot + 1)."""
    return Lane(lane).stick / "PPSSPP_STATE" / f"{a.GAME_ID}_{GAME_VERSION}_{slot}.ppst"


def floor(s: Session) -> Floor:
    """The walkable floor of the stage on screen, read with the CPU stopped (one frame)."""
    with s.client.paused():
        return Floor.read(s.mem, map_manager(s.mem).stage)


def _player(s: Session) -> Player:
    player = s.game.player
    if not player.loaded:
        raise RuntimeError("no player entity: the game is loading or on a menu")
    return player


def teleport(s: Session, x: float, z: float, near: float | None = None) -> Vec3:
    """Put the player at (x, z) on the floor (the highest there, or the one nearest `near`).

    Within the stage on screen only: crossing to another section is a load, not a write.
    """
    player = _player(s)
    y = floor(s).height(x, z, near)
    if y is None:
        raise OffFloor(f"no floor of this stage under ({x:.0f}, {z:.0f})")
    player.position = (x, y, z)
    return x, y, z


def big_monsters(s: Session) -> list[BigMonster]:
    """Registry entities of a quest target species or a known big-monster class."""
    targets = s.game.quest.target_species - {0}
    return [
        BigMonster(s.mem, e.base)
        for e in s.game.monsters().values()
        if e.species in targets or e.vtable in survival.BIG_MONSTER_VTABLES
    ]


@dataclass(frozen=True)
class Summoned:
    monster: BigMonster
    at: Vec3
    distance: float
    """From the player, in x and z."""
    drawn: bool
    """VISIBILITY_GATE drew it for DRAWN_FOR seconds."""


def summon(
    s: Session,
    monster: BigMonster | None = None,
    *,
    distance: float = SUMMON_DISTANCE,
    bearing: float | None = None,
    timeout: float = 3.0,
) -> Summoned:
    """Move a big monster (the first by default) `distance` from the player, facing him.

    `bearing` is degrees in the game's atan2(dx, dz), by default the way the player faces;
    where the floor ends along it the bearing turns in BEARING_STEP steps. One relocation and
    one visibility fix (ENTITY.SECTION, DRAW_GATE), then the draw is read back from memory.
    """
    player = _player(s)
    if monster is None:
        found = big_monsters(s)
        if not found:
            raise LookupError("no big monster in the entity registry")
        monster = found[0]
    px, py, pz = player.position
    ground = floor(s)
    start = math.degrees(player.facing) if bearing is None else bearing
    for k in range(int(360 / BEARING_STEP)):
        turn = (k + 1) // 2 * BEARING_STEP * (1 if k % 2 else -1)
        angle = math.radians(start + turn)
        x, z = px + distance * math.sin(angle), pz + distance * math.cos(angle)
        y = ground.height(x, z, near=py)
        if y is not None:
            break
    else:
        raise OffFloor(f"no floor {distance:.0f} from the player in any direction")
    monster.position = (x, y, z)
    monster.yaw = round(math.atan2(px - x, pz - z) / math.tau * TURN) % TURN
    area = s.game.area_index
    if monster.section != area:
        monster.section = area
    flags = monster.flags
    if not flags & DRAW_GATE:
        monster.flags = flags | DRAW_GATE
    drawn = _drawn(s, monster, timeout)
    return Summoned(monster, (x, y, z), math.hypot(x - px, z - pz), drawn)


def _drawn(s: Session, monster: BigMonster, timeout: float) -> bool:
    try:
        s.wait(lambda: s.holds(lambda: monster.drawn, DRAWN_FOR), timeout, "the draw")
    except TimeoutError:
        return False
    return True


def pin_hp(s: Session, hp: int | None = None, *, tick: float = 0.5) -> survival.Guard:
    """A Guard holding the player's HP (his maximum by default) and the quest clock; enter it
    to start, leave it to stop. Monsters stay free to attack."""
    pinned = _player(s).max_hp if hp is None else hp
    return survival.Guard(s, hp=pinned, calm_monsters=False, tick=tick)


class Rig:
    """A game set up for an experiment, and cleaned up after: pins stopped, speed restored.

    The emulator keeps running unless `stop_on_exit`, so the next rig attaches in a second.
    """

    def __init__(self, s: Session, launcher: Launcher | None = None) -> None:
        self.s = s
        self.launcher = launcher or Launcher.from_env()
        self.guards: list[survival.Guard] = []

    @classmethod
    def open(
        cls,
        launcher: Launcher | None = None,
        *,
        state: int | str | Path | None = None,
        quest: str | None = None,
        rank: int | str = 0,
        fast: bool = True,
        stop_on_exit: bool = False,
        log: Log | None = None,
    ) -> Rig:
        """The game in `launcher`'s lane: loaded from `state` (a slot on the lane's stick, or a
        path), cold booted into `quest` at `rank`, or as it runs (started if it does not).

        A state loads into a running game in a fraction of a second, and relaunches it when the
        load fails. A cold boot fast-forwards up to the base camp; plugins load only on one.
        """
        launcher = launcher or Launcher.from_env()
        if state is not None and quest is not None:
            raise ValueError("a state or a quest, not both")
        if quest is not None:
            rig = cls(Session.launch(launcher, cold=True, stop_on_exit=stop_on_exit), launcher)
            with boot.fast_forward(rig.s, fast):
                boot.to_village(rig.s)
                quests.take(rig.s, rank, quest, log=log)
            return rig
        if state is None:
            return cls(Session.launch(launcher, stop_on_exit=stop_on_exit), launcher)
        path = cls.state_path(launcher, state)
        if running(launcher):
            rig = cls(Session.launch(launcher, stop_on_exit=stop_on_exit), launcher)
            try:
                rig.load(path)
                return rig
            except (DebuggerError, TimeoutError):
                rig.s.close()
        s = Session.launch(launcher, state=path, stop_on_exit=stop_on_exit)
        s.wait(lambda: s.game.player.loaded, 30.0, "the player after the load")
        return cls(s, launcher)

    @classmethod
    def attach(cls, launcher: Launcher | None = None) -> Rig:
        """The game already running in `launcher`'s lane; never starts one."""
        launcher = launcher or Launcher.from_env()
        if not running(launcher):
            where = f"lane {launcher.lane}" if launcher.lane is not None else "this machine"
            raise ConnectionError(f"no game running in {where}")
        return cls(Session.launch(launcher), launcher)

    @staticmethod
    def state_path(launcher: Launcher, state: int | str | Path) -> Path:
        """A slot number (needs a lane) or a path, as the emulator sees it."""
        if isinstance(state, Path) or not str(state).isdigit():
            return Path(str(state))
        if launcher.lane is None:
            raise ValueError("a savestate slot needs a lane; give a path")
        return state_file(int(state), launcher.lane)

    def load(self, state: int | str | Path) -> None:
        """Load a savestate into the running game; a framework plugin stops until a cold boot."""
        self.s.client.load_state(self.state_path(self.launcher, state))
        self.s.wait(lambda: self.s.game.player.loaded, 10.0, "the player after the load")

    def save(self, state: int | str | Path) -> Path:
        path = self.state_path(self.launcher, state)
        self.s.client.save_state(path)
        return path

    def speed(self, *, fast: bool = False) -> bool:
        """Fast-forward, or the game's own rate; False on a PPSSPP that cannot."""
        return boot.set_fast(self.s, fast)

    def teleport(self, x: float, z: float, near: float | None = None) -> Vec3:
        return teleport(self.s, x, z, near)

    def summon(
        self,
        monster: BigMonster | None = None,
        *,
        distance: float = SUMMON_DISTANCE,
        bearing: float | None = None,
    ) -> Summoned:
        return summon(self.s, monster, distance=distance, bearing=bearing)

    def pin_hp(self, hp: int | None = None, *, tick: float = 0.5) -> survival.Guard:
        """`pin_hp`, stopped with the rig if it is still running."""
        guard = pin_hp(self.s, hp, tick=tick)
        self.guards.append(guard)
        return guard

    def close(self) -> None:
        for guard in self.guards:
            guard.__exit__(None, None, None)
        try:
            boot.set_fast(self.s, False)
        except (Disconnected, DebuggerError):
            pass
        self.s.close()

    def __enter__(self) -> Rig:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
