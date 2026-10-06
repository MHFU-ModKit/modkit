# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A big monster's clips played one executor entry at a time, read back per body part.

The bridge (`cli_bridge.lua`) holds an entry; the engine plays it on the monster's next
dispatch. Each body part's clip block (`structs.ClipBlock`) then names the clip (NODE, inside the
animation pack at ENTITY.ACTION_TABLE) and its length (END), and the pack's slot tables, read
from RAM, say which stream and slot NODE is.

    with Session.launch() as s:
        played = play(s, 100)        # the first big monster
        played.parts[0].held         # ((1, 0),): entry 100 reads stream 1

`deploy` and `ride` set a port up for it: the bridge, the runtime and a rider mod on the lane's
stick, and a cold boot into the quest where the port replaces a monster.
"""

from __future__ import annotations

import csv
import struct
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .. import addresses as a
from .. import files, inject
from ..memory import Memory
from ..structs import (
    ACTION_INPUT_STEP,
    STREAM_SLOTS,
    BigMonster,
    ClipBlock,
    entry_clip,
    input_action,
)
from .quests import Log
from .rig import Rig, big_monsters
from .session import Launcher, Session
from .shell_anim import Bridge, Op

EMPTY = 0xFFFF_FFFF
"""A slot table's empty slot."""
TIGREX = 0x4B
GIADROME = 0x4D
SETTLE = 0.15
"""Seconds between the dispatch and the read: a debugger read can land inside the frame that
sets the blocks up."""
WATCH = 0.4
"""Seconds between the two reads that tell a playing clip from a stopped one."""
CHECKOUT = Path(__file__).resolve().parents[5]
LUA = CHECKOUT / "framework" / "lua"
BRIDGE_LUA = LUA / "tools" / "cli_bridge.lua"
RUNTIME_LUA = LUA / "lib" / "mhfu_port.lua"
LIB = "lib"

_WORD = struct.Struct("<I")


@dataclass(frozen=True)
class Pack:
    """An animation pack's slot tables as the engine holds them: per stream, each slot's clip
    offset from `base`, or EMPTY."""

    base: int
    streams: tuple[tuple[int, ...], ...]

    @classmethod
    def read(cls, mem: Memory, base: int) -> Pack:
        """The header is (slot count, table offset) per stream, stream 0's table right after."""
        first = mem.u32(base + 4)
        count = first // 8 - 1
        if not 0 < count <= 16 or first % 8:
            raise ValueError(f"no animation pack at 0x{base:08X}")
        header = struct.unpack(f"<{2 * count}I", mem.read(base, 8 * count))
        streams = []
        for s in range(count):
            slots, at = header[2 * s : 2 * s + 2]
            streams.append(struct.unpack(f"<{slots}I", mem.read(base + at, 4 * slots)))
        return cls(base, tuple(streams))

    def holding(self, node: int) -> tuple[tuple[int, int], ...]:
        """Every (stream, slot) whose clip is `node`."""
        off = node - self.base
        return tuple(
            (s, i) for s, slots in enumerate(self.streams) for i, o in enumerate(slots) if o == off
        )

    def filled(self, stream: int, slot: int) -> bool:
        return (
            stream < len(self.streams)
            and slot < len(self.streams[stream])
            and (self.streams[stream][slot] != EMPTY)
        )

    def entries(self) -> list[int]:
        """The executor entries some body part has a clip for; an entry past a part's
        ACTION_INPUT_STEP would read the next part's streams."""
        parts = len(self.streams) * STREAM_SLOTS // ACTION_INPUT_STEP
        return [
            e
            for e in range(ACTION_INPUT_STEP)
            if any(self.filled(*entry_clip(e, k)) for k in range(parts))
        ]


@dataclass(frozen=True)
class Part:
    """What one body part plays after a dispatch."""

    part: int
    taken: bool
    """Its ENTITY.ANIM_INPUT reads the entry: the executor dispatched it to this part."""
    asked: tuple[int, int]
    """(stream, slot) the resolver names for the entry (`structs.entry_clip`)."""
    held: tuple[tuple[int, int], ...]
    """Every (stream, slot) holding the clip it plays."""
    empty: bool
    """The pack has no clip at `asked`."""
    end: float
    speed: float
    phase: float
    moved: bool
    """The cursor moved between two reads, or stands at the end."""

    @property
    def resolved(self) -> bool:
        """It plays the clip the resolver names."""
        return self.asked in self.held


