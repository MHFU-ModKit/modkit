# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A big monster watched while the game runs: its entity snapshotted and diffed across pair
changes, and the engine functions its species overlay calls, each call put to the (main, sub)
pair the monster was in.

    obs = Observer(s, Engine.load(Extracted.find()), 75)
    run = obs.run(list(obs.callees), seconds=30, state=path)
    print(report([run]))

Four instruments, all log-only, so the game keeps running:
- `targets`: a breakpoint on each callee, kept when $ra returns into the overlay's text;
- `indirect`: one on each of the overlay's register calls (`jalr`, `jr` tail calls), logging
  the register, so the callee is what the register held;
- `engine`: one on any function, kept when a register (a0 by default) holds the monster, which
  sees what the engine does to it with no overlay frame in between (movement, collision);
- `writes`: a watchpoint on entity fields, logging the pc of each write.
Pair changes come from a breakpoint on SET_AI_STATE, the one writer of the pair, and snapshots
from one read of the entity each, which stops the CPU for one request.
"""

from __future__ import annotations

import collections
import csv
import math
import statistics
import struct
import threading
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from types import TracebackType
from typing import Any

from ppsspp_debug import Disconnected, FrameStats, Hit, SyncStream

from .. import addresses as a
from .. import symbols
from ..em.abi import Engine
from ..entries import input_action
from ..mips import Code
from ..views import layout
from .session import Session

SNAPSHOT_SIZE = 0x800
"""Bytes of a big-monster entity one snapshot holds."""
WRAP = 1 << 32
"""PPSSPP's `usec` is the emulated clock in microseconds, cut to 32 bits."""
DRAIN = 0.3
"""Seconds the log of a call takes to arrive, with room to spare."""

Pair = tuple[int, int]
_U32 = struct.Struct("<I")


def outbound(code: Code) -> dict[int, tuple[int, ...]]:
    """Callee outside `code`'s text -> its `jal` sites in it, the most-called callee first."""
    sites: dict[int, list[int]] = {}
    for call in code.calls:
        if call.link and call.target is not None and call.target not in code.text:
            sites.setdefault(call.target, []).append(call.site)
    return {t: tuple(s) for t, s in sorted(sites.items(), key=lambda kv: (-len(kv[1]), kv[0]))}


def indirect(code: Code) -> dict[int, str]:
    """`code`'s register calls whose target static analysis cannot see: site -> register."""
    return {c.site: code.at(c.site).rs.name for c in code.calls if c.target is None}


def _pair_ref(entity: int) -> str:
    return f"{{[{entity + a.ENTITY.MAIN_STATE:#x},2]}}"


def log_format(entity: int, register: str | None = None) -> str:
    """What each breakpoint logs: the clock, $ra, the entity's pair as one u16, a0..a3, and
    for a register call the register."""
    tail = f" {{{register}}}" if register else ""
    return f"{{usec}} {{ra}} {_pair_ref(entity)} {{a0}} {{a1}} {{a2}} {{a3}}{tail}"


def write_format(entity: int) -> str:
    """What each watchpoint logs: the clock, the writing pc, the entity's pair."""
    return f"{{usec}} {{pc}} {_pair_ref(entity)}"


def _unpair(word: int) -> Pair:
    return word & 0xFF, word >> 8


@dataclass(frozen=True)
class Call:
    """A logged call: `site` is the `jal` ($ra - 8) or the register call, `pair` the monster's
    at entry, `t` the emulated microseconds since its run began."""

    callee: int
    site: int
    pair: Pair
    t: int
    args: tuple[int, int, int, int]


def parse(hit: Hit, start: int) -> Call | None:
    """A hit logged in `log_format`; None for any other line."""
    try:
        usec, ra, pair, *args = (int(f, 16) for f in (hit.message or "").split())
    except ValueError:
        return None
    pc = hit.pc or hit.address
    if len(args) == 5:  # a register call: the callee is the register, the site the breakpoint
        callee, site = args.pop(), pc
    elif len(args) == 4:
        callee, site = pc, ra - 8
    else:
        return None
    a0, a1, a2, a3 = args
    return Call(callee, site, _unpair(pair), (usec - start) % WRAP, (a0, a1, a2, a3))


