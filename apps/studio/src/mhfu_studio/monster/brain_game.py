# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The live side of the Brain tab: Follow the game and Count.

`mhfu.live.script` reads a monster's script state; `Follow` is the state of the tab's toggle: it
asks for a read at most once a second (every memory request stalls the emulator about 10 ms) and
turns what it got into marks on the rows of the view shown, the game's own script or an edited
build (`built`). `Count` is a timed per-node execution count from a debugger trace; Follow waits
while it runs. No toolkit.

    follow.poll(view, heads, running, ask, built)   # ask(row, known) -> Future[Read], on a worker
    count = Count(follow)
    count.start(built, run)                         # run(entity, seconds) -> Future[Trace]
    count.poll(built)                               # counts.nodes, .taken, .routines, .never
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from mhfu import addresses as a
from mhfu.em.script import Shape, asm, edit, graph, inject, ir
from mhfu.em.script.build import Built
from mhfu.em.script.inject import Container
from mhfu.live import script
from mhfu.live.script import CONTEXTS, ScriptState, Trace, slot
from mhfu.memory import Memory
from ppsspp_debug import DebuggerError

from mhfu_studio.monster import species
from mhfu_studio.monster.brain import BrainView, Variant

if TYPE_CHECKING:
    from mhfu.live.session import Session

PERIOD, RETRY = 1.0, 10.0
"""Seconds: between two reads, and between two tries once the game has no such monster or did not
answer."""
SECONDS = 30.0
"""Emulated seconds one Count runs."""
NEXT, PLAYING, PARKED = "next", "playing", "parked"
KINDS = (NEXT, PLAYING, PARKED)
"""The kinds of mark, in the order a line shows them."""
BUILD, NATIVE, OTHER = "build", "native", "other"
"""What the followed monster runs: the build the view shows, the game's own script, or something
else."""
NO_GAME = "no game running"
READING = "reading the game…"
PUSHED = "runs pushed code this view does not show"
NOT_SHOWN = "runs a script this view does not show"
OWN = "runs the game's own script"
NOT_BUILT = "your edits do not build yet"
NOT_FOLLOWING = "follow a running monster first"
NO_LIFT = "the game's own script is not at hand to count against"
BLOCK_END = "at the end of this block"
DOT = "·"
PUSH_AREA = range(int(a.SCRIPT_PUSH), int(a.SCRIPT_PUSH) + inject.PUSH_SIZE)
"""Where a pushed container sits."""

Native = Callable[[int], int | None]
"""A resume address of the monster's script as an address of the game's own script."""
Seen = dict[int, Container | None]
"""What `locate` found, by script header address."""


def other_build(id_: int) -> str:
    return f"runs another build of your edits (id {id_:08X}): Send or Push"


def replaced(what: str) -> str:
    return f"a quest load replaced your push with {what}: Push again"


class Mark(NamedTuple):
    kind: str
    """One of `KINDS`."""
    label: str
    """What to add after the line; empty for none."""


Marks = dict[int, tuple[Mark, ...]]


class Read(NamedTuple):
    """What one read of the game brought."""

    row: int
    monsters: dict[int, ScriptState]
    """The live monsters of the species row, by registry slot."""
    box: Container | None
    """The container the followed monster's script header lies in: found by `locate` when `fetch`
    has `seen`, else the one at SCRIPT_PUSH."""


class Target(NamedTuple):
    """The monster Follow follows and what it runs."""

    entity: int
    runs: str
    """One of `BUILD`, `NATIVE`, `OTHER`."""
    id: int | None
    """The container's id; None for the game's own script and for one nothing identifies."""
    load: int | None
    """Where the container's image sits."""


def locate(mem: Memory, header: int, seen: Seen) -> Container | None:
    """The container whose image holds the script header `header` (`inject.around`); None for the
    game's own script. `seen` keeps the last per header address, so a container is checked by its
    head alone, and a miss, which costs a scan to prove, is kept too, except in the push area,
    where a container can come back."""
    known = seen.get(header)
    if known is None and header in seen and header not in PUSH_AREA:
        return None
    box = inject.around(mem, header, known)
    if box is not None or header not in PUSH_AREA:
        seen[header] = box
    return box


