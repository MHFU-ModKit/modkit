# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Shell commands on a monster's animation and AI: observers over the debugger, and holds
through the framework bridge.

The bridge is the CLI_BRIDGE block the framework's cli_bridge.lua polls every game tick: the
shell writes a command under a new SEQ, the script applies it and copies SEQ to ACK. The block
is in extra RAM, so it needs the plugin's memory=64, cli_bridge.lua loaded, and a cold boot.
"""

from __future__ import annotations

import argparse
import csv
import struct
from collections import defaultdict
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING

from ppsspp_debug import DebuggerError

from .. import addresses as a
from ..addresses import Field
from ..memory import Memory
from ..views import View, u32
from .shell import FREEZE_BITS, CommandError, Monster, Poller, integer, table

if TYPE_CHECKING:
    from .session import Session
    from .shell import Shell

MAGIC = 0x4D484252
"""CLI_BRIDGE.MAGIC once a command is written ("MHBR")."""
PROBE_MARK = 0xC0FFEE01
ACK_TIMEOUT = 3.0
"""cli_bridge.lua acks on its next tick, 2 Hz under mhfu_port and now and then late; no ack in
this long means it is not running."""
RECORD_PERIOD = 0.1
STALLED = "the game is paused or cli_bridge.lua is not loaded"
ROW = struct.Struct("<BB")
"""The start of a big monster's ACTION_LIST row: action id, category (0xFF for most)."""


class Op(IntEnum):
    """CLI_BRIDGE.CMD values."""

    FORCE_ACTION = 1
    FREEZE = 2
    CLEAR = 3
    MOVE = 4
    """Play CLI_BRIDGE.MOVE (`mhfu.live.moves`)."""


class BridgeBlock(View):
    struct = a.CLI_BRIDGE

    magic = u32(a.CLI_BRIDGE.MAGIC)
    seq = u32(a.CLI_BRIDGE.SEQ)
    cmd = u32(a.CLI_BRIDGE.CMD)
    slot = u32(a.CLI_BRIDGE.SLOT)
    arg = u32(a.CLI_BRIDGE.ARG)
    ack = u32(a.CLI_BRIDGE.ACK)
    status = u32(a.CLI_BRIDGE.STATUS)
    probe = u32(a.CLI_BRIDGE.PROBE)


_WORD = struct.Struct("<I")


class Bridge:
    """The command block at CLI_BRIDGE_BLOCK."""

    def __init__(self, mem: Memory) -> None:
        self.block = BridgeBlock(mem, a.CLI_BRIDGE_BLOCK)

    def reachable(self) -> bool:
        """The block is backed by memory: extra RAM is mapped."""
        try:
            self.block.probe = PROBE_MARK
            return self.block.probe == PROBE_MARK
        except DebuggerError:
            return False

    def send(self, op: Op, slot: int, arg: int = 0) -> int:
        """Write a command in one write, so the script never sees half of it; returns its SEQ.

        SEQ continues from the block's, since the script ignores a SEQ it has already seen.
        Clearing also zeroes STATUS, which the script stops writing but never resets.
        """
        b, c = self.block, a.CLI_BRIDGE
        seq = (b.seq + 1) & 0xFFFF_FFFF
        command = {c.MAGIC: MAGIC, c.SEQ: seq, c.CMD: op, c.SLOT: slot, c.ARG: arg}
        words = bytearray(c.ACK)  # the command words are the ones before ACK
        for at, value in command.items():
            _WORD.pack_into(words, at, value & 0xFFFF_FFFF)
        b.mem.write(b.base, bytes(words))
        if op == Op.CLEAR:
            b.status = 0
        return seq

    def request(
        self, session: Session, op: Op, slot: int, arg: int = 0, timeout: float = ACK_TIMEOUT
    ) -> tuple[int, bool]:
        """`send`, then wait for the ack: the script sees only the newest SEQ, so a command sent
        before the last one was acked would replace it unseen. Returns (SEQ, acked)."""
        seq = self.send(op, slot, arg)
        try:
            return seq, session.wait(lambda: self.block.ack == seq, timeout, "ack")
        except TimeoutError:
            return seq, False


def _bridge(shell: Shell) -> Bridge:
    bridge = Bridge(shell.mem)
    if not bridge.reachable():
        raise CommandError(
            "bridge not reachable: extra RAM is unmapped; it needs the plugin's memory=64 and "
            "cli_bridge.lua, from a cold boot"
        )
    return bridge


def _send(shell: Shell, bridge: Bridge, op: Op, slot: int, arg: int = 0) -> tuple[int, bool]:
    return bridge.request(shell.session, op, slot, arg)


def _unacked(acked: bool) -> str:
    return "" if acked else f"; no ack: {STALLED}"


