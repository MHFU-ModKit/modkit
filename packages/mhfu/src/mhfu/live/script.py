# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A big monster's AI script state, read from game memory: the header it runs, where each context
resumes, which interrupts are raised.

    state = script.read(session.mem, entity)
    state.pc                      # where the running context resumes
    script.monsters(session.mem)  # registry slot -> state, for each monster with a script
    script.trace(session, entity, 30).counts   # executions by instruction address

`mhfu.em.script` turns the addresses into routines: `header` picks the program's header, `pc` a
line of its source. Read about once a second: a request stops the emulator for ms.
"""

from __future__ import annotations

import struct
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from ppsspp_debug import Hit

from .. import addresses as a
from ..addresses import Field
from ..memory import Memory
from ..structs import Game
from ..views import layout
from .observe import DRAIN, WRAP
from .session import Session

CONTEXTS = (0x00, 0x20, 0x10, 0x01)
"""`ENTITY.SCRIPT_CONTEXT` bits in the order of `ENTITY.SCRIPT_PCS`: the base routine, then the
interrupts that own a resume address, in the order the game tests them (`slot`)."""
_E = a.ENTITY
INTERRUPT_STARTS: Mapping[int, Field] = {
    0x08: _E.SCRIPT_INT_08,
    0x02: _E.SCRIPT_INT_02,
    0x04: _E.SCRIPT_INT_04,
}
"""The word holding the start of each pending bit's routine, for the bits whose start the entity
keeps."""
INTERRUPTS = tuple(INTERRUPT_STARTS)
_FIELDS = (
    _E.SPECIES,
    _E.SCRIPT_WAKE,
    _E.SCRIPT_PENDING,
    _E.SCRIPT_CONTEXT,
    _E.SCRIPT_HEADER,
    _E.SCRIPT_PCS,
    *INTERRUPT_STARTS.values(),
    _E.SCRIPT_CALL_RETURN,
    _E.SCRIPT_RET_RETURN,
    _E.SCRIPT_ROUTE,
    _E.SCRIPT_DEEP_RETURN,
    _E.SCRIPT_LOOP_RETURN,
    _E.SCRIPT_SPOT_RETURN,
    _E.SCRIPT_BASE,
    _E.SCRIPT_WAIT,
    _E.MAIN_STATE,
    _E.SUB_STATE,
)


def _pack(field: Field) -> struct.Struct:
    fmt = layout(field.type)
    if fmt is None:
        raise TypeError(f"{field.name} is {field.type}, which has no packing")
    return fmt


_FIRST = min(int(f) for f in _FIELDS)
_SPAN = max(int(f) + _pack(f).size for f in _FIELDS) - _FIRST
"""The bytes from the lowest field to the end of the highest: one request reads them all."""


@dataclass(frozen=True)
class ScriptState:
    entity: int
    species: int
    header: int
    """The address of the header the script runs (`ir.Header.va`)."""
    pcs: tuple[int, int, int, int]
    """Resume addresses by `CONTEXTS`."""
    context: int
    pending: int
    """Interrupt bits raised and not yet taken."""
    wake: int
    wait: int
    interrupts: dict[int, int]
    """The start of the interrupt routine by pending bit (`INTERRUPTS`)."""
    call_return: int
    ret_return: int
    route: int
    deep_return: int
    loop_return: int
    base: int
    """The base routine's index."""
    spot_return: int = 0
    main_state: int = 0
    sub_state: int = 0
    """`ENTITY.MAIN_STATE` and `SUB_STATE`: the move the monster plays now."""

    @property
    def pair(self) -> tuple[int, int]:
        """`(main, sub)` as an `enter` asks for it."""
        return self.main_state, self.sub_state

    @property
    def pc(self) -> int:
        """Where the running context resumes."""
        return self.pcs[slot(self.context)]


def slot(context: int) -> int:
    """The `SCRIPT_PCS` index the game resumes `context` from: the first `CONTEXTS` bit it holds,
    else the base routine's (an interrupt without a slot of its own shares it)."""
    return next((i for i, bit in enumerate(CONTEXTS) if context & bit), 0)


