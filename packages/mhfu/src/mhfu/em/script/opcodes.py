# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The AI script's opcode table, the one place that says how long an instruction is, what its
operands mean and which of them index a header list. Decoding, encoding, the checks and the
source text all read it.

An operand layout is a run of tokens, `name[:u16|:s8][@list]`: `u8` unless a type says otherwise,
a `u16` a big-endian pair, and `@list` the header list the value indexes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from functools import cache


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

NUMBERED = frozenset({Shape.RANDOM, Shape.BUCKET, Shape.ZONE})
"""Shapes whose markers count 1, 2, 3 by position: the encoder numbers them."""
COUNTED = frozenset({Shape.SWITCH, Shape.RANDOM, Shape.BUCKET})
"""Shapes whose header has an operand `n` that counts the markers."""
COUNT = "n"


def derived_count(shape: Shape, subs: Sequence[int]) -> int | None:
    """The header's `n` from the arms' sub-codes: a switch's cases, a random's branches, one
    less than a bucket's markers; None for a shape with no count."""
    match shape:
        case Shape.SWITCH:
            return sum(s == 1 for s in subs)
        case Shape.RANDOM:
            return len(subs)
        case Shape.BUCKET:
            return len(subs) - 1
    return None


def marker_sub(shape: Shape, sub: int, position: int) -> int:
    """The sub-code byte of the arm at `position` (from 0) whose kind is `sub`."""
    return position + 1 if shape in NUMBERED else sub


@dataclass(frozen=True)
class Operand:
    name: str
    size: int = 1
    signed: bool = False
    ref: str | None = None
    """The header list the value indexes: a SLOTS name, or `deep` for the list `table` names."""


@cache
def operands(layout: str) -> tuple[Operand, ...]:
    out = []
    for tok in layout.split():
        body, _, ref = tok.partition("@")
        name, _, kind = body.partition(":")
        out.append(Operand(name, 2 if kind == "u16" else 1, kind == "s8", ref or None))
    return tuple(out)


def _size(layout: str) -> int:
    return sum(o.size for o in operands(layout))


class Flow(Enum):
    NEXT = "next"
    CALL = "call"
    """Runs another routine, which returns to the next instruction."""
    GOTO = "goto"
    """Continues in another routine and does not come back."""
    END = "end"
    """Returns, or ends the thread; the kind says where."""


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
FIRST_DEEP = len(SLOTS)
LISTS = {s.name: i for i, s in enumerate(SLOTS)}
"""The header field of each named list."""


def slot(field: int) -> Slot:
    return SLOTS[field] if field < FIRST_DEEP else Slot(f"deep{field - FIRST_DEEP}", "routines")


@dataclass(frozen=True)
class Ref:
    """An instruction that names a routine by list and index."""

    kind: str
    """goto, call, walk (a routine of the walk list), stages (a stage array of the walk list,
    which is data) or route (an entry of a route's lists)."""
    field: int
    index: int


_REF_KINDS = {
    "base": "goto",
    "call": "call",
    "ret": "call",
    "deep": "call",
    "walk": "walk",
    "stages": "route",
    "loop": "route",
}


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
    by: Mapping[int, str] = field(default_factory=dict)
    """The operand layout of a plain opcode by the byte after it, where it differs from `args`."""
    ordered: bool = False
    """A switch whose scan stops at the first case above the value: cases must ascend."""
    resume: bool = False
    """Saves the address after the instruction as the run's resume address."""
    fixed: tuple[Ref, ...] = ()
    """The routines it names with no operand."""

    @property
    def opener(self) -> tuple[Operand, ...]:
        """A block opener's operands as the tree keeps them: all but a counted shape's `n`."""
        return tuple(
            o for o in operands(self.args) if not (o.name == COUNT and self.shape in COUNTED)
        )

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

    def layout(self, key: int | None) -> str:
        """The operand layout of the instruction `key` selects: a block's sub-code, or for a
        plain opcode the byte after it."""
        if not self.coded:
            return self.by.get(key, self.args) if key is not None else self.args
        if key == 0:
            return self.args
        marks = BLOCKS[self.shape]
        return self.label if key is not None and marks.get(key) in _LABELLED else ""

    def plain_layout(self, args: Mapping[str, object]) -> str:
        """The layout of a plain opcode with these operands."""
        if self.by:
            key = args.get(operands(self.args)[0].name)
            return self.layout(key if isinstance(key, int) else None)
        return self.args

    def length_of(self, args: Mapping[str, object]) -> int:
        """Bytes of a plain instruction with these operands."""
        return 1 + _size(self.plain_layout(args))

    def refs(self, args: Mapping[str, object], sub: int | None = None) -> tuple[Ref, ...]:
        """The list entries an instruction names, from its operands' `@list` tags; an operand
        that is not a number yet is left out."""
        if self.coded and sub != 0:
            return ()
        layout = self.args if self.coded else self.plain_layout(args)
        out = list(self.fixed)
        for o in operands(layout):
            if o.ref is None:
                continue
            index, table = args.get(o.name), args.get("table", 0)
            if not isinstance(index, int) or not isinstance(table, int):
                continue
            at = FIRST_DEEP + table if o.ref == "deep" else LISTS[o.ref]
            out.append(Ref("stages" if o.name == "stages" else _REF_KINDS[o.ref], at, index))
        return tuple(out)


