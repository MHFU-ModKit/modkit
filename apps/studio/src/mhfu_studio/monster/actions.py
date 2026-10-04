# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Actions rows: what plays in this port while the game runs each of the base monster's
actions, what it hits with and whether the timing fits; and the moves as a mod declares them.

The game asks for the base monster's anim N and plays whatever sits in the port's slot N, by
position, not meaning, unless a move paints its own clip on the action. No toolkit here: the
Actions panel shows the rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mhfu.em.intel import AttackIntel, PairIntel, SpeciesIntel
from mhfu_port.manifest import Claim, Manifest, Move

from mhfu_studio.monster import clips
from mhfu_studio.monster.align import Timing, timing

Pair = tuple[int, int]
#: row groups, in the order they are listed
MOVE, ATTACK, ENTERED = "move", "attack", "entered"
NONE = "–"
LUA_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
LUA_KEYWORDS = frozenset(
    "and break do else elseif end false for function goto if in local nil not or repeat return"
    " then true until while".split()
)


@dataclass(frozen=True)
class Plays:
    """The anim on screen while the game is in an action."""

    slot: int | None
    """None: the action names no anim, so the last one keeps playing (or it is picked live)."""
    name: str = ""
    """The manifest's name for the slot's clip."""
    kind: str | None = None
    frames: int | None = None
    impact: int | None = None
    move: str | None = None
    """The move that paints it: bound on the action, or claiming it."""
    claimed: bool = False
    computed: bool = False
    why: str = ""

    @property
    def playable(self) -> bool:
        return self.slot is not None and self.kind != clips.MISSING

    def text(self, cell: bool = False) -> str:
        """`lunge_forward · own clip`, `anim 17 · idle copy`; a `cell` leaves the expected
        kind, an own clip, to its swatch."""
        if self.slot is None:
            return "picked while it runs" if self.computed else "keeps the last clip"
        what = (self.name or f"anim {self.slot}") + (f" (via {self.move})" if self.claimed else "")
        if self.kind in (None, clips.UNKNOWN) or (cell and self.kind == clips.CARRIED):
            return what
        return f"{what} · {clips.KIND_WORDS[self.kind][0]}"


@dataclass(frozen=True)
class ActionRow:
    main: int
    sub: int
    group: str
    plays: Plays
    move: str | None = None
    label: str = ""
    alike: tuple[Pair, ...] = ()
    """Actions with the same code, anim and hand-offs, listed as this one."""
    attacks: tuple[int, ...] = ()
    """The attack ids its code spawns."""
    hit_groups: tuple[int, ...] = ()
    timing: Timing = Timing(NONE)

    @property
    def pair(self) -> Pair:
        return self.main, self.sub

    @property
    def key(self) -> tuple[int, int, str | None]:
        return self.main, self.sub, self.move

    def cells(self) -> list[str]:
        """Action, Plays now, Hits, Timing."""
        name = f"({self.main},{self.sub})" + (f" +{len(self.alike)}" if self.alike else "")
        return [
            f"{self.move} {name}" if self.move else name,
            self.plays.text(cell=True),
            self.hits(),
            self.timing.text,
        ]

    def hits(self) -> str:
        if self.hit_groups:
            word = "group" if len(self.hit_groups) == 1 else "groups"
            return f"{word} {', '.join(map(str, self.hit_groups))}"
        return "yes" if self.attacks else NONE

    def tip(self) -> str:
        what = {
            MOVE: f"Your move {self.move}" + (f": {self.label}" if self.label else ""),
            ATTACK: "An attack of the base monster",
            ENTERED: "The game goes into this action by itself",
        }[self.group]
        lines = [what, f"Plays {self.plays.text()}. {self.plays.why}".strip()]
        if self.hit_groups:
            lines.append(f"Hits with hit group {', '.join(map(str, self.hit_groups))}")
        elif self.attacks:
            lines.append("Hits, with a hit group the studio cannot match")
        if self.timing.detail:
            lines.append(self.timing.detail)
        if self.alike:
            more = " …" if len(self.alike) > 8 else ""
            near = " ".join(f"({m},{s})" for m, s in self.alike[:8])
            lines.append(f"Same code and anim as {near}{more}")
        return "\n".join(lines)


def bound_move(m: Manifest | None, main: int, sub: int) -> str | None:
    """The first move bound on the action."""
    if m is None:
        return None
    return next((n for n, mv in m.moves.items() if (mv.main, mv.sub) == (main, sub)), None)


def claimer(m: Manifest | None, main: int, sub: int) -> str | None:
    """The move whose `claim` takes the action over when the base monster's brain picks it."""
    for name, mv in ({} if m is None else m.moves).items():
        c = mv.claim
        if c is not None and main in c.mains and c.sub in (None, sub):
            return name
    return None


def plays_now(
    m: Manifest | None,
    p: PairIntel | None,
    main: int,
    sub: int,
    move: str | None,
    cov: clips.Coverage,
) -> Plays:
    """`move`'s clip (else the move bound on the action, else one claiming it); without one,
    the port's slot for the first of the base monster's anims the build has."""
    move = move if m is not None and move in m.moves else bound_move(m, main, sub)
    claimed = False
    if move is None:
        move = claimer(m, main, sub)
        claimed = move is not None
    if m is not None and move is not None:
        mv = m.moves[move]
        slot = m.clips[mv.clip].slot if mv.clip else mv.anim
        return _plays(m, slot, cov, move, claimed)
    a1 = () if p is None else p.a1
    if not a1:
        return Plays(None, computed=p is not None and p.a1_computed)
    return _plays(m, next((a for a in a1 if a in cov.slots), a1[0]), cov)


