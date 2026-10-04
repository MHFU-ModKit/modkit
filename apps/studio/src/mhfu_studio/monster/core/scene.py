# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A monster PAC assembled into one object a viewer, a test or a CLI can pose.

Both games come through one door: an MHFU model PAC (a native monster, or a built port) and an
MHP3rd donor (model PAC, its geometry companion and moveset). A donor's records are not
positional: they map to bones through the porter's own record map (`mhfu_port.build.record_map`),
never a local offset. An MHFU slot is joined over its skeleton parts by `mhfu_port.fk.rig_clip`;
a slot only some parts play (24/25 on a Tigrex frame) poses the joints it owns and leaves the
rest at bind.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from mhfu_port import build, fk, mesh, motion, records
from mhfu_port.data import Data
from mhfu_port.manifest import GEO, Manifest
from mhfu_port.manifest import Build as BuildSettings
from mhfu_port.verify import Port
from mhp_formats import anim, p3rd, pmo
from mhp_formats.anim import channel_kind
from mhp_formats.pac import Pac
from mhp_formats.skeleton import P3RD_MAGIC, Skeleton
from mhp_formats.tmh import Tmh
from numpy.typing import NDArray

from mhfu_studio.monster.clips import build_id, clip_key
from mhfu_studio.monster.core.pose import Pose
from mhfu_studio.monster.inputs import built
from mhfu_studio.shell import places

MHFU = "mhfu"
MHP3RD = "mhp3rd"


class SceneError(ValueError):
    """The file cannot be assembled into a scene."""


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
    material: int | None
    """The TMH image the group's material names; None where it resolves to none."""
    texture: int | None
    """Position in `Scene.textures`; None when that image is not there."""
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
    joint_tracks: dict[int, int] | None = field(default=None, repr=False, compare=False)
    """Joint -> track for a donor clip; None: track i drives joint i."""
    names: tuple[str, ...] = ()
    """The manifest's names for this slot."""
    whole_rig: bool = True
    """False when only some skeleton parts play the slot."""
    _curves: fk.Curves | None = field(default=None, repr=False, compare=False)

    @property
    def name(self) -> str:
        return self.names[0] if self.names else clip_key(self.slot)


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
    """The porter's own view of the donor's groups (`mhfu_port.mesh.parts`)."""
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


