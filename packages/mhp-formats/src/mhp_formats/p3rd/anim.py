# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""MHP3rd's animation pack: MHFU's nesting with compact headers and no tags. Clip
`u32 tracks, u32 size, u32 loop, f32 loop_start`; track `u16 channels, u16 size`; channel
`u16 bit, u16 keyframes, u32 size`. Sizes include the header.

A monster's moveset is its own `emNNN` file; stream 0 is the main clip set.
"""

import struct

from .._base import FormatError
from ..anim import AnimPack, Channel, Clip, Track, read_keyframes, sniff_pack, write_keyframes

_CLIP = struct.Struct("<3If")
_TRACK = struct.Struct("<2H")
_CHANNEL = struct.Struct("<2HI")


class Anim(AnimPack):
    @staticmethod
    def sniff(data: bytes) -> bool:
        return sniff_pack(data, lambda word: word < 0x10000)

    @classmethod
    def _read_clip(cls, data: memoryview, off: int) -> tuple[Clip, int]:
        count, size, loop, loop_start = _CLIP.unpack_from(data, off)
        end = off + size
        if end > len(data):
            raise FormatError(f"clip at {off:#x}: size {size:#x} runs past the data")
        tracks = []
        at = off + _CLIP.size
        for _ in range(count):
            channels, size = _TRACK.unpack_from(data, at)
            track_end = at + size
            if track_end > end:
                raise FormatError(f"track at {at:#x} runs past its clip")
            at += _TRACK.size
            track = Track()
            for _ in range(channels):
                bit, keys, size = _CHANNEL.unpack_from(data, at)
                if size != _CHANNEL.size + 8 * keys or at + size > track_end:
                    raise FormatError(f"channel at {at:#x}: size {size:#x}")
                keyframes = read_keyframes(data[at + _CHANNEL.size : at + size])
                track.channels.append(Channel(bit, keyframes))
                at += size
            if at != track_end:
                raise FormatError(f"track ending at {at:#x}, not {track_end:#x}")
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
            out += bytes(_TRACK.size)
            for channel in track.channels:
                keys = len(channel.keyframes)
                try:
                    out += _CHANNEL.pack(channel.bit, keys, _CHANNEL.size + 8 * keys)
                except struct.error as e:
                    raise ValueError(f"channel does not fit its u16 header: {e}") from None
                out += write_keyframes(channel.keyframes)
            size = len(out) - track_start
            if size > 0xFFFF:
                raise ValueError(f"a track of {size:#x} bytes does not fit its u16 size")
            _TRACK.pack_into(out, track_start, len(track.channels), size)
        size = len(out) - start
        _CLIP.pack_into(out, start, len(clip.tracks), size, clip.loop, clip.loop_start)
