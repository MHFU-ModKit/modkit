# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A big monster's AI script state, read from game memory: the header it runs, where each context
resumes, which interrupts are raised.

    state = script.read(session.mem, entity)
    state.pc                      # where the running context resumes
    script.monsters(session.mem)  # registry slot -> state, for each monster with a script

`mhfu.em.script` turns the addresses into routines: `header` picks the program's header, `pc` a
line of its source. Read about once a second: a request stops the emulator for ms.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any

from .. import addresses as a
from ..addresses import Field
from ..memory import Memory
from ..structs import Game
from ..views import layout

CONTEXTS = (0x00, 0x20, 0x10, 0x01)
"""`ENTITY.SCRIPT_CONTEXT` values in the order of `ENTITY.SCRIPT_PCS`: the base routine, then the
interrupts that own a resume address."""
INTERRUPTS = (0x08, 0x02, 0x04)
"""The pending bits whose routine's start the entity keeps, in the order of their words."""

_E = a.ENTITY
_INTERRUPT_STARTS = (_E.SCRIPT_INT_08, _E.SCRIPT_INT_02, _E.SCRIPT_INT_04)
_FIELDS = (
    _E.SPECIES,
    _E.SCRIPT_WAKE,
    _E.SCRIPT_PENDING,
    _E.SCRIPT_CONTEXT,
    _E.SCRIPT_HEADER,
    _E.SCRIPT_PCS,
    *_INTERRUPT_STARTS,
    _E.SCRIPT_CALL_RETURN,
    _E.SCRIPT_WALK_RETURN,
    _E.SCRIPT_ROUTE,
    _E.SCRIPT_DEEP_RETURN,
    _E.SCRIPT_LOOP_RETURN,
    _E.SCRIPT_BASE,
    _E.SCRIPT_WAIT,
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
    walk_return: int
    route: int
    deep_return: int
    loop_return: int
    base: int
    """The base routine's index."""

    @property
    def pc(self) -> int | None:
        """Where the running context resumes; None for a context `CONTEXTS` has no slot for."""
        return self.pcs[CONTEXTS.index(self.context)] if self.context in CONTEXTS else None


def read(mem: Memory, entity: int) -> ScriptState:
    """The entity's script state, in one request."""
    raw = mem.read(entity + _FIRST, _SPAN)

    def get(field: Field) -> tuple[Any, ...]:
        return _pack(field).unpack_from(raw, field - _FIRST)

    pcs = get(_E.SCRIPT_PCS)
    starts = (get(f)[0] for f in _INTERRUPT_STARTS)
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
        get(_E.SCRIPT_WALK_RETURN)[0],
        get(_E.SCRIPT_ROUTE)[0],
        get(_E.SCRIPT_DEEP_RETURN)[0],
        get(_E.SCRIPT_LOOP_RETURN)[0],
        get(_E.SCRIPT_BASE)[0],
    )


def monsters(mem: Memory) -> dict[int, ScriptState]:
    """Registry slot to state, for the monsters whose script header is set."""
    found = ((slot, read(mem, e.base)) for slot, e in Game(mem).monsters().items())
    return {slot: s for slot, s in found if s.header in a.RAM}
