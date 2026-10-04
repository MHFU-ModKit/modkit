# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The checks on a port manifest that need evidence: the built PAC and the base monster's intel.

The loader refuses anything structural. What is left: an anim the build does not hold (the
builder drops an original clip with no base monster's slot, and fills a third of the slots with
idle), a move on an action the census saw never entered (it lasts one tick), and joint numbers
off the skeleton the port ships. Missing evidence is said, never passed silently; a standing
condition nobody fixes in a port (no census, a guessed attack table) is info, so the problem
count holds only problems. The damage grid and the hit groups being shared with a native base
monster is said where they are edited, not here.

A finding names the control that fixes it (`FOCUS`, a key the Clips, Parts and Hitboxes panels
land on), or says what to do (`FIX`).
"""

from __future__ import annotations

from collections.abc import Sequence

from mhfu.em.intel import MIN_DWELL_TICKS, Handoff, SpeciesIntel
from mhfu_port import build, records
from mhfu_port.manifest import UNLIMITED_DIST, Hitbox, Hurtbox, Manifest
from mhfu_port.records import ANIM, GEO
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Skeleton

from mhfu_studio.monster import clips, species
from mhfu_studio.shell.findings import Finding, Level

#: the controls a finding lands on
PART_NAME, HURT_PART, HURT_JOINT, HURT_RADIUS, HURT_END, HURTBOXES, GRID = (
    "part name",
    "hurtbox part",
    "hurtbox joint",
    "hurtbox radius",
    "hurtbox far end",
    "hurtboxes",
    "damage grid",
)
HIT_JOINT, HIT_RADIUS, HIT_END, HIT_GROUP, ATTACK_STATS = (
    "hitbox joint",
    "hitbox radius",
    "hitbox far end",
    "hit group",
    "attack stats",
)
#: the Clips panel's
CLIP_NAME = "clip name"
FOCUS = {
    "CLIP_SLOT_MISSING": CLIP_NAME,
    "CLIP_FRAMES_MISMATCH": CLIP_NAME,
    "CLIP_LOOP_MISMATCH": CLIP_NAME,
    "CLIP_IS_FILLER": CLIP_NAME,
    "LABEL_UNKEYED": CLIP_NAME,
    "HURTBOX_BONE_RANGE": HURT_JOINT,
    "HURTBOX_RADIUS": HURT_RADIUS,
    "HURTBOX_CAPSULE_NO_END": HURT_END,
    "HURTBOX_NO_PART": HURT_PART,
    "HURTBOX_PART_UNNAMED": PART_NAME,
    "HURTBOX_OVER_CAPACITY": HURTBOXES,
    "PART_INDEX_DUPLICATE": PART_NAME,
    "PARTS_UNNAMED": PART_NAME,
    "HITZONE_ALL_ZERO": GRID,
    "HITBOX_BONE_RANGE": HIT_JOINT,
    "HITBOX_RADIUS": HIT_RADIUS,
    "HITBOX_CAPSULE_NO_END": HIT_END,
    "HITBOX_SET_UNKNOWN": HIT_GROUP,
    "HITBOX_OVER_CAPACITY": HIT_GROUP,
    "HITBOX_SET_UNUSED": HIT_GROUP,
    "HITBOX_SET_UNRIGGED": HIT_GROUP,
    "ATTACK_EMPTY": ATTACK_STATS,
}
MANIFEST = "In the manifest file (ports/<name>.toml): "
BUILD = "Build the port again: mhfu-port build ports/<name>.toml."
FIX = {
    "SOURCE_FILES_UNEXPECTED": MANIFEST + "fix geo and anim in [source], or leave them out.",
    "SKIN_SOURCE_WITHOUT_RIG": MANIFEST + "set source_skeleton = true in [build].",
    "BONE_OFFSET_OVERRIDE": MANIFEST + "delete bone_offset from [build].",
    "PAC_ABSENT": BUILD,
    "PAC_UNREADABLE": BUILD,
    "EFFECT_BONE_RANGE": MANIFEST + "give the [[effect]] a joint of your skeleton.",
    "INTEL_ABSENT": "Nothing to change: measure the game with mhfu intel --log to check them.",
    "INTEL_WRONG_SPECIES": "Open the studio with your base monster's data (--intel).",
    "MOVE_PAIR_NO_HANDLER": "Pick another action for the move in Actions.",
    "MOVE_PAIR_UNOBSERVED": "Nothing to change: only a census says whether it is entered.",
    "MOVE_PAIR_NEVER_ENTERED": "Pick another action in Actions, or allow_unentered = true.",
    "MOVE_PAIR_SHORT_DWELL": "Pick another action in Actions.",
    "MOVE_PAIR_BUDGET_GATED": "Keep your clip within the action's timer, or hook the seam.",
    "MOVE_PAIR_PARKS": MANIFEST + "give the move after or hold_max in [moves].",
    "MOVE_BUDGET_ROOT_MOTION": MANIFEST + "add a [[rule]] from the move with min_frames.",
    "MOVE_AFTER_ENGINE": "Nothing to change if you meant it.",
    "HITZONE_STATE_DUPLICATE": MANIFEST + "rename one [[hitzone]] state.",
    "HITZONE_STATE_COUNT": "Copy the base monster's damage grid again (Parts, More).",
    "HITBOX_UNCHECKED": "Nothing to change in your port: the base monster's data has no attacks.",
    "ATTACK_RECORD_UNKNOWN": MANIFEST + "delete this [[attack]].",
    "ATTACK_VOLUME_UNKNOWN": MANIFEST + "give the [[attack]] a hit group the base monster has.",
    "ATTACK_JOIN_INFERRED": "Nothing to change: only em75's attack table was measured.",
}


def bone_count(pac: bytes) -> int:
    """Joints of the skeleton the built PAC ships."""
    found = (e for e in Pac.from_bytes(pac).entries if Skeleton.sniff(e))
    skeleton = next(found, None)
    if skeleton is None:
        raise ValueError("no skeleton in this PAC")
    return len(Skeleton.from_bytes(skeleton).bones)


def _f(
    level: Level,
    code: str,
    where: str,
    message: str,
    at: tuple[str, int] | None = None,
    focus: str | None = None,
) -> Finding:
    """`where` is `section` or `section.key` / `section[i]`; `target` is that as a tuple, or
    `at`; the control `focus`, else `FOCUS`'s, else what to do from `FIX`."""
    target: tuple[str, str | int] | None = at
    if at is None and "[" in where:
        section, i = where.rstrip("]").split("[")
        target = (section, int(i))
    elif at is None and "." in where:
        section, key = where.split(".", 1)
        target = (section, key)
    focus = FOCUS.get(code, "") if focus is None else focus
    return Finding(level, code, message, where, target, focus, FIX.get(code, ""))


