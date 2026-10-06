# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A typed client for PPSSPP's WebSocket debugger, blocking (`Client`) and asyncio (`AsyncClient`).

from ppsspp_debug import Client

with Client.connect() as ppsspp:
    ppsspp.read_u32(address)
    with ppsspp.watchpoint(address, 2) as hits:
        hit = hits.next(timeout=30)
        ppsspp.registers()
        ppsspp.resume()
"""

from ._client import AsyncClient, Stream
from ._errors import DebuggerError, Disconnected, Unsupported
from ._lane import AppEmulator, Lane
from ._launch import DockerEmulator, LocalEmulator, emulators, find_debuggers
from ._sync import Client, SyncStream
from ._types import (
    BUTTONS,
    Breakpoint,
    Button,
    CpuStatus,
    Event,
    FrameStats,
    GameEvent,
    GameInfo,
    Hit,
    Instruction,
    LogLine,
    RawEvent,
    Resumed,
    Screenshot,
    Speed,
    Stepping,
    Watchpoint,
    parse_hit,
)

__all__ = [
    "BUTTONS",
    "AppEmulator",
    "AsyncClient",
    "Breakpoint",
    "Button",
    "Client",
    "CpuStatus",
    "DebuggerError",
    "Disconnected",
    "DockerEmulator",
    "Event",
    "FrameStats",
    "GameEvent",
    "GameInfo",
    "Hit",
    "Instruction",
    "Lane",
    "LocalEmulator",
    "LogLine",
    "RawEvent",
    "Resumed",
    "Screenshot",
    "Speed",
    "Stepping",
    "Stream",
    "SyncStream",
    "Unsupported",
    "Watchpoint",
    "emulators",
    "find_debuggers",
    "parse_hit",
]
