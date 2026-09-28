# ppsspp-debug

A typed client for [PPSSPP](https://www.ppsspp.org/)'s WebSocket debugger: read and write memory,
set breakpoints and watchpoints, pause and step the CPU, press buttons. `Client` blocks,
`AsyncClient` is for asyncio; both have the same methods. Nothing in it is specific to one
game.

```python
from ppsspp_debug import Client

with Client.connect() as ppsspp:  # finds the PPSSPP running on this machine
    ppsspp.read_u32(address)
    with ppsspp.paused():  # one stop for many reads, all from the same frame
        data = ppsspp.read(address, 0x100)
        regs = ppsspp.registers()
    with ppsspp.watchpoint(address, 2) as hits:  # removed when the block ends
        hit = hits.next(timeout=30)  # the CPU is now stopped at the writer
        print(hex(hit.pc), ppsspp.registers()["a0"])
        ppsspp.resume()
```

```python
from ppsspp_debug import AsyncClient

async with AsyncClient.connect(port=12345) as ppsspp:
    async with ppsspp.breakpoint(address, stop=False, log_format="a0={a0}") as calls:
        async for call in calls:  # the game keeps running
            print(call.message)
```

PPSSPP opens the debugger at startup only with `RemoteDebuggerOnStartup = True` in its
`ppsspp.ini`, or from Settings > Tools > Developer tools.

## What it handles for you

| PPSSPP (1.20.4) | The client |
|---|---|
| Sends its log only to the newest debugger connection, and stops it for all when any connection closes | Log streams get a connection of their own, opened when the stream starts |
| Does not say why the CPU stopped | Matches a breakpoint stop by its pc, and counts as a watchpoint hit any stop this client did not ask for; uses the reason where PPSSPP gives one |
| Reports a hit of a breakpoint that does not stop only as a log line | Parses it; takes `cpu.breakpoint.hit` events instead from a PPSSPP that sends them |
| Answers `cpu.stepping` and `cpu.resume` with a broadcast, not a reply; pausing a paused CPU answers nothing | `pause()` and `resume()` wait for the broadcast, and are fine to call twice; `resume()` also takes a new stop as its answer |
| Stops and restarts the CPU for every memory request while the game runs (about 10 ms each) | `paused()` makes each request about 0.3 ms; one `read()` of a whole struct costs one stop |
| Keeps breakpoints armed after the script that set them has died | `breakpoint()` and `watchpoint()` remove theirs when the block ends, however it ends, and let the game run if a hit arrived that nobody took |
| Reports errors as events, and an unknown event as an error | Raises `DebuggerError`, or `Unsupported` for an event this PPSSPP does not have |
| Moves to a random port when its own is busy, and saves that port to `ppsspp.ini` | Reads the port from the PPSSPP process: `find_debuggers()`, `LocalEmulator.port()` |
| Stalls the WebSocket handshake while it announces itself to report.ppsspp.org at startup | `connect()` keeps trying until its timeout (30 s by default) |
| Sends input and game events unasked, and buffers the log while it is switched off | Switches on only the kinds an open stream wants, and drops what was held back |

There are no callbacks: events arrive as a stream read in your own code, so nothing the reader
does can wait on a reply only the reader can deliver.

## Starting PPSSPP

```python
from ppsspp_debug import Client, DockerEmulator, LocalEmulator

with LocalEmulator("/path/to/PPSSPPSDL", "/path/to/game.iso") as emu:
    with Client.connect(port=emu.port()) as ppsspp:
        ppsspp.wait_for_game()
```

`DockerEmulator()` drives PPSSPP in the modkit's headless container
([`ppsspp/`](../../ppsspp)) through its `ppsspp-ctl`, relaunches it when it dies right after
reporting ready, and captures its display with `screenshot()`. A `state=` launch skips the cold
boot, so PRX plugins do not load.

## Patched PPSSPP

The modkit's PPSSPP build ([`ppsspp/`](../../ppsspp)) fixes the first two rows of the table.
`save_state()` and `load_state()` need it; `speed()` and `set_speed()` need it or a PPSSPP newer
than 1.20.4. Stock 1.20.4 raises `Unsupported` for them.

`screenshot()` depends on the GPU backend: PPSSPP 1.20.4 with Vulkan answers "Could not
download output" whether the game runs or is stopped.

## Tests

`ppsspp_debug.testing.FakePPSSPP` is a debugger server with PPSSPP's quirks, for testing code
that uses this client without an emulator.

`FakePPSSPP(patched=True)` behaves like the modkit's build, `FakePPSSPP(hit_events=True)` like a
PPSSPP that sends `cpu.breakpoint.hit`.

The live tests run against a real PPSSPP when `PPSSPP_BINARY` and `PPSSPP_GAME` are set, and
print what they measure with `-s`:

```bash
PPSSPP_BINARY=/path/to/PPSSPPSDL PPSSPP_GAME=/path/to/game.iso uv run pytest packages/ppsspp-debug -s
```

`PPSSPP_CONTAINER=ppsspp` in place of `PPSSPP_BINARY` runs them in the modkit's container, with
`PPSSPP_GAME` a path inside it.