def validate(
    m: Manifest, pac: bytes | None = None, intel: SpeciesIntel | None = None
) -> list[Finding]:
    """Every check; `pac` is the BUILT port, `intel` the host species'."""
    return [
        *_settings(m),
        *_pac(m, pac),
        *_moves(m, intel),
        *_parts(m, intel),
        *_attacks(m, intel),
    ]


def _settings(m: Manifest) -> list[Finding]:
    out = []
    s = m.source
    if (s.geo, s.anim) != (s.model + GEO, s.model + ANIM):
        out.append(
            _f(
                "warning",
                "SOURCE_FILES_UNEXPECTED",
                "source",
                f"the original's geo/anim files are normally model+{GEO} / model+{ANIM} "
                f"({s.model + GEO}/{s.model + ANIM}), got {s.geo}/{s.anim}",
            )
        )
    if m.build.skin == "source" and not m.build.source_skeleton:
        out.append(
            _f(
                "warning",
                "SKIN_SOURCE_WITHOUT_RIG",
                "build",
                'skin = "source" uses the original\'s skin weights; without source_skeleton = true '
                "the joint numbers are the base monster's and every vertex lands on the wrong "
                "joint.",
            )
        )
    em = build.em_of(m)
    measured = records.OFFSET.get(em) if em is not None else None
    if m.build.bone_offset is not None and measured not in (None, m.build.bone_offset):
        out.append(
            _f(
                "warning",
                "BONE_OFFSET_OVERRIDE",
                "build",
                f"bone_offset {m.build.bone_offset} overrides the measured {measured} for "
                f"em{em:03d}: leave it out.",
            )
        )
    return out


