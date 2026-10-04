# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A monster model PAC of either game, assembled into one object to draw, pose or export.

An MHFU model PAC (a native monster or a built port), or an MHP3rd donor: its model PAC, the
geometry companion and the moveset, the separate `emNNN` animation file. A donor's records map
to bones through `records`, never a local offset. An MHFU slot is joined over its skeleton parts
by `fk.rig_clip`; a slot only some parts play poses the joints it owns and leaves the rest at
bind. Nothing here imports `mhfu`, so a Blender extension can bundle it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

import numpy as np
from mhp_formats import anim, fu, p3rd, pmo
from mhp_formats.anim import channel_kind
from mhp_formats.pac import Pac
from mhp_formats.skeleton import P3RD_MAGIC, Skeleton
from mhp_formats.tmh import Tmh
from numpy.typing import NDArray

from . import fk, mesh, motion, records

MHFU = "mhfu"
MHP3RD = "mhp3rd"

SKELETON, MODEL, TEXTURES, ANIMATION = range(4)
"""An MHFU model PAC's entries; the others and the tail ride along."""


class ModelError(ValueError):
    """The file cannot be assembled into a model."""


def clip_key(slot: int) -> str:
    """An unnamed slot's clip name, in a port manifest and on screen alike."""
    return f"clip_{slot:02d}"


@dataclass
class TextureImage:
    index: int
    """The TMH image number."""
    width: int
    height: int
    rgba: NDArray[np.uint8]
    """`(height, width, 4)`, row 0 at the top."""


@dataclass
class MeshGroup:
    index: int
    """The PMO group."""
    material: int | None
    """The TMH image the group's material names; None where it resolves to none."""
    texture: int | None
    """Position in `Model.textures`; None when that image is not there."""
    positions: fk.Floats
    triangles: NDArray[np.int32]
    skin: fk.Skin
    uvs: fk.Floats | None = None
    """`(vertices, 2)` as the GE samples them: the mesh's scale and offset applied."""
    normals: fk.Floats | None = None

    @property
    def n_vertices(self) -> int:
        return len(self.positions)

    @property
    def n_faces(self) -> int:
        return len(self.triangles)


@dataclass
class Clip:
    slot: int
    """The executor a1."""
    frames: int
    """The last keyframe."""
    loop: bool
    tracks: int
    driven: tuple[int, ...]
    """Joints with keyframes in this clip."""
    source: anim.Clip = field(repr=False, compare=False)
    """An MHFU slot over the whole rig (`fk.rig_clip`); a donor's clip as stored."""
    joint_tracks: dict[int, int] | None = field(default=None, repr=False, compare=False)
    """Joint -> track for a donor clip; None: track i drives joint i."""
    names: tuple[str, ...] = ()
    """What a port manifest calls this slot."""
    whole_rig: bool = True
    """False when only some skeleton parts play the slot."""

    @property
    def name(self) -> str:
        return self.names[0] if self.names else clip_key(self.slot)