def fetch(
    attach: Callable[[], AbstractContextManager[Session]],
    row: int,
    known: Container | None = None,
    seen: Seen | None = None,
) -> Read:
    """Attaches to the game and reads the monsters of species `row` and their container: with
    `seen` (`Follow.seen`) the one around the followed monster's header, wherever it sits; without,
    the one pushed at SCRIPT_PUSH (`known`, the last read's, is not read again). The seam the tests
    fake; it runs on a worker."""
    with attach() as s:
        found = {k: m for k, m in script.monsters(s.mem).items() if m.species == row}
        if not found:
            return Read(row, found, None)
        if seen is None:
            return Read(row, found, inject.pushed(s.mem, known))
        return Read(row, found, locate(s.mem, found[min(found)].header, seen))


def context_words(context: int) -> str:
    return "the base routine" if context == CONTEXTS[0] else f"interrupt 0x{context:02X}"


def row_at(view: BrainView, at: int | None) -> tuple[int, str] | None:
    """The row of the line at the native address `at`, and a word when it is the end of a block
    (which has no line: it answers with its opener); None when the view has nothing there."""
    if at is None:
        return None
    if (row := view.at.get(at)) is not None:
        return row, ""
    if (row := view.closes.get(at)) is not None:
        return row, BLOCK_END
    return None


def instead(view: BrainView, pair: tuple[int, int]) -> str:
    """What the monster plays when it is not the move the script asked for, as an `enter` of it
    in this script names it."""
    named = next(
        (view.labels[r] for r, p in view.enters.items() if p == pair and r in view.labels), ""
    )
    return f"plays {pair[0]} {pair[1]} instead" + (f" ({named})" if named else "")


class Spot(NamedTuple):
    """Where a resume address stands in a view."""

    row: int
    word: str = ""
    """BLOCK_END when it is the end of a block, which answers with its opener's row."""
    played: int | None = None
    """The row of the `enter` that ended here."""


Find = Callable[[int], Spot | None]
"""A live resume address as a place in the view."""


def native_find(view: BrainView, native: Native) -> Find:
    """Places by the game's own addresses, `native` mapping the live ones."""

    def find(pc: int) -> Spot | None:
        at = native(pc)
        found = row_at(view, at)
        if at is None or found is None:
            return None
        return Spot(found[0], found[1], view.ends.get(at))

    return find


class Layout:
    """An edited build's image against the view that shows it, so a live address is a node id and
    then a row: what `edit.ids_by_offset` gives, for new nodes as for native ones."""

    def __init__(self, view: BrainView, image: asm.Image) -> None:
        self.size = len(image.data)
        self.items = image.items
        row_of = view.row_of
        self.at = {off: row_of[n] for off, n in edit.ids_by_offset(image).items() if n in row_of}
        self.closes = {off: row_of[b] for b, off in image.closes.items() if b in row_of}
        self.ends: dict[int, int] = {}
        for row in view.enters:
            where = image.nodes.get(view.lines[row].node or "")
            if where is not None:
                self.ends[where[0] + where[1]] = row

    def find(self, off: int) -> Spot | None:
        """The place at image offset `off`."""
        if (row := self.at.get(off)) is not None:
            return Spot(row, "", self.ends.get(off))
        if (row := self.closes.get(off)) is not None:
            return Spot(row, BLOCK_END, self.ends.get(off))
        return None

    def header(self, off: int, names: set[str]) -> str | None:
        """The header, of `names`, whose words sit at image offset `off`."""
        return next((n for n, (at, _) in self.items.items() if at == off and n in names), None)


def spot_marks(view: BrainView, state: ScriptState, find: Find) -> tuple[int | None, Marks]:
    """The row the running context resumes at, and every mark of `state` on `view`: NEXT where it
    resumes, PLAYING on the `enter` that saved that address (labelled when the monster plays
    another move), PARKED where each other context waits."""
    out: dict[int, list[Mark]] = {}
    here = find(state.pc)
    if here is not None:
        out.setdefault(here.row, []).append(Mark(NEXT, here.word))
        if here.played is not None:
            label = "" if view.enters[here.played] == state.pair else instead(view, state.pair)
            out.setdefault(here.played, []).append(Mark(PLAYING, label))
    parked: dict[int, list[int]] = {}
    running = slot(state.context)
    for i, (context, pc) in enumerate(zip(CONTEXTS, state.pcs, strict=True)):
        if i != running and (where := find(pc)) is not None:
            parked.setdefault(where.row, []).append(context)
    for row, contexts in parked.items():
        words = ", ".join(context_words(c) for c in contexts)
        out.setdefault(row, []).append(Mark(PARKED, f"parked in {words}"))
    ordered = {r: tuple(sorted(ms, key=lambda m: KINDS.index(m.kind))) for r, ms in out.items()}
    return (None if here is None else here.row), ordered