@dataclass(frozen=True)
class Write:
    """A logged write to the entity: `offset` from its base, `function` the one holding `pc`
    when known."""

    offset: int
    pc: int
    function: int | None
    pair: Pair
    t: int


def parse_write(
    hit: Hit, start: int, entity: int, function: Callable[[int], int | None] = lambda _: None
) -> Write | None:
    """A watchpoint hit logged in `write_format`; None for any other line."""
    try:
        usec, pc, pair = (int(f, 16) for f in (hit.message or "").split())
    except ValueError:
        return None
    return Write(hit.address - entity, pc, function(pc), _unpair(pair), (usec - start) % WRAP)


@dataclass(frozen=True)
class Transition:
    """SET_AI_STATE on the monster: the pair it left and the one it entered."""

    t: int
    before: Pair
    after: Pair
    site: int

    @classmethod
    def of(cls, call: Call) -> Transition:
        _, main, sub, _ = call.args
        return cls(call.t, call.pair, (main & 0xFF, sub & 0xFF), call.site)


@dataclass(frozen=True)
class Snapshot:
    t: int
    data: bytes

    @property
    def pair(self) -> Pair:
        return self.data[a.ENTITY.MAIN_STATE], self.data[a.ENTITY.SUB_STATE]


@dataclass(frozen=True)
class Change:
    """A field, or an unnamed word `+0xNNN`, that differs; `churn` if it also changes while
    the pair holds."""

    name: str
    offset: int
    before: Any
    after: Any
    churn: bool = False


@cache
def _fields() -> tuple[tuple[str, int, struct.Struct], ...]:
    out = []
    for f in a.ENTITY.fields.values():
        fmt = layout(f.type)
        if fmt is not None and f + fmt.size <= SNAPSHOT_SIZE:
            out.append((f.name, int(f), fmt))
    return tuple(sorted(out, key=lambda x: x[1]))


def _value(fmt: struct.Struct, data: bytes, offset: int) -> Any:
    values = fmt.unpack_from(data, offset)
    return values[0] if len(values) == 1 else values


def diff(before: bytes, after: bytes, churn: Collection[int] = ()) -> list[Change]:
    """The ENTITY fields that differ, then words no field covers, by offset."""
    changed = {i for i, (x, y) in enumerate(zip(before, after, strict=True)) if x != y}
    covered: set[int] = set()
    out = []
    for name, offset, fmt in _fields():
        span = range(offset, offset + fmt.size)
        covered.update(span)
        if not changed.isdisjoint(span):
            noisy = not set(churn).isdisjoint(span)
            out.append(
                Change(name, offset, _value(fmt, before, offset), _value(fmt, after, offset), noisy)
            )
    for word in sorted({i & ~3 for i in changed - covered}):
        noisy = not set(churn).isdisjoint(range(word, word + 4))
        x, y = _value(_U32, before, word), _value(_U32, after, word)
        out.append(Change(f"+0x{word:03X}", word, x, y, noisy))
    return sorted(out, key=lambda c: c.offset)


def churn(snapshots: Sequence[Snapshot]) -> frozenset[int]:
    """Byte offsets that change between consecutive snapshots of one pair."""
    out: set[int] = set()
    for x, y in zip(snapshots, snapshots[1:], strict=False):
        if x.pair == y.pair:
            out.update(i for i, (p, q) in enumerate(zip(x.data, y.data, strict=True)) if p != q)
    return frozenset(out)


@dataclass(frozen=True)
class PairChange:
    before: Snapshot
    after: Snapshot
    changes: tuple[Change, ...]


