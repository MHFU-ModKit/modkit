# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The editor's words for the AI script: what kind of node each opcode is, how it is titled,
what its operands and arms are called, and how sure we are. Keyed by `opcodes.OPCODES`, which
stays the one place a length, a layout or a mnemonic lives.

    from mhfu.em.script import catalogue as cat
    cat.kind_of(node), cat.title(node), cat.summary(node)
    for f in cat.operands(node): f.label, f.text, f.meaning
    cat.ports(block).arms                         # one name per arm, in order
    cat.end_kind(0xFC), cat.interrupt(0x20), cat.list_info(4), cat.ENTRY_POINTS
    w = cat.Words(pairs, routines, vars=...)      # names for the numbers, passed to all of them
    cat.gloss(node, w), cat.hint(routine, w)      # a code line's words; what a routine does

A native node's wording is the opcode's; only a few things depend on the operands: a pick of
two arms with one empty is a `Chance` (no native pick is one, `edit.random` builds them), a
switch's case names carry the case value. A stage is an "area" and a map row a "map", as the
player and the map workspace say.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from typing import NamedTuple

from ... import addresses
from ...stage import STAGE_NAMES, row_name, stage_id
from . import draw
from .ir import Arm, Block, Insn, Node, Routine, Sym, Value, walk
from .opcodes import BY_NAME, END_KINDS, OPCODES, SLOTS, Flow, Op, Operand, Shape, slot
from .opcodes import operands as layout_operands

__all__ = [
    "ANY",
    "BEYOND",
    "DRAW",
    "END_KIND_INFO",
    "ENTRIES",
    "ENTRY_POINTS",
    "GROUPS",
    "INTERRUPTS",
    "NO_WORDS",
    "VARS",
    "Chance",
    "Confidence",
    "EndKind",
    "Entry",
    "EntryPoint",
    "Field",
    "Fmt",
    "Interrupt",
    "Kind",
    "ListInfo",
    "OperandInfo",
    "Pairs",
    "Ports",
    "Words",
    "arm_operands",
    "chance",
    "confidence",
    "end_kind",
    "end_of",
    "entries",
    "entry",
    "entry_point",
    "gloss",
    "hint",
    "interrupt",
    "kind_of",
    "list_info",
    "operands",
    "pair_text",
    "ports",
    "shares",
    "steps_draw",
    "summary",
    "title",
]

DRAW = draw.DRAW
"""Steps of a pick's draw; a branch's weight is a share of it."""
ANY = 0xFFFF
"""An area operand that is every area, or the monster's own."""
BEYOND = 5
"""The distance range past the last limit, or with no target, among `Words.ranges`."""
VARS = 9
"""Script variables 1 to 9; 0 names them all."""
Pairs = Mapping[tuple[int, int], str]
"""Move names by `(main, sub)`, from the studio's intel."""


class Kind(Enum):
    MOVE = "Move"
    PICK = "Pick"
    CALL = "Call"
    JUMP = "Jump"
    END = "End"
    IF = "If"
    SWITCH = "Switch"
    REPEAT = "Repeat"
    TARGET = "Target"
    VAR = "Variable"
    OP = "Op"

    @property
    def label(self) -> str:
        return self.value


class Confidence(IntEnum):
    """How sure the notes are of what an opcode or name means."""

    LOW = 1
    MEDIUM = 2
    HIGH = 3

    @property
    def label(self) -> str:
        return self.name.lower()


_H, _M, _L = Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW


class Fmt(Enum):
    """How an operand's value reads."""

    NUM = "num"
    HEX = "hex"
    SECTION = "section"
    """An area by its stage number; `ANY` is every area."""
    MAP = "map"
    """A map row."""
    VAR = "var"
    """A script variable."""
    DIST = "dist"
    """Units of 100."""
    DIST10 = "dist10"
    """Units of 10."""
    PERCENT = "percent"
    TURNS = "turns"
    """256ths of a turn."""
    DEG = "deg"
    WEIGHT = "weight"
    """A share of the pick's draw."""
    APPLY = "apply"
    """0 does it, any other value does nothing."""
    WORD = "word"
    """A number with a name for the values that have one."""
    END = "end"


@dataclass(frozen=True)
class OperandInfo:
    name: str
    """The operand's name in the opcode's layout."""
    label: str
    meaning: str
    fmt: Fmt = Fmt.NUM
    words: Mapping[int, str] | None = None


@dataclass(frozen=True)
class Entry:
    """One opcode as the editor shows it."""

    name: str
    """The mnemonic, the key into `OPCODES`."""
    kind: Kind
    title: str
    group: str
    """What a picker files it under."""
    meaning: str
    confidence: Confidence
    operands: tuple[OperandInfo, ...] = ()
    case: str = ""
    """A switch's case name with `{}` for the case value."""
    text: str = ""
    """The one-line form with `{operand}` and `{pair}` slots; the title and operands if empty."""

    @property
    def op(self) -> Op:
        return BY_NAME[self.name]

    def info(self, name: str) -> OperandInfo:
        return next(i for i in self.operands if i.name == name)

    def has(self, name: str) -> bool:
        return any(i.name == name for i in self.operands)


@dataclass(frozen=True)
class Field:
    """An operand of a node with its value."""

    name: str
    label: str
    meaning: str
    value: Value
    text: str
    says: bool = False
    """The text says more than the number: a name, a unit or a word."""


