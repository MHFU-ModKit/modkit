# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from types import SimpleNamespace

import numpy as np
from mhfu_port import manifest
from mhfu_port.brain import own, spare
from mhfu_port.manifest import AttackWindow
from mhfu_port.p3hits import Hit, Hits, Length, Plan, Record, Step, Volume

TEXT = """
schema = 2
[port]
name = "z"
host_species = 75
pac = "z.bin"
[source]
model = 5248
[brain]
donor = 23
splice = "base_3"
entry = "base_12"
[brain.pairs]
"7,13" = "own"
"""
RECORDS = 107
ROAR_KIND, WEAK_KIND = 29, 1


def host_attack(i: int, kinds: dict[int, int], sets: int) -> SimpleNamespace:
    """em75's record `i`: the species' own 1..32 on sets 0..17, the others on 18..55."""
    own_set = i % 18 if i < 33 else 18 + (i - 34) % (sets - 18)
    return SimpleNamespace(
        kind=kinds.get(i, 2),
        family=0x10 if kinds.get(i) == ROAR_KIND else 0,
        angle=0,
        half_0c=4096 if i == 38 else 0,
        volume_set=own_set,
        power=50,
        unknown_01=10,
    )


def table(sizes: dict[int, int] | None = None, records: int = RECORDS, **kinds: int):
    """A host table shaped like em75's: `sizes` the record count of a set (default 2), `kinds`
    the kind of records named `r<id>`."""
    sets = 56
    sized = {i: 2 for i in range(sets)} | (sizes or {})
    by_id = {int(k[1:]): v for k, v in kinds.items()}
    return SimpleNamespace(
        attacks=[host_attack(i, by_id, sets) for i in range(records)],
        volumes=[SimpleNamespace(volumes=[object()] * sized[i]) for i in range(sets)],
    )


def vol(bone: int, shape: str = "sphere", **kw) -> Volume:
    base = Volume(bone, 0, shape, 0, 130.0, (0.0, 1.0, 2.0), (0.0, 0.0, 0.0))
    return base._replace(**kw)


def donor(*records: Record, sets: tuple[tuple[Volume, ...], ...] = ((vol(5),),)) -> Hits:
    return Hits((Record(0, 0, 0, 0, 0, 0, 0), *records), sets)


def rec(i: int, power: int = 50, kind: int = 2, length: int = 10, volumes: int = 0) -> Record:
    return Record(i, 0, length, power, kind, 40, volumes)


def manifest_of(extra: str = "") -> manifest.Manifest:
    return manifest.loads(TEXT + extra)


BIND = np.array(
    [[0.0, 0.0, 0.0]] * 8 + [[0.0, 0.0, 0.0], [5.0, 6.0, 7.0]] + [[0.0, 100.0, 0.0]] * 4
)


def spares(m, hits, tbl=None, joint_of=None, scale=0.5) -> spare.Spares:
    return spare.Spares(m, hits, tbl or table(), joint_of or {5: 9, 6: 12}, BIND, scale)


def test_a_spare_takes_the_first_plain_record_and_a_fitting_set():
    m = manifest_of()
    s = spares(m, donor(rec(1, power=50, length=24)))
    got = s.take(1)
    assert isinstance(got, spare.Spare) and got.id == 34
    assert got.set == 18 and got.family == "hit" and got.joints == (9,)
    (a,) = m.attacks
    assert (a.id, a.power, a.kind, a.active, a.volume) == (34, 25, 2, 24, 18)
    (h,) = m.hitboxes
    assert (h.bone, h.radius, h.set, h.shape, h.offset, h.to) == (
        9,
        130.0,
        18,
        "sphere",
        [0.0, 1.0, 2.0],
        None,
    )
    manifest.check(m)
    assert s.take(1) is got and len(m.attacks) == 1, "one record, one spare"