def pair_changes(snapshots: Sequence[Snapshot]) -> list[PairChange]:
    """A diff for each pair change between consecutive snapshots, churn marked."""
    noisy = churn(snapshots)
    return [
        PairChange(x, y, tuple(diff(x.data, y.data, noisy)))
        for x, y in zip(snapshots, snapshots[1:], strict=False)
        if x.pair != y.pair
    ]


@dataclass
class Run:
    """One soak: what was traced, what it logged, and the frame rate meanwhile."""

    entity: int
    targets: tuple[int, ...]
    indirect: tuple[int, ...] = ()
    """Register-call sites traced."""
    engine: tuple[int, ...] = ()
    watched: tuple[tuple[int, int], ...] = ()
    """(offset, size) spans of the entity whose writes are logged."""
    seconds: float = 0.0
    calls: list[Call] = field(default_factory=list)
    writes: list[Write] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    stats: list[FrameStats] = field(default_factory=list)
    dropped: int = 0
    unparsed: int = 0

    def traced(self) -> set[int]:
        """Callees this run would have seen: those armed, and what its register calls hit."""
        seen = {c.callee for c in self.calls if c.site in self.indirect}
        return set(self.targets) | set(self.engine) | seen

    def timeline(self) -> list[tuple[Pair, int, int]]:
        """(pair, from, to) in emulated microseconds, from the first snapshot and each
        transition."""
        if not self.snapshots:
            return []
        end = self.snapshots[-1].t
        out, pair, since = [], self.snapshots[0].pair, self.snapshots[0].t
        for tr in sorted(self.transitions, key=lambda x: x.t):
            if tr.t > end:
                break
            out.append((pair, since, tr.t))
            pair, since = tr.after, tr.t
        out.append((pair, since, end))
        return out

    @property
    def speed(self) -> float | None:
        """Median emulation speed, a multiple of real time."""
        return statistics.median(s.speed for s in self.stats) if self.stats else None

    @property
    def fps(self) -> float | None:
        return statistics.median(s.fps for s in self.stats) if self.stats else None


class _Reader:
    """Takes hits off a stream on a thread of its own until its trace has ended and the hits
    queued by then are taken."""

    def __init__(self, hits: SyncStream[Hit], sink: Callable[[Hit], None]) -> None:
        self.hits, self.sink = hits, sink
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mhfu-observe", daemon=True)

    def __enter__(self) -> _Reader:
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._stop.set()
        self._thread.join()

    def _run(self) -> None:
        while True:
            try:
                hit = self.hits.next(DRAIN)
            except TimeoutError:
                if self._stop.is_set():
                    return
                continue
            except Disconnected:  # the trace ended and its queue is empty
                return
            self.sink(hit)


