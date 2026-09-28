# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Monster Hunter Portable file formats (MHFU and MHP3rd), each one a dataclass that reads with
`from_bytes` and writes back with `to_bytes`, byte for byte."""

from ._base import Format, FormatError
from .anim import AnimPack, Channel, Clip, Keyframe, Track
from .detect import detect
from .pac import Pac
from .pmo import Block, BoneSlot, Group, Material, Mesh, Pmo
from .skeleton import Bone, Skeleton
from .tmh import Tmh, TmhImage

__all__ = [
    "AnimPack",
    "Block",
    "Bone",
    "BoneSlot",
    "Channel",
    "Clip",
    "Format",
    "FormatError",
    "Group",
    "Keyframe",
    "Material",
    "Mesh",
    "Pac",
    "Pmo",
    "Skeleton",
    "Tmh",
    "TmhImage",
    "Track",
    "detect",
]
