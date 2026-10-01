# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The clip vocabulary: what each slot of a built port holds, and whether a clip's name still
points at the clip it was written for.

A port's slot holds the donor's own clip (CARRIED), the builder's fill, a copy of the donor's
first clip (FILLER: forcing it plays idle, which looks exactly like a failed override), the
host's clip, or something else; a donor clip whose slot the host lacks is DROPPED. Slots move
between builds, so a label records `(frames, loop)` and the build it was written against, and
`track_labels` looks the fingerprint up in the build at hand.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from mhfu_port import motion, slots
from mhfu_port.manifest import Clip, Manifest, ManifestError, Move
from mhp_formats import anim
from mhp_formats.anim import AnimPack

from mhfu_studio.monster.document import PortDocument

Fingerprint = tuple[int, bool]
"""(last keyframe, loop): what identifies a clip across builds."""

CARRIED = "CARRIED"
FILLER = "FILLER"
HOST = "HOST"
ALTERED = "ALTERED"
DROPPED = "DROPPED"
UNKNOWN = "UNKNOWN"
"""No donor moveset to compare against."""

CURRENT = "CURRENT"
STILL_VALID = "STILL_VALID"
MOVED = "MOVED"
AMBIGUOUS = "AMBIGUOUS"
LOST = "LOST"
UNCHECKABLE = "UNCHECKABLE"
SUSPECT = (MOVED, AMBIGUOUS, LOST, UNCHECKABLE)
"""Label statuses not to trust until someone looks again."""

UNRECORDED = "unrecorded: {}, written against a build nobody wrote down"
"""Provenance for an imported label file that does not name its build."""

NAME = re.compile(r"[A-Za-z0-9_-]+")
"""What a clip, move or part name may be: a bare TOML key."""


def check_name(name: str) -> str:
    name = name.strip()
    if not NAME.fullmatch(name):
        raise ManifestError(f"{name!r} is not a name: letters, digits, - and _")
    return name


def clip_table(anim: AnimPack) -> dict[int, Fingerprint]:
    """Slot -> fingerprint of an MHFU pack (a native monster or a built port)."""
    return {s: (frames, bool(loop)) for s, (frames, loop) in slots.slot_prints(anim).items()}


def pac_clip_table(pac: bytes) -> dict[int, Fingerprint]:
    return clip_table(slots.anim_of(pac))


def source_clip_table(moveset: bytes) -> dict[int, Fingerprint]:
    """Slot -> fingerprint of an MHP3rd donor moveset: its main clip set."""
    clips = motion.moveset(moveset)
    return {s: _print(c) for s, c in enumerate(clips) if c is not None}


def _print(clip: anim.Clip) -> Fingerprint:
    frames, loop = slots.fingerprint(clip)
    return frames, bool(loop)


def build_id(name: str, data: bytes) -> str:
    """`zinogre.bin@09091d56`: the file name and a short digest of its bytes."""
    return f"{name}@{hashlib.sha1(data).hexdigest()[:8]}"


# coverage


@dataclass(frozen=True)
class SlotCoverage:
    slot: int
    kind: str
    frames: int
    loop: bool
    source: Fingerprint | None = None
    """The donor clip of the same index."""

    @property
    def scriptable(self) -> bool:
        """Forcing this a1 plays the donor's own clip."""
        return self.kind == CARRIED

    def why(self) -> str:
        fp = f"{self.frames}f, loop={self.loop}"
        return {
            CARRIED: f"the donor's own clip {self.slot}, intact ({fp})",
            FILLER: f"a COPY OF THE IDLE clip ({fp}). Forcing this a1 plays idle, which on "
            "screen is identical to the override never firing.",
            HOST: f"the HOST species' own clip ({fp}): this a1 plays the host animal's motion "
            "on your rig.",
            ALTERED: f"{fp}: matches neither the donor's clip {self.slot} nor the host's.",
            UNKNOWN: f"{fp}. No donor moveset to compare against.",
        }[self.kind]


@dataclass
class Coverage:
    slots: dict[int, SlotCoverage] = field(default_factory=dict)
    dropped: dict[int, Fingerprint] = field(default_factory=dict)
    """Donor slots the host has no slot for."""
    has_source: bool = False
    has_host: bool = False

    def counts(self) -> dict[str, int]:
        out = dict.fromkeys((CARRIED, FILLER, HOST, ALTERED, UNKNOWN), 0)
        for c in self.slots.values():
            out[c.kind] += 1
        out[DROPPED] = len(self.dropped)
        return out

    def kind(self, slot: int) -> str | None:
        c = self.slots.get(slot)
        return c.kind if c else None

    def summary(self) -> str:
        n = self.counts()
        if not self.has_source:
            return (
                f"{len(self.slots)} populated slot(s), all UNKNOWN: without the donor moveset "
                "there is nothing to compare a slot against."
            )
        lines = [
            f"{len(self.slots)} populated slot(s): {n[CARRIED]} carried, {n[FILLER]} filler, "
            f"{n[HOST]} host, {n[ALTERED]} altered"
        ]
        if not self.has_host:
            lines.append("  no host pack: a slot still holding the host's clip reads ALTERED")
        if self.dropped:
            lines.append(
                f"  {len(self.dropped)} donor clip(s) DROPPED, no host slot of that index: "
                + ", ".join(map(str, sorted(self.dropped)))
            )
        lines.append(f"  => {n[CARRIED]} scriptable slot(s), a1 == the slot index")
        return "\n".join(lines)