class Observer:
    """One species' big monster in a running game, and the calls its overlay makes."""

    def __init__(self, s: Session, engine: Engine, species: int) -> None:
        if species not in engine.owners:
            raise ValueError(f"no entity vtable for em{species}")
        self.s, self.engine, self.species = s, engine, species
        self.code = engine.codes[species]
        self.callees = outbound(self.code)
        """Callee -> its call sites in the overlay."""
        self.indirect = indirect(self.code)
        """Register-call site in the overlay -> its register."""
        self.vtable = engine.owners[species].vtable.va
        self._zones: dict[str, Code] = {}

    @property
    def condition(self) -> str:
        """True when $ra returns into the overlay's text."""
        return f"ra > {self.code.text.start:#x} && ra <= {self.code.text.stop:#x}"

    def function(self, pc: int) -> int | None:
        """The start of the function holding `pc`, in the engine or this species' overlay."""
        zone = self.engine.zone(pc)
        if zone is None:
            return None
        if zone not in self._zones:
            e = self.engine
            if zone == "em":
                code = self.code
            elif zone == "game_task":
                code = e.task_code
            else:
                mem = e.eboot if zone == "eboot" else e.game_sub
                code = Code(mem, mem.text)
            self._zones[zone] = code
        code = self._zones[zone]
        return code.function(pc).start if pc in code.text else None

    def entities(self) -> list[int]:
        """Registry entities of this species, by vtable."""
        return [e.base for e in self.s.game.monsters().values() if e.vtable == self.vtable]

    def check_loaded(self, count: int = 8) -> None:
        """Raise unless the overlay in memory is this species': its AI step must match."""
        step = self.engine.eboot.u32(self.vtable + a.MONSTER_VTABLE.AI_STEP)
        live = [i.encoding for i in self.s.client.disasm(step, count)]
        if live != [self.code.at(step + 4 * k).getRaw() for k in range(count)]:
            raise ValueError(f"em{self.species} is not the overlay loaded")

    def usec(self) -> int:
        return self.s.client.evaluate("usec")

    def run(
        self,
        targets: Iterable[int] = (),
        *,
        seconds: float,
        indirect: Iterable[int] = (),
        engine: Mapping[int, str] | Iterable[int] = (),
        writes: Iterable[tuple[int, int]] = (),
        rate: float = 2.0,
        state: str | None = None,
        entity: int | None = None,
        marks: bool = True,
        log: Callable[[str], None] = lambda _: None,
    ) -> Run:
        """Trace `targets`, the register-call sites `indirect`, the `engine` functions (address
        -> the register holding the monster, a0 for a plain list), the writes to the entity's
        `(offset, size)` spans, and with `marks` the pair changes; snapshot the monster `rate`
        times a second (0: at the ends only) for `seconds` of emulated time; `state` is loaded
        first."""
        targets = tuple(int(t) for t in targets)
        holders = (
            {int(k): v for k, v in engine.items()}
            if isinstance(engine, Mapping)
            else dict.fromkeys((int(k) for k in engine), "a0")
        )
        writes = tuple((int(offset), int(size)) for offset, size in writes)
        if overlap := set(targets) & set(holders):
            raise ValueError(f"traced both as callee and engine function: {sorted(overlap)}")
        client = self.s.client
        if state is not None:
            client.load_state(state)
        self.check_loaded()
        if entity is None:
            found = self.entities()
            if not found:
                raise ValueError(f"no em{self.species} monster in the entity registry")
            entity = found[0]
        run = Run(entity, targets, tuple(int(i) for i in indirect), tuple(holders), writes)
        start = self.usec()

        def take(sink: list[Any], convert: Callable[[Call], Any]) -> Callable[[Hit], None]:
            def put(hit: Hit) -> None:
                call = parse(hit, start)
                if call is None:
                    run.unparsed += 1
                else:
                    sink.append(convert(call))

            return put

        def put_write(hit: Hit) -> None:
            w = parse_write(hit, start, entity, self.function)
            if w is None:
                run.unparsed += 1
            else:
                run.writes.append(w)

        def snap() -> Snapshot:
            data = client.read(entity, SNAPSHOT_SIZE)
            return Snapshot((self.usec() - start) % WRAP, data)

        fmt = log_format(entity)
        calls = take(run.calls, lambda c: c)
        wanted = [_Trace(calls, run.targets, condition=self.condition, log_format=fmt)]
        for reg, sites in _by_value({s: self.indirect[s] for s in run.indirect}).items():
            wanted.append(_Trace(calls, sites, log_format=log_format(entity, reg)))
        for reg, fns in _by_value(holders).items():
            wanted.append(_Trace(calls, fns, condition=f"{reg} == {entity:#x}", log_format=fmt))
        spans = tuple((entity + off, size) for off, size in run.watched)
        wanted.append(_Trace(put_write, writes=spans, log_format=write_format(entity)))
        if marks:
            marked = take(run.transitions, Transition.of)
            condition = f"a0 == {entity:#x}"
            wanted.append(_Trace(marked, (a.SET_AI_STATE,), condition=condition, log_format=fmt))
        streams: list[SyncStream[Hit]] = []
        # the traces end before their readers do, so the hits queued by then are all taken
        with ExitStack() as readers, ExitStack() as traces:
            for w in wanted:
                if not (w.addresses or w.writes):
                    continue
                hits = traces.enter_context(
                    client.trace(
                        w.addresses, writes=w.writes, condition=w.condition, log_format=w.log_format
                    )
                )
                readers.enter_context(_Reader(hits, w.sink))
                streams.append(hits)
            log(
                f"{len(run.targets)} callee(s), {len(run.indirect)} register call(s), "
                f"{len(run.engine)} engine function(s), {len(run.watched)} field(s) "
                f"on 0x{entity:08X}, {seconds:g} emulated s"
            )
            run.snapshots.append(snap())
            next_snap = next_stats = self.s.now()
            while (self.usec() - start) % WRAP < seconds * 1e6:
                now = self.s.now()
                if now >= next_stats:
                    run.stats.append(client.frame_stats())
                    next_stats = now + 1.0
                if rate and now >= next_snap:
                    run.snapshots.append(snap())
                    next_snap = now + 1 / rate
                self.s.sleep(min(0.25, 1 / rate) if rate else 0.25)
            last = snap()
            run.snapshots.append(last)
            run.seconds = last.t / 1e6
            self.s.sleep(DRAIN)  # the log of the calls before `last` is still on its way
            traces.close()
        run.dropped = sum(s.dropped for s in streams)
        run.calls = [c for c in run.calls if c.t <= last.t]
        run.writes = [w for w in run.writes if w.t <= last.t]
        run.transitions = [tr for tr in run.transitions if tr.t <= last.t]
        return run


