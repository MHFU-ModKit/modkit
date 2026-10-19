# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A big monster's AI script, disassembled: the bytecode AI_SCRIPT interprets, found through the
header each species row points at.

    from mhfu.em import script
    sc = script.Scripts(game, 75)         # an em overlay and the species table in game_task
    sc.variants()                         # {header address: the map types that use it}
    lst = sc.disassemble(variant=0)       # every routine the header reaches
    lst.routines[entry].insns             # Insn(pc, op, sub, raw, args, depth, succ, refs)
    lst.stats()                           # counts by kind and opcode, enter pairs, conditions
    print(lst.text())                     # the listing
    script.decode(read, entry)            # one routine, from any byte reader

The header is a list of pointer fields, each a routine list: base, calls, interrupts by bit,
loop bodies. Routines are not found by sweeping, since data tables sit among them: each is followed
from its entry through the structured control flow (if, switch, repeat, weighted branch) and ends
where every path ends in a goto or an `end`. OPCODES is the one table of lengths, the ones the
game's own skipper (SCRIPT_SKIP) steps by.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import cached_property

from .. import addresses as a
from ..files import GAME_TASK, Extracted
from ..memory import Space, Unmapped
from ..overlay import Overlay

Reader = Callable[[int], int]
"""The byte at an address."""

VARIANTS = 17
"""Headers per species row: one per map type."""
SCAN_LIMIT = 100_000
"""Instructions a scan for the end of a block steps before it is taken for a runaway."""
SENTINEL = 0x95
"""The byte a scan for the end of a block stops on; no script holds it."""
_MAX_FIELDS = 64
_U16 = ":u16"


class Shape(Enum):
    """How the byte after an opcode, its sub-code, structures the stream. 0 always opens a
    block; the markers of BLOCKS follow, the last of them ends it."""

    PLAIN = "plain"
    IF = "if"
    SWITCH = "switch"
    """The first case follows the header as an ordinary case marker."""
    REPEAT = "repeat"
    RANDOM = "random"
    BUCKET = "bucket"
    ZONE = "zone"


BLOCKS: Mapping[Shape, Mapping[int, str]] = {
    Shape.IF: {1: "else", 2: "end"},
    Shape.SWITCH: {1: "case", 2: "default", 3: "end"},
    Shape.REPEAT: {1: "end"},
    Shape.RANDOM: {**dict.fromkeys(range(1, 14), "branch"), 0xFF: "end"},
    Shape.BUCKET: {**dict.fromkeys(range(1, 6), "bucket"), 0xFF: "end"},
    Shape.ZONE: {**dict.fromkeys(range(1, 4), "case"), 0xFF: "end"},
}
"""A block's marker sub-codes and what each is called; the one called `end` closes it."""
_LABELLED = ("case", "branch")
"""Markers that carry the operands of `Op.label`."""


CLOSE = {
    shape: next(s for s, name in marks.items() if name == "end") for shape, marks in BLOCKS.items()
}
"""The sub-code that ends each shape's block."""


def _size(layout: str) -> int:
    """Bytes of an operand layout: names, `_` for a byte the game ignores, `name:s8` signed,
    `name:u16` a big-endian pair."""
    return sum(2 if tok.endswith(_U16) else 1 for tok in layout.split())


class Flow(Enum):
    NEXT = "next"
    CALL = "call"
    """Runs another routine, which returns to the next instruction."""
    GOTO = "goto"
    """Continues in another routine and does not come back."""
    END = "end"
    """Returns, or ends the thread; the kind says where."""


@dataclass(frozen=True)
class Op:
    """One opcode: its name, kind, operand layout and length rule."""

    code: int
    name: str
    kind: str
    """flow, condition, action, state, random, wait or other."""
    shape: Shape
    args: str
    """Operand layout after the opcode, or after the sub-code of a block's opener."""
    label: str
    """Operand layout of a block's `case` and `branch` markers."""
    lengths: Mapping[int, int]
    """Length by the byte after the opcode; for a block, by its sub-code."""
    size: int
    """Length for any other byte: all of a fixed opcode, one for a sub-code it has none for."""
    flow: Flow
    marker: int
    """The opcode of the markers inside a block this opcode opens."""

    @property
    def coded(self) -> bool:
        return self.shape is not Shape.PLAIN

    def valid(self, sub: int) -> bool:
        return not self.coded or sub in self.lengths

    def role(self, sub: int | None) -> str | None:
        """open, mid or close for a block's sub-codes, else None."""
        if not self.coded or sub is None or sub not in self.lengths:
            return None
        if sub == 0:
            return "open"
        return "close" if sub == CLOSE[self.shape] else "mid"

    def mnemonic(self, sub: int | None) -> str:
        """The name, a marker's sub-code spelled out: `if_enraged.else`."""
        if self.role(sub) in (None, "open") or sub is None:
            return self.name
        return f"{self.name}.{BLOCKS[self.shape][sub]}"

    def layout(self, sub: int | None) -> str:
        """The operand layout of the instruction with this sub-code."""
        if not self.coded or sub == 0:
            return self.args
        marks = BLOCKS[self.shape]
        return self.label if sub is not None and marks.get(sub) in _LABELLED else ""


