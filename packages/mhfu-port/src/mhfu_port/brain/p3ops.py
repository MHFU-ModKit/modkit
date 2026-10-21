# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""MHP3rd's AI script opcodes, the one place that says what each is called, which operands it has
and how long it is. Decoding, encoding and the text all read it.

An instruction is an opcode byte and its operands, all `u8`. A block opcode (any kind but
`Kind.PLAIN`) has a sub-code byte after it: 0 opens the block, 1 is a case marker that carries
one value byte, 2 is an else or default, 3 a break, 0xFF ends it. Any other sub-code is a marker
without operands. Nothing else tells the length: `length` is the game's own rule.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

LENGTH_TABLE = 0x09D76E70  # noaddr: MHP3rd game_task; the game's per-opcode byte table
"""Where the game keeps the lengths this table is checked against (tests only: the code does
not read it). Entry < 0x80: that many operand bytes. Entry >= 0x80: a block, whose opener has
`(entry + 1) & 0xF` bytes after the opcode, sub-code included."""
NUM_OPS = 0x70
"""Opcodes 0x00 to 0x6F have an entry; 0x70 to 0xFE are invalid."""
ENDS = (0x10, 0xFF)
"""The end instruction and its alias, one byte each."""

OPEN, CASE, ELSE, BREAK, CLOSE = 0, 1, 2, 3, 0xFF
"""The sub-codes of a block: the opener, a case (the only marker with an operand), else or
default, a repeat's break, and the closer."""

LISTS = (
    "base",
    "call",
    "walk",
    "event3",
    "event4",
    "event5",
    "event6",
    "event7",
    "pending",
    "death",
    "react",
    "event11",
    "trigger",
)
"""The 13 lists of a species descriptor, by number: base (routine 0 starts the script), call,
walk, interrupts (3 to 7 and 11), the pending-state pair, death, hit reaction, trigger."""
BASE, CALL, WALK = 0, 1, 2


class Kind(Enum):
    PLAIN = "plain"
    IF = "if"
    """The true branch is the body between the opener and the first marker; the else follows."""
    SWITCH = "switch"
    RANDOM = "random"
    """Branches with weights that add to 100."""
    LOOP = "loop"
    BRACKET = "bracket"
    """An opener and a closer around a body, no markers between."""


class Flow(Enum):
    NEXT = "next"
    CALL = "call"
    """Runs another routine and continues after the op."""
    END = "end"
    YIELD = "yield"
    """Ends the run; the script resumes after the op."""
    OTHER = "other"


@dataclass(frozen=True)
class P3Op:
    code: int
    name: str
    kind: Kind
    args: str
    """The operand names after the opcode of a plain op, after the sub-code of an opener."""
    case: str
    """The name of a case marker's value byte; blocks only."""
    flow: Flow
    calls: int | None
    """The list whose routine it enters (an index into LISTS): the `routine` operand, else 0."""
    doc: str

    @property
    def block(self) -> bool:
        return self.kind is not Kind.PLAIN

    @property
    def operands(self) -> tuple[str, ...]:
        return tuple(self.args.split())

    def length(self, sub: int = 0) -> int:
        """Bytes of the instruction whose byte after the opcode is `sub`; a plain op ignores it."""
        if not self.block:
            return 1 + len(self.operands)
        if sub == OPEN:
            return 2 + len(self.operands)
        return 3 if sub == CASE else 2

    def entry(self, args: Mapping[str, int]) -> tuple[int, int] | None:
        """The (list, index) a call or goto enters."""
        return None if self.calls is None else (self.calls, args.get("routine", 0))

    def marker(self, sub: int) -> str:
        """What a marker's sub-code is called in this op's block."""
        if sub == CLOSE:
            return "end"
        if sub == CASE:
            return "branch" if self.kind is Kind.RANDOM else "case"
        if sub == ELSE:
            return "else" if self.kind is Kind.IF else "default"
        return "break" if sub == BREAK else f"m{sub}"

    def mnemonic(self, sub: int | None = None) -> str:
        """The name, a marker's sub-code spelled out: `if_enraged.else`."""
        return self.name if not sub else f"{self.name}.{self.marker(sub)}"


