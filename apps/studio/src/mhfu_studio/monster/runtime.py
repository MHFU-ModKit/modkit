# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A port's hit tables as the game takes them: `plan` turns the manifest and the host species'
intel into byte writes, and two routes apply them as given.

The plan owns where every record goes and what is refused: the hurtboxes over the set the host
species walks, then a sentinel, at most that set's record count; the grid over the species'
state blocks; each hitbox set over the host's set of that index, at most its count; each attack
record's named levers, one byte each. Every write carries guards, bytes the game must already
hold (the pointer that leads to the table, the host set's own sentinel) that no write changes:
another or a relocated overlay fails them, and then nothing is written.

The routes: `<name>_hit.lua` on the memory stick, which the framework's `mhfu_port.lua` applies
once the port is live in-area and re-applies after a reload (`export`, `ship`), and `push`,
which writes through the debugger and reads back. The module is regenerated, never edited, and
carries a content id the log names.
"""

from __future__ import annotations

import hashlib
import shutil
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from mhfu import addresses as a
from mhfu import hitzone
from mhfu.em.intel import SpeciesIntel
from mhfu_port.manifest import Hitbox, Hurtbox, Manifest, ManifestError

from mhfu_studio.shell import places

LIBRARY = "mhfu_port.lua"
"""The library the module requires; a stale copy on the memory stick does not know the plan,
so `deploy` keeps it in step."""
LIB_SUBDIR = "lib"
"""Where `require` finds it, under the mods directory."""
SHAPE_ID = {"sphere": 0, "capsule": 1}
LEVERS = {
    "power": a.ATTACK_RECORD.POWER,
    "element": a.ATTACK_RECORD.ELEMENT,
    "volume": a.ATTACK_RECORD.VOLUME_SET,
}
"""The attack record bytes a manifest may set."""
WORD = 4
"""Bytes per group in the module's hex."""


def library() -> Path:
    """The checkout's `framework/lua/lib/mhfu_port.lua`."""
    path = Path(__file__).resolve().parents[5] / "framework" / "lua" / "lib" / LIBRARY
    if not path.is_file():
        raise FileNotFoundError(f"no {LIBRARY} beside this studio: pass --library")
    return path


def _pack(h: Hurtbox | Hitbox, row: int, part: int) -> bytes:
    """A sphere ships zeros for its far end."""
    off = h.offset or [0.0, 0.0, 0.0]
    far = (h.to or [0.0, 0.0, 0.0]) if h.is_capsule else [0.0, 0.0, 0.0]
    shape = SHAPE_ID[h.shape]
    return hitzone.pack(h.bone, shape, row, part, h.flags, h.radius, _v3(off), _v3(far))


def _v3(v: list[float]) -> tuple[float, float, float]:
    return float(v[0]), float(v[1]), float(v[2])


def volume(h: Hurtbox) -> bytes:
    """A hurtbox's record."""
    return _pack(h, h.hitzone_row or 0, h.part or 0)


def attack_volume(h: Hitbox) -> bytes:
    """An attack volume's record: row and part zero."""
    return _pack(h, 0, 0)


def _u32(v: int) -> bytes:
    return struct.pack("<I", v)


@dataclass(frozen=True)
class AttackTables:
    """Where the host overlay keeps its attack data."""

    handle_va: int
    """The overlay's word that holds `records_va`."""
    records_va: int
    n_records: int
    volumes_va: int
    """The set-pointer table, one u32 per set."""
    sets: Mapping[int, tuple[int, int]] = field(default_factory=dict)
    """Set -> (its address, its record count: what fits in place)."""

    @property
    def n_sets(self) -> int:
        return len(self.sets)


@dataclass(frozen=True)
class Host:
    """Where the host species keeps its hit data: static addresses from its intel."""

    species: int
    hurtboxes: int | None = None
    """The set the species walks."""
    capacity: int | None = None
    grid_table: int | None = None
    grids: tuple[int, ...] = ()
    attacks: AttackTables | None = None

    @property
    def row(self) -> int:
        return int(a.SPECIES_TABLE + self.species * a.SPECIES.stride)

    @property
    def label(self) -> str:
        return f"host em{self.species:02d}"


def host(intel: SpeciesIntel | None) -> Host | None:
    """What `plan` needs from `intel`; None without intel."""
    if intel is None:
        return None
    pt, tables = intel.parts, None
    t = intel.attacks.primary if intel.attacks.present else None
    if t is not None and t.volume_table_va is not None:
        sets = {s.index: (s.va, s.capacity) for s in t.sets}
        tables = AttackTables(t.handle_va, t.records_va, len(t.attacks), t.volume_table_va, sets)
    return Host(
        intel.host_species,
        pt.active_set_va if pt.present else None,
        pt.capacity if pt.present else None,
        pt.state_table if pt.has_grid else None,
        tuple(s.va for s in pt.states),
        tables,
    )


@dataclass(frozen=True)
class Write:
    """`data` at `at`, once every guard reads back as given; no write changes a guard."""

    what: str
    at: int
    pieces: tuple[tuple[bytes, str], ...]
    """The bytes in order, each with a label for the module."""
    guards: tuple[tuple[int, bytes], ...] = ()

    @property
    def data(self) -> bytes:
        return b"".join(p for p, _ in self.pieces)


@dataclass(frozen=True)
class Plan:
    writes: tuple[Write, ...]
    notes: tuple[str, ...] = ()
    """What the host has no room for, left out."""

    @property
    def size(self) -> int:
        return sum(len(w.data) for w in self.writes)


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
        h.update(volume(v))
    for st in m.hitzones:
        for row in st.rows:
            h.update(bytes(x & 0xFF for x in row))
    for s, vols in sets_of(m).items():
        h.update(b"set%d:" % s)
        for hb in vols:
            h.update(attack_volume(hb))
    for at in m.attacks:
        h.update(f"atk{at.id}:{at.power},{at.element},{at.volume};".encode())
    return h.hexdigest()[:8]


def has_tables(m: Manifest) -> bool:
    """`m` authors something the plan writes."""
    return bool(m.hurtboxes or m.hitzones or m.hitboxes or m.attacks)


def module_name(m: Manifest) -> str:
    return f"{m.port.name}_hit.lua"


def source_of(m: Manifest) -> str:
    """The manifest as the module's header names it."""
    return f"ports/{m.path.name if m.path is not None else m.port.name + '.toml'}"


def check(m: Manifest, host: Host | None, source: str = "") -> None:
    """Raises `ManifestError` with why `m` cannot be written over `host`."""
    source = source or source_of(m)
    name = f"host em{m.port.host_species:02d}"
    if not has_tables(m):
        raise ManifestError(f"{source} authors no hurtbox, hitzone, hitbox or attack to ship")
    t = host.attacks if host is not None else None
    if (m.hitboxes or m.attacks) and t is None:
        raise ManifestError(
            f"{source} authors hitboxes or attacks but the host's attack table addresses are "
            f"unknown: build the {name} intel so the runtime knows where to write"
        )
    if m.hurtboxes and (host is None or host.hurtboxes is None or host.capacity is None):
        raise ManifestError(
            f"{source} authors hurtboxes but the set {name} walks is unknown: build its intel"
        )
    if m.hitzones and (host is None or host.grid_table is None or not host.grids):
        raise ManifestError(f"{source} authors a hitzone grid but {name} has none known")
    if t is None:
        return
    for h in m.hitboxes:
        if h.set not in t.sets:
            raise ManifestError(
                f"{source}: hitbox set {h.set}, {name} has {t.n_sets} volume set(s) "
                f"(0..{t.n_sets - 1})"
            )
    for at in m.attacks:
        if not 0 <= at.id < t.n_records:
            raise ManifestError(f"{source}: attack id {at.id}, {name} has {t.n_records} record(s)")
        if at.volume is not None and at.volume not in t.sets:
            raise ManifestError(
                f"{source}: attack id {at.id} volume {at.volume}, {name} has {t.n_sets} set(s)"
            )


def _set(
    what: str, at: int, rows: list[tuple[bytes, str]], cap: int, pointer: int, notes: list[str]
) -> Write:
    """A volume set written in place: at most `cap` records, then a sentinel; guarded by the
    pointer that leads to it and the host set's own sentinel, which stays where it is."""
    if len(rows) > cap:
        notes.append(
            f"{what}: {len(rows)} volume(s), the host's set holds {cap}: the rest left out"
        )
    pieces = (*rows[:cap], (hitzone.SENTINEL, "end of set"))
    guards = ((pointer, _u32(at)), (at + cap * hitzone.STRIDE, hitzone.SENTINEL[:2]))
    return Write(what, at, pieces, guards)


def _hit_label(h: Hitbox) -> str:
    return h.label or ("node-space" if h.is_node_space else "marker" if h.is_marker else "")


def plan(m: Manifest, host: Host | None, source: str = "") -> Plan:
    """Every write `m` makes over `host`, in game order; `ManifestError` when it cannot."""
    check(m, host, source)
    assert host is not None  # check refuses a table without its host
    writes: list[Write] = []
    notes: list[str] = []
    if m.hurtboxes:
        assert host.hurtboxes is not None and host.capacity is not None
        rows = [(volume(h), h.label or ("marker" if h.is_marker else "")) for h in m.hurtboxes]
        pointer = host.row + a.SPECIES.HURTBOX_SET
        writes.append(_set("hurtboxes", host.hurtboxes, rows, host.capacity, pointer, notes))
    if m.hitzones:
        assert host.grid_table is not None
        n = min(len(m.hitzones), len(host.grids))
        if len(m.hitzones) != len(host.grids):
            notes.append(
                f"grid: {len(m.hitzones)} state(s), {host.label} has {len(host.grids)}: {n} written"
            )
        table = ((host.row + a.SPECIES.HITZONE_STATES, _u32(host.grid_table)),)
        for s, st in enumerate(m.hitzones[:n]):
            pieces = tuple((bytes(row), st.name if r == 0 else "") for r, row in enumerate(st.rows))
            entry = (host.grid_table + 4 * s, _u32(host.grids[s]))
            writes.append(Write(f"grid state {s}", host.grids[s], pieces, (*table, entry)))
    t = host.attacks
    if t is not None:
        for s, vols in sets_of(m).items():
            va, cap = t.sets[s]
            rows = [(attack_volume(h), _hit_label(h)) for h in vols]
            writes.append(_set(f"attack set {s}", va, rows, cap, t.volumes_va + 4 * s, notes))
        handle = ((t.handle_va, _u32(t.records_va)),)
        for at in m.attacks:
            record = t.records_va + at.id * a.ATTACK_RECORD.step
            for lever, off in LEVERS.items():
                v = getattr(at, lever)
                if v is not None:
                    piece = ((bytes([v]), at.label),)
                    writes.append(Write(f"attack {at.id} {lever}", record + off, piece, handle))
    return Plan(tuple(writes), tuple(notes))


# --- the module ---


def _hex(b: bytes) -> str:
    return " ".join(b[i : i + WORD].hex().upper() for i in range(0, len(b), WORD))


def _lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def _note(label: str) -> str:
    return f"  -- {' '.join(label.split())}" if label.strip() else ""


def lua_hit_module(m: Manifest, host: Host | None, source: str = "") -> str:
    """The module's text: `plan` as data for `P.hit()`."""
    name = m.port.name
    source = source or source_of(m)
    p = plan(m, host, source)
    mod = f"{name}_hit"
    out = [
        f"-- {mod}.lua, GENERATED by `studio port hit` from {source}",
        f"-- ({len(m.hurtboxes)} volume(s), {len(m.hitzones)} grid state(s), {len(sets_of(m))} "
        f"attack set(s), {len(m.attacks)} attack record(s), id {content_id(m)}). Do not edit: "
        "re-export.",
        f"-- The studio computed every write from the host's tables (em{m.port.host_species:02d})."
        " mhfu_port.lua's",
        "-- P.hit() puts each `data` at `at` once every `guard` reads back as given, and writes",
        "-- nothing while one does not (another overlay, or a relocated one).",
        *(f"-- {n}" for n in p.notes),
        "",
        'local port = require("mhfu_port")',
        "",
        f'port.mod("{mod}", function(P)',
        f'  P.hit("{name}", {{',
        f"    species = {m.port.host_species},",
        f'    id = "{content_id(m)}",',
    ]
    if p.notes:
        out.append(f"    notes = {{ {', '.join(_lua_str(n) for n in p.notes)} }},")
    out.append("    writes = {")
    for w in p.writes:
        guard = ", ".join(f'{{ 0x{at:08X}, "{_hex(b)}" }}' for at, b in w.guards)
        out.append(f"      {{ what = {_lua_str(w.what)}, at = 0x{w.at:08X}, guard = {{ {guard} }},")
        out.append("        data = {")
        out += [f'          "{_hex(b)}",{_note(label)}' for b, label in w.pieces]
        out.append("        } },")
    return "\n".join([*out, "    },", "  })", "end)", ""])


def export(m: Manifest, out: Path | None, host: Host | None, source: str = "") -> Path:
    """Write the module to `out`, by default `module_name` here."""
    path = out or Path(module_name(m))
    text = lua_hit_module(m, host, source)
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
    host: Host | None,
    *,
    mods_dir: Path | None = None,
    library_path: Path | None = None,
    source: str = "",
) -> Deployment:
    """`m` as it is now, exported to `out` and deployed: the one way a module reaches the
    memory stick."""
    return deploy(export(m, out, host, source), mods_dir, library_path)