def marks_of(view: BrainView, state: ScriptState, native: Native) -> tuple[int | None, Marks]:
    """`spot_marks` for the game's own script, or a container mapped back through `native`."""
    return spot_marks(view, state, native_find(view, native))


class Follow:
    """The state of Follow the game. `marks` (a row's marks, in `KINDS` order) and `status` say
    what the tab shows; `header` is the header the monster runs, which the view shown should be,
    `next_row` the line it resumes at and `next_node` that line's node id. `target` is the monster
    and what it runs, which `Count` counts."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.on = False
        self.paused = False
        self.status = ""
        self.marks: Marks = {}
        self.header: str | None = None
        self.next_row: int | None = None
        self.next_node: str | None = None
        self.target: Target | None = None
        self.seen: Seen = {}  # for `fetch`, which runs on a worker; cleared with the rest
        self._read: Read | None = None
        self._why = ""
        self._job: Future[Read] | None = None
        self._asked = self._lost = float("-inf")
        self._held: tuple[int, bool] | None = None
        """The build the view shows that a monster ran, and whether it was pushed."""
        self._layout: tuple[BrainView, asm.Image, Layout] | None = None

    def set(self, on: bool) -> None:
        self.on = on
        self.reset()

    def reset(self) -> None:
        """Forgets what was read: another script, or the toggle changed."""
        self._read, self._job, self._why = None, None, ""
        self.marks, self.header, self.next_row, self.next_node = {}, None, None, None
        self.target, self._held, self._layout = None, None, None
        self.seen.clear()
        self.status = READING if self.on else ""
        self._asked = self._lost = float("-inf")

    def hold(self, on: bool) -> None:
        """Stops the reads while a trace has the debugger's log (it goes to the newest
        connection), or lets them go on: a read in flight is dropped."""
        if on == self.paused:
            return
        self.paused = on
        if not on:
            self._job = None
            self._asked = self._lost = float("-inf")

    def poll(
        self,
        view: BrainView | None,
        heads: Sequence[Variant],
        running: bool,
        ask: Callable[[int, Container | None], Future[Read]],
        built: Built | None = None,
    ) -> bool:
        """Takes a finished read, and asks for the next when `PERIOD` has passed (`ask` runs it
        on a worker, told the species row and the container read last). `built` is the build the
        view shows, when it shows an edited one. True when what the tab shows changed."""
        if not self.on or view is None or self.paused:
            return False
        before = self._shown()
        if self._job is not None and not self._job.done():
            return False
        if self._job is not None:
            job, self._job = self._job, None
            self._took(job)
        now = self.clock()
        if not running:
            self.reset()
            self._why = NO_GAME
        else:
            if self._why == NO_GAME:
                self._why = ""
            if now - self._asked >= PERIOD and now - self._lost >= RETRY:
                self._asked = now
                self._job = ask(view.row, None if self._read is None else self._read.box)
        self._place(view, heads, built)
        return before != self._shown()

    def place(
        self, view: BrainView | None, heads: Sequence[Variant], built: Built | None = None
    ) -> bool:
        """Marks `view` from what was read last, for a view, header or build that came after the
        read. True when what the tab shows changed."""
        before = self._shown()
        if self.on and view is not None:
            self._place(view, heads, built)
        return before != self._shown()

    def _shown(self) -> tuple[object, ...]:
        return self.marks, self.status, self.header, self.next_row, self.next_node

    def _took(self, job: Future[Read]) -> None:
        try:
            got = job.result()
        except (LookupError, OSError, ValueError, DebuggerError) as e:
            self._read, self._why = None, f"the game did not answer: {e}"
            self._lost = self.clock()
            return
        self._read, self._why = got, ""
        if not got.monsters:
            self._lost = self.clock()

    def _place(self, view: BrainView, heads: Sequence[Variant], built: Built | None) -> None:
        self.marks, self.header, self.next_row, self.next_node = {}, None, None, None
        self.target = None
        got = self._read
        if self._why:
            self.status = self._why
        elif got is None or got.row != view.row:
            self.status = READING
        elif not got.monsters:
            self.status = f"no {species.label(got.row)} in the game"
        else:
            self._follow(view, heads, got, built)
            if self.next_row is not None:
                self.next_node = view.lines[self.next_row].node

    def _follow(
        self, view: BrainView, heads: Sequence[Variant], got: Read, built: Built | None
    ) -> None:
        first = min(got.monsters)
        state = got.monsters[first]
        who = f"following {DOT} slot {first}"
        if len(got.monsters) > 1:
            who += f" {DOT} first of {len(got.monsters)}"
        runs = got.box is not None and state.header in got.box.head.addresses
        box = got.box if runs else None
        if built is None:
            self._game(view, heads, state, box, who)
        else:
            self._edited(view, heads, state, box, who, built)

    def _game(
        self,
        view: BrainView,
        heads: Sequence[Variant],
        state: ScriptState,
        box: Container | None,
        who: str,
    ) -> None:
        """A view of the game's own script: the monster's, or a container's mapped back to it."""
        native: Native = (lambda pc: pc) if box is None else box.native
        header = native(state.header)
        want = next((h.header for h in heads if h.va is not None and h.va == header), None)
        why = NOT_SHOWN if box is None else PUSHED
        if box is not None:
            self.target = Target(state.entity, OTHER, box.head.id, box.load)
        else:
            self.target = Target(state.entity, OTHER if want is None else NATIVE, None, None)
        if want is None:
            self.status = f"{who} {DOT} {why}"
            return
        self.header, self.status = want, who
        if view.header == want:  # else the tab switches to it next
            self.next_row, self.marks = marks_of(view, state, native)
            if self.next_row is None:
                self.status += f" {DOT} {why}"

    def _edited(
        self,
        view: BrainView,
        heads: Sequence[Variant],
        state: ScriptState,
        box: Container | None,
        who: str,
        built: Built,
    ) -> None:
        """A view of an edited build: marks only while the monster runs that very build."""
        entity = state.entity
        held = self._held if self._held is not None and self._held[0] == built.id else None
        pushed = held is not None and held[1]
        if built.id is None or built.image is None:
            self.target = Target(entity, OTHER, None, None)
            self.status = f"{who} {DOT} {NOT_BUILT}"
        elif box is None and any(h.va is not None and h.va == state.header for h in heads):
            self.target = Target(entity, NATIVE, None, None)
            self.status = f"{who} {DOT} " + (replaced("the game's own script") if pushed else OWN)
        elif box is None:
            self.target = Target(entity, OTHER, None, None)
            self.status = f"{who} {DOT} {NOT_SHOWN}"
        elif box.head.id != built.id:
            self.target = Target(entity, OTHER, box.head.id, box.load)
            then = box.load not in PUSH_AREA and pushed
            say = replaced(f"the stick's build (id {box.head.id:08X})") if then else None
            self.status = f"{who} {DOT} {say or other_build(box.head.id)}"
        else:
            self.target = Target(entity, BUILD, built.id, box.load)
            self._held = (built.id, box.load in PUSH_AREA)
            self._on_build(view, heads, state, box, who, built.image)

    def _on_build(
        self,
        view: BrainView,
        heads: Sequence[Variant],
        state: ScriptState,
        box: Container,
        who: str,
        image: asm.Image,
    ) -> None:
        if self._layout is None or self._layout[0] is not view or self._layout[1] is not image:
            self._layout = (view, image, Layout(view, image))
        lay = self._layout[2]
        want = lay.header(state.header - box.load, {h.header for h in heads})
        if want is None:
            self.status = f"{who} {DOT} {NOT_SHOWN}"
            return
        self.header, self.status = want, who
        if view.header == want:
            load = box.load
            self.next_row, self.marks = spot_marks(
                view, state, lambda pc: lay.find(pc - load) if 0 <= pc - load < lay.size else None
            )
            if self.next_row is None:
                self.status += f" {DOT} {NOT_SHOWN}"


