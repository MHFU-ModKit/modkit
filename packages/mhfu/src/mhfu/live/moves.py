# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A move the framework's move player runs on a live big monster (`framework/include/mhfu/move.h`),
and what came of it: each body part's clip, each attack's spawn, the hunter's HP writes against
the clip's phase, and the pairs the monster went through after.

    with Rig.attach() as rig:
        r = play(rig.s, Move(46, attacks=(Attack(6, 56, 90),)))
        r.parts[0].played, r.spawns[0].at, r.hits

It asks through the debug bridge (`cli_bridge.lua`, CMD 4), so the game needs the framework,
the bridge and a monster whose vtable em_vhook wrapped: a cold boot (`clips.ride`). `watch`
follows a move something else starts, a port's own move from Lua or a rule among them.
"""

from __future__ import annotations

import re
import struct
from collections.abc import Callable
from dataclasses import dataclass, replace

from .. import addresses as a
from ..entries import entry_clip
from ..views import View, f32s, ptr, ptrs, u8, u8s, u16, u32, u32s
from .clips import Pack, bridge, monster
from .session import Session
from .shell_anim import Bridge, Op

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
    8: "a wall ahead",
    9: "a class-2 wall: stuck",
}
TURNS = ("still", "hunter", "away", "fixed")
"""STEER_PARAMS.TURN by name."""
FULL_TURN = 0x10000
"""ENTITY.YAW units in a turn."""
SPEED = 2.0
"""Clip frames a forced clip's cursor moves per AI frame (CLIP_BLOCK.SPEED)."""
POLL = 0.2
WRAP = 1 << 32
"""The emulated clock a log line carries is cut to 32 bits."""

Pair = tuple[int, int]


@dataclass(frozen=True)
class Attack:
    """Attack record `id` spawned at clip frame `frame`, its node ended at clip frame `end`;
    without `end` the node lives as long as its record says."""

    id: int
    frame: int
    end: int | None = None


@dataclass(frozen=True)
class Move:
    """What `mhfu_move_t` holds: an entry, its attacks, the carrier
    pair it rides, the pair entered at the end (None: the carrier hands off itself). The
    default carrier (0,2) is em75's alert hub: one dispatch, then the brain once the clip ends;
    (0,1) is not entered as itself when ENTITY+0x4B9 is set (the translator makes it (0,2))."""

    entry: int
    attacks: tuple[Attack, ...] = ()
    carrier: Pair = (0, 2)
    back: tuple[int, int, int] | None = None
    length: int = 0
    skip: bool = False
    part: int = 0
    spawner: int = 0
    host_attacks: bool = False
    """Keep the host entry's own attacks and effects (MONSTER_VTABLE.ANIM_EVENTS)."""

    @classmethod
    def unpack(cls, raw: bytes) -> Move:
        """struct MOVE, as the move player holds it."""
        f = a.MOVE
        entry, length = struct.unpack_from("<HH", raw, f.ENTRY)
        cm, cs, bm, bs, bmode = struct.unpack_from("<BBBBB", raw, f.CARRIER_MAIN)
        skip, part, n = struct.unpack_from("<BBB", raw, f.SKIP)
        assert a.MOVE_ATTACK.size
        attacks = []
        for i in range(min(n, a.MOVE.ATTACKS.count or 0)):
            at = f.ATTACKS + i * a.MOVE_ATTACK.size
            frame, id_ = struct.unpack_from("<HH", raw, at + a.MOVE_ATTACK.FRAME)
            (end,) = struct.unpack_from("<H", raw, at + a.MOVE_ATTACK.END)
            attacks.append(Attack(id_, frame, end or None))
        (spawner,) = struct.unpack_from("<I", raw, f.SPAWNER)
        return cls(
            entry,
            tuple(attacks),
            (cm, cs),
            None if bm == NO_PAIR else (bm, bs, bmode),
            length,
            bool(skip),
            part,
            spawner,
            bool(raw[f.HOST_ATTACKS]),
        )

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
        for i, atk in enumerate(self.attacks):
            at = f.ATTACKS + i * a.MOVE_ATTACK.size
            struct.pack_into("<HH", out, at + a.MOVE_ATTACK.FRAME, atk.frame, atk.id)
            struct.pack_into("<H", out, at + a.MOVE_ATTACK.END, atk.end or 0)
        struct.pack_into("<I", out, f.SPAWNER, self.spawner)
        struct.pack_into("<B", out, f.HOST_ATTACKS, int(self.host_attacks))
        return bytes(out)