class Model:
    """Bind skeleton, skinned groups, textures and clips. Open one with `from_bytes` or
    `from_path`."""

    def __init__(
        self,
        name: str,
        game: str,
        skeleton: Skeleton,
        groups: list[MeshGroup],
        textures: list[TextureImage],
        clips: Sequence[Clip],
        *,
        anim: fu.Anim | None = None,
        record_to_bone: dict[int, int] | None = None,
        notes: Sequence[str] = (),
        pac: bytes | None = None,
        path: Path | None = None,
    ) -> None:
        self.name = name
        self.game = game
        """`MHFU` or `MHP3RD`."""
        self.skeleton = skeleton
        self.rig = fk.Rig.from_skeleton(skeleton)
        self.groups = groups
        self.textures = textures
        self.clips = sorted(clips, key=lambda c: c.slot)
        self.anim = anim
        """The MHFU animation pack the clips come from, which `motion.put` writes back into;
        None for a donor."""
        self.record_to_bone = record_to_bone
        """A donor's record -> joint map; None for an MHFU pack."""
        self.notes = list(notes)
        self.pac = pac
        """The model PAC it was read from."""
        self.path = path
        self._by_slot = {c.slot: c for c in self.clips}
        self._by_name: dict[str, Clip] = {}
        self._curves: dict[int, fk.Curves] = {}
        self._merged: fk.Skin | None = None
        self._ranges: list[tuple[int, int]] = []
        self.rename({c.slot: c.names for c in self.clips})

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        name: str,
        geometry: bytes | None = None,
        moveset: bytes | None = None,
        em_id: int | None = None,
        bone_offset: int | None = None,
        skip_bones: Sequence[int] | None = None,
        path: Path | None = None,
    ) -> Self:
        """A model PAC; for a donor, its geometry companion, its moveset, its em id (negative:
        unmapped) and overrides of its record map (`records.for_monster`)."""
        try:
            entries = Pac.from_bytes(data).entries
        except ValueError as e:
            raise ModelError(f"{name}: not a model PAC: {e}") from None
        at = next((i for i, e in enumerate(entries) if Skeleton.sniff(e)), None)
        skeleton = None if at is None else Skeleton.from_bytes(entries[at])
        if skeleton is None or not skeleton.bones:
            raise ModelError(f"{name}: no skeleton, so not a big-monster PAC")
        tmh = next((e for e in entries if Tmh.sniff(e)), None)
        notes: list[str] = []
        textures = _textures(tmh, notes)
        by_image = {t.index: i for i, t in enumerate(textures)}
        n = len(skeleton.bones)
        if skeleton.magic == P3RD_MAGIC:
            try:
                donor = mesh.donor(data, geometry)
                groups = _donor_groups(donor, n, by_image)
                moves = motion.moveset(moveset) if moveset is not None else []
                em = em_id if em_id is not None and em_id >= 0 else None
                clips, r2b = _donor_clips(moves, skeleton, em, bone_offset, skip_bones, notes)
            except ValueError as e:
                raise ModelError(f"{name}: {e}") from None
            return cls(
                name,
                MHP3RD,
                skeleton,
                groups,
                textures,
                clips,
                record_to_bone=r2b,
                notes=notes,
                pac=data,
                path=path,
            )
        try:
            if at != SKELETON:
                raise ValueError(f"the skeleton is entry {at}, not {SKELETON}")
            model = pmo.Pmo.from_bytes(entries[MODEL])
            pack = fu.Anim.from_bytes(entries[ANIMATION])
        except (ValueError, IndexError) as e:
            raise ModelError(f"{name}: not an MHFU monster PAC: {e}") from None
        groups = _fu_groups(model, n, by_image)
        clips = _fu_clips(pack, skeleton, notes)
        if not clips:
            notes.append("no clips: bind pose only")
        return cls(
            name,
            MHFU,
            skeleton,
            groups,
            textures,
            clips,
            anim=pack,
            notes=notes,
            pac=data,
            path=path,
        )

    @classmethod
    def from_path(
        cls,
        path: Path,
        em_id: int | None = None,
        geometry: Path | None = None,
        moveset: Path | None = None,
    ) -> Self:
        """A model PAC file. A donor's geometry and moveset default to the files `records.GEO`
        and `records.ANIM` after a `file_NNNNN` name, its em id to `records.em_for_model`."""
        data = path.read_bytes()
        given = [geometry, moveset]
        notes = []
        if _is_donor(data):
            stem = path.stem
            file_id = int(stem[5:]) if stem.startswith("file_") and stem[5:].isdigit() else None
            if file_id is None and None in given:
                notes.append(f"{path.name} is not a file_NNNNN name: no companions probed")
            for k, step in enumerate((records.GEO, records.ANIM)):
                if given[k] is None and file_id is not None:
                    other = path.with_name(f"file_{file_id + step:05d}{path.suffix}")
                    if other.is_file():
                        given[k] = other
                    else:
                        notes.append(f"{other.name} is missing")
            if em_id is None and file_id is not None:
                em_id = records.em_for_model(file_id)
        geo, moves = (None if p is None else p.read_bytes() for p in given)
        out = cls.from_bytes(data, path.stem, geo, moves, em_id, path=path)
        out.notes[:0] = notes
        return out

    # clips

    def rename(self, names: Mapping[int, Sequence[str]]) -> None:
        """Name the clips by slot; a slot missing from `names` goes unnamed."""
        for c in self.clips:
            c.names = tuple(sorted(names.get(c.slot, ())))
        self._by_name = {n: c for c in self.clips for n in c.names}

    def clip(self, key: int | str | Clip) -> Clip:
        """By slot, by name, or passed through."""
        if isinstance(key, Clip):
            return key
        found = self._by_name.get(key) if isinstance(key, str) else self._by_slot.get(key)
        if found is None:
            raise KeyError(f"no clip {key!r}")
        return found

    def curves(self, key: int | str | Clip) -> fk.Curves:
        c = self.clip(key)
        if c.slot not in self._curves:
            self._curves[c.slot] = fk.Curves(c.source, self.rig, c.joint_tracks)
        return self._curves[c.slot]

    # geometry

    @property
    def n_vertices(self) -> int:
        return sum(g.n_vertices for g in self.groups)

    @property
    def merged(self) -> fk.Skin:
        """Every group in one skin, a viewer's single buffer; `group_range` slices it."""
        if self._merged is None:
            positions: list[list[float]] = []
            influences: list[list[tuple[int, float]]] = []
            for g in self.groups:
                start = len(positions)
                positions += g.skin.positions.tolist()
                influences += [
                    [(int(j), float(w)) for j, w in zip(js, ws, strict=True) if w > 0]
                    for js, ws in zip(g.skin.joints, g.skin.weights, strict=True)
                ]
                self._ranges.append((start, len(positions)))
            self._merged = fk.Skin(positions, influences, self.rig.n)
        return self._merged

    def group_range(self, index: int) -> tuple[int, int]:
        _ = self.merged
        return self._ranges[index]

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} {self.name!r} {self.game}: {self.rig.n} bones, "
            f"{len(self.groups)} groups, {len(self.clips)} clips>"
        )