@dataclass(frozen=True)
class Words:
    """What the numbers of one row's script are called, by the game and by the user. No byte
    depends on them."""

    pairs: Pairs = field(default_factory=dict)
    routines: Mapping[str, str] = field(default_factory=dict)
    """The user's names of routines."""
    vars: Mapping[int, str] = field(default_factory=dict)
    """The user's names of variables 1 to 9."""
    ranges: Mapping[int, str] = field(default_factory=dict)
    """The user's names of distance ranges: 1 to 4 within that limit, `BEYOND` past them."""
    limits: Sequence[Sequence[float]] = ()
    """The row's distance limits, set 0 then set 1 (`SPECIES.RANGE_SETS`)."""
    maps: Mapping[int, tuple[int, ...]] = field(default_factory=dict)
    """The map rows each area is in (`stage.maps_of`)."""

    def routine(self, name: str) -> str:
        return self.routines.get(name) or name

    def var(self, v: int) -> str:
        """`var 4`, its name, `every variable` for 0; `reset_var` 10 clears the destination."""
        if v in self.vars:
            return self.vars[v]
        return {0: "every variable", 10: "the destination"}.get(v, f"var {v}")

    def area(self, stage: int) -> str:
        """`Snowy area 1`, or `st141` for an area the game gives no name."""
        return "any area" if stage == ANY else STAGE_NAMES.get(stage, stage_id(stage))

    def where(self, stages: Sequence[int]) -> str:
        """The map all `stages` are in, `Map 3`; empty when they share none or none is known."""
        shared: set[int] | None = None
        for s in stages:
            if s != ANY:
                rows = set(self.maps.get(s, ()))
                shared = rows if shared is None else shared & rows
        return row_name(min(shared)) if shared else ""

    def limit(self, i: int) -> float | None:
        """Distance limit `i` of set 0; None when unknown. One below 0 is skipped."""
        return self.limits[0][i] if self.limits and 0 <= i < len(self.limits[0]) else None

    def bucket(self, i: int, last: bool) -> str:
        """Arm `i` of a distance range: `≤ 450`, `beyond 5000`, with the user's name before."""
        v = self.limit(i - 1 if last else i)
        if last:
            text = "beyond" if v is None or v < 0 else f"beyond {v:g}"
        else:
            text = f"range {i + 1}" if v is None else "skipped" if v < 0 else f"\u2264 {v:g}"
        name = self.ranges.get(BEYOND if last else i + 1)
        return f"{name} {text}" if name else text


NO_WORDS = Words()


class Ports(NamedTuple):
    head: str | None
    """The port for what runs before the first arm: an if's then, a repeat's body."""
    arms: tuple[str, ...]
    """One name per arm of the block, in order."""


class Chance(NamedTuple):
    arm: int
    """The arm with a body."""
    share: int
    """Steps of the draw it gets, of `DRAW`."""


# ---- operands

_MAIN = OperandInfo("main", "Main", "Main state of the move pair")
_SUB = OperandInfo("sub", "Sub", "Sub state of the move pair")
_SECTION = OperandInfo(
    "section", "Area", "An area by its stage number (st141); any is every area", Fmt.SECTION
)
_APPLY = OperandInfo("apply", "Apply", "0 does it, any other value does nothing", Fmt.APPLY)
_VAR = OperandInfo(
    "var",
    "Variable",
    "Variable 1 to 9, 0 for all: 1 to 4 are the monster's own bytes, 5 to 9 call species hooks;"
    " resetting 10 clears the destination",
    Fmt.VAR,
)
_VALUE = OperandInfo("value", "Value", "Value to compare with")
_ZONE = OperandInfo("zone", "Zone", "Zone id to match")
_MASK = OperandInfo("mask", "Mask", "Bits a hunter's role must share", Fmt.HEX)
_CASE = OperandInfo("case", "Value", "Value this case matches")
_WAYPOINT = OperandInfo("waypoint", "Waypoint", "Index in the species' waypoint list")


def _o(
    name: str, label: str, meaning: str, fmt: Fmt = Fmt.NUM, words: Mapping[int, str] | None = None
) -> OperandInfo:
    return OperandInfo(name, label, meaning, fmt, words)


def _deg(name: str, what: str) -> OperandInfo:
    return _o(name, "Angle", f"{what} off its facing by at least this", Fmt.DEG)


_TARGET_KINDS = {
    1: "hunter",
    2: "walk stop",
    3: "area",
    7: "saved spot",
    9: "spot",
    11: "remembered hunter",
}

# ---- opcodes


def _kind(op: Op) -> Kind:
    match op.flow:
        case Flow.CALL:
            return Kind.CALL
        case Flow.GOTO:
            return Kind.JUMP
        case Flow.END:
            return Kind.END
        case Flow.NEXT:
            pass
    match op.shape:
        case Shape.IF:
            return Kind.IF
        case Shape.SWITCH | Shape.BUCKET | Shape.ZONE:
            return Kind.SWITCH
        case Shape.REPEAT:
            return Kind.REPEAT
        case Shape.RANDOM:
            return Kind.PICK
        case Shape.PLAIN:
            pass
    if op.name == "enter":
        return Kind.MOVE
    if op.name in ("set_var", "reset_var", "clear_counters"):
        return Kind.VAR
    if op.name == "set_target" or op.name.startswith(("target_", "pick_target")):
        return Kind.TARGET
    return Kind.OP


def _e(
    name: str,
    title: str,
    conf: Confidence,
    meaning: str,
    *ops: OperandInfo,
    case: str = "",
    text: str = "",
) -> Entry:
    return Entry(name, _kind(BY_NAME[name]), title, "", meaning, conf, ops, case, text)


def _in(group: str, *entries: Entry) -> list[Entry]:
    return [replace(e, group=group) for e in entries]


