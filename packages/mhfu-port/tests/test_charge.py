# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
from pathlib import Path
from types import SimpleNamespace

import pytest
from mhfu_port import manifest, moves, p3charge
from mhfu_port.brain import charge, fatigue, own
from mhfu_port.brain.splice import Spliced
from mhfu_port.cli.splice import summary
from mhfu_port.p3charge import Chip

PORTS = Path(__file__).parents[3] / "ports"

DONOR = p3charge.Donor(
    ups={(1, 6): (10, 15, 15, 20), (1, 15): (25, 35, 40, 55), (1, 16): (40, 55, 65, 100)},
    transform=(1, 4),
    gauge=Chip(5, 10),
    durability=Chip(4, 12),
    cooldown_up=420,
    cooldown_out=900,
)
HOSTS = {(4, 0): "flinch", (4, 5): "fall", (4, 30): "knockout", (4, 16): "knockout"}


def donor_with(ups: dict[tuple[int, int], p3charge.Gains]) -> p3charge.Donor:
    return p3charge.Donor(**{**vars(DONOR), "ups": ups})


def brain(**pairs: manifest.Plays) -> manifest.Brain:
    return manifest.Brain(40, "base_3", "base_3", pairs=pairs, charge=True)


def derive(b: manifest.Brain, hosts=None, moves=None, donor: p3charge.Donor = DONOR):
    ch, found = charge.derive(donor, HOSTS if hosts is None else hosts, b, moves)
    return ch, [(f.level, f.code) for f in found]


ALL = {"1,6": "own", "1,15": "own", "1,16": "own", "1,4": "own"}


def test_each_charge_up_gives_its_gain_on_its_move():
    ch, found = derive(brain(**ALL))
    assert ch is not None and not found
    assert ch.ups == {
        "p3_1_6": (10, 15, 15, 20),
        "p3_1_15": (25, 35, 40, 55),
        "p3_1_16": (40, 55, 65, 100),
    }
    assert ch.transform == "p3_1_4"


def test_the_donor_steps_cooldowns_and_knockouts():
    ch, _ = derive(brain(**ALL))
    assert ch is not None
    assert (ch.chip_gauge, ch.chip_durability) == (5, 4)
    assert (ch.cooldown_up, ch.cooldown_out) == (420, 900)
    assert ch.discharge == ((4, 16), (4, 30))


def test_a_move_the_manifest_names_is_used_by_its_name():
    b = brain(**{"1,15": "charge_up", "1,16": "charge_up", "1,6": "own", "1,4": [[1, 2], "roar"]})
    ch, found = derive(b)
    assert ch is not None and ch.transform == "roar"
    assert ch.ups["charge_up"] == (40, 55, 65, 100) and ch.ups["p3_1_6"] == (10, 15, 15, 20)
    assert found == [("info", "P3_CHARGE_SHARED")]


def test_two_pairs_on_one_move_take_the_larger_gain_in_each_band():
    donor = p3charge.Donor(
        **{**vars(DONOR), "ups": {(1, 15): (25, 60, 40, 55), (1, 16): (40, 55, 65, 50)}}
    )
    ch, found = charge.derive(donor, {}, brain(**{"1,15": "up", "1,16": "up", "1,4": "own"}))
    assert ch is not None and ch.ups == {"up": (40, 60, 65, 55)}
    assert found[0].code == "P3_CHARGE_SHARED"
    assert (
        found[0].text
        == "up takes 1,15 (25, 60, 40, 55), 1,16 (40, 55, 65, 50): the larger, (40, 60, 65, 55)"
    )


def test_pairs_with_equal_gains_share_without_a_word():
    donor = p3charge.Donor(**{**vars(DONOR), "ups": {(1, 15): (5, 5, 5, 5), (1, 16): (5, 5, 5, 5)}})
    ch, found = charge.derive(donor, {}, brain(**{"1,15": "up", "1,16": "up", "1,4": "own"}))
    assert (
        ch is not None
        and ch.ups == {"up": (5, 5, 5, 5)}
        and not [f for f in found if f.code == "P3_CHARGE_SHARED"]
    )


