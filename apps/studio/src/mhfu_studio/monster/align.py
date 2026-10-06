# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The host action's expectations and the port's clip on one frame axis.

A move pairs a host behaviour pair, which owns the hitbox, damage and effects, with a clip, which
only paints the animation; it works when the clip's contact lands where the handler looks. Four
facts decide that: the cursor frames the handler tests (a gate past the clip's end never runs),
what ends the action (the clip, or the frame budget), the effects it spawns at the HOST's bone
numbers, and the measured dwell (a pair the census saw never entered survives one tick).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from mhfu.em.intel import PairIntel, SpeciesIntel
from mhfu_port import layout
from mhfu_port.manifest import Manifest

from mhfu_studio.monster import species
from mhfu_studio.shell.findings import Finding, Level

GATE = "gate"
WINDOW = "window"
EFFECT = "effect"
OURS = "ours"
IMPACT = "impact"
"""Marker kinds, in the order a timeline stacks them."""

IMPACT_TOLERANCE = 2.0
"""Frames an impact may sit off a tested frame: within one dispatch at speeds 2.0 to 2.4."""


@dataclass
class PortRig:
    """The skeleton the port ships: `driven` joints some clip animates, `vertices` per joint."""

    n_bones: int
    driven: set[int] | None = None
    vertices: dict[int, int] = field(default_factory=dict)

    def describe(self, bone: int) -> str:
        if not 0 <= bone < self.n_bones:
            return f"joint {bone} does not exist on this rig ({self.n_bones} joints)"
        n = self.vertices.get(bone, 0)
        bits = [f"joint {bone}", f"{n} vertices" if n else "no geometry"]
        if self.driven is not None:
            bits.append("driven" if bone in self.driven else "NOT ANIMATED")
        return ", ".join(bits)


@dataclass
class Marker:
    """A frame on the timeline, in the clip's own frames; `unreachable` past the clip's end."""

    frame: float
    kind: str
    label: str
    detail: str = ""
    unreachable: bool = False


@dataclass
class Alignment:
    move: str
    main: int
    sub: int
    clip: str | None = None
    slot: int | None = None
    frames: int | None = None
    """The clip's last keyframe, from the build when one was given."""
    impact: float | None = None
    markers: list[Marker] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    headline: str = ""
    pair: PairIntel | None = None

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def gates(self) -> list[float]:
        """Every frame the handler names, of either kind."""
        return sorted({m.frame for m in self.markers if m.kind in (GATE, WINDOW)})

    def marker_map(self) -> dict[float, str]:
        out: dict[float, str] = {}
        for m in self.markers:
            out[m.frame] = f"{out[m.frame]} {m.label}" if m.frame in out else m.label
        return out

    def report(self) -> str:
        clip = f"  clip {self.clip}" if self.clip else ""
        lines = [f"{self.move} -> ({self.main},{self.sub}){clip}", f"  {self.headline}"]
        return "\n".join(lines + [f"  {f}" for f in self.findings])

    def _add(self, level: Level, code: str, message: str) -> None:
        self.findings.append(Finding(level, code, message, self.move))


def align(
    m: Manifest,
    move: str,
    intel: SpeciesIntel | None = None,
    clip_frames: int | None = None,
    rig: PortRig | None = None,
    ids: Mapping[int, int] | None = None,
) -> Alignment:
    """What the host pair of `move` expects, against its clip in the anim `ids` (MHP3rd id ->
    anim, a layout's) or its pin gives it; `clip_frames` from the build wins over the
    manifest's `frames`, since a rebuild moves clips."""
    mv = m.moves.get(move)
    if mv is None:
        raise KeyError(f"no move named {move!r} (have: {', '.join(sorted(m.moves)) or 'none'})")
    c = m.clips.get(mv.clip) if mv.clip else None
    return align_pair(
        m,
        mv.main,
        mv.sub,
        intel,
        move=move,
        clip=mv.clip,
        slot=layout.where(c, ids or {}) if c else mv.anim,
        clip_frames=clip_frames if clip_frames is not None else (c.frames if c else None),
        impact=c.impact_frame if c else None,
        allow_unentered=mv.allow_unentered,
        rig=rig,
    )


def align_pair(
    m: Manifest,
    main: int,
    sub: int,
    intel: SpeciesIntel | None = None,
    move: str | None = None,
    clip: str | None = None,
    slot: int | None = None,
    clip_frames: int | None = None,
    impact: float | None = None,
    allow_unentered: bool = False,
    rig: PortRig | None = None,
) -> Alignment:
    """The same for a pair not bound yet: choosing the pair is the authoring act."""
    a = Alignment(move or f"({main},{sub})", main, sub, clip, slot, clip_frames, impact)
    if intel is None:
        a.headline = (
            f"no action data for {species.label(m.port.host_species)}, so nothing is known"
            f" about ({main},{sub})."
        )
        a._add("warning", "INTEL_ABSENT", a.headline)
        _impact_marker(a)
        return a
    a.pair = intel.pair(main, sub)
    _census(a, intel, allow_unentered)
    if a.pair is None:
        a.headline = (
            f"({main},{sub}) is in neither the overlay's jump tables nor the census, so there "
            "is nothing to check the clip against."
        )
        _impact_marker(a)
        _our_effects(a, m, move, rig)
        return a
    _gates(a, a.pair)
    _ends_on(a, a.pair)
    _effects(a, a.pair, rig)
    _our_effects(a, m, move, rig)
    _impact_marker(a)
    a.headline = _headline(a)
    return a


def align_all(
    m: Manifest,
    intel: SpeciesIntel | None = None,
    clip_frames: Mapping[int, int] | None = None,
    rig: PortRig | None = None,
    ids: Mapping[int, int] | None = None,
) -> list[Alignment]:
    """Every move; `clip_frames` maps slot to last keyframe, `ids` as `align`."""
    out = []
    for name, mv in sorted(m.moves.items()):
        c = m.clips.get(mv.clip) if mv.clip else None
        at = None if c is None else layout.where(c, ids or {})
        end = None if clip_frames is None or at is None else clip_frames.get(at)
        out.append(align(m, name, intel, end, rig, ids))
    return out


def _census(a: Alignment, intel: SpeciesIntel, allow_unentered: bool) -> None:
    b = intel.bindable(a.main, a.sub, override=allow_unentered)
    p = a.pair
    if p is not None and p.never_entered:
        a._add(
            "warning" if allow_unentered else "error",
            "NEVER_ENTERED",
            f"the game was watched and never enters ({a.main},{a.sub}): its code waits for "
            "something nothing set up and returns at once, so forced, the move lasts one tick "
            "and the clip restarts from frame 0 twice a second. 411 of 411 did."
            + ("  (allow_unentered is set, so this is your call.)" if allow_unentered else ""),
        )
        return
    if p is not None and p.measured:
        travel = "" if p.move_per_tick is None else f", travelling {p.move_per_tick:.0f} u/tick"
        a._add(
            "info",
            "DWELL",
            f"the game entered ({a.main},{a.sub}) {p.entered} time(s) on its own and stayed "
            f"{p.dwell_ticks:.1f} tick(s) ({p.dwell_ticks / 2:.1f} s at 2 Hz){travel}",
        )
        if b.code == "SHORT_DWELL":
            a._add("warning", "SHORT_DWELL", b.reason)
        return
    if b.code == "NO_HANDLER" and not b.ok:
        a._add("error", "NO_HANDLER", b.reason)
    why = f" ({intel.census_reason})" if intel.census_reason else ""
    a._add(
        "warning",
        "UNMEASURED",
        f"({a.main},{a.sub}) was never measured in the game{why}, so whether the game ever "
        "enters it is unknown, not never. Everything here is read from the code.",
    )


def _gates(a: Alignment, p: PairIntel) -> None:
    for frames, kind, what in (
        (p.fixed_event_frames, GATE, "hit check: the code waits for frame {:g}"),
        (p.fixed_window_frames, WINDOW, "a timing window opens or closes at {:g}"),
    ):
        for f in sorted(set(frames)):
            past = a.frames is not None and f > a.frames
            a.markers.append(Marker(float(f), kind, f"{f:g}", what.format(f), past))
    if p.handler is None:
        a._add(
            "warning",
            "INLINE_CASE",
            f"the dispatcher's case for ({p.main},{p.sub}) runs inline and calls no handler, so "
            "nothing offline can say what frames it tests.",
        )
        return
    if not a.gates:
        a._add(
            "info",
            "NO_FIXED_FRAMES",
            "this handler tests no fixed frame numbers, so the clip's internal timing is yours.",
        )
        return
    late = sorted(mk.frame for mk in a.markers if mk.unreachable)
    if late:
        s = "s" if len(late) > 1 else ""
        a._add(
            "error",
            "GATE_BEYOND_CLIP",
            f"the handler tests frame{s} {', '.join(f'{f:g}' for f in late)} but the clip ends at "
            f"{a.frames}: the cursor never gets there, so "
            + ("those branches never run" if s else "that branch never runs")
            + ". A ported clip may be any length, but not shorter than the frames the handler "
            "names.",
        )


def _ends_on(a: Alignment, p: PairIntel) -> None:
    if p.ends_on_clip:
        a._add(
            "info",
            "ENDS_ON_CLIP",
            "the action lasts as long as the clip does, so its total length is free; only the "
            "frames above are fixed.",
        )
        return
    if p.budget.gated:
        seeds = p.budget.phase0_seeds
        who = (
            "a slot-32 POST-hook can own the budget (phase 0 only consumes it)"
            if p.budget.post_hook_owns
            else "the handler RE-SEEDS the budget in its phase-0 block"
            + (f" with {', '.join(map(str, seeds))}" if seeds else "")
            + ", so a post-hook is overwritten one frame later"
        )
        a._add(
            "warning",
            "ENDS_ON_BUDGET",
            "this action ends on the frame BUDGET, not on the clip: a longer clip is cut off "
            "mid-play and a shorter one leaves the monster in the action after the animation "
            f"stops. The budget is settable: {who}.",
        )
        return
    end = p.ends_on or "unknown"
    a._add(
        "warning",
        "ENDS_ON_" + end.upper().replace("+", "_"),
        f"what ends this action reads as {end!r} rather than clip-done, so the clip's length is "
        "not simply free here.",
    )


def _effects(a: Alignment, p: PairIntel, rig: PortRig | None) -> None:
    if not p.effects:
        return
    unframed = sum(e.frame is None for e in p.effects)
    a._add(
        "info",
        "HOST_EFFECTS",
        f"the host handler spawns {', '.join(map(str, p.effects))}. "
        + (
            f"{unframed} of them name no frame, so WHEN it fires is not decidable offline."
            if unframed
            else "Every one names its frame."
        ),
    )
    for e in p.effects:
        if e.frame is None:
            continue
        past = a.frames is not None and e.frame > a.frames
        detail = f"the base monster spawns effect {e.id} at its joint {e.bone} on frame {e.frame}"
        a.markers.append(Marker(float(e.frame), EFFECT, f"fx{e.id}", detail, past))
        if past:
            a._add(
                "warning",
                "EFFECT_BEYOND_CLIP",
                f"effect {e.id} fires at frame {e.frame}, past this clip's last frame "
                f"({a.frames}): it never spawns.",
            )
    bones = sorted({e.bone for e in p.effects if e.bone is not None})
    if rig is not None:
        for bone in bones:
            ids = ", ".join(str(e.id) for e in p.effects if e.bone == bone)
            if not 0 <= bone < rig.n_bones:
                a._add(
                    "error",
                    "EFFECT_BONE_RANGE",
                    f"the host anchors effect(s) {ids} to its bone {bone}, and this port's rig "
                    f"has only {rig.n_bones} joints.",
                )
            elif rig.driven is not None and bone not in rig.driven:
                a._add(
                    "warning",
                    "EFFECT_BONE_UNDRIVEN",
                    f"bone {bone}, where the host anchors effect(s) {ids}, is driven by NO clip "
                    "on this rig: the effect would not follow the animal.",
                )
    if bones:
        where = "" if rig is None else "  On YOUR rig: " + "; ".join(map(rig.describe, bones)) + "."
        a._add(
            "warning",
            "EFFECT_BONES_ARE_THE_HOSTS",
            f"bone {', '.join(map(str, bones))} {'is' if len(bones) == 1 else 'are'} the HOST "
            "species' own: the same number on the port's rig lands somewhere else. Watch the "
            f"joint in the viewport and write the port's own recipe into [[effect]].{where}",
        )


def _our_effects(a: Alignment, m: Manifest, move: str | None, rig: PortRig | None) -> None:
    for i, e in enumerate(m.effects):
        if move is None or e.move != move:
            continue
        past = a.frames is not None and e.frame > a.frames
        on = "" if rig is None else f" ({rig.describe(e.bone)})"
        detail = f"your port spawns effect {e.id} at its joint {e.bone} on frame {e.frame}{on}"
        a.markers.append(Marker(float(e.frame), OURS, f"fx{e.id}*", detail, past))
        if past:
            a._add(
                "warning",
                "OUR_EFFECT_BEYOND_CLIP",
                f"effect[{i}] fires at frame {e.frame}, past the clip's last frame ({a.frames}).",
            )
        if rig is not None and not 0 <= e.bone < rig.n_bones:
            a._add(
                "error",
                "OUR_EFFECT_BONE_RANGE",
                f"effect[{i}] names bone {e.bone}; this rig has {rig.n_bones} joints.",
            )


def _impact_marker(a: Alignment) -> None:
    if a.impact is not None:
        detail = f"where your clip's own hit lands (clips.{a.clip}.impact_frame)"
        a.markers.append(Marker(float(a.impact), IMPACT, "impact", detail))


def _gate_list(gates: Sequence[float], cap: int = 6) -> str:
    return ", ".join(f"{f:g}" for f in gates[:cap]) + (" ..." if len(gates) > cap else "")


def nearest(gates: Sequence[float], impact: float) -> tuple[float, float]:
    """The checked frame closest to `impact`, and how far after it the impact lands."""
    near = min(gates, key=lambda f: abs(f - impact))
    return near, impact - near


@dataclass(frozen=True)
class Timing:
    """An impact against the checked frames, in a table cell's words."""

    text: str
    level: Level | None = None
    detail: str = ""


def timing(gates: Sequence[float], impact: float | None, frames: int | None) -> Timing:
    """`on time`, `6 late`, `checks 40, 70`; a check past the clip's last frame is the error."""
    if not gates:
        return Timing("–", None, "The code checks no fixed frame: the clip's timing is yours.")
    late = [f for f in gates if frames is not None and f > frames]
    if late:
        return Timing(
            "clip too short",
            "error",
            f"The code checks frame {_gate_list(late)}, past the clip's last frame ({frames}):"
            " that branch never runs. Use a longer clip.",
        )
    if impact is None:
        return Timing(
            f"check {gates[0]:g}" + ("…" if len(gates) > 1 else ""),
            None,
            f"The code checks frame {_gate_list(gates)}. No impact frame yet: scrub to where the"
            " clip hits and Set impact in Timeline.",
        )
    near, gap = nearest(gates, impact)
    if abs(gap) <= IMPACT_TOLERANCE:
        return Timing("on time", None, f"The impact ({impact:g}) is on the {near:g} check.")
    word = "late" if gap > 0 else "early"
    return Timing(
        f"{abs(gap):.0f} {word}",
        "warning",
        f"The impact ({impact:g}) lands {abs(gap):.0f} frames {word} for the {near:g} check: the"
        " hit and the animation part company by that much.",
    )


def _headline(a: Alignment) -> str:
    """The handler's frames against the clip's impact, in one sentence."""
    gates = a.gates
    s = "s" if len(gates) > 1 else ""
    if a.impact is None:
        if not gates:
            return f"({a.main},{a.sub}) checks no fixed frames, so only the clip's length matters."
        return (
            f"the base monster's code checks frame{s} {_gate_list(gates)}; your clip has no impact"
            " frame yet: scrub to where it hits, set it in Timeline, and the gap is the answer."
        )
    if not gates:
        return (
            f"your clip's impact is at frame {a.impact:g}, and ({a.main},{a.sub}) checks no fixed"
            " frames: nothing has to line up."
        )
    near, gap = nearest(gates, a.impact)
    head = f"the base monster's code checks frame{s} {_gate_list(gates)}; your clip's impact is"
    head += f" at frame {a.impact:g}"
    if abs(gap) <= IMPACT_TOLERANCE:
        return f"{head}, on the {near:g} check ({gap:+.1f})."
    side = "after" if gap > 0 else "before"
    a._add(
        "warning",
        "IMPACT_OFF_GATE",
        f"the impact lands {abs(gap):.1f} frame(s) {side} the nearest tested frame ({near:g}): "
        "the handler fires at ITS number, so the hit and the animation part company by that much.",
    )
    when = "late" if gap > 0 else "early"
    return f"{head}, {abs(gap):.1f} frame(s) {when} (the nearest check is {near:g})."
