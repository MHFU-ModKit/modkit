# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A big monster's script state, read from a fake memory."""

import struct
from typing import Any

import pytest
from mhfu import addresses as a
from mhfu.live import script
from mhfu.memory import Image, Space
from mhfu.views import layout

E = a.ENTITY
SIZE = 0x800
HEADER = a.USER_RAM + 0x9000


class Counting(Space):
    """A memory that counts the requests it is asked."""

    def __init__(self, images: list[Image]) -> None:
        super().__init__(images)
        self.reads: list[tuple[int, int]] = []

    def read(self, address: int, size: int) -> bytes:
        self.reads.append((address, size))
        return super().read(address, size)

    def unpack(self, fmt: struct.Struct, address: int) -> tuple[Any, ...]:
        self.reads.append((address, fmt.size))  # a Space reads without `read`; Live does not
        return super().unpack(fmt, address)


def entity(**fields: Any) -> bytes:
    """An entity image with the fields (`SCRIPT_PCS=(1, 2, 3, 4)`) set by name."""
    raw = bytearray(SIZE)
    for name, value in fields.items():
        field = getattr(E, name)
        values = value if isinstance(value, tuple) else (value,)
        fmt = layout(field.type)
        assert fmt is not None
        fmt.pack_into(raw, field, *values)
    return bytes(raw)


def at(k: int) -> int:
    return a.USER_RAM + 0x10000 * (k + 1)


def memory(*entities: bytes) -> Counting:
    """The entity registry (slot 1 on) and an image per entity at `at(k)`."""
    slots = [at(k) for k in range(len(entities))]
    registry = struct.pack("<21I", 0, *slots, *[0] * (20 - len(slots)))
    images = [Image(registry, int(a.ENTITY_REGISTRY))]
    return Counting(images + [Image(raw, at(k)) for k, raw in enumerate(entities)])


FIELDS: dict[str, Any] = dict(
    SPECIES=75,
    SCRIPT_WAKE=1,
    SCRIPT_PENDING=0x12,
    SCRIPT_CONTEXT=0x20,
    SCRIPT_HEADER=HEADER,
    SCRIPT_PCS=(a.USER_RAM + 0x100, a.USER_RAM + 0x200, a.USER_RAM + 0x300, a.USER_RAM + 0x400),
    SCRIPT_INT_08=a.USER_RAM + 0x508,
    SCRIPT_INT_02=a.USER_RAM + 0x502,
    SCRIPT_INT_04=a.USER_RAM + 0x504,
    SCRIPT_CALL_RETURN=a.USER_RAM + 0x601,
    SCRIPT_WALK_RETURN=a.USER_RAM + 0x602,
    SCRIPT_ROUTE=a.USER_RAM + 0x603,
    SCRIPT_DEEP_RETURN=a.USER_RAM + 0x604,
    SCRIPT_LOOP_RETURN=a.USER_RAM + 0x605,
    SCRIPT_BASE=3,
    SCRIPT_WAIT=-5,
)


def test_one_request_reads_every_field() -> None:
    mem = memory(entity(**FIELDS))
    state = script.read(mem, at(0))
    assert mem.reads == [(at(0) + E.SPECIES, E.SCRIPT_WAIT + 2 - E.SPECIES)]
    assert state == script.ScriptState(
        at(0),
        75,
        HEADER,
        FIELDS["SCRIPT_PCS"],
        0x20,
        0x12,
        1,
        -5,
        {0x08: a.USER_RAM + 0x508, 0x02: a.USER_RAM + 0x502, 0x04: a.USER_RAM + 0x504},
        a.USER_RAM + 0x601,
        a.USER_RAM + 0x602,
        a.USER_RAM + 0x603,
        a.USER_RAM + 0x604,
        a.USER_RAM + 0x605,
        3,
    )


@pytest.mark.parametrize(
    ("context", "k"), [(0x00, 0), (0x20, 1), (0x10, 2), (0x01, 3), (0x08, None), (0x80, None)]
)
def test_pc_is_the_running_contexts(context: int, k: int | None) -> None:
    state = script.read(memory(entity(**{**FIELDS, "SCRIPT_CONTEXT": context})), at(0))
    assert state.pc == (None if k is None else FIELDS["SCRIPT_PCS"][k])


def test_contexts_follow_the_pc_words() -> None:
    assert script.CONTEXTS == (0x00, 0x20, 0x10, 0x01)
    assert E.SCRIPT_PCS.count == len(script.CONTEXTS)


def test_monsters_with_a_script() -> None:
    bare = entity(SPECIES=5)
    stray = entity(SPECIES=6, SCRIPT_HEADER=0x1234)
    mem = memory(entity(**FIELDS), bare, stray, entity(**{**FIELDS, "SPECIES": 76}))
    found = script.monsters(mem)
    assert {slot: s.species for slot, s in found.items()} == {1: 75, 4: 76}
    assert found[1].entity == at(0) and found[4].entity == at(3)
    assert len(mem.reads) == 1 + 4  # the registry, then an entity at a time


def test_no_monsters() -> None:
    assert script.monsters(memory()) == {}