def _op(
    code: int,
    name: str,
    kind: str,
    args: str = "",
    *,
    shape: Shape = Shape.PLAIN,
    label: str = "",
    by: Mapping[int, int] | None = None,
    flow: Flow = Flow.NEXT,
    marker: int | None = None,
) -> Op:
    """An Op whose lengths follow from its layouts; `by` sets the length for a byte after a
    plain opcode."""
    if shape is Shape.PLAIN:
        lengths = dict(by or {})
        size = 1 + _size(args)
    else:
        lengths = {0: 2 + _size(args)}
        for sub, mark in BLOCKS[shape].items():
            lengths[sub] = 2 + (_size(label) if mark in _LABELLED else 0)
        size = 1
    return Op(
        code,
        name,
        kind,
        shape,
        args,
        label,
        lengths,
        size,
        flow,
        code if marker is None else marker,
    )


C, F, S, A = "condition", "flow", "state", "action"
_IF, _SW = Shape.IF, Shape.SWITCH

OPCODES: Mapping[int, Op] = {
    o.code: o
    for o in (
        _op(0x01, "if_provoked", C, shape=_IF),
        _op(0x02, "if_aware", C, shape=_IF),
        _op(0x03, "if_goal_elsewhere", C, shape=_IF),
        _op(0x04, "goto_base", F, flow=Flow.GOTO),
        _op(0x05, "enter", A, "main sub gate"),
        _op(0x06, "set_target", S, "kind a b", by={3: 5}),
        _op(0x07, "goto", F, "routine", flow=Flow.GOTO),
        _op(0x08, "if_mode0", C, shape=_IF),
        _op(0x09, "if_mode2", C, shape=_IF),
        _op(0x0A, "set_aux", S, "value"),
        _op(0x0B, "if_combat_mode", C, "mode", shape=_IF),
        _op(0x0C, "set_var", S, "var value"),
        _op(0x0D, "reset_var", S, "var"),
        _op(0x0E, "if_in_section", C, "section:u16", shape=_IF),
        _op(0x0F, "walk", F, "mode limit stages step arrive", flow=Flow.CALL),
        _op(0x10, "walk_next", S),
        _op(0x11, "target_provoker", S),
        _op(0x12, "target_last_aware", S),
        _op(0x13, "target_remembered", S),
        _op(0x14, "if_off_facing", C, "deg", shape=_IF),
        _op(0x15, "switch_section", C, "n", shape=_SW, label="section:u16"),
        _op(0x16, "call_ret", F, "routine", flow=Flow.CALL),
        _op(0x17, "route_arm", S, "list count body loop"),
        _op(0x18, "route_run", F, flow=Flow.CALL),
        _op(0x19, "target_saved_section", S),
        _op(0x1A, "target_hop", S, "section:u16"),
        _op(0x1B, "if_counter", C, "value", shape=_IF),
        _op(0x1C, "switch_hp", C, "n", shape=_SW, label="case"),
        _op(0x1D, "switch_counter", C, "n", shape=_SW, label="case"),
        _op(0x1E, "clear_counters", S),
        _op(0x1F, "if_mode1", C, shape=_IF),
        _op(0x20, "switch_yaw", C, "n", shape=_SW, label="case"),
        _op(0x21, "if_gauge_a_low", C, shape=_IF),
        _op(0x22, "if_dist_flat", C, "dist", shape=_IF),
        _op(0x24, "repeat", F, "count", shape=Shape.REPEAT),
        _op(0x25, "reset_repeat", S),
        _op(0x26, "set_lock", S, "value"),
        _op(0x27, "switch_flags16", C, "n", shape=_SW, label="case"),
        _op(0x28, "if_hunter_here", C, shape=_IF),
        _op(0x29, "if_timer_a_done", C, shape=_IF),
        _op(0x2A, "if_timer_b_done", C, shape=_IF),
        _op(0x2B, "if_var", C, "var value", shape=_IF),
        _op(0x2C, "switch_species_group", C, "n", shape=_SW, label="case"),
        _op(0x2D, "target_saved_spot", S),
        _op(0x2E, "select_row", S, "row"),
        _op(0x2F, "if_hunter_special", C, shape=_IF),
        _op(0x30, "hunter_special", S),
        _op(0x31, "target_kind8", S),
        _op(0x32, "if_aux", C, "value", shape=_IF),
        _op(0x33, "switch_aux", C, "n", shape=_SW, label="case"),
        _op(0x34, "if_in_pair", C, "main sub", shape=_IF),
        _op(0x35, "if_enraged", C, shape=_IF),
        _op(0x36, "if_dist_3d", C, "dist", shape=_IF),
        _op(0x38, "if_cell_flag", C, shape=_IF),
        _op(0x39, "if_latch", C, shape=_IF),
        _op(0x3A, "if_var3_zero", C, shape=_IF),
        _op(0x3B, "if_partner", C, shape=_IF),
        _op(0x3C, "if_partner_fighting", C, shape=_IF),
        _op(0x3D, "if_dest_section", C, "section:u16", shape=_IF),
        _op(0x3E, "switch_dest_section", C, "n", shape=_SW, label="section:u16"),
        _op(0x3F, "call_spot", F, "section:u16 radius", flow=Flow.CALL),
        _op(0x40, "set_combat", S, "on"),
        _op(0x41, "self_heal", A, "apply"),
        _op(0x42, "if_pursuit_off_facing", C, "deg:s8", shape=_IF),
        _op(0x43, "if_quest_flag", C, shape=_IF),
        _op(0x44, "if_partner_here", C, shape=_IF),
        _op(0x45, "if_sole_hunter", C, shape=_IF),
        _op(0x46, "if_hunter_in_pair", C, "main sub", shape=_IF),
        _op(0x47, "if_flag40", C, shape=_IF),
        _op(0x48, "wait", "wait", "frames"),
        _op(0x49, "target_remembered_param", S, "param"),
        _op(0x4A, "if_sensed", C, shape=_IF),
        _op(0x4B, "forget_hunter_timer", S),
        _op(0x4C, "clear_notice", S),
        _op(0x4D, "refresh_pursuit", S),
        _op(0x4E, "refill_gauge_a", S, "apply"),
        _op(0x4F, "refill_gauge_b", S, "apply"),
        _op(0x50, "refill_gauge_c", S, "apply"),
        _op(0x51, "if_mode4", C, shape=_IF),
        _op(0x52, "pick_target", S),
        _op(0x53, "pick_target_drawn", S),
        _op(0x54, "if_target_drawn", C, shape=_IF),
        _op(0x55, "if_target_noticed", C, shape=_IF),
        _op(0x56, "switch_var5f3", C, "n", shape=_SW, label="case"),
        _op(0x57, "switch_class", C, "n", shape=_SW, label="case"),
        _op(0x58, "target_attacker", S),
        _op(0x59, "if_var529", C, shape=_IF),
        _op(0x5A, "if_hunter_zone", C, "zone", shape=_IF),
        _op(0x5B, "forget_unsensed", S, "apply"),
        _op(0x5C, "if_valid_ground", C, shape=_IF),
        _op(0x5D, "if_pursuit_valid", C, shape=_IF),
        _op(0x5E, "if_zone", C, "zone", shape=_IF),
        _op(0x5F, "pick_target_zone", S),
        _op(0x60, "if_stage_flag", C, shape=_IF),
        _op(0x61, "set_timer_b", S),
        _op(0x62, "switch_zone_relation", C, shape=Shape.ZONE),
        _op(0x63, "if_flags16_mask", C, "mask", shape=_IF),
        _op(0x64, "if_target_notice", C, "level", shape=_IF),
        _op(0x65, "forget_all", S),
        _op(0x66, "if_hunter_mark", C, shape=_IF),
        _op(0x67, "if_active", C, shape=_IF),
        _op(0x68, "script_reset", S),
        _op(0x69, "if_request_off_facing", C, "deg:s8", shape=_IF),
        _op(0x70, "switch_species", C, "n", shape=_SW, label="case"),
        _op(0x71, "if_class_at_least", C, "min", shape=_IF),
        _op(0x75, "switch_var492", C, "n", shape=_SW, label="case"),
        _op(0x76, "switch_zero", C, "n", shape=_SW, label="case"),
        _op(0x77, "if_area_unmapped", C, shape=_IF),
        _op(0x78, "if_target_in_arc", C, "lo hi", shape=_IF),
        _op(0x79, "switch_query", C, "n arg", shape=_SW, label="case"),
        _op(0x7A, "switch_area", C, "n", shape=_SW, label="case"),
        _op(0x7B, "rng_step", S),
        _op(0x7C, "if_area_listed", C, shape=_IF),
        _op(0x7D, "if_never", C, shape=_IF),
        _op(0x7E, "switch_dist", C, "n", shape=_SW, label="case"),
        _op(0x80, "random", "random", "n", shape=Shape.RANDOM, label="weight"),
        _op(0x81, "call", F, "routine", flow=Flow.CALL),
        _op(0x82, "call_deep", F, "table routine", flow=Flow.CALL),
        _op(0x83, "switch_range", C, "n", shape=Shape.BUCKET),
        _op(0x84, "rng_step2", S),
        _op(
            0x85,
            "random_shifted",
            "random",
            "n shift",
            shape=Shape.RANDOM,
            label="weight",
            marker=0x80,
        ),
        _op(0x86, "pick_target_role", S, "mask:u16"),
        _op(0x87, "if_role_hunter", C, "mask:u16", shape=_IF),
        _op(0x90, "warp", A, "waypoint"),
        _op(0x91, "face_waypoint", A, "waypoint"),
        _op(0x92, "nop", "other"),
        _op(0x93, "nop2", "other"),
        # SCRIPT_SKIP steps this header one byte short, onto the first case value
        _op(0x94, "switch_enemy_var", C, "n", shape=_SW, label="case"),
        _op(0x99, "face", A, "yaw"),
        _op(0xFF, "end", F, "kind", flow=Flow.END),
    )
}
"""Every opcode the game's dispatch has a case for."""

