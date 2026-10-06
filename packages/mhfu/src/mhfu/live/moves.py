# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A move the framework's move player runs on a live big monster (`framework/include/mhfu/move.h`),
and what came of it: each body part's clip, each attack's spawn, the hunter's HP writes against
the clip's phase, and the pairs the monster went through after.

    with Rig.attach() as rig:
        r = play(rig.s, Move(46, attacks=((56, 6),)))
        r.parts[0].played, r.spawns[0].at, r.hits

It asks through the debug bridge (`cli_bridge.lua`, CMD 4), so the game needs the framework,
the bridge and a monster whose vtable em_vhook wrapped: a cold boot (`clips.ride`).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import cast

from .. import addresses as a
from ..entries import entry_clip
from ..views import View, f32s, ptr, ptrs, u8, u16, u32, u32s
from .clips import Pack, bridge, monster
from .session import Session
from .shell_anim import Bridge, Op

CMD_MOVE = 4
"""cli_bridge.lua's CMD_MOVE."""
NO_PAIR = 0xFF
DONE = 4
"""MOVE_STATE.STATE once the move is over."""
NEVER = 0xFFFF_FFFF
ENDS = {
    1: "clip done",
    2: "back pair",
    3: "pair changed",
    4: "stopped",
    5: "replaced",
    6: "carrier refused",
    7: "clip lost",
}
SPEED = 2.0
"""Clip frames a forced clip's cursor moves per AI frame (CLIP_BLOCK.SPEED)."""
POLL = 0.2
WRAP = 1 << 32
"""The emulated clock a log line carries is cut to 32 bits."""

Pair = tuple[int, int]


@dataclass(frozen=True)
class Move:
    """What `mhfu_move_t` holds: an entry, its attacks as (clip frame, attack id), the carrier
    pair it rides, the pair entered at the end (None: the carrier hands off itself). The
    default carrier (0,2) is em75's alert hub: one dispatch, then the brain once the clip ends;
    (0,1) is not entered as itself when ENTITY+0x4B9 is set (the translator makes it (0,2))."""

    entry: int
    attacks: tuple[tuple[int, int], ...] = ()
    carrier: Pair = (0, 2)
    back: tuple[int, int, int] | None = None
    length: int = 0
    skip: bool = False
    part: int = 0
    spawner: int = 0

    def pack(self) -> bytes:
        """struct MOVE."""
        f = a.MOVE
        most = a.MOVE.ATTACKS.count or 0
        if len(self.attacks) > most:
            raise ValueError(f"at most {most} attacks")
        assert a.MOVE.size and a.MOVE_ATTACK.size
        out = bytearray(a.MOVE.size)
        back = self.back or (NO_PAIR, 0, 0)
        struct.pack_into("<HH", out, f.ENTRY, self.entry, self.length)
        struct.pack_into("<BBBBB", out, f.CARRIER_MAIN, *self.carrier, *back)
        struct.pack_into("<BBB", out, f.SKIP, int(self.skip), self.part, len(self.attacks))
        for i, (frame, id_) in enumerate(self.attacks):
            at = f.ATTACKS + i * a.MOVE_ATTACK.size
            struct.pack_into("<HH", out, at + a.MOVE_ATTACK.FRAME, frame, id_)
        struct.pack_into("<I", out, f.SPAWNER, self.spawner)
        return bytes(out)