def _fu_clips(port: Port, notes: list[str]) -> list[Clip]:
    streams = port.anim.streams
    live = sorted({fk.FU_PART_STREAM * b.stream for b in port.skeleton.bones})
    live = [s for s in live if s < len(streams) and any(c is not None for c in streams[s])]
    n = len(port.skeleton.bones)
    out = []
    for slot in port.slots():
        clip = port.clip(slot)
        if clip is None:
            continue
        held = [s for s in live if slot < len(streams[s]) and streams[s][slot] is not None]
        tracks = sum(len(c.tracks) for s in held if (c := streams[s][slot]) is not None)
        out.append(
            Clip(
                slot,
                motion.frames(clip),
                bool(clip.loop),
                tracks,
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
    d: build.Donor, settings: BuildSettings, notes: list[str]
) -> tuple[list[Clip], dict[int, int] | None]:
    """Clips through the porter's record map; `{record: bone}` alongside."""
    if not any(c is not None for c in d.clips):
        notes.append("no MHP3rd moveset was loaded: bind pose only")
        return [], None
    if d.em is None:
        notes.append(
            f"no em id for the original, so the record map falls back to offset "
            f"{records.DEFAULT_OFFSET} with no skips: very probably WRONG. Pin it with the fork "
            "rule (mhfu_port.records)."
        )
    bone_record = build.record_map(d, settings)
    n = len(d.skeleton.bones)
    parents = [b.parent for b in d.skeleton.bones]
    loc = {
        bone
        for c in d.clips
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
        for slot, c in enumerate(d.clips)
        if c is not None
    ]
    return out, {r: b for b, r in bone_record.items()}


class Scene:
    """Bind skeleton, skinned groups, textures, clips and `pose()`. Open one with `open_scene`,
    `from_bytes` or `from_manifest`."""

    def __init__(
        self,
        name: str,
        game: str,
        skeleton: Skeleton,
        groups: list[MeshGroup],
        textures: list[TextureImage],
        clips: list[Clip],
        path: Path | None = None,
        manifest: Manifest | None = None,
        record_to_bone: dict[int, int] | None = None,
        notes: Sequence[str] = (),
        build_id: str | None = None,
        pac: bytes | None = None,
    ) -> None:
        self.name = name
        self.game = game
        self.skeleton = skeleton
        self.rig = fk.Rig.from_skeleton(skeleton)
        self.groups = groups
        self.textures = textures
        self.clips = sorted(clips, key=lambda c: c.slot)
        self.path = path
        self.manifest = manifest
        self.record_to_bone = record_to_bone
        """A donor's record -> joint map; None for an MHFU pack."""
        self.notes = list(notes)
        self.build_id = build_id
        """`name@digest` of the file, what clip labels are keyed to."""
        self.pac = pac
        """The model PAC it was read from."""
        self._by_slot = {c.slot: c for c in self.clips}
        self._by_name: dict[str, Clip] = {}
        self._merged: fk.Skin | None = None
        self._ranges: list[tuple[int, int]] = []
        if manifest is not None:
            self.attach_manifest(manifest)

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        name: str,
        geometry: bytes | None = None,
        moveset: bytes | None = None,
        em_id: int | None = None,
        settings: BuildSettings | None = None,
        manifest: Manifest | None = None,
        path: Path | None = None,
    ) -> Scene:
        """A model PAC; for a donor, its geometry companion and moveset, its em id (negative:
        unmapped) and the manifest's `[build]` overrides of the record map."""
        try:
            entries = Pac.from_bytes(data).entries
        except ValueError as e:
            raise SceneError(f"{name}: not a model PAC: {e}") from None
        skeleton = next((Skeleton.from_bytes(e) for e in entries if Skeleton.sniff(e)), None)
        if skeleton is None or not skeleton.bones:
            raise SceneError(f"{name}: no skeleton, so not a big-monster PAC")
        tmh = next((e for e in entries if Tmh.sniff(e)), None)
        notes: list[str] = []
        textures = _textures(tmh, notes)
        by_image = {t.index: i for i, t in enumerate(textures)}
        n = len(skeleton.bones)
        ident = None if path is None else build_id(path.name, data)
        if skeleton.magic == P3RD_MAGIC:
            try:
                model = mesh.donor(data, geometry)
                clips_ = motion.moveset(moveset) if moveset is not None else []
                em = em_id if em_id is not None and em_id >= 0 else None
                donor = build.Donor(model, skeleton, tmh, clips_, em)
                groups = _donor_groups(model, n, by_image)
                clips, r2b = _donor_clips(donor, settings or BuildSettings(), notes)
            except ValueError as e:
                raise SceneError(f"{name}: {e}") from None
            return cls(
                name,
                MHP3RD,
                skeleton,
                groups,
                textures,
                clips,
                path,
                manifest,
                r2b,
                notes,
                ident,
                data,
            )
        try:
            port = Port(data)
        except (ValueError, IndexError) as e:
            raise SceneError(f"{name}: not an MHFU monster PAC: {e}") from None
        groups = _fu_groups(port.model, n, by_image)
        clips = _fu_clips(port, notes)
        if not clips:
            notes.append("no clips: bind pose only")
        return cls(
            name, MHFU, skeleton, groups, textures, clips, path, manifest, None, notes, ident, data
        )

    @classmethod
    def from_path(cls, path: Path, em_id: int | None = None) -> Scene:
        """A model PAC file; a donor's companions are the next two files."""
        data = path.read_bytes()
        stem = path.stem
        file_id = int(stem[5:]) if stem.startswith("file_") and stem[5:].isdigit() else None
        companions: list[bytes | None] = [None, None]
        notes = []
        if _is_donor(data):
            if file_id is None:
                notes.append(f"{path.name} is not a file_NNNNN name: no companions probed")
            else:
                for k in (0, 1):
                    other = path.with_name(f"file_{file_id + GEO + k:05d}.bin")
                    companions[k] = other.read_bytes() if other.is_file() else None
                    if companions[k] is None:
                        notes.append(f"{other.name} is missing")
            if em_id is None and file_id is not None:
                em_id = records.em_for_model(file_id)
        sc = cls.from_bytes(data, path.stem, companions[0], companions[1], em_id, path=path)
        sc.notes[:0] = notes
        return sc

    @classmethod
    def from_manifest(
        cls,
        m: Manifest,
        pac: Path | None = None,
        side: str = "port",
        data: Data | None = None,
    ) -> Scene:
        """The port: its built PAC at `pac`, else built in memory from the extracted games; or
        its donor (`side="source"`) through the porter's own reading."""
        if side == "source":
            games = data or places.games()
            em = build.em_of(m)
            return cls.from_bytes(
                games.p3rd.read(m.source.model),
                f"{m.port.name} (source)",
                games.p3rd.read(m.source.geo),
                games.p3rd.read(m.source.anim),
                -1 if em is None else em,
                m.build,
                m,
            )
        if side != "port":
            raise ValueError(f"side is 'port' or 'source', not {side!r}")
        b = built(m, pac, data)
        sc = cls.from_bytes(b.pac, m.port.name, manifest=m, path=pac)
        sc.build_id = b.id
        return sc

    # names

    def attach_manifest(self, m: Manifest) -> None:
        """Bind the clip names to a manifest, after an edit renamed them."""
        self.manifest = m
        by_slot: dict[int, list[str]] = {}
        for name, c in m.clips.items():
            by_slot.setdefault(c.slot, []).append(name)
        for clip in self.clips:
            clip.names = tuple(sorted(by_slot.get(clip.slot, ())))
        self._by_name = {n: c for c in self.clips for n in c.names}

    def clip_table(self) -> dict[int, tuple[int, bool]]:
        return {c.slot: (c.frames, c.loop) for c in self.clips}

    def clip(self, key: int | str | Clip) -> Clip:
        """By slot, by manifest name, or passed through."""
        if isinstance(key, Clip):
            return key
        found = self._by_name.get(key) if isinstance(key, str) else self._by_slot.get(key)
        if found is None:
            raise KeyError(f"no clip {key!r}")
        return found

    def curves(self, key: int | str | Clip) -> fk.Curves:
        c = self.clip(key)
        if c._curves is None:
            c._curves = fk.Curves(c.source, self.rig, c.joint_tracks)
        return c._curves

    def pose(self, key: int | str | Clip, frame: float = 0.0) -> Pose:
        """Frames outside the clip hold per channel."""
        c = self.clip(key)
        rot, loc = self.curves(c).at(float(frame))
        return Pose(self.rig, float(frame), self.rig.world(rot, loc), c.slot)

    def bind_pose(self) -> Pose:
        return Pose(self.rig, 0.0, self.rig.bind_world)

    # geometry

    @property
    def n_vertices(self) -> int:
        return sum(g.n_vertices for g in self.groups)

    @property
    def merged(self) -> fk.Skin:
        """Every group in one skin, a viewer's single buffer; `group_range` slices it."""
        if self._merged is None:
            positions: list[Any] = []
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

    # reporting

    def clip_mismatches(self) -> list[str]:
        """Where the manifest's clip fingerprints disagree with this PAC."""
        out: list[str] = []
        for name, mc in sorted((self.manifest.clips if self.manifest else {}).items()):
            got = self._by_slot.get(mc.slot)
            if got is None:
                out.append(f"clips.{name}: slot {mc.slot} is not in this PAC")
                continue
            if mc.frames is not None and mc.frames != got.frames:
                out.append(
                    f"clips.{name}: manifest says {mc.frames} frames, the PAC has {got.frames}"
                )
            if mc.loop is not None and mc.loop != got.loop:
                out.append(f"clips.{name}: manifest says loop={mc.loop}, the PAC has {got.loop}")
        return out

    def summary(self) -> str:
        partial = [c.slot for c in self.clips if not c.whole_rig]
        tex = ", ".join(f"#{t.index} {t.width}x{t.height}" for t in self.textures) or "none"
        widest = max((g.skin.joints.shape[1] for g in self.groups), default=0)
        lines = [
            f"{self.name}  [{self.game}]" + (f"  {self.path}" if self.path else ""),
            f"  skeleton  {self.rig.n} bones, {int((self.rig.parents < 0).sum())} roots, "
            f"depth {len(self.rig.levels) - 1}",
            f"  geometry  {len(self.groups)} groups, {self.n_vertices} vertices, "
            f"{sum(g.n_faces for g in self.groups)} triangles, max {widest} influences/vertex",
            f"  textures  {len(self.textures)}  ({tex})",
            f"  clips     {len(self.clips)}  ({sum(c.loop for c in self.clips)} looping"
            + (f", {len(partial)} partial: {partial}" if partial else "")
            + f"), frames {min((c.frames for c in self.clips), default=0)}.."
            f"{max((c.frames for c in self.clips), default=0)}",
        ]
        if self.record_to_bone:
            js = sorted(self.record_to_bone.values())
            lines.append(f"  anim map  {len(js)} records -> joints {js[0]}..{js[-1]}")
        lines += [f"  note: {n}" for n in [*self.notes, *self.clip_mismatches()]]
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"<Scene {self.name!r} {self.game}: {self.rig.n} bones, {len(self.groups)} groups, "
            f"{len(self.clips)} clips>"
        )


def _is_donor(data: bytes) -> bool:
    try:
        entries = Pac.from_bytes(data).entries
    except ValueError:
        return False
    return any(Skeleton.sniff(e) and Skeleton.from_bytes(e).magic == P3RD_MAGIC for e in entries)


def open_scene(target: str | Path | Manifest, **kw: Any) -> Scene:
    """A PAC path, a manifest path or a loaded manifest; keywords go to `from_manifest` (`pac`,
    `side`, `data`) or `from_path` (`em_id`)."""
    if isinstance(target, Manifest):
        return Scene.from_manifest(target, **kw)
    path = Path(target)
    if path.suffix == ".toml":
        from mhfu_port import manifest

        return Scene.from_manifest(manifest.load(path), **kw)
    return Scene.from_path(path, **kw)
