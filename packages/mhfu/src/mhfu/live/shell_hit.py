"""Shell commands on hit volumes: where a monster can be hit (`hitzone`), where it hits
(`hitbox`). Both read and write the live game.

Hurtboxes and hitzone grids are found from the monster's live species row. Attack tables are
found in its extracted overlay (--data or MHFU_DATA), which the game maps at the address the
file names, so they are read and written live at the file's addresses; a live overlay that
differs there, as a relocated port does, is refused.
"""

from __future__ import annotations

import argparse
import functools
from typing import TYPE_CHECKING

from .. import addresses as a
from .. import files, hitbox, hitzone
from ..files import Extracted
from ..hitbox import Attack, AttackTable
from ..hitzone import COLUMNS, GRID_COLS, GRID_ROWS, HitVolume, HitzoneGrid, Hitzones, VolumeSet
from ..memory import Image, Memory
from ..views import Value
from .shell import CommandError, Monster, integer, table, vec

if TYPE_CHECKING:
    from .shell import Shell

SHAPES = {hitzone.SPHERE: "sphere", hitzone.CAPSULE: "capsule"}
ATTACK_FIELDS = sorted(n for n, v in vars(Attack).items() if isinstance(v, Value) and n != "lead")
"""The fields `hitbox set` writes; LEAD ends a table, so it is left out."""


def snapshot(mem: Memory, va: int, size: int) -> Image:
    """`size` bytes from `va` in one read, up to the end of its RAM partition."""
    end = a.USER_RAM_END if va < a.USER_RAM_END else a.RAM.stop
    return Image(mem.read(va, max(0, min(size, end - va))), va)


def _set(mem: Memory, va: int, what: str) -> VolumeSet:
    if va not in a.RAM:
        raise CommandError(f"{what} is 0x{va:08X}, not a pointer")
    found = hitzone.walk_set(snapshot(mem, va, hitzone.MAX_WALK * hitzone.STRIDE), va)
    if found is None:
        raise CommandError(f"no volume set ends within {hitzone.MAX_WALK} records of 0x{va:08X}")
    return found


def _volumes(s: VolumeSet) -> str:
    rows = [
        [i, v.bone, SHAPES.get(v.shape, v.shape), v.row, v.part, f"{v.radius:.1f}", vec(v.offset)]
        + ([vec(v.far)] if v.capsule else [""])
        for i, v in enumerate(s.volumes)
    ]
    return table(["i", "bone", "shape", "row", "part", "radius", "offset", "far"], rows)


def _setvol(mem: Memory, s: VolumeSet, i: int, bone: int, radius: float) -> str:
    if not 0 <= i < len(s.volumes):
        raise CommandError(f"volume {i} is not 0..{len(s.volumes) - 1}")
    v = HitVolume(mem, s.volumes[i].base)
    v.bone, v.radius = bone, radius
    return f"volume {i} at 0x{v.base:08X}: bone {bone}, radius {radius:g}"


def register(shell: Shell) -> None:
    p = shell.command("hitzone show", "a monster's hurtboxes and hitzone grids", hitzone_show)
    p.add_argument("slot", type=integer)
    p = shell.command("hitzone setvol", "set a hurtbox's bone and radius", hitzone_setvol)
    for name in ("slot", "i", "bone"):
        p.add_argument(name, type=integer)
    p.add_argument("radius", type=float)
    p = shell.command("hitzone setwk", "set one hitzone grid percentage", hitzone_setwk)
    for name in ("slot", "state", "row"):
        p.add_argument(name, type=integer)
    p.add_argument("column", help=f"0..9 or one of {', '.join(COLUMNS)}")
    p.add_argument("percent", type=integer)
    p = shell.command("hitbox show", "a monster's attack table, or one attack", hitbox_show)
    p.add_argument("slot", type=integer)
    p.add_argument("attack", type=integer, nargs="?")
    p = shell.command("hitbox set", "set a field of one attack record", hitbox_set)
    p.add_argument("slot", type=integer)
    p.add_argument("attack", type=integer)
    p.add_argument("field", choices=ATTACK_FIELDS, metavar="field", help=", ".join(ATTACK_FIELDS))
    p.add_argument("value", type=integer)
    p = shell.command("hitbox setvol", "set an attack volume's bone and radius", hitbox_setvol)
    for name in ("slot", "attack", "i", "bone"):
        p.add_argument(name, type=integer)
    p.add_argument("radius", type=float)


# --- hitzone: the hurtboxes and their grids ---


def _hurtboxes(shell: Shell, m: Monster) -> VolumeSet:
    return _set(shell.mem, shell.game.species(m.species).hurtbox_set, "SPECIES.HURTBOX_SET")


def _grids(shell: Shell, m: Monster) -> Hitzones:
    found = hitzone.species_hitzones(shell.session.mem, m.species)
    if found is None:
        raise CommandError(f"species {m.species} has no hitzone grids")
    return found


