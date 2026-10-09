# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Building a port from its manifest: the donor's model, rig and moveset on the host's PAC.

`build` runs the steps below in order; each is a function of the ones before, so a tool or a
test can stop at any of them.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from mhp_formats import fu, p3rd, pmo
from mhp_formats.anim import Clip
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Skeleton
from mhp_formats.tmh import Tmh

from . import constraints, mesh, motion, records, retarget, travel
from . import layout as layouts
from . import rig as rigs
from . import skin as skins
from .data import Data
from .layout import Layout
from .manifest import Build, Manifest, Skin
from .mesh import Part, Skinned
from .model import ANIMATION, MODEL, SKELETON, TEXTURES
from .rig import Rig, Tip

Mode = Literal["source_skeleton", "retarget"]


@dataclass(frozen=True)
class Donor:
    """The MHP3rd monster."""

    model: p3rd.Pmo
    skeleton: Skeleton
    textures: bytes | None
    """Its TMH as stored; None keeps the host's."""
    clips: dict[int, Clip]
    """Its moveset by MHP3rd id (`motion.clip_id`)."""
    em: int | None
    """Its species, which picks the record map; None builds unmapped."""


@dataclass(frozen=True)
class Host:
    """The MHFU model PAC the port rides."""

    pac: Pac
    skeleton: Skeleton
    model: pmo.Pmo | None
    """None where the PAC carries no PMO, which rules out a transferred skin."""
    anim: fu.Anim


@dataclass(frozen=True)
class Binding:
    """The rig the port ships and how the donor's bones land on it."""

    mode: Mode
    rig: Rig
    bone_of: dict[int, int | None]
    """Output joint -> the donor bone whose motion it plays; None or missing rests."""
    joint_of: dict[int, int]
    """Donor bone -> the output joint its skin rides; a bone missing here loses its weight."""
    dead: frozenset[int]
    """Animated joints nothing drives, which no guessed skin may ride."""


@dataclass(frozen=True)
class Summary:
    mode: Mode
    skin: Skin
    """The skin built, after the upgrade and the fallbacks."""
    em: int | None
    lead_pad: int
    streams: tuple[int, ...]
    groups: int
    vertices: int
    clips: int
    """The donor's distinct clips."""
    placed: int
    """The clips in an executor entry."""
    entries: int
    """The host's executor entries."""
    size: int
    host_size: int

    def text(self) -> str:
        em = f"em {self.em}" if self.em is not None else "unmapped (default record offset)"
        return "\n".join(
            (
                f"{self.mode}, skin {self.skin}, {em}",
                f"streams {'+'.join(map(str, self.streams))}, lead pad {self.lead_pad}",
                f"{self.groups} groups, {self.vertices} vertices, {self.clips} clips, "
                f"{self.placed} in the host's {self.entries} executor entries",
                f"{self.size} bytes, host {self.host_size} ({self.size - self.host_size:+d})",
            )
        )


@dataclass(frozen=True)
class Built:
    pac: bytes
    summary: Summary
    layout: Layout
    tip: Tip | None = None
    """The tail tip's chain and the joints it rides, for `<port>_moves.lua`; None without one."""


# 1. the inputs


def em_of(m: Manifest) -> int | None:
    """The manifest's em id, else the model file's; a negative one opts out of the map."""
    em = m.source.em_id if m.source.em_id is not None else records.em_for_model(m.source.model)
    return em if em is not None and em >= 0 else None


def donor(m: Manifest, data: Data) -> Donor:
    """The donor's model PAC, geometry and moveset."""
    raw = data.p3rd.read(m.source.model)
    entries = Pac.from_bytes(raw).entries
    skeleton = next((Skeleton.from_bytes(e) for e in entries if Skeleton.sniff(e)), None)
    if skeleton is None:
        raise ValueError(f"MHP3rd file {m.source.model} carries no skeleton")
    return Donor(
        model=mesh.donor(raw, data.p3rd.read(m.source.geo)),
        skeleton=skeleton,
        textures=next((e for e in entries if Tmh.sniff(e)), None),
        clips=motion.moveset(data.p3rd.read(m.source.anim)),
        em=em_of(m),
    )


def host(m: Manifest, data: Data) -> Host:
    """The host species' model PAC."""
    pac = Pac.from_bytes(data.fu.read(m.port.host_frame))
    e = pac.entries
    return Host(
        pac=pac,
        skeleton=Skeleton.from_bytes(e[SKELETON]),
        model=pmo.Pmo.from_bytes(e[MODEL]) if pmo.Pmo.sniff(e[MODEL]) else None,
        anim=fu.Anim.from_bytes(e[ANIMATION]),
    )


# 2. the record map


def record_map(d: Donor, b: Build) -> dict[int, int]:
    """`{donor bone: record}`; the manifest's `bone_offset` and `skip_bones` override the
    species' table rows."""
    return records.for_moveset(
        d.clips.values(), d.em, len(d.skeleton.bones), b.bone_offset, b.skip_bones
    )


def animated(b: Build, record_of: Mapping[int, int], bones: int) -> int:
    """The donor bones the port animates: the manifest's count, else up to the last driven
    bone, else (or out of range) the whole rig."""
    n = b.animated if b.animated is not None else max(record_of, default=bones - 1) + 1
    return n if 0 < n <= bones else bones


# 3. the rig


