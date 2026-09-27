from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import threading
from collections.abc import Callable, Coroutine, Iterator
from contextlib import AbstractAsyncContextManager, AbstractContextManager, contextmanager
from types import TracebackType
from typing import Any, Concatenate, Generic, ParamSpec, TypeVar

from ._client import AsyncClient, Stream
from ._errors import Disconnected

P = ParamSpec("P")
R = TypeVar("R")
T = TypeVar("T")


class _Loop:
    """An event loop on a daemon thread, running the async client for blocking callers."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="ppsspp-debug", daemon=True
        )
        self._thread.start()
        self._stopping = False

    def run(self, coro: Coroutine[Any, Any, R]) -> R:
        if threading.current_thread() is self._thread:
            coro.close()
            raise RuntimeError("a blocking call on the client's own loop would deadlock")
        if self._stopping:
            coro.close()
            raise Disconnected("the client is closed")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result()
        except concurrent.futures.CancelledError:
            if self._stopping:
                raise Disconnected("the client is closed") from None
            raise
        except BaseException:
            future.cancel()
            raise

    @property
    def stopped(self) -> bool:
        return self._stopping

    def stop(self) -> None:
        """Cancel what still runs, so a call waiting in another thread fails instead of hanging."""
        self._stopping = True

        async def cancel_all() -> None:
            tasks = asyncio.all_tasks() - {asyncio.current_task()}
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(cancel_all(), self._loop).result()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._loop.close()


class SyncStream(Generic[T]):
    """The blocking view of a Stream."""

    def __init__(self, loop: _Loop, stream: Stream[T]) -> None:
        self._loop = loop
        self._stream = stream

    @property
    def dropped(self) -> int:
        """How many events were discarded because the stream was not read fast enough."""
        return self._stream.dropped

    def next(self, timeout: float | None = None) -> T:
        """The next item; raises TimeoutError after `timeout` seconds."""
        return self._loop.run(self._stream.next(timeout))

    def __iter__(self) -> Iterator[T]:
        return self

    def __next__(self) -> T:
        try:
            return self.next()
        except Disconnected:
            raise StopIteration from None


def _blocking(
    method: Callable[Concatenate[AsyncClient, P], Coroutine[Any, Any, R]],
) -> Callable[Concatenate[Client, P], R]:
    @functools.wraps(method)
    def call(self: Client, /, *args: P.args, **kwargs: P.kwargs) -> R:
        return self._loop.run(method(self._async, *args, **kwargs))

    return call


def _blocking_cm(
    method: Callable[Concatenate[AsyncClient, P], AbstractAsyncContextManager[R]],
) -> Callable[Concatenate[Client, P], AbstractContextManager[R]]:
    @functools.wraps(method)
    def call(self: Client, /, *args: P.args, **kwargs: P.kwargs) -> AbstractContextManager[R]:
        return self._enter(method(self._async, *args, **kwargs))

    return call


def _blocking_stream(
    method: Callable[Concatenate[AsyncClient, P], AbstractAsyncContextManager[Stream[T]]],
) -> Callable[Concatenate[Client, P], AbstractContextManager[SyncStream[T]]]:
    @functools.wraps(method)
    def call(
        self: Client, /, *args: P.args, **kwargs: P.kwargs
    ) -> AbstractContextManager[SyncStream[T]]:
        return self._enter_stream(method(self._async, *args, **kwargs))

    return call


async def _open(host: str, port: int | None, timeout: float, request_timeout: float) -> AsyncClient:
    return await AsyncClient.connect(host, port, timeout=timeout, request_timeout=request_timeout)


class Client:
    """The blocking twin of AsyncClient: the same methods, run on a private event loop thread.

    One client may be shared between threads.
    """

    def __init__(self, loop: _Loop, client: AsyncClient) -> None:
        self._loop = loop
        self._async = client

    @classmethod
    def connect(
        cls,
        host: str = "127.0.0.1",
        port: int | None = None,
        *,
        timeout: float = 30.0,
        request_timeout: float = 5.0,
    ) -> Client:
        """Connect, retrying until `timeout` while PPSSPP starts or its handshake stalls.

        Without a port, it is read from the PPSSPP process running on this machine.
        """
        loop = _Loop()
        try:
            client = loop.run(_open(host, port, timeout, request_timeout))
        except BaseException:
            loop.stop()
            raise
        return cls(loop, client)

    def close(self) -> None:
        """Close the connection; breakpoints stay armed in PPSSPP."""
        if not self._loop.stopped:
            self._loop.run(self._async.close())
            self._loop.stop()

    def __enter__(self) -> Client:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def host(self) -> str:
        return self._async.host

    @property
    def port(self) -> int:
        return self._async.port

    @property
    def server(self) -> str:
        """The server's name and version, such as "PPSSPP v1.20.4"."""
        return self._async.server

    @property
    def closed(self) -> bool:
        return self._async.closed

    @property
    def timeout(self) -> float:
        """Seconds to wait for each reply."""
        return self._async.timeout

    @timeout.setter
    def timeout(self, value: float) -> None:
        self._async.timeout = value

    @contextmanager
    def _enter(self, acm: AbstractAsyncContextManager[R]) -> Iterator[R]:
        value = self._loop.run(acm.__aenter__())
        try:
            yield value
        except BaseException as e:
            if not self._loop.run(acm.__aexit__(type(e), e, e.__traceback__)):
                raise
        else:
            self._loop.run(acm.__aexit__(None, None, None))

    @contextmanager
    def _enter_stream(self, acm: AbstractAsyncContextManager[Stream[T]]) -> Iterator[SyncStream[T]]:
        with self._enter(acm) as stream:
            yield SyncStream(self._loop, stream)

    request = _blocking(AsyncClient.request)
    events = _blocking_stream(AsyncClient.events)

    read = _blocking(AsyncClient.read)
    read_u8 = _blocking(AsyncClient.read_u8)
    read_u16 = _blocking(AsyncClient.read_u16)
    read_u32 = _blocking(AsyncClient.read_u32)
    read_f32 = _blocking(AsyncClient.read_f32)
    read_string = _blocking(AsyncClient.read_string)
    write = _blocking(AsyncClient.write)
    write_u8 = _blocking(AsyncClient.write_u8)
    write_u16 = _blocking(AsyncClient.write_u16)
    write_u32 = _blocking(AsyncClient.write_u32)
    write_f32 = _blocking(AsyncClient.write_f32)
    disasm = _blocking(AsyncClient.disasm)

    status = _blocking(AsyncClient.status)
    pause = _blocking(AsyncClient.pause)
    paused = _blocking_cm(AsyncClient.paused)
    resume = _blocking(AsyncClient.resume)
    step_into = _blocking(AsyncClient.step_into)
    step_over = _blocking(AsyncClient.step_over)
    step_out = _blocking(AsyncClient.step_out)
    run_until = _blocking(AsyncClient.run_until)
    registers = _blocking(AsyncClient.registers)
    register = _blocking(AsyncClient.register)
    set_register = _blocking(AsyncClient.set_register)
    evaluate = _blocking(AsyncClient.evaluate)

    add_breakpoint = _blocking(AsyncClient.add_breakpoint)
    remove_breakpoint = _blocking(AsyncClient.remove_breakpoint)
    breakpoints = _blocking(AsyncClient.breakpoints)
    breakpoint = _blocking_stream(AsyncClient.breakpoint)
    add_watchpoint = _blocking(AsyncClient.add_watchpoint)
    remove_watchpoint = _blocking(AsyncClient.remove_watchpoint)
    watchpoints = _blocking(AsyncClient.watchpoints)
    watchpoint = _blocking_stream(AsyncClient.watchpoint)

    press = _blocking(AsyncClient.press)
    hold = _blocking(AsyncClient.hold)
    release = _blocking(AsyncClient.release)
    analog = _blocking(AsyncClient.analog)

    game = _blocking(AsyncClient.game)
    wait_for_game = _blocking(AsyncClient.wait_for_game)
    reset = _blocking(AsyncClient.reset)
    frame_stats = _blocking(AsyncClient.frame_stats)
    screenshot = _blocking(AsyncClient.screenshot)

    save_state = _blocking(AsyncClient.save_state)
    load_state = _blocking(AsyncClient.load_state)
    speed = _blocking(AsyncClient.speed)