@dataclass(frozen=True)
class _Trace:
    """One `trace()` of a run and where its hits go."""

    sink: Callable[[Hit], None]
    addresses: tuple[int, ...] = ()
    writes: tuple[tuple[int, int], ...] = ()
    condition: str | None = None
    log_format: str | None = None


def _by_value(mapping: Mapping[int, str]) -> dict[str, tuple[int, ...]]:
    out: dict[str, list[int]] = collections.defaultdict(list)
    for key, value in mapping.items():
        out[value].append(key)
    return {k: tuple(v) for k, v in out.items()}


@dataclass(frozen=True)
class Cost:
    """A soak with `breakpoints` armed: median emulation speed and frame rate, calls logged."""

    breakpoints: int
    speed: float | None
    fps: float | None
    calls: int


def cost(
    obs: Observer,
    counts: Iterable[int],
    *,
    seconds: float,
    state: str | None = None,
    log: Callable[[str], None] = lambda _: None,
) -> list[Cost]:
    """For each count, a soak tracing that many callees, the most-called first, without
    snapshots; 0 arms nothing at all."""
    out = []
    for k in counts:
        targets = list(obs.callees)[:k]
        run = obs.run(targets, seconds=seconds, rate=0, state=state, marks=bool(k), log=log)
        out.append(Cost(k + bool(k), run.speed, run.fps, len(run.calls) + len(run.transitions)))
    return out


def batches(targets: Sequence[int], budget: int | None) -> list[tuple[int, ...]]:
    """`targets` in runs of at most `budget` breakpoints each (one run without a budget)."""
    if not budget or budget >= len(targets):
        return [tuple(targets)]
    return [tuple(targets[i : i + budget]) for i in range(0, len(targets), budget)]


@dataclass(frozen=True)
class Callee:
    va: int
    count: int
    per_entry: float
    sites: dict[int, int]
    a1: dict[int, int]

    @property
    def name(self) -> str | None:
        return symbols.name(self.va)


@dataclass(frozen=True)
class PairCalls:
    pair: Pair
    entries: int
    seconds: float
    callees: tuple[Callee, ...]