@dataclass(frozen=True)
class Steer:
    """What `mhfu_steer_spec_t` holds: the clip's turn curve (`curve`, YAW every 2 clip frames:
    the clips module's `_turns`, `turns_of`), a turn mode on top, and whether a wall ends the
    move, a class-2 one into `stuck`."""

    curve: tuple[int, ...] = ()
    turn: str = "still"
    rate: int = 64
    """YAW units an AI frame toward or away from the hunter."""
    total: float = 0.0
    """Degrees "fixed" turns over `frames`, the way YAW grows."""
    frames: int = 1
    walls: bool = False
    dir: float = 0.0
    """Degrees of the travel against YAW: which wall sectors count as ahead."""
    stuck: tuple[int, int, int] = (0, 6, 1)

    def pack(self) -> bytes:
        """struct STEER_SPEC."""
        f, p = a.STEER_SPEC, a.STEER_PARAMS
        most = a.STEER_SPEC.KEYS.count or 0
        if len(self.curve) > most:
            raise ValueError(f"at most {most} curve keys")
        assert f.size
        out = bytearray(f.size)
        total = round(self.total * FULL_TURN / 360)
        struct.pack_into(
            "<BBH", out, f.STEER + p.TURN, TURNS.index(self.turn), self.walls, self.rate
        )
        struct.pack_into("<iHH", out, f.STEER + p.TOTAL, total, self.frames, _yaw(self.dir))
        struct.pack_into("<HBBB", out, f.KEY_COUNT, len(self.curve), *self.stuck)
        struct.pack_into(f"<{len(self.curve)}H", out, f.KEYS, *(k & 0xFFFF for k in self.curve))
        return bytes(out)


def _yaw(degrees: float) -> int:
    return round(degrees * FULL_TURN / 360) & 0xFFFF


def turns_of(module: str) -> dict[int, tuple[int, ...]]:
    """Entry -> its turn curve, from a clips module's text (`mhfu-port build` writes it)."""
    return {
        int(e): tuple(int(h[i : i + 4], 16) for i in range(0, len(h), 4))
        for e, h in re.findall(r'\[(\d+)\] = "([0-9a-f]*)"', module)
    }


NO_STEER = Steer()
"""No turn, no walls."""


class SteerState(View):
    """The move player's steering (struct STEER_STATE) in its block."""

    struct = a.STEER_STATE
    yaw_base = u16(a.STEER_STATE.BASE)
    wall = u8(a.STEER_STATE.WALL)
    yaw0 = u16(a.STEER_STATE.YAW0)
    yaw = u16(a.STEER_STATE.YAW)


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
    ended_frame = u32s(a.MOVE_STATE.ENDED_FRAME)
    ended_state = u8s(a.MOVE_STATE.ENDED_STATE)


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
    end: int | None = None
    """The clip frame it was to be ended at."""
    ended: int | None = None
    """AI frame the move ended it; None if it did not."""
    ended_state: int = 0
    """ATTACK_NODE.STATE then: 1 or 2 ended by the move, 0 it had ended itself, 0xFF gone."""


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
    steer: Steer = NO_STEER
    yaw0: int = 0
    """YAW on the move's first playing frame."""
    yaw: int = 0
    """YAW as the move's last step left it."""

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


def _parse(message: str | None, start: int, part: int = 0) -> HpWrite | None:
    """A `_hp_format` line; `part`'s cursor is the phase."""
    try:
        usec, pc, ra, *phases, hp, pair, frame = (int(f, 16) for f in (message or "").split())
        phase = phases[part]
    except (ValueError, IndexError):
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


def ask(
    s: Session, move: Move, slot: int, link: Bridge, st: MoveState, steer: Steer = NO_STEER
) -> None:
    """Write `move` into the bridge and `steer` into the block, and have it played on `slot`'s
    monster."""
    before = st.started
    s.mem.write(st.base + a.MOVE_STATE.STEER + a.STEER_STATE.NEXT, steer.pack())
    s.mem.write(a.CLI_BRIDGE_BLOCK + a.CLI_BRIDGE.MOVE, move.pack())
    if not link.request(s, Op.MOVE, slot)[1]:
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
    steer: Steer = NO_STEER,
) -> Played:
    """Play `move` on a big monster (`slot`'s, else the first) and watch it to its end and
    `after` seconds more, the hunter's HP writes logged throughout without stopping the game."""
    k, _ = monster(s, slot)
    link = link or bridge(s)
    st = block(s)
    played = watch(
        s, lambda: ask(s, move, k, link, st, steer), slot=slot, after=after, timeout=timeout
    )
    return replace(played, steer=steer)


