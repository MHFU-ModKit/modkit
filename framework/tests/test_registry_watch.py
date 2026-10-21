# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The registry poll's memory (src/core/registry_watch.h): a monster is reported when its slot's
pointer changes, and a new quest makes the poll forget, so a monster that comes back at the same
address is reported again."""

import ctypes
from collections.abc import Callable

import pytest

TIGREX, POPO = 0x090C1B00, 0x090CDC00  # noaddr


@pytest.fixture(scope="module")
def lib(host_lib: Callable[..., ctypes.CDLL]) -> ctypes.CDLL:
    lib = host_lib("tests/registry_host.cpp")
    lib.reg_host_spawned.argtypes = [ctypes.c_int, ctypes.c_uint32]
    return lib


@pytest.fixture
def poll(lib: ctypes.CDLL) -> Callable[..., list[int]]:
    """One pass over the slots given as {slot: pointer}; the slots reported."""
    lib.reg_host_reset()

    def run(slots: dict[int, int]) -> list[int]:
        lib.reg_host_begin()
        return [s for s, p in slots.items() if lib.reg_host_spawned(s, p)]

    return run


def test_a_pointer_is_reported_once(poll: Callable[..., list[int]]) -> None:
    assert poll({1: TIGREX, 3: POPO}) == [1, 3]
    assert poll({1: TIGREX, 3: POPO}) == []


def test_an_empty_slot_is_not_a_spawn(poll: Callable[..., list[int]]) -> None:
    assert poll({1: 0}) == []
    assert poll({1: TIGREX}) == [1]
    assert poll({1: 0}) == []
    assert poll({1: TIGREX}) == [1]


def test_a_new_quest_reports_what_comes_back(
    lib: ctypes.CDLL, poll: Callable[..., list[int]]
) -> None:
    assert poll({1: TIGREX, 3: POPO}) == [1, 3]
    lib.reg_host_forget()
    assert poll({1: TIGREX, 3: 0}) == [1]
    assert poll({1: TIGREX, 3: POPO}) == [3]


def test_a_forget_is_taken_once(lib: ctypes.CDLL, poll: Callable[..., list[int]]) -> None:
    assert poll({1: TIGREX}) == [1]
    lib.reg_host_forget()
    lib.reg_host_forget()
    assert poll({1: TIGREX}) == [1]
    assert poll({1: TIGREX}) == []
