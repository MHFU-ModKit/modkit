# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
from __future__ import annotations

import asyncio
import base64
import itertools
import json
import logging
import os
import struct
from collections import deque
from collections.abc import AsyncIterator, Callable, Collection, Generator, Iterable
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from importlib.metadata import version
from types import TracebackType
from typing import Any, Generic, Literal, TypeVar

from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, InvalidMessage
from websockets.typing import Subprotocol

from . import _types as t
from ._errors import DebuggerError, Disconnected, Unsupported
from ._launch import find_debuggers

log = logging.getLogger("ppsspp_debug")

SUBPROTOCOL = Subprotocol("debugger.ppsspp.org")  # optional for PPSSPP, harmless to offer
QUEUE_LIMIT = 10_000  # events kept per stream before the oldest are dropped
_VERSION = version("ppsspp-debug")

# broadcast.config switches a stream can turn on; "stepping" stays on because pause, resume and
# stops rely on it, and "logger" stays off because the log comes over a connection of its own
_SWITCHES = ("game", "input", "breakpoint")

T = TypeVar("T")


def _switch(event: str) -> str | None:
    if event.startswith(("game.", "input.")):
        return event.partition(".")[0]
    if event == "cpu.breakpoint.hit":
        return "breakpoint"
    return None


class _Subscription:
    """A bounded queue of the broadcasts one stream asked for."""

    def __init__(self, names: Collection[str] | None) -> None:
        self.names = frozenset(names) if names is not None else None
        self.switches = (
            {s for n in self.names if (s := _switch(n))} if self.names is not None else {*_SWITCHES}
        )
        self.logs = self.wants("log")
        self.dropped = 0
        self._items: deque[dict[str, Any]] = deque(maxlen=QUEUE_LIMIT)
        self._ready = asyncio.Event()
        self._closed = False

    def wants(self, event: str) -> bool:
        return self.names is None or event in self.names

    def put(self, msg: dict[str, Any]) -> None:
        if len(self._items) == QUEUE_LIMIT:
            self.dropped += 1
        self._items.append(msg)
        self._ready.set()

    def clear(self) -> None:
        self._items.clear()

    def drain(self) -> list[dict[str, Any]]:
        items = list(self._items)
        self._items.clear()
        return items

    def close(self) -> None:
        self._closed = True
        self._ready.set()

    async def get(self) -> dict[str, Any]:
        while not self._items:
            if self._closed:
                raise Disconnected("the debugger connection closed")
            self._ready.clear()
            await self._ready.wait()
        return self._items.popleft()


class Stream(Generic[T]):
    """Events or hits in arrival order: iterate it, or take one at a time with `next()`."""

    def __init__(self, sub: _Subscription, convert: Callable[[t.Event], T | None]) -> None:
        self._sub = sub
        self._convert = convert

    @property
    def dropped(self) -> int:
        """How many events were discarded because the stream was not read fast enough."""
        return self._sub.dropped

    async def next(self, timeout: float | None = None) -> T:
        """The next item; raises TimeoutError after `timeout` seconds."""
        async with asyncio.timeout(timeout):
            while True:
                item = self._convert(t.parse_event(await self._sub.get()))
                if item is not None:
                    return item

    def _untaken(self) -> bool:
        """Whether an item arrived that nobody took; clears the queue."""
        return any(self._convert(t.parse_event(m)) is not None for m in self._sub.drain())

    def __aiter__(self) -> Stream[T]:
        return self

    async def __anext__(self) -> T:
        try:
            return await self.next()
        except Disconnected:
            raise StopAsyncIteration from None


class _LogFeed:
    """A connection of its own for PPSSPP's log.

    PPSSPP 1.20.4 sends the log only to its newest debugger connection, and closing any
    connection stops the log for all of them; a fresh connection owns it again. Newer builds
    send the log to every connection, where one of its own does no harm.
    """

    def __init__(self, ws: ClientConnection, dispatch: Callable[[dict[str, Any]], None]) -> None:
        self._ws = ws
        self._reader = asyncio.create_task(self._read(dispatch), name="ppsspp-debug log")

    @classmethod
    async def open(
        cls, uri: str, timeout: float, dispatch: Callable[[dict[str, Any]], None]
    ) -> _LogFeed:
        ws = await _ws_open(uri, timeout)
        off = {"logger": False, "game": True, "input": True, "stepping": True}  # 1.20.4's kinds
        await ws.send(json.dumps({"event": "broadcast.config.set", "disallowed": off}))
        return cls(ws, dispatch)

    async def _read(self, dispatch: Callable[[dict[str, Any]], None]) -> None:
        with suppress(ConnectionClosed):
            async for raw in self._ws:
                msg = json.loads(raw)
                if msg.get("event") == "log":
                    dispatch(msg)

    async def close(self) -> None:
        await self._ws.close()
        await self._reader