END_KINDS: Mapping[int, str] = {
    0: "base",
    1: "return",
    2: "return2",
    3: "return_ret",
    4: "route",
    5: "route2",
    0xF5: "int_0x40",
    0xF6: "int_0x80",
    0xF7: "forget",
    0xF8: "int_0x08",
    0xF9: "int_0x04",
    0xFA: "int_0x02",
    0xFB: "loop_next",
    0xFC: "notice",
    0xFD: "int_0x10",
    0xFE: "walk_step",
    0xFF: "walk_loop",
}
"""The kinds of `end` the game acts on, named by where they go; any other it ignores."""


@dataclass(frozen=True)
class Problem:
    """Why a routine does not decode cleanly."""

    kind: str
    """opcode, sub-code, end, structure, unmapped or overlap."""
    pc: int
    text: str


class BadScript(ValueError):
    """Bytes that are not a script: an opcode the game has no case for, a sub-code the opcode
    has none for, a block with no end."""

    def __init__(self, kind: str, pc: int, text: str) -> None:
        super().__init__(text)
        self.problem = Problem(kind, pc, text)


def op_at(read: Reader, pc: int) -> Op:
    code = read(pc)
    try:
        return OPCODES[code]
    except KeyError:
        raise BadScript(
            "opcode", pc, f"0x{code:02X} at 0x{pc:08X} has no case in the dispatch"
        ) from None