def _plain(
    code: int,
    name: str,
    args: str = "",
    *,
    flow: Flow = Flow.NEXT,
    calls: int | None = None,
    doc: str,
) -> P3Op:
    return P3Op(code, name, Kind.PLAIN, args, "", flow, calls, doc)


def _block(
    code: int, name: str, kind: Kind, args: str = "", *, case: str = "case", doc: str
) -> P3Op:
    flow = Flow.OTHER if kind in (Kind.LOOP, Kind.BRACKET) else Flow.NEXT
    return P3Op(code, name, kind, args, case, flow, None, doc)


def _if(code: int, name: str, args: str = "", *, doc: str) -> P3Op:
    return _block(code, name, Kind.IF, args, doc=doc)


def _switch(code: int, name: str, args: str = "", *, case: str = "case", doc: str) -> P3Op:
    return _block(code, name, Kind.SWITCH, args, case=case, doc=doc)


OPS: Mapping[int, P3Op] = {
    o.code: o
    for o in (
        _plain(
            0x00,
            "enter",
            "main sub",
            flow=Flow.YIELD,
            doc="enter the move pair (main, sub), then yield; the script resumes after the op",
        ),
        _plain(
            0x01,
            "set_target",
            "kind a b",
            doc="aim later moves at a target: kind = class, a = rule, b = slot (0xFF none)",
        ),
        _block(
            0x02,
            "random",
            Kind.RANDOM,
            case="weight",
            doc="the first branch whose running weight total exceeds draw % 100",
        ),
        _plain(
            0x03,
            "set_dest",
            "mode arg",
            doc="set the pending destination: mode 1 and 2 area arg, 3 nearest, 4 the group's",
        ),
        _plain(0x04, "set_aux_c7a", "value", doc="store a byte in the aux slot +0xC7A"),
        _block(
            0x05,
            "transit",
            Kind.BRACKET,
            doc="`05 00` aims at the remembered hop area, the body walks, `05 ff` moves the entity",
        ),
        _plain(
            0x06,
            "call_list2",
            "a b",
            flow=Flow.CALL,
            calls=WALK,
            doc="set target kind 8 and run routine 0 of list 2 with (a, b), then continue",
        ),
        _switch(0x07, "switch_area", doc="branch on the area the entity stands in"),
        _switch(0x08, "switch_move_main", doc="branch on the main of the running move pair"),
        _switch(0x09, "switch_move_sub", doc="branch on the sub of the running move pair"),
        _switch(
            0x0A,
            "switch_combat_mode",
            doc="branch on the combat mode: 0 out of combat, 1 in combat",
        ),
        _switch(
            0x0B,
            "switch_range",
            case="range",
            doc="branch on the 3D distance to the pursuit target: the first range it is within",
        ),
        _switch(0x0C, "switch_mode", doc="branch on the mode byte"),
        _plain(
            0x0D,
            "goto_base",
            flow=Flow.END,
            calls=BASE,
            doc="restart at base routine 0, clearing the call stacks and the wait",
        ),
        _if(
            0x0E,
            "if_group",
            doc="true when the entity has a group record (read by ops 0x12 to 0x19)",
        ),
        _plain(
            0x0F,
            "target_spot",
            "mode dist flag",
            doc="aim at the nearest route spot of the area; mode = walk mode, dist = stop distance",
        ),
        _plain(0x10, "end_alias", flow=Flow.END, doc="end, the same op as 0xFF"),
        _if(0x11, "if_target_set", doc="true when the last set_target found a target (b != 0xFF)"),
        _switch(
            0x12,
            "switch_group_18",
            doc="branch on the group record's byte +0x18: the first case at or above it",
        ),
        _switch(
            0x13,
            "switch_member",
            "source",
            doc="branch on the group member counter `source` (0 to 2): the first case above it",
        ),
        _plain(
            0x14,
            "call",
            "routine",
            flow=Flow.CALL,
            calls=CALL,
            doc="call routine n of the call list and come back",
        ),
        _switch(0x15, "switch_group_1c", doc="branch on the group record's halfword +0x1C"),
        _switch(
            0x16,
            "switch_dist",
            case="hundreds",
            doc="branch on the 3D distance to the pursuit target: first limit hundreds * 100",
        ),
        _switch(
            0x17, "switch_dist_saved", case="hundreds", doc="as switch_dist, to the saved position"
        ),
        _switch(
            0x18,
            "switch_dist_spot",
            "spot",
            case="hundreds",
            doc="as switch_dist, the flat distance to map spot `spot`",
        ),
        _plain(0x19, "target_group", doc="aim at the target the group record names"),
        _plain(
            0x1A,
            "save_pos",
            "mode",
            doc="save the entity's own (mode 0) or the target's (1) position",
        ),
        _plain(
            0x1B,
            "set_target_dist",
            "kind a b dist flag",
            doc="set_target that stops dist * 100 short of the target (flag 1 sets walk mode 5)",
        ),
        _if(
            0x1C,
            "if_enraged",
            doc="true while the rage window +0xBCA is open and the entity is not in a locked pair",
        ),
        _if(
            0x1D,
            "if_facing",
            "ang",
            doc="true when the pursuit target is within ang / 256 of a turn of the facing",
        ),
        _plain(
            0x1E,
            "set_pick_range",
            "value",
            doc="limit the next set_target pick: candidates farther than value * 100 are skipped",
        ),
        _if(0x1F, "if_timer_a_done", doc="true when the countdown timer +0x59C is 0 or below"),
        _plain(
            0x20,
            "call_base",
            "routine",
            flow=Flow.CALL,
            calls=BASE,
            doc="call base routine n and come back; only from the base list",
        ),
        _if(0x21, "if_timer_running", doc="true while +0xC48 is above 0"),
        _switch(
            0x22,
            "switch_prev_area",
            doc="branch on the area the entity came from; with none, a random case",
        ),
        _switch(0x23, "switch_spawn_state", doc="branch on the byte +0x2E4 from the spawn record"),
        _if(
            0x24,
            "if_tired",
            doc="true when fatigue +0xBBF is 2 or 3 (tired, recovering), not in a locked pair",
        ),
        _if(
            0x25,
            "if_row_area",
            doc="true when the enemy-manager row exists and holds the entity's area",
        ),
        _switch(
            0x26,
            "switch_row_0c",
            doc="branch on the s16 at +0xC of the enemy-manager row; no row skips it",
        ),
        _if(
            0x27,
            "if_row_set",
            doc="true when the enemy-manager row exists and its byte +0x2D is not 0xFF",
        ),
        _if(0x28, "if_flag380_8", doc="true when bit 3 of the flag word +0x380 is set"),
        _switch(0x29, "switch_variant", doc="branch on the variant byte +0x2E3"),
        _switch(
            0x2A, "switch_map", doc="branch on the quest map id; a night map matches its day twin"
        ),
        _plain(0x2B, "warp", "point", doc="teleport to map point `point` of the entity's area"),
        _plain(
            0x2C, "face_angles", "pitch yaw roll", doc="set the rotation to (pitch, yaw, roll) << 8"
        ),
        _plain(0x2D, "face_point", "point", doc="face map point `point`"),
        _if(
            0x2E,
            "if_target_side",
            doc="true when the pursuit target is on the positive-angle half of the facing",
        ),
        _plain(
            0x2F, "wait", "frames", flow=Flow.YIELD, doc="wait `frames` script ticks, then resume"
        ),
        _plain(0x30, "recover", doc="end fatigue: refill stamina, top up a rage gauge over 80 %"),
        _block(
            0x31,
            "repeat",
            Kind.LOOP,
            "mode count",
            doc="loop: mode 0 jumps back at the end marker, 1 `count` times; marker 3 breaks",
        ),
        _if(
            0x32,
            "if_in_row_zone",
            doc="true when the entity is inside its enemy-manager row's zone",
        ),
        _switch(0x33, "switch_pitch", doc="branch on the elevation angle to the pursuit target"),
        _if(
            0x34, "if_timer_time", "min sec", doc="true when the elapsed timer is at least min:sec"
        ),
        _if(0x35, "if_hp_low", doc="true when HP percent is at most the species' threshold"),
        _plain(0x36, "walk", "route flags", doc="walk route `route` of the species' route table"),
        _plain(0x37, "reset_timer", doc="elapsed timer +0x5A2 := 0"),
        _if(0x38, "if_timer_done", doc="true when the elapsed timer has reached its limit"),
        _plain(0x39, "nop", doc="no operation; pads an empty last random branch"),
        _if(0x3A, "if_object_near", doc="true when a stage object is in reach; then targets it"),
        _if(0x3B, "if_never", doc="always false"),
        _plain(0x3C, "set_var2", "value", doc="var 2 (+0xECC) := value"),
        _switch(0x3D, "switch_aux", doc="branch on var 2 (+0xECC)"),
        _switch(0x3E, "switch_row_15", doc="branch on the byte +0x15 of the enemy-manager row"),
        _if(0x3F, "if_intent", doc="true when an area-change intent is pending (+0x3A3)"),
        _switch(0x40, "switch_intent", doc="branch on the pending intent's type +0x3A4"),
        _if(
            0x41,
            "if_stamina_low",
            "pct10",
            doc="true when stamina +0xBC2 is at most pct10 * 10, or the monster is tired",
        ),
        _if(0x42, "if_flag_bcc", doc="true when +0xBCC is non-zero"),
        _if(
            0x43,
            "if_hunter_aware",
            doc="true when a hunter is flagged in the entity's hunter bit sets",
        ),
        _switch(
            0x44,
            "switch_variant_bits",
            doc="branch on bits of the variant byte: (+0x2E3 & value) != 0",
        ),
        _switch(
            0x45,
            "switch_row_17_le",
            doc="branch on the byte +0x17 of the enemy-manager row: first case at or above",
        ),
        _switch(0x46, "switch_species", doc="branch on the monster id"),
        _switch(0x47, "switch_38e", doc="branch on the byte +0x38E"),
        _plain(0x48, "flag_next", doc="guard flag: the next set_target keeps a still-valid target"),
        _switch(0x49, "switch_next_area", doc="branch on the next-hop area +0xC78"),
        _switch(
            0x4A,
            "switch_roll",
            case="weight",
            doc="the first branch whose running weight total exceeds the percent roll +0x39A",
        ),
        _if(
            0x4B,
            "if_target_state",
            "sel param",
            doc="true when the current target passes test `sel` (`param` for some)",
        ),
        _if(
            0x4C,
            "if_line_clear",
            "src spot",
            doc="true when a collision sweep to the target finds nothing; src picks the target",
        ),
        _plain(
            0x4D,
            "route_step",
            "arrive",
            flow=Flow.CALL,
            doc="route walker: runs the armed route's node routines; `arrive` != 0 skips the hook",
        ),
        _if(0x4E, "if_route_armed", doc="true when a route is armed (+0xC84)"),
        _plain(
            0x4F,
            "walk_next",
            doc="species hook; by default advances the armed route to its next node",
        ),
        _if(0x50, "if_timer_c6e", doc="true while the timer +0xC6E is above 0"),
        _switch(0x51, "switch_role", doc="branch on the own role byte (+0x132 & 0x7F)"),
        _switch(
            0x52, "switch_event_param", doc="branch on the first byte of the pending event record"
        ),
        _if(0x53, "if_flag_bcd", doc="true when +0xBCD is set"),
        _if(0x54, "if_flag_3a8", doc="true when +0x3A8 is non-zero"),
        _plain(
            0x55,
            "set_target_index",
            "index",
            doc="set_target's index b (+0x4DA) := index; the kind stays",
        ),
        _switch(
            0x56, "switch_var_c79", doc="branch on the byte +0xC79 (destination area, 0xFF none)"
        ),
        _switch(
            0x57,
            "switch_range_flat",
            case="range",
            doc="as switch_range, the flat distance to the pursuit target",
        ),
        _switch(0x58, "switch_dist_flat", case="hundreds", doc="as switch_dist, the flat distance"),
        _if(0x59, "if_hunter_here", doc="true when a hunter stands in the entity's area"),
        _plain(0x5A, "set_var", "var value", doc="script variable `var` := value"),
        _switch(0x5B, "switch_var", "var", doc="branch on script variable `var`"),
        _switch(
            0x5C,
            "switch_dist_saved_flat",
            case="hundreds",
            doc="as switch_dist_flat, to the saved position",
        ),
        _plain(0x5D, "set_flag_eb8", doc="set the byte +0xEB8"),
        _if(0x5E, "if_in_dest_area", doc="true when the own area is the destination area"),
        _switch(
            0x5F,
            "switch_hp",
            case="percent",
            doc="branch on HP percent: the first case at or above it",
        ),
        _plain(0x60, "set_aux", "value", doc="byte +0x390 := value"),
        _if(0x61, "if_travelling", doc="true when a trip to another area is under way (+0x3A0)"),
        _if(0x62, "if_flag_table", "flag", doc="true when progress flag `flag` is set"),
        _switch(0x63, "switch_class", doc="branch on the spawn record's class byte +0x2ED"),
        _plain(
            0x64,
            "set_latch",
            "value",
            doc="interpreter register := value; only switch_latch reads it",
        ),
        _switch(0x65, "switch_latch", doc="branch on the latch: a compile-time on/off switch"),
        _if(0x66, "if_flag_3ac", doc="true when +0x3AC is non-zero"),
        _if(
            0x67,
            "if_same_species_present",
            "param",
            doc="true when a live slot record has the entity's species (and `param`, if non-zero)",
        ),
        _if(0x68, "if_flag_bd5_0", doc="true when bit 0 of +0xBD5 is set: fatigue began"),
        _if(0x69, "if_flag_bd5_1", doc="true when bit 1 of +0xBD5 is set"),
        _switch(0x6A, "switch_global_540", doc="branch on halfword +0x540 of the global object"),
        _plain(0x6B, "clear_counters", doc="zero +0x4E8 to +0x4EB and reset +0x585"),
        _if(
            0x6C,
            "if_target_in_arc",
            "lo hi",
            doc="true when the target's bearing minus the facing is in [lo, hi] << 8",
        ),
        _switch(0x6D, "switch_target_kind", doc="branch on the target kind +0x4D8"),
        _switch(
            0x6E, "switch_prev_call", doc="branch on the id of the routine called before this one"
        ),
        _switch(
            0x6F, "switch_query", "arg", doc="branch on the species' own query, called with `arg`"
        ),
        _plain(
            0xFF,
            "end",
            flow=Flow.END,
            doc="end the routine; what follows depends on the list it runs from",
        ),
    )
}
"""Every opcode the game's dispatch gives a length: 0x00 to 0x6F and 0xFF."""

BY_NAME: Mapping[str, P3Op] = {o.name: o for o in OPS.values()}
"""Opcodes by name; the names are unique."""


def length(code: int, sub: int = 0) -> int:
    """Bytes of the instruction at opcode `code` followed by `sub`; ValueError if invalid."""
    op = OPS.get(code)
    if op is None:
        raise ValueError(f"opcode 0x{code:02X}")
    return op.length(sub)
