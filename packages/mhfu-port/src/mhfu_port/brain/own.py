# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The own moves a manifest's `[brain.pairs] "7,13" = "own"` stands for, derived at each build
from both games' files: the clips the donor's handler plays, from the clip frame it starts them
at to the one it ends them at, the attacks it spawns with their frames, matched to the host
species' attack records, and how it turns.

    m, findings = own.expand(manifest, data)    # the manifest with `p3_7_13` and its clips

`expand` is the one place derived moves enter a port. Every reader of a port's moves takes its
result: the moves module (`mhfu-port build`, `inject`, the studio's deploy) and `mhfu-port splice`
(the slots the script's `enter 16 k 0` names). A move of the same name in `[moves]` is used
instead; nothing derived is stored.

A pair with several clips is a chain, `p3_7_14`, `p3_7_14_2`, ..., each step handing to the next
through `after`; a step carries the donor's attacks that are active on its clip. A donor record
becomes the host record whose volumes overlap it most on the port's rig (`[brain.attacks]` names
one instead), and the node plays with the host record's power and life. A record nothing matches
gets a spare host record (`brain.spare`), added to the returned manifest as `[[attack]]` and
`[[hitbox]]`; the studio's hit tables write it into the game.
"""

from __future__ import annotations

import copy
import statistics
from collections.abc import Callable, Sequence
from typing import NamedTuple

import numpy as np
from mhfu import hitbox, hitzone
from mhfu.em.script.check import Finding
from mhfu.files import em_overlay
from numpy.typing import NDArray

from .. import build, fk, layout, moves, p3hits, p3moveset
from ..data import Data
from ..manifest import (
    MOVE_ATTACKS,
    OWN_MOVES,
    AttackWindow,
    Brain,
    Clip,
    Manifest,
    ManifestError,
    Move,
    Steer,
    own_name,
)
from ..p3hits import Plan, Step, Walk
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


class Derivation:
    """One pair's plan turned into moves and findings."""

    def __init__(
        self,
        m: Manifest,
        walk: Walk,
        matcher: Callable[[], Matcher],
        say: Report,
        pairs: Sequence[Pair] = (),
    ) -> None:
        self.m, self.walk, self.matcher, self.say = m, walk, matcher, say
        self.pairs = list(pairs)
        """Every pair being derived: the spare records' power scale reads all their matches."""
        self.pair: Pair = (0, 0)
        self.item = ""
        """The move findings are about."""
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
        for k, step in enumerate(plan.steps):
            if names[k] in self.m.moves:
                continue
            self.item = names[k]
            after = names[k + 1] if k + 1 < len(names) else None
            self.m.moves[names[k]] = self._move(step, after)

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
        rest = {p for ps in p3moveset.END.values() for p in ps}
        if plan.complete and (over := [p for p in plan.leaves if p not in rest]):
            to = ", ".join(f"{a},{b}" for a, b in over)
            self.find("info", "P3_OWN_NEXT", f"it hands over to pair {to}, which the script enters")

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
        """The step as a move, from the clip frame the donor started it at to the one its handler
        tested when it ended it (`Step.stop`); `length`, in AI frames, only for a step that ended
        on no frame of its clip: a pair, a loop, a test the cursor never changed."""
        length = self.walk.lengths[step.clip]
        frames, start = round(length.frames), round(step.start)
        stop = round(step.stop) if step.stop is not None else 0
        stop = stop if start < stop <= frames else 0
        cut = length.loop or step.ended != "clip"
        return Move(
            clip=self._clip(step.clip),
            attacks=self._attacks(step, start, stop or frames),
            steer=self._steer(step, walls=length.loop),
            length=max(1, step.ticks) if cut and not stop else None,
            start=start,
            stop=stop,
            after=after,
            label=f"MHP3rd {self.pair[0]},{self.pair[1]}",
        )

    def _attacks(self, step: Step, start: int, until: int) -> list[AttackWindow]:
        """The step's attack windows inside `[start, until]`."""
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
            out.append(AttackWindow(host, frame, end, label=f"donor {h.record}"))
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
        plans = (self._plan(p) for p in dict.fromkeys([*self.pairs, self.pair]))
        used = {h.record for plan in plans if plan for s in plan.steps for h in s.hits}
        ratios = []
        for r in sorted(used):
            got = mt.match(r)
            host = None if got.host is None or got.given else mt.attack(got.host)
            donor = self.walk.hits.records[r] if 0 <= r < len(self.walk.hits.records) else None
            if host is not None and donor is not None and host.power and donor.power:
                ratios.append(host.power / donor.power)
        return statistics.median(ratios) if ratios else POWER_SCALE

    def _steer(self, step: Step, walls: bool) -> Steer:
        """The donor's turn; a wall ends only a step on a looping clip, a run (the donor ends no
        move at a wall: a one-shot stop or blow plays out where it stands)."""
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
        if rate is not None and not 1 <= rate <= MAX_RATE:
            self.find("info", "P3_OWN_MOTION", f"turn rate {rate} is out of range: the host's")
            rate = None
        lo = round(turn.window[0])
        window = (lo, max(lo + 1, round(turn.window[1])))
        return Steer(turn="hunter", rate=rate, walls=walls, window=window)


def pairs(b: Brain) -> list[Pair]:
    """The donor pairs whose own move the build derives, in order."""
    return sorted(b.derived().values())


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

    derive = Derivation(out, walk, matcher, found.append, wanted)
    for pair in wanted:
        derive.run(pair)
    derive.finish()
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
