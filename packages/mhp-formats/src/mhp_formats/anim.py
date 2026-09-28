# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Animation packs of both games: streams of slot tables, each slot a clip or empty, each clip one
keyframe track per bone. Only the clip encoding differs between the games; it lives in
`mhp_formats.fu.anim` and `mhp_formats.p3rd.anim`.

MHFU splits one rig across its streams (a big monster's body, head and tail play the same slot
together); MHP3rd's streams are separate clip sets over the whole rig.
"""

import math
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from itertools import chain
from typing import Literal, NamedTuple, Self

from construct import Array, Int32ul

from ._base import FormatError

EMPTY = 0xFFFFFFFF
"""A slot without a clip."""

Kind = Literal["rot", "loc", "scl"]

CHANNEL_BITS: dict[int, tuple[Kind, int]] = {
    0x008: ("rot", 0),
    0x010: ("rot", 1),
    0x020: ("rot", 2),
    0x040: ("loc", 0),
    0x080: ("loc", 1),
    0x100: ("loc", 2),
    0x200: ("scl", 0),
    0x400: ("scl", 1),
    0x800: ("scl", 2),
}
"""Channel bit -> (kind, axis). Bits 0x1/0x2/0x4 also occur, with values near 16; their meaning
is not known. MHFU's engine crashes on the scale bits."""

_STEP: dict[Kind, float] = {"rot": math.pi / 8192, "loc": 1 / 16, "scl": 1 / 256}
"""What one raw unit is worth: 4096 = 90 degrees, 16 = 1.0, 256 = 1.0."""

_MAX_HEADER = 0x800


class Keyframe(NamedTuple):
    """Raw s16 words; `value` goes through `dequantize`."""

    value: int
    frame: int
    ease_in: int = 0
    ease_out: int = 0


@dataclass
class Channel:
    """One animated component of a bone."""

    bit: int
    keyframes: list[Keyframe] = field(default_factory=list)


@dataclass
class Track:
    """The channels of one bone."""

    channels: list[Channel] = field(default_factory=list)

    @property
    def mask(self) -> int:
        mask = 0
        for channel in self.channels:
            mask |= channel.bit
        return mask


@dataclass
class Clip:
    tracks: list[Track] = field(default_factory=list)
    loop: int = 0
    loop_start: float = 0.0


Stream = list[Clip | None]


def channel_kind(bit: int) -> tuple[Kind, int] | None:
    """`(kind, axis)` of a channel bit, None for the bits nobody has named."""
    return CHANNEL_BITS.get(bit)


def dequantize(kind: Kind, raw: int) -> float:
    """Rotation comes out in radians."""
    return raw * _STEP[kind]


def quantize(kind: Kind, value: float) -> int:
    """Rounds to nearest and clamps to s16."""
    return max(-0x8000, min(0x7FFF, round(value / _STEP[kind])))


# Keyframes and the section headers around them are packed with `struct`, not construct: a pack
# holds up to ~200k keyframes, and construct parses one 8-byte keyframe in ~4 us against ~0.1 us.
_KEY = struct.Struct("<4h")
_to_keyframe = partial(tuple.__new__, Keyframe)


def read_keyframes(view: memoryview) -> list[Keyframe]:
    return list(map(_to_keyframe, _KEY.iter_unpack(view)))


def write_keyframes(keyframes: list[Keyframe]) -> bytes:
    try:
        return struct.pack(f"<{4 * len(keyframes)}h", *chain.from_iterable(keyframes))
    except struct.error as e:
        raise ValueError(f"a keyframe does not fit s16: {e}") from None


@dataclass
class AnimPack:
    """Header: N x (u32 slot count, u32 table offset), u32 0, u32 end of the tables; then the
    tables, then the clips back to back in the order the slots first use them. Slots holding the
    same `Clip` object share one copy in the file."""

    streams: list[Stream] = field(default_factory=list)
    tail: bytes = b""
    """Bytes after the last clip (a standalone pack pads to 0x800)."""

    @classmethod
    def _read_clip(cls, data: memoryview, off: int) -> tuple[Clip, int]:
        """The clip at `off` and where it ends."""
        raise NotImplementedError

    @staticmethod
    def _write_clip(clip: Clip, out: bytearray) -> None:
        raise NotImplementedError

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        layout = _layout(data)
        if layout is None:
            raise FormatError("not an animation pack: the header does not describe its tables")
        tables, cursor = layout
        view = memoryview(data)
        clips: dict[int, Clip] = {}
        streams: list[Stream] = []
        for table in tables:
            stream: Stream = []
            for off in table:
                clip = clips.get(off)
                if clip is None and off != EMPTY:
                    if off != cursor:
                        raise FormatError(f"clip at {off:#x}, expected the next one at {cursor:#x}")
                    try:
                        clip, cursor = cls._read_clip(view, off)
                    except struct.error as e:
                        raise FormatError(f"clip at {off:#x} runs past the data") from e
                    clips[off] = clip
                stream.append(clip)
            streams.append(stream)
        return cls(streams, data[cursor:])

    def to_bytes(self) -> bytes:
        if not self.streams:
            raise ValueError("an animation pack has at least one stream")
        header = []
        run = 8 * (len(self.streams) + 1)
        for stream in self.streams:
            header += [len(stream), run]
            run += 4 * len(stream)
        header += [0, run]
        out = bytearray(run)
        placed: dict[int, int] = {}
        for stream in self.streams:
            for clip in stream:
                if clip is not None and id(clip) not in placed:
                    placed[id(clip)] = len(out)
                    self._write_clip(clip, out)
        for stream in self.streams:
            header += [EMPTY if clip is None else placed[id(clip)] for clip in stream]
        out[:run] = Array(len(header), Int32ul).build(header)
        return bytes(out) + self.tail


def sniff_pack(data: bytes, is_clip: Callable[[int], bool]) -> bool:
    """A pack header whose first clip starts with a word `is_clip` accepts."""
    layout = _layout(data)
    if layout is None:
        return False
    tables, start = layout
    if all(off == EMPTY for table in tables for off in table):
        return True
    return len(data) >= start + 4 and is_clip(int.from_bytes(data[start : start + 4], "little"))


def _layout(data: bytes) -> tuple[list[list[int]], int] | None:
    """The slot tables and where the clips start, or None if the header does not chain."""
    if len(data) < 16:
        return None
    hsize = int.from_bytes(data[4:8], "little")
    if not 16 <= hsize <= min(len(data), _MAX_HEADER) or hsize % 8:
        return None
    words = Array(hsize // 4, Int32ul).parse(data)
    run = hsize
    for i in range(0, hsize // 4 - 2, 2):
        if words[i + 1] != run:
            return None
        run += 4 * words[i]
    if words[-2] != 0 or words[-1] != run or run > len(data):
        return None
    flat = Array((run - hsize) // 4, Int32ul).parse(data[hsize:run])
    tables = []
    for i in range(0, hsize // 4 - 2, 2):
        start = (words[i + 1] - hsize) // 4
        tables.append(list(flat[start : start + words[i]]))
    return tables, run