_ENTRIES = (
    *_in(
        "Hunter",
        _e("if_provoked", "Provoked", _M, "a hunter hit or provoked it"),
        _e("if_aware", "Aware of a hunter", _H, "knows of at least one hunter"),
        _e("if_hunter_here", "Hunter in area", _H, "a drawn hunter is in its area"),
        _e("if_hunter_special", "Hunter special", _L, "any hunter has its special field set"),
        _e("hunter_special", "Hunter special (act)", _L, "the action twin of if_hunter_special"),
        _e("if_sole_hunter", "Only one hunter", _L, "exactly one eligible hunter is there"),
        _e(
            "if_hunter_in_pair",
            "Hunter's move is",
            _M,
            "the remembered hunter is in the move pair",
            _MAIN,
            _SUB,
            text="Hunter's move is {pair}",
        ),
        _e(
            "forget_hunter_timer",
            "Clear hunter timer",
            _M,
            "zero the remembered hunter's threat count",
        ),
        _e("clear_notice", "Clear notice", _H, "zero the notice build-up of the remembered hunter"),
        _e(
            "forget_unsensed",
            "Forget unsensed",
            _M,
            "hunters not sensed this frame lose their aware timer",
            _APPLY,
            text="Forget unsensed ({apply})",
        ),
        _e("forget_all", "Forget all hunters", _H, "zero every hunter's awareness"),
        _e("if_hunter_mark", "Hunter marked", _L, "a hunter has its mark byte set"),
        _e(
            "if_role_hunter",
            "Hunter with role",
            _M,
            "a hunter has the role",
            _MASK,
            text="A hunter with role {mask}",
        ),
    ),
    *_in(
        "Area",
        _e("if_goal_elsewhere", "Goal elsewhere", _M, "its goal area is another, reachable"),
        _e(
            "if_in_section",
            "In area",
            _M,
            "it stands in the area; a night area counts as its day twin",
            _SECTION,
            text="In {section}",
        ),
        _e(
            "switch_section",
            "Own area",
            _H,
            "branch on the area it stands in, by stage number; a night area counts as its day"
            " twin, a case of any matches every area",
            _o("section", "Area", "Area this case matches; any is every area", Fmt.SECTION),
            case="{}",
        ),
        _e(
            "if_dest_section",
            "Destination is",
            _L,
            "its pending destination is the area",
            _SECTION,
            text="Destination is {section}",
        ),
        _e(
            "switch_dest_section",
            "Destination area",
            _M,
            "branch on the area it is headed for",
            _o("section", "Area", "Area this case matches; any is every area", Fmt.SECTION),
            case="to {}",
        ),
    ),
    *_in(
        "Flow",
        _e("goto_base", "Back to the top", _H, "continue at base[0] and do not return"),
        _e(
            "goto",
            "Jump to base",
            _H,
            "continue in another base routine and do not return",
            _o("routine", "Routine", "Entry in the base list"),
            text="Jump to {routine}",
        ),
        _e(
            "walk",
            "Walk route",
            _M,
            "walk stop by stop through stages, then come back",
            _o("mode", "Mode", "0 picks stops at random, 1 walks the stage array in order"),
            _o("limit", "Stops", "Stops to visit, capped by the area's table"),
            _o("stages", "Stage array", "Stage numbers to walk, in the walk list"),
            _o("step", "Step routine", "Routine run at each stop, in the walk list"),
            _o("arrive", "Arrival routine", "Routine run when done, in the walk list"),
            text="Walk route {stages}, step {step}, arrive {arrive}",
        ),
        _e(
            "call_ret",
            "Call (with return)",
            _H,
            "run a routine of the return list and come back",
            _o("routine", "Routine", "Entry in the return list"),
            text="Call {routine}",
        ),
        _e(
            "route_arm",
            "Arm route",
            _H,
            "set up a stage route for route_run",
            _o("list", "Stage list", "Which stage array to walk, in the stages list"),
            _o("count", "Stops", "How many stops; 0 means none"),
            _o("body", "Loop routine", "Routine run at each stop, in the loop list"),
            _o("loop", "Loop flag", "0 wraps around, any other value stops at the end"),
            text="Arm route {list}, {count} stops, body {body}",
        ),
        _e("route_run", "Run route", _M, "visit the armed route, its loop routine at each stop"),
        _e(
            "repeat",
            "Repeat",
            _M,
            "run the body count times; repeats do not nest",
            _o("count", "Count", "Times to run the body; 1 or more"),
            text="Repeat {count} times",
        ),
        _e("reset_repeat", "Reset repeat", _H, "reset the repeat counter"),
        _e(
            "call_spot",
            "Call spot",
            _M,
            "pick a spot in the area, aim at it and run its routine",
            _SECTION,
            _o("radius", "Radius", "How far from the area's centre a spot may be", Fmt.DIST10),
            text="Call a spot in {section} within {radius}",
        ),
        _e(
            "wait",
            "Wait",
            _H,
            "set the wait before the next run; it holds once the run ends",
            _o("frames", "Frames", "How long to wait"),
            text="Wait {frames}",
        ),
        _e("script_reset", "Reset script", _M, "reset the script so the next run starts at base 0"),
        _e("if_never", "Never", _M, "constant false: always takes the else"),
        _e(
            "call",
            "Call",
            _M,
            "run a routine of the call list and come back",
            _o("routine", "Routine", "Entry in the call list"),
            text="Call {routine}",
        ),
        _e(
            "call_deep",
            "Call deep",
            _M,
            "run a routine of a deep table and come back",
            _o("table", "Table", "Which deep table"),
            _o("routine", "Routine", "Entry in that table"),
            text="Call {routine}",
        ),
        _e("nop", "No-op", _M, "does nothing"),
        _e("nop2", "No-op (2)", _M, "does nothing"),
        _e(
            "end",
            "End",
            _M,
            "end the routine: return, restart or close an event; the kind says which",
            _o("kind", "Kind", "Where the end goes", Fmt.END),
            text="End: {kind}",
        ),
    ),
    *_in(
        "Move",
        _e(
            "enter",
            "Move",
            _H,
            "play a move pair; the run ends and resumes after it",
            _MAIN,
            _SUB,
            _o("gate", "Gate", "Freeze gate byte kept with the move; natively 0"),
            text="Move {pair}",
        ),
        _e("warp", "Warp", _H, "jump to a waypoint", _WAYPOINT, text="Warp to waypoint {waypoint}"),
        _e(
            "face_waypoint",
            "Face waypoint",
            _H,
            "turn to face a waypoint",
            _WAYPOINT,
            text="Face waypoint {waypoint}",
        ),
        _e(
            "face",
            "Face",
            _H,
            "set its facing",
            _o("yaw", "Facing", "Facing in 256ths of a turn", Fmt.TURNS),
            text="Face {yaw}",
        ),
    ),
    *_in(
        "Target",
        _e(
            "set_target",
            "Set target",
            _H,
            "choose what later moves and turns aim at",
            _o("kind", "Kind", "What kind of target", Fmt.WORD, _TARGET_KINDS),
            _o("a", "A", "0 for a hunter, 1 for a walk stop, the region of a spot"),
            _o("b", "B", "Hunter index (255 none), walk stop index or spot index"),
            _SECTION,
        ),
        _e("walk_next", "Next walk stop", _M, "pick the next stop of the walk and aim at it"),
        _e("target_provoker", "Target the provoker", _M, "aim at the hunter that provoked it"),
        _e(
            "target_last_aware",
            "Target last aware hunter",
            _M,
            "aim at the last aware hunter and remember it; also leaves a walk or sensed event",
        ),
        _e("target_remembered", "Target remembered hunter", _H, "aim at the remembered hunter"),
        _e("target_saved_section", "Target saved area", _H, "aim at the saved area"),
        _e(
            "target_hop",
            "Target next hop",
            _M,
            "aim one area hop towards the area",
            _SECTION,
            text="Target next hop to {section}",
        ),
        _e("target_saved_spot", "Target saved spot", _M, "aim at the saved spot"),
        _e("target_kind8", "Target kind 8", _M, "set the target kind to 8 and touch nothing else"),
        _e(
            "target_remembered_param",
            "Target remembered, with param",
            _M,
            "aim at the remembered hunter with a parameter",
            _o("param", "Param", "Parameter kept with the target"),
            text="Target remembered, param {param}",
        ),
        _e("if_sensed", "Target sensed", _M, "the remembered hunter is sensed this frame"),
        _e(
            "refresh_pursuit",
            "Refresh pursuit",
            _L,
            "recompute the pursuit position from the target",
        ),
        _e("pick_target", "Pick target", _M, "choose the aware hunter that noticed most"),
        _e("pick_target_drawn", "Pick drawn target", _M, "like pick_target, among drawn hunters"),
        _e("if_target_drawn", "Target drawn", _M, "the chosen target is in its area and drawn"),
        _e(
            "if_target_noticed",
            "Target noticed",
            _H,
            "the chosen target's awareness is 30000 or more",
        ),
        _e(
            "target_attacker", "Target last attacker", _M, "aim at the hunter that last attacked it"
        ),
        _e(
            "if_hunter_zone",
            "Target in zone",
            _M,
            "the chosen target stands in the zone",
            _ZONE,
            text="Target in zone {zone}",
        ),
        _e(
            "if_pursuit_valid",
            "Pursuit target valid",
            _M,
            "the pursuit target resolves to a position",
        ),
        _e("pick_target_zone", "Pick target by zone", _L, "like pick_target, by zone relation"),
        _e(
            "if_target_notice",
            "Target notice level",
            _H,
            "the chosen target's awareness reaches the level",
            _o(
                "level",
                "Level",
                "Awareness level to reach",
                Fmt.WORD,
                {0: "awareness 10000", 1: "awareness 20000", 2: "awareness 30000"},
            ),
            text="Target noticed at level {level}",
        ),
        _e(
            "pick_target_role",
            "Pick target by role",
            _M,
            "choose the aware hunter with the role that noticed most",
            _MASK,
            text="Pick target by role {mask}",
        ),
    ),
    *_in(
        "State",
        _e("if_mode0", "Mode 0", _M, "the mode byte is 0"),
        _e("if_mode2", "Mode 2", _M, "the mode byte is 2"),
        _e(
            "if_combat_mode",
            "Combat mode",
            _H,
            "its combat mode is the given one",
            _o("mode", "Mode", "Combat mode", Fmt.WORD, {0: "out of combat", 1: "in combat"}),
            text="Combat mode is {mode}",
        ),
        _e(
            "switch_hp",
            "HP percent",
            _H,
            "branch on its HP in percent; the first case with a threshold above it wins",
            _o("case", "HP", "Threshold; the case matches below it", Fmt.PERCENT),
            case="HP below {}",
        ),
        _e("if_mode1", "Mode 1", _M, "the mode byte is 1"),
        _e("if_gauge_a_low", "Gauge A low", _M, "gauge A is at or below 30 % of its maximum"),
        _e(
            "switch_flags16",
            "Flags value",
            _M,
            "branch on the 16-bit flags word at +0x4AE; cases ascend",
            _CASE,
            case="flags {}",
        ),
        _e(
            "switch_species_group",
            "Species group",
            _M,
            "branch on the group its species belongs to",
            _o("case", "Species", "A species whose group this case matches"),
            case="group of species {}",
        ),
        _e(
            "select_row",
            "Select species row",
            _M,
            "choose the species' parameter row later ops read",
            _o("row", "Row", "Parameter row number"),
            text="Select row {row}",
        ),
        _e(
            "if_in_pair",
            "Own move is",
            _H,
            "it is in the move pair now",
            _MAIN,
            _SUB,
            text="Own move is {pair}",
        ),
        _e("if_enraged", "Enraged", _H, "it is enraged"),
        _e("if_latch", "Latch set", _M, "the latch word is not 0"),
        _e(
            "set_combat",
            "Set combat",
            _H,
            "enter or leave combat; leaving forgets awareness",
            _o(
                "on",
                "Combat",
                "0 leaves, any other value enters",
                Fmt.WORD,
                {0: "leave", 1: "enter"},
            ),
            text="Combat: {on}",
        ),
        _e(
            "self_heal",
            "Self heal",
            _M,
            "the species' own healing",
            _APPLY,
            text="Self heal ({apply})",
        ),
        _e("if_flag40", "Flag 40", _M, "entity flag 0x40 is set; testing it clears it"),
        _e(
            "refill_gauge_a",
            "Refill gauge A",
            _M,
            "raise gauge A by half its max",
            _APPLY,
            text="Refill gauge A ({apply})",
        ),
        _e(
            "refill_gauge_b",
            "Refill gauge B",
            _M,
            "raise gauge B by half its max",
            _APPLY,
            text="Refill gauge B ({apply})",
        ),
        _e(
            "refill_gauge_c",
            "Refill gauge C",
            _H,
            "raise gauge C by half its max",
            _APPLY,
            text="Refill gauge C ({apply})",
        ),
        _e("if_mode4", "Mode 4", _M, "the mode byte is 4"),
        _e(
            "switch_var5f3",
            "Byte 5F3",
            _M,
            "branch on the unsigned byte at +0x5F3; cases ascend",
            _CASE,
            case="value {}",
        ),
        _e(
            "switch_class",
            "Class",
            _M,
            "branch on its class: 0 for 0 to 15, 1 for 16 to 23, 2 else; cases ascend",
            _o("case", "Class", "Class this case matches"),
            case="class {}",
        ),
        _e("if_var529", "Byte 529 set", _M, "the signed byte at +0x529 is not 0"),
        _e(
            "if_flags16_mask",
            "Flags have bits",
            _M,
            "some of the bits are set in the 16-bit flags word",
            _o("mask", "Mask", "Bits to test", Fmt.HEX),
            text="Flags have any of {mask}",
        ),
        _e("if_active", "Active", _M, "the active byte at +0x6DB is set"),
        _e(
            "switch_species",
            "Species",
            _M,
            "branch on its species; cases ascend",
            _o("case", "Species", "Species id this case matches"),
            case="species {}",
        ),
        _e(
            "if_class_at_least",
            "Class at least",
            _M,
            "its class byte is at least the value",
            _o("min", "Minimum", "Smallest class value that matches"),
            text="Class at least {min}",
        ),
        _e(
            "switch_var492",
            "Byte 492",
            _M,
            "branch on the signed byte at +0x492; cases ascend",
            _CASE,
            case="value {}",
        ),
        _e(
            "switch_zero",
            "Constant zero",
            _M,
            "branch on the constant 0, so only a case of 0 matches; cases ascend",
            _CASE,
            case="value {}",
        ),
        _e(
            "switch_query",
            "Species query",
            _M,
            "branch on the answer of the species' own query; cases ascend",
            _o("arg", "Argument", "Argument the query gets"),
            _o("case", "Answer", "Answer this case matches"),
            case="answer {}",
            text="Species query {arg}",
        ),
    ),
    *_in(
        "Variable",
        _e(
            "set_aux",
            "Set aux",
            _H,
            "set the aux byte that if_aux and switch_aux read",
            _o("value", "Value", "Value to store"),
            text="Aux = {value}",
        ),
        _e(
            "set_var",
            "Set variable",
            _H,
            "set a script variable",
            _VAR,
            _o("value", "Value", "Value to store"),
            text="{var} = {value}",
        ),
        _e(
            "reset_var",
            "Reset variable",
            _H,
            "set a script variable back to 0",
            _VAR,
            text="Reset {var}",
        ),
        _e(
            "if_counter",
            "Counter is",
            _M,
            "the counter byte at +0x544 equals the value",
            _VALUE,
            text="Counter is {value}",
        ),
        _e(
            "switch_counter",
            "Counter",
            _H,
            "branch on the counter byte at +0x545; cases ascend",
            _CASE,
            case="counter {}",
        ),
        _e("clear_counters", "Clear counters", _H, "zero the script's counters and flags"),
        _e(
            "set_lock",
            "Set lock",
            _H,
            "set the lock byte that notice and reset interrupts check",
            _o("value", "Value", "Value to store"),
            text="Lock = {value}",
        ),
        _e(
            "if_var",
            "Variable is",
            _M,
            "a script variable equals the value",
            _VAR,
            _VALUE,
            text="{var} is {value}",
        ),
        _e("if_aux", "Aux is", _H, "the aux byte equals the value", _VALUE, text="Aux is {value}"),
        _e("switch_aux", "Aux", _H, "branch on the aux byte", _CASE, case="aux {}"),
        _e("if_var3_zero", "Variable 3 is 0", _M, "script variable 3 is 0"),
    ),
    *_in(
        "Angle",
        _e(
            "if_off_facing",
            "Target off-facing",
            _M,
            "the target hunter is off its facing by at least the angle",
            _deg("deg", "The target hunter is"),
            text="Target at least {deg} off facing",
        ),
        _e(
            "switch_yaw",
            "Angle to target",
            _M,
            "branch on the angle from its facing round to the target hunter; cases ascend",
            _o("case", "Angle", "Largest angle this case matches", Fmt.TURNS),
            case="up to {}",
        ),
        _e(
            "if_pursuit_off_facing",
            "Pursuit off-facing",
            _M,
            "the pursuit target is off its facing by at least the angle",
            _deg("deg", "The pursuit target is"),
            text="Pursuit at least {deg} off facing",
        ),
        _e(
            "if_request_off_facing",
            "Request off-facing",
            _M,
            "the move request's target is off its facing by at least the angle",
            _deg("deg", "The request target is"),
            text="Request at least {deg} off facing",
        ),
        _e(
            "if_target_in_arc",
            "Target in arc",
            _M,
            "the request's hunter lies in an arc of its facing",
            _o("lo", "From", "Start of the arc", Fmt.TURNS),
            _o("hi", "To", "End of the arc", Fmt.TURNS),
            text="Target in arc {lo} to {hi}",
        ),
    ),
    *_in(
        "Distance",
        _e(
            "if_dist_flat",
            "Distance (flat)",
            _H,
            "the pursuit target is within the distance on the ground plane",
            _o("dist", "Distance", "Largest distance that matches", Fmt.DIST),
            text="Within {dist} (flat)",
        ),
        _e(
            "if_dist_3d",
            "Distance (3D)",
            _H,
            "the pursuit target is within the distance in space",
            _o("dist", "Distance", "Largest distance that matches", Fmt.DIST),
            text="Within {dist} (3D)",
        ),
        _e(
            "switch_dist",
            "Distance to target",
            _M,
            "branch on the distance to the target hunter; cases ascend",
            _o("case", "Distance", "Largest distance this case matches", Fmt.DIST),
            case="within {}",
        ),
        _e(
            "switch_range",
            "Distance range",
            _M,
            "branch on the target's distance range; the limits are species data",
        ),
    ),
    *_in(
        "Timer",
        _e("if_timer_a_done", "Timer A done", _M, "timer A has run out"),
        _e("if_timer_b_done", "Timer B done", _M, "timer B has run out"),
        _e("set_timer_b", "Set timer B", _L, "load timer B from the species' record for the area"),
    ),
    *_in(
        "World",
        _e("if_cell_flag", "Cell flag", _L, "a flag of the map cell it stands in is set"),
        _e("if_quest_flag", "Quest flag", _M, "a bit of the quest's flag word is set"),
        _e("if_valid_ground", "On valid ground", _L, "its position is on valid ground"),
        _e(
            "if_zone",
            "In zone",
            _M,
            "its own zone id is the given one",
            _ZONE,
            text="In zone {zone}",
        ),
        _e("if_stage_flag", "Stage flag", _L, "a flag of the stage manager is set"),
        _e(
            "switch_zone_relation",
            "Zone relation",
            _M,
            "branch on how its zone relates to the target's; no target is relation 3",
        ),
        _e("if_area_unmapped", "Map unmapped", _L, "the map has no entry in the map table"),
        _e(
            "switch_area",
            "Map",
            _M,
            "branch on the map it is on; a night map counts as its day twin; cases ascend",
            _o("case", "Map", "Map this case matches", Fmt.MAP),
            case="{}",
        ),
        _e("if_area_listed", "Map 9, 10 or 22", _L, "the map is 9, 10 or 22, after the map table"),
        _e(
            "switch_enemy_var",
            "Enemy manager value",
            _M,
            "branch on a word of the enemy manager; cases ascend",
            _CASE,
            case="value {}",
        ),
    ),
    *_in(
        "Partner",
        _e("if_partner", "Has a partner", _M, "it has a partner monster"),
        _e("if_partner_fighting", "Partner fighting here", _M, "its partner fights in its area"),
        _e("if_partner_here", "Partner here", _M, "its partner is in its area"),
    ),
    *_in(
        "Random",
        _e(
            "rng_step",
            "Step the draw",
            _H,
            "step the pick's draw, so the next pick sees another number",
        ),
        _e(
            "random",
            "Pick one",
            _H,
            "take one arm by weight, a draw of 32",
            _o("weight", "Weight", "Share of the draw; 0 never, 255 always", Fmt.WEIGHT),
        ),
        _e(
            "rng_step2",
            "Step the draw",
            _H,
            "step the pick's draw, so the next pick sees another number",
        ),
        _e(
            "random_shifted",
            "Pick one (shifted)",
            _M,
            "take one arm by weight, the draw shifted right first",
            _o("shift", "Shift", "Bits the draw is shifted right; 12 or more is 11"),
            _o("weight", "Weight", "Share of the draw; 0 never, 255 always", Fmt.WEIGHT),
            text="Pick one, draw shifted by {shift}",
        ),
    ),
)