def test_a_charge_up_the_map_lacks_is_a_warning():
    ch, found = derive(brain(**{"1,15": "own", "1,4": "own"}))
    assert ch is not None and set(ch.ups) == {"p3_1_15"}
    assert found == [("warning", "P3_CHARGE_UNMAPPED")] * 2


def test_a_charge_up_on_a_host_pair_is_a_warning():
    ch, found = charge.derive(DONOR, HOSTS, brain(**{**ALL, "1,16": (1, 2)}))
    assert ch is not None and "p3_1_16" not in ch.ups
    assert [(f.level, f.code) for f in found] == [("warning", "P3_CHARGE_UNMAPPED")]
    assert found[0].text == "charge-up 1,16 plays host pair 1,2, not an own move"


def test_a_transform_the_map_lacks_is_an_error():
    ch, found = derive(brain(**{k: v for k, v in ALL.items() if k != "1,4"}))
    assert ch is None and found == [("error", "P3_CHARGE_UNMAPPED")]
    ch, found = derive(brain(**{**ALL, "1,4": (1, 4)}))
    assert ch is None and found == [("error", "P3_CHARGE_UNMAPPED")]


def test_a_name_that_is_no_own_move():
    own = {"p3_1_6": True, "p3_1_15": True, "p3_1_16": False, "p3_1_4": True}
    ch, found = derive(brain(**ALL), moves=own)
    assert ch is not None and set(ch.ups) == {"p3_1_6", "p3_1_15"}
    assert found == [("warning", "P3_CHARGE_MOVE")]
    ch, found = derive(brain(**ALL), moves={**own, "p3_1_16": True, "p3_1_4": False})
    assert ch is None and found == [("error", "P3_CHARGE_MOVE")]
    assert derive(brain(**ALL), moves={k: True for k in own})[1] == []


def test_the_pools():
    donor = p3charge.Donor(
        **{**vars(DONOR), "ups": {(1, k): (1, 1, 1, 1) for k in range(charge.UPS + 1)}}
    )
    ch, found = charge.derive(
        donor, {}, brain(**{f"1,{k}": "own" for k in [4, *range(charge.UPS + 1)]})
    )
    assert ch is None and [f.code for f in found] == ["P3_CHARGE_UPS"]
    hosts = {(4, k): "knockout" for k in range(charge.PAIRS + 1)}
    ch, found = derive(brain(**ALL), hosts)
    assert ch is None and found == [("error", "P3_CHARGE_PAIRS")]
    assert derive(brain(**ALL), dict(list(hosts.items())[: charge.PAIRS]))[0] is not None


def test_a_host_without_reactions_discharges_nothing():
    ch, found = derive(brain(**ALL), {})
    assert ch is not None and ch.discharge == () and not found


def test_the_pairs_it_names():
    ch, _ = derive(brain(**ALL))
    assert ch is not None and ch.named == {(1, 4), (1, 6), (1, 15), (1, 16)}
    ch, _ = derive(brain(**{"1,15": "own", "1,4": "own"}))
    assert ch is not None and ch.named == {(1, 4), (1, 6), (1, 15), (1, 16)}


def test_the_module_table():
    ch, _ = derive(brain(**ALL))
    assert ch is not None
    assert ch.module() == {
        "ups": {
            "p3_1_15": [25, 35, 40, 55],
            "p3_1_16": [40, 55, 65, 100],
            "p3_1_6": [10, 15, 15, 20],
        },
        "transform": "p3_1_4",
        "chip_gauge": 5,
        "chip_durability": 4,
        "cooldown_up": 420,
        "cooldown_out": 900,
        "discharge": [[4, 16], [4, 30]],
    }
    assert moves.lua_value(ch.module()).startswith("{ ups = { p3_1_15 = { 25, 35, 40, 55 }, ")