def watch(
    s: Session,
    start: Callable[[], object],
    *,
    slot: int | None = None,
    after: float = 8.0,
    timeout: float = 30.0,
) -> Played:
    """Call `start`, then follow the next move the move player starts on a big monster (`slot`'s,
    else the first) to its end and `after` seconds more, as `play` does; the move is read back
    from the block."""
    _, m = monster(s, slot)
    pack = Pack.read(s.mem, m.action_table)
    hp = a.PLAYER_ENTITY + a.ENTITY.HP
    cells = m.base + a.ENTITY.MAIN_STATE
    st = block(s)
    frames = st.base + a.MOVE_STATE.FRAMES
    assert a.CLIP_BLOCK.size
    pairs: list[tuple[float, Pair]] = []
    raw: list[str | None] = []
    before = st.started
    phase = 0
    with s.client.trace(writes=[(hp, 2)], log_format=_hp_format(m.base, 0, cells, frames)) as h:
        t0 = _usec(s)

        def note() -> int:
            now = _usec(s)
            pair = (m.main_state, m.sub_state)
            if not pairs or pairs[-1][1] != pair:
                pairs.append((((now - t0) % WRAP) / 1e6, pair))
            return now

        note()
        start()
        s.wait(lambda: st.started != before and not st.pending, timeout, "the move's start")
        move = Move.unpack(s.mem.read(st.base + a.MOVE_STATE.MOVE, a.MOVE.size or 0))
        phase = move.part
        deadline = s.now() + timeout
        while st.state != DONE and s.now() < deadline:
            note()
            s.sleep(POLL)
        t_end = ((note() - t0) % WRAP) / 1e6
        stop_at = s.now() + after
        while s.now() < stop_at:
            note()
            s.sleep(POLL)
        while True:
            try:
                raw.append(h.next(0.5).message)
            except TimeoutError:
                break
    hits = tuple(w for w in (_parse(r, t0, phase) for r in raw) if w is not None)
    parts = tuple(
        Part(k_, entry_clip(move.entry, k_), pack.holding(node), end, peak)
        for k_, (node, end, peak) in enumerate(zip(st.node, st.clip_end, st.peak, strict=True))
    )
    spawns = tuple(
        Spawn(
            atk.id,
            atk.frame,
            None if at == NEVER else at,
            cur,
            node,
            atk.end,
            None if ended == NEVER else ended,
            how,
        )
        for atk, at, cur, node, ended, how in zip(
            move.attacks,
            st.spawn_frame,
            st.spawn_cursor,
            st.spawn_node,
            st.ended_frame,
            st.ended_state,
            strict=False,
        )
    )
    sst = SteerState(s.mem, st.base + a.MOVE_STATE.STEER)
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
        t0,
        NO_STEER,
        sst.yaw0,
        sst.yaw,
    )


def _hp_format(ent: int, part: int, cells: int, frames: int) -> str:
    """A HP write's log line: the clock, pc, ra, every part's cursor, HP, the pair, FRAMES."""
    assert a.CLIP_BLOCK.size
    cursors = " ".join(
        f"{{[{ent + a.ENTITY.CLIP_BLOCKS + k * a.CLIP_BLOCK.size + a.CLIP_BLOCK.PHASE:#x},4]}}"
        for k in range(a.ENTITY.CLIP_BLOCKS.count or 0)
    )
    hp = a.PLAYER_ENTITY + a.ENTITY.HP
    return (
        f"{{usec}} {{pc}} {{ra}} {cursors} {{[{hp:#x},2]}} {{[{cells:#x},2]}} {{[{frames:#x},4]}}"
    )


def stop(s: Session, slot: int | None = None) -> bool:
    """End the running move (the bridge's CLEAR, which also ends a hold); False without an ack."""
    k, _ = monster(s, slot)
    return bridge(s).request(s, Op.CLEAR, k)[1]
