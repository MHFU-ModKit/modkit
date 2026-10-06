# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The engine's rules for a monster's model, skeleton and animation, as checked predicates: what
an edit must keep for the game to load and play the result.

Rules an mhp-formats encoder enforces are not repeated here: a clip's track count and a track's
channel mask are written from the lists, keyframe words that do not fit s16 and channel bits past
12 refuse to encode, groups are a list in draw order, and a group's weight count cannot pass the
GE's eight.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from mhfu.entries import PART_STREAMS
from mhp_formats import fu, pmo
from mhp_formats.anim import CHANNEL_BITS
from mhp_formats.skeleton import Skeleton

Level = Literal["error", "warn"]

RULES: dict[str, tuple[Level, str, str]] = {
    "PARENT_ORDER": ("error", "every parent precedes its child", "renumber the bones"),
    "ANIMATED_COUNT": (
        "error",
        "the skeleton's animated count fits it",
        "params[1] is at most the bone count",
    ),
    "STREAM_RUNS": (
        "error",
        "the animated joints' streams form contiguous runs",
        "reorder the bones so each stream is one run",
    ),
    "STREAM_SUBTREE": (
        "error",
        "each stream past the first is one subtree",
        "a part hangs off exactly one joint and holds no child of an earlier part",
    ),
    "PARTITION": (
        "error",
        "each animation stream carries its part's joints",
        "stream s plays part s // 2: one track per joint of that part, in joint order",
    ),
    "NO_TRACKS": ("error", "every clip has tracks", "give the clip one track per joint"),
    "SCALE_CHANNEL": (
        "error",
        "no scale channels",
        "drop them: the engine crashes on the scale bits",
    ),
    "DUPLICATE_CHANNEL": (
        "error",
        "no channel twice in a track",
        "one channel per bit; the mask holds one bit each",
    ),
    "NEGATIVE_FRAME": ("error", "keyframes at frame 0 or later", "shift the keys"),
    "FRAME_ORDER": ("warn", "keyframes in frame order", "sort the keys by frame"),
    "EMPTY_CHANNEL": ("warn", "no channel without keyframes", "remove it or key it"),
    "PALETTE_JOINT": (
        "error",
        "every skinned joint exists",
        "a group's palette names skeleton bones",
    ),
    "EMPTY_GROUP": ("warn", "no group without vertices", "an empty group still binds a slot"),
    "TEMPLATE": (
        "error",
        "the skeleton keeps its species' bone tree",
        "the species' overlay and AI assume its bone count and hierarchy",
    ),
}
"""Code -> (level, what holds, how to fix a breach)."""

_SCALE = frozenset(bit for bit, (kind, _) in CHANNEL_BITS.items() if kind == "scl")


@dataclass(frozen=True)
class Result:
    code: str
    message: str

    @property
    def level(self) -> Level:
        return RULES[self.code][0]

    @property
    def fix(self) -> str:
        return RULES[self.code][2]

    def __str__(self) -> str:
        return f"{self.level} {self.code}: {self.message} (fix: {self.fix})"


class ConstraintError(ValueError):
    def __init__(self, errors: Sequence[Result]) -> None:
        super().__init__("; ".join(f"{r.code}: {r.message}" for r in errors))
        self.errors = list(errors)


def validate(
    model: pmo.Pmo | None = None,
    skeleton: Skeleton | None = None,
    anim: fu.Anim | None = None,
    template: Skeleton | None = None,
) -> list[Result]:
    """Every breach of `RULES` by what is given; a rule needing a missing part is skipped.
    `template` is a skeleton of the species the result rides."""
    out: list[Result] = []
    if skeleton is not None:
        out += _skeleton(skeleton)
        if template is not None:
            out += _template(skeleton, template)
    if anim is not None:
        out += _anim(anim)
        if skeleton is not None and 0 < animated(skeleton) <= len(skeleton.bones):
            out += _partition(anim, skeleton)
    if model is not None:
        out += _model(model, skeleton)
    return out


def check(
    model: pmo.Pmo | None = None,
    skeleton: Skeleton | None = None,
    anim: fu.Anim | None = None,
    template: Skeleton | None = None,
) -> list[Result]:
    """`validate`, raising ConstraintError on any error; returns the warnings."""
    results = validate(model, skeleton, anim, template)
    errors = [r for r in results if r.level == "error"]
    if errors:
        raise ConstraintError(errors)
    return results


def animated(skeleton: Skeleton) -> int:
    """The joints the animation drives: `params[1]` where the skeleton declares it, else all."""
    return skeleton.params[1] if len(skeleton.params) > 1 else len(skeleton.bones)


def runs(skeleton: Skeleton) -> list[tuple[int, int]]:
    """`(stream, joints)` over the animated joints, in joint order."""
    out: list[tuple[int, int]] = []
    for bone in skeleton.bones[: animated(skeleton)]:
        if out and out[-1][0] == bone.stream:
            out[-1] = (bone.stream, out[-1][1] + 1)
        else:
            out.append((bone.stream, 1))
    return out


