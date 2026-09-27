"""A fake PPSSPP debugger for tests, with the real one's quirks.

    async with FakePPSSPP() as fake:
        async with AsyncClient.connect(port=fake.port) as ppsspp:
            ...

Quirks it keeps: cpu.stepping and cpu.resume answer with a broadcast instead of a reply, errors
come back with the request's ticket, an unknown event is an error, cpu.stepping on a stopped CPU
does nothing, a breakpoint stop comes without its reason, the log goes only to the newest
connection and stops for all when any connection closes, log lines held while the log was
switched off arrive when it is switched on, gpu.stats.get never answers while stopped, and
input.buttons.press answers only once the button is released.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request

Handler = Callable[[ServerConnection, dict[str, Any]], Awaitable[None]]

# one transparent pixel, enough to stand in for a frame
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


class FakePPSSPP:
    """A PPSSPP debugger server on a free local port, with `size` bytes of memory at `base`.

    `patched` adds the modkit build's savestate and game.speed commands. `handshake_delay`
    stalls each WebSocket upgrade, as PPSSPP's startup phone-home does. `reasons` adds why the
    CPU stopped to cpu.stepping, which PPSSPP 1.20.4 leaves out for breakpoints.
    """

    def __init__(
        self,
        *,
        base: int = 0x1000_0000,
        size: int = 0x1_0000,
        patched: bool = False,
        handshake_delay: float = 0.0,
        reasons: bool = False,
    ) -> None:
        self.base = base
        self.reasons = reasons
        self.memory = bytearray(size)
        self.patched = patched
        self.handshake_delay = handshake_delay
        self.stepping = False
        self.pc = base
        self.ticks = 0
        self.regs: dict[str, int] = {"zero": 0, "a0": 0, "a1": 0, "ra": 0, "sp": 0}
        self.game: dict[str, str] | None = {"id": "TEST00000", "version": "1.00", "title": "Test"}
        self.breakpoints: dict[int, dict[str, Any]] = {}
        self.watchpoints: dict[tuple[int, int], dict[str, Any]] = {}
        self.speed = 60
        self.states: dict[str, bytes] = {}
        self.received: list[dict[str, Any]] = []
        self.port = 0
        self.connections: list[ServerConnection] = []
        self._config: dict[ServerConnection, dict[str, bool]] = {}
        self._log_owner: ServerConnection | None = None
        self._server: Server | None = None
        self._held: list[dict[str, Any]] = []
        self._table = self._handlers()

    async def __aenter__(self) -> FakePPSSPP:
        self._server = await serve(
            self._serve, "127.0.0.1", self.port, process_request=self._handshake
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handshake(self, conn: ServerConnection, request: Request) -> None:
        await asyncio.sleep(self.handshake_delay)

    @property
    def disallowed(self) -> dict[str, bool]:
        """The broadcast switches of the first open connection."""
        return self._config[self.connections[0]]

    async def _serve(self, ws: ServerConnection) -> None:
        self.connections.append(ws)
        self._config[ws] = {"logger": False, "game": False, "input": False, "stepping": False}
        self._log_owner, self._held = ws, []
        try:
            async for raw in ws:
                msg = json.loads(raw)
                self.received.append(msg)
                handler = self._table.get(msg["event"])
                if handler is None:
                    await self._fail(ws, msg, "Bad message: unknown event")
                else:
                    await handler(ws, msg)
        except ConnectionClosed:
            pass
        finally:
            self.connections.remove(ws)
            del self._config[ws]
            self._log_owner = None

    # what tests drive

    async def broadcast(self, msg: dict[str, Any]) -> None:
        """Send an event to every client that has not switched its kind off."""
        name = msg["event"]
        switch = (
            "logger"
            if name == "log"
            else "stepping"
            if name in ("cpu.stepping", "cpu.resume")
            else name.partition(".")[0]
        )
        if switch == "logger":
            owner = self._log_owner
            if owner is not None and self._config[owner]["logger"]:
                self._held = [*self._held, msg][-1024:]
            elif owner is not None:
                await owner.send(json.dumps(msg))
            return
        for ws in list(self.connections):
            if not self._config[ws].get(switch, False):
                await ws.send(json.dumps(msg))

    async def log(self, message: str, channel: str = "MEMMAP") -> None:
        await self.broadcast(
            {
                "event": "log",
                "timestamp": "00:00:000",
                "header": "",
                "message": message + "\n",
                "level": 1,
                "channel": channel,
            }
        )

    async def stop(self, reason: str | None = None, related: int = 0) -> None:
        """Enter stepping, as a breakpoint or a pause does."""
        self.stepping = True
        self.pc = related if reason == "cpu.breakpoint" else self.pc
        msg: dict[str, Any] = {"event": "cpu.stepping", "pc": self.pc, "ticks": self.ticks}
        if reason is not None:
            msg |= {"reason": reason, "relatedAddress": related}
        await self.broadcast(msg)

    async def execute(self, pc: int) -> None:
        """Run the instruction at `pc`, tripping a breakpoint there."""
        self.pc, self.ticks = pc, self.ticks + 1
        bp = self.breakpoints.get(pc)
        if bp is None or self.stepping:
            return
        if bp["log"]:
            text = f": {bp['logFormat']}" if bp.get("logFormat") else " (z_un_test)"
            await self.log(f"BKP PC={pc:08x}{text}", "JIT")
        if bp["enabled"]:
            await self.stop("cpu.breakpoint" if self.reasons else None, pc)

    async def access(self, address: int, size: int = 4, *, write: bool = True, pc: int = 0) -> None:
        """A memory access by the game at `pc`, tripping a watchpoint that covers it."""
        self.pc, self.ticks = pc or self.pc, self.ticks + 1
        for (start, length), wp in self.watchpoints.items():
            if not (start <= address < start + length) or not wp["write" if write else "read"]:
                continue
            if wp["log"]:
                kind = f"{'Write' if write else 'Read'}{size * 8}(CPU) at {address:08x}"
                text = (
                    f": {wp['logFormat']}"
                    if wp.get("logFormat")
                    else f" (data), PC={self.pc:08x} (z_un_test)"
                )
                await self.log(f"CHK {kind}{text}")
            if wp["enabled"] and not self.stepping:
                await self.stop("memory.breakpoint" if self.reasons else None, start)

    # protocol

    async def _reply(self, ws: ServerConnection, msg: dict[str, Any], **data: Any) -> None:
        out = {"event": msg["event"], **data}
        if "ticket" in msg:
            out["ticket"] = msg["ticket"]
        await ws.send(json.dumps(out))

    async def _fail(self, ws: ServerConnection, msg: dict[str, Any], message: str) -> None:
        out: dict[str, Any] = {"event": "error", "message": message, "level": 2}
        if "ticket" in msg:
            out["ticket"] = msg["ticket"]
        await ws.send(json.dumps(out))

    def _span(self, address: int, size: int) -> slice | None:
        start = address - self.base
        return (
            slice(start, start + size) if 0 <= start and start + size <= len(self.memory) else None
        )

    def _handlers(self) -> dict[str, Handler]:
        handlers: dict[str, Handler] = {
            "version": self._version,
            "broadcast.config.set": self._broadcast_config,
            "memory.read": self._read,
            "memory.write": self._write,
            "memory.readString": self._read_string,
            "memory.disasm": self._disasm,
            "cpu.status": self._status,
            "cpu.stepping": self._pause,
            "cpu.resume": self._resume,
            "cpu.stepInto": self._step,
            "cpu.stepOver": self._step,
            "cpu.stepOut": self._step,
            "cpu.runUntil": self._step,
            "cpu.getAllRegs": self._all_regs,
            "cpu.getReg": self._get_reg,
            "cpu.setReg": self._set_reg,
            "cpu.evaluate": self._evaluate,
            "cpu.breakpoint.add": self._add_breakpoint,
            "cpu.breakpoint.remove": self._remove_breakpoint,
            "cpu.breakpoint.list": self._list_breakpoints,
            "memory.breakpoint.add": self._add_watchpoint,
            "memory.breakpoint.remove": self._remove_watchpoint,
            "memory.breakpoint.list": self._list_watchpoints,
            "input.buttons.send": self._ok,
            "input.buttons.press": self._press,
            "input.analog.send": self._ok,
            "game.status": self._game_status,
            "game.reset": self._ok,
            "gpu.stats.get": self._stats,
            "gpu.buffer.screenshot": self._screenshot,
        }
        for width in (8, 16, 32):
            handlers[f"memory.read_u{width}"] = self._read_int
            handlers[f"memory.write_u{width}"] = self._write_int
        if self.patched:
            handlers |= {
                "savestate.save": self._save_state,
                "savestate.load": self._load_state,
                "game.speed": self._speed,
            }
        return handlers

    async def _ok(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        await self._reply(ws, msg)

    async def _version(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        await self._reply(ws, msg, name="PPSSPP", version="v1.20.4-fake")

    async def _broadcast_config(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        self._config[ws] |= msg.get("disallowed", {})
        await self._reply(ws, msg, disallowed=self._config[ws])
        if ws is self._log_owner and not self._config[ws]["logger"]:
            held, self._held = self._held, []
            for line in held:
                await ws.send(json.dumps(line))

    async def _read(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        span = self._span(msg["address"], msg["size"])
        if span is None:
            return await self._fail(ws, msg, "Invalid address or size")
        await self._reply(ws, msg, base64=base64.b64encode(self.memory[span]).decode())

    async def _write(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        data = base64.b64decode(msg["base64"])
        span = self._span(msg["address"], len(data))
        if span is None:
            return await self._fail(ws, msg, "Invalid address or size")
        self.memory[span] = data
        await self._reply(ws, msg)

    async def _read_int(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        width = int(msg["event"].rpartition("u")[2]) // 8
        span = self._span(msg["address"], width)
        if span is None:
            return await self._fail(ws, msg, "Invalid address")
        await self._reply(ws, msg, value=int.from_bytes(self.memory[span], "little"))

    async def _write_int(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        width = int(msg["event"].rpartition("u")[2]) // 8
        span = self._span(msg["address"], width)
        if span is None:
            return await self._fail(ws, msg, "Invalid address")
        self.memory[span] = int(msg["value"]).to_bytes(width, "little")
        await self._reply(ws, msg, value=msg["value"])

    async def _read_string(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        span = self._span(msg["address"], 1)
        if span is None:
            return await self._fail(ws, msg, "Invalid address")
        raw = self.memory[span.start :].split(b"\0", 1)[0]
        await self._reply(ws, msg, value=raw.decode())

    async def _disasm(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        start = msg["address"]
        lines = [
            {
                "type": "opcode",
                "address": start + 4 * i,
                "addressSize": 4,
                "encoding": 0,
                "name": "nop",
                "params": "",
            }
            for i in range(msg.get("count", 1))
        ]
        await self._reply(ws, msg, range={"start": start}, branchGuides=[], lines=lines)

    async def _status(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        await self._reply(
            ws, msg, stepping=self.stepping, paused=False, pc=self.pc, ticks=self.ticks
        )

    async def _pause(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        if not self.stepping:
            await self.stop()

    async def _resume(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        if not self.stepping:
            return await self._fail(ws, msg, "CPU not stepping")
        self.stepping = False
        await self.broadcast({"event": "cpu.resume"})

    async def _step(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        if not self.stepping:
            return await self._fail(ws, msg, "CPU currently running (cpu.stepping first)")
        self.pc = msg.get("address", self.pc + 4)
        await self.stop()

    async def _all_regs(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        names = [*self.regs, "pc"]
        values = [*self.regs.values(), self.pc]
        category = {"id": 0, "name": "GPR", "registerNames": names, "uintValues": values}
        await self._reply(ws, msg, categories=[category | {"floatValues": ["0"] * len(names)}])

    async def _get_reg(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        if msg["name"] not in self.regs:
            return await self._fail(ws, msg, "Invalid 'name' parameter")
        await self._reply(ws, msg, category=0, register=0, uintValue=self.regs[msg["name"]])

    async def _set_reg(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        if not self.stepping:
            return await self._fail(ws, msg, "CPU currently running (cpu.stepping first)")
        value = msg["value"]
        self.regs[msg["name"]] = value if isinstance(value, int) else int(float(value))
        await self._reply(ws, msg, category=0, register=0, uintValue=self.regs[msg["name"]])

    async def _evaluate(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        await self._reply(ws, msg, uintValue=int(msg["expression"], 0), floatValue="0")

    async def _add_breakpoint(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        self.breakpoints[msg["address"]] = {
            "address": msg["address"],
            "enabled": msg.get("enabled", True),
            "log": msg.get("log", False),
            "condition": msg.get("condition"),
            "logFormat": msg.get("logFormat"),
            "symbol": None,
            "code": "nop",
        }
        await self._reply(ws, msg)

    async def _remove_breakpoint(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        self.breakpoints.pop(msg["address"], None)
        await self._reply(ws, msg)

    async def _list_breakpoints(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        await self._reply(ws, msg, breakpoints=list(self.breakpoints.values()))

    async def _add_watchpoint(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        key = (msg["address"], msg["size"])
        self.watchpoints[key] = {
            "address": msg["address"],
            "size": msg["size"],
            "enabled": msg.get("enabled", True),
            "log": msg.get("log", False),
            "read": msg.get("read", False),
            "write": msg.get("write", False),
            "change": msg.get("change", False),
            "condition": msg.get("condition"),
            "logFormat": msg.get("logFormat"),
            "symbol": None,
        }
        await self._reply(ws, msg)

    async def _remove_watchpoint(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        self.watchpoints.pop((msg["address"], msg["size"]), None)
        await self._reply(ws, msg)

    async def _list_watchpoints(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        await self._reply(ws, msg, breakpoints=list(self.watchpoints.values()))

    async def _press(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        async def release() -> None:
            await asyncio.sleep(msg.get("duration", 1) / 60)
            await self._reply(ws, msg)

        asyncio.get_running_loop().create_task(release())

    async def _game_status(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        await self._reply(ws, msg, game=self.game, paused=False)

    async def _stats(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        if not self.stepping:
            rate = {"actual": 59.94 * self.speed / 60, "target": 59.94}
            await self._reply(ws, msg, fps=rate, vblanksPerSecond=rate, info="", timing={})

    async def _screenshot(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        uri = "data:image/png;base64," + base64.b64encode(_PNG).decode()
        await self._reply(ws, msg, width=1, height=1, uri=uri)

    async def _save_state(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        self.states[msg["path"]] = bytes(self.memory)
        await self._reply(ws, msg)

    async def _load_state(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        if msg["path"] not in self.states:
            return await self._fail(ws, msg, "Failed to load state")
        self.memory[:] = self.states[msg["path"]]
        await self._reply(ws, msg)

    async def _speed(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        if "limit" in msg:
            self.speed = msg["limit"]
        await self._reply(ws, msg, limit=self.speed)
