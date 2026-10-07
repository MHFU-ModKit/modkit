# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A port's rules (`mhfu_port.manifest.Rule`) as document edits, and each as a sentence.

No toolkit: the Moves panel calls these through the workspace, one undo step per edit. A
refusal is the manifest's own `ManifestError`: its checks are the only ones.
"""

from __future__ import annotations

import dataclasses
import functools
from typing import Any

from mhfu_port import manifest
from mhfu_port.manifest import UNLIMITED_DIST, Event, Manifest, ManifestError, Move, Rule

from mhfu_studio.monster.document import PortDocument

FIELDS = tuple(f.name for f in dataclasses.fields(Rule))
FORCE = "force" in FIELDS
"""The schema carries `Rule.force`; the editor shows it only then."""
FORCE_TIP = (
    "A move asked while the monster has noticed the hunter but is not yet in combat waits for"
    ' combat, so the "!" and the howl play out; force plays it at once.'
)
#: an event after "on"
EVENT_WORDS: dict[str, str] = {
    "noticed": "noticing the hunter",
    "combat_entered": "entering combat",
    "combat_left": "leaving combat",
    "flinch": "a flinch",
    "part_broken": "a break",
    "tail_cut": "the tail cut",
}


def forced(r: Rule) -> bool:
    """`r.force`, False where the schema has none."""
    return bool(vars(r).get("force", False))


def event_words(on: str) -> str:
    return EVENT_WORDS.get(on, on.replace("_", " "))


@functools.cache
def takes_part(on: Event | None) -> bool:
    """Whether a rule on `on` may name a part: asked of the manifest's own checks."""
    probe = Manifest(
        manifest.Port("p", 0, "p.bin"),
        manifest.Source(0),
        moves={"m": Move(anim=0)},
        rules=[Rule("m", on=on, part=0)],
    )
    try:
        manifest.check(probe)
    except ManifestError:
        return False
    return True


def rule(m: Manifest, index: int) -> Rule:
    if not 0 <= index < len(m.rules):
        raise ManifestError(f"no rule {index}")
    return m.rules[index]


def new_rule(doc: PortDocument, play: str) -> str:
    """A rule that plays `play` once, when the monster notices the hunter."""
    r = Rule(play, on="noticed", count=1)
    doc.edit(lambda m: m.rules.append(r))
    return f"rule {len(doc.manifest.rules) - 1}: {sentence(r, doc.manifest)}"


def set_rule(doc: PortDocument, index: int, **fields: Any) -> str:
    """`FIELDS` of rule `index`. An event that takes no part drops the part, and an own move to
    play drops `mode`, a pair's."""
    bad = sorted(set(fields) - set(FIELDS))
    if bad:
        raise ManifestError(f"not a rule field: {', '.join(bad)}")
    if fields.get("dist") is not None:
        fields["dist"] = tuple(float(x) for x in fields["dist"])
    rule(doc.manifest, index)

    def change(m: Manifest) -> None:
        r = dataclasses.replace(m.rules[index], **fields)
        if "part" not in fields and not takes_part(r.on):
            r.part = None
        played = m.moves.get(r.play)
        if "mode" not in fields and played is not None and played.own:
            r.mode = 0
        m.rules[index] = r

    doc.edit(change)
    return f"rule {index}: " + ", ".join(f"{k} = {v}" for k, v in fields.items())


def remove_rule(doc: PortDocument, index: int) -> str:
    rule(doc.manifest, index)
    doc.edit(lambda m: m.rules.pop(index))
    return f"rule {index} removed"


def part_name(m: Manifest, part: int) -> str:
    """`the head` for a named part, else `part 3`."""
    name = next((n for n, p in m.parts.items() if p.index == part), None)
    return f"part {part}" if name is None else f"the {name}"


def sentence(r: Rule, m: Manifest) -> str:
    """The rule in words: "on a flinch of the head, play flinch_head", "after the skid for 20
    frames, within 1200, play stamp"."""
    out = []
    if r.on is not None:
        of = "" if r.part is None else f" of {part_name(m, r.part)}"
        out.append(f"on {event_words(r.on)}{of}")
    after = [f"the {r.from_move}"] if r.from_move is not None else []
    if r.from_main:
        mains = " or ".join(map(str, r.from_main))
        after.append(f"main state{'s' if len(r.from_main) > 1 else ''} {mains}")
    if after:
        held = f" for {r.min_frames} frames" if r.min_frames else ""
        out.append(("after " if held else "during ") + " or ".join(after) + held)
    lo, hi = r.dist
    if hi < UNLIMITED_DIST:
        out.append(f"between {lo:g} and {hi:g} away" if lo > 0 else f"within {hi:g}")
    elif lo > 0:
        out.append(f"beyond {lo:g}")
    if r.receding:
        out.append("while the hunter moves away")
    if r.closing:
        out.append("while the hunter closes in")
    out.append(
        f"play {r.play}"
        + (f" in mode {r.mode}" if r.mode else "")
        + (" right away" if forced(r) else "")
    )
    if r.cooldown:
        out.append(f"then wait {r.cooldown} frames")
    if r.count is not None:
        out.append("once" if r.count == 1 else f"at most {r.count} times")
    return ", ".join(out)