ENTRIES: Mapping[str, Entry] = {e.name: e for e in _ENTRIES}
"""Every opcode's entry, by mnemonic."""
if len(ENTRIES) != len(_ENTRIES):
    raise ValueError("an opcode has two catalogue entries")


def entry(op: Op | str) -> Entry:
    return ENTRIES[op if isinstance(op, str) else op.name]


def entries(kind: Kind | None = None) -> tuple[Entry, ...]:
    """The entries of a kind, or all, in opcode order: what a picker lists."""
    return tuple(entry(o) for o in OPCODES.values() if kind is None or entry(o).kind is kind)


def kind_of(node: Node | Op) -> Kind:
    return entry(node if isinstance(node, Op) else node.op).kind


def confidence(node: Node) -> Confidence:
    if isinstance(node, Insn) and node.op.flow is Flow.END:
        kind = node.args.get("kind")
        if isinstance(kind, int):
            return end_kind(kind).confidence
    return entry(node.op).confidence


# ---- values


def _num(
    fmt: Fmt, v: int, words: Mapping[int, str] | None, w: Words = NO_WORDS
) -> tuple[str, bool]:
    """A value's text, and whether it says more than the number."""
    match fmt:
        case Fmt.HEX:
            return f"0x{v:X}", False
        case Fmt.SECTION:
            return w.area(v), True
        case Fmt.MAP:
            return row_name(v), True
        case Fmt.VAR:
            return w.var(v), v in w.vars or v in (0, 10)
        case Fmt.DIST:
            return f"{v * 100} units", True
        case Fmt.DIST10:
            return f"{v * 10} units", True
        case Fmt.PERCENT:
            return f"{v} %", True
        case Fmt.TURNS:
            return f"{v * 360 / 256:g} deg ({v}/256)", True
        case Fmt.DEG:
            return f"{v} deg", True
        case Fmt.WEIGHT:
            return ("never" if v == 0 else "always" if v == 0xFF else f"{v}/{DRAW}"), True
        case Fmt.APPLY:
            return ("apply" if v == 0 else "no-op"), True
        case Fmt.WORD:
            return (f"{v} ({words[v]})", True) if words and v in words else (str(v), False)
        case Fmt.END:
            k = end_kind(v)
            return k.title, k.title.lower() != k.name.lower()
        case Fmt.NUM:
            return str(v), False


