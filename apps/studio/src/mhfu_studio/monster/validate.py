# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The checks on a port manifest that need evidence: the built PAC and the host's intel.

The loader refuses anything structural. What is left: a clip slot the build does not hold (the
builder drops a donor clip with no host slot, and fills a third of the slots with idle), a move
on a pair the census saw never entered (it survives one tick), and bone numbers off the rig the
port ships. Missing evidence warns; it never passes silently.
"""

from __future__ import annotations

from collections.abc import Sequence

from mhfu.em.intel import MIN_DWELL_TICKS, Handoff, SpeciesIntel
from mhfu_port import build, records
from mhfu_port.manifest import ANIM, GEO, UNLIMITED_DIST, Hitbox, Hurtbox, Manifest
from mhp_formats.pac import Pac
from mhp_formats.skeleton import Skeleton

from mhfu_studio.monster import clips
from mhfu_studio.shell.findings import Finding, Level


def bone_count(pac: bytes) -> int:
    """Joints of the skeleton the built PAC ships."""
    found = (e for e in Pac.from_bytes(pac).entries if Skeleton.sniff(e))
    skeleton = next(found, None)
    if skeleton is None:
        raise ValueError("no skeleton in this PAC")
    return len(Skeleton.from_bytes(skeleton).bones)


def _f(level: Level, code: str, where: str, message: str) -> Finding:
    """`where` is `section` or `section.key` / `section[i]`; `target` is that as a tuple."""
    if "[" in where:
        section, i = where.rstrip("]").split("[")
        target: tuple[str, str | int] | None = (section, int(i))
    elif "." in where:
        section, key = where.split(".", 1)
        target = (section, key)
    else:
        target = None
    return Finding(level, code, message, where, target)


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
                f"geo/anim are normally model+{GEO} / model+{ANIM} "
                f"({s.model + GEO}/{s.model + ANIM}), got {s.geo}/{s.anim}",
            )
        )
    if m.build.skin == "source" and not m.build.source_skeleton:
        out.append(
            _f(
                "warning",
                "SKIN_SOURCE_WITHOUT_RIG",
                "build",
                'skin = "source" pairs the donor\'s bone palette with the output rig; without '
                "source_skeleton the indices are the host's and every vertex lands on the wrong "
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
                f"bone_offset {m.build.bone_offset} overrides the MEASURED offset {measured} for "
                f"em{em:03d}.",
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
                    f"no built PAC: {len(m.clips)} clip slot(s), {len(m.hurtboxes)} hurtbox "
                    f"bone(s) and {len(m.effects)} effect bone(s) went unchecked.",
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
                    f"slot {c.slot} is not populated in this build: the builder drops a donor "
                    "clip the host has no slot for, so scripting it reaches nothing."
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
                    f"declared frames = {c.frames}, the build's slot {c.slot} ends at {end}: the "
                    "slot holds a different clip." + moved.get(name, ""),
                )
            )
        if c.loop is not None and c.loop != loop:
            out.append(
                _f(
                    "error",
                    "CLIP_LOOP_MISMATCH",
                    w,
                    f"declared loop = {c.loop}, the build's slot {c.slot} has loop = {loop}",
                )
            )
        if idle is not None and c.slot != 1 and table[c.slot] == idle:
            out.append(
                _f(
                    "warning",
                    "CLIP_IS_FILLER",
                    w,
                    f"slot {c.slot} holds a copy of the idle clip ({idle[0]}f, loop={idle[1]}): "
                    "forcing it plays IDLE, which looks identical to the override failing.",
                )
            )
        if c.label and not c.labelled_build:
            out.append(
                _f(
                    "warning",
                    "LABEL_UNKEYED",
                    w,
                    "the label records no build, and clip ids are per build: re-label it in the "
                    "studio, which stamps the build.",
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
                        f"bone {h.bone} is outside the shipped skeleton's {n} joints: a port "
                        "ships its OWN rig, so a host bone number does not transfer.",
                    )
                )
            if h.radius <= 0:
                out.append(
                    _f("error", f"{kind.upper()}_RADIUS", w, f"radius {h.radius:g} is no volume")
                )
    for i, e in enumerate(m.effects):
        if not 0 <= e.bone < n:
            out.append(
                _f(
                    "error",
                    "EFFECT_BONE_RANGE",
                    f"effect[{i}]",
                    f"bone {e.bone} is outside the shipped skeleton's {n} joints.",
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
        msg = f"no intel for host em{host:02d}: {len(m.moves)} (main,sub) pair(s) unchecked."
        return [_f("warning", "INTEL_ABSENT", "moves", msg)]
    if intel.host_species != host:
        msg = f"the intel is for em{intel.host_species:02d}, this port rides em{host:02d}."
        return [_f("error", "INTEL_WRONG_SPECIES", "moves", msg)]
    out = []
    if not intel.has_census:
        why = intel.census_reason or "no census was attached"
        out.append(
            _f(
                "warning",
                "INTEL_ABSENT",
                "moves",
                f"the intel carries NO measurements ({why}), so whether the engine ever enters "
                f"{len(m.moves)} (main,sub) pair(s) is UNKNOWN, not zero.",
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
                msg = f"nothing is known about {pair}: absent is not never entered."
                out.append(_f("warning", "MOVE_PAIR_UNOBSERVED", w, msg))
            continue
        if intel.has_static and p.handler is None:
            msg = f"{pair} runs inline and calls no handler: nothing offline says what it does."
            out.append(_f("warning", "MOVE_PAIR_NO_HANDLER", w, msg))
        if p.entered is None:
            if intel.has_census:
                msg = f"the census covers this species but says nothing about {pair}."
                out.append(_f("warning", "MOVE_PAIR_UNOBSERVED", w, msg))
        elif p.entered <= 0:
            allowed = mv.allow_unentered
            out.append(
                _f(
                    "warning" if allowed else "error",
                    "MOVE_PAIR_NEVER_ENTERED",
                    w,
                    f"the census says the engine enters {pair} ZERO times: forced, it survives "
                    "exactly one tick and the clip restarts from frame 0 forever."
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
                    f"{pair} holds for only {p.dwell_ticks:.1f} ticks even when the ENGINE picks "
                    "it.",
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
                        f"{pair} ends on the frame budget, not the clip, so a longer ported clip "
                        f"is TRUNCATED. {how}",
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
                    f"{pair}'s handler never ends the action itself: forced from Lua it stands "
                    "until something else moves him. Declare `after` and/or `hold_max`.",
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
                    f"{pair} ends when its run budget is spent, which the engine's translator "
                    "sets on the way in and a Lua act_set does not: forced, it parks in its last "
                    f"phase with its hitbox spent.{situational} Declare `after` / `hold_max`.",
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
                    f"{pair}'s run budget is spent by the playing clip's root motion, and clip "
                    f"{mv.clip!r} is the port's: if its root does not travel the pair never ends. "
                    "Declare a [[rule]] from this move with only `min_frames`, or give the clip "
                    "root motion.",
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
                    f"after = {mv.after} ({t.main},{t.sub}); the engine itself hands {pair} to "
                    f"{took}. Fine if deliberate.",
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
                    f"{', '.join(map(repr, names))} all claim part {idx}: two break bars that are "
                    "secretly one.",
                )
            )
    named = {part.index for part in m.parts.values()}
    for i, h in enumerate(m.hurtboxes):
        w = f"hurtbox[{i}]"
        if h.part is None:
            msg = "no `part`, so a hit here deposits into slot 0. `hitzone_row` is another field."
            out.append(_f("warning", "HURTBOX_NO_PART", w, msg))
        elif m.parts and h.part not in named:
            msg = f"part {h.part} has no entry in [parts]."
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
            f"{len(m.hitzones)} state(s) authored but the host ships {pt.n_states}: an extra "
            "block has nothing to point at it."
        )
        out.append(_f("warning", "HITZONE_STATE_COUNT", "hitzone", msg))
    if m.hitzones:
        msg = (
            "the damage grid is SPECIES data: with the port replacing the host it is his alone; "
            "beside a native host monster it changes that one too."
        )
        out.append(_f("warning", "HITZONE_SHARED", "hitzone", msg))
    cap = pt.capacity if pt is not None else None
    if m.hurtboxes and cap is not None and len(m.hurtboxes) > cap:
        msg = (
            f"{len(m.hurtboxes)} volume(s) but the host set holds {cap}: the runtime writes them "
            "in place and truncates the rest."
        )
        out.append(_f("warning", "HURTBOX_OVER_CAPACITY", "hurtbox", msg))
    if m.hurtboxes and not m.parts:
        msg = f"{len(m.hurtboxes)} hurtbox volume(s) and no [parts] naming them."
        out.append(_f("warning", "PARTS_UNNAMED", "parts", msg))
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
            msg = f"record {a.id} names no lever, so the block changes nothing."
            out.append(_f("warning", "ATTACK_EMPTY", f"attack[{i}]", msg))
    at = intel.attacks if intel is not None else None
    if at is None or not at.present:
        msg = (
            f"no attack intel for em{host:02d}: set indices, record ids and capacities are "
            "unchecked, and the hit module export refuses until there is."
        )
        out.append(_f("warning", "HITBOX_UNCHECKED", "hitbox", msg))
        return out
    n_sets, n_records = len(at.sets), len(at.primary.attacks) if at.primary else 0
    for st in sorted({h.set for h in m.hitboxes}):
        hs = at.set(st)
        if hs is None:
            msg = f"set {st}: host em{host:02d} has {n_sets} volume set(s)."
            out.append(_f("error", "HITBOX_SET_UNKNOWN", "hitbox", msg))
            continue
        mine = sum(h.set == st for h in m.hitboxes)
        if mine > hs.capacity:
            msg = (
                f"set {st}: {mine} volume(s) but the host's set holds {hs.capacity}; the runtime "
                "writes them in place and truncates the rest."
            )
            out.append(_f("warning", "HITBOX_OVER_CAPACITY", "hitbox", msg))
        if not hs.rigged:
            msg = (
                f"set {st} is un-rigged on the host (node-space only): a joint here aligns nothing."
            )
            out.append(_f("warning", "HITBOX_SET_UNRIGGED", "hitbox", msg))
        if not at.attacks_using(st):
            msg = f"no attack record points at set {st}, unless an [[attack]] re-points one."
            out.append(_f("warning", "HITBOX_SET_UNUSED", "hitbox", msg))
    for i, a in enumerate(m.attacks):
        if at.attack(a.id) is None:
            msg = f"record {a.id}: host em{host:02d} has {n_records} record(s)."
            out.append(_f("error", "ATTACK_RECORD_UNKNOWN", f"attack[{i}]", msg))
        if a.volume is not None and at.set(a.volume) is None:
            msg = f"volume {a.volume}: host em{host:02d} has {n_sets} volume set(s)."
            out.append(_f("error", "ATTACK_VOLUME_UNKNOWN", f"attack[{i}]", msg))
    if at.join != "measured":
        msg = (
            f"em{host:02d}'s spawner-to-table join is {at.join}, not measured: which set a move "
            "hits with is an inference here."
        )
        out.append(_f("warning", "ATTACK_JOIN_INFERRED", "hitbox", msg))
    msg = (
        "attack sets and records are SPECIES data: with the port replacing the host they are his "
        f"alone; beside a native em{host:02d} they re-arm the native too."
    )
    out.append(_f("warning", "HITBOX_SHARED", "hitbox", msg))
    return out


def report(findings: list[Finding]) -> str:
    if not findings:
        return "OK: no findings."
    n = {lv: sum(f.level == lv for f in findings) for lv in ("error", "warning", "info")}
    tail = (
        f"{len(findings)} finding(s): {n['error']} error, {n['warning']} warning, {n['info']} info"
    )
    return "\n".join([*map(str, findings), "", tail])