def by_pair(runs: Sequence[Run]) -> list[PairCalls]:
    """Per pair, the callees with their counts, per entry of the pair in the runs that traced
    them, sites and a1 values; pairs in order of first entry."""
    entries: collections.Counter[tuple[int, Pair]] = collections.Counter()
    seconds: dict[Pair, float] = collections.defaultdict(float)
    order: dict[Pair, None] = {}
    for r, run in enumerate(runs):
        for pair, since, until in run.timeline():
            entries[r, pair] += 1
            seconds[pair] += (until - since) / 1e6
            order.setdefault(pair, None)
    calls: dict[tuple[Pair, int], list[Call]] = collections.defaultdict(list)
    traced: dict[int, list[int]] = collections.defaultdict(list)
    for r, run in enumerate(runs):
        for va in run.traced():
            traced[va].append(r)
        for c in run.calls:
            calls[c.pair, c.callee].append(c)
            order.setdefault(c.pair, None)
    out = []
    for pair in order:
        callees = []
        for (p, va), cs in calls.items():
            if p != pair:
                continue
            n = sum(entries[r, pair] for r in traced[va])
            sites = collections.Counter(c.site for c in cs)
            a1 = collections.Counter(c.args[1] for c in cs)
            callees.append(Callee(va, len(cs), len(cs) / n if n else 0.0, dict(sites), dict(a1)))
        callees.sort(key=lambda c: (-c.count, c.va))
        total = sum(entries[r, pair] for r in range(len(runs)))
        out.append(PairCalls(pair, total, seconds[pair], tuple(callees)))
    return out


def _pair(p: Pair) -> str:
    return f"({p[0]},{p[1]})"


def _counts(counter: dict[int, int], limit: int, small: bool = False) -> str:
    """`value:count`, the most counted first; `small` values in decimal, as action ids are."""
    top = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))

    def show(k: int) -> str:
        if not small:
            return f"{k:08X}"
        return str(k) if k < 0x10000 else f"{k:#x}"

    text = " ".join(f"{show(k)}:{n}" for k, n in top[:limit])
    return text + (f" +{len(top) - limit}" if len(top) > limit else "")


def report(runs: Sequence[Run], *, sites: int = 3, a1_limit: int = 6) -> str:
    """Per pair, the callees most called first, with their call sites and, when few, their
    a1 values (decimal, as action ids are written); then the pair sequence of the first run."""
    lines = []
    traced = {va for run in runs for va in run.traced()}
    calls = sum(len(run.calls) for run in runs)
    lines.append(
        f"{len(runs)} run(s), {len(traced)} callee(s) traced, "
        f"{sum(r.seconds for r in runs):.1f} emulated s, {calls} call(s)"
    )
    for pc in by_pair(runs):
        n = sum(c.count for c in pc.callees)
        lines.append(f"{_pair(pc.pair)}  {pc.entries} entries, {pc.seconds:.1f} s, {n} calls")
        for c in pc.callees:
            a1 = f"  a1 {_counts(c.a1, a1_limit, True)}" if len(c.a1) <= a1_limit else ""
            lines.append(
                f"  {c.va:08X} {c.count:6} {c.per_entry:8.1f}/entry  {c.name or '-'}"
                f"  at {_counts(c.sites, sites)}{a1}"
            )
    if runs:
        steps = [f"{_pair(p)} {(b - f) / 1e6:.1f}s" for p, f, b in runs[0].timeline()]
        lines.append("sequence: " + " -> ".join(steps))
    return "\n".join(lines)


def field_name(offset: int) -> str:
    """The ENTITY field at `offset`, `NAME+0xN` inside one, else `+0xNNN`."""
    for name, start, fmt in _fields():
        if start <= offset < start + fmt.size:
            return name if offset == start else f"{name}+{offset - start:#x}"
    return f"+0x{offset:03X}"


def writes_report(runs: Sequence[Run], *, limit: int = 4) -> str:
    """Per pair, each field written and the functions writing it (`function@pc`), the most
    writes first."""
    per: dict[Pair, dict[str, collections.Counter[tuple[int | None, int]]]] = {}
    for run in runs:
        for w in run.writes:
            fields = per.setdefault(w.pair, {})
            fields.setdefault(field_name(w.offset), collections.Counter())[w.function, w.pc] += 1
    lines = []
    for pair, fields in per.items():
        lines.append(f"{_pair(pair)} writes")
        for name, writers in sorted(fields.items()):
            shown = []
            for (fn, pc), n in writers.most_common(limit):
                label = f"{fn:08X} {symbols.name(fn) or '-'}" if fn is not None else "?"
                shown.append(f"{label} @{pc:08X} x{n}")
            more = f" +{len(writers) - limit}" if len(writers) > limit else ""
            lines.append(f"  {name}: " + ", ".join(shown) + more)
    return "\n".join(lines)


