# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A live experiment set up without a human: the game booted fast or loaded from a savestate,
the player and a big monster put where the test needs them, the player kept alive.

    with Rig.open(state=6) as rig:    # MHFU_LANE's PPSSPP_STATE/ULES01213_1.01_6.ppst
        rig.teleport(6400, 7800)
        tigrex = rig.summon(distance=600)
        with rig.pin_hp(cull=True):
            rig.hold()                # its AI script waits; reactions still play
            rig.aim(44)               # the tail tip straight ahead, then press triangle
            ...

Every write to a monster here is one-shot: a big monster maintained per tick stops fighting.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont
from ppsspp_debug import DebuggerError, Disconnected, Lane, find_debuggers

from .. import addresses as a
from ..memory import Unmapped
from ..stage import Floor, map_manager
from ..structs import DRAW_GATE, BigMonster, Player
from . import boot, quests, survival
from .session import Launcher, Session

if TYPE_CHECKING:
    from ..points import Point
    from . import navigation, route

Vec3 = tuple[float, float, float]
Log = Callable[[str], None]

GAME_VERSION = "1.01"
"""The disc version PPSSPP puts in a savestate's name, after GAME_ID."""
TURN = 0x10000
"""ENTITY.YAW units in a full turn."""
SUMMON_DISTANCE = 600.0
"""Inside a Tigrex's charge reach (645), outside its body."""
AIM_DISTANCE = 170.0
"""From the player to the bone `aim` puts ahead: a weapon's first swing reaches it."""
HOLD = 0x7FFF
"""ENTITY.SCRIPT_WAIT `hold` writes: AI frames, ~18 minutes."""
SETTLE = 0.3
"""Seconds for the pose to follow a monster's turned YAW."""
BEARING_STEP = 30.0
"""Degrees a summon turns when the floor ends along its bearing."""
DRAWN_FOR = 0.5
"""Seconds SKIP_DRAW must stay clear before a summon counts as drawn."""
SHEET_COLUMNS = 6
"""Frames across a film's contact sheet."""


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
        BigMonster(s.mem, e.base) for e in s.game.monsters().values() if survival.is_big(e, targets)
    ]


def _first(s: Session, monster: BigMonster | None) -> BigMonster:
    if monster is not None:
        return monster
    found = big_monsters(s)
    if not found:
        raise LookupError("no big monster in the entity registry")
    return found[0]


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
    """Move a big monster (the first by default) `distance` from the player, facing the player.

    `bearing` is degrees in the game's atan2(dx, dz), by default the way the player faces;
    where the floor ends along it the bearing turns in BEARING_STEP steps. One relocation and
    one visibility fix (ENTITY.SECTION, DRAW_GATE), then the draw is read back from memory.
    """
    player = _player(s)
    monster = _first(s, monster)
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


def hold(s: Session, monster: BigMonster | None = None) -> BigMonster:
    """Stop a big monster's AI script choosing its next move (the first by default).

    It ends what it is doing and waits in a hub; its reactions (flinch, break, tail cut) still
    play, and the first one ends the hold: the reaction zeroes SCRIPT_WAIT.
    """
    monster = _first(s, monster)
    monster.script_wait = HOLD
    return monster


def aim(
    s: Session,
    bone: int,
    monster: BigMonster | None = None,
    *,
    distance: float = AIM_DISTANCE,
    turn: bool = True,
) -> Vec3:
    """Put the player `distance` before a big monster's `bone`, facing it.

    A teleport keeps the player's facing, so the bone ends up straight ahead. With `turn` the
    monster is turned first so the bone is on the player's side of its body (a tail end-on).
    """
    player, monster = _player(s), _first(s, monster)
    f = player.facing
    if turn:
        mx, _, mz = monster.position
        bx, _, bz = monster.joint(bone)
        miss = f + math.pi - math.atan2(bx - mx, bz - mz)
        monster.yaw = (monster.yaw + round(miss / math.tau * TURN)) % TURN
        s.sleep(SETTLE)
    bx, _, bz = monster.joint(bone)
    return teleport(s, bx - distance * math.sin(f), bz - distance * math.cos(f))


def pin_hp(
    s: Session, hp: int | None = None, *, tick: float = 0.5, cull: bool = False
) -> survival.Guard:
    """A Guard holding the player's HP (the maximum by default) and the quest clock, and with
    `cull` no small monster in the player's section; enter it to start, leave it to stop. Big
    monsters stay free to attack."""
    pinned = _player(s).max_hp if hp is None else hp
    return survival.Guard(s, hp=pinned, calm_monsters=False, cull_small=cull, tick=tick)


# After a launch with a state, memory is read only with the CPU stopped: PPSSPP deadlocks on a
# read that waits for the CPU to stop while the queued load reinitialises memory, which it does
# when the state's memory size is not the boot's (a plugin.ini `memory =` line).


def _paused(s: Session) -> bool:
    """Stopped the CPU; False while PPSSPP says it has not started."""
    try:
        s.client.pause()
    except DebuggerError as e:
        if e.message != "CPU not started":
            raise
        return False
    return True


def _loaded(s: Session) -> bool:
    """The player is in memory; False while memory is not up."""
    try:
        return s.game.player.loaded
    except Unmapped:
        return False