def _ref(ref: str, v: int, args: Mapping[str, Value]) -> str:
    """A list entry as `list[index]`, a deep list by its table."""
    table = args.get("table", 0)
    return f"deep{table}[{v}]" if ref == "deep" else f"{ref}[{v}]"


def _fields(
    op: Op,
    names: Sequence[Operand],
    args: Mapping[str, Value],
    w: Words,
    refs: Mapping[str, str] | None = None,
) -> tuple[Field, ...]:
    e, out = entry(op), []
    for o in names:
        info, v = e.info(o.name), args[o.name]
        if isinstance(v, Sym):
            text, says = f"@{v.label}", False
        elif o.ref:
            routine = (refs or {}).get(o.name)
            text = _ref(o.ref, v, args) if routine is None else w.routine(routine)
            says = routine is not None
        else:
            text, says = _num(info.fmt, v, info.words, w)
        out.append(Field(o.name, info.label, info.meaning, v, text, says))
    return tuple(out)


def operands(
    node: Node, words: Words = NO_WORDS, refs: Mapping[str, str] | None = None
) -> tuple[Field, ...]:
    """The operands of an instruction, or of a block's opener, with their values. `refs` are
    the routines the list operands lead to, by operand, where one header's lists are known."""
    if isinstance(node, Insn):
        layout = layout_operands(node.op.plain_layout(node.args))
        return _fields(node.op, layout, node.args, words, refs)
    return _fields(node.op, node.op.opener, node.args, words, refs)