def _pac(m: Manifest, pac: bytes | None) -> list[Finding]:
    if pac is None:
        if m.clips or m.hurtboxes or m.effects:
            return [
                _f(
                    "warning",
                    "PAC_ABSENT",
                    "",
                    f"no built PAC: {len(m.clips)} clip(s), {len(m.hurtboxes)} hurtbox(es) and "
                    f"{len(m.effects)} effect(s) were not checked against it.",
                )
            ]
        return []
    out = []
    try:
        table: dict[int, clips.Fingerprint] | None = clips.pac_clip_table(pac)
    except ValueError as e:
        out.append(_f("error", "PAC_UNREADABLE", "", f"no clip table in the PAC: {e}"))
        table = None
    if table is not None:
        out += _clips(m, table)
    if m.hurtboxes or m.effects or m.hitboxes:
        try:
            n = bone_count(pac)
        except ValueError as e:
            out.append(_f("error", "PAC_UNREADABLE", "", f"no skeleton in the PAC: {e}"))
        else:
            out += _bones(m, n)
    return out


def _clips(m: Manifest, table: dict[int, clips.Fingerprint]) -> list[Finding]:
    out = []
    moved = {
        t.name: f" {t.message}"
        for t in clips.track_labels(m, table)
        if t.status in (clips.MOVED, clips.AMBIGUOUS)
    }
    idle = table.get(1)
    for name, c in sorted(m.clips.items()):
        w = f"clips.{name}"
        if c.slot not in table:
            out.append(
                _f(
                    "error",
                    "CLIP_SLOT_MISSING",
                    w,
                    f"anim {c.slot} is not in this build: the builder drops an original clip "
                    "the base monster has no anim number for, so forcing it reaches nothing."
                    + moved.get(name, ""),
                )
            )
            continue
        end, loop = table[c.slot]
        if c.frames is not None and c.frames != end:
            out.append(
                _f(
                    "error",
                    "CLIP_FRAMES_MISMATCH",
                    w,
                    f"frames = {c.frames} here, but anim {c.slot} of this build ends at {end}: "
                    "it holds a different clip." + moved.get(name, ""),
                )
            )
        if c.loop is not None and c.loop != loop:
            out.append(
                _f(
                    "error",
                    "CLIP_LOOP_MISMATCH",
                    w,
                    f"loop = {c.loop} here, but anim {c.slot} of this build has loop = {loop}",
                )
            )
        if idle is not None and c.slot != 1 and table[c.slot] == idle:
            out.append(
                _f(
                    "warning",
                    "CLIP_IS_FILLER",
                    w,
                    f"anim {c.slot} is an idle copy ({idle[0]}f, loop={idle[1]}): forcing it "
                    "plays idle, which looks just like an override that did nothing.",
                )
            )
        if c.label and not c.labelled_build:
            out.append(
                _f(
                    "warning",
                    "LABEL_UNKEYED",
                    w,
                    "the name records no build, and anim numbers change between builds: name it "
                    "again in Clips, which records the build.",
                )
            )
    return out


def _bones(m: Manifest, n: int) -> list[Finding]:
    out = []
    sides: list[tuple[str, Sequence[Hurtbox | Hitbox]]] = [
        ("hurtbox", m.hurtboxes),
        ("hitbox", m.hitboxes),
    ]
    for kind, vols in sides:
        for i, h in enumerate(vols):
            w = f"{kind}[{i}]"
            if h.is_marker:
                continue
            if not 0 <= h.bone < n:
                out.append(
                    _f(
                        "error",
                        f"{kind.upper()}_BONE_RANGE",
                        w,
                        f"joint {h.bone} is not on your skeleton ({n} joints): a port ships its "
                        "own skeleton, so a base monster's joint number does not carry over.",
                    )
                )
            if h.radius <= 0:
                out.append(
                    _f("error", f"{kind.upper()}_RADIUS", w, f"radius {h.radius:g}: it has no size")
                )
    for i, e in enumerate(m.effects):
        if not 0 <= e.bone < n:
            out.append(
                _f(
                    "error",
                    "EFFECT_BONE_RANGE",
                    f"effect[{i}]",
                    f"joint {e.bone} is not on your skeleton ({n} joints).",
                )
            )
    return out


def _needs_budget(e: Handoff) -> bool:
    """An exit waiting on the run budget only the engine's translator provisions."""
    return any("budget" in g for g in e.guards)


def _reliable(e: Handoff) -> bool:
    """An exit gated only on the phase and the clip cursor, which always comes."""
    return all(g.startswith(("phase", "cursor", "clip", "!cursor", "!clip")) for g in e.guards)


