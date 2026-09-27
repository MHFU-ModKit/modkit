from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any, Literal, get_args

Button = Literal[
    "cross",
    "circle",
    "triangle",
    "square",
    "up",
    "down",
    "left",
    "right",
    "start",
    "select",
    "ltrigger",
    "rtrigger",
    "home",
    "screen",
    "note",
    "hold",
    "vol_up",
    "vol_down",
]
BUTTONS: tuple[Button, ...] = get_args(Button)


@dataclass(frozen=True, slots=True)
class CpuStatus:
    """The CPU's state; `pc` is exact only while stepping."""

    stepping: bool
    paused: bool
    pc: int
    ticks: int


@dataclass(frozen=True, slots=True)
class GameInfo:
    """The running game as PPSSPP identifies it."""

    id: str
    version: str
    title: str


@dataclass(frozen=True, slots=True)
class Breakpoint:
    """An execution breakpoint."""

    address: int
    stop: bool
    log: bool
    condition: str | None
    log_format: str | None
    symbol: str | None


@dataclass(frozen=True, slots=True)
class Watchpoint:
    """A memory breakpoint over `size` bytes from `address`."""

    address: int
    size: int
    read: bool
    write: bool
    change: bool
    stop: bool
    log: bool
    condition: str | None
    log_format: str | None
    symbol: str | None


@dataclass(frozen=True, slots=True)
class Instruction:
    """One disassembled line."""

    address: int
    size: int
    encoding: int
    name: str
    params: str

    def __str__(self) -> str:
        return f"{self.name} {self.params}".strip()


@dataclass(frozen=True, slots=True)
class FrameStats:
    """Frame and vblank rates, actual against target."""

    fps: float
    fps_target: float
    vblanks: float
    vblanks_target: float

    @property
    def speed(self) -> float:
        """Emulation speed as a multiple of real time."""
        return self.vblanks / self.vblanks_target if self.vblanks_target else 0.0


@dataclass(frozen=True, slots=True)
class Screenshot:
    """The current output frame as a PNG."""

    width: int
    height: int
    png: bytes


@dataclass(frozen=True, slots=True)
class Hit:
    """One trip of a breakpoint or watchpoint.

    From a stop: `pc` and `address` (the watchpoint's start) are known, the CPU is stepping.
    From a log line: `access`, `size` and `message` too; `pc` is None when a watchpoint has a
    custom log format.
    """

    kind: Literal["exec", "memory"]
    address: int
    pc: int | None
    stopped: bool
    access: Literal["read", "write"] | None = None
    size: int | None = None
    source: str | None = None
    message: str | None = None


# Broadcasts


@dataclass(frozen=True, slots=True)
class Stepping:
    """The CPU stopped (cpu.stepping).

    `requested` is whether this client asked for it, with a pause or a step. `reason` and
    `related_address` say why when PPSSPP tells; 1.20.4 leaves them out for breakpoints.
    """

    pc: int
    ticks: int
    reason: str | None
    related_address: int | None
    requested: bool = False


@dataclass(frozen=True, slots=True)
class Resumed:
    """The CPU runs again (cpu.resume)."""


@dataclass(frozen=True, slots=True)
class LogLine:
    """A line of PPSSPP's log."""

    timestamp: str
    header: str
    message: str
    level: int
    channel: str

    @property
    def hit(self) -> Hit | None:
        """The breakpoint or watchpoint trip this line reports, if it is one."""
        return parse_hit(self.message)


@dataclass(frozen=True, slots=True)
class GameEvent:
    """game.start, game.quit, game.pause or game.resume."""

    name: str
    game: GameInfo | None


@dataclass(frozen=True, slots=True)
class RawEvent:
    """Any other broadcast, as PPSSPP sent it."""

    name: str
    data: dict[str, Any]


Event = Stepping | Resumed | LogLine | GameEvent | RawEvent


def parse_event(msg: dict[str, Any]) -> Event:
    """A broadcast as its typed event."""
    name = msg.get("event", "")
    if name == "cpu.stepping":
        return stepping(msg)
    if name == "cpu.resume":
        return Resumed()
    if name == "log":
        return LogLine(
            msg["timestamp"],
            msg["header"],
            msg["message"].rstrip("\n"),
            msg["level"],
            msg["channel"],
        )
    if name.startswith("game."):
        return GameEvent(name, game_info(msg.get("game")))
    return RawEvent(name, msg)


_BKP = re.compile(r"BKP PC=(?P<pc>[0-9a-fA-F]{8})(?:: (?P<msg>.*)| \((?P<sym>.*)\))?\Z", re.S)
_CHK = re.compile(
    r"CHK (?P<access>Read|Write)(?P<bits>\d+)\((?P<source>[^)]*)\) at (?P<addr>[0-9a-fA-F]{8})"
    r"(?:: (?P<msg>.*)| \((?P<sym>.*)\), PC=(?P<pc>[0-9a-fA-F]{8}) \((?P<pcsym>.*)\))\Z",
    re.S,
)


def parse_hit(message: str) -> Hit | None:
    """Parse PPSSPP's `BKP PC=...` and `CHK Write16(CPU) at ...` log lines."""
    message = message.rstrip("\n")
    if m := _BKP.match(message):
        pc = int(m["pc"], 16)
        return Hit("exec", pc, pc, False, message=m["msg"] if m["msg"] is not None else m["sym"])
    if m := _CHK.match(message):
        return Hit(
            "memory",
            int(m["addr"], 16),
            int(m["pc"], 16) if m["pc"] else None,
            False,
            access="read" if m["access"] == "Read" else "write",
            size=int(m["bits"]) // 8,
            source=m["source"],
            message=m["msg"] if m["msg"] is not None else m["sym"],
        )
    return None


# Reply parsing


def stepping(msg: dict[str, Any]) -> Stepping:
    return Stepping(
        msg["pc"],
        msg["ticks"],
        msg.get("reason"),
        msg.get("relatedAddress"),
        msg.get("_requested", False),
    )


def cpu_status(msg: dict[str, Any]) -> CpuStatus:
    return CpuStatus(msg["stepping"], msg["paused"], msg["pc"], msg["ticks"])


def game_info(game: dict[str, Any] | None) -> GameInfo | None:
    return GameInfo(game["id"], game["version"], game["title"]) if game else None


def breakpoint(b: dict[str, Any]) -> Breakpoint:
    return Breakpoint(
        b["address"],
        b["enabled"],
        b["log"],
        b.get("condition"),
        b.get("logFormat"),
        b.get("symbol"),
    )


def watchpoint(w: dict[str, Any]) -> Watchpoint:
    return Watchpoint(
        w["address"],
        w["size"],
        w.get("read", False),
        w.get("write", False),
        w.get("change", False),
        w["enabled"],
        w.get("log", False),
        w.get("condition"),
        w.get("logFormat"),
        w.get("symbol"),
    )


def instruction(line: dict[str, Any]) -> Instruction:
    return Instruction(
        line["address"], line["addressSize"], line["encoding"], line["name"], line["params"]
    )


def frame_stats(msg: dict[str, Any]) -> FrameStats:
    fps, vblanks = msg["fps"], msg["vblanksPerSecond"]
    return FrameStats(fps["actual"], fps["target"], vblanks["actual"], vblanks["target"])


def screenshot(msg: dict[str, Any]) -> Screenshot:
    header, _, data = msg["uri"].partition(",")
    if header != "data:image/png;base64":
        raise ValueError(f"unexpected screenshot encoding {header!r}")
    return Screenshot(msg["width"], msg["height"], base64.b64decode(data))
