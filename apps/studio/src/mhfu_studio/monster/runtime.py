# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A port's hit tables as the runtime eats them: `<name>_hit.lua`, one `P.hit()` call that the
framework's `mhfu_port.lua` writes into the running game.

Hurtboxes go in place over the host species' own set, then a sentinel, so at most the set's
record count fits; the grid over the species' state blocks. Each hitbox set goes in place over
the host's set of that index, through the overlay's set-pointer table, and each attack record
gets only the levers it names. The runtime refuses a set whose live count is not the exported
`cap`. The module is regenerated, never edited, and carries a content id the log names.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from mhfu import hitzone
from mhfu.em.intel import SpeciesIntel
from mhfu_port.manifest import ROWS, Hitbox, Hurtbox, Manifest, ManifestError

from mhfu_studio.shell import places

LIBRARY = "mhfu_port.lua"
"""The library the module requires; a stale copy on the memory stick drops fields it does not
know, so `deploy` keeps it in step."""
LIB_SUBDIR = "lib"
"""Where `require` finds it, under the mods directory."""
SHAPE_ID = {"sphere": 0, "capsule": 1}


def library() -> Path:
    """The checkout's `framework/lua/lib/mhfu_port.lua`."""
    path = Path(__file__).resolve().parents[5] / "framework" / "lua" / "lib" / LIBRARY
    if not path.is_file():
        raise FileNotFoundError(f"no {LIBRARY} beside this studio: pass --library")
    return path


def _f(v: float) -> str:
    """A Lua float literal that reads like a number."""
    s = repr(float(v))
    return s if "." in s or "e" in s else s + ".0"


def _row(h: Hurtbox | Hitbox, row: int, part: int) -> list[str]:
    """The twelve literals of one record, in record order: bone, shape, row, part, flags, radius,
    ax, ay, az, bx, by, bz. A sphere ships zeros for its far end."""
    a = h.offset or [0.0, 0.0, 0.0]
    b = (h.to or [0.0, 0.0, 0.0]) if h.is_capsule else [0.0, 0.0, 0.0]
    head = [str(h.bone), str(SHAPE_ID[h.shape]), str(row), str(part), f"0x{h.flags:X}"]
    return head + [_f(v) for v in (h.radius, *a, *b)]


def volume_row(h: Hurtbox) -> list[str]:
    return _row(h, h.hitzone_row or 0, h.part or 0)


def attack_volume_row(h: Hitbox) -> list[str]:
    """The hurtbox's literals with row and part zero, so the runtime has one record writer."""
    return _row(h, 0, 0)


@dataclass(frozen=True)
class AttackTables:
    """Where the host overlay keeps its attack data: static addresses from the intel."""

    volumes_va: int
    """The set-pointer table, one u32 per set."""
    records_va: int
    n_sets: int
    n_records: int
    capacities: dict[int, int] = field(default_factory=dict)
    """Set -> the host's record count: what fits in place."""


def host_capacity(intel: SpeciesIntel | None) -> int | None:
    """The host's hurtbox set record count."""
    return intel.parts.capacity if intel is not None and intel.parts.present else None


def host_attack_tables(intel: SpeciesIntel | None) -> AttackTables | None:
    if intel is None or not intel.attacks.present:
        return None
    t = intel.attacks.primary
    if t is None or t.volume_table_va is None:
        return None
    caps = {s.index: s.capacity for s in t.sets}
    return AttackTables(t.volume_table_va, t.records_va, len(t.sets), len(t.attacks), caps)


def sets_of(m: Manifest) -> dict[int, list[Hitbox]]:
    """The hitboxes by set, in file order within a set."""
    out: dict[int, list[Hitbox]] = {}
    for h in m.hitboxes:
        out.setdefault(h.set, []).append(h)
    return dict(sorted(out.items()))


def content_id(m: Manifest) -> str:
    """Eight hex digits over the tables."""
    h = hashlib.sha1()
    for v in m.hurtboxes:
        h.update(",".join(volume_row(v)).encode() + b";")
    for st in m.hitzones:
        for row in st.rows:
            h.update(bytes(x & 0xFF for x in row))
    for s, vols in sets_of(m).items():
        h.update(b"set%d:" % s)
        for hb in vols:
            h.update(",".join(attack_volume_row(hb)).encode() + b";")
    for a in m.attacks:
        h.update(f"atk{a.id}:{a.power},{a.element},{a.volume};".encode())
    return h.hexdigest()[:8]