def _array(rows: Sequence[Sequence[float]], width: int) -> fk.Floats | None:
    return np.array(rows, dtype=np.float64).reshape(-1, width) if rows else None


def _group(
    index: int,
    positions: Sequence[Sequence[float]],
    triangles: Sequence[tuple[int, int, int]],
    influences: Sequence[Sequence[tuple[int, float]]],
    joints: int,
    material: int | None,
    textures: dict[int, int],
    uvs: Sequence[Sequence[float]],
    normals: Sequence[Sequence[float]],
) -> MeshGroup:
    skin = fk.Skin(positions, influences, joints)
    tris = np.array([t for t in triangles if len(set(t)) == 3], dtype=np.int32).reshape(-1, 3)
    return MeshGroup(
        index,
        material,
        None if material is None else textures.get(material),
        skin.positions,
        tris,
        skin,
        _array(uvs, 2),
        _array(normals, 3),
    )


def _fu_groups(model: pmo.Pmo, joints: int, textures: dict[int, int]) -> list[MeshGroup]:
    out = []
    for g, group in enumerate(model.groups()):
        v = group.block.vertices
        su, sv = model.mesh_of(g).uv_scale
        material = model.material(g)
        out.append(
            _group(
                g,
                model.positions(g),
                model.triangles(g),
                model.influences(g),
                joints,
                None if material is None else material.texture,
                textures,
                [(u * su, w * sv) for u, w in v.uvs()],
                v.normals(),
            )
        )
    return out


def _donor_groups(model: p3rd.Pmo, joints: int, textures: dict[int, int]) -> list[MeshGroup]:
    """The porter's own view of the donor's groups (`mesh.parts`)."""
    return [
        _group(
            g, p.positions, p.triangles, p.influences, joints, p.texture, textures, p.uvs, p.normals
        )
        for g, p in enumerate(mesh.parts(model))
    ]


