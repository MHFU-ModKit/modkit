"""lua_host's hand-over to the exec thread (mods/lua_host/marshal.cpp), on host threads."""

import ctypes
import threading
from collections.abc import Callable

import pytest

DOUBLE, PANIC = 1, 2


@pytest.fixture
def lib(host_lib: Callable[..., ctypes.CDLL]) -> ctypes.CDLL:
    lib = host_lib("mods/lua_host/marshal.cpp", "tests/marshal_host.cpp")
    lib.host_marshal.restype = ctypes.c_uint32
    lib.host_marshal.argtypes = [ctypes.c_int, ctypes.c_uint32]
    lib.host_marshal_in_vm.restype = ctypes.c_uint32
    lib.host_marshal_in_vm.argtypes = [ctypes.c_uint32]
    assert lib.host_start() == 0
    return lib


def finishes(fn: Callable[[], object], timeout: float = 5.0) -> object:
    """fn's result, or a failure when it has not returned within timeout (a deadlock)."""
    box: list[object] = []
    t = threading.Thread(target=lambda: box.append(fn()), daemon=True)
    t.start()
    t.join(timeout)
    assert box, "deadlocked"
    return box[0]


def test_answers(lib: ctypes.CDLL) -> None:
    assert finishes(lambda: lib.host_marshal(DOUBLE, 21)) == 42


def test_producers_get_their_own_answers(lib: ctypes.CDLL) -> None:
    wrong: list[tuple[int, int]] = []

    def producer(base: int) -> None:
        for v in range(base, base + 40):
            got = lib.host_marshal(DOUBLE, v)
            if got != 2 * v:
                wrong.append((v, got))

    threads = [
        threading.Thread(target=producer, args=(1000 * k,), daemon=True) for k in range(1, 7)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not any(t.is_alive() for t in threads), "deadlocked"
    assert wrong == []


def test_inside_the_vm_passes_through(lib: ctypes.CDLL) -> None:
    assert finishes(lambda: lib.host_marshal_in_vm(5)) == 5


def test_panic_answers_then_passes_through(lib: ctypes.CDLL) -> None:
    try:
        assert finishes(lambda: lib.host_marshal(PANIC, 7)) == 7
        assert finishes(lambda: lib.host_marshal(DOUBLE, 3)) == 3
    finally:
        lib.host_release_parked()


def test_stopped_passes_through(lib: ctypes.CDLL) -> None:
    lib.host_stop()
    assert finishes(lambda: lib.host_marshal(DOUBLE, 9)) == 9