def test_the_set_is_the_smallest_that_holds_the_volumes():
    m = manifest_of()
    three = (vol(5), vol(6), vol(5, offset=(9.0, 9.0, 9.0)))
    hits = donor(rec(1), rec(2, volumes=1), sets=((vol(5),), three))
    s = spares(m, hits, table({19: 3, 20: 3, 21: 7, 24: 10}))
    one, two = s.take(1), s.take(2)
    assert isinstance(one, spare.Spare) and isinstance(two, spare.Spare)
    assert (one.set, two.set) == (18, 19), "2 holds one volume; the first of size 3 holds three"
    assert [h.set for h in m.hitboxes] == [18, 19, 19, 19]


def test_records_on_one_donor_set_share_the_host_set():
    m = manifest_of()
    s = spares(m, donor(rec(1), rec(2)))
    one, two = s.take(1), s.take(2)
    assert isinstance(one, spare.Spare) and isinstance(two, spare.Spare)
    assert one.set == two.set and one.id != two.id and len(m.hitboxes) == 1


def test_a_donor_set_too_big_for_any_free_set():
    hits = donor(rec(1), sets=(tuple(vol(5) for _ in range(4)),))
    got = spares(manifest_of(), hits, table(dict.fromkeys(range(18, 56), 3))).take(1)
    assert got == "4 volume(s), the largest free set holds 3"


def test_authored_and_chosen_ids_and_sets_are_skipped():
    m = manifest_of(
        "[[attack]]\nid = 34\npower = 1\n[[hitbox]]\nbone = 1\nradius = 1.0\nset = 18\n"
        "[brain.attacks]\n'7' = 35\n"
    )
    got = spares(m, donor(rec(1))).take(1)
    assert isinstance(got, spare.Spare) and (got.id, got.set) == (36, 19)


def test_the_ruled_ids_are_never_handed_out():
    recs = [rec(i) for i in range(1, 70)]
    s = spares(manifest_of(), donor(*recs))
    got = [s.take(r.id) for r in recs]
    ids = [g.id for g in got if isinstance(g, spare.Spare)]
    assert len(ids) == len(set(ids)) == 68, "34..106 but 70 and the four ruled ids"
    assert not set(ids) & {*spare.RULED[75], 33, 70}
    assert (min(ids), max(ids)) == (34, 106)
    assert got[-1] == "the host's table has no spare record"


def test_none_left():
    s = spares(manifest_of(), donor(rec(1), rec(2)), table(records=36))
    assert isinstance(s.take(1), spare.Spare) and isinstance(s.take(2), spare.Spare)
    none = spares(manifest_of(), donor(rec(1)), table(records=34))
    assert none.take(1) == "the host's table has no spare record"


def test_kind_by_family_and_the_record_that_has_it():
    m = manifest_of()
    hits = donor(
        rec(1, power=0, kind=spare.NO_DAMAGE, length=10),
        rec(2, power=15, kind=spare.WEAK),
        rec(3, power=60, kind=31),
        sets=((vol(5),),),
    )
    s = spares(m, hits, table(r44=ROAR_KIND, r45=WEAK_KIND, r46=WEAK_KIND))
    roar, weak, hit = s.take(1), s.take(2), s.take(3)
    assert isinstance(roar, spare.Spare) and isinstance(weak, spare.Spare)
    assert isinstance(hit, spare.Spare)
    assert (roar.family, roar.id) == ("roar", 44)
    assert (weak.family, weak.id) == ("weak", 45)
    assert (hit.family, hit.id) == ("hit", 34)
    kinds = {a.id: (a.kind, a.power) for a in m.attacks}
    assert kinds == {44: (spare.HOST_NO_DAMAGE, 0), 45: (1, 8), 34: (2, 30)}


def test_the_plainest_record_of_the_kind_wins_over_a_lower_id():
    s = spares(manifest_of(), donor(rec(1)), table(r34=3, r35=3, r36=3, r37=3))
    got = s.take(1)
    # 38 is a hit but carries +0x0C, 39 and 40 are ruled: 41 is the first plain one
    assert isinstance(got, spare.Spare) and got.id == 41


