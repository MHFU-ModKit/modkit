# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""MHP3rd's PMO, version `102`: a 0x30 mesh record carrying the mesh's own scale, lists and
buffers aligned to 4, a remap table only on some models, and the big models' geometry region in
a companion file (the next DATA.BIN file), still counted in the header's size.
"""

from dataclasses import dataclass
from typing import ClassVar, Self, cast

from construct import Array, Float32l, Int32ul
from construct_typed import DataclassMixin, DataclassStruct, csfield

from .. import pmo
from .._base import FormatError
from ..pmo import MeshCounts, Vec3


@dataclass
class _MeshRecord(DataclassMixin):
    scale: list[float] = csfield(Array(4, Float32l))
    uv_scale: list[float] = csfield(Array(2, Float32l))
    uv_offset: list[float] = csfield(Array(2, Float32l))
    lighting: int = csfield(Int32ul)
    blend: int = csfield(Int32ul)
    counts: MeshCounts = csfield(DataclassStruct(MeshCounts))


@dataclass
class Mesh(pmo.Mesh):
    scale: Vec3 = (1.0, 1.0, 1.0)
    """Multiplies the dequantised positions of the mesh's groups, in place of `Pmo.scale`."""
    scale_w: float = 0.0
    uv_offset: tuple[float, float] = (0.0, 0.0)


@dataclass
class Pmo(pmo.Pmo):
    """`from_bytes` takes the companion file as `geometry` when `needs_geometry` says so."""

    remap_table: bool = True
    """False: no remap table, and every mesh's materials run in order (most models)."""
    external: bool = False
    """The geometry region lives in the companion file: `to_bytes` stops where it would start
    and `geometry_bytes` writes it."""
    geometry_tail: bytes = b""
    """Companion bytes after the region (the file pads to 0x800 with leftover memory)."""

    VERSION: ClassVar[bytes] = b"102\0"
    ALIGN: ClassVar[int] = 4
    _MESH: ClassVar[DataclassStruct] = DataclassStruct(_MeshRecord)  # type: ignore[type-arg]

    @staticmethod
    def sniff(data: bytes) -> bool:
        return pmo._sniff(data, Pmo.VERSION)

    @staticmethod
    def needs_geometry(data: bytes) -> bool:
        """The PMO's geometry is in a companion file, which `from_bytes` then needs."""
        if not Pmo.sniff(data):
            return False
        size, geometry = (int.from_bytes(data[i : i + 4], "little") for i in (8, 0x34))
        return size > len(data) and geometry == len(data)

    @classmethod
    def from_bytes(cls, data: bytes, geometry: bytes | None = None) -> Self:
        if cls.needs_geometry(data) and geometry is None:
            raise FormatError("the PMO's geometry is in a companion file, which was not given")
        return cls._read(data, geometry)

    def to_bytes(self) -> bytes:
        tables, region = self._layout()
        if not self.external:
            return bytes(tables) + region + self.tail
        if self.tail:
            raise ValueError("a PMO with external geometry ends where the geometry would start")
        return bytes(tables)

    def geometry_bytes(self) -> bytes:
        """The companion file."""
        if not self.external:
            raise ValueError("the geometry is inline")
        return self._layout()[1] + self.geometry_tail

    def scale_of(self, g: int) -> Vec3:
        mesh = self.mesh_of(g)
        return mesh.scale if isinstance(mesh, Mesh) else self.scale

    @classmethod
    def _mesh(cls, record: DataclassMixin) -> tuple[pmo.Mesh, MeshCounts]:
        rec = cast(_MeshRecord, record)
        mesh = Mesh(
            uv_scale=(rec.uv_scale[0], rec.uv_scale[1]),
            lighting=rec.lighting,
            blend=rec.blend,
            scale=(rec.scale[0], rec.scale[1], rec.scale[2]),
            scale_w=rec.scale[3],
            uv_offset=(rec.uv_offset[0], rec.uv_offset[1]),
        )
        return mesh, rec.counts

    def _mesh_record(self, mesh: pmo.Mesh, counts: MeshCounts) -> DataclassMixin:
        if not isinstance(mesh, Mesh):
            raise ValueError("an MHP3rd PMO takes p3rd.pmo.Mesh meshes")
        return _MeshRecord(
            [*mesh.scale, mesh.scale_w],
            list(mesh.uv_scale),
            list(mesh.uv_offset),
            mesh.lighting,
            mesh.blend,
            counts,
        )

    def _store(self, remap_table: bool, geometry_tail: bytes | None) -> None:
        self.remap_table = remap_table
        self.external = geometry_tail is not None
        self.geometry_tail = geometry_tail or b""

    def _remap_table(self) -> bool:
        return self.remap_table