def _textures(tmh: bytes | None, notes: list[str]) -> list[TextureImage]:
    if tmh is None:
        notes.append("no TMH: the model has no textures")
        return []
    out = []
    for i, image in enumerate(Tmh.from_bytes(tmh).images):
        try:
            rgba = image.decode()
        except ValueError as e:
            notes.append(f"texture {i} does not decode: {e}")
            continue
        pixels = np.frombuffer(rgba, dtype=np.uint8).reshape(image.height, image.width, 4)
        out.append(TextureImage(i, image.width, image.height, pixels))
    return out


def _driven(clip: anim.Clip, joint_tracks: dict[int, int] | None, n: int) -> tuple[int, ...]:
    keyed = {t for t, tr in enumerate(clip.tracks) if any(c.keyframes for c in tr.channels)}
    if joint_tracks is None:
        return tuple(sorted(j for j in keyed if j < n))
    return tuple(sorted(j for j, t in joint_tracks.items() if t in keyed and j < n))


def _fu_clips(pack: fu.Anim, skeleton: Skeleton, notes: list[str]) -> list[Clip]:
    parts = sorted({b.stream for b in skeleton.bones})
    live = [
        k for k in parts if any(fk.part_clip(pack, k, s) is not None for s in motion.filled(pack))
    ]
    n = len(skeleton.bones)
    out = []
    for slot in motion.filled(pack):
        clip = fk.rig_clip(pack, slot, skeleton)
        if clip is None:
            continue
        held = [c for k in live if (c := fk.part_clip(pack, k, slot)) is not None]
        out.append(
            Clip(
                slot,
                motion.frames(clip),
                bool(clip.loop),
                sum(len(c.tracks) for c in held),
                _driven(clip, None, n),
                clip,
                whole_rig=len(held) == len(live),
            )
        )
    partial = [c.slot for c in out if not c.whole_rig]
    if partial:
        notes.append(
            f"anims {', '.join(map(str, partial))} are partial: they move the joints of only "
            f"some of the {len(live)} skeleton parts and leave the rest still"
        )
    return out


def _donor_clips(
    moves: Sequence[anim.Clip | None],
    skeleton: Skeleton,
    em: int | None,
    bone_offset: int | None,
    skip_bones: Sequence[int] | None,
    notes: list[str],
) -> tuple[list[Clip], dict[int, int] | None]:
    """Clips through the porter's record map; `{record: bone}` alongside."""
    if not any(c is not None for c in moves):
        notes.append("no MHP3rd moveset was loaded: bind pose only")
        return [], None
    if em is None:
        notes.append(
            f"no em id for the original, so the record map falls back to offset "
            f"{records.DEFAULT_OFFSET} with no skips: very probably WRONG. Pin it with the fork "
            "rule (mhfu_port.records)."
        )
    n = len(skeleton.bones)
    bone_record = records.for_moveset(moves, em, n, bone_offset, skip_bones)
    parents = [b.parent for b in skeleton.bones]
    loc = {
        bone
        for c in moves
        if c is not None
        for bone, r in bone_record.items()
        if r < len(c.tracks)
        and any(
            ch.keyframes and (k := channel_kind(ch.bit)) is not None and k[0] == "loc"
            for ch in c.tracks[r].channels
        )
    }
    below = records.loc_below_fork(parents, loc)
    if below:
        notes.append(
            f"the record map puts LOCATION channels on joints {below}, below the body fork "
            f"(joint {records.body_fork(parents)}): half the animal lifts and the waist tears"
        )
    out = [
        Clip(
            slot,
            motion.frames(c),
            bool(c.loop),
            len(c.tracks),
            _driven(c, bone_record, n),
            c,
            bone_record,
        )
        for slot, c in enumerate(moves)
        if c is not None
    ]
    return out, {r: b for b, r in bone_record.items()}


def _is_donor(data: bytes) -> bool:
    try:
        entries = Pac.from_bytes(data).entries
    except ValueError:
        return False
    return any(Skeleton.sniff(e) and Skeleton.from_bytes(e).magic == P3RD_MAGIC for e in entries)