def _plays(
    m: Manifest | None,
    slot: int | None,
    cov: clips.Coverage,
    move: str | None = None,
    claimed: bool = False,
) -> Plays:
    if slot is None:
        return Plays(None, move=move, claimed=claimed)
    sc = cov.slots.get(slot)
    found = None if m is None else clips.entry(m, slot)
    if sc is None:
        kind = clips.MISSING if cov.slots else None
        why = f"Anim {slot} is not in this build." if cov.slots else ""
    else:
        kind, why = sc.kind, sc.why()
    return Plays(
        slot,
        found[0] if found else "",
        kind,
        None if sc is None else sc.frames,
        None if found is None else found[1].impact_frame,
        move,
        claimed,
        why=why,
    )


def entered(p: PairIntel, hubs: set[Pair]) -> bool:
    """Seen entered by the census; unmeasured, another action hands to it or the brain picks it."""
    if p.entered is not None:
        return p.entered > 0
    return bool(p.prev) or (p.main, p.sub) in hubs


def row(
    m: Manifest | None,
    p: PairIntel | None,
    main: int,
    sub: int,
    group: str,
    cov: clips.Coverage,
    attacks: AttackIntel | None = None,
    species: int | None = None,
    move: str | None = None,
    alike: tuple[Pair, ...] = (),
) -> ActionRow:
    plays = plays_now(m, p, main, sub, move, cov)
    ids = () if p is None else p.attack_ids
    sets = tuple(attacks.sets_for(ids, species)) if attacks is not None and ids else ()
    mv = None if m is None or move is None else m.moves.get(move)
    gates = [] if p is None else p.tested_frames
    return ActionRow(
        main,
        sub,
        group,
        plays,
        move,
        "" if mv is None else mv.label,
        alike,
        ids,
        sets,
        timing(gates, plays.impact, plays.frames),
    )


def rows(
    m: Manifest,
    intel: SpeciesIntel | None,
    cov: clips.Coverage,
    attacks: AttackIntel | None = None,
    species: int | None = None,
) -> list[ActionRow]:
    """Your moves, then the actions that attack, then the rest the game enters; actions alike
    (same code, anim, hits and hand-offs) as one row, and none the census saw never entered."""

    def pair_of(main: int, sub: int) -> PairIntel | None:
        return None if intel is None else intel.pair(main, sub)

    out = [
        row(m, pair_of(mv.main, mv.sub), mv.main, mv.sub, MOVE, cov, attacks, species, name)
        for name, mv in m.moves.items()
    ]
    if intel is None:
        return out
    bound = {(mv.main, mv.sub) for mv in m.moves.values()}
    hubs = set(intel.hubs)
    groups: dict[object, list[PairIntel]] = {}
    for p in sorted(intel, key=lambda p: (p.main, p.sub)):
        if (p.main, p.sub) in bound or p.never_entered:
            continue
        group = ATTACK if p.attack_ids else ENTERED if entered(p, hubs) else None
        if group is None:
            continue
        sig = (group, p.handler, tuple(p.successors), p.attack_ids, p.a1)
        groups.setdefault(sig if p.handler is not None else (p.main, p.sub, group), []).append(p)
    for group in (ATTACK, ENTERED):
        for ps in groups.values():
            p = ps[0]
            if (ATTACK if p.attack_ids else ENTERED) == group:
                alike = tuple((q.main, q.sub) for q in ps[1:])
                out.append(row(m, p, p.main, p.sub, group, cov, attacks, species, None, alike))
    return out


# the moves as a mod's Lua declares them


def _lua_key(name: str) -> str:
    return name if LUA_NAME.fullmatch(name) and name not in LUA_KEYWORDS else f'["{name}"]'


def _lua_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _lua_claim(c: Claim) -> str:
    mains = str(c.mains[0]) if len(c.mains) == 1 else "{ " + ", ".join(map(str, c.mains)) + " }"
    return "{ main = " + mains + ("" if c.sub is None else f", sub = {c.sub}") + " }"


def lua_move(mv: Move) -> str:
    """One move's table: the fields `mhfu_port.lua` reads, defaults left out."""
    bits = [f"main = {mv.main}", f"sub = {mv.sub}"]
    if mv.clip is not None:
        bits.append(f"clip = {_lua_str(mv.clip)}")
    if mv.anim is not None:
        bits.append(f"anim = {mv.anim}")
    if mv.latch != 1:
        bits.append(f"latch = {mv.latch}")
    if mv.after is not None:
        bits.append(f"after = {_lua_str(mv.after)}")
    if mv.hold_max is not None:
        bits.append(f"hold_max = {mv.hold_max}")
    if mv.claim is not None:
        bits.append(f"claim = {_lua_claim(mv.claim)}")
    return "{ " + ", ".join(bits) + " }"


def lua_moves(m: Manifest) -> str:
    """The `clips` and `moves` fields of a mod's `P.define{…}` (the shape
    `mods/lua/zinogre_lunge.lua` writes by hand); empty without moves."""
    if not m.moves:
        return ""
    used = [n for n in m.clips if any(mv.clip == n for mv in m.moves.values())]
    builds = {m.clips[n].labelled_build for n in used} - {None}
    source = m.path.name if m.path is not None else m.port.name
    head = [f"-- from {source}, for your mod's P.define{{ ... }}"]
    if len(builds) == 1:
        head.append(f"-- anims of build {builds.pop()}")
    return "\n".join(
        [
            *head,
            "clips = {",
            *(f"  {_lua_key(n)} = {m.clips[n].slot}," for n in used),
            "},",
            "moves = {",
            *(f"  {_lua_key(n)} = {lua_move(mv)}," for n, mv in m.moves.items()),
            "},",
        ]
    )
