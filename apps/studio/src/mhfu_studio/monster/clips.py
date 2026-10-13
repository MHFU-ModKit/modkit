# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The clip vocabulary: what each executor entry (anim) of a built port holds, and whether a
clip's name still points at the clip it was written for.

A port's entry holds the donor clip its layout (`mhfu_port.layout`) puts there (CARRIED), a copy
of the donor's first clip an older build filled with (FILLER: forcing it plays idle, which looks
exactly like a failed override), the host's clip, or something else; a donor clip the layout
finds no entry for is DROPPED. Entries move between builds, so a label records `(frames, loop)`
and the build it was written against, and `track_labels` looks the fingerprint up in the build
at hand.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from mhfu_port import behaviour, motion, slots
from mhfu_port import layout as layouts
from mhfu_port.manifest import Clip, Manifest, ManifestError, Move
from mhfu_port.model import clip_key
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
MISSING = "MISSING"
"""An anim the build does not populate: what the game asks for is not there."""
#: a kind in words: one, many
KIND_WORDS = {
    CARRIED: ("own clip", "own clips"),
    FILLER: ("idle copy", "idle copies"),
    HOST: ("base monster's", "base monster's"),
    ALTERED: ("changed", "changed"),
    UNKNOWN: ("unknown", "unknown"),
    MISSING: ("missing", "missing"),
}

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
    """MHP3rd clip id -> fingerprint of a donor moveset, every stream."""
    return {cid: fingerprint(c) for cid, c in motion.moveset(moveset).items()}


def fingerprint(clip: anim.Clip) -> Fingerprint:
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
    """The donor clip the layout puts in this entry."""
    clip: int | None = None
    """Its MHP3rd id."""

    @property
    def scriptable(self) -> bool:
        """Forcing this a1 plays the donor's own clip."""
        return self.kind == CARRIED

    def why(self) -> str:
        """In words, for the Clips panel."""
        fp = f"{self.frames} frames" + (", loops" if self.loop else "")
        return {
            CARRIED: f"The original's clip {self.clip}, intact ({fp}).",
            FILLER: f"An idle copy ({fp}). Forcing this anim plays idle, which on screen looks"
            " just like an override that never fired.",
            HOST: f"The base monster's own clip ({fp}): this anim plays its motion on your"
            " skeleton.",
            ALTERED: f"{fp}: matches neither the original's clip {self.clip} nor the base"
            " monster's.",
            UNKNOWN: f"{fp}. No original moveset to compare against.",
        }[self.kind]


@dataclass
class Coverage:
    slots: dict[int, SlotCoverage] = field(default_factory=dict)
    dropped: dict[int, Fingerprint] = field(default_factory=dict)
    """Donor clips the layout finds no entry for."""
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

    def sources(self) -> dict[int, int]:
        """Entry -> the MHP3rd clip the layout puts there."""
        return {s: c.clip for s, c in self.slots.items() if c.clip is not None}

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
                f"  {len(self.dropped)} donor clip(s) DROPPED, no entry left for them: "
                + ", ".join(map(str, sorted(self.dropped)))
            )
        lines.append(f"  => {n[CARRIED]} scriptable slot(s), a1 == the slot index")
        return "\n".join(lines)


def coverage(
    port: AnimPack,
    host: AnimPack | None = None,
    donor: Mapping[int, anim.Clip] | None = None,
    layout: layouts.Layout | None = None,
) -> Coverage:
    """Each entry of a built pack against the donor clip `layout` puts there (by default each
    entry read as the clip of its own id) and the host's pack; every entry is UNKNOWN without
    the donor."""
    table = clip_table(port)
    host_table = clip_table(host) if host is not None else {}
    donor = donor or {}
    if layout is None:
        layout = layouts.Layout({cid: cid for cid in donor if cid in table})
    held = slots.correspondence(port, donor, layout) if donor else {}
    cov = Coverage(has_source=bool(donor), has_host=host is not None)
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
        cid = layout.entries.get(slot)
        fp_src = fingerprint(donor[cid]) if cid is not None and cid in donor else None
        cov.slots[slot] = SlotCoverage(slot, kind, fp[0], fp[1], fp_src, cid)
    if host is not None:
        placed = set(layout.entries.values())
        cov.dropped = {cid: fingerprint(c) for cid, c in donor.items() if cid not in placed}
    return cov