def test_active_frames_grow_to_cover_a_move_and_stop_at_a_byte():
    m = manifest_of()
    s = spares(m, donor(rec(1, length=10)))
    s.take(1, 6)
    assert m.attacks[0].active == 10
    s.take(1, 59)
    assert m.attacks[0].active == 59
    s.take(1, 600)
    assert m.attacks[0].active == spare.MAX_ACTIVE


def test_volumes_land_on_the_ports_joints():
    m = manifest_of()
    vols = (
        vol(5),
        vol(6, "capsule", to=(0.0, 0.0, 50.0)),
        vol(5, "capsule2", bone2=6, to=(10.0, 0.0, 0.0), radius=77.0),
        vol(5, "capsule2", bone2=99, to=(10.0, 0.0, 0.0)),
        vol(99),
        vol(5, radius=0.0),
        vol(125),
    )
    s = spares(m, donor(rec(1), sets=(vols,)), table({18: 6}), joint_of={5: 9, 6: 12, 125: 3})
    got = s.take(1)
    assert isinstance(got, spare.Spare) and got.volumes == 4
    cap, across, short = m.hitboxes[1:]
    assert (m.hitboxes[0].bone, m.hitboxes[0].shape) == (9, "sphere")
    assert (cap.bone, cap.shape, cap.to) == (12, "capsule", [0.0, 0.0, 50.0])
    assert (across.bone, across.shape, across.radius) == (9, "capsule", 77.0)
    assert across.to == [5.0, 94.0, -7.0]  # bone 12 at (0, 100, 0) + to, from bone 9 at (5, 6, 7)
    assert short.shape == "sphere" and short.to is None, "a second bone off the rig: a sphere"


def test_no_volume_on_the_rig_or_no_such_record():
    m = manifest_of()
    s = spares(m, donor(rec(1), sets=((vol(99),),)))
    assert s.take(1) == "none of its volumes lands on the port's rig"
    assert s.take(40) == "the donor has no such record"
    assert not m.attacks and not m.hitboxes, "nothing is kept for a refusal"
    assert spare.Spares(m, None, None, {}, BIND).take(1) == "the host has no attack table"


def test_a_species_without_spare_records():
    m = manifest_of()
    m.port.host_species = 7
    assert spares(m, donor(rec(1))).take(1) == "the host's table has no spare record"


def test_family():
    assert spare.family(rec(1, power=0, kind=2)) == "roar"
    assert spare.family(rec(1, power=9, kind=spare.NO_DAMAGE)) == "roar"
    assert spare.family(rec(1, power=9, kind=1)) == "weak"
    assert spare.family(rec(1, power=9, kind=31)) == "hit"


# --- derived through a pair ---


class Stub:
    """A walk and a matcher in one: every donor record in `hosts` matches, the rest do not."""

    def __init__(self, plan: Plan, hits: Hits, hosts: dict[int, int], tbl=None) -> None:
        self.lengths = {61: Length(382.0)}
        self.ms = SimpleNamespace(pairs={plan.pair: object()})
        self._plan, self.hits, self.hosts = plan, hits, hosts
        self.table = tbl or table()
        self.binding = SimpleNamespace(joint_of={5: 9, 6: 12})
        self.bind = BIND

    def plan(self, pair: object) -> Plan:
        return self._plan

    def match(self, record: int) -> own.Match:
        host = self.hosts.get(record)
        return own.Match(record, host, 0.5 if host is not None else 0.12, False, 25)

    def attack(self, host: int) -> object:
        return self.table.attacks[host]


def derive(hits: Hits, hosts: dict[int, int], spawns: list[Hit], tbl=None):
    m = manifest_of("[clips.charge]\nsource = 61\n")
    plan = Plan((7, 13), (Step(61, 4, 0.0, "clip", 191, tuple(spawns), ()),), ((0, 0),), True)
    found: list = []
    stub = Stub(plan, hits, hosts, tbl)
    d = own.Derivation(m, stub, lambda: stub, found.append, [(7, 13)])  # type: ignore[arg-type]
    d.run((7, 13))
    d.finish()
    return m, found