# ---- Count


Runner = Callable[[int, float], Future[Trace]]
"""Runs `script.trace(session, entity, seconds)` on a worker."""
IMPLICIT = "{}#implicit"
"""The key of a block's implicit port (an if's missing else, a switch's missing default), by the
block's id."""


def port_key(block: str, port: graph.Port) -> str:
    """The key of a block's port in `Counted.taken`: the arm's id, `graph.head_key` for a head,
    `IMPLICIT` for an implicit port."""
    return port.scope if port.scope is not None else IMPLICIT.format(block)


class Total(NamedTuple):
    starts: int
    """Times the routine was entered at its first node."""
    ops: int
    """Instructions run in it."""


@dataclass(frozen=True)
class Counted:
    """What one trace came to, by node id of the script it ran."""

    entity: int
    seconds: float
    id: int | None
    """The container's id the node ids belong to; None for the game's own script."""
    steps: int
    """Instructions the monster ran."""
    outside: int
    """Of them, run outside the script: the monster left it."""
    dropped: int
    """Log lines lost, which leaves the counts low."""
    nodes: dict[str, int]
    """Executions of each instruction and block opener that ran."""
    taken: dict[str, int]
    """Times each port of the blocks that ran was taken (`port_key`): the count of the first node
    of its body; an empty port of an if, switch or random is the opener's count less the others'.
    Left out: an empty port beside another empty one, which no count tells apart."""
    routines: dict[str, Total]
    """By routine, for those that ran."""
    never: frozenset[str]
    """The instructions and block openers that did not run."""