def coverage(
    port: AnimPack, host: AnimPack | None = None, donor: AnimPack | None = None
) -> Coverage:
    """Each slot of a built pack against the donor's moveset (stream 0) and the host's pack, by
    the builder's own fill rule; every slot is UNKNOWN without the donor."""
    table = clip_table(port)
    host_table = clip_table(host) if host is not None else {}
    donor_table = (
        {s: _print(c) for s, c in enumerate(donor.streams[0]) if c is not None}
        if donor is not None
        else {}
    )
    held = slots.correspondence(port, donor) if donor is not None else {}
    cov = Coverage(has_source=donor is not None, has_host=host is not None)
    for slot, fp in sorted(table.items()):
        src = held.get(slot)
        if src is None:
            kind = UNKNOWN
        elif src.match == "same":
            kind = CARRIED
        elif src.match == "fill":
            kind = FILLER
        else:
            kind = HOST if host_table.get(slot) == fp else ALTERED
        cov.slots[slot] = SlotCoverage(slot, kind, fp[0], fp[1], donor_table.get(slot))
    if host is not None:
        cov.dropped = {s: fp for s, fp in donor_table.items() if s not in host_table}
    return cov


# labels


@dataclass(frozen=True)
class LabelTrack:
    """One named clip, checked against the build in front of you."""

    name: str
    slot: int
    status: str
    message: str
    label: str = ""
    now_at: int | None = None
    candidates: tuple[int, ...] = ()
    labelled_build: str | None = None

    @property
    def trusted(self) -> bool:
        return self.status in (CURRENT, STILL_VALID)


def track_labels(
    m: Manifest, table: Mapping[int, Fingerprint], build: str | None = None
) -> list[LabelTrack]:
    """Whether each clip name still points at its clip: the fingerprint looked up in `table`.
    A fingerprint several slots share is AMBIGUOUS, never resolved to the first."""
    out = []
    for name, c in sorted(m.clips.items()):
        lb = c.labelled_build
        if c.frames is None:
            msg = f"no `frames` recorded; whatever is in slot {c.slot} now wears this name."
            out.append(LabelTrack(name, c.slot, UNCHECKABLE, msg, c.label, labelled_build=lb))
            continue
        loop = c.loop
        want = f"{c.frames}f{', loop' if loop else ''}"
        matches = tuple(
            s for s, (f, lp) in sorted(table.items()) if f == c.frames and loop in (None, lp)
        )
        if c.slot in matches:
            same = build is not None and lb == build
            if same:
                why = " — labelled against this very build"
            elif lb:
                why = f" (labelled against {lb})"
            else:
                why = " (no build recorded for the label: this rests on the fingerprint alone)"
            status = CURRENT if same else STILL_VALID
            msg = f"slot {c.slot} still holds a {want} clip{why}"
            out.append(LabelTrack(name, c.slot, status, msg, c.label, labelled_build=lb))
        elif len(matches) == 1:
            msg = (
                f"the {want} clip this name was written for is now at slot {matches[0]}, not "
                f"{c.slot}. Re-point it: a1 IS the slot index."
            )
            out.append(LabelTrack(name, c.slot, MOVED, msg, c.label, matches[0], matches, lb))
        elif matches:
            msg = (
                f"slot {c.slot} does not hold it any more and {len(matches)} slots share its "
                f"fingerprint ({', '.join(map(str, matches))})."
            )
            out.append(LabelTrack(name, c.slot, AMBIGUOUS, msg, c.label, None, matches, lb))
        else:
            msg = f"nothing in this build is {want}: the clip this name describes is not here."
            out.append(LabelTrack(name, c.slot, LOST, msg, c.label, labelled_build=lb))
    return out


def unlabelled_slots(m: Manifest, table: Mapping[int, Fingerprint]) -> list[int]:
    named = {c.slot for c in m.clips.values()}
    return [s for s in sorted(table) if s not in named]


def clip_key(slot: int) -> str:
    """The default name for a slot's clip."""
    return f"clip_{slot:02d}"


def entry(m: Manifest, slot: int) -> tuple[str, Clip] | None:
    """The manifest's clip in `slot`, with its name."""
    return next(((n, c) for n, c in m.clips.items() if c.slot == slot), None)


def _label(
    m: Manifest,
    slot: int,
    name: str,
    fp: Fingerprint,
    label: str | None,
    build: str | None,
    impact_frame: int | None = None,
) -> None:
    found = entry(m, slot)
    if found is None:
        m.clips[name] = Clip(slot)
    elif found[0] != name:
        m.rename_clip(found[0], name)
    c = m.clips[name]
    c.frames, c.loop = fp
    c.labelled_build = build
    if label is not None:
        c.label = label
    if impact_frame is not None:
        c.impact_frame = impact_frame