class MoveState(View):
    """The move player's block (struct MOVE_STATE)."""

    struct = a.MOVE_STATE
    magic = u32(a.MOVE_STATE.MAGIC)
    started = u32(a.MOVE_STATE.STARTED)
    pending = u32(a.MOVE_STATE.PENDING)
    state = u8(a.MOVE_STATE.STATE)
    end = u8(a.MOVE_STATE.END)
    entity = ptr(a.MOVE_STATE.ENTITY)
    frames = u32(a.MOVE_STATE.FRAMES)
    skipped = u32(a.MOVE_STATE.SKIPPED)
    end_pair = u16(a.MOVE_STATE.END_PAIR)
    end_frame = u32(a.MOVE_STATE.END_FRAME)
    node = ptrs(a.MOVE_STATE.NODE)
    clip_end = f32s(a.MOVE_STATE.CLIP_END)
    peak = f32s(a.MOVE_STATE.PEAK)
    spawn_frame = u32s(a.MOVE_STATE.SPAWN_FRAME)
    spawn_cursor = f32s(a.MOVE_STATE.SPAWN_CURSOR)
    spawn_node = ptrs(a.MOVE_STATE.SPAWN_NODE)


@dataclass(frozen=True)
class Part:
    part: int
    asked: tuple[int, int]
    """(stream, slot) the entry names for this part."""
    held: tuple[tuple[int, int], ...]
    """Every (stream, slot) holding the clip the dispatch put in."""
    end: float
    peak: float

    @property
    def played(self) -> bool:
        """It played the entry's clip to its last frame."""
        return self.asked in self.held and self.peak >= self.end - SPEED


@dataclass(frozen=True)
class Spawn:
    id: int
    frame: int
    """The clip frame it was asked for."""
    at: int | None
    """AI frame after the dispatch; None if it never spawned."""
    cursor: float
    node: int
    """The attack node; 0 when the spawner refused (out of section)."""


@dataclass(frozen=True)
class HpWrite:
    """A CPU write to the hunter's HP: what it read just before, the timing part's cursor and
    the move's AI frame (MOVE_STATE.FRAMES) then."""

    t: float
    """Seconds after the ask, emulated."""
    pc: int
    ra: int
    hp: int
    phase: float
    pair: Pair
    frame: int


@dataclass(frozen=True)
class Played:
    move: Move
    end: int
    """MOVE_STATE.END."""
    end_pair: Pair
    frames: int
    """AI frames from the dispatch to the end."""
    skipped: int
    parts: tuple[Part, ...]
    spawns: tuple[Spawn, ...]
    hits: tuple[HpWrite, ...]
    pairs: tuple[tuple[float, Pair], ...]
    """Each pair the monster entered, emulated seconds after the ask, at the poll's grain."""
    t_end: float
    hp_after: int
    """The hunter's HP once the watch was over."""
    start: int
    """The emulated clock at the ask, in microseconds cut to 32 bits: the zero of every `t`."""

    @property
    def reason(self) -> str:
        return ENDS.get(self.end, f"end {self.end}")

    def damage(self) -> list[tuple[HpWrite, int]]:
        """HP writes that lowered it, with the drop: each write's reading against the next
        one's, the last against `hp_after`. Only right while nothing but the CPU writes HP."""
        after = [w.hp for w in self.hits[1:]] + [self.hp_after]
        return [(w, w.hp - nxt) for w, nxt in zip(self.hits, after, strict=False) if w.hp > nxt]


def _pair(word: int) -> Pair:
    return word >> 8, word & 0xFF


def _usec(s: Session) -> int:
    return s.client.evaluate("usec")


def _parse(message: str | None, start: int) -> HpWrite | None:
    try:
        usec, pc, ra, phase, hp, pair, frame = (int(f, 16) for f in (message or "").split())
    except ValueError:
        return None
    (cursor,) = struct.unpack("<f", struct.pack("<I", phase & 0xFFFF_FFFF))
    # the logged pair is the u16 at MAIN_STATE: main in the low byte
    t = ((usec - start) % WRAP) / 1e6
    return HpWrite(t, pc, ra, hp, cursor, (pair & 0xFF, pair >> 8), frame)


def block(s: Session) -> MoveState:
    """The move player's block, as cli_bridge.lua published it."""
    at = s.mem.u32(a.CLI_BRIDGE_BLOCK + a.CLI_BRIDGE.MOVE_STATE)
    if not at:
        raise ConnectionError("no move player: cli_bridge.lua has not published its block")
    return MoveState(s.mem, at)


