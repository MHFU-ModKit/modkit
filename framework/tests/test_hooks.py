"""Hook arbitration (hooks.cpp) on the host: owners, the when-quiet queue, detours."""

import ctypes
from collections.abc import Callable
from typing import Any

import pytest

OK, CONFLICT, NOSPACE, BADARG = 0, -1, -2, -3
PRE = ctypes.c_void_p(0x4000)  # any non-null helper; the host build never calls it
X, Y, D, S = 0x100, 0x200, 0x300, 0x400
SW_RA = 0xAFBF001C  # sw ra, 0x1c(sp)
ADDIU_SP = 0x27BDFFE0  # addiu sp, sp, -0x20


class Hooks:
    def __init__(self, lib: ctypes.CDLL, mips: Any) -> None:
        self.lib, self.mips = lib, mips
        lib.mhfu_hook_init()
        lib.host_reset()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.lib, name)

    def peek(self, addr: int) -> int:
        return int(self.lib.host_peek(addr))

    def poke(self, addr: int, *words: int) -> None:
        for i, w in enumerate(words):
            self.lib.host_poke(addr + 4 * i, w)

    def logged(self, needle: str) -> int:
        return int(self.lib.host_log_drain(needle.encode()))


@pytest.fixture(scope="module")
def lib(host_lib: Callable[..., ctypes.CDLL]) -> ctypes.CDLL:
    lib = host_lib("src/core/hooks.cpp", "tests/hooks_host.cpp")
    u32, own = ctypes.c_uint32, ctypes.c_char_p
    lib.host_peek.restype = u32
    lib.host_peek.argtypes = [u32]
    lib.host_poke.argtypes = [u32, u32]
    lib.host_last_wrapper.restype = u32
    lib.host_log_drain.argtypes = [own]
    lib.mhfu_hook_word.argtypes = [u32, u32, own]
    lib.mhfu_hook_vtable.argtypes = [u32, u32, own]
    lib.mhfu_hook_word_when_quiet.argtypes = [u32, u32, u32, own]
    lib.mhfu_hook_call.argtypes = [u32, u32, ctypes.c_void_p, ctypes.c_void_p, own]
    lib.mhfu_hook_detour.argtypes = [u32, u32, u32, ctypes.c_void_p, own]
    lib.mhfu_hook_detour_now.argtypes = [u32, u32, u32, ctypes.c_void_p, own]
    lib.mhfu_hook_release.argtypes = [own]
    return lib


@pytest.fixture
def h(lib: ctypes.CDLL, mips: Any) -> Hooks:
    return Hooks(lib, mips)


def test_a_second_owner_conflicts_when_queueing(h: Hooks) -> None:
    h.poke(X, 1)
    assert h.mhfu_hook_word_when_quiet(X, 1, 0xA, b"a") == OK
    assert h.mhfu_hook_word_when_quiet(X, 1, 0xB, b"b") == CONFLICT
    assert h.mhfu_hook_word(X, 0xB, b"b") == CONFLICT
    assert h.mhfu_hook_detour_now(X - 4, 0, 1, PRE, b"b") == CONFLICT
    assert h.mhfu_hook_word_when_quiet(X, 1, 0xA2, b"a") == OK  # the owner's update
    h.mhfu_hook_land_queued()
    assert h.peek(X) == 0xA2


def test_a_mismatch_waits_and_logs_once(h: Hooks) -> None:
    h.poke(X, 7)
    assert h.mhfu_hook_word_when_quiet(X, 1, 0xA, b"a") == OK
    h.logged("")
    for _ in range(3):
        h.mhfu_hook_land_queued()
    assert (h.peek(X), h.logged("waits")) == (7, 1)
    h.poke(X, 1)
    h.mhfu_hook_land_queued()
    assert h.peek(X) == 0xA


def test_a_conflict_on_landing_drops_the_entry(h: Hooks) -> None:
    # the only way left to meet one: the owner took the address in another shape meanwhile
    assert h.mhfu_hook_word_when_quiet(X, 5, 0xA, b"a") == OK
    assert h.mhfu_hook_vtable(X, 5, b"a") == OK
    h.logged("")
    h.mhfu_hook_land_queued()
    h.mhfu_hook_land_queued()
    assert (h.logged("dropped"), h.peek(X)) == (1, 5)


