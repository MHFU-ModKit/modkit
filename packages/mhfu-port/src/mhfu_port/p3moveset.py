# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""An MHP3rd big monster's move table, read from its em overlay.

    ms = Moveset(Overlay.from_path(".../file_05246.bin"))   # em058m0
    ms.pairs[1, 4]      # Pair: the handler, the clip ids it plays, the pairs it enters
    ms.translator       # (main, sub) -> the pairs the species' vt+0x88 can rewrite it to

The action tick (vt+0x84) switches on MAIN, a dispatcher per main on SUB, and a handler plays
clips through the executor and the record, walk, turn and reaction helpers. A clip id is
`stream * 100 + slot`, the `source` of a port manifest's clip.
"""

from __future__ import annotations

import struct
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import cached_property
from typing import Literal, NamedTuple

import rabbitizer
from mhfu.em.moveset import Site, Walker, known, loads
from mhfu.memory import Unmapped
from mhfu.mips import CALL_CLOBBERS, Code, Gpr, Register, Switch, move_source
from mhfu.overlay import Overlay
from rabbitizer import InstrId, Instruction

MAIN = 0x18C
"""ENTITY.main: the action's main state; ACT_SET alone writes it."""
SUB = 0x18D
"""ENTITY.sub: the action's sub state."""
VT_TICK = 0x84
"""The species vtable slot of the action tick: a switch on MAIN."""
VT_TRANSLATE = 0x88
"""The slot of the translator, `f(entity, u8 *main, u8 *sub)`; ACT_SET repeats it until stable."""
VT_SLOTS = range(0, 0x130, 4)
"""The vtable slots whose calls a dispatch walk records."""

EXECUTOR = 0x09CAC684  # noaddr: play_clip(entity, id, a2, a3), a tail call into CLIP_CORE
CLIP_CORE = 0x09CAB32C  # noaddr: the clip start every player ends in
PLAYERS = (
    EXECUTOR,
    0x09CAC554,  # noaddr: play_clip(entity, id, a2, a3) behind a check of the entity
    0x09CAC5E4,  # noaddr: the same, taking t0
    CLIP_CORE,
)
"""Calls that start the clip `a1`."""


class Records(NamedTuple):
    """A helper that plays clip descriptors from a record its argument points at."""

    arg: int
    """Which of ARGS holds the record."""
    offsets: tuple[int, ...]
    """Where in the record a descriptor, `{u16 clip, u16, u32, u32, u32}`, starts."""


RECORDS = {
    0x09CABAE4: Records(0, (0x10,)),  # noaddr: (entity, rec) plays one clip
    0x09CAB55C: Records(1, (0x00, 0x10, 0x20)),  # noaddr: walk (entity, flag, rec)
    0x09CAC330: Records(0, (0x10, 0x20, 0x30, 0x40)),  # noaddr: turn (entity, rec, ...)
    0x09CABFC4: Records(0, (0x10, 0x20, 0x30, 0x40)),  # noaddr: turn, a variant
    0x09CABB84: Records(0, (0x10, 0x20, 0x30, 0x40)),  # noaddr: turn, the part both share
}
DESCRIPTOR = struct.Struct("<HHIII")
"""A clip descriptor: the executor id first."""
REACTIONS = (
    0x09C7E378,  # noaddr: (entity, step list, flag, sub) plays a reaction
    0x09C7E384,  # noaddr: the same, a variant
)
STEP = 8
"""A reaction step list's row: `{u8 op, ..., u32 arg}`."""
STEPS = 64
"""Rows read from a step list before giving up on its end."""
STEP_PLAY = frozenset([0, 1, 2])
"""Ops whose arg points at a descriptor to play."""
STEP_END = frozenset([0xFE, 0xFF])
"""Ops that end a step list."""