@dataclass(frozen=True)
class Played:
    entry: int
    parts: tuple[Part, ...]
    dispatched: bool
    """The executor took the entry on some part."""
    kicked: bool
    """The current action was restarted to get a dispatch."""
    waited: float


@dataclass(frozen=True)
class Expect:
    """What a built port holds in an entry, from `mhfu-port slots`: the streams with a clip,
    and the frames of its longest part (every part's, on a port with its own skeleton)."""

    streams: tuple[int, ...]
    frames: int | None


def read_expect(path: Path) -> dict[int, Expect]:
    """`mhfu-port slots <pac> -o FILE`: entry -> Expect, for the entries the port fills."""
    out = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            streams = tuple(int(s) for s in row["port_streams"].split())
            if streams:
                frames = int(row["port_frames"]) if row["port_frames"] else None
                out[int(row["slot"])] = Expect(streams, frames)
    return out


def verdict(played: Played, expect: Expect | None) -> list[str]:
    """What is wrong with a played entry, against the port's build; empty when it is right."""
    bad = []
    if not played.dispatched:
        bad.append("no dispatch")
    for p in played.parts:
        if expect is not None and p.asked[0] not in expect.streams:
            continue
        if not p.taken:
            bad.append(f"part {p.part} was not dispatched")
        elif p.empty:
            bad.append(f"part {p.part} keeps {_places(p.held)}: {_place(p.asked)} is empty")
        elif not p.resolved:
            bad.append(f"part {p.part} plays {_places(p.held)}, not {_place(p.asked)}")
        if expect is not None and expect.frames is not None and p.end != expect.frames:
            bad.append(f"part {p.part} is {p.end:g} frames, the build {expect.frames}")
        if not p.moved:
            bad.append(f"part {p.part} stands at {p.phase:g}")
    return bad


def _place(where: tuple[int, int]) -> str:
    return f"stream {where[0]} slot {where[1]}"


def _places(held: tuple[tuple[int, int], ...]) -> str:
    return " / ".join(map(_place, held)) or "a clip outside the pack"


def monster(s: Session, slot: int | None = None) -> tuple[int, BigMonster]:
    """(registry slot, monster): `slot`'s, else the first big monster's."""
    found = {m.base: m for m in big_monsters(s)}
    for k, base in enumerate(s.game.registry):
        if k and base in found and slot in (None, k):
            return k, found[base]
    where = f"slot {slot}" if slot is not None else "the entity registry"
    raise LookupError(f"no big monster in {where}")


def bridge(s: Session) -> Bridge:
    b = Bridge(s.mem)
    if not b.reachable():
        raise ConnectionError(
            "bridge not reachable: extra RAM is unmapped; it needs the plugin's memory=64 and "
            "cli_bridge.lua, from a cold boot"
        )
    return b


def _taken(m: BigMonster, entry: int) -> list[bool]:
    """Per part, whether its input reads `entry`."""
    return [input_action(v, k) == entry for k, v in enumerate(m.anim_input)]


def play(
    s: Session,
    entry: int,
    *,
    slot: int | None = None,
    link: Bridge | None = None,
    pack: Pack | None = None,
    kick_after: float = 2.0,
    timeout: float = 6.0,
) -> Played:
    """Hold `entry` on a big monster (`slot`'s, else the first) and read every part once the
    executor took it. Without a dispatch in `kick_after` s the current action restarts (its
    phase cursor zeroed, once), which dispatches again. The hold stays: `release` ends it.

    An entry the inputs already read restarts at once, and its dispatch shows as a part's
    cursor going back; on an empty entry nothing shows it."""
    k, m = monster(s, slot)
    link = link or bridge(s)
    blocks = [ClipBlock.of(s.mem, m.base, p) for p in range(a.ENTITY.CLIP_BLOCKS.count)]
    stale = any(_taken(m, entry))
    if not link.request(s, Op.FORCE_ACTION, k, entry)[1]:
        raise TimeoutError("no ack: the game is paused or cli_bridge.lua is not loaded")
    pack = pack or Pack.read(s.mem, m.action_table)
    before = [b.phase for b in blocks]

    def dispatched() -> bool:
        taken = _taken(m, entry)
        if not stale:
            return any(taken)
        return any(t and b.phase < was for t, b, was in zip(taken, blocks, before, strict=False))

    start, kicked = s.now(), False
    while not (done := dispatched()) and s.now() - start < timeout:
        if not kicked and (stale or s.now() - start >= kick_after):
            s.mem.write(m.base + a.ENTITY.PHASE, bytes(3))
            kicked = True
        s.sleep(0.1)
    waited = s.now() - start
    s.sleep(SETTLE)
    taken = _taken(m, entry)
    blocks = [b for b in blocks if b.node]
    phases = [b.phase for b in blocks]
    s.sleep(WATCH)
    parts = []
    for p, b in enumerate(blocks):
        end, phase, node = b.end, b.phase, b.node
        parts.append(
            Part(
                p,
                taken[p],
                entry_clip(entry, p),
                pack.holding(node),
                not pack.filled(*entry_clip(entry, p)),
                end,
                b.speed,
                phase,
                phase != phases[p] or phase >= end,
            )
        )
    return Played(entry, tuple(parts), done, kicked, waited)