def _show(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, tuple):
        return "(" + ",".join(_show(v) for v in value) + ")"
    return f"{value:#x}" if value > 9 else str(value)


def changes_report(changes: Sequence[PairChange]) -> str:
    """Each pair change: the fields that moved with it, then those that churn anyway."""
    lines = []
    for pc in changes:
        stable = [c for c in pc.changes if not c.churn]
        noisy = [c.name for c in pc.changes if c.churn]
        head = f"{_pair(pc.before.pair)} -> {_pair(pc.after.pair)} at {pc.after.t / 1e6:.2f} s"
        lines.append(head)
        lines.extend(f"  {c.name} {_show(c.before)} -> {_show(c.after)}" for c in stable)
        if noisy:
            lines.append("  churning: " + " ".join(noisy))
    return "\n".join(lines)


# --- a big monster's path, one AI frame at a time ---

_STUCK_WORD = a.ENTITY.STUCK_WALL & ~3
_PHASE = a.ENTITY.CLIP_BLOCKS + a.CLIP_BLOCK.PHASE
_SPEED = a.ENTITY.CLIP_BLOCKS + a.CLIP_BLOCK.SPEED
_FRAME_REFS = (
    (a.ENTITY.MAIN_STATE, 2),
    (a.ENTITY.POSITION, 4),
    (a.ENTITY.POSITION + 4, 4),
    (a.ENTITY.POSITION + 8, 4),
    (a.ENTITY.YAW, 2),
    (a.ENTITY.WALL_SECTORS, 4),
    (_STUCK_WORD, 4),
    (_PHASE, 4),
    (_SPEED, 4),
    (a.ENTITY.ANIM_INPUT, 2),
    (a.ENTITY.RENDER_SCALE, 4),
)
FRAME_FIELDS: tuple[str, ...] = ("t", "main", "sub", "x", "y", "z", "yaw", "walls", "stuck")
FRAME_FIELDS += ("clip", "speed", "entry", "scale")


def _f32(word: int) -> float:
    return float(struct.unpack("<f", struct.pack("<I", word))[0])


@dataclass(frozen=True)
class Frame:
    """A big monster as MONSTER_STEP calls its AI step: the position, walls and clip the frame
    before left, which is what a step running there reads."""

    t: float
    """Emulated seconds since the first frame."""
    main: int
    sub: int
    x: float
    y: float
    z: float
    yaw: int
    walls: int
    stuck: int
    clip: float
    """Part 0's clip cursor."""
    speed: float
    entry: int
    """The executor entry part 0 plays."""
    scale: float


def frame_format(entity: int) -> str:
    """What the frame breakpoint logs: the clock and each field of `Frame`, in hex."""
    return " ".join(["{usec}", *(f"{{[{entity + off:#x},{n}]}}" for off, n in _FRAME_REFS)])


def parse_frame(hit: Hit, start: int) -> Frame | None:
    """A hit logged in `frame_format`; None for any other line."""
    try:
        usec, pair, x, y, z, yaw, walls, stuck, clip, speed, entry, scale = (
            int(f, 16) for f in (hit.message or "").split()
        )
    except ValueError:
        return None
    shift = 8 * (a.ENTITY.STUCK_WALL - _STUCK_WORD)
    main, sub = _unpair(pair)
    return Frame(
        ((usec - start) % WRAP) / 1e6,
        main,
        sub,
        _f32(x),
        _f32(y),
        _f32(z),
        yaw,
        walls,
        (stuck >> shift) & 0xFF,
        _f32(clip),
        _f32(speed),
        input_action(entry, 0),
        _f32(scale),
    )