ACT_SET = 0x09CA9FD4  # noaddr: act_set(entity, main, sub, mode): enter a pair, yields
ENTER = (
    ACT_SET,
    0x09CAA6B8,  # noaddr: act_set wrappers, mode 0
    0x09CAAB18,  # noaddr: mode 1
    0x09CAAB98,  # noaddr: mode 1
    0x09CAAC24,  # noaddr: mode 1
    0x09CAA798,  # noaddr: mode 2
    0x09CAA664,  # noaddr: mode 3
    0x09CAA718,  # noaddr: mode 5
    0x09CAAC84,  # noaddr: mode 5
    0x09CAAD94,  # noaddr: mode 0
    0x09CAAD18,  # noaddr: mode 5
)
"""Calls `f(entity, main, sub)` that enter a pair."""
END = {
    0x09CAA818: ((0, 9),),  # noaddr: the move is over, back to a rest pair
    0x09CAA87C: ((0, 8),),  # noaddr
    0x09CAA8E0: ((0, 7),),  # noaddr
    0x09CAA928: ((0, 6),),  # noaddr
    0x09CAA970: ((0, 4), (0, 5)),  # noaddr
    0x09CAAA04: ((0, 3),),  # noaddr
    0x09CAAA58: ((0, 0), (0, 1)),  # noaddr
}
"""Move-end helpers, each entering the pairs it can leave the monster in."""
ATTACK = 0x09C97F04  # noaddr: (entity, slot, record, flags) spawns an attack from a 28-byte row

PLAY, RECORD, REACT, ENTERS, ENDS, ATTACKS = "play", "record", "react", "enter", "end", "attack"
WATCH = {
    **dict.fromkeys(PLAYERS, PLAY),
    **dict.fromkeys(RECORDS, RECORD),
    **dict.fromkeys(REACTIONS, REACT),
    **dict.fromkeys(ENTER, ENTERS),
    **dict.fromkeys(END, ENDS),
    ATTACK: ATTACKS,
}
"""What a pair walk records, by call target."""
ARGS = (Gpr.a1, Gpr.a2, Gpr.a3, Gpr.t0)
"""The registers a walk reads at a call."""

CASE_SETUP = 8
"""Instructions from a case label to the call it makes."""
VARIANTS, PHASES = 2, 3
"""A handler's constant id table: a row of PHASES ids per variant."""
SUBS = 256
"""Sub values tried for each main."""
MAX_PATHS, MAX_STEPS = 64, 400
"""Bounds on one translator evaluation."""

Pairs = tuple[int, int]
"""(main, sub): what act_set takes."""
Kind = Literal["move", "reaction"]
_MASK = 0xFFFFFFFF


class Attack(NamedTuple):
    """An attack object a handler spawns; None where the argument is not a literal."""

    slot: int | None
    record: int | None
    """The row of the species' attack table (28 bytes each)."""
    flags: int | None


@dataclass(frozen=True)
class Pair:
    """A (main, sub) and what entering it does."""

    main: int
    sub: int
    handler: int | None
    """The function that runs it; the dispatcher's case label where the case runs inline."""
    clips: tuple[int, ...]
    """Executor ids it can play, from literals, descriptor records and reaction step lists.

    Id 0 is the empty descriptor and is left out."""
    computed: bool
    """Some play takes an id this walk cannot resolve."""
    kind: Kind
    """`reaction` where a step list plays the clips."""
    attacks: tuple[Attack, ...]
    """The attack objects it can spawn."""
    next: tuple[Pairs, ...]
    """The pairs it enters with literal arguments, its end helpers' rest pairs included."""
    sites: frozenset[int] = field(default_factory=frozenset)
    """The play calls it reaches."""


class _Route(NamedTuple):
    """How the tick reaches one (main, sub): the dispatchers it passes through."""

    chain: tuple[int, ...]
    """The dispatch functions below the tick, outermost first."""
    entry: tuple[int | None, ...]
    """The literal a1 to t0 of the call into the last of them; a variant rides there."""
    shape: tuple[frozenset[tuple[str, int | None]], ...]
    """The calls made at each level, as (kind, callee)."""
    quiet: bool
    """The last level makes no call."""


@dataclass
class _Found:
    """What one pair's walk reached."""

    clips: set[int] = field(default_factory=set)
    reactions: set[int] = field(default_factory=set)
    computed: bool = False
    next: set[Pairs] = field(default_factory=set)
    attacks: dict[Attack, None] = field(default_factory=dict)
    sites: set[int] = field(default_factory=set)