def binding(b: Build, d: Donor, h: Host, animated: int) -> Binding:
    """The donor's own rig with `animated` bones (`source_skeleton`), or the host's with each
    host joint matched to a donor bone and the animated joints left unmatched dead."""
    if b.source_skeleton:
        rig = rigs.from_donor(d.skeleton, h.skeleton, animated)
        bone_of: dict[int, int | None] = {j: bone for bone, j in rig.joint_of.items()}
        return Binding("source_skeleton", rig, bone_of, dict(rig.joint_of), frozenset())
    rig = rigs.from_host(h.skeleton)
    match = retarget.match(d.skeleton, h.skeleton)
    joint_of: dict[int, int] = {}
    for j in sorted(match):
        bone = match[j]
        if bone is not None:
            joint_of.setdefault(bone, j)
    dead = frozenset(j for j in range(rig.animated) if match.get(j) is None)
    return Binding("retarget", rig, dict(match), joint_of, dead)


# 4. the geometry


def parts(d: Donor, b: Build) -> list[Part]:
    """The donor's draw groups, less those riding a `drop_joints` bone."""
    out = mesh.parts(d.model)
    return mesh.drop(out, b.drop_joints) if b.drop_joints else out


def skin(parts: Sequence[Part], b: Build, bind: Binding, h: Host) -> tuple[list[Skinned], Skin]:
    """The parts bound to the rig, and the skin used: `auto` means `source` on the donor's own
    rig when the donor carries weights; `source` without weights and `transfer` without a host
    PMO fall back to `auto`."""
    weighted = any(v for p in parts for v in p.influences)
    mode = b.skin
    if mode == "auto" and bind.mode == "source_skeleton" and weighted:
        mode = "source"
    if mode == "source" and weighted:
        return skins.source(parts, bind.joint_of), "source"
    if mode == "transfer" and h.model is not None:
        return skins.transfer(parts, h.model, bind.rig.parents, dead=bind.dead), "transfer"
    rig = bind.rig
    out = skins.auto(parts, rig.bind, rig.parents, exclude=bind.dead, nb=b.nb)
    skins.weld(out, rig.bind)
    return out, "auto"


# 5. the motion


def layout(m: Manifest, d: Donor, h: Host) -> Layout:
    """Each donor clip's executor entry: the manifest's, else the packer's."""
    return layouts.of(m, d.clips, h.anim)


def animation(
    d: Donor,
    h: Host,
    bind: Binding,
    record_of: Mapping[int, int],
    placed: Layout,
    lift: float = 0.0,
) -> fu.Anim:
    """The donor's moveset on the rig's joints in the entries `placed` gives it, its pelvis
    raised by `lift` world units, and on its own rig its travel and turns where the engine takes
    them (`travel.carry`). Entries it leaves keep the host's clips on the host's rig."""
    clips: Mapping[int, Clip] = d.clips
    if lift:
        bone_of_record = {r: bone for bone, r in record_of.items()}
        parents = [bone.parent for bone in d.skeleton.bones]
        pelvis = motion.pelvis(clips.values(), bone_of_record, parents)
        if pelvis is not None:
            clips = motion.lift(clips, lift, pelvis)
    # records are not positional in either mode: joint -> donor bone -> record
    track_of = {j: record_of.get(bone) for j, bone in bind.bone_of.items() if bone is not None}
    keep = bind.mode == "retarget"
    out = motion.build(clips, placed.entries, h.anim, bind.rig.streams, track_of, keep)
    return out if keep else travel.carry(out, bind.rig.skeleton)


def authored(m: Manifest, placed: Layout) -> dict[int, float]:
    """Entry -> the manifest's `turn` of the clip it holds."""
    return {
        placed.ids[c.id]: c.turn
        for c in m.clips.values()
        if c.turn is not None and c.id in placed.ids
    }


# 6. the PAC


def pac(h: Host, rig: Rig, model: pmo.Pmo, textures: bytes | None, anim: fu.Anim) -> bytes:
    """The host's PAC with the port's skeleton, PMO, textures and animation in their entries.

    Raises `constraints.ConstraintError` where they break an engine rule."""
    constraints.check(model, rig.skeleton, anim)
    entries = list(h.pac.entries)
    entries[SKELETON] = rig.skeleton.to_bytes()
    entries[MODEL] = model.to_bytes()
    if textures is not None:
        entries[TEXTURES] = textures
    entries[ANIMATION] = anim.to_bytes()
    return Pac(entries, h.pac.align, h.pac.tail).to_bytes()


def build(m: Manifest, data: Data) -> Built:
    """The port's model PAC."""
    d, h = donor(m, data), host(m, data)
    record_of = record_map(d, m.build)
    bind = binding(m.build, d, h, animated(m.build, record_of, len(d.skeleton.bones)))
    kept = parts(d, m.build)
    skinned, skin_used = skin(kept, m.build, bind, h)
    model = mesh.build(skinned, d.model.scale, tip=bind.rig.tip)
    placed = layout(m, d, h)
    anim = animation(d, h, bind, record_of, placed, m.build.ground_lift)
    turned = travel.turns(anim, bind.rig.skeleton, authored(m, placed))
    frames = {e: motion.frames(d.clips[cid]) for e, cid in placed.entries.items()}
    placed = dataclasses.replace(placed, turns=turned, frames=frames)
    out = pac(h, bind.rig, model, d.textures, anim)
    summary = Summary(
        mode=bind.mode,
        skin=skin_used,
        em=d.em,
        lead_pad=bind.rig.lead_pad,
        streams=tuple(bind.rig.streams),
        groups=len(kept),
        vertices=sum(len(p.positions) for p in kept),
        clips=len({id(c) for c in d.clips.values()}),
        placed=len(placed.entries),
        entries=placed.capacity,
        size=len(out),
        host_size=len(h.pac.to_bytes()),
    )
    return Built(out, summary, placed, bind.rig.tip)