class LabelSession:
    """Names clips against ONE build (`table`, `build`): the fingerprint and the build id come
    from the pack that is open, never from what the manifest said."""

    def __init__(
        self, doc: PortDocument, table: Mapping[int, Fingerprint], build: str | None = None
    ) -> None:
        self.doc = doc
        self.table = dict(table)
        self.build = build

    def entry(self, slot: int) -> tuple[str, Clip] | None:
        return entry(self.doc.manifest, slot)

    def default_name(self, slot: int) -> str:
        found = self.entry(slot)
        return found[0] if found else clip_key(slot)

    def label(self, slot: int, name: str, label: str = "", impact_frame: int | None = None) -> str:
        """Name `slot`'s clip, renaming its old name and every move that plays it."""
        name = check_name(name)
        clash = self.doc.manifest.clips.get(name)
        if clash is not None and clash.slot != slot:
            raise ManifestError(f"clips.{name} already exists, on slot {clash.slot}")
        fp = self.table.get(slot)
        if fp is None:
            raise ManifestError(f"slot {slot} is not populated in this build")
        self.doc.edit(lambda m: _label(m, slot, name, fp, label, self.build, impact_frame))
        return f"clips.{name} = slot {slot}"

    def bind_move(self, name: str, main: int, sub: int, clip: str | None = None) -> str:
        """Write a `[moves.<name>]`: a host pair and the clip it paints."""
        name = check_name(name)
        if clip is not None and clip not in self.doc.manifest.clips:
            raise ManifestError(f"clip {clip!r} is not named in this manifest yet")

        def bind(m: Manifest) -> None:
            m.moves[name] = Move(main, sub, clip=clip)

        self.doc.edit(bind)
        return f"moves.{name} = ({main},{sub})" + (f" on {clip}" if clip else "")


def import_labels(
    doc: PortDocument,
    labels: Mapping[int, str],
    table: Mapping[int, Fingerprint],
    labelled_build: str,
    only_carried: Coverage | None = None,
    overwrite: bool = False,
) -> list[int]:
    """Fold `N -> text` labels into the manifest as one edit; returns the slots labelled.

    `labelled_build` is where the LABELS came from, never the build at hand. Slots the build
    does not populate are skipped, so are non-CARRIED ones with `only_carried`, and so are
    labelled ones unless `overwrite`."""
    todo = []
    for slot, text in sorted(labels.items()):
        if slot not in table:
            continue
        if only_carried is not None and only_carried.kind(slot) != CARRIED:
            continue
        found = entry(doc.manifest, slot)
        if found is not None and found[1].label and not overwrite:
            continue
        todo.append((slot, found[0] if found else clip_key(slot), text))

    def apply(m: Manifest) -> None:
        for slot, name, text in todo:
            _label(m, slot, name, table[slot], text, labelled_build)

    if todo:
        doc.edit(apply)
    return [slot for slot, _, _ in todo]


# one survey


@dataclass
class Vocabulary:
    coverage: Coverage
    tracks: list[LabelTrack] = field(default_factory=list)
    unlabelled: list[int] = field(default_factory=list)
    build: str | None = None
    notes: list[str] = field(default_factory=list)
    """Why part of the report is missing."""

    @property
    def suspect(self) -> list[LabelTrack]:
        return [t for t in self.tracks if not t.trusted]

    def track(self, slot: int) -> LabelTrack | None:
        return next((t for t in self.tracks if t.slot == slot), None)

    def kind(self, slot: int) -> str | None:
        return self.coverage.kind(slot)


def survey(
    m: Manifest | None,
    table: Mapping[int, Fingerprint],
    cov: Coverage,
    build: str | None = None,
    notes: Iterable[str] = (),
) -> Vocabulary:
    """Coverage and label health for one pack; `m` None for a PAC with no manifest."""
    return Vocabulary(
        cov,
        [] if m is None else track_labels(m, table, build),
        sorted(table) if m is None else unlabelled_slots(m, table),
        build,
        list(notes),
    )


def report(name: str, v: Vocabulary) -> str:
    lines = [f"{name}  {v.build or '(build not identified)'}", v.coverage.summary(), ""]
    if not v.tracks:
        lines.append("no clips are named in this manifest yet")
    else:
        lines.append(
            f"{len(v.tracks)} named clip(s), {len(v.tracks) - len(v.suspect)} trustworthy "
            "against this build"
        )
        lines += [
            f"  {' ' if t.trusted else '!'} {t.name:<14} slot {t.slot:<3} {t.status:<12} "
            f"{t.message}"
            for t in v.tracks
        ]
    worth = [s for s in v.unlabelled if v.kind(s) == CARRIED]
    shown = ", ".join(map(str, worth[:24])) + (" ..." if len(worth) > 24 else "")
    lines += [
        "",
        f"{len(v.unlabelled)} populated slot(s) unnamed, {len(worth)} of them CARRIED: {shown}",
    ]
    lines += [f"note: {n}" for n in v.notes]
    return "\n".join(lines)