def _op(
    code: int,
    name: str,
    kind: str,
    args: str = "",
    *,
    shape: Shape = Shape.PLAIN,
    label: str = "",
    by: Mapping[int, str] | None = None,
    flow: Flow = Flow.NEXT,
    marker: int | None = None,
    ordered: bool = False,
    resume: bool = False,
    fixed: tuple[Ref, ...] = (),
) -> Op:
    """An Op whose lengths follow from its layouts; `by` gives the layout of a plain opcode by
    the byte after it."""
    layouts = dict(by or {})
    if shape is Shape.PLAIN:
        lengths = {k: 1 + _size(v) for k, v in layouts.items()}
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
        layouts,
        ordered,
        resume,
        fixed,
    )


C, F, S, A = "condition", "flow", "state", "action"
_IF, _SW = Shape.IF, Shape.SWITCH

OPCODES: Mapping[int, Op] = {
    o.code: o
    for o in (
        _op(0x01, "if_provoked", C, shape=_IF),
        _op(0x02, "if_aware", C, shape=_IF),
        _op(0x03, "if_goal_elsewhere", C, shape=_IF),
        _op(0x04, "goto_base", F, flow=Flow.GOTO, fixed=(Ref("goto", 0, 0),)),
        _op(0x05, "enter", A, "main sub gate", resume=True),
        _op(0x06, "set_target", S, "kind a b", by={3: "kind a section:u16"}),
        _op(0x07, "goto", F, "routine@base", flow=Flow.GOTO),
        _op(0x08, "if_mode0", C, shape=_IF),
        _op(0x09, "if_mode2", C, shape=_IF),
        _op(0x0A, "set_aux", S, "value"),
        _op(0x0B, "if_combat_mode", C, "mode", shape=_IF),
        _op(0x0C, "set_var", S, "var value"),
        _op(0x0D, "reset_var", S, "var"),
        _op(0x0E, "if_in_section", C, "section:u16", shape=_IF),
        _op(
            0x0F,
            "walk",
            F,
            "mode limit stages@walk step@walk arrive@walk",
            flow=Flow.CALL,
            resume=True,
        ),
        _op(0x10, "walk_next", S),
        _op(0x11, "target_provoker", S),
        _op(0x12, "target_last_aware", S),
        _op(0x13, "target_remembered", S),
        _op(0x14, "if_off_facing", C, "deg", shape=_IF),
        _op(0x15, "switch_section", C, "n", shape=_SW, label="section:u16"),
        _op(0x16, "call_ret", F, "routine@ret", flow=Flow.CALL),
        _op(0x17, "route_arm", S, "list@stages count body@loop loop"),
        _op(0x18, "route_run", F, flow=Flow.CALL),
        _op(0x19, "target_saved_section", S),
        _op(0x1A, "target_hop", S, "section:u16"),
        _op(0x1B, "if_counter", C, "value", shape=_IF),
        _op(0x1C, "switch_hp", C, "n", shape=_SW, label="case"),
        _op(0x1D, "switch_counter", C, "n", shape=_SW, label="case", ordered=True),
        _op(0x1E, "clear_counters", S),
        _op(0x1F, "if_mode1", C, shape=_IF),
        _op(0x20, "switch_yaw", C, "n", shape=_SW, label="case"),
        _op(0x21, "if_gauge_a_low", C, shape=_IF),
        _op(0x22, "if_dist_flat", C, "dist", shape=_IF),
        _op(0x24, "repeat", F, "count", shape=Shape.REPEAT),
        _op(0x25, "reset_repeat", S),
        _op(0x26, "set_lock", S, "value"),
        _op(0x27, "switch_flags16", C, "n", shape=_SW, label="case", ordered=True),
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
        _op(0x56, "switch_var5f3", C, "n", shape=_SW, label="case", ordered=True),
        _op(0x57, "switch_class", C, "n", shape=_SW, label="case", ordered=True),
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
        _op(0x70, "switch_species", C, "n", shape=_SW, label="case", ordered=True),
        _op(0x71, "if_class_at_least", C, "min", shape=_IF),
        _op(0x75, "switch_var492", C, "n", shape=_SW, label="case", ordered=True),
        _op(0x76, "switch_zero", C, "n", shape=_SW, label="case", ordered=True),
        _op(0x77, "if_area_unmapped", C, shape=_IF),
        _op(0x78, "if_target_in_arc", C, "lo hi", shape=_IF),
        _op(0x79, "switch_query", C, "n arg", shape=_SW, label="case", ordered=True),
        _op(0x7A, "switch_area", C, "n", shape=_SW, label="case", ordered=True),
        _op(0x7B, "rng_step", S),
        _op(0x7C, "if_area_listed", C, shape=_IF),
        _op(0x7D, "if_never", C, shape=_IF),
        _op(0x7E, "switch_dist", C, "n", shape=_SW, label="case", ordered=True),
        _op(0x80, "random", "random", "n", shape=Shape.RANDOM, label="weight"),
        _op(0x81, "call", F, "routine@call", flow=Flow.CALL),
        _op(0x82, "call_deep", F, "table routine@deep", flow=Flow.CALL),
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
        _op(0x94, "switch_enemy_var", C, "n", shape=_SW, label="case", ordered=True),
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

BY_NAME: Mapping[str, Op] = {o.name: o for o in OPCODES.values()}
