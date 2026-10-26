# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The own moves a manifest's `[brain.pairs] "7,13" = "own"` stands for, derived at each build
from both games' files: the clips the donor's handler plays, from the clip frame it starts them
at to the one it ends them at, the attacks it spawns with their frames, matched to the host
species' attack records, how it turns, and the motion it makes by code (`Move.travel`,
`Move.lunge`, a fixed turn, and a jump's height as a lifted copy of the clip in a free entry).

    m, findings = own.expand(manifest, data)    # the manifest with `p3_7_13` and its clips

`expand` is the one place derived moves enter a port. Every reader of a port's moves takes its
result: the moves module (`mhfu-port build`, `inject`, the studio's deploy) and `mhfu-port splice`
(the slots the script's `enter 16 k 0` names). A move of the same name in `[moves]` is used
instead; nothing derived is stored.

A pair with several clips is a chain, `p3_7_14`, `p3_7_14_2`, ..., each step handing to the next
through `after`; a step carries the donor's attacks that are active on its clip. The last step
hands to the pair the handler hands over to (`P3_OWN_NEXT`: where it cannot); equal tails are one
move (`share`). A donor record becomes the host record whose volumes overlap it most on the port's
rig (`[brain.attacks]` names one instead), and the node plays with the host record's power and
life. A record nothing matches gets a spare host record (`brain.spare`), added to the returned
manifest as `[[attack]]` and `[[hitbox]]`; the studio's hit tables write it into the game.
"""

from __future__ import annotations

import copy
import dataclasses
import statistics
from collections.abc import Callable, Collection, Iterable, Sequence
from typing import NamedTuple

import numpy as np
from mhfu import hitbox, hitzone
from mhfu.em.script.check import Finding
from mhfu.files import em_overlay
from mhp_formats import fu
from numpy.typing import NDArray

from .. import build, fk, layout, moves, p3hits, p3motion, p3moveset, sequence
from ..data import Data
from ..manifest import (
    MOVE_ATTACKS,
    OWN_MOVES,
    AttackWindow,
    Brain,
    Clip,
    Lunge,
    Manifest,
    ManifestError,
    Move,
    Steer,
    own_name,
    pair_of,
)
from ..p3hits import Plan, Step, Walk
from ..travel import KEY_STEP
from .spare import HOST_NO_DAMAGE, NO_DAMAGE, POWER_SCALE, Spare, Spares

Pair = tuple[int, int]
Floats = NDArray[np.float64]
Prim = tuple[Floats, Floats, float]
"""A sphere or capsule in bind space: its two ends and its radius."""

FLOOR = 0.25
"""The overlap below which a donor record has no host record. The matches that hold overlap at
0.30 or more, measured (27 on 8 is 0.297); the best that does not is 0.12."""
SAMPLES = 100_000
"""Points the overlap of two volume sets is sampled at."""
MAX_RATE = 0xFFFF
REST = frozenset(p for ps in p3moveset.END.values() for p in ps)
"""The pairs a move ends in, to rest: no hand-over."""

Report = Callable[[Finding], None]


class Match(NamedTuple):
    """The host record a donor attack record plays as."""

    record: int
    """The donor's record."""
    host: int | None
    """None: no host record overlaps it enough."""
    overlap: float
    """How much of the two volume sets' union they share; 1.0 where the manifest chose."""
    given: bool = False
    """The manifest's `[brain.attacks]` chose it."""
    best: int | None = None
    """The host record that came closest."""


class Matcher:
    """Donor attack records against the host's, on the rig the port ships."""

    def __init__(self, m: Manifest, data: Data, hits: p3hits.Hits) -> None:
        assert m.brain is not None
        self.hits, self.brain = hits, m.brain
        species = m.port.host_species
        self.table = hitbox.primary_table(hitbox.tables(data.fu.overlay(em_overlay(species))))
        d, h = build.donor(m, data), build.host(m, data)
        rec = build.record_map(d, m.build)
        animated = build.animated(m.build, rec, len(d.skeleton.bones))
        self.binding = build.binding(m.build, d, h, animated)
        self.bind = fk.Rig.from_skeleton(self.binding.rig.skeleton).bind_joints
        self._host = {i: p for i in self._own_ids(species) if (p := self._host_prims(i))}
        self._memo: dict[int, Match] = {}

    def _own_ids(self, species: int) -> range:
        """The host's own records: from 1 to where another species' records start in a table
        that serves several (`hitbox.ID_OFFSETS`), else to the table's end."""
        count = 0 if self.table is None else len(self.table.attacks)
        starts = [o for o in hitbox.ID_OFFSETS.get(species, {}).values() if o > 0]
        return range(1, min(starts, default=count))

    def attack(self, host: int) -> hitbox.Attack | None:
        """The host's record `host`."""
        t = self.table
        return t.attacks[host] if t is not None and 0 <= host < len(t.attacks) else None

    def match(self, record: int) -> Match:
        if record not in self._memo:
            self._memo[record] = self._match(record)
        return self._memo[record]

    def _match(self, record: int) -> Match:
        if (given := self.brain.attacks.get(str(record))) is not None:
            return Match(record, given, 1.0, True, given)
        donor = self.hits.records[record] if 0 <= record < len(self.hits.records) else None
        prims = self._donor_prims(record)
        if donor is None or not prims:
            return Match(record, None, 0.0)
        rows = []
        for host, theirs in self._host.items():
            a = self.attack(host)
            assert a is not None
            same_kind = (donor.kind == NO_DAMAGE) == (a.kind == HOST_NO_DAMAGE)
            family = same_kind and (donor.power == 0) == (a.power == 0)
            rows.append(
                (not family, -round(overlap(prims, theirs), 2), abs(a.power - donor.power), host)
            )
        if not rows:
            return Match(record, None, 0.0)
        rows.sort()
        host = rows[0][3]
        best = overlap(prims, self._host[host])
        return Match(record, host if best >= FLOOR else None, best, False, host)

    def _donor_prims(self, record: int) -> list[Prim]:
        out = []
        for v in self.hits.volumes(record):
            if v.bone in hitzone.MARKER_BONES or v.radius <= 0:
                continue
            j = self.binding.joint_of.get(v.bone)
            if j is None:
                continue
            p = self.bind[j] + np.asarray(v.offset)
            q = p
            if v.shape == "capsule":
                q = self.bind[j] + np.asarray(v.to)
            elif v.shape == "capsule2" and (j2 := self.binding.joint_of.get(v.bone2)) is not None:
                q = self.bind[j2] + np.asarray(v.to)
            out.append((p, q, v.radius))
        return out

    def _host_prims(self, host: int) -> list[Prim]:
        found = None if self.table is None else self.table.volume_for(host)
        out = []
        for v in [] if found is None else found.volumes:
            if v.bone in hitzone.MARKER_BONES or v.radius <= 0 or v.bone >= len(self.bind):
                continue
            p = self.bind[v.bone] + np.asarray(v.offset)
            out.append((p, self.bind[v.bone] + np.asarray(v.far) if v.capsule else p, v.radius))
        return out


def _inside(points: Floats, prims: Sequence[Prim]) -> NDArray[np.bool_]:
    out = np.zeros(len(points), bool)
    for p, q, r in prims:
        d = q - p
        length = float(d @ d)
        t = np.zeros(len(points)) if length == 0 else np.clip((points - p) @ d / length, 0, 1)
        out |= np.linalg.norm(points - (p + t[:, None] * d), axis=1) <= r
    return out


def overlap(a: Sequence[Prim], b: Sequence[Prim]) -> float:
    """How much of the union of two sets of spheres and capsules both hold, by sampling their
    bounding box with a fixed seed."""
    if not a or not b:
        return 0.0
    both = [*a, *b]
    lo = np.min([np.minimum(p, q) - r for p, q, r in both], axis=0)
    hi = np.max([np.maximum(p, q) + r for p, q, r in both], axis=0)
    points = lo + np.random.default_rng(0).random((SAMPLES, 3)) * (hi - lo)
    ia, ib = _inside(points, a), _inside(points, b)
    union = int((ia | ib).sum())
    return float((ia & ib).sum() / union) if union else 0.0


# --- the moves ---


class Hand(NamedTuple):
    """Where a complete handler hands over to, outside the rest pairs."""

    name: str | None = None
    """The own move that pair plays; None: not followed."""
    make: Pair | None = None
    """The donor pair whose derivation makes `name`; None for a move the manifest authors."""
    text: str = ""
    """The finding when it is not followed."""


class Derivation:
    """One pair's plan turned into moves and findings."""

    def __init__(
        self,
        m: Manifest,
        walk: Walk,
        matcher: Callable[[], Matcher],
        say: Report,
        pairs: Sequence[Pair] = (),
        spare: Callable[[], list[int]] = list,
    ) -> None:
        self.m, self.walk, self.matcher, self.say, self.spare = m, walk, matcher, say, spare
        self.pairs = list(pairs)
        """The pairs being derived; the pairs they hand over to are derived too, and with them
        feed the spare records' power scale, which reads all their matches."""
        self.pair: Pair = (0, 0)
        self.item = ""
        """The move findings are about."""
        self.made: list[str] = []
        """The moves derived, in order."""
        self._authored = frozenset(m.moves)
        self._done: set[Pair] = set()
        self._plans: dict[Pair, Plan] = {}
        self._spares: Spares | None = None
        self._near: dict[int, tuple[str, str]] = {}
        """Spare donor record -> what it came closest to, and the move that first needed it."""

    def find(self, level: str, code: str, text: str) -> None:
        self.say(Finding(level, code, text, None, self.item))

    def _plan(self, pair: Pair) -> Plan | None:
        donor = self.walk.ms.pairs.get(pair)
        if donor is None:
            return None
        if pair not in self._plans:
            self._plans[pair] = self.walk.plan(donor)
        return self._plans[pair]

    def run(self, pair: Pair) -> None:
        """Derive `pair`'s moves, and those of the pairs its handler hands over to; once each."""
        if pair in self._done:
            return
        self._done.add(pair)
        head = own_name(pair)
        self.pair, self.item = pair, head
        plan = self._plan(pair)
        if plan is None:
            self.find("error", "P3_OWN_NOCLIPS", "the donor has no such pair")
            return
        self._notes(plan)
        if not plan.steps:
            self.find("error", "P3_OWN_NOCLIPS", "its handler plays no clip")
            return
        names = [head, *(f"{head}_{k}" for k in range(2, len(plan.steps) + 1))]
        made = []
        for k, step in enumerate(plan.steps):
            if names[k] in self.m.moves:
                continue
            self.item = names[k]
            after = names[k + 1] if k + 1 < len(names) else None
            self.m.moves[names[k]] = self._move(step, after)
            made.append(names[k])
        self.made += made
        if names[-1] in made:
            self._follow(pair, plan, names[-1])

    def _follow(self, pair: Pair, plan: Plan, last: str) -> None:
        """Hand `last` over to the move of the pair the handler ends into. Linked before that
        pair is derived, so a pair that leads back sees the ring and is the one cut."""
        hand = self._hand(plan)
        if hand is None:
            return
        name, text = hand.name, hand.text
        if name is not None:
            target = self.m.moves.get(name)
            if target is not None and not target.own:
                text = f"{self._lead(plan)}: moves.{name} is no own move"
            elif last in sequence.chain(self.m, name):
                text = f"{self._lead(plan)}: following it would close a ring"
        if name is None or text:
            self.pair, self.item = pair, own_name(pair)
            self.find("info", "P3_OWN_NEXT", text)
            return
        self.m.moves[last].after = name
        if hand.make is not None:
            self.run(hand.make)

    @staticmethod
    def _lead(plan: Plan) -> str:
        over = [f"{a},{b}" for a, b in plan.leaves if (a, b) not in REST]
        return f"it hands over to pair{'s' if len(over) > 1 else ''} {', '.join(over)}"

    def _hand(self, plan: Plan) -> Hand | None:
        """What a complete plan hands over to; None when it ends at rest or did not end."""
        over = [p for p in plan.leaves if p not in REST]
        if not plan.complete or not over:
            return None
        if len(plan.leaves) > 1:
            ends = ", ".join(f"{a},{b}" for a, b in plan.leaves)
            return Hand(text=f"it hands over to one of pairs {ends}, as the run goes: not followed")
        lead = self._lead(plan)
        (to,) = over
        brain = self.m.brain
        plays = [] if brain is None else brain.targets(to)
        if plays and not (len(plays) == 1 and isinstance(plays[0], str)):
            what = f"host pair {plays[0][0]},{plays[0][1]}" if len(plays) == 1 else "several moves"
            return Hand(text=f"{lead}, which [brain.pairs] plays as {what}: not followed")
        name = plays[0] if plays else own_name(to)
        assert isinstance(name, str)
        if name in self._authored:
            return Hand(name)
        make = brain.derived().get(name) if brain is not None else None
        make = make or (to if name == own_name(to) else None)
        if make is None:
            return Hand(text=f"{lead}, which the port does not play: no pair derives {name}")
        if why := self._unplayable(make):
            return Hand(text=f"{lead}, which the port does not play: {why}")
        return Hand(name, make)

    def _unplayable(self, pair: Pair) -> str:
        """Why `pair` yields no moves a hand-over can go on into; empty when it does."""
        plan = self._plan(pair)
        if plan is None:
            return "the donor has no such pair"
        if not plan.steps:
            return "its handler plays no clip"
        if lacking := [s.clip for s in plan.steps if s.clip not in self.walk.lengths]:
            return f"it plays clip {lacking[0]}, which the donor lacks"
        if plan.complete:
            return ""
        why = next((n.text for n in plan.notes if n.code == "unresolved"), "")
        return "the walk gave up before its handler ended" + (f" ({why})" if why else "")

    def _reach(self, start: Sequence[Pair]) -> list[Pair]:
        """`start` and the pairs derived to follow them, in order."""
        out = list(dict.fromkeys(start))
        todo = list(out)
        while todo:
            plan = self._plan(todo.pop(0))
            hand = None if plan is None else self._hand(plan)
            if hand is not None and hand.make is not None and hand.make not in out:
                out.append(hand.make)
                todo.append(hand.make)
        return out

    def finish(self) -> None:
        """One `P3_OWN_SPARE` per spare record taken, once every pair has had its say: the
        record's final active frames are known only then."""
        for record, (near, item) in self._near.items():
            got = self._spares.take(record) if self._spares is not None else None
            if isinstance(got, Spare):
                a = got.attack
                joints = ", ".join(map(str, sorted(set(got.joints))))
                self.item = item
                self.find(
                    "info",
                    "P3_OWN_SPARE",
                    f"donor record {record} has no host record{near}: spare record {got.id} on "
                    f"set {got.set} ({got.volumes} volume(s) on joint(s) {joints}), power "
                    f"{a.power}, {got.family}, active {a.active} frames",
                )

    def _notes(self, plan: Plan) -> None:
        codes = {
            "branch": ("info", "P3_OWN_BRANCH"),
            "record": ("info", "P3_OWN_BRANCH"),
            "limit": ("info", "P3_OWN_BRANCH"),
            "motion": ("info", "P3_OWN_MOTION"),
            "unresolved": ("warning", "P3_OWN_PARTIAL"),
        }
        for n in plan.notes:
            level, code = codes[n.code]
            self.find(level, code, n.text)
        if plan.alternatives:
            paths = "; ".join(" ".join(map(str, c)) or "none" for c in plan.alternatives)
            self.find(
                "info", "P3_OWN_BRANCH", f"other paths through its branches play clips {paths}"
            )
        if not plan.complete and plan.steps:
            self.find("warning", "P3_OWN_PARTIAL", "the walk gave up before the handler ended")

    def _clip(self, cid: int) -> str:
        held = layout.holder(self.m, cid)
        if held is not None:
            return held
        name = f"p3_{cid}"
        while name in self.m.clips:
            name += "_"
        self.m.clips[name] = Clip(source=cid)
        return name

    def _move(self, step: Step, after: str | None) -> Move:
        """The step as a move, from the clip frame the donor started it at to the one it ended
        at (`_stop`); `length`, in AI frames, only for a step on a looping clip."""
        length = self.walk.lengths[step.clip]
        frames, start = round(length.frames), round(step.start)
        air = self._air(step, start, frames)
        # a lifted copy is the source cut at `start`: the move counts its frames from there
        off, size = (start, len(step.lift) - 1) if air else (0, frames)
        stop = self._stop(step, length.loop and not air, start, frames, off if air else None)
        played = (start - off, stop - off if stop else size)  # the clip frames it plays
        return Move(
            clip=air or self._clip(step.clip),
            attacks=self._attacks(step, start, stop or frames, off),
            steer=self._steer(step, length.loop and not step.lift, off, played),
            length=max(1, step.ticks) if length.loop and not air and not stop else None,
            start=start - off,
            stop=stop - off if stop else 0,
            travel=self._travel(step, size, start - off),
            lunge=self._lunge(step, off, played),
            after=after,
            label=f"MHP3rd {self.pair[0]},{self.pair[1]}",
        )

    def _stop(self, step: Step, loop: bool, start: int, frames: int, lifted: int | None) -> int:
        """The clip frame a step ends at, 0 for its clip's end: where the handler's test of the
        cursor was crossed, else where the walk's cursor stood when it handed over (the engine
        holds the cursor through a blend, so AI frames would end it early). A lifted copy
        (`lifted`: its first clip frame) also ends where its height last is at rest or above, so
        the landing clip takes the fall. A loop has none: it ends by `length`."""
        stop = round(step.stop) if step.stop is not None else 0
        if not start < stop <= frames:
            stop = 0
        if not stop and not loop and step.ended != "clip":
            stop = min(round(start + step.ticks * self.walk.step), frames)
        if lifted is not None:
            last = max((i for i, h in enumerate(step.lift) if h >= 0), default=len(step.lift) - 1)
            if last < len(step.lift) - 1:
                stop = min(stop or frames, lifted + last)
        return stop if start < stop else 0

    def _air(self, step: Step, start: int, frames: int) -> str | None:
        """The name of the one-shot copy of the step's clip cut to the frames it plays, which
        carries its height over rest (`Clip.lift`) in an entry of its own; None where it has no
        height, or the copy cannot be made."""
        if not step.lift:
            return None
        n = len(step.lift) - 1
        if start + n > frames:
            self.find(
                "info",
                "P3_OWN_AIR",
                f"clip {step.clip} is {frames} frames and the step plays {n} from {start}: its "
                "height is left out",
            )
            return None
        lift = list(step.lift)
        for name, c in self.m.clips.items():
            if (c.source, c.start, c.frames, c.lift) == (step.clip, start, n, lift):
                return name
        taken = {c.slot for c in self.m.clips.values() if c.lift is not None}
        free = [e for e in self.spare() if e not in taken]
        if not free:
            self.find(
                "error",
                "P3_OWN_AIR",
                f"no free executor entry for the lifted copy of clip {step.clip}",
            )
            return None
        name = f"p3_{step.clip}_air"
        while name in self.m.clips:
            name += "_"
        self.m.clips[name] = Clip(slot=free[0], source=step.clip, start=start, frames=n, lift=lift)
        peak = max(lift, key=abs)
        self.find(
            "info",
            "P3_OWN_AIR",
            f"clip {step.clip} frames {start} to {start + n} carry a height of {peak:+.0f} over "
            f"rest, as clips.{name} in entry {free[0]}",
        )
        return name

    def _travel(self, step: Step, frames: int, start: int) -> list[tuple[float, float]] | None:
        """The step's travel by code as samples from the move's `start`, those past the clip's
        `frames` and a held tail left off."""
        found = list(step.travel)
        room = (frames - start) // KEY_STEP + 1
        if len(found) > room:
            self.find(
                "info", "P3_OWN_MOTION", f"its travel outruns its clip: kept to {room} samples"
            )
            found = found[:room]
        while len(found) > 2 and found[-1] == found[-2]:
            found.pop()
        return found or None

    def _inside(
        self, what: str, window: tuple[float, float], off: int, played: tuple[int, int]
    ) -> tuple[int, int, float] | None:
        """`window` counted from clip frame `off`, cut to the frames `played`, and the share of
        it that is left; None, said, where none is."""
        lo, hi = round(window[0]) - off, round(window[1]) - off
        a, b = max(lo, played[0]), min(hi, played[1])
        if hi <= lo or b <= a:
            self.find(
                "info", "P3_OWN_MOTION", f"{what} falls outside the frames it plays: left out"
            )
            return None
        if (a, b) != (lo, hi):
            share = (b - a) / (hi - lo)
            self.find(
                "info", "P3_OWN_MOTION", f"{what} is cut to the frames it plays ({share:.0%})"
            )
        return a, b, (b - a) / (hi - lo)

    def _lunge(self, step: Step, off: int, played: tuple[int, int]) -> Lunge | None:
        """The step's lunge, its window counted from the move's first clip frame and cut to the
        frames the move plays, its length by the share of it that is left."""
        got = step.lunge
        inside = None if got is None else self._inside("a lunge", got.window, off, played)
        if got is None or inside is None:
            return None
        lo, hi, share = inside
        return Lunge((lo, hi), got.near, got.span, got.base * share, got.range * share)

    def _attacks(self, step: Step, start: int, until: int, off: int = 0) -> list[AttackWindow]:
        """The step's attack windows inside `[start, until]`, counted from clip frame `off`."""
        out: list[AttackWindow] = []
        for h in sorted(step.hits, key=lambda h: (h.frame, h.slot)):
            frame = max(start, round(h.frame))
            end = round(until if h.end is None else min(h.end, until))
            if end <= frame:
                continue
            if h.carried:
                self.find("info", "P3_OWN_SPLIT", f"record {h.record} spans clips: split per clip")
            match = self.matcher().match(h.record)
            host, life = match.host, None
            if host is None:
                spare = self._spare(h.record, match, end - frame)
                if spare is None:
                    continue
                host, life = spare.id, spare.attack.active
            elif (active := self.matcher().attack(host)) is not None:
                life = active.unknown_01
            if life is not None and end - frame > life > 0:
                self.find(
                    "info",
                    "P3_OWN_ACTIVE",
                    f"record {h.record} is active {end - frame} frames, host record {host} {life}",
                )
            out.append(AttackWindow(host, frame - off, end - off, label=f"donor {h.record}"))
        opens = [h for h in step.hits if h.flags & p3hits.FLAG_OPENS]
        joins = [h for h in step.hits if h.flags & p3hits.FLAG_JOINS]
        if opens and joins:
            both = ", ".join(str(h.record) for h in (*opens, *joins))
            self.find(
                "info", "P3_OWN_GROUP", f"records {both} land one hit between them in the donor"
            )
        if len(out) > MOVE_ATTACKS:
            self.find(
                "warning",
                "P3_OWN_ATTACK",
                f"{len(out)} attacks on one clip, the move player holds {MOVE_ATTACKS}: "
                f"the last {len(out) - MOVE_ATTACKS} are left out",
            )
            out = out[:MOVE_ATTACKS]
        return out

    def _spare(self, record: int, match: Match, frames: int) -> Spare | None:
        """The spare host record for a donor record nothing matches; None, with the warning,
        when there is none."""
        got = self._pool().take(record, frames)
        near = ""
        if match.best is not None:
            near = f" (closest {match.best}, overlap {match.overlap:.2f})"
        if isinstance(got, str):
            self.find(
                "warning",
                "P3_OWN_ATTACK",
                f"donor record {record} has no host record{near} and no spare ({got}): left out",
            )
            return None
        self._near.setdefault(record, (near, self.item))
        return got

    def _pool(self) -> Spares:
        if self._spares is None:
            mt = self.matcher()
            self._spares = Spares(
                self.m, mt.hits, mt.table, mt.binding.joint_of, mt.bind, self._scale()
            )
        return self._spares

    def _scale(self) -> float:
        """Host power over donor power for a spare record: the median over the records of this
        port's pairs that matched (the ones `[brain.attacks]` chose left out)."""
        mt = self.matcher()
        plans = (self._plan(p) for p in self._reach([*self.pairs, self.pair]))
        used = {h.record for plan in plans if plan for s in plan.steps for h in s.hits}
        ratios = []
        for r in sorted(used):
            got = mt.match(r)
            host = None if got.host is None or got.given else mt.attack(got.host)
            donor = self.walk.hits.records[r] if 0 <= r < len(self.walk.hits.records) else None
            if host is not None and donor is not None and host.power and donor.power:
                ratios.append(host.power / donor.power)
        return statistics.median(ratios) if ratios else POWER_SCALE

    def _steer(
        self, step: Step, walls: bool, off: int = 0, played: tuple[int, int] = (0, 0xFFFF)
    ) -> Steer:
        """The donor's turn, its windows counted from clip frame `off`: a fixed one where the
        handler spreads a total over a window, else the longest toward the target. A wall ends
        only a step on a looping clip that is no jump, a run (the donor ends no move at a wall: a
        one-shot stop, blow or leap plays out where it stands)."""
        if step.spreads:
            return self._spread(step, walls, off, played)
        if not step.turns:
            return Steer(walls=walls)
        turn = max(step.turns, key=lambda t: t.window[1] - t.window[0])
        if len(step.turns) > 1:
            self.find(
                "info",
                "P3_OWN_MOTION",
                f"{len(step.turns)} turn rates on one clip: kept the longest",
            )
        rate = turn.rate
        if rate is None:
            self.find("info", "P3_OWN_MOTION", "its turn rate is not known: the host's")
        elif not 1 <= rate <= MAX_RATE:
            self.find("info", "P3_OWN_MOTION", f"turn rate {rate} is out of range: the host's")
            rate = None
        lo = max(round(turn.window[0]) - off, 0)
        window = (lo, max(lo + 1, round(turn.window[1]) - off))
        return Steer(turn="hunter", rate=rate, walls=walls, window=window)

    def _spread(self, step: Step, walls: bool, off: int, played: tuple[int, int]) -> Steer:
        """The turn of a fixed total across a window: the longest of the step's, if it has more
        than one."""
        spread = max(step.spreads, key=lambda s: s.window[1] - s.window[0])
        if len(step.spreads) > 1:
            self.find("info", "P3_OWN_MOTION", f"{len(step.spreads)} fixed turns: kept the longest")
        if step.turns:
            self.find(
                "info", "P3_OWN_MOTION", "a fixed turn and a turn toward the hunter: kept the fixed"
            )
        inside = self._inside("a fixed turn", spread.window, off, played)
        if inside is None or not -p3motion.TURN < spread.total < p3motion.TURN:
            if inside is not None:
                self.find("info", "P3_OWN_MOTION", f"a turn of {spread.total} units is left out")
            return Steer(walls=walls)
        lo, hi, share = inside
        return Steer(turn="fixed", angle=spread.degrees * share, walls=walls, window=(lo, hi))


def pairs(b: Brain) -> list[Pair]:
    """The donor pairs whose own move the build derives, in order."""
    return sorted(b.derived().values())


def anchored(m: Manifest, derived: Collection[str]) -> set[str]:
    """The derived moves something outside the derived set names: `[brain.pairs]`, the behaviour
    graph, an effect, or the `after` of a move that is not derived."""
    named: set[str] = set()
    if m.brain is not None:
        for key in m.brain.pairs:
            named.update(t for t in m.brain.targets(pair_of(key)) if isinstance(t, str))
    for blk in m.behaviour.blocks.values():
        named.update(blk.play)
    named.update(m.behaviour.moves)
    named.update(e.move for e in m.effects)
    named.update(mv.after for n, mv in m.moves.items() if mv.after and n not in derived)
    return named & set(derived)


def share(m: Manifest, derived: Iterable[str]) -> dict[str, str]:
    """Merge the derived moves equal in every field but `label` (their `after` compared as this
    pass renames it) until none are: equal tails make their predecessors equal. The one kept is an
    anchored move, else the first derived; two anchored moves stay apart. Every `after` that
    named a dropped move names the kept one. Returns each dropped move's kept one."""
    names = list(dict.fromkeys(derived))
    keep = anchored(m, names)
    gone: dict[str, str] = {}

    def now(name: str) -> str:
        while name in gone:
            name = gone[name]
        return name

    while True:
        groups: list[tuple[Move, list[str]]] = []
        for n in (n for n in names if n not in gone):
            mv = m.moves[n]
            key = dataclasses.replace(mv, label="", after=mv.after and now(mv.after))
            for first, same in groups:
                if first == key:
                    same.append(n)
                    break
            else:
                groups.append((key, [n]))
        merged = False
        for _, same in groups:
            kept = next((n for n in same if n in keep), same[0])
            for n in same:
                if n != kept and n not in keep:
                    gone[n] = kept
                    merged = True
        if not merged:
            break
    for mv in m.moves.values():
        if mv.after in gone:
            mv.after = now(mv.after)
    for n in gone:
        del m.moves[n]
    return {n: now(n) for n in gone}


def expand(m: Manifest, data: Data) -> tuple[Manifest, list[Finding]]:
    """`m` with the own moves its `[brain.pairs]` derive and the clips they play; `m` itself
    where it derives none. A move already in `m.moves` under a derived name is kept."""
    wanted = [] if m.brain is None else [p for p in pairs(m.brain) if own_name(p) not in m.moves]
    if not wanted:
        return m, []
    assert m.brain is not None
    found: list[Finding] = []
    out = copy.deepcopy(m)
    walk = Walk.of(data.p3rd, m.brain.donor, data.p3rd.read(m.source.anim), m.brain.slot)
    cache: list[Matcher] = []

    def matcher() -> Matcher:
        if not cache:
            cache.append(Matcher(m, data, walk.hits))
        return cache[0]

    hosted: list[fu.Anim] = []

    def spare() -> list[int]:
        if not hosted:
            hosted.append(build.host(m, data).anim)
        return layout.spare(out.clips, walk.lengths, hosted[0], m.port.host_species)

    derive = Derivation(out, walk, matcher, found.append, wanted, spare)
    for pair in wanted:
        derive.run(pair)
    derive.finish()
    dropped = share(out, derive.made)
    found[:] = dict.fromkeys(
        dataclasses.replace(f, item=dropped.get(f.item or "", f.item)) for f in found
    )
    species = m.port.host_species
    if (
        any(mv.own and mv.carrier is None for mv in out.moves.values())
        and moves.hub(species) is None
    ):
        found.append(
            Finding("error", "P3_OWN_CARRIER", f"no carrier is known for host species {species}")
        )
    if (n := sum(mv.own for mv in out.moves.values())) > OWN_MOVES:
        found.append(
            Finding("error", "P3_OWN_SLOTS", f"{n} own moves, the framework holds {OWN_MOVES}")
        )
    return out, found


def expanded(m: Manifest, data: Data, report: Report | None = None) -> Manifest:
    """`expand`, each finding handed to `report`; `ManifestError` for the first error."""
    out, found = expand(m, data)
    for f in found:
        if report is not None:
            report(f)
    errors = [f for f in found if f.level == "error"]
    if errors:
        raise ManifestError(f"{errors[0].code}: {errors[0].text}")
    return out