def release(s: Session, slot: int | None = None, link: Bridge | None = None) -> bool:
    """End a hold; False without an ack."""
    k, _ = monster(s, slot)
    return (link or bridge(s)).request(s, Op.CLEAR, k)[1]


def sweep(
    s: Session,
    entries: Iterable[int],
    *,
    slot: int | None = None,
    kick_after: float = 2.0,
    timeout: float = 6.0,
) -> Iterator[Played]:
    """`play` each entry in turn; the hold ends when the sweep does."""
    _, m = monster(s, slot)
    link = bridge(s)
    pack = Pack.read(s.mem, m.action_table)
    try:
        for e in entries:
            yield play(
                s, e, slot=slot, link=link, pack=pack, kick_after=kick_after, timeout=timeout
            )
    finally:
        release(s, slot, link)


# --- a port in the lane's game ---


def rider(name: str, pac: str, host: int = TIGREX, replace: Iterable[int] = (GIADROME,)) -> str:
    """A mod that injects port `name` (its PAC `pac`) on `host` in place of `replace`, with no
    brain."""
    frame = files.monster_pac(host)
    victims = ", ".join(map(str, replace))
    return "\n".join(
        [
            f"-- {name}_rig.lua, GENERATED by mhfu.live.clips: the {name} port on em {host} in",
            f"-- place of em {victims}, with no brain; cli_bridge.lua plays its clips.",
            'local port = require("mhfu_port")',
            f'port.mod("{name}_rig", function(P)',
            f'  P.define{{ name = "{name}", species = {host}, replace = {{ {victims} }},',
            f'            pac = "{pac}", orig = "{inject.orig_filename(frame)}",',
            f"            fid = {files.engine_id(frame)} }}",
            "end)",
            "",
        ]
    )


def deploy(
    name: str,
    pac: str | None = None,
    host: int = TIGREX,
    replace: Iterable[int] = (GIADROME,),
    stick: Path | None = None,
) -> list[Path]:
    """Put the bridge, the port runtime and a rider for port `name` into the stick's mods (the
    lane's with MHFU_LANE); the port itself is `mhfu-port inject`'s. Returns what it wrote."""
    pac = pac or f"{name}.bin"
    root = inject.memstick(stick)
    mods = root / inject.MODS_SUBDIR
    built = root / inject.INJECT_SUBDIR / pac
    clips = mods / LIB / f"{name}_clips.lua"
    for need in (built, clips):
        if not need.is_file():
            raise FileNotFoundError(f"no {need}: run `mhfu-port inject ports/{name}.toml` first")
    out = {
        mods / BRIDGE_LUA.name: BRIDGE_LUA.read_text(encoding="utf-8"),
        mods / LIB / RUNTIME_LUA.name: RUNTIME_LUA.read_text(encoding="utf-8"),
        mods / f"{name}_rig.lua": rider(name, pac, host, replace),
    }
    for path, text in out.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return list(out)


def ride(
    name: str,
    *,
    pac: str | None = None,
    host: int = TIGREX,
    replace: Iterable[int] = (GIADROME,),
    quest: str = "Giadrome",
    rank: int = 1,
    launcher: Launcher | None = None,
    log: Log | None = None,
) -> Rig:
    """A cold boot into `quest` at `rank` with port `name` deployed (`deploy`), once its monster
    and the bridge are up; the emulator keeps running when the rig closes."""
    deploy(name, pac, host, replace)
    rig = Rig.open(launcher, quest=quest, rank=rank, log=log)
    try:
        s = rig.s
        s.wait(lambda: big_monsters(s), 30.0, "the port's monster")
        bridge(s)
    except BaseException:
        rig.close()
        raise
    return rig