@dataclass(frozen=True)
class Basis:
    """The script a trace is read against."""

    program: ir.Program
    id: int | None
    image: asm.Image | None = None
    """The build's image, at `load`; None for the game's own script, whose nodes have addresses."""
    load: int = 0

    def starts(self) -> dict[int, str]:
        """The node id at each live address a node starts at."""
        if self.image is None:
            return {n.pc: n.id for n in self.program.nodes() if n.pc is not None}
        return {self.load + off: nid for off, nid in edit.ids_by_offset(self.image).items()}

    @property
    def extent(self) -> range:
        if self.image is None:
            return range(self.program.base, self.program.end or 1 << 32)
        return range(self.load, self.load + len(self.image.data))


def tally(trace: Trace, basis: Basis) -> Counted:
    """`trace` as counts per node of `basis`, and what follows from them."""
    at, extent = basis.starts(), basis.extent
    stmts: dict[str, list[str]] = {}
    for r in basis.program.routines():
        stmts[r.name] = [n.id for n in ir.walk(r.body) if isinstance(n, ir.Insn | ir.Block)]
    ids = {nid for names in stmts.values() for nid in names}
    hits: Counter[str] = Counter()
    outside = 0
    for pc, n in trace.counts.items():
        if pc not in extent:
            outside += n
        else:
            nid = at.get(pc)
            if nid is not None and nid in ids:  # a marker's pc is inside, and no statement
                hits[nid] += n
    routines = {}
    for r in basis.program.routines():
        ops = sum(hits[nid] for nid in stmts[r.name])
        if ops:
            routines[r.name] = Total(hits[r.body[0].id] if r.body else 0, ops)
    return Counted(
        trace.entity,
        trace.seconds,
        basis.id,
        len(trace.steps),
        outside,
        trace.dropped,
        dict(hits),
        _taken(basis.program, hits),
        routines,
        frozenset(ids - hits.keys()),
    )


def _taken(p: ir.Program, hits: Counter[str]) -> dict[str, int]:
    """The ports of the blocks that ran, by `port_key`, from the first node of each port's body."""
    out: dict[str, int] = {}
    head = next((h.name for h in p.headers()), None)
    proj = graph.Projection(p)
    for r in p.routines():
        ran = [n.id for n in ir.walk(r.body) if isinstance(n, ir.Block) and hits[n.id]]
        if not ran:
            continue
        g = proj.routine(r.name, head)
        for bid in ran:
            b = g.nodes[bid]
            if not isinstance(b, graph.Stmt):
                continue
            firsts = [g.scopes[x.scope].body if x.scope is not None else () for x in b.ports]
            counts = [hits[f[0]] if f else None for f in firsts]
            empty = [i for i, c in enumerate(counts) if c is None]
            if len(empty) == 1 and b.op.shape in (Shape.IF, Shape.SWITCH, Shape.RANDOM):
                rest = hits[bid] - sum(c for c in counts if c is not None)
                counts[empty[0]] = max(rest, 0)
            out |= {
                port_key(bid, x): c for x, c in zip(b.ports, counts, strict=True) if c is not None
            }
    return out