async def _ws_open(uri: str, timeout: float) -> ClientConnection:
    return await ws_connect(
        uri,
        subprotocols=[SUBPROTOCOL],
        open_timeout=timeout,
        max_size=2**27,
        compression=None,
    )


class _Connecting:
    """What `AsyncClient.connect()` returns: await it, or use it with `async with`."""

    def __init__(self, host: str, port: int | None, timeout: float, request_timeout: float) -> None:
        self._args = (host, port, timeout, request_timeout)
        self._client: AsyncClient | None = None

    def __await__(self) -> Generator[Any, None, AsyncClient]:
        return AsyncClient._open(*self._args).__await__()

    async def __aenter__(self) -> AsyncClient:
        self._client = await AsyncClient._open(*self._args)
        return self._client

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.close()


class AsyncClient:
    """One connection to PPSSPP's debugger; open it with `AsyncClient.connect()`.

    A single reader task drains the socket and never runs caller code, so any method may be
    awaited at any time, including while the CPU is stopped at a breakpoint.
    """

    def __init__(self, ws: ClientConnection, host: str, port: int, timeout: float) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        """Seconds to wait for each reply."""
        self.server = ""
        """The server's name and version, such as "PPSSPP v1.20.4"."""
        self._ws = ws
        self._tickets = itertools.count(1)
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._subs: set[_Subscription] = set()
        self._disallowed: dict[str, bool] | None = None
        self._configurable = True
        self._hit_events = False
        self._log: _LogFeed | None = None
        self._log_users = 0
        self._log_lock = asyncio.Lock()
        self._requested_stops = 0
        self._stopping_breakpoints: set[int] = set()
        self._reader = asyncio.create_task(self._read(), name="ppsspp-debug reader")

    @staticmethod
    def connect(
        host: str = "127.0.0.1",
        port: int | None = None,
        *,
        timeout: float = 30.0,
        request_timeout: float = 5.0,
    ) -> _Connecting:
        """Connect, retrying until `timeout` while PPSSPP starts or its handshake stalls.

        Without a port, it is read from the PPSSPP process running on this machine.
        """
        return _Connecting(host, port, timeout, request_timeout)

    @classmethod
    async def _open(
        cls, host: str, port: int | None, timeout: float, request_timeout: float
    ) -> AsyncClient:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            error: Exception
            found = [port] if port is not None else find_debuggers()
            if len(found) > 1:
                raise ConnectionError(f"several PPSSPP debuggers listen on {found}; pass port=")
            if found:
                try:
                    ws = await _ws_open(_uri(host, found[0]), max(deadline - loop.time(), 0.1))
                    break
                except (OSError, TimeoutError, InvalidMessage) as e:
                    error = e
            else:
                error = ConnectionError("no PPSSPP process with an open debugger port")
            if loop.time() + 0.25 >= deadline:
                raise ConnectionError(
                    f"no PPSSPP debugger at {host}:{port or '?'} within {timeout:g}s: {error}. "
                    "Is RemoteDebuggerOnStartup = True in ppsspp.ini?"
                ) from error
            await asyncio.sleep(0.25)

        client = cls(ws, host, found[0], request_timeout)
        try:
            reply = await client.request("version", name="ppsspp-debug", version=_VERSION)
            client.server = f"{reply['name']} {reply['version']}"
            client._hit_events = await client._probe_hit_events()
            await client._sync_broadcasts()
        except BaseException:
            await client.close()
            raise
        return client

    async def close(self) -> None:
        """Close the connection; breakpoints stay armed in PPSSPP."""
        if self._log is not None:
            await self._log.close()
            self._log = None
        await self._ws.close()
        with suppress(asyncio.CancelledError):
            await self._reader

    @property
    def closed(self) -> bool:
        return self._reader.done()

    async def __aenter__(self) -> AsyncClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # transport

    async def _read(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    log.warning("ignoring a message that is not JSON: %.80r", raw)
                    continue
                ticket = msg.get("ticket")
                if ticket is not None:
                    reply = self._pending.pop(ticket, None)
                    if reply is not None and not reply.done():
                        reply.set_result(msg)
                    continue
                event = msg.get("event", "")
                if event == "error":
                    log.warning("PPSSPP: %s", msg.get("message"))
                elif event == "cpu.stepping" and self._requested_stops:
                    self._requested_stops -= 1
                    msg["_requested"] = True
                self._dispatch(msg)
        except ConnectionClosed:
            pass
        finally:
            for reply in self._pending.values():
                if not reply.done():
                    reply.set_exception(Disconnected("the debugger connection closed"))
            self._pending.clear()
            for sub in self._subs:
                sub.close()

    def _dispatch(self, msg: dict[str, Any]) -> None:
        event = msg.get("event", "")
        for sub in self._subs:
            if sub.wants(event):
                sub.put(msg)

    def _ticket(self) -> tuple[int, asyncio.Future[dict[str, Any]]]:
        if self.closed:
            raise Disconnected("the debugger connection is closed")
        ticket = next(self._tickets)
        reply = self._pending[ticket] = asyncio.get_running_loop().create_future()
        return ticket, reply

    async def _send(self, msg: dict[str, Any]) -> None:
        try:
            await self._ws.send(json.dumps(msg))
        except ConnectionClosed as e:
            raise Disconnected("the debugger connection closed") from e

    @staticmethod
    def _check(event: str, msg: dict[str, Any]) -> dict[str, Any]:
        if msg.get("event") == "error":
            message = str(msg.get("message", ""))
            raise (Unsupported if message.endswith("unknown event") else DebuggerError)(
                event, message
            )
        return msg

    async def _call(
        self, event: str, params: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any]:
        timeout = self.timeout if timeout is None else timeout
        ticket, reply = self._ticket()
        try:
            await self._send({"event": event, "ticket": ticket, **params})
            async with asyncio.timeout(timeout):
                msg = await reply
        except TimeoutError:
            raise TimeoutError(f"{event}: no reply within {timeout:g}s") from None
        finally:
            self._pending.pop(ticket, None)
        return self._check(event, msg)

    async def _command(
        self,
        event: str,
        ack: str | tuple[str, ...],
        sub: _Subscription | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        """Send an event PPSSPP answers with an `ack` broadcast rather than a reply.

        The ticket still matters: PPSSPP sends errors back ticketed.
        """
        acks = {ack} if isinstance(ack, str) else set(ack)
        async with AsyncExitStack() as stack:
            if sub is None:
                sub = await stack.enter_async_context(self._subscribed(acks))
            ticket, reply = self._ticket()
            broadcast = asyncio.ensure_future(sub.get())
            stops = ack == "cpu.stepping"
            self._requested_stops += stops
            try:
                await self._send({"event": event, "ticket": ticket, **params})
                try:
                    async with asyncio.timeout(self.timeout):
                        await asyncio.wait({reply, broadcast}, return_when=asyncio.FIRST_COMPLETED)
                        if reply.done():
                            self._check(event, reply.result())
                        return await broadcast
                except TimeoutError:
                    expected = " or ".join(sorted(acks))
                    raise TimeoutError(f"{event}: no {expected} within {self.timeout:g}s") from None
            except BaseException:
                if stops and not broadcast.done():
                    self._requested_stops = max(0, self._requested_stops - 1)
                raise
            finally:
                broadcast.cancel()
                self._pending.pop(ticket, None)
        raise AssertionError("unreachable")

    @asynccontextmanager
    async def _subscribed(self, names: Collection[str] | None) -> AsyncIterator[_Subscription]:
        sub = _Subscription(names)
        self._subs.add(sub)
        try:
            if sub.logs:
                await self._open_log()
            if await self._sync_broadcasts():
                # a kind switched on arrives with what PPSSPP held back while it was off, all
                # of it before this reply
                await self._call("cpu.status", {})
                sub.clear()
            yield sub
        finally:
            self._subs.discard(sub)
            sub.close()
            if sub.logs:
                await self._close_log()
            if not self.closed:
                with suppress(Disconnected):
                    await self._sync_broadcasts()

    async def _open_log(self) -> None:
        async with self._log_lock:
            self._log_users += 1
            if self._log is None:
                self._log = await _LogFeed.open(_uri(self.host, self.port), 5.0, self._dispatch)

    async def _close_log(self) -> None:
        async with self._log_lock:
            self._log_users -= 1
            if self._log_users == 0 and self._log is not None:
                await self._log.close()
                self._log = None

    async def _probe_hit_events(self) -> bool:
        """Whether PPSSPP sends cpu.breakpoint.hit; if so, it stays off until a stream wants it."""
        try:
            await self._call("broadcast.config.set", {"disallowed": {"breakpoint": True}})
        except DebuggerError:  # 1.20.4 rejects a kind it does not have
            return False
        return True

    async def _sync_broadcasts(self) -> bool:
        """Ask PPSSPP to send only the broadcast kinds an open stream wants.

        Returns whether a kind was switched on.
        """
        switches = _SWITCHES if self._hit_events else _SWITCHES[:-1]
        wanted = set().union(*(s.switches for s in self._subs))
        disallowed = {"logger": True} | {s: s not in wanted for s in switches}
        before = self._disallowed
        if not self._configurable or disallowed == before:
            return False
        self._disallowed = disallowed
        try:
            await self._call("broadcast.config.set", {"disallowed": disallowed})
        except Unsupported:
            self._configurable = False
            return False
        return before is not None and any(before[s] and not disallowed[s] for s in switches)

    async def request(self, event: str, /, **params: Any) -> dict[str, Any]:
        """Send any debugger event and return its reply, for events without a method here."""
        return await self._call(event, params)

    @asynccontextmanager
    async def events(self, *names: str) -> AsyncIterator[Stream[t.Event]]:
        """Receive the named broadcasts (all if none are named) while the block runs."""
        async with self._subscribed(names or None) as sub:
            yield Stream(sub, lambda e: e)

    # memory

    async def read(self, address: int, size: int) -> bytes:
        reply = await self._call("memory.read", {"address": address, "size": size})
        return base64.b64decode(reply["base64"])

    async def read_u8(self, address: int) -> int:
        return int((await self._call("memory.read_u8", {"address": address}))["value"])

    async def read_u16(self, address: int) -> int:
        return int((await self._call("memory.read_u16", {"address": address}))["value"])

    async def read_u32(self, address: int) -> int:
        return int((await self._call("memory.read_u32", {"address": address}))["value"])

    async def read_f32(self, address: int) -> float:
        value: float = struct.unpack("<f", await self.read(address, 4))[0]
        return value

    async def read_string(self, address: int) -> str:
        """A NUL-terminated UTF-8 string."""
        return str((await self._call("memory.readString", {"address": address}))["value"])

    async def write(self, address: int, data: bytes) -> None:
        payload = {"address": address, "base64": base64.b64encode(data).decode("ascii")}
        await self._call("memory.write", payload)

    async def write_u8(self, address: int, value: int) -> None:
        await self._call("memory.write_u8", {"address": address, "value": value & 0xFF})

    async def write_u16(self, address: int, value: int) -> None:
        await self._call("memory.write_u16", {"address": address, "value": value & 0xFFFF})

    async def write_u32(self, address: int, value: int) -> None:
        await self._call("memory.write_u32", {"address": address, "value": value & 0xFFFFFFFF})

    async def write_f32(self, address: int, value: float) -> None:
        await self.write(address, struct.pack("<f", value))

    async def disasm(
        self, address: int, count: int = 1, *, symbols: bool = True
    ) -> list[t.Instruction]:
        """Disassemble `count` instructions; this sees through JIT markers, a RAM read does not."""
        params = {"address": address, "count": count, "displaySymbols": symbols}
        return [
            t.instruction(line) for line in (await self._call("memory.disasm", params))["lines"]
        ]

    # cpu

    async def status(self) -> t.CpuStatus:
        return t.cpu_status(await self._call("cpu.status", {}))

    async def pause(self) -> t.Stepping:
        """Stop the CPU and return where it stopped; a stopped CPU is fine."""
        async with self._subscribed({"cpu.stepping"}) as sub:
            status = await self.status()
            if status.stepping:
                return t.Stepping(status.pc, status.ticks, None, None, requested=True)
            return t.stepping(await self._command("cpu.stepping", "cpu.stepping", sub))

    @asynccontextmanager
    async def paused(self) -> AsyncIterator[t.Stepping]:
        """Stop the CPU for the block, and let it run after unless it was stopped before.

        While the game runs, PPSSPP stops and restarts the CPU for every memory request; in a
        paused block each request is an order of magnitude faster, and all see the same frame.
        """
        was_stepping = (await self.status()).stepping
        stop = await self.pause()
        try:
            yield stop
        finally:
            if not was_stepping:
                with suppress(Disconnected):
                    await self.resume()

    async def resume(self) -> None:
        """Let the CPU run; a running CPU is fine."""
        try:
            # a breakpoint that trips again at once gets PPSSPP to report the new stop instead
            await self._command("cpu.resume", ("cpu.resume", "cpu.stepping"))
        except DebuggerError as e:
            if e.message != "CPU not stepping":
                raise

    async def step_into(self) -> t.Stepping:
        return t.stepping(await self._command("cpu.stepInto", "cpu.stepping"))

    async def step_over(self) -> t.Stepping:
        return t.stepping(await self._command("cpu.stepOver", "cpu.stepping"))

    async def step_out(self) -> t.Stepping:
        return t.stepping(await self._command("cpu.stepOut", "cpu.stepping"))

    async def run_until(self, address: int) -> t.Stepping:
        return t.stepping(await self._command("cpu.runUntil", "cpu.stepping", address=address))

    async def registers(self, thread: int | None = None) -> dict[str, int]:
        """Every register by name: the GPRs with pc, hi and lo, then FPU and VFPU registers."""
        reply = await self._call("cpu.getAllRegs", _given(thread=thread))
        regs: dict[str, int] = {}
        for category in reply["categories"]:
            regs.update(zip(category["registerNames"], category["uintValues"], strict=True))
        return regs

    async def register(self, name: str, thread: int | None = None) -> int:
        reply = await self._call("cpu.getReg", _given(name=name, thread=thread))
        return int(reply["uintValue"])

    async def set_register(self, name: str, value: int | float, thread: int | None = None) -> None:
        """Set a register; the CPU must be stopped."""
        raw: int | str = value & 0xFFFFFFFF if isinstance(value, int) else repr(float(value))
        await self._call("cpu.setReg", _given(name=name, value=raw, thread=thread))

    async def evaluate(self, expression: str, thread: int | None = None) -> int:
        """Evaluate a debugger expression such as "a0 + 0x10"."""
        reply = await self._call("cpu.evaluate", _given(expression=expression, thread=thread))
        return int(reply["uintValue"])

    # breakpoints

    async def add_breakpoint(
        self,
        address: int,
        *,
        stop: bool = True,
        log: bool = False,
        condition: str | None = None,
        log_format: str | None = None,
    ) -> None:
        """Arm an execution breakpoint; `stop=False, log=True` traces without stopping."""
        params = _given(condition=condition, logFormat=log_format)
        await self._call(
            "cpu.breakpoint.add", {"address": address, "enabled": stop, "log": log, **params}
        )
        (self._stopping_breakpoints.add if stop else self._stopping_breakpoints.discard)(address)

    async def remove_breakpoint(self, address: int) -> None:
        await self._call("cpu.breakpoint.remove", {"address": address})
        self._stopping_breakpoints.discard(address)

    async def breakpoints(self) -> list[t.Breakpoint]:
        return [
            t.breakpoint(b) for b in (await self._call("cpu.breakpoint.list", {}))["breakpoints"]
        ]

    async def add_watchpoint(
        self,
        address: int,
        size: int = 4,
        *,
        read: bool = False,
        write: bool = True,
        change: bool = False,
        stop: bool = True,
        log: bool = False,
        condition: str | None = None,
        log_format: str | None = None,
    ) -> None:
        """Arm a memory breakpoint; `change=True` trips only on writes that change the value.

        With the JIT, an armed watchpoint sends every memory access down PPSSPP's slow path.
        """
        params = _given(condition=condition, logFormat=log_format)
        await self._call(
            "memory.breakpoint.add",
            {
                "address": address,
                "size": size,
                "read": read,
                "write": write,
                "change": change,
                "enabled": stop,
                "log": log,
                **params,
            },
        )

    async def remove_watchpoint(self, address: int, size: int = 4) -> None:
        await self._call("memory.breakpoint.remove", {"address": address, "size": size})

    async def watchpoints(self) -> list[t.Watchpoint]:
        reply = await self._call("memory.breakpoint.list", {})
        return [t.watchpoint(w) for w in reply["breakpoints"]]

    @asynccontextmanager
    async def breakpoint(
        self,
        address: int,
        *,
        stop: bool = True,
        condition: str | None = None,
        log_format: str | None = None,
    ) -> AsyncIterator[Stream[t.Hit]]:
        """An execution breakpoint for the block; yields its hits and removes it on exit.

        With `stop`, each hit leaves the CPU stopped until `resume()`. Without, the game keeps
        running; a `log_format` message comes from the log.
        """

        def match(event: t.Event) -> t.Hit | None:
            if isinstance(event, t.Stepping):
                if event.hit is not None:
                    return event.hit if _trips(event.hit, "exec", address) else None
                if self._hit_events or event.requested or event.pc != address:
                    return None
                # stock PPSSPP 1.20.4 gives no reason; the pc tells
                if event.reason in (None, "cpu.breakpoint"):
                    return t.Hit("exec", address, event.pc, True, start=address)
            elif isinstance(event, t.Hit):
                return event if _trips(event, "exec", address) else None
            elif isinstance(event, t.LogLine):
                hit = event.hit
                if hit is not None and hit.kind == "exec" and hit.pc == address:
                    return hit
            return None

        async with self._subscribed(self._hit_source(stop, log_format)) as sub:
            await self.add_breakpoint(
                address, stop=stop, log=not stop, condition=condition, log_format=log_format
            )
            hits = Stream(sub, match)
            try:
                yield hits
            finally:
                with suppress(Disconnected):
                    await self.remove_breakpoint(address)
                    await self._release_untaken(hits, stop)

    @asynccontextmanager
    async def watchpoint(
        self,
        address: int,
        size: int = 4,
        *,
        read: bool = False,
        write: bool = True,
        change: bool = False,
        stop: bool = True,
        condition: str | None = None,
        log_format: str | None = None,
    ) -> AsyncIterator[Stream[t.Hit]]:
        """A memory breakpoint for the block; yields its hits and removes it on exit.

        With `stop`, each hit leaves the CPU stopped until `resume()`. Without, the game keeps
        running; a `log_format` message comes from the log.

        Stock PPSSPP 1.20.4 does not say why the CPU stopped, so there a stop this client did
        not ask for and that is not at one of its breakpoints counts as a hit, a pause from
        PPSSPP's own UI included.
        """

        def match(event: t.Event) -> t.Hit | None:
            if isinstance(event, t.Stepping):
                if event.hit is not None:
                    return event.hit if _trips(event.hit, "memory", address) else None
                if self._hit_events or event.requested:
                    return None
                if event.reason == "memory.breakpoint" and event.related_address == address:
                    return t.Hit("memory", address, event.pc, True, start=address)
                if event.reason is None and event.pc not in self._stopping_breakpoints:
                    return t.Hit("memory", address, event.pc, True, start=address)
            elif isinstance(event, t.Hit):
                return event if _trips(event, "memory", address) else None
            elif isinstance(event, t.LogLine):
                hit = event.hit
                if hit is not None and hit.kind == "memory" and 0 <= hit.address - address < size:
                    return hit
            return None

        async with self._subscribed(self._hit_source(stop, log_format)) as sub:
            await self.add_watchpoint(
                address,
                size,
                read=read,
                write=write,
                change=change,
                stop=stop,
                log=not stop,
                condition=condition,
                log_format=log_format,
            )
            hits = Stream(sub, match)
            try:
                yield hits
            finally:
                with suppress(Disconnected):
                    await self.remove_watchpoint(address, size)
                    await self._release_untaken(hits, stop)

    @asynccontextmanager
    async def trace(
        self,
        addresses: Iterable[int],
        *,
        condition: str | None = None,
        log_format: str | None = None,
    ) -> AsyncIterator[Stream[t.Hit]]:
        """Execution breakpoints that only log, on every address for the block, as one stream.

        All are armed in one stop of the CPU, so they start on the same frame, and removed on
        exit. Each costs the emulator a lookup over every armed breakpoint per hit.
        """
        points = frozenset(addresses)

        def match(event: t.Event) -> t.Hit | None:
            if isinstance(event, t.Hit):
                return event if event.kind == "exec" and event.start in points else None
            if isinstance(event, t.LogLine):
                hit = event.hit
                if hit is not None and hit.kind == "exec" and hit.pc in points:
                    return hit
            return None

        async with self._subscribed(self._hit_source(False, log_format)) as sub:
            armed: list[int] = []
            try:
                async with self.paused():
                    for address in points:
                        await self.add_breakpoint(
                            address,
                            stop=False,
                            log=True,
                            condition=condition,
                            log_format=log_format,
                        )
                        armed.append(address)
                yield Stream(sub, match)
            finally:
                with suppress(Disconnected):
                    async with self.paused():
                        for address in armed:
                            await self.remove_breakpoint(address)

    def _hit_source(self, stop: bool, log_format: str | None) -> set[str]:
        """The broadcasts a breakpoint's hits arrive as."""
        if stop:
            return {"cpu.stepping"}
        return {"cpu.breakpoint.hit"} if self._hit_events and log_format is None else {"log"}

    async def _release_untaken(self, hits: Stream[t.Hit], stop: bool) -> None:
        """Resume a CPU left stopped by a hit that arrived after its caller stopped reading."""
        # a stop broadcast comes after the reply to the removal, but before this one
        if stop and (await self.status()).stepping and hits._untaken():
            await self.resume()

    # input

    async def press(self, button: t.Button, frames: int = 1) -> None:
        """Hold a button for `frames` emulated frames; returns once it is released."""
        params = {"button": button, "duration": frames}
        await self._call("input.buttons.press", params, self.timeout + frames / 15)

    async def hold(self, *buttons: t.Button) -> None:
        """Press buttons until `release()`."""
        await self._call("input.buttons.send", {"buttons": dict.fromkeys(buttons, True)})

    async def release(self, *buttons: t.Button) -> None:
        """Release buttons, or every button if none are given."""
        names = buttons or t.BUTTONS
        await self._call("input.buttons.send", {"buttons": dict.fromkeys(names, False)})

    async def analog(self, x: float, y: float, stick: Literal["left", "right"] = "left") -> None:
        """Set a stick's position, each axis from -1.0 to 1.0."""
        if not (-1 <= x <= 1 and -1 <= y <= 1):
            raise ValueError(f"analog position ({x}, {y}) is outside -1..1")
        await self._call("input.analog.send", {"x": x, "y": y, "stick": stick})

    # game

    async def game(self) -> t.GameInfo | None:
        """The running game, or None before one has booted."""
        return t.game_info((await self._call("game.status", {})).get("game"))

    async def wait_for_game(self, timeout: float = 60.0) -> t.GameInfo:
        """Wait until a game has booted; the debugger answers well before that."""
        async with asyncio.timeout(timeout):
            while (game := await self.game()) is None:
                await asyncio.sleep(0.2)
        return game

    async def reset(self, *, stop: bool = False) -> None:
        """Restart the game; with `stop`, the CPU waits at the first instruction."""
        await self._call("game.reset", {"break": stop})

    async def speed(self) -> t.Speed:
        """The emulation speed; stock PPSSPP 1.20 and older raise Unsupported."""
        return t.speed(await self._call("game.speed.get", {}))

    async def set_speed(self, percent: int | None = None, *, fast_forward: bool = False) -> t.Speed:
        """Run at `percent` of real time, at the game's own rate with None, or unlimited."""
        params = {"percent": percent, "fastForward": fast_forward}
        return t.speed(await self._call("game.speed.set", params))

    async def frame_stats(self) -> t.FrameStats:
        """Rates after the next frame; times out while the CPU is stopped."""
        return t.frame_stats(await self._call("gpu.stats.get", {}))

    async def screenshot(self) -> t.Screenshot:
        """The current output frame; some GPU backends answer only while stepping."""
        return t.screenshot(await self._call("gpu.buffer.screenshot", {"type": "uri"}))

    # the modkit's patched PPSSPP only: Unsupported on stock builds

    async def save_state(self, path: str | os.PathLike[str], timeout: float = 30.0) -> None:
        """Write a savestate to `path`, as the emulator sees it; returns once it is written."""
        await self._call("savestate.save", {"path": os.fspath(path)}, timeout)

    async def load_state(self, path: str | os.PathLike[str], timeout: float = 30.0) -> None:
        """Load the savestate at `path`, as the emulator sees it; returns once it is loaded."""
        await self._call("savestate.load", {"path": os.fspath(path)}, timeout)


def _trips(hit: t.Hit, kind: str, start: int) -> bool:
    return hit.kind == kind and hit.start == start


def _uri(host: str, port: int) -> str:
    return f"ws://{host}:{port}/debugger"


def _given(**params: Any) -> dict[str, Any]:
    return {k: v for k, v in params.items() if v is not None}