def _moves(m: Manifest, intel: SpeciesIntel | None) -> list[Finding]:
    """The (main, sub) checks: static ones always, measured ones with a census. Only a pair the
    census measured as never entered is an error."""
    if not m.moves:
        return []
    host = m.port.host_species
    if intel is None:
        msg = f"no action data for {species.label(host)}: {len(m.moves)} move(s) not checked."
        return [_f("warning", "INTEL_ABSENT", "moves", msg)]  # no data at all, not only no census
    if intel.host_species != host:
        msg = (
            f"the action data is for {species.label(intel.host_species)}, but your base monster"
            f" is {species.label(host)}."
        )
        return [_f("error", "INTEL_WRONG_SPECIES", "moves", msg)]
    out = []
    if not intel.has_census:
        why = intel.census_reason or "no census was attached"
        out.append(
            _f(
                "info",
                "INTEL_ABSENT",
                "moves",
                f"nothing was measured in the game ({why}), so whether it ever enters these "
                f"{len(m.moves)} action(s) is unknown, not never.",
            )
        )
    for name, mv in sorted(m.moves.items()):
        w = f"moves.{name}"
        pair = f"({mv.main},{mv.sub})"
        p = intel.pair(mv.main, mv.sub)
        if p is None:
            if intel.has_static and mv.main in intel.enumerated_mains:
                b = intel.bindable(mv.main, mv.sub)
                out.append(_f("error", "MOVE_PAIR_NO_HANDLER", w, b.reason))
            else:
                msg = f"nothing is known about action {pair}: not seen is not never entered."
                out.append(_f("warning", "MOVE_PAIR_UNOBSERVED", w, msg))
            continue
        if intel.has_static and p.handler is None:
            msg = (
                f"action {pair} has no code of its own the studio can read: "
                "what it does is unknown."
            )
            out.append(_f("warning", "MOVE_PAIR_NO_HANDLER", w, msg))
        if p.entered is None:
            if intel.has_census:
                msg = f"the game was measured for this monster, but action {pair} never came up."
                out.append(_f("warning", "MOVE_PAIR_UNOBSERVED", w, msg))
        elif p.entered <= 0:
            allowed = mv.allow_unentered
            out.append(
                _f(
                    "warning" if allowed else "error",
                    "MOVE_PAIR_NEVER_ENTERED",
                    w,
                    f"the game was watched and never enters action {pair}: forced, it lasts one "
                    "tick and the clip restarts from frame 0 forever."
                    + (f" {p.note}" if p.note else "")
                    + (" Allowed by allow_unentered." if allowed else ""),
                )
            )
        elif p.dwell_ticks and p.dwell_ticks < MIN_DWELL_TICKS:
            out.append(
                _f(
                    "warning",
                    "MOVE_PAIR_SHORT_DWELL",
                    w,
                    f"action {pair} lasts only {p.dwell_ticks:.1f} ticks even when the game picks "
                    "it itself.",
                )
            )
        if intel.has_static and p.handler is not None:
            out += _chain(m, name, p.next)
            if mv.clip is not None and p.ends_on == "budget" and p.budget.gated:
                seeds = p.budget.phase0_seeds
                how = (
                    f"Its phase-0 block re-seeds it with {list(seeds)}, so a slot-32 post-hook "
                    "cannot raise it; use the slot-29 seam."
                    if seeds
                    else "A slot-32 post-hook owns the budget, so it can be raised."
                )
                out.append(
                    _f(
                        "warning",
                        "MOVE_PAIR_BUDGET_GATED",
                        w,
                        f"action {pair} ends on a timer, not with the clip, so a longer clip of "
                        f"yours is cut short. {how}",
                    )
                )
    return out