def basis_of(t: Target, built: Built | None, lift: ir.Program | None) -> Basis | str:
    """The script the monster `t` runs, as far as it can be counted against; else why not."""
    if t.runs == BUILD:
        if built is None or built.image is None or built.id != t.id or t.load is None:
            return "the monster runs an older build than the view shows: Send or Push, then count"
        return Basis(built.program, built.id, built.image, t.load)
    program = lift or (None if built is None else built.lift)
    if t.runs == NATIVE:
        return NO_LIFT if program is None else Basis(program, None)
    if t.id is not None:
        return f"the monster runs another build (id {t.id:08X}): Send or Push, then count"
    return "the monster runs a script this view does not show"


class Count:
    """A timed per-node count of the followed monster: `start` asks for a trace, `poll` takes it.
    Follow does not read while it runs. `result` is the last count and `status` says what it is,
    or why there is none; it never raises."""

    def __init__(self, follow: Follow) -> None:
        self.follow = follow
        self.result: Counted | None = None
        self._note = ""
        self._job: Future[Trace] | None = None
        self._basis: Basis | None = None
        self._dropped = False
        self._now: int | None = None

    @property
    def busy(self) -> bool:
        return self._job is not None

    @property
    def stale(self) -> bool:
        """The counts are of a build the view no longer shows."""
        r = self.result
        return r is not None and r.id is not None and r.id != self._now

    @property
    def status(self) -> str:
        if self.stale and self.result is not None:
            old = f"these counts belong to an older build (id {self.result.id:08X})"
            return f"{self._note} {DOT} {old}"
        return self._note

    def start(
        self,
        built: Built | None,
        run: Runner,
        seconds: float = SECONDS,
        lift: ir.Program | None = None,
    ) -> bool:
        """Counts what the followed monster runs for `seconds` emulated seconds, when that is the
        build `built` or the game's own script (`lift`, else the build's). False, and `status`
        says why, when it is not, or a count is running."""
        if self.busy:
            return False
        self.result, self._now = None, None if built is None else built.id
        t = self.follow.target
        if t is None:
            self._note = NOT_FOLLOWING
            return False
        basis = basis_of(t, built, lift)
        if isinstance(basis, str):
            self._note = basis
            return False
        self._basis = basis
        self._job = run(t.entity, seconds)
        self.follow.hold(True)
        self._note = f"counting {seconds:g} s…"
        return True

    def poll(self, built: Built | None = None) -> bool:
        """Takes a finished trace; `built` is the build the view shows now, which says whether the
        counts are still of it. True when what the tab shows changed."""
        before = (self._note, self.result, self.stale)
        self._now = None if built is None else built.id
        if self._job is not None and self._job.done():
            job, self._job = self._job, None
            self.follow.hold(False)
            if self._dropped:
                self._dropped = False
            else:
                self._took(job)
        return before != (self._note, self.result, self.stale)

    def reset(self) -> None:
        """Forgets the count, and drops one in flight (Follow waits until it is over)."""
        self.result, self._note, self._basis = None, "", None
        self._dropped = self._job is not None

    def _took(self, job: Future[Trace]) -> None:
        basis, self._basis = self._basis, None
        try:
            got = job.result()
        except TimeoutError as e:
            self._note = f"the count timed out: {e}; is the game paused?"
        except DebuggerError as e:
            self._note = f"the debugger failed the count: {e}"
        except (LookupError, OSError, ValueError) as e:
            self._note = f"the game did not answer: {e}"
        else:
            if basis is not None:
                self._count(got, basis)

    def _count(self, trace: Trace, basis: Basis) -> None:
        if not trace.steps:
            self._note = (
                f"no instruction ran in {trace.seconds:g} s: the monster left,"
                " or is out of the hunter's section"
            )
            return
        got = tally(trace, basis)
        if got.outside == got.steps:
            self._note = "the monster left this script during the count"
            return
        self.result = got
        self._note = f"counted {got.steps} instructions in {got.seconds:g} s"
        if got.dropped:
            self._note += f" {DOT} {got.dropped} log lines were lost, so the counts are low"
