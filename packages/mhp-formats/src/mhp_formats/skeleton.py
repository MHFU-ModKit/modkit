# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Skeleton: a model's bone tree and bind pose, in both games.

A section `u32 magic, u32 1 + bones, u32 size`, then a parameter section `u32 0, u32 count,
u32 size, u32 values[]`, then one section per bone. MHFU's bone sections are 0x10C bytes and end
in zeros, MHP3rd's are 0x5C and end in the bone's name; the magic is independent of that.
"""

from dataclasses import dataclass, field
from typing import Self

from construct import Array, ConstructError, Float32l, Int32sl, Int32ul
from construct_typed import DataclassMixin, DataclassStruct, csfield, csfield_const

from ._base import FormatError

FU_MAGIC = 0xC0000000
P3RD_MAGIC = 0x80000000
BONE_TAG = 0x40000000

Vec3 = tuple[float, float, float]


@dataclass
class Bone:
    """Links are bone indices, -1 for none."""

    parent: int = -1
    child: int = -1
    sibling: int = -1
    scale: Vec3 = (1.0, 1.0, 1.0)
    rotation: Vec3 = (0.0, 0.0, 0.0)
    position: Vec3 = (0.0, 0.0, 0.0)
    """Offset from the parent."""
    kind: int = 1
    """The low half of the section tag: 1, 2 or 3."""
    link: int = -1
    """An earlier bone's index on kind-2 bones."""
    stream: int = 0
    """The MHFU animation stream that drives the bone."""
    name: bytes | None = None
    """MHP3rd's 8-byte name field (a C string, then leftovers); None for MHFU's section."""


@dataclass
class Skeleton:
    bones: list[Bone] = field(default_factory=list)
    params: list[int] = field(default_factory=lambda: [0])
    """The parameter section; on the big monsters the second value is the animated bone count."""
    params_count: int | None = None
    """The parameter section's count word, where it disagrees with `len(params)`."""
    magic: int = FU_MAGIC
    tail: bytes = b""
    """Bytes after the skeleton (a standalone file pads to 0x800)."""

    def roots(self) -> list[int]:
        return [i for i, bone in enumerate(self.bones) if bone.parent == -1]

    @staticmethod
    def sniff(data: bytes) -> bool:
        if len(data) < 24:
            return False
        magic, count, size, tag = (int.from_bytes(data[i : i + 4], "little") for i in (0, 4, 8, 12))
        return magic in (FU_MAGIC, P3RD_MAGIC) and count > 0 and size <= len(data) and tag == 0

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        if not cls.sniff(data):
            raise FormatError("not a skeleton")
        try:
            head = _HEAD.parse(data)
            at = 12 + head.params_size
            if head.params_size < 12 or head.params_size % 4:
                raise FormatError(f"parameter section of {head.params_size:#x} bytes")
            params = Array((head.params_size - 12) // 4, Int32ul).parse(data[24:at])
            bones = []
            for index in range(head.count - 1):
                bone, at = _read_bone(data, at, index)
                bones.append(bone)
        except ConstructError as e:
            raise FormatError(f"skeleton: {e}") from None
        if at != head.size:
            raise FormatError(f"bones end at {at:#x}, the header says {head.size:#x}")
        count = None if head.params_count == len(params) else head.params_count
        return cls(bones, list(params), count, head.magic, data[at:])

    def to_bytes(self) -> bytes:
        body = b"".join(_write_bone(bone, i) for i, bone in enumerate(self.bones))
        params = Array(len(self.params), Int32ul).build(self.params)
        head = _Head(
            magic=self.magic,
            count=1 + len(self.bones),
            size=_HEAD.sizeof() + len(params) + len(body),
            params_count=len(self.params) if self.params_count is None else self.params_count,
            params_size=12 + len(params),
        )
        return _HEAD.build(head) + params + body + self.tail


@dataclass
class _Head(DataclassMixin):
    magic: int = csfield(Int32ul)
    count: int = csfield(Int32ul)
    size: int = csfield(Int32ul)
    params_tag: int = csfield_const(Int32ul, 0)
    params_count: int = csfield(Int32ul)
    params_size: int = csfield(Int32ul)


@dataclass
class _BoneRecord(DataclassMixin):
    tag: int = csfield(Int32ul)
    count: int = csfield_const(Int32ul, 1)
    size: int = csfield(Int32ul)
    index: int = csfield(Int32sl)
    parent: int = csfield(Int32sl)
    child: int = csfield(Int32sl)
    sibling: int = csfield(Int32sl)
    scale: list[float] = csfield(Array(4, Float32l))
    rotation: list[float] = csfield(Array(4, Float32l))
    position: list[float] = csfield(Array(4, Float32l))
    """Each vector carries w = 1."""
    link: int = csfield(Int32sl)
    stream: int = csfield(Int32ul)


_HEAD = DataclassStruct(_Head)
_BONE_RECORD = DataclassStruct(_BoneRecord)
_RECORD = _BONE_RECORD.sizeof()
_FU_REST = 0x10C - _RECORD
"""MHFU's bone section ends in this many zeros."""
_NAME = 8


def _read_bone(data: bytes, at: int, index: int) -> tuple[Bone, int]:
    rec = _BONE_RECORD.parse(data[at : at + _RECORD])
    rest = data[at + _RECORD : at + rec.size]
    if rec.tag & ~0xFFFF != BONE_TAG or rec.index != index or rec.size < _RECORD:
        raise FormatError(f"bone {index}: tag {rec.tag:#x}, index {rec.index}, size {rec.size:#x}")
    if len(rest) != rec.size - _RECORD:
        raise FormatError(f"bone {index} runs past the data")
    if len(rest) == _NAME:
        name: bytes | None = rest
    elif len(rest) == _FU_REST and not any(rest):
        name = None
    else:
        raise FormatError(f"bone {index}: a {rec.size:#x}-byte section")
    if (rec.scale[3], rec.rotation[3], rec.position[3]) != (1.0, 1.0, 1.0):
        raise FormatError(f"bone {index}: w is not 1")
    bone = Bone(
        rec.parent,
        rec.child,
        rec.sibling,
        _vec3(rec.scale),
        _vec3(rec.rotation),
        _vec3(rec.position),
        rec.tag & 0xFFFF,
        rec.link,
        rec.stream,
        name,
    )
    return bone, at + rec.size


def _write_bone(bone: Bone, index: int) -> bytes:
    if bone.kind & ~0xFFFF:
        raise ValueError(f"bone {index}: kind {bone.kind:#x} does not fit 16 bits")
    if bone.name is not None and len(bone.name) != _NAME:
        raise ValueError(f"bone {index}: the name field is {_NAME} bytes")
    rest = bytes(_FU_REST) if bone.name is None else bone.name
    rec = _BoneRecord(
        tag=BONE_TAG | bone.kind,
        size=_RECORD + len(rest),
        index=index,
        parent=bone.parent,
        child=bone.child,
        sibling=bone.sibling,
        scale=[*bone.scale, 1.0],
        rotation=[*bone.rotation, 1.0],
        position=[*bone.position, 1.0],
        link=bone.link,
        stream=bone.stream,
    )
    return _BONE_RECORD.build(rec) + rest


def _vec3(v: list[float]) -> Vec3:
    return v[0], v[1], v[2]