def ask(s: Session, move: Move, slot: int, link: Bridge, st: MoveState) -> None:
    """Write `move` into the bridge and have it played on `slot`'s monster."""
    before = st.started
    s.mem.write(a.CLI_BRIDGE_BLOCK + a.CLI_BRIDGE.MOVE, move.pack())
    if not link.request(s, cast(Op, CMD_MOVE), slot)[1]:
        raise TimeoutError("no ack: the game is paused or cli_bridge.lua is not loaded")
    if not st.pending and st.started == before:
        raise RuntimeError("move refused: no big monster vtable is wrapped (em_vhook)")


def play(
    s: Session,
    move: Move,
    *,
    slot: int | None = None,
    after: float = 8.0,
    timeout: float = 30.0,
    link: Bridge | None = None,
) -> Played:
    """Play `move` on a big monster (`slot`'s, else the first) and watch it to its end and
    `after` seconds more, the hunter's HP writes logged throughout without stopping the game."""
    k, m = monster(s, slot)
    link = link or bridge(s)
    pack = Pack.read(s.mem, m.action_table)
    assert a.CLIP_BLOCK.size
    phase = m.base + a.ENTITY.CLIP_BLOCKS + move.part * a.CLIP_BLOCK.size + a.CLIP_BLOCK.PHASE
    hp = a.PLAYER_ENTITY + a.ENTITY.HP
    cells = m.base + a.ENTITY.MAIN_STATE
    st = block(s)
    frames = st.base + a.MOVE_STATE.FRAMES
    fmt = (
        f"{{usec}} {{pc}} {{ra}} {{[{phase:#x},4]}} {{[{hp:#x},2]}} {{[{cells:#x},2]}}"
        f" {{[{frames:#x},4]}}"
    )
    pairs: list[tuple[float, Pair]] = []
    raw: list[str | None] = []
    with s.client.trace(writes=[(hp, 2)], log_format=fmt) as stream:
        start = _usec(s)

        def note() -> int:
            now = _usec(s)
            pair = (m.main_state, m.sub_state)
            if not pairs or pairs[-1][1] != pair:
                pairs.append((((now - start) % WRAP) / 1e6, pair))
            return now

        note()
        ask(s, move, k, link, st)
        s.wait(lambda: not st.pending, 5.0, "the move's AI step")
        deadline = s.now() + timeout
        while st.state != DONE and s.now() < deadline:
            note()
            s.sleep(POLL)
        t_end = ((note() - start) % WRAP) / 1e6
        stop = s.now() + after
        while s.now() < stop:
            note()
            s.sleep(POLL)
        while True:
            try:
                raw.append(stream.next(0.5).message)
            except TimeoutError:
                break
    hits = tuple(h for h in (_parse(r, start) for r in raw) if h is not None)
    parts = tuple(
        Part(k_, entry_clip(move.entry, k_), pack.holding(node), end, peak)
        for k_, (node, end, peak) in enumerate(zip(st.node, st.clip_end, st.peak, strict=True))
    )
    spawns = tuple(
        Spawn(id_, frame, None if at == NEVER else at, cur, node)
        for (frame, id_), at, cur, node in zip(
            move.attacks, st.spawn_frame, st.spawn_cursor, st.spawn_node, strict=False
        )
    )
    return Played(
        move,
        st.end,
        _pair(st.end_pair),
        st.end_frame,
        st.skipped,
        parts,
        spawns,
        hits,
        tuple(pairs),
        t_end,
        s.mem.u16(hp),
        start,
    )


def stop(s: Session, slot: int | None = None) -> bool:
    """End the running move (the bridge's CLEAR, which also ends a hold); False without an ack."""
    k, _ = monster(s, slot)
    return bridge(s).request(s, Op.CLEAR, k)[1]