def _chain(m: Manifest, name: str, nxt: tuple[Handoff, ...] | None) -> list[Finding]:
    """A forced pair that never ends by itself, and a declared `after` the engine does not take."""
    mv = m.moves[name]
    w, pair = f"moves.{name}", f"({mv.main},{mv.sub})"
    out = []
    declared = mv.after is not None or mv.hold_max is not None
    budget_only = (
        bool(nxt) and any(map(_needs_budget, nxt or ())) and not any(map(_reliable, nxt or ()))
    )
    if nxt is not None and not declared:
        if not nxt:
            out.append(
                _f(
                    "warning",
                    "MOVE_PAIR_PARKS",
                    w,
                    f"action {pair} never ends by itself: forced from a script, the monster "
                    "stays in it. Give the move `after` or `hold_max`.",
                )
            )
        elif budget_only:
            other = sorted({e.reason for e in nxt if not _needs_budget(e)})
            situational = (
                f" Its other exit(s), {', '.join(other)}, are situational." if other else ""
            )
            out.append(
                _f(
                    "warning",
                    "MOVE_PAIR_PARKS",
                    w,
                    f"action {pair} ends when its run timer runs out, which the game sets on the "
                    "way in and a script does not: forced, it stays in its last step with its hit "
                    f"spent.{situational} Give the move `after` or `hold_max`.",
                )
            )
    if budget_only and mv.clip is not None:
        timed = [
            r
            for r in m.rules
            if r.from_move == name
            and r.min_frames > 0
            and not (r.receding or r.closing)
            and r.dist == (0.0, UNLIMITED_DIST)
        ]
        if not timed:
            out.append(
                _f(
                    "warning",
                    "MOVE_BUDGET_ROOT_MOTION",
                    w,
                    f"action {pair} runs until the clip has travelled far enough, and clip "
                    f"{mv.clip!r} is yours: if it does not travel, the action never ends. Add a "
                    "[[rule]] from this move with only `min_frames`, or give the clip travel.",
                )
            )
    if nxt and mv.after is not None and mv.after in m.moves:
        t = m.moves[mv.after]
        engine = sorted({p for e in nxt for p in e.to})
        if (t.main, t.sub) not in engine:
            took = " / ".join(f"({a},{b})" for a, b in engine)
            out.append(
                _f(
                    "info",
                    "MOVE_AFTER_ENGINE",
                    w,
                    f"after = {mv.after} ({t.main},{t.sub}), but the game itself goes from "
                    f"{pair} to {took}. Fine if meant.",
                )
            )
    return out


def _parts(m: Manifest, intel: SpeciesIntel | None) -> list[Finding]:
    out = []
    by_index: dict[int, list[str]] = {}
    for name, part in sorted(m.parts.items()):
        by_index.setdefault(part.index, []).append(name)
    for idx, names in sorted(by_index.items()):
        if len(names) > 1:
            out.append(
                _f(
                    "error",
                    "PART_INDEX_DUPLICATE",
                    "parts",
                    f"{', '.join(map(repr, names))} all name breakable part {idx}: two parts that "
                    "are secretly one.",
                    ("part", idx),
                )
            )
    named = {part.index for part in m.parts.values()}
    for i, h in enumerate(m.hurtboxes):
        w = f"hurtbox[{i}]"
        if h.part is None:
            msg = (
                "no `part`, so a hit here counts toward no breakable part (0). The damage row,"
                " `hitzone_row`, is another field."
            )
            out.append(_f("warning", "HURTBOX_NO_PART", w, msg))
        elif m.parts and h.part not in named:
            msg = f"breakable part {h.part} has no name in [parts]."
            out.append(_f("warning", "HURTBOX_PART_UNNAMED", w, msg))
        if h.is_capsule and h.to is None:
            msg = 'shape = "capsule" but no `to`: say which you mean.'
            out.append(_f("error", "HURTBOX_CAPSULE_NO_END", w, msg))
    for i, hz in enumerate(m.hitzones):
        if not any(v for row in hz.rows for v in row):
            msg = "every percentage is 0: a monster nothing can hurt."
            out.append(_f("warning", "HITZONE_ALL_ZERO", f"hitzone[{i}]", msg))
    names = [hz.name for hz in m.hitzones]
    for n in sorted({n for n in names if names.count(n) > 1}):
        msg = f"two states are both called {n!r}; the engine picks a state by index."
        out.append(_f("error", "HITZONE_STATE_DUPLICATE", "hitzone", msg))
    pt = intel.parts if intel is not None else None
    if m.hitzones and pt is not None and pt.has_grid and len(m.hitzones) != pt.n_states:
        msg = (
            f"{len(m.hitzones)} state(s) here but the base monster has {pt.n_states}: an extra "
            "one is never used."
        )
        out.append(_f("warning", "HITZONE_STATE_COUNT", "hitzone", msg))
    cap = pt.capacity if pt is not None else None
    if m.hurtboxes and cap is not None and len(m.hurtboxes) > cap:
        msg = (
            f"{len(m.hurtboxes)} hurtboxes but the base monster's table holds {cap}: the game "
            "drops the rest."
        )
        out.append(_f("warning", "HURTBOX_OVER_CAPACITY", "hurtbox", msg, ("hurtbox", cap)))
    if m.hurtboxes and not m.parts:
        msg = f"{len(m.hurtboxes)} hurtbox(es) and no [parts] naming their parts."
        used = sorted({h.part for h in m.hurtboxes if h.part})
        # part 0 is nobody: with no other, a hurtbox needs a part before a name helps
        at, focus = (("part", used[0]), PART_NAME) if used else (("hurtbox", 0), HURT_PART)
        out.append(_f("warning", "PARTS_UNNAMED", "parts", msg, at, focus))
    return out


