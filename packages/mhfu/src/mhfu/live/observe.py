# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A big monster watched while the game runs: its entity snapshotted and diffed across pair
changes, and the engine functions its species overlay calls, each call put to the (main, sub)
pair the monster was in.

    obs = Observer(s, Engine.load(Extracted.find()), 75)
    run = obs.run(list(obs.callees), seconds=30, state=path)
    print(report([run]))

A call is seen by a log-only breakpoint on the callee whose condition keeps the returns into the
overlay's text: calls the overlay makes through engine code, by `jalr` or by a tail jump are not.
Pair changes come from a breakpoint on SET_AI_STATE, the one writer of the pair, and snapshots
from one read of the entity each, which stops the CPU for one request.
"""

from __future__ import annotations

import collections
import statistics
import struct
import threading
from collections.abc import Callable, Collection, Iterable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from functools import cache
from types import TracebackType
from typing import Any

from ppsspp_debug import Disconnected, FrameStats, Hit, SyncStream

from .. import addresses as a
from .. import symbols
from ..em.abi import Engine
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


def log_format(entity: int) -> str:
    """What each breakpoint logs: the clock, $ra, the entity's pair as one u16, a0..a2."""
    return f"{{usec}} {{ra}} {{[{entity + a.ENTITY.MAIN_STATE:#x},2]}} {{a0}} {{a1}} {{a2}}"


@dataclass(frozen=True)
class Call:
    """A logged call: `site` is the `jal` ($ra - 8), `pair` the monster's at entry, `t` the
    emulated microseconds since its run began."""

    callee: int
    site: int
    pair: Pair
    t: int
    args: tuple[int, int, int]


def parse(hit: Hit, start: int) -> Call | None:
    """A hit logged in `log_format`; None for any other line."""
    try:
        usec, ra, pair, a0, a1, a2 = (int(f, 16) for f in (hit.message or "").split())
    except ValueError:
        return None
    t = (usec - start) % WRAP
    return Call(hit.pc or hit.address, ra - 8, (pair & 0xFF, pair >> 8), t, (a0, a1, a2))


@dataclass(frozen=True)
class Transition:
    """SET_AI_STATE on the monster: the pair it left and the one it entered."""

    t: int
    before: Pair
    after: Pair
    site: int

    @classmethod
    def of(cls, call: Call) -> Transition:
        _, main, sub = call.args
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
    seconds: float = 0.0
    calls: list[Call] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    snapshots: list[Snapshot] = field(default_factory=list)
    stats: list[FrameStats] = field(default_factory=list)
    dropped: int = 0
    unparsed: int = 0

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
        self.vtable = engine.owners[species].vtable.va

    @property
    def condition(self) -> str:
        """True when $ra returns into the overlay's text."""
        return f"ra > {self.code.text.start:#x} && ra <= {self.code.text.stop:#x}"

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
        rate: float = 2.0,
        state: str | None = None,
        entity: int | None = None,
        marks: bool = True,
        log: Callable[[str], None] = lambda _: None,
    ) -> Run:
        """Trace `targets`, and with `marks` the pair changes, and snapshot the monster `rate`
        times a second (0: at the ends only) for `seconds` of emulated time; `state` is loaded
        first."""
        client = self.s.client
        if state is not None:
            client.load_state(state)
        self.check_loaded()
        if entity is None:
            found = self.entities()
            if not found:
                raise ValueError(f"no em{self.species} monster in the entity registry")
            entity = found[0]
        run = Run(entity, tuple(targets))
        start = self.usec()

        def take(sink: list[Any], convert: Callable[[Call], Any]) -> Callable[[Hit], None]:
            def put(hit: Hit) -> None:
                call = parse(hit, start)
                if call is None:
                    run.unparsed += 1
                else:
                    sink.append(convert(call))

            return put

        def snap() -> Snapshot:
            data = client.read(entity, SNAPSHOT_SIZE)
            return Snapshot((self.usec() - start) % WRAP, data)

        fmt = log_format(entity)
        streams: list[SyncStream[Hit]] = []
        # the traces end before their readers do, so the hits queued by then are all taken
        with ExitStack() as readers, ExitStack() as traces:
            if run.targets:
                hits = traces.enter_context(
                    client.trace(run.targets, condition=self.condition, log_format=fmt)
                )
                readers.enter_context(_Reader(hits, take(run.calls, lambda c: c)))
                streams.append(hits)
            if marks:
                pairs = traces.enter_context(
                    client.trace([a.SET_AI_STATE], condition=f"a0 == {entity:#x}", log_format=fmt)
                )
                readers.enter_context(_Reader(pairs, take(run.transitions, Transition.of)))
                streams.append(pairs)
            log(f"{len(run.targets)} callee(s) on 0x{entity:08X}, {seconds:g} emulated s")
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
        run.transitions = [tr for tr in run.transitions if tr.t <= last.t]
        return run


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
        for va in run.targets:
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
    traced = {va for run in runs for va in run.targets}
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