def has_tables(m: Manifest) -> bool:
    """`m` authors something the module ships."""
    return bool(m.hurtboxes or m.hitzones or m.hitboxes or m.attacks)


def module_name(m: Manifest) -> str:
    return f"{m.port.name}_hit.lua"


def source_of(m: Manifest) -> str:
    """The manifest as the module's header names it."""
    return f"ports/{m.path.name if m.path is not None else m.port.name + '.toml'}"


def check(m: Manifest, attacks: AttackTables | None, source: str = "") -> None:
    """Raises `ManifestError` with why `m` cannot be exported against `attacks`."""
    source = source or source_of(m)
    host = f"host em{m.port.host_species:02d}"
    if not has_tables(m):
        raise ManifestError(f"{source} authors no hurtbox, hitzone, hitbox or attack to ship")
    if (m.hitboxes or m.attacks) and attacks is None:
        raise ManifestError(
            f"{source} authors hitboxes or attacks but the host's attack table addresses are "
            f"unknown: build the {host} intel so the runtime knows where to write"
        )
    if attacks is None:
        return
    for h in m.hitboxes:
        if not 0 <= h.set < attacks.n_sets:
            raise ManifestError(
                f"{source}: hitbox set {h.set}, {host} has {attacks.n_sets} volume set(s) "
                f"(0..{attacks.n_sets - 1})"
            )
    for a in m.attacks:
        if not 0 <= a.id < attacks.n_records:
            raise ManifestError(
                f"{source}: attack id {a.id}, {host} has {attacks.n_records} record(s)"
            )
        if a.volume is not None and not 0 <= a.volume < attacks.n_sets:
            raise ManifestError(
                f"{source}: attack id {a.id} volume {a.volume}, {host} has {attacks.n_sets} set(s)"
            )


def _lua(v: int | None, fmt: str = "{}") -> str:
    return "nil" if v is None else fmt.format(v)


def _note(label: str) -> str:
    return f"  -- {label}" if label else ""


def lua_hit_module(
    m: Manifest,
    capacity: int | None = None,
    source: str = "",
    attacks: AttackTables | None = None,
) -> str:
    """The module's text; `capacity` is the host hurtbox set's count, `attacks` where the host
    keeps its attack tables (required once the port authors a hitbox or an attack)."""
    name = m.port.name
    source = source or source_of(m)
    check(m, attacks, source)
    sets = sets_of(m)
    mod = f"{name}_hit"
    out = [
        f"-- {mod}.lua, GENERATED by `studio port hit` from {source}",
        f"-- ({len(m.hurtboxes)} volume(s), {len(m.hitzones)} grid state(s), {len(sets)} attack "
        f"set(s), {len(m.attacks)} attack record(s), id {content_id(m)}). Do not edit: re-export.",
        "-- mhfu_port.lua's P.hit() writes the volumes in place over the host species' own set,",
        "-- sentinel-terminated, the grid over its state blocks, each attack set in place over the",
        "-- host's set of that index (refused when the live count is not `cap`), and on each",
        "-- attack record only the levers named, once the port is live in-area.",
    ]
    if capacity is not None:
        over = len(m.hurtboxes) - capacity
        more = f"; {over} MORE than fit, the runtime truncates and logs" if over > 0 else ""
        out.append(f"-- Host set capacity: {capacity} record(s){more}.")
    out += [
        "",
        f'local port = require("{Path(LIBRARY).stem}")',
        "",
        f'port.mod("{mod}", function(P)',
        f'  P.hit("{name}", {{',
        f"    species = {m.port.host_species},",
        f'    id = "{content_id(m)}",',
    ]
    if m.hurtboxes:
        out.append(
            "    -- bone, shape(0 sphere/1 capsule), hitzone_row, part, flags, radius, "
            "ax, ay, az, bx, by, bz"
        )
        out.append("    volumes = {")
        for h in m.hurtboxes:
            label = h.label or ("marker" if h.is_marker else "")
            out.append(f"      {{ {', '.join(volume_row(h))} }},{_note(label)}")
        out.append("    },")
    else:
        out.append("    volumes = nil,   -- the host's own set stays as it is")
    if m.hitzones:
        out.append(f"    -- per state: {len(ROWS)} rows x ({' '.join(hitzone.COLUMNS)})")
        out.append("    grid = {")
        for st in m.hitzones:
            out.append(f"      {{  -- {st.name}")
            out += [f"        {{ {', '.join(map(str, row))} }}," for row in st.rows]
            out.append("      },")
        out.append("    },")
    else:
        out.append("    grid = nil,      -- the species grid stays as it is")
    if attacks is not None and (sets or m.attacks):
        out.append(
            f"    -- where host em{m.port.host_species:02d} keeps its attack data (static, the "
            "overlay's own)"
        )
        out.append(
            f"    attack_tables = {{ volumes = 0x{attacks.volumes_va:08X}, records = "
            f"0x{attacks.records_va:08X}, n_sets = {attacks.n_sets}, n_records = "
            f"{attacks.n_records} }},"
        )
    if sets:
        out.append(
            "    -- per set: cap = the host's record count (the in-place limit AND the "
            "fingerprint);"
        )
        out.append(
            "    -- rows: bone, shape(0 sphere/1 capsule), 0, 0, flags, radius, "
            "ax, ay, az, bx, by, bz"
        )
        out.append("    attack_sets = {")
        for s, vols in sets.items():
            cap = attacks.capacities.get(s) if attacks is not None else None
            over = len(vols) - cap if cap is not None else 0
            more = f"  -- {over} MORE than fit; the runtime truncates and logs" if over > 0 else ""
            out.append(f"      [{s}] = {{ cap = {_lua(cap)}, volumes = {{{more}")
            for hb in vols:
                label = hb.label or (
                    "node-space" if hb.is_node_space else "marker" if hb.is_marker else ""
                )
                out.append(f"        {{ {', '.join(attack_volume_row(hb))} }},{_note(label)}")
            out.append("      } },")
        out.append("    },")
    elif attacks is not None and m.attacks:
        out.append("    attack_sets = nil,   -- the host's own sets stay as they are")
    if m.attacks:
        out.append("    -- the levers on a record; nil = the host's byte stands")
        out.append("    attacks = {")
        for a in m.attacks:
            out.append(
                f"      {{ id = {a.id}, power = {_lua(a.power)}, element = "
                f"{_lua(a.element, '0x{:02X}')}, volume = {_lua(a.volume)} }},{_note(a.label)}"
            )
        out.append("    },")
    return "\n".join([*out, "  })", "end)", ""])


