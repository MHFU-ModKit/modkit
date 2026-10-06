# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A monster PAC as the studio opens it: `mhfu_port.model.Model` with a port manifest's clip
names, the file's build id and `pose()`."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Self

from mhfu_port import build
from mhfu_port.data import Data
from mhfu_port.manifest import Manifest
from mhfu_port.model import MHP3RD, Clip, Model

from mhfu_studio.monster.clips import build_id
from mhfu_studio.monster.core.pose import Pose
from mhfu_studio.monster.inputs import built
from mhfu_studio.shell import places


class Scene(Model):
    """Open one with `open_scene`, `from_bytes`, `from_path` or `from_manifest`."""

    def __init__(self, *args: Any, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.manifest: Manifest | None = None
        self.build_id = (
            None if self.path is None or self.pac is None else build_id(self.path.name, self.pac)
        )
        """`name@digest` of the file, what clip labels are keyed to."""

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
        manifest: Manifest | None = None,
    ) -> Self:
        """`Model.from_bytes`, the clips named by `manifest`."""
        sc = super().from_bytes(data, name, geometry, moveset, em_id, bone_offset, skip_bones, path)
        if manifest is not None:
            sc.attach_manifest(manifest)
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
                m.build.bone_offset,
                m.build.skip_bones,
                manifest=m,
            )
        if side != "port":
            raise ValueError(f"side is 'port' or 'source', not {side!r}")
        b = built(m, pac, data)
        sc = cls.from_bytes(b.pac, m.port.name, manifest=m, path=pac)
        sc.build_id = b.id
        return sc

    # names

    def attach_manifest(self, m: Manifest) -> None:
        """Bind the clip names to a manifest, after an edit renamed them: by executor entry on a
        port, by MHP3rd clip id on its donor."""
        self.manifest = m
        by_slot: dict[int, list[str]] = {}
        for name, c in m.clips.items():
            by_slot.setdefault(c.id if self.game == MHP3RD else c.slot, []).append(name)
        self.rename(by_slot)

    def clip_table(self) -> dict[int, tuple[int, bool]]:
        return {c.slot: (c.frames, c.loop) for c in self.clips}

    def pose(self, key: int | str | Clip, frame: float = 0.0) -> Pose:
        """Frames outside the clip hold per channel."""
        c = self.clip(key)
        rot, loc = self.curves(c).at(float(frame))
        return Pose(self.rig, float(frame), self.rig.world(rot, loc), c.slot)

    def bind_pose(self) -> Pose:
        return Pose(self.rig, 0.0, self.rig.bind_world)

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