def length(read: Reader, pc: int) -> int:
    """Bytes of the instruction at `pc`, as the game's skipper counts them, except that a header
    counts apart from the marker after it, which the skipper steps with it. A sub-code the
    opcode has none for counts as one byte; `Op.valid` tells it apart."""
    op = op_at(read, pc)
    return op.lengths.get(read(pc + 1), op.size) if op.lengths else op.size


def markers(read: Reader, pc: int) -> list[int]:
    """The markers of the block the instruction at `pc` opens or sits in, up to the one that
    ends it, as the game's scan finds them: a block of the same opcode inside is skipped whole,
    one of another opcode is stepped over."""
    op = op_at(read, pc)
    end = CLOSE[op.shape]
    found: list[int] = []
    depth = 0
    at = pc + length(read, pc)
    for _ in range(SCAN_LIMIT):
        if read(at) == SENTINEL:
            break
        if read(at) == op.marker:
            sub = read(at + 1)
            if sub == 0:
                depth += 1
            elif sub == end and depth:
                depth -= 1
            elif not depth:
                found.append(at)
                if sub == end:
                    return found
        at += length(read, at)
    raise BadScript("structure", pc, f"no end for the block at 0x{pc:08X}")


@dataclass(frozen=True)
class Ref:
    """An instruction that names a routine by list and index."""

    kind: str
    """goto, call, walk (a routine of the walk) or stages (a stage array, which is data)."""
    field: int
    index: int


@dataclass(frozen=True)
class Insn:
    """One decoded instruction."""

    pc: int
    op: Op
    sub: int | None
    """The byte after the opcode of an instruction that has a sub-code, else None."""
    raw: bytes
    args: Mapping[str, int]
    depth: int = 0
    """Blocks it sits in; a marker sits level with the block it belongs to."""
    succ: tuple[int, ...] = ()
    """Where control goes inside the routine; empty at a goto and an `end`."""
    refs: tuple[Ref, ...] = ()

    @property
    def length(self) -> int:
        return len(self.raw)

    @property
    def kind(self) -> str:
        return self.op.kind

    @property
    def role(self) -> str | None:
        return self.op.role(self.sub)

    @property
    def mnemonic(self) -> str:
        if self.op.flow is Flow.END:
            return f"{self.op.name}.{END_KINDS[self.args['kind']]}"
        return self.op.mnemonic(self.sub)