def _attacks(m: Manifest, intel: SpeciesIntel | None) -> list[Finding]:
    if not (m.hitboxes or m.attacks):
        return []
    host = m.port.host_species
    out = []
    for i, h in enumerate(m.hitboxes):
        if h.is_capsule and h.to is None:
            msg = 'shape = "capsule" but no `to`: say which you mean.'
            out.append(_f("error", "HITBOX_CAPSULE_NO_END", f"hitbox[{i}]", msg))
    for i, a in enumerate(m.attacks):
        if a.is_empty:
            msg = f"attack {a.id} changes no stat, so it does nothing."
            out.append(_f("warning", "ATTACK_EMPTY", f"attack[{i}]", msg))
    attacks = intel.attacks if intel is not None else None
    if attacks is None or not attacks.present:
        msg = (
            f"no attack data for {species.label(host)}: hit groups, attack ids and sizes are "
            "not checked, and Send to game refuses until there is."
        )
        out.append(_f("warning", "HITBOX_UNCHECKED", "hitbox", msg))
        return out
    n_sets = len(attacks.sets)
    n_records = len(attacks.primary.attacks) if attacks.primary else 0
    for st in sorted({h.set for h in m.hitboxes}):
        hs = attacks.set(st)
        at = ("set", st)
        if hs is None:
            msg = f"hit group {st}: {species.label(host)} has {n_sets}."
            out.append(_f("error", "HITBOX_SET_UNKNOWN", "hitbox", msg, at))
            continue
        mine = sum(h.set == st for h in m.hitboxes)
        if mine > hs.capacity:
            msg = (
                f"hit group {st}: {mine} hitboxes but the base monster's holds {hs.capacity}; "
                "the game drops the rest."
            )
            out.append(_f("warning", "HITBOX_OVER_CAPACITY", "hitbox", msg, at))
        if not hs.rigged:
            msg = (
                f"hit group {st} is on no joint in the base monster (the attack's own place): a "
                "joint here changes nothing."
            )
            out.append(_f("info", "HITBOX_SET_UNRIGGED", "hitbox", msg, at))
        if not attacks.attacks_using(st) and not any(a.volume == st for a in m.attacks):
            msg = f"no attack uses hit group {st}: an [[attack]] can point one at it."
            out.append(_f("warning", "HITBOX_SET_UNUSED", "hitbox", msg, at))
    for i, a in enumerate(m.attacks):
        if attacks.attack(a.id) is None:
            msg = f"attack {a.id}: {species.label(host)} has {n_records}."
            out.append(_f("error", "ATTACK_RECORD_UNKNOWN", f"attack[{i}]", msg))
        if a.volume is not None and attacks.set(a.volume) is None:
            msg = f"hit group {a.volume}: {species.label(host)} has {n_sets}."
            out.append(_f("error", "ATTACK_VOLUME_UNKNOWN", f"attack[{i}]", msg))
    if attacks.join != "measured":
        msg = (
            f"for {species.label(host)} the link from code to attack table is {attacks.join}, not"
            " measured: which hit group an action uses is a guess here."
        )
        out.append(_f("info", "ATTACK_JOIN_INFERRED", "hitbox", msg))
    return out


def report(findings: list[Finding]) -> str:
    if not findings:
        return "OK: no findings."
    n = {lv: sum(f.level == lv for f in findings) for lv in ("error", "warning", "info")}
    tail = (
        f"{len(findings)} finding(s): {n['error']} error, {n['warning']} warning, {n['info']} info"
    )
    return "\n".join([*map(str, findings), "", tail])