def register(shell: Shell) -> None:
    p = shell.command("anim ls", "the action a monster is animating", anim_ls)
    p.add_argument("slot", type=integer)
    p = shell.command("anim record", "collect the values a monster's field takes", anim_record)
    p.add_argument("slot", type=integer)
    p.add_argument("action", choices=("start", "stop"))
    p.add_argument("--cell", default="ANIM_INPUT", help="ENTITY field (default ANIM_INPUT)")
    p = shell.command("anim table", "a monster's action pointers and descriptor rows", anim_table)
    p.add_argument("slot", type=integer)
    p.add_argument("--addr", type=integer, help="rows at this address (default ACTION_LIST)")
    p.add_argument("--stride", type=integer, default=8)
    p.add_argument("--rows", type=integer, default=48)
    p = shell.command("anim sweep", "force each action in turn and log the clip it plays", sweep)
    p.add_argument("slot", type=integer)
    p.add_argument("lo", type=integer, nargs="?", default=0)
    p.add_argument("hi", type=integer, nargs="?", default=63)
    p.add_argument("--dwell", type=float, default=2.0, help="seconds per action")
    p.add_argument("--out", type=Path, help="write the rows to this CSV")
    p = shell.command("anim play", "force and hold an action (bridge)", anim_play)
    p.add_argument("slot", type=integer)
    p.add_argument("action", type=integer)
    p = shell.command("anim stop", "release a forced action (bridge)", anim_stop)
    p.add_argument("slot", type=integer)
    p = shell.command("ai freeze", "halt or resume a monster's AI tick (bridge)", ai_freeze)
    p.add_argument("slot", type=integer)
    p.add_argument("on", choices=("on", "off"))
    shell.command("bridge status", "whether the bridge is up and in step", bridge_status)


def _state(m: Monster) -> str:
    inputs = "/".join(map(str, m.anim_input))
    ai = "halted" if m.frozen else "live"
    return f"action {m.action} (inputs {inputs}), move ({m.main_state}, {m.sub_state}), AI {ai}"


def anim_ls(shell: Shell, args: argparse.Namespace) -> str:
    m = shell.monster(args.slot)
    return f"mon {args.slot} {m.name}: {_state(m)}"


# --- record ---


def _cell(name: str) -> tuple[Field, struct.Struct]:
    """An integer ENTITY field, or an array's first element, and how to read it."""
    f = a.ENTITY.fields.get(name.upper())
    base = f.type.split("[")[0] if f else ""
    formats = {"u8": "B", "u16": "H", "u32": "I", "s8": "b", "s16": "h", "s32": "i", "ptr": "I"}
    if f is None or base not in formats:
        raise CommandError(f"{name} is not an integer ENTITY field")
    return f, struct.Struct("<" + formats[base])


@dataclass
class Recording:
    """How often each value of `cell` was seen, and when first."""

    cell: Field
    started: float
    hits: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    first: dict[int, float] = field(default_factory=dict)
    poller: Poller | None = None

    def stop(self) -> None:
        if self.poller is not None:
            self.poller.stop()


def anim_record(shell: Shell, args: argparse.Namespace) -> str:
    name = f"record {args.slot}"
    if args.action == "stop":
        rec = shell.tasks.get(name)
        if not isinstance(rec, Recording):
            return f"not recording mon {args.slot}"
        shell.stop(name)
        took = shell.session.now() - rec.started
        rows = [[v, f"0x{v:X}", rec.hits[v], f"{rec.first[v]:.1f}s"] for v in sorted(rec.hits)]
        head = f"mon {args.slot}: {len(rows)} distinct {rec.cell.name} values over {took:.1f}s"
        return head + ("\n" + table(["value", "hex", "hits", "first"], rows) if rows else "")
    m = shell.monster(args.slot)
    cell, fmt = _cell(args.cell)
    rec = Recording(cell, shell.session.now())
    address = m.base + cell

    def tick() -> None:
        (v,) = shell.mem.unpack(fmt, address)
        rec.hits[v] += 1
        rec.first.setdefault(v, shell.session.now() - rec.started)

    rec.poller = Poller(tick, RECORD_PERIOD)
    shell.start(name, rec)
    hz = 1 / RECORD_PERIOD
    return (
        f"recording mon {args.slot} {cell.name} at {hz:g} Hz until `anim record {args.slot} stop`"
    )


# --- table ---


def anim_table(shell: Shell, args: argparse.Namespace) -> str:
    m = shell.monster(args.slot)
    at = args.addr if args.addr is not None else m.action_list
    if at not in a.RAM:
        raise CommandError(f"ACTION_LIST is 0x{at:08X}, not a pointer; pass --addr")
    stride = max(args.stride, ROW.size)
    data = shell.mem.read(at, stride * args.rows)
    rows = []
    for r in range(args.rows):
        row = data[r * stride : (r + 1) * stride]
        action, kind = ROW.unpack_from(row)
        rows.append([r, f"0x{at + r * stride:08X}", action, f"0x{kind:02X}", row.hex(" ")])
    return "\n".join(
        [
            f"mon {args.slot} {m.name} at 0x{m.base:08X}: {_state(m)}",
            f"  ACTION_TABLE 0x{m.action_table:08X}  CLIP 0x{m.clip:08X}"
            f"  ACTION_LIST 0x{m.action_list:08X}",
            f"rows at 0x{at:08X}, stride {stride}; id and kind read as a big monster's row:",
            table(["row", "address", "id", "kind", "bytes"], rows),
        ]
    )