def _operands(op: Op, sub: int | None, raw: bytes) -> dict[str, int]:
    out: dict[str, int] = {}
    at = 1 if sub is None else 2
    for tok in op.layout(sub).split():
        name, _, kind = tok.partition(":")
        if kind == "u16":
            value, size = int.from_bytes(raw[at : at + 2], "big"), 2
        else:
            value, size = raw[at], 1
            if kind == "s8" and value > 0x7F:
                value -= 0x100
        if name != "_":
            out[name] = value
        at += size
    out |= {f"x{i}": b for i, b in enumerate(raw[at:], at)}
    return out


def _refs(op: Op, args: Mapping[str, int]) -> tuple[Ref, ...]:
    """The routines an instruction names; a walk's stage array is data, not a routine."""
    match op.name:
        case "goto_base":
            return (Ref("goto", 0, 0),)
        case "goto":
            return (Ref("goto", 0, args["routine"]),)
        case "call":
            return (Ref("call", 1, args["routine"]),)
        case "call_ret":
            return (Ref("call", 9, args["routine"]),)
        case "call_deep":
            return (Ref("call", _FIRST_DEEP + args["table"], args["routine"]),)
        case "walk":
            return (
                Ref("walk", 2, args["step"]),
                Ref("walk", 2, args["arrive"]),
                Ref("stages", 2, args["stages"]),
            )
    return ()


def fetch(read: Reader, pc: int) -> Insn:
    """The instruction at `pc` without its flow: length, operands and the routines it names."""
    op = op_at(read, pc)
    sub = read(pc + 1) if op.coded else None
    if sub is not None and not op.valid(sub):
        raise BadScript("sub-code", pc, f"{op.name} at 0x{pc:08X} has no sub-code 0x{sub:02X}")
    raw = bytes(read(pc + i) for i in range(length(read, pc)))
    args = _operands(op, sub, raw)
    if op.flow is Flow.END and args["kind"] not in END_KINDS:
        raise BadScript(
            "end", pc, f"end 0x{args['kind']:02X} at 0x{pc:08X} is no kind the game acts on"
        )
    return Insn(pc, op, sub, raw, args, refs=_refs(op, args))


@dataclass(frozen=True)
class Routine:
    """The instructions reached from one entry, by address."""

    entry: int
    insns: tuple[Insn, ...]
    problems: tuple[Problem, ...] = ()

    @property
    def clean(self) -> bool:
        """Every path ends in a goto or an `end`, on instructions the game dispatches."""
        return not self.problems

    @cached_property
    def at(self) -> dict[int, Insn]:
        return {i.pc: i for i in self.insns}

    @property
    def unknown(self) -> int:
        """Opcodes the game's dispatch has no case for."""
        return sum(p.kind == "opcode" for p in self.problems)


def _flow(read: Reader, ins: Insn, loops: dict[int, int]) -> tuple[list[int], list[int]]:
    """Where control goes after `ins`, the first successor being the fall-through, and the
    markers of the block it opens, which belong to the routine whether or not a path runs
    through them."""
    op, pc, nxt = ins.op, ins.pc, ins.pc + ins.length
    role = ins.role
    if op.flow in (Flow.GOTO, Flow.END):
        return [], []
    if role is None:
        return [nxt], []
    if role == "mid":
        end = markers(read, pc)[-1]
        return [end + length(read, end)], []
    if role == "close":
        return [nxt, *([loops[pc]] if pc in loops else [])], []
    found = markers(read, pc)
    end = found[-1]
    bodies = [m + length(read, m) for m in found[:-1]]
    match op.shape:
        case Shape.IF:
            return [nxt, bodies[0] if bodies else end + length(read, end)], found
        case Shape.REPEAT:
            loops[end] = nxt
            return [nxt], found
        case Shape.SWITCH:
            default = any(read(m + 1) == 2 for m in found)
            return [*bodies, *([] if default else [end + length(read, end)])], found
        case Shape.ZONE:
            return [nxt, *bodies], found
    return bodies, found


def decode(read: Reader, entry: int) -> Routine:
    """The routine at `entry`: every instruction a path from it reaches, and the markers of
    every block it opens. A path ends at a goto or an `end`; bytes that are no instruction, and
    a block with no end, are problems and not guesses. What was decoded before them is kept."""
    insns: dict[int, Insn] = {}
    problems: list[Problem] = []
    blocks: list[tuple[int, int]] = []
    loops: dict[int, int] = {}
    todo = [entry]
    while todo:
        pc = todo.pop()
        while pc not in insns:
            try:
                ins = fetch(read, pc)
            except (BadScript, Unmapped) as e:
                problems.append(_problem(e, pc))
                break
            try:
                succ, found = _flow(read, ins, loops)
            except (BadScript, Unmapped) as e:
                problems.append(_problem(e, pc))
                succ, found = [pc + ins.length], []
            if found:
                blocks.append((pc, found[-1]))
            insns[pc] = replace(ins, succ=tuple(succ))
            todo += found + succ[1:]
            if not succ:
                break
            pc = succ[0]
    placed = tuple(replace(i, depth=_depth(i, blocks)) for _, i in sorted(insns.items()))
    return Routine(entry, placed, tuple(dict.fromkeys(problems)))