def test_a_donor_record_without_a_match_plays_its_spare():
    hits = donor(rec(1, power=40), rec(2, power=100), rec(3, power=60, length=40))
    spawns = [Hit(0, 1, 0, 135.0, 165.0), Hit(1, 2, 0, 135.0, 165.0), Hit(0, 3, 0, 140.0, 170.0)]
    m, found = derive(hits, {1: 7, 2: 8}, spawns)
    attacks = m.moves["p3_7_13"].attacks
    assert attacks == [
        AttackWindow(7, 135, 165, label="donor 1"),
        AttackWindow(8, 135, 165, label="donor 2"),
        AttackWindow(34, 140, 170, label="donor 3"),
    ]
    # host power 50 against donor 40 and 100: ratios 1.25 and 0.5, median 0.875
    (a,) = m.attacks
    assert (a.id, a.power, a.kind, a.active) == (34, round(60 * 0.875), 2, 40)
    (note,) = [f for f in found if f.code == "P3_OWN_SPARE"]
    assert note.level == "info" and note.item == "p3_7_13"
    assert "donor record 3" in note.text and "spare record 34 on set 18" in note.text
    assert "P3_OWN_ATTACK" not in [f.code for f in found]
    manifest.check(m)


class Two(Stub):
    """A walk over several pairs."""

    def __init__(self, plans: dict, hits: Hits, hosts: dict[int, int]) -> None:
        super().__init__(next(iter(plans.values())), hits, hosts)
        self.plans = plans
        self.ms = SimpleNamespace(pairs={p: p for p in plans})

    def plan(self, pair: object) -> Plan:
        return self.plans[pair]


def test_the_scale_reads_the_pairs_a_handler_hands_over_to():
    hits = donor(rec(1, power=40), rec(2, power=100), rec(3, power=60, length=40))

    def one(pair: tuple[int, int], to: tuple[int, int], *spawns: Hit) -> Plan:
        return Plan(pair, (Step(61, 4, 0.0, "clip", 191, spawns, ()),), (to,), True)

    plans = {
        (7, 13): one((7, 13), (7, 14), Hit(0, 3, 0, 140.0, 170.0)),
        (7, 14): one((7, 14), (0, 0), Hit(0, 1, 0, 135.0, 165.0), Hit(1, 2, 0, 135.0, 165.0)),
    }
    m = manifest_of("[clips.charge]\nsource = 61\n")
    stub = Two(plans, hits, {1: 7, 2: 8})
    d = own.Derivation(m, stub, lambda: stub, [].append, [(7, 13)])  # type: ignore[arg-type]
    d.run((7, 13))
    assert m.moves["p3_7_13"].after == "p3_7_14"
    (a,) = m.attacks
    assert a.power == round(60 * 0.875), "the ratios 1.25 and 0.5 are the follow-up's"


def test_the_scale_falls_back_where_nothing_matched():
    m, _ = derive(donor(rec(1, power=100)), {}, [Hit(0, 1, 0, 10.0, 20.0)])
    assert m.attacks[0].power == round(100 * spare.POWER_SCALE)


def test_a_refused_spare_is_a_warning_and_the_attack_is_left_out():
    spawns = [Hit(0, 1, 0, 10.0, 20.0), Hit(1, 9, 0, 10.0, 20.0)]
    m, found = derive(donor(rec(1)), {}, spawns, table(records=34))
    assert m.moves["p3_7_13"].attacks == []
    warnings = [f for f in found if f.code == "P3_OWN_ATTACK"]
    assert len(warnings) == 2 and "no spare" in warnings[0].text and "record 9" in warnings[1].text
    assert not [f for f in found if f.code == "P3_OWN_SPARE"]


def test_a_node_held_past_its_record_widens_it_and_the_window_is_not_cut():
    m, found = derive(donor(rec(1, length=10)), {}, [Hit(0, 1, 0, 85.0, 144.0)])
    assert m.attacks[0].active == 59
    assert "P3_OWN_ACTIVE" not in [f.code for f in found]