def arm_operands(block: Block, arm: Arm, words: Words = NO_WORDS) -> tuple[Field, ...]:
    """The operands of an arm's marker: a case's value, a branch's weight."""
    return _fields(block.op, layout_operands(block.op.layout(arm.sub)), arm.args, words)


def pair_text(main: int | str, sub: int | str, pairs: Pairs | None = None) -> str:
    """A move pair as `(3, 27)`, an own move (main `addresses.OWN_MOVE_MAIN`) as `(own 3)`, with its
    name after it when `pairs` has one."""
    name = (
        pairs.get((main, sub)) if pairs and isinstance(main, int) and isinstance(sub, int) else None
    )
    text = f"(own {sub})" if main == addresses.OWN_MOVE_MAIN else f"({main}, {sub})"
    return text + (f" {name}" if name else "")


def _where(node: Node, w: Words) -> str:
    """The map an area switch's cases are all in, `Map 3`; empty for any other node."""
    if not isinstance(node, Block) or not entry(node.op).has("section"):
        return ""
    return w.where([v for a in node.arms if isinstance(v := a.args.get("section"), int)])


def summary(node: Node, words: Words = NO_WORDS, refs: Mapping[str, str] | None = None) -> str:
    """One line for the node with its values: `Within 500 units (flat)`, `Move (3, 27)`."""
    e, fs = entry(node.op), operands(node, words, refs)
    if not e.text:
        if where := _where(node, words):
            return f"{title(node)}: {where}"
        if not fs:
            return title(node)
        return f"{title(node)}: " + ", ".join(f"{f.label} {f.text}" for f in fs)
    texts = {f.name: f.text for f in fs}
    by = {f.name: f.value for f in fs}
    if "main" in by and "sub" in by:
        texts["pair"] = pair_text(_pair(by["main"]), _pair(by["sub"]), words.pairs)
    return e.text.format_map(texts)