def _problem(e: BadScript | Unmapped, pc: int) -> Problem:
    return e.problem if isinstance(e, BadScript) else Problem("unmapped", pc, str(e))


def _depth(ins: Insn, blocks: list[tuple[int, int]]) -> int:
    inside = sum(start < ins.pc < stop for start, stop in blocks)
    return inside - (ins.role == "mid")


@dataclass(frozen=True)
class Slot:
    """A header field: what its list holds."""

    name: str
    kind: str
    """routines, walk (routines among stage arrays) or data."""
    interrupt: int = 0
    """The bit of the pending-interrupt byte whose handler is the list's first routine."""


SLOTS = (
    Slot("base", "routines"),
    Slot("call", "routines"),
    Slot("walk", "walk"),
    Slot("int_0x10", "routines", 0x10),
    Slot("int_0x20", "routines", 0x20),
    Slot("stages", "data"),
    Slot("loop", "routines"),
    Slot("pick", "data"),
    Slot("int_0x02", "routines", 0x02),
    Slot("ret", "routines"),
    Slot("int_0x04", "routines", 0x04),
    Slot("int_0x08", "routines", 0x08),
    Slot("node", "routines"),
    Slot("int_0x80", "routines", 0x80),
    Slot("int_0x40", "routines", 0x40),
)
"""Header fields 0 to 14, then one routine list per `call_deep` table."""
_FIRST_DEEP = len(SLOTS)


def slot(field: int) -> Slot:
    return SLOTS[field] if field < _FIRST_DEEP else Slot(f"deep{field - _FIRST_DEEP}", "routines")


@dataclass(frozen=True)
class Source:
    """Where an entry point came from: a header field and an index in its list."""

    field: int
    index: int

    def __str__(self) -> str:
        return f"{slot(self.field).name}[{self.index}]"


def _plausible(word: int, window: range) -> bool:
    """A header word is empty or points past the code, into the overlay's data."""
    return word == 0 or word in window