# labels


@dataclass(frozen=True)
class LabelTrack:
    """One named clip, checked against the build in front of you."""

    name: str
    slot: int | None
    """The anim the layout plays it in; None: none."""
    status: str
    message: str
    label: str = ""
    now_at: int | None = None
    candidates: tuple[int, ...] = ()
    labelled_build: str | None = None

    @property
    def trusted(self) -> bool:
        return self.status in (CURRENT, STILL_VALID)


def _ids(sources: Mapping[int, int]) -> dict[int, int]:
    """Entry -> MHP3rd id (`Coverage.sources`, a layout's `entries`) turned around."""
    return {cid: e for e, cid in sources.items()}


def at(c: Clip, sources: Mapping[int, int]) -> int | None:
    """The anim clip `c` plays in: where `sources` (entry -> MHP3rd id) puts its clip, else
    its pin."""
    return layouts.where(c, _ids(sources))


def track_labels(
    m: Manifest,
    table: Mapping[int, Fingerprint],
    build: str | None = None,
    sources: Mapping[int, int] | None = None,
) -> list[LabelTrack]:
    """Whether each clip name still points at its clip: the fingerprint looked up in `table`
    at the anim `sources` (entry -> MHP3rd id) or its pin puts it in. A fingerprint several
    slots share is AMBIGUOUS, never resolved to the first."""
    ids = _ids(sources or {})
    out = []
    for name, c in sorted(m.clips.items()):
        lb = c.labelled_build
        slot = layouts.where(c, ids)
        if slot is None:
            why = "the layout leaves its clip out" if ids else "no layout says where it plays"
            msg = f"clip {c.id} is in no anim: {why}."
            status = LOST if ids else UNCHECKABLE
            out.append(LabelTrack(name, None, status, msg, c.label, labelled_build=lb))
            continue
        if c.frames is None:
            msg = f"no `frames` recorded; whatever is in anim {slot} now wears this name."
            out.append(LabelTrack(name, slot, UNCHECKABLE, msg, c.label, labelled_build=lb))
            continue
        loop = c.loop
        want = f"{c.frames}f{', loop' if loop else ''}"
        matches = tuple(
            s for s, (f, lp) in sorted(table.items()) if f == c.frames and loop in (None, lp)
        )
        if slot in matches:
            same = build is not None and lb == build
            if same:
                why = " — labelled against this very build"
            elif lb:
                why = f" (labelled against {lb})"
            else:
                why = " (no build recorded for the label: this rests on the fingerprint alone)"
            status = CURRENT if same else STILL_VALID
            msg = f"anim {slot} still holds a {want} clip{why}"
            out.append(LabelTrack(name, slot, status, msg, c.label, labelled_build=lb))
        elif len(matches) == 1:
            msg = (
                f"the {want} clip this name was written for is now anim {matches[0]}, not "
                f"{slot}. Name it again there."
            )
            out.append(LabelTrack(name, slot, MOVED, msg, c.label, matches[0], matches, lb))
        elif matches:
            msg = (
                f"anim {slot} does not hold it any more and {len(matches)} anims match its "
                f"length and loop ({', '.join(map(str, matches))})."
            )
            out.append(LabelTrack(name, slot, AMBIGUOUS, msg, c.label, None, matches, lb))
        else:
            msg = f"nothing in this build is {want}: the clip this name describes is not here."
            out.append(LabelTrack(name, slot, LOST, msg, c.label, labelled_build=lb))
    return out


def unlabelled_slots(
    m: Manifest, table: Mapping[int, Fingerprint], sources: Mapping[int, int] | None = None
) -> list[int]:
    ids = _ids(sources or {})
    named = {layouts.where(c, ids) for c in m.clips.values()}
    return [s for s in sorted(table) if s not in named]