class Rig:
    """A game set up for an experiment, and cleaned up after: its pins stopped, the game's own
    rate back if it fast-forwarded.

    The emulator keeps running unless `stop_on_exit`, so the next rig attaches in a second.
    """

    def __init__(self, s: Session, launcher: Launcher | None = None) -> None:
        self.s = s
        self.launcher = launcher or Launcher.from_env()
        self.guards: list[survival.Guard] = []
        self.fast = False

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
            try:
                with boot.fast_forward(rig.s, fast):
                    boot.to_village(rig.s)
                    quests.take(rig.s, rank, quest, log=log)
            except BaseException:
                rig.close()
                raise
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
        try:
            s.wait(lambda: _paused(s), 10.0, "the CPU to stop")
            try:
                s.wait(lambda: _loaded(s), 30.0, "the player after the load")
            finally:
                s.client.resume()
        except BaseException:
            s.close()
            raise
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
        done = boot.set_fast(self.s, fast)
        self.fast = fast and done
        return done

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

    def hold(self, monster: BigMonster | None = None) -> BigMonster:
        return hold(self.s, monster)

    def aim(
        self,
        bone: int,
        monster: BigMonster | None = None,
        *,
        distance: float = AIM_DISTANCE,
        turn: bool = True,
    ) -> Vec3:
        return aim(self.s, bone, monster, distance=distance, turn=turn)

    def pin_hp(
        self, hp: int | None = None, *, tick: float = 0.5, cull: bool = False
    ) -> survival.Guard:
        """`pin_hp`, stopped with the rig if it is still running."""
        guard = pin_hp(self.s, hp, tick=tick, cull=cull)
        self.guards.append(guard)
        return guard

    def close(self) -> None:
        for guard in self.guards:
            guard.__exit__(None, None, None)
        if self.fast:
            with suppress(Disconnected):
                boot.set_fast(self.s, False)
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

    def goto(self, point: Point, plan: route.Map, log: Log | None = None) -> Vec3:
        """`route.goto`: the hunter on the floor at `point`, one area change away at most."""
        from . import route

        return route.goto(self.s, point, plan, log)

    def walk(
        self, point: Point, plan: route.Map, climbs: Sequence[Point] = (), log: Log | None = None
    ) -> navigation.Walk:
        """`route.walk`: walked to `point`, exit to exit, taking the ledges in `climbs`."""
        from . import route

        return route.walk(self.s, point, plan, climbs, log=log)

    def shot(self, path: str | Path, scale: int = 1) -> Path:
        return shot(self.s, path, scale)

    def film(
        self,
        folder: str | Path,
        seconds: float,
        fps: float,
        *,
        scale: int = 1,
        columns: int = SHEET_COLUMNS,
    ) -> Film:
        return film(self.s, folder, seconds, fps, scale=scale, columns=columns)


def shot(s: Session, path: str | Path, scale: int = 1) -> Path:
    """The frame on screen as a PNG at `path`, while the game runs: at most `scale` times
    480x272, 0 for the render size. PPSSPP on this machine or in a lane; the modkit's build."""
    out = Path(path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    s.client.save_screenshot(out, scale)
    return out


@dataclass(frozen=True)
class Film:
    frames: list[Path]
    times: list[float]
    """Seconds from the first frame, when each was asked for."""
    costs: list[float]
    """Seconds each shot took to answer."""
    speed: float
    """The game's speed as a multiple of real time over PPSSPP's last whole second of rates:
    the last second of filming, in a film of 2 s or more."""
    sheet: Path

    @property
    def fps(self) -> float:
        """Frames per second achieved."""
        span = self.times[-1] - self.times[0] if len(self.times) > 1 else 0.0
        return (len(self.times) - 1) / span if span else 0.0


def film(
    s: Session,
    folder: str | Path,
    seconds: float,
    fps: float,
    *,
    scale: int = 1,
    columns: int = SHEET_COLUMNS,
) -> Film:
    """`seconds` of shots `1 / fps` apart in `folder` (0000.png, ...), and sheet.png of them all.

    A shot answering late skips the slots that pass meanwhile; `Film.fps` is the rate achieved.
    """
    if seconds <= 0 or fps <= 0:
        raise ValueError("a film needs seconds and fps above 0")
    out = Path(folder).resolve()
    out.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    times: list[float] = []
    costs: list[float] = []
    start, slot = s.now(), 0
    while (due := slot / fps) < seconds:
        if (wait := start + due - s.now()) > 0:
            s.sleep(wait)
        asked = s.now()
        frames.append(shot(s, out / f"{len(frames):04d}.png", scale))
        times.append(asked - start)
        costs.append(s.now() - asked)
        slot = max(slot + 1, round((s.now() - start) * fps))
    speed = s.client.frame_stats().speed
    labels = [f"{k}  {t:.2f}s" for k, t in enumerate(times)]
    sheet = contact_sheet(frames, out / "sheet.png", labels, columns)
    return Film(frames, times, costs, speed, sheet)


def contact_sheet(
    frames: Sequence[Path], out: Path, labels: Sequence[str], columns: int = SHEET_COLUMNS
) -> Path:
    """`frames` in rows of `columns` on one PNG, each labelled in its top left corner."""
    images: list[Image.Image] = []
    for f in frames:
        with Image.open(f) as opened:
            images.append(opened.convert("RGB"))
    w, h = images[0].size
    gap = 2
    cols = min(columns, len(images))
    rows = math.ceil(len(images) / cols)
    sheet = Image.new("RGB", (cols * (w + gap) - gap, rows * (h + gap) - gap))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=max(12, h // 12))
    for k, (image, label) in enumerate(zip(images, labels, strict=True)):
        x, y = k % cols * (w + gap), k // cols * (h + gap)
        sheet.paste(image, (x, y))
        left, top, right, bottom = draw.textbbox((x + 4, y + 3), label, font=font)
        draw.rectangle((left - 3, top - 2, right + 3, bottom + 2), fill="black")
        draw.text((x + 4, y + 3), label, fill="white", font=font)
    sheet.save(out)
    return out