def track(s: Session, entity: int, seconds: float) -> list[Frame]:
    """Every AI frame of big monster `entity` for `seconds` emulated seconds, from one log-only
    breakpoint: the game keeps running."""
    out: list[Frame] = []
    start = s.client.evaluate("usec")
    condition = f"s2 == {entity:#x}"
    with s.client.trace(
        [a.MONSTER_AI_STEP_CALL], condition=condition, log_format=frame_format(entity)
    ) as hits:
        while not out or out[-1].t < seconds:
            try:
                hit = hits.next(DRAIN)
            except (TimeoutError, Disconnected):
                if (s.client.evaluate("usec") - start) % WRAP > (seconds + 1) * 1e6:
                    break
                continue
            f = parse_frame(hit, start)
            if f is not None:
                out.append(f)
    return out


@dataclass(frozen=True)
class Leg:
    """Consecutive frames playing one entry, its cursor rising: one play of a clip."""

    entry: int
    frames: tuple[Frame, ...]

    @property
    def path(self) -> float:
        """Units travelled in x and z."""
        fs = self.frames
        return sum(math.hypot(q.x - p.x, q.z - p.z) for p, q in zip(fs, fs[1:], strict=False))

    @property
    def turn(self) -> int:
        """YAW units turned, signed, the short way each frame."""
        fs = self.frames
        return sum(
            ((q.yaw - p.yaw + 0x8000) & 0xFFFF) - 0x8000 for p, q in zip(fs, fs[1:], strict=False)
        )

    @property
    def wall(self) -> Frame | None:
        """The first frame with a wall touched."""
        return next((f for f in self.frames if f.walls), None)


def legs(frames: Sequence[Frame]) -> list[Leg]:
    """`frames` cut wherever the entry changes or its clip restarts."""
    out: list[list[Frame]] = []
    for f in frames:
        last = out[-1][-1] if out else None
        if last is None or f.entry != last.entry or f.clip < last.clip:
            out.append([f])
        else:
            out[-1].append(f)
    return [Leg(fs[0].entry, tuple(fs)) for fs in out]


def write_frames(frames: Iterable[Frame], path: Path) -> None:
    with path.open("w", newline="") as f:
        out = csv.writer(f)
        out.writerow(FRAME_FIELDS)
        for fr in frames:
            out.writerow([getattr(fr, name) for name in FRAME_FIELDS])


def read_frames(path: Path) -> list[Frame]:
    """`write_frames`' file."""
    with path.open(newline="") as f:
        return [_frame(r) for r in csv.DictReader(f)]


def _frame(r: Mapping[str, str]) -> Frame:
    i, x = int, float
    return Frame(
        x(r["t"]),
        i(r["main"]),
        i(r["sub"]),
        x(r["x"]),
        x(r["y"]),
        x(r["z"]),
        i(r["yaw"]),
        i(r["walls"]),
        i(r["stuck"]),
        x(r["clip"]),
        x(r["speed"]),
        i(r["entry"]),
        x(r["scale"]),
    )


def legs_report(found: Sequence[Leg]) -> str:
    """One line per leg: entry, cursor span, frames, path, turn, the first wall touched."""
    lines = ["entry  clip from->to  frames   path  turn deg  pairs        wall"]
    for leg in found:
        fs = leg.frames
        pairs = " ".join(dict.fromkeys(_pair((f.main, f.sub)) for f in fs))
        w = leg.wall
        wall = f"{w.walls:#010x} class {2 if w.stuck else 1} at {w.clip:g}" if w else "-"
        lines.append(
            f"{leg.entry:5d}  {fs[0].clip:6.1f}->{fs[-1].clip:<6.1f} {len(fs):6d} {leg.path:6.0f}"
            f"  {leg.turn * 360 / 0x10000:8.1f}  {pairs:12} {wall}"
        )
    return "\n".join(lines)