@dataclass(frozen=True)
class Header:
    """A species' script header: pointer fields, then the words of each list."""

    va: int
    fields: tuple[int, ...]
    """The pointer fields, 0 for none; floats follow them in memory."""
    lists: Mapping[int, tuple[int, ...]]
    """The words of each list field, up to the next list or the header. A list has no
    terminator: a slot that is 0 is unused."""

    @classmethod
    def read(cls, ovl: Overlay, va: int) -> Header:
        """The header at `va`, in the overlay that holds it."""
        window = range(ovl.text.stop, ovl.bss.stop)
        words = [ovl.u32(va + 4 * i) for i in range(min(_MAX_FIELDS, (ovl.end - va) // 4))]
        valid = next((i for i, w in enumerate(words) if not _plausible(w, window)), len(words))
        if valid < _FIRST_DEEP or not words[0]:
            raise BadScript("structure", va, f"0x{va:08X} is not a script header")
        last = max(i for i in range(valid) if words[i] or i < _FIRST_DEEP)
        fields = tuple(words[: last + 1])
        bounds = sorted({va, ovl.initialised.stop, *(w for w in fields if w)})
        lists = {}
        for f, p in enumerate(fields):
            if p and p < ovl.initialised.stop and slot(f).kind != "data":
                stop = next((b for b in bounds if b > p), ovl.initialised.stop)
                n = 0
                while p + 4 * n < stop and _plausible(ovl.u32(p + 4 * n), window):
                    n += 1
                lists[f] = tuple(ovl.u32(p + 4 * i) for i in range(n))
        return cls(va, fields, lists)

    def entries(self) -> list[tuple[Source, int]]:
        """Every nonzero slot of the routine lists and the walk list, in header order."""
        return [(Source(f, i), w) for f, ws in self.lists.items() for i, w in enumerate(ws) if w]

    @property
    def interrupts(self) -> dict[int, int]:
        """The routine each pending-interrupt bit starts, by bit."""
        return {
            slot(f).interrupt: ws[0]
            for f, ws in self.lists.items()
            if slot(f).interrupt and ws and ws[0]
        }

    def target(self, ref: Ref) -> int | None:
        """The entry a reference names; None when its slot is empty or past its list."""
        ws = self.lists.get(ref.field, ())
        return (ws[ref.index] or None) if ref.index < len(ws) else None


@dataclass(frozen=True)
class Stats:
    """What a set of routines is made of; plain counts, `as_dict` is JSON."""

    routines: int = 0
    instructions: int = 0
    """All of them: an else, an end and a case marker count here and in no other count."""
    unknown: int = 0
    """Opcodes the game's dispatch has no case for."""
    unterminated: int = 0
    """Routines with a problem."""
    by_kind: Mapping[str, int] = field(default_factory=dict)
    by_op: Mapping[int, int] = field(default_factory=dict)
    enters: Mapping[tuple[int, int], int] = field(default_factory=dict)
    """The (main, sub) pairs `enter` asks for, and how often."""
    conditions: Mapping[int, int] = field(default_factory=dict)
    """Tests by opcode, an if or a switch counted where it opens."""

    @classmethod
    def of(cls, routines: Iterable[Routine]) -> Stats:
        """The routines' counts; an instruction two routines share counts once."""
        rs = list(routines)
        seen: dict[int, Insn] = {}
        for r in rs:
            seen |= r.at
        real = [i for i in seen.values() if i.role not in ("mid", "close")]
        enters = Counter((i.args["main"], i.args["sub"]) for i in real if i.op.name == "enter")
        tests = Counter(i.op.code for i in real if i.kind == "condition")
        return cls(
            len(rs),
            len(seen),
            sum(r.unknown for r in rs),
            sum(not r.clean for r in rs),
            dict(Counter(i.kind for i in real)),
            dict(sorted(Counter(i.op.code for i in real).items())),
            dict(sorted(enters.items())),
            dict(sorted(tests.items())),
        )

    def as_dict(self) -> dict[str, object]:
        def named(counts: Mapping[int, int]) -> dict[str, object]:
            return {f"0x{c:02X}": {"name": OPCODES[c].name, "count": n} for c, n in counts.items()}

        return {
            "routines": self.routines,
            "instructions": self.instructions,
            "unknown": self.unknown,
            "unterminated": self.unterminated,
            "by_kind": dict(self.by_kind),
            "by_op": named(self.by_op),
            "enters": {f"{m},{s}": n for (m, s), n in self.enters.items()},
            "conditions": named(self.conditions),
        }


@dataclass(frozen=True)
class Listing:
    """Every routine one header reaches."""

    header: Header
    routines: Mapping[int, Routine]
    """By entry address."""
    sources: Mapping[int, tuple[Source, ...]]
    """The header slots that hold each entry, and the `walk` slots that name it."""
    stages: tuple[int, ...] = ()
    """The stage arrays `walk` names: data, not code."""
    unresolved: tuple[tuple[int, Ref], ...] = ()
    """References whose slot is empty or past its list, with the instruction's address."""
    idle: tuple[Source, ...] = ()
    """Walk-list slots nothing names: unused scripts and stage arrays."""

    def pick(self, entries: Iterable[int] | None = None) -> list[Routine]:
        """The routines at these entries, by address; by default all of them."""
        return [self.routines[e] for e in sorted(self.routines if entries is None else entries)]

    def stats(self, entries: Iterable[int] | None = None) -> Stats:
        return Stats.of(self.pick(entries))

    def target(self, ref: Ref) -> int | None:
        return self.header.target(ref)

    def text(self, entries: Iterable[int] | None = None) -> str:
        """The listing of the routines at these entries, by default every one."""
        out = []
        for r in self.pick(entries):
            tags = ", ".join(map(str, self.sources.get(r.entry, ())))
            head = f"; routine 0x{r.entry:08X}" + (f"  {tags}" if tags else "")
            out.append(head + ("" if r.clean else "  UNCLEAN"))
            out += [self.line(i) for i in r.insns]
            out += [f"; {p.kind}: {p.text}" for p in r.problems]
            out.append("")
        return "\n".join(out)

    def line(self, ins: Insn) -> str:
        args = "" if ins.op.flow is Flow.END else " ".join(f"{k}={v}" for k, v in ins.args.items())
        text = f"{ins.pc:08X}  {ins.raw.hex(' '):<20s}  {'  ' * ins.depth}{ins.mnemonic} {args}"
        for ref in ins.refs:
            t = self.target(ref)
            text += f"  ; {ref.kind} -> " + ("?" if t is None else f"{t:08X}")
        return text.rstrip()

    def to_dict(self, entries: Iterable[int] | None = None) -> dict[str, object]:
        return {
            "header": self.header.va,
            "interrupts": {f"0x{b:02X}": e for b, e in sorted(self.header.interrupts.items())},
            "routines": [
                {
                    "entry": r.entry,
                    "sources": [str(s) for s in self.sources.get(r.entry, ())],
                    "clean": r.clean,
                    "problems": [vars(p) for p in r.problems],
                    "insns": [self._insn(i) for i in r.insns],
                }
                for r in self.pick(entries)
            ],
            "stats": self.stats(entries).as_dict(),
        }

    def _insn(self, i: Insn) -> dict[str, object]:
        return {
            "pc": i.pc,
            "op": i.op.code,
            "name": i.mnemonic,
            "sub": i.sub,
            "raw": i.raw.hex(),
            "args": dict(i.args),
            "length": i.length,
            "kind": i.kind,
            "depth": i.depth,
            "succ": list(i.succ),
            "refs": [
                {"kind": r.kind, "field": r.field, "index": r.index, "target": self.target(r)}
                for r in i.refs
            ],
        }


def disassemble(read: Reader, header: Header) -> Listing:
    """Every routine the header's lists name, and the walk routines those routines name."""
    sources: dict[int, list[Source]] = {}
    for src, w in header.entries():
        if slot(src.field).kind == "routines":
            sources.setdefault(w, []).append(src)
    routines: dict[int, Routine] = {}
    stages: set[int] = set()
    named: set[tuple[int, int]] = set()
    unresolved: list[tuple[int, Ref]] = []
    todo = list(sources)
    while todo:
        entry = todo.pop()
        if entry in routines:
            continue
        routines[entry] = decode(read, entry)
        for ins in routines[entry].insns:
            for ref in ins.refs:
                t = header.target(ref)
                if t is None:
                    unresolved.append((ins.pc, ref))
                    continue
                if ref.kind == "stages":
                    stages.add(t)
                    named.add((ref.field, ref.index))
                elif ref.kind == "walk":
                    named.add((ref.field, ref.index))
                    if Source(ref.field, ref.index) not in sources.setdefault(t, []):
                        sources[t].append(Source(ref.field, ref.index))
                    todo.append(t)
    for e, r in routines.items():
        into = [x for x in routines if x != e and x in r.at]
        if into:
            texts = ", ".join(f"0x{x:08X}" for x in into)
            routines[e] = replace(
                r,
                problems=(*r.problems, Problem("overlap", e, f"runs into the routine at {texts}")),
            )
    idle = tuple(
        src
        for src, _ in header.entries()
        if slot(src.field).kind == "walk" and (src.field, src.index) not in named
    )
    return Listing(
        header,
        dict(sorted(routines.items())),
        {e: tuple(sorted(ss, key=lambda s: (s.field, s.index))) for e, ss in sources.items()},
        tuple(sorted(stages)),
        tuple(unresolved),
        idle,
    )


def merge(listings: Iterable[Listing]) -> Stats:
    """The counts over several headers' listings; a routine two of them reach counts once."""
    routines: dict[int, Routine] = {}
    for lst in listings:
        routines |= lst.routines
    return Stats.of(routines.values())


class Scripts:
    """The AI scripts of one species overlay, read from the extracted game."""

    def __init__(self, game: Extracted, species: int) -> None:
        self.species = species
        self.ovl = game.em(species)
        self.task = game.overlay(GAME_TASK)
        self.mem = Space([self.task, self.ovl])

    def header_at(self, variant: int, row: int | None = None) -> int:
        """The header a species row has for a map type. `row` is a species id, by default this
        overlay's own: an overlay also serves the rows of its sibling species (em75: 76, 81,
        88), and some rows' headers sit in game_task."""
        if not 0 <= variant < VARIANTS:
            raise ValueError(f"map type {variant} is not 0..{VARIANTS - 1}")
        species = self.species if row is None else row
        try:
            return self.task.u32(a.SCRIPT_HEADERS + species * a.SPECIES.stride + 4 * variant)
        except Unmapped:
            raise ValueError(f"species row {species} is not in the table") from None

    def variants(self, row: int | None = None) -> dict[int, tuple[int, ...]]:
        """The distinct headers of a row, with the map types that use each."""
        out: dict[int, list[int]] = {}
        for v in range(VARIANTS):
            out.setdefault(self.header_at(v, row), []).append(v)
        return {va: tuple(vs) for va, vs in out.items()}

    def header(self, variant: int = 0, row: int | None = None) -> Header:
        va = self.header_at(variant, row)
        holder = next((o for o in (self.ovl, self.task) if va in o), None)
        if holder is None:
            raise ValueError(f"0x{va:08X} is in neither game_task nor em{self.species:02d}")
        return Header.read(holder, va)

    def disassemble(self, variant: int = 0, row: int | None = None) -> Listing:
        return disassemble(self.mem.u8, self.header(variant, row))

    def listings(self, row: int | None = None) -> dict[int, Listing]:
        """One listing per distinct header of the row, by header address."""
        return {va: self.disassemble(vs[0], row) for va, vs in self.variants(row).items()}

    def stats(self, row: int | None = None) -> Stats:
        return merge(self.listings(row).values())
