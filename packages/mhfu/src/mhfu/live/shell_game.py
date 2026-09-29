"""Shell commands on the player, the quest, the screen, the monsters and savestates."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import TYPE_CHECKING

from ppsspp_debug import Unsupported

from .. import addresses as a
from ..structs import Player, Screen
from .shell import SCENES, CommandError, Monster, Poller, integer, table, vec

if TYPE_CHECKING:
    from .shell import Shell

QUEST_FPS = 30
"""QUEST.TIMER counts frames at this rate."""
PAINT = 0xFF
PAINT_PERIOD = 0.5
"""MAP_PAINT has to be rewritten at 2 Hz or faster."""
EM_ID_SPECIES = 0xFF
"""QUEST_TARGET.EM_ID holds the species in its low byte."""


def register(shell: Shell) -> None:
    shell.command("get player hp", "current, cap and maximum HP", get_player_hp)
    p = shell.command("set player hp", "write current HP", set_player_hp)
    p.add_argument("value", type=integer)
    shell.command("get player stamina", "stamina", get_player_stamina)
    p = shell.command("set player stamina", "write stamina", set_player_stamina)
    p.add_argument("value", type=integer)
    shell.command("get player pos", "world position", get_player_pos)
    p = shell.command("set player pos", "teleport the player", set_player_pos)
    p.add_argument("xyz", type=float, nargs=3, metavar=("x", "y", "z"))
    shell.command("get timer", "quest time left", get_timer)
    p = shell.command("set timer", "set the quest time left", set_timer)
    p.add_argument("seconds", type=integer)
    p = shell.command("paint", "keep every big monster painted on the map", paint)
    p.add_argument("on", choices=("on", "off"))
    shell.command("sys screen", "screen state and scene", sys_screen)
    shell.command("sys section", "map section", sys_section)
    shell.command("sys status", "one-line summary", sys_status)

    shell.command("ls mon", "every monster in the entity registry", ls_mon)
    shell.command("ls bigmon", "the monsters whose species is a quest target", ls_bigmon)
    p = shell.command("get mon", "a monster's fields", get_mon)
    p.add_argument("slot", type=integer)
    p.add_argument(
        "field", nargs="?", choices=sorted(FIELDS), metavar="field", help=", ".join(FIELDS)
    )
    p = shell.command("set mon", "write a monster's hp, size or position", set_mon)
    p.add_argument("slot", type=integer)
    p.add_argument("field", choices=("hp", "size", "pos"))
    p.add_argument("values", type=float, nargs="+")
    p = shell.command("mon aggro", "let a monster notice the hunter, or calm its species", aggro)
    p.add_argument("slot", type=integer)
    p.add_argument("on", choices=("on", "off"))

    p = shell.command("state save", "save a savestate to a path, as the emulator sees it", save)
    p.add_argument("path")
    p = shell.command("state load", "load a savestate from a path, as the emulator sees it", load)
    p.add_argument("path")


# --- player and quest ---


def _player(shell: Shell) -> Player:
    player = shell.game.player
    if not player.loaded:
        raise CommandError("no player entity: the game is loading or on a menu")
    return player


def get_player_hp(shell: Shell, args: argparse.Namespace) -> str:
    p = _player(shell)
    hp, cap = p.hp, p.hp_cap
    return f"HP {hp}/{p.max_hp}" + (f" (cap {cap})" if cap != hp else "")


def set_player_hp(shell: Shell, args: argparse.Namespace) -> str:
    _player(shell).hp = args.value
    return f"player HP -> {args.value}"


def get_player_stamina(shell: Shell, args: argparse.Namespace) -> str:
    return f"stamina {shell.game.stamina}"


def set_player_stamina(shell: Shell, args: argparse.Namespace) -> str:
    shell.game.stamina = args.value
    shell.mem.write_u16(a.PLAYER_STAMINA_HEAP, args.value)
    return f"player stamina -> {args.value}"


def get_player_pos(shell: Shell, args: argparse.Namespace) -> str:
    return f"pos {vec(_player(shell).position)}"


def set_player_pos(shell: Shell, args: argparse.Namespace) -> str:
    x, y, z = args.xyz
    _player(shell).position = (x, y, z)
    return f"player pos -> {vec(args.xyz)}"


def _clock(frames: int) -> str:
    seconds = frames // QUEST_FPS
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def get_timer(shell: Shell, args: argparse.Namespace) -> str:
    frames = shell.game.quest.timer
    return f"quest timer {_clock(frames)} ({frames} frames)"


def set_timer(shell: Shell, args: argparse.Namespace) -> str:
    frames = args.seconds * QUEST_FPS
    shell.game.quest.timer = frames
    shell.game.quest_timer_mirror = frames
    return f"quest timer -> {_clock(frames)}"


def paint(shell: Shell, args: argparse.Namespace) -> str:
    game = shell.game
    shell.stop("paint")
    if args.on == "off":
        game.map_paint = 0
        return "map paint off"

    def tick() -> None:
        game.map_paint = PAINT

    shell.start("paint", Poller(tick, PAINT_PERIOD))
    return f"map paint on, rewritten every {PAINT_PERIOD:g} s"


def _screen(shell: Shell) -> str:
    state = shell.game.screen_state
    try:
        return f"{state} ({Screen(state).name})"
    except ValueError:
        return str(state)


def sys_screen(shell: Shell, args: argparse.Namespace) -> str:
    scene = shell.game.scene
    return f"screen {_screen(shell)}, scene {SCENES.get(scene, f'0x{scene:08X}')}"


def sys_section(shell: Shell, args: argparse.Namespace) -> str:
    return f"area {shell.game.area_index}, sub-area {shell.game.map_subsection}"


def sys_status(shell: Shell, args: argparse.Namespace) -> str:
    game = shell.game
    player = game.player
    hp = f"{player.hp}/{player.max_hp}" if player.loaded else "n/a"
    return (
        f"screen={_screen(shell)} area={game.area_index} timer={_clock(game.quest.timer)} "
        f"monsters={len(game.monsters())} player_hp={hp}"
    )


# --- monsters ---


def _rows(monsters: dict[int, Monster]) -> str:
    if not monsters:
        return "(no monsters loaded)"
    rows = [
        [k, f"0x{m.base:08X}", f"0x{m.species:02X}", m.name, m.hp, f"{m.size_scale:.2f}"]
        + [vec(m.position)]
        for k, m in monsters.items()
    ]
    return table(["slot", "entity", "species", "name", "hp", "size", "pos"], rows)


def ls_mon(shell: Shell, args: argparse.Namespace) -> str:
    return _rows(shell.monsters())


def ls_bigmon(shell: Shell, args: argparse.Namespace) -> str:
    targets = {t.em_id & EM_ID_SPECIES for t in shell.game.quest.targets if t.count}
    return _rows({k: m for k, m in shell.monsters().items() if m.species in targets})


FIELDS: dict[str, Callable[[Monster], object]] = {
    "hp": lambda m: f"{m.hp}/{m.max_hp}",
    "size": lambda m: f"{m.size_scale:.3f}",
    "pos": lambda m: vec(m.position),
    "species": lambda m: f"0x{m.species:02X} {m.name}",
    "id": lambda m: f"0x{m.id:02X}",
    "state": lambda m: f"(main {m.main_state}, sub {m.sub_state})",
    "speed": lambda m: "/".join(map(str, m.anim_speed)),
}


def get_mon(shell: Shell, args: argparse.Namespace) -> str:
    m = shell.monster(args.slot)
    names = [args.field] if args.field else list(FIELDS)
    return "\n".join(f"{n} = {FIELDS[n](m)}" for n in names)


def set_mon(shell: Shell, args: argparse.Namespace) -> str:
    m = shell.monster(args.slot)
    want = 3 if args.field == "pos" else 1
    if len(args.values) != want:
        raise CommandError(f"{args.field} takes {want} value(s)")
    if args.field == "hp":
        m.hp = int(args.values[0])
        return f"mon {args.slot} hp -> {m.hp}"
    if args.field == "size":
        v = args.values[0]
        # SIZE_SCALE alone is re-derived every frame; the framework's size setter writes all three
        m.size_scale, m.render_scale, m.size_radius = v, (v, v, v), v
        return f"mon {args.slot} size -> {v:.3f}"
    x, y, z = args.values
    m.position = (x, y, z)
    return f"mon {args.slot} pos -> {vec(args.values)}, one-shot"


def aggro(shell: Shell, args: argparse.Namespace) -> str:
    m = shell.monster(args.slot)
    species = shell.game.species(m.species)
    sight = shell.sight_radii
    if args.on == "off":
        # ENGAGE alone is rewritten every frame; a zero sight radius stops the next notice
        sight.setdefault(m.species, species.sight_radius)
        species.sight_radius = 0.0
        m.engage = 0.0
        return f"mon {args.slot} calm: sight radius 0 for every {m.name}"
    if m.species in sight:
        species.sight_radius = sight.pop(m.species)
    m.engage = 1.0
    return f"mon {args.slot} engaged, {m.name} sight radius {species.sight_radius:g}"


# --- savestates ---


def save(shell: Shell, args: argparse.Namespace) -> str:
    try:
        shell.session.client.save_state(args.path)
    except Unsupported as e:
        raise CommandError("this PPSSPP has no savestate commands; the modkit's build does") from e
    return f"saved {args.path}"


def load(shell: Shell, args: argparse.Namespace) -> str:
    try:
        shell.session.client.load_state(args.path)
    except Unsupported as e:
        raise CommandError("this PPSSPP has no savestate commands; the modkit's build does") from e
    return f"loaded {args.path}; a framework plugin that was running is stopped until a cold boot"