def entry(
    m: Manifest, slot: int, sources: Mapping[int, int] | None = None
) -> tuple[str, Clip] | None:
    """The manifest's clip in anim `slot`, with its name: the one naming the clip `sources`
    (entry -> MHP3rd id) puts there, else the one pinned there."""
    cid = (sources or {}).get(slot)
    if cid is not None:
        return next(((n, c) for n, c in m.clips.items() if c.id == cid), None)
    return next(((n, c) for n, c in m.clips.items() if c.slot == slot), None)


def _label(
    m: Manifest,
    slot: int,
    name: str,
    fp: Fingerprint,
    label: str | None,
    build: str | None,
    impact_frame: int | None = None,
    source: int | None = None,
    turn: float | None = None,
) -> None:
    """Names the clip in `slot`: MHP3rd clip `source` without pinning it; without a `source`,
    whatever is in `slot`, pinned there, all a build alone says."""
    if source is not None:
        layouts.name_clip(m, name, source)
    else:
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
    if turn is not None:
        c.turn = turn


class LabelSession:
    """Names clips against ONE build (`table`, `build`): the fingerprint and the build id come
    from the pack that is open, never from what the manifest said. With `sources` (entry ->
    MHP3rd clip, `Coverage.sources`), a name is the clip's, which the packer keeps placing;
    without, it pins the anim."""

    def __init__(
        self,
        doc: PortDocument,
        table: Mapping[int, Fingerprint],
        build: str | None = None,
        sources: Mapping[int, int] | None = None,
    ) -> None:
        self.doc = doc
        self.table = dict(table)
        self.build = build
        self.sources = dict(sources or {})

    def entry(self, slot: int) -> tuple[str, Clip] | None:
        return entry(self.doc.manifest, slot, self.sources)

    def default_name(self, slot: int) -> str:
        found = self.entry(slot)
        return found[0] if found else clip_key(slot)

    def label(
        self,
        slot: int,
        name: str,
        label: str = "",
        impact_frame: int | None = None,
        turn: float | None = None,
    ) -> str:
        """Name `slot`'s clip, renaming its old name and every move that plays it."""
        name = check_name(name)
        fp = self._fingerprint(slot, name)
        src = self.sources.get(slot)
        build = self.build
        self.doc.edit(lambda m: _label(m, slot, name, fp, label, build, impact_frame, src, turn))
        return f"clips.{name} = slot {slot}"

    def _fingerprint(self, slot: int, name: str) -> Fingerprint:
        """`slot`'s fingerprint, refusing a slot this build lacks, one holding none of the
        original's clips, or a name another slot has."""
        clash = self.doc.manifest.clips.get(name)
        if clash is not None and (there := at(clash, self.sources)) != slot:
            raise ManifestError(f"clips.{name} already exists, on anim {there}")
        fp = self.table.get(slot)
        if fp is None:
            raise ManifestError(f"slot {slot} is not populated in this build")
        if self.sources and slot not in self.sources and self.entry(slot) is None:
            raise ManifestError(f"anim {slot} holds none of the original's clips")
        return fp

    def _named(self, slot: int) -> tuple[str, Callable[[Manifest], None]]:
        """`slot`'s clip name (`clip_key` when it has none) and the edit that names it."""
        found = self.entry(slot)
        if found is not None:
            return found[0], lambda m: None
        name = clip_key(slot)
        fp, src, build = self._fingerprint(slot, name), self.sources.get(slot), self.build
        return name, lambda m: _label(m, slot, name, fp, None, build, source=src)

    def bind_move(self, name: str, main: int, sub: int, slot: int | None = None) -> str:
        """`[moves.<name>]` on the pair, painting `slot`'s clip (named `clip_key` when it has no
        name). A move of that name keeps its other fields; its clip replaces any raw `anim`."""
        name = check_name(name)
        clip, named = self._named(slot) if slot is not None else (None, None)

        def bind(m: Manifest) -> None:
            if named is not None:
                named(m)
            mv = m.moves.get(name)
            if mv is None:
                m.moves[name] = Move(main, sub, clip=clip)
                return
            mv.main, mv.sub = main, sub
            if clip is not None:
                mv.clip, mv.anim = clip, None

        self.doc.edit(bind)
        return f"moves.{name} = ({main},{sub})" + (f" on {clip}" if clip else "")

    def own_move(self, name: str, slot: int) -> str:
        """A new own move `[moves.<name>]` playing `slot`'s clip, named `clip_key` when it has
        no name."""
        name = check_name(name)
        if name in self.doc.manifest.moves:
            raise ManifestError(f"moves.{name} already exists")
        clip, named = self._named(slot)

        def make(m: Manifest) -> None:
            named(m)
            m.moves[name] = Move(clip=clip)

        self.doc.edit(make)
        return f"moves.{name}: an own move on {clip}"

    def unbind_move(self, name: str) -> str:
        return drop_move(self.doc, name)

    def rename_move(self, old: str, new: str) -> str:
        return rename_move(self.doc, old, new)