def test_release_restores_and_unqueues(h: Hooks) -> None:
    h.poke(X, 1)
    h.poke(Y, 2)
    assert h.mhfu_hook_word(X, 0xA, b"a") == OK
    assert h.mhfu_hook_word_when_quiet(Y, 2, 0xA, b"a") == OK
    h.mhfu_hook_release(b"a")
    h.mhfu_hook_land_queued()
    assert (h.peek(X), h.peek(Y)) == (1, 2)
    assert h.mhfu_hook_word(X, 0xB, b"b") == OK


def test_detour_now_reuses_its_wrapper(h: Hooks) -> None:
    h.poke(D, ADDIU_SP, SW_RA)
    assert h.mhfu_hook_detour_now(D, ADDIU_SP, SW_RA, PRE, b"a") == OK
    wrapper = h.host_last_wrapper()
    patched = [h.mips.j(wrapper), 0]
    assert [h.peek(D), h.peek(D + 4)] == patched
    assert h.mhfu_hook_detour_now(D, ADDIU_SP, SW_RA, PRE, b"a") == OK
    h.poke(D, ADDIU_SP, SW_RA)  # the overlay reloaded
    assert h.mhfu_hook_detour_now(D, ADDIU_SP, SW_RA, PRE, b"a") == OK
    assert [h.peek(D), h.peek(D + 4)] == patched
    assert h.host_last_wrapper() == wrapper
    assert h.mhfu_hook_detour_now(D, ADDIU_SP, SW_RA, PRE, b"b") == CONFLICT
    h.poke(D, 0x68000001, SW_RA)  # neither ours nor the expected words
    h.logged("")
    assert all(h.mhfu_hook_detour_now(D, ADDIU_SP, SW_RA, PRE, b"a") == BADARG for _ in range(3))
    assert h.logged("detour") == 1


def test_detour_refuses_displacing_a_branch(h: Hooks) -> None:
    beq, jal, jr_ra = 0x10400003, h.mips.jal(0x1000), h.mips.jr("ra")
    for word in (beq, jal, jr_ra):
        assert h.mhfu_hook_detour(D, word, SW_RA, PRE, b"a") == BADARG
        assert h.mhfu_hook_detour_now(D, ADDIU_SP, word, PRE, b"a") == BADARG


def test_queued_detour_lands_both_words(h: Hooks) -> None:
    h.poke(D, ADDIU_SP, SW_RA)
    assert h.mhfu_hook_detour(D, ADDIU_SP, SW_RA, PRE, b"a") == OK
    h.mhfu_hook_land_queued()
    assert [h.peek(D), h.peek(D + 4)] == [h.mips.j(h.host_last_wrapper()), 0]
    h.mhfu_hook_release(b"a")
    assert [h.peek(D), h.peek(D + 4)] == [ADDIU_SP, SW_RA]


def test_call_hook_lands_once(h: Hooks) -> None:
    target = 0x1000
    h.poke(S, h.mips.jal(target))
    assert h.mhfu_hook_call(S, target, PRE, None, b"a") == OK
    h.mhfu_hook_land_queued()
    wrapper = h.host_last_wrapper()
    assert h.peek(S) == h.mips.jal(wrapper)
    assert h.mhfu_hook_call(S, target, PRE, None, b"a") == OK  # again, same helper
    h.logged("")
    h.mhfu_hook_land_queued()
    assert (h.peek(S), h.host_last_wrapper()) == (h.mips.jal(wrapper), wrapper)
    assert h.logged("waits") == 0


def test_release_all_restores_every_owner(h: Hooks) -> None:
    h.poke(X, 1)
    h.poke(Y, 2)
    h.poke(D, ADDIU_SP, SW_RA)
    assert h.mhfu_hook_word(X, 0xA, b"a") == OK
    assert h.mhfu_hook_vtable(Y, 0xB, b"b") == OK
    assert h.mhfu_hook_detour_now(D, ADDIU_SP, SW_RA, PRE, b"mhfu_events") == OK
    h.mhfu_hook_release_all()
    assert [h.peek(X), h.peek(Y), h.peek(D), h.peek(D + 4)] == [1, 2, ADDIU_SP, SW_RA]
