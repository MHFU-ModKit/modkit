"""`hud`: the live HUD next to PPSSPP, or with `--shot DIR` one PNG per tab and no window."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path

SDL_DEFAULTS = {
    # SDL's HID and MFi joystick backends race PPSSPP for the gamepad on macOS and freeze it
    "SDL_JOYSTICK_HIDAPI": "0",
    "SDL_JOYSTICK_DISABLE_MFI": "1",
    "SDL_GAMECONTROLLER_IGNORE_DEVICES": "0x0000/0x0000",
    "SDL_AUDIODRIVER": "dummy",
    "PYGAME_HIDE_SUPPORT_PROMPT": "1",
}
"""Set before pygame is imported; a value already in the environment wins."""
SHOT_POLLS = 2
"""Polls `--shot` waits for: the second shows the reader is ticking."""
SHOT_TIMEOUT = 30.0


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="hud", description="Live MHFU game-state HUD for PPSSPP.")
    ap.add_argument("--host", help="PPSSPP debugger host (default: this machine)")
    ap.add_argument(
        "--port", type=int, help="PPSSPP debugger port (default: read from the PPSSPP process)"
    )
    ap.add_argument(
        "--poll-hz",
        type=float,
        default=3.0,
        help="memory polls per second (default 3; faster hitches the emulator)",
    )
    ap.add_argument("--fullscreen", action="store_true", help="start fullscreen")
    ap.add_argument(
        "--read-only",
        action="store_true",
        help="never write game memory: QUEST_PREP's staged edits are off",
    )
    ap.add_argument(
        "--assets",
        type=Path,
        help="artwork folder (default: $MHFU_HUD_ASSETS, else "
        "${XDG_DATA_HOME:-~/.local/share}/mhfu-hud/assets)",
    )
    ap.add_argument(
        "--shot",
        type=Path,
        metavar="DIR",
        help="attach, render every tab once to DIR/<tab>.png without a window, and exit",
    )
    return ap.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse(argv)
    for k, v in SDL_DEFAULTS.items():
        os.environ.setdefault(k, v)
    if args.shot:
        os.environ["SDL_VIDEODRIVER"] = "dummy"

    from .app import HUDApp
    from .calibration import Calibration
    from .reader import QUEST_MAP, MemoryReader
    from .writer import GameWriter

    calib = Calibration()
    reader = MemoryReader(args.host, args.port, args.poll_hz, calib.section_map(QUEST_MAP))
    writer = None if args.read_only else GameWriter(reader)
    reader.start()
    if writer:
        writer.start()
    try:
        app = HUDApp(reader, calib, writer=writer, assets=args.assets, fullscreen=args.fullscreen)
        if not args.shot:
            app.run()
            return 0
        deadline = time.monotonic() + SHOT_TIMEOUT
        while reader.snapshot.poll_count < SHOT_POLLS:
            if time.monotonic() > deadline:
                print(f"no poll within {SHOT_TIMEOUT:g} s: {reader.snapshot.status_text}")
                return 1
            time.sleep(0.1)
        for path in app.shoot(args.shot):
            print(path)
        return 0
    finally:
        if writer:
            writer.stop()
        reader.stop()


if __name__ == "__main__":
    sys.exit(main())