def hitzone_show(shell: Shell, args: argparse.Namespace) -> str:
    m = shell.monster(args.slot)
    s = _hurtboxes(shell, m)
    out = [f"mon {args.slot} {m.name}: {len(s.volumes)} hurtboxes at 0x{s.va:08X}", _volumes(s)]
    current = m.hitzone_state
    for k, grid in enumerate(_grids(shell, m).states):
        mark = ", current" if k == current else ""
        out.append(f"hitzone state {k} at 0x{grid.base:08X}{mark}:")
        out.append(table(["row", *COLUMNS], [[r, *vals] for r, vals in enumerate(grid.rows)]))
    return "\n".join(out)


def hitzone_setvol(shell: Shell, args: argparse.Namespace) -> str:
    s = _hurtboxes(shell, shell.monster(args.slot))
    return _setvol(shell.mem, s, args.i, args.bone, args.radius)


def hitzone_setwk(shell: Shell, args: argparse.Namespace) -> str:
    states = _grids(shell, shell.monster(args.slot)).states
    col = COLUMNS.index(args.column) if args.column in COLUMNS else integer(args.column)
    if not 0 <= args.state < len(states):
        raise CommandError(f"state {args.state} is not 0..{len(states) - 1}")
    if not (0 <= args.row < GRID_ROWS and 0 <= col < GRID_COLS and 0 <= args.percent <= 0xFF):
        raise CommandError(f"row 0..{GRID_ROWS - 1}, column 0..{GRID_COLS - 1}, percent 0..255")
    grid = states[args.state]
    shell.mem.write_u8(HitzoneGrid.percent.address(grid) + args.row * GRID_COLS + col, args.percent)
    return f"state {args.state} row {args.row} {COLUMNS[col]} -> {args.percent}%"


# --- hitbox: the attack table ---


@functools.cache
def _primary(game: Extracted, species: int) -> tuple[int, AttackTable]:
    try:
        em = species if species in files.EM_SPECIES else hitzone.owner(game, species)
    except ValueError as e:
        raise CommandError(str(e)) from e
    found = hitbox.primary_table(hitbox.tables(game.em(em)))
    if found is None:
        raise CommandError(f"em{em}.ovl passes no attack table")
    return em, found


def _attacks(shell: Shell, m: Monster) -> tuple[int, AttackTable]:
    game = shell.extracted()
    em, t = _primary(game, m.species)
    if t.volume_table is not None:  # set pointers: a relocated overlay changes every one
        size = 4 * len(t.volumes)
        if shell.mem.read(t.volume_table, size) != game.em(em).read(t.volume_table, size):
            raise CommandError(f"the live em{em}.ovl is not where the file maps it (relocated?)")
    return em, t


def _attack(shell: Shell, t: AttackTable, i: int) -> Attack:
    if not 0 <= i < len(t.attacks):
        raise CommandError(f"attack {i} is not 0..{len(t.attacks) - 1}")
    return Attack(shell.mem, t.attacks[i].base, i)


def _attack_set(shell: Shell, t: AttackTable, attack: Attack) -> VolumeSet:
    if t.volume_table is None:
        raise CommandError("this table's volume sets were not found")
    va = shell.mem.u32(t.volume_table + 4 * attack.volume_set)
    return _set(shell.mem, va, f"volume set {attack.volume_set}")


def hitbox_show(shell: Shell, args: argparse.Namespace) -> str:
    m = shell.monster(args.slot)
    em, t = _attacks(shell, m)
    if args.attack is not None:
        attack = _attack(shell, t, args.attack)
        fields = ", ".join(f"{n} {getattr(attack, n)}" for n in ATTACK_FIELDS)
        s = _attack_set(shell, t, attack)
        return f"attack {args.attack} at 0x{attack.base:08X}: {fields}\n{_volumes(s)}"
    image = snapshot(shell.mem, t.records, len(t.attacks) * hitbox.RECORD)
    rows = []
    for i, rec in enumerate(t.attacks):
        r = Attack(image, rec.base, i)
        rows.append([i, r.power, f"0x{r.element:02X}", r.kind, r.volume_set, r.tag, r.value_14])
    head = f"mon {args.slot} {m.name}: {len(rows)} attacks at 0x{t.records:08X} (em{em}.ovl)"
    return head + "\n" + table(["i", "power", "element", "kind", "set", "tag", "value_14"], rows)


def hitbox_set(shell: Shell, args: argparse.Namespace) -> str:
    _, t = _attacks(shell, shell.monster(args.slot))
    attack = _attack(shell, t, args.attack)
    setattr(attack, args.field, args.value)
    return f"attack {args.attack} {args.field} -> {getattr(attack, args.field)}"


def hitbox_setvol(shell: Shell, args: argparse.Namespace) -> str:
    _, t = _attacks(shell, shell.monster(args.slot))
    s = _attack_set(shell, t, _attack(shell, t, args.attack))
    return _setvol(shell.mem, s, args.i, args.bone, args.radius)