def test_the_splice_summary_names_the_table():
    ch, _ = derive(brain(**ALL))
    spliced = Spliced(None, [], {}, {}, {})  # type: ignore[arg-type]
    lines = summary(spliced, SimpleNamespace(image=None), [], None, ch).splitlines()  # type: ignore[arg-type]
    assert lines[-2] == (
        "  charge: p3_1_15 25/35/40/55, p3_1_16 40/55/65/100, p3_1_6 10/15/15/20; "
        "transform p3_1_4; chips 5 gauge, 4 durability; cooldowns 420 and 900; "
        "2 discharge pairs"
    )


def test_the_classes_are_fatigues():
    assert charge.KNOCKOUT == fatigue.CLASSES[-1] == "knockout"


# ---- the table of a manifest

TEXT = """
schema = 2
[port]
name = "z"
host_species = 75
pac = "z.bin"
[source]
model = 5339
[clips.charge]
source = 18
[brain]
donor = 40
splice = "base_3"
entry = "base_3"
[brain.pairs]
"1,15" = "own"
"""


def test_a_port_without_the_flag_has_no_table():
    m = manifest.loads(TEXT)
    assert charge.table(m, None) == (None, [])  # type: ignore[arg-type]  # nothing read
    assert charge.expanded(m, None) is None  # type: ignore[arg-type]


def test_the_flag_round_trips():
    m = manifest.loads(TEXT.replace('entry = "base_3"', 'entry = "base_3"\ncharge = true'))
    assert m.brain is not None and m.brain.charge
    assert manifest.loads(manifest.dumps(m)).brain == m.brain
    assert "charge =" not in manifest.dumps(manifest.loads(TEXT))


def test_a_species_without_a_charge_state(data):
    m = manifest.loads(TEXT)
    m.brain = manifest.Brain(23, "base_3", "base_12", charge=True)
    m.source = manifest.Source(model=5248)
    ch, found = charge.table(m, data)
    assert ch is None and [(f.level, f.code) for f in found] == [("error", "P3_CHARGE_DONOR")]
    with pytest.raises(manifest.ManifestError, match="P3_CHARGE_DONOR"):
        charge.expanded(m, data)


def zinogre(**pairs: manifest.Plays) -> manifest.Manifest:
    """The Zinogre port with a `[brain] charge` of these pairs."""
    m = manifest.load(PORTS / "zinogre.toml")
    m.brain = manifest.Brain(40, "base_3", "base_3", pairs=pairs, charge=True)
    return m


def test_zinogres_table(data):
    pairs = {"1,15": "charge_up", "1,16": "charge_up", "1,6": "own", "1,17": "own", "1,4": "own"}
    m, derived = own.expand(zinogre(**pairs), data)
    assert not [f for f in derived if f.level == "error"]
    ch, found = charge.table(m, data)
    assert ch is not None
    assert [(f.level, f.code) for f in found] == [
        ("warning", "P3_CHARGE_UNMAPPED"),
        ("warning", "P3_CHARGE_UNMAPPED"),
        ("info", "P3_CHARGE_SHARED"),
    ]
    assert ch.ups == {
        "p3_1_6": (10, 15, 15, 20),
        "charge_up": (40, 55, 65, 100),
        "p3_1_17": (55, 75, 100, 100),
    }
    assert ch.transform == "p3_1_4" and (ch.chip_gauge, ch.chip_durability) == (5, 5)
    assert (ch.cooldown_up, ch.cooldown_out) == (420, 900)
    assert ch.discharge == ((4, 16), (4, 29), (4, 30), (4, 31))
    assert "charge_up takes 1,15 (25, 35, 40, 55), 1,16 (40, 55, 65, 100)" in found[2].text
    assert {t for t in ch.named} >= {(1, 3), (1, 20), (1, 4)}


def test_zinogres_table_needs_the_moves_derived(data):
    ch, found = charge.table(zinogre(**{"1,15": "charge_up", "1,4": "own"}), data)
    assert ch is None and ("error", "P3_CHARGE_MOVE") in [(f.level, f.code) for f in found]
