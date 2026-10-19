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
from functools import cached_property, partial

from ... import addresses as a
from ...files import GAME_TASK, Extracted
from ...memory import Memory, Space, Unmapped
from ...overlay import Overlay
from .opcodes import (
    CLOSE,
    END_KINDS,
    FIRST_DEEP,
    OPCODES,
    Flow,
    Op,
    Ref,
    Shape,
    operands,
    slot,
)

Reader = Callable[[int], int]
"""The byte at an address."""

VARIANTS = 17
"""Headers per species row: one per map type."""
SCAN_LIMIT = 100_000
"""Instructions a scan for the end of a block steps before it is taken for a runaway."""
SENTINEL = 0x95
"""The byte a scan for the end of a block stops on; no script holds it."""
ROWS = 0x5A
"""Species rows 0 to 0x59 have a script header; the ids above are no monsters."""
_MAX_FIELDS = 64
_MIN_ROUTINES = 5
"""Fewer routines than this is not a species' script."""


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
    key = sub if op.coded else (raw[1] if op.by and len(raw) > 1 else None)
    out: dict[str, int] = {}
    at = 1 if sub is None else 2
    for o in operands(op.layout(key)):
        out[o.name] = int.from_bytes(raw[at : at + o.size], "big", signed=o.signed)
        at += o.size
    return out


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
    # a route's lists are not followed: its bodies are roots already, its stage arrays data
    refs = tuple(r for r in op.refs(args, sub) if r.kind != "route")
    return Insn(pc, op, sub, raw, args, refs=refs)


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
class Source:
    """Where an entry point came from: a header field and an index in its list."""

    field: int
    index: int

    def __str__(self) -> str:
        return f"{slot(self.field).name}[{self.index}]"


def _word(read: Reader, at: int) -> int:
    return int.from_bytes(bytes(read(at + i) for i in range(4)), "little")


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
    """The words of each routine or walk list field, up to the next list or the header. A list
    has no terminator: a slot that is 0 is unused."""
    pointers: Mapping[int, tuple[int, ...]] = field(default_factory=dict)
    """The same for every field, the data lists (stages, pick) too."""

    @classmethod
    def read(
        cls, mem: Memory | Reader, va: int, window: range | None = None, data: range | None = None
    ) -> Header:
        """The header at `va`, read through a memory or a byte reader. `window` is where its
        words may point (an overlay's data and bss by default) and `data` where its lists can
        be read (the overlay's initialised data)."""
        if window is None or data is None:
            if not isinstance(mem, Overlay):
                raise ValueError("a memory that is no overlay needs its window and data ranges")
            window = window or range(mem.text.stop, mem.bss.stop)
            data = data or range(mem.text.stop, mem.initialised.stop)
        u32 = mem.u32 if isinstance(mem, Memory) else partial(_word, mem)
        words = []
        for i in range(_MAX_FIELDS):
            try:
                words.append(u32(va + 4 * i))
            except Unmapped:
                break
        valid = next((i for i, w in enumerate(words) if not _plausible(w, window)), len(words))
        if valid < FIRST_DEEP or not words[0]:
            raise BadScript("structure", va, f"0x{va:08X} is not a script header")
        last = max(i for i in range(valid) if words[i] or i < FIRST_DEEP)
        fields = tuple(words[: last + 1])
        bounds = sorted({va, data.stop, *(w for w in fields if w)})
        pointers = {}
        for f, p in enumerate(fields):
            if p in data:
                stop = next((b for b in bounds if b > p), data.stop)
                n = 0
                while p + 4 * n < stop and _plausible(u32(p + 4 * n), window):
                    n += 1
                pointers[f] = tuple(u32(p + 4 * i) for i in range(n))
        lists = {f: ws for f, ws in pointers.items() if slot(f).kind != "data"}
        return cls(va, fields, lists, pointers)

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
        self._listings: dict[int, Listing] = {}

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

    def header_in(self, va: int) -> Header:
        """The header at `va`, in this overlay or in game_task."""
        holder = next((o for o in (self.ovl, self.task) if va in o), None)
        if holder is None:
            raise ValueError(f"0x{va:08X} is in neither game_task nor em{self.species:02d}")
        return Header.read(holder, va)

    def header(self, variant: int = 0, row: int | None = None) -> Header:
        return self.header_in(self.header_at(variant, row))

    def listing(self, va: int) -> Listing:
        """Every routine the header at `va` reaches; each header is disassembled once."""
        if va not in self._listings:
            self._listings[va] = disassemble(self.mem.u8, self.header_in(va))
        return self._listings[va]

    def disassemble(self, variant: int = 0, row: int | None = None) -> Listing:
        return self.listing(self.header_at(variant, row))

    def listings(self, row: int | None = None) -> dict[int, Listing]:
        """One listing per distinct header of the row, by header address."""
        return {va: self.listing(va) for va in self.variants(row)}

    def stats(self, row: int | None = None) -> Stats:
        return merge(self.listings(row).values())

    @cached_property
    def rows(self) -> tuple[int, ...]:
        """The species rows whose headers all sit in this overlay: its own and its siblings'. A
        row of another overlay points at the same addresses and does not decode cleanly here."""
        return tuple(r for r in range(ROWS) if self._serves(r))

    def _serves(self, row: int) -> bool:
        try:
            for va in self.variants(row):
                if va not in self.ovl.initialised:
                    return False
                first = self.header_in(va).lists[0][0]
                if not decode(self.mem.u8, first).clean:
                    return False
                s = self.listing(va).stats()
                if s.unknown or s.unterminated or s.routines < _MIN_ROUTINES:
                    return False
        except (BadScript, Unmapped, ValueError, KeyError, IndexError):
            return False
        return True