# --- sweep ---


@dataclass
class Step:
    action: int
    input: int
    clip: int
    """CLIP relative to ACTION_TABLE, stable across runs; absolute where that is 0."""
    held: bool


def sweep(shell: Shell, args: argparse.Namespace) -> str:
    m = shell.monster(args.slot)
    bridge = _bridge(shell)
    if args.hi < args.lo:
        raise CommandError("hi < lo")
    shell.say(
        f"forcing actions {args.lo}..{args.hi} on mon {args.slot}, {args.dwell:g} s each; "
        "watch the screen, Ctrl-C stops"
    )
    shell.say("action  input  clip      held")
    steps: list[Step] = []
    try:
        for action in range(args.lo, args.hi + 1):
            if not _send(shell, bridge, Op.FORCE_ACTION, args.slot, action)[1]:
                raise CommandError(f"no ack at action {action}: {STALLED}")
            shell.session.sleep(args.dwell)
            base, clip = m.action_table, m.clip
            rel = clip - base if base and clip >= base else clip
            step = Step(action, m.anim_input[0], rel, bridge.block.status == action)
            steps.append(step)
            shell.say(f"{action:<6}  {step.input:<5}  0x{rel:06X}  {'yes' if step.held else 'no'}")
    except KeyboardInterrupt:
        shell.say("interrupted")
    finally:
        _send(shell, bridge, Op.CLEAR, args.slot)
    by_clip: dict[int, list[int]] = defaultdict(list)
    for s in steps:
        by_clip[s.clip].append(s.action)
    rows = [[f"0x{c:06X}", ",".join(map(str, acts))] for c, acts in sorted(by_clip.items())]
    out = [f"{len(steps)} actions reach {len(by_clip)} clips", table(["clip", "actions"], rows)]
    if args.out:
        with args.out.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["action", "input", "clip", "held"])
            w.writerows([s.action, s.input, f"0x{s.clip:X}", int(s.held)] for s in steps)
        out.append(f"wrote {len(steps)} rows to {args.out}")
    return "\n".join(out)


# --- bridge holds ---


def anim_play(shell: Shell, args: argparse.Namespace) -> str:
    m = shell.monster(args.slot)
    bridge = _bridge(shell)
    seq, acked = _send(shell, bridge, Op.FORCE_ACTION, args.slot, args.action)
    lines = [f"forcing action {args.action} on mon {args.slot} [seq {seq}] until `anim stop`"]
    if not acked:
        lines.append(f"  no ack: {STALLED}")
    elif bridge.block.status == args.action:
        lines.append("  acked and held on a live monster")
    else:
        lines.append(f"  acked but not held: the game sees no entity in slot {args.slot}")
    lines.append(f"  now: {_state(m)}")
    return "\n".join(lines)


def anim_stop(shell: Shell, args: argparse.Namespace) -> str:
    shell.monster(args.slot)
    seq, acked = _send(shell, _bridge(shell), Op.CLEAR, args.slot)
    return f"released mon {args.slot} [seq {seq}]{_unacked(acked)}"


def ai_freeze(shell: Shell, args: argparse.Namespace) -> str:
    m = shell.monster(args.slot)
    on = args.on == "on"
    seq, acked = _send(shell, _bridge(shell), Op.FREEZE, args.slot, int(on))
    if not on:
        # the script stops setting the bits but leaves them set, so clear them once here
        m.freeze_gate &= ~FREEZE_BITS
    return f"ai freeze mon {args.slot} {args.on} [seq {seq}]{_unacked(acked)}"


def bridge_status(shell: Shell, args: argparse.Namespace) -> str:
    bridge = Bridge(shell.mem)
    if not bridge.reachable():
        return "bridge not reachable: extra RAM is unmapped (plugin memory=64, cold boot)"
    b = bridge.block
    magic = "a command was written" if b.magic == MAGIC else "no command this boot"
    step = "in step" if b.seq == b.ack else f"behind: {STALLED}"
    return "\n".join(
        [
            f"bridge at 0x{b.base:08X} reachable",
            f"  magic   0x{b.magic:08X} ({magic})",
            f"  seq/ack {b.seq}/{b.ack} ({step})",
            f"  command {b.cmd}, slot {b.slot}",
            f"  held    {b.status} (0 = none)",
        ]
    )