def read(mem: Memory, entity: int) -> ScriptState:
    """The entity's script state, in one request."""
    raw = mem.read(entity + _FIRST, _SPAN)

    def get(field: Field) -> tuple[Any, ...]:
        return _pack(field).unpack_from(raw, field - _FIRST)

    pcs = get(_E.SCRIPT_PCS)
    starts = (get(f)[0] for f in INTERRUPT_STARTS.values())
    return ScriptState(
        entity,
        get(_E.SPECIES)[0],
        get(_E.SCRIPT_HEADER)[0],
        (pcs[0], pcs[1], pcs[2], pcs[3]),
        get(_E.SCRIPT_CONTEXT)[0],
        get(_E.SCRIPT_PENDING)[0],
        get(_E.SCRIPT_WAKE)[0],
        get(_E.SCRIPT_WAIT)[0],
        dict(zip(INTERRUPTS, starts, strict=True)),
        get(_E.SCRIPT_CALL_RETURN)[0],
        get(_E.SCRIPT_RET_RETURN)[0],
        get(_E.SCRIPT_ROUTE)[0],
        get(_E.SCRIPT_DEEP_RETURN)[0],
        get(_E.SCRIPT_LOOP_RETURN)[0],
        get(_E.SCRIPT_BASE)[0],
        get(_E.SCRIPT_SPOT_RETURN)[0],
        get(_E.MAIN_STATE)[0],
        get(_E.SUB_STATE)[0],
    )


def monsters(mem: Memory) -> dict[int, ScriptState]:
    """Registry slot to state, for the monsters whose script header is set."""
    found = ((slot, read(mem, e.base)) for slot, e in Game(mem).monsters().items())
    return {slot: s for slot, s in found if s.header in a.RAM}


DISPATCH_FORMAT = "{usec} {s1} {v0}"
"""What the dispatch breakpoint logs: the clock, the instruction's address, its opcode."""
PATIENCE = 4
"""Wall seconds `trace` allows per emulated second, plus `PATIENCE_BASE`, for a game that runs."""
PATIENCE_BASE = 10.0


@dataclass(frozen=True)
class Step:
    """One script instruction run, `t` emulated microseconds into the trace."""

    t: int
    pc: int
    opcode: int


@dataclass(frozen=True)
class Trace:
    entity: int
    seconds: float
    """The emulated window."""
    steps: tuple[Step, ...]
    dropped: int
    """Log lines lost, which leaves the counts low: the stream's overflow and unreadable lines."""

    @cached_property
    def counts(self) -> dict[int, int]:
        """Executions by instruction address."""
        return dict(Counter(step.pc for step in self.steps))


def _step(hit: Hit, start: int) -> Step | None:
    try:
        usec, pc, opcode = (int(f, 16) for f in (hit.message or "").split())
    except ValueError:
        return None
    return Step((usec - start) % WRAP, pc, opcode)


def trace(s: Session, entity: int, seconds: float) -> Trace:
    """The script instructions `entity` runs in `seconds` emulated seconds, from one log-only
    breakpoint at AI_SCRIPT_DISPATCH, so the game keeps running; removed on return and on error.

    PPSSPP logs to its newest debugger connection only: pause other readers meanwhile.
    TimeoutError when the emulated clock does not get there (a paused game).
    """
    client = s.client
    window = int(seconds * 1e6)
    steps: list[Step] = []
    unread = 0
    start = client.evaluate("usec")
    limit = PATIENCE * seconds + PATIENCE_BASE
    give_up = s.now() + limit
    condition = f"s3 == {entity:#x}"
    with client.trace(
        [a.AI_SCRIPT_DISPATCH], condition=condition, log_format=DISPATCH_FORMAT
    ) as hits:
        while True:
            if s.now() > give_up:
                raise TimeoutError(f"the game did not run {seconds:g} emulated s in {limit:g} s")
            try:
                hit = hits.next(DRAIN)
            except TimeoutError:  # a quiet monster: the clock says when the window is over
                if (client.evaluate("usec") - start) % WRAP >= window:
                    break
                continue
            step = _step(hit, start)
            if step is None:
                unread += 1
            elif step.t > window:
                break
            else:
                steps.append(step)
        lost = hits.dropped + unread
    return Trace(entity, seconds, tuple(steps), lost)
