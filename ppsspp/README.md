# PPSSPP for automation

PPSSPP v1.20.4 with six patches for scripted debugging, and a container that runs it headless,
watchable in a browser. [`ppsspp-debug`](../packages/ppsspp-debug) works with stock PPSSPP as well;
it uses what the patches add where the emulator has it.

| Patch | Adds |
|---|---|
| `0001-debugger-savestate-save-load` | `savestate.save` and `savestate.load` debugger commands that answer once the file is written or the state loaded |
| `0002-arm-jit-vfpu-memcheck-offset` | On ARM hosts, Apple Silicon included, watchpoints catch VFPU loads and stores with a negative offset |
| `0003-debugger-log-to-every-connection` | Every debugger connection gets the log, and closing one no longer stops it for the rest |
| `0004-debugger-stepping-reason` | `cpu.stepping` says why the CPU stopped |
| `0005-debugger-game-speed` | `game.speed.get` and `game.speed.set`, to run faster or slower than real time |
| `0006-debugger-screenshot-save` | `screenshot.save`, the frame on screen to a PNG file while the game runs, in a hidden lane too |

0003 to 0005 are backports from PPSSPP's master branch and go when the pin moves to a release
that has them.

## Build

```bash
ppsspp/build.sh
```

It fetches PPSSPP at the pinned commit into `~/.cache/modkit/ppsspp` (2.4 GB with its submodules;
`PPSSPP_BUILD` moves it), applies the patches, builds, and prints the path of the binary. It needs
git, CMake and Ninja, plus Xcode's command line tools on macOS, or on Linux the packages the
Dockerfile installs.

## Example

```bash
export MHFU_PPSSPP=$(ppsspp/build.sh | tail -n 1)   # the binary build.sh prints last
uv run mhfu start --iso mhfu.iso                    # the game, with its debugger open
```

The debugger opens at startup once Settings > Tools > Developer tools > Allow remote debugger
has been ticked (it sets `RemoteDebuggerOnStartup = True` in `ppsspp.ini`).

## Lanes: several at once on one machine

```bash
MHFU_ISO=~/isos/mhfu.iso uv run mhfu start --lane 1    # prints 45101
uv run mhfu stop --lane 1
```

A lane is a PPSSPP of its own: its own `HOME` under `~/.cache/modkit/lanes/<n>` (so its own
memory stick, cloned from `~/.config/ppsspp/PSP` the first time), its own clone of the game image,
and debugger port 45100 + n. On macOS it starts hidden and never takes focus. `mhfu stop` without
`--lane` leaves lanes alone. In Python, `ppsspp_debug.Lane` or `MHFU_LANE` for `mhfu.live`;
with `MHFU_LANE` set, everything that writes the memory stick (`mhfu-port inject`, the studio)
writes the lane's.

## Headless in Docker

```bash
cd ppsspp/docker
./setup.sh ~/isos ~/ppsspp-home       # once: the game images and PPSSPP's user directory
docker compose build --build-arg JOBS=4   # compiles PPSSPP; JOBS caps it where memory is short
docker compose up -d
docker compose exec ppsspp ppsspp-ctl start --iso /iso/game.iso
```

Watch and play at http://localhost:6080/vnc.html?autoconnect=1&resize=scale;
`WEB_BIND=0.0.0.0 ./setup.sh ...` opens it to the network, without a password. The debugger
listens on 127.0.0.1:12345:

```python
from ppsspp_debug import Client, DockerEmulator

with DockerEmulator(game="/iso/game.iso") as emu, Client.connect(port=emu.port()) as ppsspp:
    ppsspp.wait_for_game()
    png = emu.screenshot()
```

`ppsspp-ctl` starts, stops and restarts PPSSPP inside the container, shows its log and captures
its display (`docker compose exec -T ppsspp ppsspp-ctl screenshot > shot.png`). Paths given to it,
or to `save_state()`, are the container's: `/iso` holds the images and `/config/ppsspp/PSP` is the
memory stick. A `--state` launch skips the cold boot, so PRX plugins do not load.

On Linux, `setup.sh` hands the container the GPU's render node for Vulkan; without one, as under
Docker Desktop, PPSSPP renders in software.

What the container takes care of:

- PPSSPP silently moves to a random port when its own is busy. `ppsspp-ctl start` waits until the
  port is free and returns only once the new process owns it.
- PPSSPP announces itself to report.ppsspp.org before its debugger accepts connections, which
  stalls the first handshake. The container resolves that name to loopback.
- PPSSPP rewrites `ppsspp.ini` on exit. `ppsspp-ctl` sets the debugger and frame timing settings
  again before each start.

A container that has been up for days can start failing in ways that look like your code;
restart it first.

## Status

The patches apply to v1.20.4 and build on macOS and Linux; the container runs PPSSPP with Vulkan
on a Linux GPU and in software elsewhere.

## Licence

The patches are GPL-2.0-or-later, like PPSSPP; the scripts and the container are MIT.
