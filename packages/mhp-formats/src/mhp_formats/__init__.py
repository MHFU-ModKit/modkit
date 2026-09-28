# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Monster Hunter Portable file formats (MHFU and MHP3rd), each one a dataclass that reads with
`from_bytes` and writes back with `to_bytes`, byte for byte."""

from ._base import Format, FormatError
from .anim import AnimPack, Channel, Clip, Keyframe, Track, dequantize, quantize
from .pac import Pac
from .skeleton import Bone, Skeleton

__all__ = [
    "AnimPack",
    "Bone",
    "Channel",
    "Clip",
    "Format",
    "FormatError",
    "Keyframe",
    "Pac",
    "Skeleton",
    "Track",
    "dequantize",
    "quantize",
]
