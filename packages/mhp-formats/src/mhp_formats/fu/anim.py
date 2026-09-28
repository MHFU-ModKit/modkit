# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""MHFU's animation pack. Every level is a section `u32 tag, u32 count, u32 size` (size includes
the header): clip `0x80000002, tracks, size, u32 loop, f32 loop_start`; track
`0x80000000 | mask, channels, size`; channel `0x80120000 | bit, keyframes, size`.

The in-game packs of the big monsters have 6 streams, the rest 2, 4 or 10; the encoding is the same.
"""

import struct

from .._base import FormatError
from ..anim import AnimPack, Channel, Clip, Track, read_keyframes, sniff_pack, write_keyframes

CLIP_TAG = 0x80000002
TRACK_TAG = 0x80000000
CHANNEL_TAG = 0x80120000

_CLIP = struct.Struct("<4If")
_SECTION = struct.Struct("<3I")


class Anim(AnimPack):
    @staticmethod
    def sniff(data: bytes) -> bool:
        return sniff_pack(data, lambda word: word == CLIP_TAG)

    @classmethod
    def _read_clip(cls, data: memoryview, off: int) -> tuple[Clip, int]:
        tag, count, size, loop, loop_start = _CLIP.unpack_from(data, off)
        end = off + size
        if tag != CLIP_TAG or end > len(data):
            raise FormatError(f"clip at {off:#x}: tag {tag:#x}, size {size:#x}")
        tracks = []
        at = off + _CLIP.size
        for _ in range(count):
            tag, channels, size = _SECTION.unpack_from(data, at)
            track_end = at + size
            if track_end > end:
                raise FormatError(f"track at {at:#x} runs past its clip")
            at += _SECTION.size
            track = Track()
            for _ in range(channels):
                bit, keys, size = _SECTION.unpack_from(data, at)
                if bit & ~0xFFF != CHANNEL_TAG or size != _SECTION.size + 8 * keys:
                    raise FormatError(f"channel at {at:#x}: tag {bit:#x}, size {size:#x}")
                if at + size > track_end:
                    raise FormatError(f"channel at {at:#x} runs past its track")
                keyframes = read_keyframes(data[at + _SECTION.size : at + size])
                track.channels.append(Channel(bit & 0xFFF, keyframes))
                at += size
            if at != track_end or tag != TRACK_TAG | track.mask:
                raise FormatError(f"track ending at {at:#x}: tag {tag:#x}, end {track_end:#x}")
            tracks.append(track)
        if at != end:
            raise FormatError(f"clip at {off:#x} ends at {at:#x}, not {end:#x}")
        return Clip(tracks, loop, loop_start), end

    @staticmethod
    def _write_clip(clip: Clip, out: bytearray) -> None:
        start = len(out)
        out += bytes(_CLIP.size)
        for track in clip.tracks:
            track_start = len(out)
            out += bytes(_SECTION.size)
            for channel in track.channels:
                if channel.bit & ~0xFFF:
                    raise ValueError(f"channel bit {channel.bit:#x} does not fit 12 bits")
                keys = len(channel.keyframes)
                out += _SECTION.pack(CHANNEL_TAG | channel.bit, keys, _SECTION.size + 8 * keys)
                out += write_keyframes(channel.keyframes)
            size = len(out) - track_start
            _SECTION.pack_into(out, track_start, TRACK_TAG | track.mask, len(track.channels), size)
        size = len(out) - start
        _CLIP.pack_into(out, start, CLIP_TAG, len(clip.tracks), size, clip.loop, clip.loop_start)