def export(
    m: Manifest,
    out: Path | None = None,
    capacity: int | None = None,
    attacks: AttackTables | None = None,
    source: str = "",
) -> Path:
    """Write the module to `out`, by default `module_name` here."""
    path = out or Path(module_name(m))
    text = lua_hit_module(m, capacity, source, attacks)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@dataclass(frozen=True)
class Deployment:
    """What `deploy` copied; `library` None when the memory stick's was already in step."""

    module: Path
    library: Path | None = None

    def describe(self) -> str:
        lib = "" if self.library is None else f" + {LIB_SUBDIR}/{self.library.name} (was stale)"
        return self.module.name + lib


def sync_library(mods_dir: Path, source: Path) -> Path | None:
    """Copy `source` to `mods/lib/` when they differ, and drop a copy left in `mods/` itself,
    where it would run as a mod; the destination when it copied."""
    (mods_dir / LIBRARY).unlink(missing_ok=True)
    dst = mods_dir / LIB_SUBDIR / LIBRARY
    if dst.is_file() and dst.read_bytes() == source.read_bytes():
        return None
    dst.parent.mkdir(exist_ok=True)
    shutil.copyfile(source, dst)
    return dst


def deploy(
    path: Path, mods_dir: Path | None = None, library_path: Path | None = None
) -> Deployment:
    """Copy the module beside the other mods on the memory stick, and the library when the
    memory stick's is behind. A running game hot-reloads both."""
    mods = mods_dir if mods_dir is not None else places.mods_dir()
    if not mods.is_dir():
        raise FileNotFoundError(f"no mods directory at {mods}")
    lib = library_path or library()
    dst = mods / path.name
    shutil.copyfile(path, dst)
    return Deployment(dst, sync_library(mods, lib))


def cache_dir() -> Path:
    """Where the studio exports a module on its way to the memory stick."""
    from mhfu_studio.monster.species import cache_root

    return cache_root() / "hit"


def ship(
    m: Manifest,
    out: Path,
    capacity: int | None = None,
    attacks: AttackTables | None = None,
    *,
    mods_dir: Path | None = None,
    library_path: Path | None = None,
    source: str = "",
) -> Deployment:
    """`m` as it is now, exported to `out` and deployed: the one way a module reaches the game."""
    return deploy(export(m, out, capacity, attacks, source), mods_dir, library_path)