def _pair(v: Value) -> int | str:
    return f"@{v.label}" if isinstance(v, Sym) else v


def gloss(node: Node | Arm, words: Words = NO_WORDS, block: Block | None = None) -> str:
    """What a code line says beyond its numbers, quietly after it: `in_hunt_area = 1`,
    `Snowy area 1`, `≤ 450`; empty when it says nothing more. An arm needs its `block`. A move's
    and a link's words are the caller's (what it plays, where it leads)."""
    if isinstance(node, Arm):
        if block is None:
            return ""
        i = next((k for k, a in enumerate(block.arms) if a is node), None)
        if i is None:
            return ""
        name = ports(block, words).arms[i]
        if block.op.shape is Shape.BUCKET:
            return name
        if block.op.shape is Shape.RANDOM:  # only a share the weight does not say
            w = node.args.get("weight")
            return "" if name in ("otherwise", f"{w}/{DRAW}") else name
        fs = arm_operands(block, node, words)
        return name if any(f.says for f in fs) else ""
    if node.op.name == "enter":
        return ""
    if where := _where(node, words):
        return where
    fs = operands(node, words)
    if node.op.flow is Flow.END:
        return " ".join(f.text for f in fs if f.says)
    return summary(node, words) if any(f.says for f in fs) else ""


# ---- picks


def shares(weights: Sequence[int]) -> tuple[int, ...]:
    """The steps of the draw each branch gets (`draw.take`)."""
    return tuple(len(r) for r in draw.spans(weights))


def chance(b: Block) -> Chance | None:
    """A pick of two arms, one of them empty: the arm with a body and its share."""
    if b.op.shape is not Shape.RANDOM or len(b.arms) != 2:
        return None
    ws = draw.weights(b)
    full = [i for i, a in enumerate(b.arms) if a.body]
    if ws is None or len(full) != 1:
        return None
    return Chance(full[0], shares(ws)[full[0]])


def steps_draw(node: Node) -> bool:
    """Whether the node only steps the draw of the pick after it."""
    return isinstance(node, Insn) and node.op.name in ("rng_step", "rng_step2")


def title(node: Node) -> str:
    if isinstance(node, Block) and (c := chance(node)):
        return f"Chance {c.share}/{DRAW}"
    return entry(node.op).title


# ---- routines


def hint(r: Routine, words: Words = NO_WORDS) -> str:
    """What a routine does at a glance, for the outline: `by own area in Map 3 · plays (0, 1)`,
    `plays (1, 2), (3, 0)`, `walks a route`, `does nothing`."""
    body = [n for n in r.body if not _idle(n)]
    if not body:
        return "does nothing"
    parts = []
    if (first := next((n for n in body if isinstance(n, Block)), None)) is not None:
        parts.append(_decides(first, words))
    moves = list(
        dict.fromkeys(
            (m, s)
            for n in walk(r.body)
            if isinstance(n, Insn)
            and n.op.name == "enter"
            and isinstance(m := n.args.get("main"), int)
            and isinstance(s := n.args.get("sub"), int)
        )
    )
    if len(moves) == 1:
        parts.append(f"plays {pair_text(*moves[0], words.pairs)}")
    elif len(moves) == 2:
        parts.append("plays " + ", ".join(pair_text(*m) for m in moves))
    elif moves:
        parts.append(f"plays {len(moves)} moves")
    elif any(isinstance(n, Insn) and n.op.name == "walk" for n in walk(r.body)):
        parts.append("walks a route")
    return " \u00b7 ".join(parts) or summary(body[0], words)


def _idle(n: Node) -> bool:
    return isinstance(n, Insn) and (n.op.flow is Flow.END or n.op.name in ("nop", "nop2"))


def _decides(b: Block, words: Words) -> str:
    """What a block decides by, as a hint says it: `if Mode 2`, `picks 1 of 3`, `by map`."""
    match b.op.shape:
        case Shape.IF:
            return f"if {summary(b, words)}"
        case Shape.RANDOM:
            return _lower(title(b)) if chance(b) else f"picks 1 of {len(b.arms)}"
        case Shape.REPEAT:
            return _lower(summary(b, words))
        case _:
            where = _where(b, words)
            return f"by {_lower(title(b))}" + (f" in {where}" if where else "")


def _lower(text: str) -> str:
    """`Own area` as `own area`; `HP percent` stays."""
    return text if text[:2].isupper() else text[:1].lower() + text[1:]


# ---- ports

_MARKS = {Shape.IF: "then", Shape.REPEAT: "body"}


def ports(b: Block, words: Words = NO_WORDS) -> Ports:
    """The names of a block's ports: its head, where it has one, and each arm."""
    op, e = b.op, entry(b.op)
    ws = draw.weights(b)
    got = shares(ws) if ws else None
    c = chance(b)
    arms = []
    for i, a in enumerate(b.arms):
        match op.shape:
            case Shape.SWITCH:
                texts = (f.text for f in arm_operands(b, a, words))
                name = "otherwise" if a.sub == 2 else e.case.format(*texts)
            case Shape.RANDOM:
                if c and i != c.arm:
                    name = "otherwise"
                else:
                    name = "branch" if got is None else _num(Fmt.WEIGHT, got[i], None)[0]
            case Shape.BUCKET:
                name = words.bucket(i, i == len(b.arms) - 1)
            case Shape.ZONE:
                name = f"relation {i + 1}"
            case Shape.IF:
                name = "else"
            case Shape.PLAIN | Shape.REPEAT:
                name = "arm"
        arms.append(name)
    return Ports(_MARKS.get(op.shape), tuple(arms))


# ---- end kinds