class Moveset:
    """The move table of one MHP3rd em overlay."""

    def __init__(self, ovl: Overlay) -> None:
        self.ovl = ovl
        self.code = Code(ovl, ovl.text)

    # --- the tick and the species vtable ---

    @cached_property
    def _calls(self) -> dict[int, int]:
        """The callee of each call site whose callee is known."""
        return {c.site: c.target for c in self.code.calls if c.target is not None}

    def _dispatcher(self, label: int) -> int | None:
        """The overlay function the case at `label` calls."""
        for i in range(CASE_SETUP):
            if (target := self._calls.get(label + 4 * i)) is not None:
                return target if target in self.code.text else None
        return None

    def _switch_on_sub(self, fn: int) -> Switch | None:
        where = self.code.function(fn)
        found = sorted((s for s in self.code.switches.values() if s.jr in where), key=_jr)
        return next((s for s in found if loads(s.operand, SUB)), None)

    @cached_property
    def tick(self) -> Switch | None:
        """The switch on MAIN whose cases reach the most switches on SUB."""
        best, most = None, -1
        for s in sorted(self.code.switches.values(), key=_jr):
            if not loads(s.operand, MAIN):
                continue
            fan = sum(
                1
                for label in s.targets
                if (d := self._dispatcher(label)) is not None and self._switch_on_sub(d)
            )
            if fan > most:
                best, most = s, fan
        return best

    @cached_property
    def _vtable(self) -> tuple[int | None, int | None]:
        """(species vtable, action tick function): the vtable holding the tick at VT_TICK.

        The tick often starts right at its load of MAIN, which no call or prologue marks as an
        entry, so that address is tried after the function's start."""
        if self.tick is None or self.tick.operand is None:
            return None, None
        op, d = self.tick.operand.vram, self.ovl.initialised
        words = struct.unpack(f"<{len(d) // 4}I", self.ovl.read(d.start, len(d) // 4 * 4))
        for entry in (self.code.function(op).start, op):
            for i, w in enumerate(words):
                va = d.start + 4 * i
                if w == entry and va - VT_TICK >= d.start:
                    if self.ovl.u32(va - VT_TICK) in self.ovl.text:
                        return va - VT_TICK, entry
        return None, op

    @property
    def vtable(self) -> int | None:
        """The species vtable, None where no vtable holds the tick."""
        return self._vtable[0]

    @property
    def tick_entry(self) -> int | None:
        """The action tick function."""
        return self._vtable[1]

    @property
    def main_count(self) -> int:
        """How many MAIN values the tick switches on."""
        return 0 if self.tick is None else self.tick.first + len(self.tick.targets)

    # --- the translator ---

    @cached_property
    def translator(self) -> dict[Pairs, frozenset[Pairs]]:
        """The pairs the species translator can leave in place of each it rewrites.

        Run for every main the tick switches on and every sub, along every path an unknown (an
        entity cell, a call's result) forks. A pair it leaves alone is not listed; a conditional
        rewrite lists only where it can go. A rewrite every pair of a main gets (a state override)
        is listed like the rest."""
        vt = self.vtable
        if vt is None or (fn := self.ovl.u32(vt + VT_TRANSLATE)) not in self.ovl.text:
            return {}
        run = _Translator(self.code, fn)
        out = {}
        for main in range(self.main_count):
            for sub in range(SUBS):
                if to := run(main, sub) - {(main, sub)}:
                    out[main, sub] = to
        return out

    # --- the pairs ---

    @cached_property
    def _structure(self) -> Walker:
        """Records every call a path makes, to see the dispatch shape."""
        entries = dict.fromkeys((f.start for f in self.code.functions), "fn")
        outside = dict.fromkeys(
            (c.target for c in self.code.calls if c.target and c.target not in self.code.text),
            "ext",
        )
        methods = {slot: f"vt+{slot:#x}" for slot in VT_SLOTS}
        return Walker(
            self.code, {**outside, **entries}, methods=methods, depth=0, guards=False, args=ARGS
        )

    @cached_property
    def _clips(self) -> Walker:
        return Walker(self.code, WATCH, depth=3, guards=False, args=ARGS)

    def _route(self, tick: int, main: int, sub: int) -> _Route:
        """Follow the tick's one call out of each level (later ones only by a tail call)."""
        facts = known({MAIN: main, SUB: sub})
        chain: list[int] = []
        entry: tuple[int | None, ...] = ()
        shape = []
        cur = tick
        while True:
            sites = self._structure.run(cur, facts=facts)
            shape.append(frozenset((s.kind, self._calls.get(s.site)) for s in sites))
            onward: dict[int, tuple[int | None, ...]] = {}
            for s in sites:
                ins = self.code.at(s.site)
                if not ins.isJumpWithAddress():
                    continue
                to = ins.getInstrIndexAsVram()
                if to in self.code.text and to not in self.code.function(tick):
                    if cur == tick or ins.uniqueId == InstrId.cpu_j:
                        onward.setdefault(to, s.args)
            if len(onward) != 1 or (cur := next(iter(onward))) in chain:
                return _Route(tuple(chain), entry, tuple(shape), not sites)
            chain.append(cur)
            entry = onward[cur]

    @cached_property
    def _rows(self) -> dict[Pairs, Pair]:
        """The pairs the tick dispatches to a handler of their own, without the translator's.

        A sub the switch has no case for takes the default, the shape most subs of a main share;
        the rest are pairs."""
        tick = self.tick_entry
        if tick is None:
            return {}
        out = {}
        for main in range(self.main_count):
            routes = [self._route(tick, main, sub) for sub in range(SUBS)]
            default = Counter((r.chain, r.shape) for r in routes).most_common(1)[0][0]
            own = [sub for sub, r in enumerate(routes) if (r.chain, r.shape) != default]
            firsts = {routes[sub].chain[0] for sub in own if routes[sub].chain}
            for sub in own:
                r = routes[sub]
                if not r.chain and r.quiet:
                    continue
                handler: int | None = None
                if len(r.chain) >= 2 or (len(r.chain) == 1 and len(firsts) > 1):
                    handler = r.chain[-1]
                elif r.chain:
                    handler = self._case(r.chain[0], sub)
                sites = self._clips.run(tick, facts=known({MAIN: main, SUB: sub}))
                out[main, sub] = _pair(main, sub, handler, self._read(sites, r.entry))
        return out

    def _case(self, dispatcher: int, sub: int) -> int | None:
        """The label of `sub` in a dispatcher's switch on SUB."""
        sw = self._switch_on_sub(dispatcher)
        i = sub - sw.first if sw is not None else -1
        return sw.targets[i] if sw is not None and 0 <= i < len(sw.targets) else None

    @cached_property
    def pairs(self) -> dict[Pairs, Pair]:
        """The pairs the tick dispatches, and each pair without a handler that the translator
        rewrites, which takes its destination's handler and clips. A rewrite most of a main's subs
        share is that main's override, not an alias: no rows. Sorted by (main, sub)."""
        rows = dict(self._rows)
        reach = Counter((src[0], dests) for src, dests in self.translator.items())
        for src, dests in self.translator.items():
            into = [rows[d] for d in sorted(dests) if d in rows]
            if src not in rows and into and reach[src[0], dests] * 2 <= SUBS:
                rows[src] = _alias(src, into)
        return dict(sorted(rows.items()))

    @cached_property
    def sites(self) -> frozenset[int]:
        """Every call in the overlay that plays a clip."""
        plays = {*PLAYERS, *RECORDS, *REACTIONS}
        return frozenset(c.site for c in self.code.calls if c.target in plays)

    @property
    def orphans(self) -> frozenset[int]:
        """The play calls no pair's handler reaches."""
        return self.sites - frozenset().union(*(p.sites for p in self._rows.values()))

    # --- what a walk found ---

    def _read(self, sites: Iterable[Site], entry: tuple[int | None, ...]) -> _Found:
        """Sort a pair walk's calls into clips, reactions, attacks and the pairs entered."""
        f = _Found()
        for s in sites:
            a = s.args
            if s.kind == PLAY:
                f.sites.add(s.site)
                if a[0] is not None:
                    f.clips.add(a[0])
                elif (ids := self._id_from_table(s.site, entry)) is not None:
                    f.clips.update(ids)
                else:
                    f.computed = True
            elif s.kind == RECORD:
                helper = RECORDS[self._calls[s.site]]
                ids = self._descriptors(a[helper.arg], helper.offsets)
                f.sites.add(s.site)
                if ids is None:
                    f.computed = True
                else:
                    f.clips.update(ids)
            elif s.kind == REACT:
                f.sites.add(s.site)
                if a[0] is None or a[0] not in self.ovl.initialised:
                    f.computed = True
                else:
                    f.reactions.update(self._steps(a[0]))
            elif s.kind == ATTACKS:
                f.attacks[Attack(*a[:3])] = None
            elif s.kind == ENTERS:
                if a[0] is not None and a[1] is not None:
                    f.next.add((a[0], a[1]))
            elif s.kind == ENDS:
                f.next.update(END[self._calls[s.site]])
        return f

    def _descriptors(self, rec: int | None, offsets: tuple[int, ...]) -> list[int] | None:
        """The clip ids of the descriptors at `offsets` of the record at `rec`; None if the
        record is not a literal."""
        if rec is None or rec not in self.ovl.initialised:
            return None
        found = (DESCRIPTOR.unpack(self.ovl.read(rec + off, DESCRIPTOR.size)) for off in offsets)
        return [d[0] for d in found if any(d)]

    def _steps(self, table: int) -> list[int]:
        """The clip ids a reaction step list plays, up to its end step."""
        ids = []
        for i in range(STEPS):
            va = table + STEP * i
            op = self.ovl.u8(va)
            arg = self.ovl.u32(va + 4)
            if op in STEP_END:
                break
            if op in STEP_PLAY and arg in self.ovl.initialised:
                ids.append(self.ovl.u16(arg))
        return ids

    def _id_from_table(self, site: int, entry: tuple[int | None, ...]) -> list[int] | None:
        """The ids of a play whose id is a byte of the constant table its handler copies to its
        stack at entry, indexed `variant * PHASES + phase`: the variant's row, or the whole table
        where the call into the handler passes none."""
        if not _loaded(self.code, site, Gpr.a1):
            return None
        fn = self.code.function(site)
        for ins in self.code.span(range(fn.start, fn.start + 4 * 24)):
            if ins.uniqueId != InstrId.cpu_lw or ins.rs == Gpr.sp:
                continue
            base = self.code.constant(ins.vram, ins.rs)
            spills = [
                i
                for i in self.code.span(range(ins.vram, ins.vram + 4 * 12))
                if i.doesStore() and i.rs == Gpr.sp and i.getProcessedImmediate() == 0
            ]
            if base is None or not spills:
                continue
            table = self.ovl.read((base + ins.getProcessedImmediate()) & _MASK, VARIANTS * PHASES)
            variant = entry[0] if entry else None
            if variant is not None and variant < VARIANTS:
                return list(table[variant * PHASES : (variant + 1) * PHASES])
            return list(table)
        return None


def _jr(s: Switch) -> int:
    return s.jr


def _loaded(code: Code, site: int, reg: Register) -> bool:
    """`reg` at `site` is a load, possibly masked or moved on the way."""
    for _ in range(8):
        ins = code.source(site, reg)
        if ins is None or ins.doesLink():
            return False
        if ins.doesLoad():
            return True
        if ins.uniqueId == InstrId.cpu_andi or (
            ins.uniqueId == InstrId.cpu_addiu and ins.getProcessedImmediate() == 0
        ):
            reg, site = ins.rs, ins.vram
        elif (moved := move_source(ins)) is not None:
            reg, site = moved, ins.vram
        else:
            return False
    return False


def _pair(main: int, sub: int, handler: int | None, f: _Found) -> Pair:
    return Pair(
        main,
        sub,
        handler,
        tuple(sorted((f.clips | f.reactions) - {0})),
        f.computed,
        "reaction" if f.reactions else "move",
        tuple(f.attacks),
        tuple(sorted(f.next)),
        frozenset(f.sites),
    )


def _alias(src: Pairs, into: list[Pair]) -> Pair:
    """The pair `src` stands for when the translator rewrites it into one of `into`."""
    first = into[0]
    return Pair(
        *src,
        first.handler if len({p.handler for p in into}) == 1 else None,
        tuple(sorted({c for p in into for c in p.clips})),
        any(p.computed for p in into),
        "reaction" if any(p.kind == "reaction" for p in into) else "move",
        tuple(dict.fromkeys(a for p in into for a in p.attacks)),
        tuple(sorted({n for p in into for n in p.next})),
        frozenset().union(*(p.sites for p in into)),
    )


# --- the translator, run on one pair ---


class _Sym:
    """A value the translator reads and this run does not know."""


_Value = int | _Sym
_Op = Callable[[int, int], int]
_IMM: dict[rabbitizer.Enum, _Op] = {
    InstrId.cpu_addiu: lambda x, i: x + i,
    InstrId.cpu_addi: lambda x, i: x + i,
    InstrId.cpu_andi: lambda x, i: x & i,
    InstrId.cpu_ori: lambda x, i: x | i,
    InstrId.cpu_xori: lambda x, i: x ^ i,
    InstrId.cpu_slti: lambda x, i: int(_signed(x) < _signed(i)),
    InstrId.cpu_sltiu: lambda x, i: int(x < i & _MASK),
}
_REG: dict[rabbitizer.Enum, _Op] = {
    InstrId.cpu_addu: lambda x, y: x + y,
    InstrId.cpu_add: lambda x, y: x + y,
    InstrId.cpu_subu: lambda x, y: x - y,
    InstrId.cpu_sub: lambda x, y: x - y,
    InstrId.cpu_and: lambda x, y: x & y,
    InstrId.cpu_or: lambda x, y: x | y,
    InstrId.cpu_xor: lambda x, y: x ^ y,
    InstrId.cpu_slt: lambda x, y: int(_signed(x) < _signed(y)),
    InstrId.cpu_sltu: lambda x, y: int(x < y),
}
_SHIFT: dict[rabbitizer.Enum, _Op] = {
    InstrId.cpu_sll: lambda x, s: x << s,
    InstrId.cpu_srl: lambda x, s: x >> s,
    InstrId.cpu_sra: lambda x, s: _signed(x) >> s,
}
_EQUAL: dict[rabbitizer.Enum, bool] = {
    InstrId.cpu_beq: True,
    InstrId.cpu_beql: True,
    InstrId.cpu_beqz: True,
    InstrId.cpu_bne: False,
    InstrId.cpu_bnel: False,
    InstrId.cpu_bnez: False,
}
"""Branches comparing two values: taken on equal when True."""
_ZERO = frozenset([InstrId.cpu_beqz, InstrId.cpu_bnez])
_SIGN: dict[rabbitizer.Enum, Callable[[int], bool]] = {
    InstrId.cpu_blez: lambda x: x <= 0,
    InstrId.cpu_blezl: lambda x: x <= 0,
    InstrId.cpu_bgtz: lambda x: x > 0,
    InstrId.cpu_bgtzl: lambda x: x > 0,
    InstrId.cpu_bltz: lambda x: x < 0,
    InstrId.cpu_bltzl: lambda x: x < 0,
    InstrId.cpu_bgez: lambda x: x >= 0,
    InstrId.cpu_bgezl: lambda x: x >= 0,
}
"""Branches comparing a value with zero."""
_AT_MAIN, _AT_SUB = 0x1000, 0x1004
"""Where the run puts the two bytes the translator gets pointers to."""


def _signed(x: int) -> int:
    x &= _MASK
    return x - (1 << 32) if x & 0x80000000 else x


@dataclass
class _State:
    pc: int
    regs: dict[Register, _Value]
    cells: dict[int, _Value]
    seen: set[int] = field(default_factory=set)

    def fork(self, pc: int) -> _State:
        return _State(pc, dict(self.regs), dict(self.cells), set(self.seen))

    def get(self, reg: Register) -> _Value:
        if reg == Gpr.zero:
            return 0
        return self.regs.setdefault(reg, _Sym())

    def learn(self, sym: _Sym, value: int) -> None:
        """A branch showed the unknown `sym` equals `value`."""
        for reg in [r for r, v in self.regs.items() if v is sym]:
            self.regs[reg] = value
        for at in [a for a, v in self.cells.items() if v is sym]:
            self.cells[at] = value


class _Translator:
    """The species translator, `f(entity, u8 *main, u8 *sub)`, interpreted on one pair.

    It knows the two bytes and what it computes from literals; entity cells and call results are
    unknown, a branch on one forks, and the equal side learns its value. A pc runs once per path."""

    def __init__(self, code: Code, fn: int) -> None:
        self.code, self.fn = code, fn

    def __call__(self, main: int, sub: int) -> frozenset[Pairs]:
        """The (main, sub) pairs the translator can end with for this input."""
        out: set[Pairs] = set()
        start = _State(self.fn, {Gpr.a1: _AT_MAIN, Gpr.a2: _AT_SUB}, {_AT_MAIN: main, _AT_SUB: sub})
        work, paths = [start], 0
        while work and paths < MAX_PATHS:
            paths += 1
            p = work.pop()
            self._walk(p, work)
            if isinstance(m := p.cells[_AT_MAIN], int) and isinstance(s := p.cells[_AT_SUB], int):
                out.add((m, s))
        return frozenset(out)

    def _walk(self, p: _State, work: list[_State]) -> None:
        text = self.code.text
        while p.pc in text and p.pc not in p.seen and len(p.seen) < MAX_STEPS:
            p.seen.add(p.pc)
            ins = self.code.at(p.pc)
            if not ins.hasDelaySlot():
                self._exec(ins, p)
                p.pc += 4
                continue
            slot = self.code.at(p.pc + 4)
            if ins.isBranch():
                self._branch(ins, slot, p, work)
            elif ins.isReturn():
                self._exec(slot, p)
                return
            elif ins.isJumpWithAddress():
                to = ins.getInstrIndexAsVram()
                self._exec(slot, p)
                if ins.doesLink():
                    self._call(p)
                    p.pc += 8
                elif to in self.code.function(self.fn):
                    p.pc = to
                else:
                    return
            else:
                where = p.get(ins.rs)
                self._exec(slot, p)
                if ins.doesLink():
                    self._call(p)
                    p.pc += 8
                elif isinstance(where, int):
                    p.pc = where
                else:
                    return

    def _call(self, p: _State) -> None:
        for reg in CALL_CLOBBERS:
            p.regs.pop(reg, None)

    def _branch(self, ins: Instruction, slot: Instruction, p: _State, work: list[_State]) -> None:
        target = ins.getBranchVramGeneric()
        if ins.isUnconditionalBranch():
            self._exec(slot, p)
            p.pc = target
            return
        taken, learn_taken, learn_fell = self._cond(ins, p)
        likely = ins.isBranchLikely()
        if taken is not None:
            if taken or not likely:
                self._exec(slot, p)
            p.pc = target if taken else p.pc + 8
            return
        side = p.fork(target)
        if learn_taken:
            side.learn(*learn_taken)
        if learn_fell:
            p.learn(*learn_fell)
        self._exec(slot, side)
        work.append(side)
        if not likely:
            self._exec(slot, p)
        p.pc += 8

    def _cond(
        self, ins: Instruction, p: _State
    ) -> tuple[bool | None, tuple[_Sym, int] | None, tuple[_Sym, int] | None]:
        """(whether it is taken, if known; what the taken side learns; what the other learns)."""
        u = ins.uniqueId
        if u in _EQUAL:
            x = p.get(ins.rs)
            y = 0 if u in _ZERO else p.get(ins.rt)
            if isinstance(x, int) and isinstance(y, int):
                return (x == y) == _EQUAL[u], None, None
            sym, lit = (x, y) if isinstance(x, _Sym) else (y, x)
            fact = (sym, lit) if isinstance(sym, _Sym) and isinstance(lit, int) else None
            return (None, fact, None) if _EQUAL[u] else (None, None, fact)
        x = p.get(ins.rs)
        if u in _SIGN and isinstance(x, int):
            return _SIGN[u](_signed(x)), None, None
        return None, None, None

    def _exec(self, ins: Instruction, p: _State) -> None:
        """One non-control instruction; what it writes that is not known becomes unknown."""
        if ins.isNop():
            return
        if ins.doesStore():
            base = p.get(ins.rs)
            if ins.uniqueId == InstrId.cpu_sb and isinstance(base, int):
                at = (base + ins.getProcessedImmediate()) & _MASK
                if at in p.cells:
                    v = p.get(ins.rt)
                    p.cells[at] = v & 0xFF if isinstance(v, int) else v
            return
        dest = ins.getDestinationGpr()
        if dest is not None and dest != Gpr.zero:
            p.regs[dest] = self._value(ins, p)

    def _value(self, ins: Instruction, p: _State) -> _Value:
        u = ins.uniqueId
        if ins.doesLoad():
            base = p.get(ins.rs)
            if isinstance(base, int):
                at = (base + ins.getProcessedImmediate()) & _MASK
                if u in (InstrId.cpu_lbu, InstrId.cpu_lb) and at in p.cells:
                    return p.cells[at]
                if u == InstrId.cpu_lw:
                    try:
                        return self.code.mem.u32(at)
                    except Unmapped:
                        pass
            return _Sym()
        if (moved := move_source(ins)) is not None:
            return p.get(moved)
        if u == InstrId.cpu_lui:
            return (ins.getProcessedImmediate() << 16) & _MASK
        if u in _IMM and isinstance(x := p.get(ins.rs), int):
            return _IMM[u](x, ins.getProcessedImmediate()) & _MASK
        if u in _REG:
            x, y = p.get(ins.rs), p.get(ins.rt)
            if isinstance(x, int) and isinstance(y, int):
                return _REG[u](x, y) & _MASK
        if u in _SHIFT and isinstance(x := p.get(ins.rt), int):
            return _SHIFT[u](x, ins.sa) & _MASK
        return _Sym()