def _skeleton(skeleton: Skeleton) -> list[Result]:
    out = []
    parents = [b.parent for b in skeleton.bones]
    late = [i for i, p in enumerate(parents) if p >= i]
    if late:
        out.append(Result("PARENT_ORDER", f"bones {_few(late)}"))
    n = animated(skeleton)
    if not 0 < n <= len(parents):
        out.append(Result("ANIMATED_COUNT", f"{n} animated of {len(parents)} bones"))
        return out
    streams = runs(skeleton)
    if len({s for s, _ in streams}) != len(streams):
        out.append(Result("STREAM_RUNS", " ".join(f"{s}:{c}" for s, c in streams)))
    kids: dict[int, list[int]] = {}
    for i, p in enumerate(parents):
        kids.setdefault(p, []).append(i)
    start = 0
    for k, (sid, count) in enumerate(streams):
        span = range(start, start + count)
        roots = [i for i in span if parents[i] >= 0 and parents[i] not in span]
        if k and len(roots) > 1:
            out.append(Result("STREAM_SUBTREE", f"stream {sid} hangs off {_few(roots)}"))
        behind = [c for i in span for c in kids.get(i, []) if c < start]
        if behind:
            out.append(Result("STREAM_SUBTREE", f"stream {sid} holds children {_few(behind)}"))
        start += count
    return out


def _template(skeleton: Skeleton, template: Skeleton) -> list[Result]:
    have = [b.parent for b in skeleton.bones]
    want = [b.parent for b in template.bones]
    if len(have) != len(want):
        return [Result("TEMPLATE", f"{len(have)} bones, the species {len(want)}")]
    moved = [i for i, (p, q) in enumerate(zip(have, want, strict=True)) if p != q]
    return [Result("TEMPLATE", f"parents differ at bones {_few(moved)}")] if moved else []


def _partition(anim: fu.Anim, skeleton: Skeleton) -> list[Result]:
    """Stream s plays part s // PART_STREAMS; a part's clips carry one track per joint of it,
    and every part has clips, so the parts together cover exactly the animated joints."""
    part = Counter(b.stream for b in skeleton.bones[: animated(skeleton)])
    out = []
    for si, stream in enumerate(anim.streams):
        widths = sorted({len(c.tracks) for c in stream if c is not None and c.tracks})
        want = part.get(si // PART_STREAMS, 0)
        if widths and widths != [want]:
            out.append(Result("PARTITION", f"stream {si} carries {widths} tracks, its part {want}"))
    for k, joints in sorted(part.items()):
        si = PART_STREAMS * k
        if si >= len(anim.streams) or all(c is None for c in anim.streams[si]):
            out.append(Result("PARTITION", f"part {k} has {joints} joints and no clips"))
    return out


def _anim(anim: fu.Anim) -> list[Result]:
    out = []
    seen: set[int] = set()
    for si, stream in enumerate(anim.streams):
        for slot, clip in enumerate(stream):
            if clip is None or id(clip) in seen:
                continue
            seen.add(id(clip))
            where = f"stream {si} slot {slot}"
            if not clip.tracks:
                out.append(Result("NO_TRACKS", where))
            for j, track in enumerate(clip.tracks):
                bits = [c.bit for c in track.channels]
                if _SCALE & set(bits):
                    out.append(Result("SCALE_CHANNEL", f"{where} track {j}"))
                if len(set(bits)) != len(bits):
                    out.append(Result("DUPLICATE_CHANNEL", f"{where} track {j}"))
                for c in track.channels:
                    frames = [k.frame for k in c.keyframes]
                    if not frames:
                        out.append(Result("EMPTY_CHANNEL", f"{where} track {j} bit {c.bit:#x}"))
                    elif min(frames) < 0:
                        out.append(Result("NEGATIVE_FRAME", f"{where} track {j} bit {c.bit:#x}"))
                    elif frames != sorted(frames):
                        out.append(Result("FRAME_ORDER", f"{where} track {j} bit {c.bit:#x}"))
    return out


def _model(model: pmo.Pmo, skeleton: Skeleton | None) -> list[Result]:
    out = []
    groups = model.groups()
    empty = [g for g, group in enumerate(groups) if not group.block.vertices.position]
    if empty:
        out.append(Result("EMPTY_GROUP", f"groups {_few(empty)}"))
    if skeleton is not None:
        n = len(skeleton.bones)
        past = sorted({b for g in range(len(groups)) for b in model.palette(g) if b >= n})
        if past:
            out.append(Result("PALETTE_JOINT", f"joints {_few(past)} of {n}"))
    return out


def _few(items: Sequence[int], limit: int = 8) -> str:
    shown = " ".join(map(str, items[:limit]))
    return shown + (f" (+{len(items) - limit})" if len(items) > limit else "")