@dataclass(frozen=True)
class EndKind:
    code: int
    name: str
    """The kind's name in `END_KINDS`."""
    title: str
    meaning: str
    confidence: Confidence
    closes: int = 0
    """The interrupt bit whose handler it ends, 0 for none."""


def _end(code: int, title: str, conf: Confidence, meaning: str, closes: int = 0) -> EndKind:
    return EndKind(code, END_KINDS[code], title, meaning, conf, closes)


_END_KINDS = (
    _end(0, "Restart", _H, "restart the script at base[0]"),
    _end(1, "Return", _H, "return from a call"),
    _end(2, "Return from deep call", _H, "return from a call_deep"),
    _end(3, "Return (call_ret)", _H, "return from a call_ret"),
    _end(4, "Route step", _L, "continue the spot route of call_spot"),
    _end(5, "Route step 2", _L, "continue the spot route of call_spot"),
    _end(0xF5, "Roaming event done", _M, "end the roaming handler and restart", 0x40),
    _end(0xF6, "Reset event done", _M, "end the reset handler and restart", 0x80),
    _end(0xF7, "Restart, forget hunter", _M, "restart at base[0] and forget the chosen hunter"),
    _end(0xF8, "Rage event done", _M, "end the rage handler, back to the routine it cut in on", 8),
    _end(0xF9, "Event 0x04 done", _M, "end the 0x04 handler, back to the routine it cut in on", 4),
    _end(0xFA, "Reposition event done", _M, "end the reposition handler and restart", 0x02),
    _end(0xFB, "Next route stop", _M, "go to the next stop of the armed route"),
    _end(0xFC, "Notice done", _M, "end the notice roar: enter combat and restart", 0x20),
    _end(
        0xFD,
        "Sensed event done",
        _M,
        "end the sensed handler, back to the routine it cut in on",
        0x10,
    ),
    _end(0xFE, "Walk step done", _L, "close in on the walk's stop, then run its step routine"),
    _end(0xFF, "Walk loop", _L, "run the next step of the walk, or return to the caller"),
)
END_KIND_INFO: Mapping[int, EndKind] = {k.code: k for k in _END_KINDS}


def end_kind(code: int) -> EndKind:
    """What an `end` of this kind does; a code the game ignores answers as ignored."""
    return END_KIND_INFO.get(
        code,
        EndKind(code, str(code), f"Ignored ({code})", "the game does not act on this kind", _H),
    )


def end_of(bit: int) -> EndKind | None:
    """The end kind that closes an interrupt's handler."""
    return next((k for k in _END_KINDS if k.closes == bit), None)


# ---- header


@dataclass(frozen=True)
class Interrupt:
    """One bit of the pending-interrupt byte."""

    bit: int
    list_name: str
    """The header list whose first routine is the handler."""
    title: str
    entry: str
    """The handler's name in the outline."""
    meaning: str
    confidence: Confidence


def _irq(bit: int, title: str, entry: str, conf: Confidence, meaning: str) -> Interrupt:
    handler = next(s.name for s in SLOTS if s.interrupt == bit)
    return Interrupt(bit, handler, title, entry, meaning, conf)


INTERRUPTS: Mapping[int, Interrupt] = {
    i.bit: i
    for i in (
        _irq(0x80, "Reset", "On reset", _M, "reset or leaving; the top priority"),
        _irq(0x40, "Roaming", "On roaming", _L, "roaming out of combat"),
        _irq(0x20, "Notice", "On notice", _H, "first notices a hunter out of combat, the roar"),
        _irq(0x10, "Sensed", "On sensed", _L, "a hunter is sensed or has provoked it"),
        _irq(0x08, "Rage start", "On enrage", _H, "rage begins, the rage roar"),
        _irq(0x04, "Other notice", "On other notice", _M, "other brain classes' notice; empty"),
        _irq(0x02, "Repositioning", "On repositioning", _L, "repositioning in combat"),
    )
}
"""By bit, highest priority first."""


def interrupt(bit: int) -> Interrupt:
    return INTERRUPTS[bit]


@dataclass(frozen=True)
class ListInfo:
    name: str
    """The list's name in `SLOTS`, `deepK` for a deep table."""
    group: str
    """What the outline files its routines under."""
    title: str
    meaning: str
    confidence: Confidence


GROUPS = ("Decisions", "Events", "Calls", "Deep calls", "Routes", "Loops", "Data")
"""The outline's groups of header lists, in the order it shows them."""
_LIST_WORDS = {
    "base": ("Decisions", "Base routines", _H, "what goto jumps among; base[0] is the top loop"),
    "call": ("Calls", "Calls", _H, "routines call runs"),
    "walk": ("Routes", "Walk routes", _M, "routines and stage arrays of walk, mixed"),
    "stages": ("Data", "Stages", _M, "stage numbers a route visits"),
    "loop": ("Loops", "Loops", _M, "loop routines of a route"),
    "pick": ("Data", "Picks", _L, "weighted stage picks"),
    "ret": ("Calls", "Calls with return", _H, "routines call_ret runs"),
    "node": ("Routes", "Route nodes", _L, "routine run on arriving at a route node"),
}


def list_info(field: int | str) -> ListInfo:
    """The words for a header list, by field number or name."""
    name = slot(field).name if isinstance(field, int) else field
    if name.startswith("int_0x"):
        i = INTERRUPTS[int(name[4:], 16)]
        return ListInfo(name, "Events", i.entry, i.meaning, i.confidence)
    if name.startswith("deep"):
        return ListInfo(name, "Deep calls", f"Deep calls {name[4:]}", "routines call_deep runs", _M)
    group, ttl, conf, meaning = _LIST_WORDS[name]
    return ListInfo(name, group, ttl, meaning, conf)


@dataclass(frozen=True)
class EntryPoint:
    """A routine the game starts on its own: the top loop or an interrupt's handler."""

    title: str
    list_name: str
    index: int
    confidence: Confidence


ENTRY_POINTS = (
    EntryPoint("Top", "base", 0, _H),
    *(EntryPoint(i.entry, i.list_name, 0, i.confidence) for i in INTERRUPTS.values()),
)
"""The top loop, then each interrupt's handler by priority."""


def entry_point(list_name: str, index: int) -> EntryPoint | None:
    return next((p for p in ENTRY_POINTS if p.list_name == list_name and p.index == index), None)
