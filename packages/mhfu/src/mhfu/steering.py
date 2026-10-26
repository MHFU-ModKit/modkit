# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A move's travel curve and code turn as the game holds them, so that what a port's moves module
writes and what `mhfu.live.moves` puts in the game agree. Travel is cumulative (x, z) every `STEP`
clip frames in 16-bit samples of `1 / SCALE` world units (struct TRAVEL_SAMPLE), and as
`steer.travel` in that module, 8 hex digits a sample (x, then z). The code turn is the donor's
entity-yaw curve: cumulative YAW units every `STEP` clip frames from the move's start, a signed 16
bits each (`steer.code_turn`, 4 hex digits a value).

    hex_of([(0, 0), (-2.5, 40)])   # "00000000fff60000..." for the module
    samples([(0, 0), (-2.5, 40)])  # [(0, 0), (-10, 160)] for the game
    turns_hex([0, 3000, -2])       # "00000bb8fffe" for the module
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from . import addresses

SCALE = int(addresses.STEER_TRAVEL_SCALE)
"""Sample units to a world unit."""
STEP = int(addresses.STEER_KEY_STEP)
"""Clip frames between two samples."""
MOST = int(addresses.STEER_SAMPLES)
"""Samples one move holds."""
REACH = 0x8000 / SCALE
"""A sample's x and z are within -REACH and REACH (exclusive) world units."""
MOST_TURNS = int(addresses.STEER_TURNS)
"""Code-turn values one move holds."""

Point = tuple[float, float]


def sample(value: float) -> int:
    """`value` world units as a sample's 16 bits; ValueError past `REACH`."""
    q = round(value * SCALE)
    if not -0x8000 <= q < 0x8000:
        raise ValueError(f"{value} is past the +-{REACH} a travel sample holds")
    return q


def samples(path: Iterable[Point]) -> list[tuple[int, int]]:
    """Each (x, z) of `path` as the game's sample; ValueError past `MOST` or `REACH`."""
    out = [(sample(x), sample(z)) for x, z in path]
    if len(out) > MOST:
        raise ValueError(f"{len(out)} travel samples, a move holds {MOST}")
    return out


def path_of(held: Iterable[tuple[int, int]]) -> tuple[Point, ...]:
    """World units from the game's samples."""
    return tuple((x / SCALE, z / SCALE) for x, z in held)


def turn_values(values: Iterable[int]) -> list[int]:
    """`values`, a code turn's YAW units, as the game holds them; ValueError past `MOST_TURNS` or a
    signed 16 bits."""
    out = list(values)
    if len(out) > MOST_TURNS:
        raise ValueError(f"{len(out)} code-turn values, a move holds {MOST_TURNS}")
    for v in out:
        if not -0x8000 <= v < 0x8000:
            raise ValueError(f"{v} is past the +-32768 YAW units a code-turn value holds")
    return out


def turns_hex(values: Iterable[int]) -> str:
    """A code turn as a moves module's `steer.code_turn`."""
    return "".join(f"{v & 0xFFFF:04x}" for v in turn_values(values))


def parse_turns(text: str) -> tuple[int, ...]:
    """A `steer.code_turn` string's values."""
    if len(text) % 4 or not all(c in "0123456789abcdefABCDEF" for c in text):
        raise ValueError("code_turn is 4 hex digits a value")
    words = [int(text[i : i + 4], 16) for i in range(0, len(text), 4)]
    return tuple(w - 0x10000 if w >= 0x8000 else w for w in words)


def hex_of(path: Iterable[Point]) -> str:
    """`path` as a moves module's `steer.travel`."""
    return "".join(f"{x & 0xFFFF:04x}{z & 0xFFFF:04x}" for x, z in samples(path))


def parse(text: str) -> tuple[Point, ...]:
    """A `steer.travel` string's path."""
    if len(text) % 8 or not all(c in "0123456789abcdefABCDEF" for c in text):
        raise ValueError("travel is 8 hex digits a sample")
    words = [int(text[i : i + 4], 16) for i in range(0, len(text), 4)]
    held = [w - 0x10000 if w >= 0x8000 else w for w in words]
    return path_of(zip(held[0::2], held[1::2], strict=True))


def at(path: Sequence[Point], clip_frame: float, start: float = 0) -> Point:
    """The curve at `clip_frame`: straight lines between samples `STEP` frames apart from
    `start`, held before the first and after the last; (0, 0) for an empty path."""
    if not path:
        return 0.0, 0.0
    u = (clip_frame - start) / STEP
    if u <= 0:
        return path[0]
    if u >= len(path) - 1:
        return path[-1]
    i = int(u)
    t = u - i
    (x0, z0), (x1, z1) = path[i], path[i + 1]
    return x0 + (x1 - x0) * t, z0 + (z1 - z0) * t
