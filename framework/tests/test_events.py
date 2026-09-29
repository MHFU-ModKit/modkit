"""The event registry (src/core/events.cpp), built on the host with stub installers."""

import ctypes
import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

FRAMEWORK = Path(__file__).parents[1]

# the registry's collaborators: a log that drops its line and installers whose result and
# call count the test controls, indexed by the event that installs them
STUBS = r"""
extern "C" {
int g_install_rc[64];
int g_install_calls[64];
void mhfu_log(const char *, ...) {}
static int inst(int ev) { g_install_calls[ev]++; return g_install_rc[ev]; }
int mhfu_quest_install_targets(void)     { return inst(%(QUEST_TARGETS_BUILDING)d); }
int mhfu_ai_install_overlay_loaded(void) { return inst(%(AI_OVERLAY_LOADED)d); }
int mhfu_ai_install_slot_picked(void)    { return inst(%(BIGMONSTER_SLOT_PICKED)d); }
int mhfu_ai_install_picker(void)         { return inst(%(BIGMONSTER_ACTION_INPUT)d); }
int mhfu_ai_install_ai_step(void)        { return inst(%(BIGMONSTER_AI_STEP)d); }
int mhfu_ai_install_action(void)         { return inst(%(BIGMONSTER_ACTION)d); }
}
"""


def _event_ids() -> dict[str, int]:
    text = (FRAMEWORK / "include/mhfu/events.h").read_text()
    body = re.search(r"typedef enum \{(.*?)\} mhfu_event_id_t;", text, re.S)
    assert body
    names = re.findall(r"^\s*MHFU_EVENT_(\w+?),", body.group(1), re.M)
    return {name: i for i, name in enumerate(names)}


def _max_handlers() -> int:
    text = (FRAMEWORK / "src/core/internal.h").read_text()
    m = re.search(r"#define MHFU_EVENT_MAX_HANDLERS (\d+)", text)
    assert m
    return int(m.group(1))


EV = _event_ids()
MAX = _max_handlers()
OK, CONFLICT, NOSPACE, BADARG = 0, -1, -2, -3

Fn = ctypes.CFUNCTYPE(None)
Notify = ctypes.CFUNCTYPE(None, ctypes.c_void_p)


class Handler(ctypes.Structure):
    _fields_ = [("fn", ctypes.c_void_p), ("priority", ctypes.c_int), ("owner", ctypes.c_char_p)]


class Registry:
    def __init__(self, lib: ctypes.CDLL) -> None:
        self.lib = lib
        lib.mhfu_event_on.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p]
        lib.mhfu_event_release.argtypes = [ctypes.c_char_p]
        lib.mhfu_event_handlers.argtypes = [ctypes.c_int, ctypes.POINTER(Handler)]
        lib.mhfu_event_fire.argtypes = [ctypes.c_int, ctypes.c_void_p]
        self.rc = (ctypes.c_int * 64).in_dll(lib, "g_install_rc")
        self.calls = (ctypes.c_int * 64).in_dll(lib, "g_install_calls")
        self.keep: list[object] = []

    def fn(self) -> int:
        f = Fn(lambda: None)
        self.keep.append(f)
        return ctypes.cast(f, ctypes.c_void_p).value or 0

    def on(self, ev: str, fn: int | None, prio: int = 0, owner: bytes | None = b"m") -> int:
        return int(self.lib.mhfu_event_on(EV[ev], fn, prio, owner))

    def handlers(self, ev: str) -> list[tuple[int, int, bytes]]:
        out = (Handler * MAX)()
        n = self.lib.mhfu_event_handlers(EV[ev], out)
        return [(h.fn, h.priority, h.owner) for h in out[:n]]


@pytest.fixture
def reg(host_lib: Callable[..., ctypes.CDLL], tmp_path: Path) -> Iterator[Registry]:
    stubs = tmp_path / "stubs.cpp"
    stubs.write_text(STUBS % EV)
    yield Registry(host_lib("src/core/events.cpp", str(stubs)))


def test_priority_order_ties_in_registration_order(reg: Registry) -> None:
    a, b, c, d = reg.fn(), reg.fn(), reg.fn(), reg.fn()
    for fn, prio in ((a, 0), (b, 10), (c, -5), (d, 10)):
        assert reg.on("MONSTER_SPAWNED", fn, prio) == OK
    assert [h[0] for h in reg.handlers("MONSTER_SPAWNED")] == [b, d, a, c]


def test_same_fn_twice(reg: Registry) -> None:
    a = reg.fn()
    assert reg.on("QUEST_BEGINNING", a, 1) == OK
    assert reg.on("QUEST_BEGINNING", a, 7) == OK
    assert reg.handlers("QUEST_BEGINNING") == [(a, 1, b"m")]
    assert reg.on("QUEST_BEGINNING", a, 0, b"other") == OK
    assert len(reg.handlers("QUEST_BEGINNING")) == 2


def test_release_drops_only_that_owner(reg: Registry) -> None:
    a, b = reg.fn(), reg.fn()
    reg.on("QUEST_ENTERED", a, 0, b"one")
    reg.on("QUEST_ENTERED", b, 0, b"two")
    reg.on("MAP_SECTION_ENTERED", a, 0, b"one")
    reg.lib.mhfu_event_release(b"one")
    assert reg.handlers("QUEST_ENTERED") == [(b, 0, b"two")]
    assert reg.lib.mhfu_event_count(EV["MAP_SECTION_ENTERED"]) == 0


def test_installer_once_and_failure_is_conflict(reg: Registry) -> None:
    ev = "BIGMONSTER_ACTION"
    reg.rc[EV[ev]] = -1
    assert reg.on(ev, reg.fn()) == CONFLICT
    assert reg.lib.mhfu_event_count(EV[ev]) == 0
    reg.rc[EV[ev]] = 0
    assert reg.on(ev, reg.fn()) == OK
    assert reg.on(ev, reg.fn(), 0, b"x") == OK
    assert reg.calls[EV[ev]] == 2
    assert reg.lib.mhfu_event_count(EV[ev]) == 2


def test_bad_arguments_and_full(reg: Registry) -> None:
    assert reg.on("QUEST_BEGINNING", None) == BADARG
    assert reg.on("QUEST_BEGINNING", reg.fn(), 0, None) == BADARG
    assert reg.on("QUEST_BEGINNING", reg.fn(), 0, b"") == BADARG
    assert reg.lib.mhfu_event_on(len(EV), reg.fn(), 0, b"m") == BADARG
    for _ in range(MAX):
        assert reg.on("QUEST_BEGINNING", reg.fn()) == OK
    assert reg.on("QUEST_BEGINNING", reg.fn()) == NOSPACE


def test_fire_calls_each_with_ctx(reg: Registry) -> None:
    seen: list[tuple[str, int | None]] = []
    cbs = [Notify(lambda ctx, tag=tag: seen.append((tag, ctx))) for tag in "ab"]
    for prio, cb in zip((1, 2), cbs, strict=True):
        reg.on("MONSTER_SPAWNED", ctypes.cast(cb, ctypes.c_void_p).value, prio)
    reg.lib.mhfu_event_fire(EV["MONSTER_SPAWNED"], 0x1234)
    assert seen == [("b", 0x1234), ("a", 0x1234)]