def drop_move(doc: PortDocument, name: str) -> str:
    """Drops `[moves.<name>]`; refused while another move, a block or an effect names it."""
    if name not in doc.manifest.moves:
        raise ManifestError(f"no move {name!r}")
    users = move_users(doc.manifest, name)
    if users:
        raise ManifestError(f"{name} is still used by {', '.join(users)}: change that first")

    def drop(m: Manifest) -> None:
        del m.moves[name]
        behaviour.drop_move(m, name)

    doc.edit(drop)
    return f"moves.{name} removed"


def rename_move(doc: PortDocument, old: str, new: str) -> str:
    """Renames a move and every `after`, block and effect that names it."""
    new = check_name(new)
    if old not in doc.manifest.moves:
        raise ManifestError(f"no move {old!r}")
    if new == old:
        return f"moves.{old}"
    if new in doc.manifest.moves:
        raise ManifestError(f"moves.{new} already exists")

    def rename(m: Manifest) -> None:
        m.moves = {new if k == old else k: mv for k, mv in m.moves.items()}
        for mv in m.moves.values():
            mv.after = new if mv.after == old else mv.after
        behaviour.rename_move(m, old, new)
        for e in m.effects:
            e.move = new if e.move == old else e.move

    doc.edit(rename)
    return f"moves.{old} is now moves.{new}"


def move_users(m: Manifest, name: str) -> list[str]:
    """What names move `name`: other moves' `after`, the behaviour graph and effects."""
    out = [f"moves.{k} (after)" for k, mv in m.moves.items() if mv.after == name]
    out += behaviour.uses(m, name)
    out += [f"effect {e.id}" for e in m.effects if e.move == name]
    return out


def import_labels(
    doc: PortDocument,
    labels: Mapping[int, str],
    table: Mapping[int, Fingerprint],
    labelled_build: str,
    only_carried: Coverage | None = None,
    overwrite: bool = False,
    sources: Mapping[int, int] | None = None,
) -> list[int]:
    """Fold `N -> text` labels into the manifest as one edit; returns the slots labelled.

    `labelled_build` is where the LABELS came from, never the build at hand. Slots the build
    does not populate are skipped, so are non-CARRIED ones with `only_carried`, and so are
    labelled ones unless `overwrite`. A new name places the clip `sources` says the entry
    holds."""
    todo = []
    for slot, text in sorted(labels.items()):
        if slot not in table:
            continue
        if only_carried is not None and only_carried.kind(slot) != CARRIED:
            continue
        found = entry(doc.manifest, slot, sources)
        if found is not None and found[1].label and not overwrite:
            continue
        todo.append((slot, found[0] if found else clip_key(slot), text))

    def apply(m: Manifest) -> None:
        for slot, name, text in todo:
            src = (sources or {}).get(slot)
            _label(m, slot, name, table[slot], text, labelled_build, source=src)

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
        [] if m is None else track_labels(m, table, build, cov.sources()),
        sorted(table) if m is None else unlabelled_slots(m, table, cov.sources()),
        build,
        list(notes),
    )


def _or_dash(n: int | None) -> str:
    return "-" if n is None else str(n)


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
            f"  {' ' if t.trusted else '!'} {t.name:<14} slot {_or_dash(t.slot):<3} "
            f"{t.status:<12} {t.message}"
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
