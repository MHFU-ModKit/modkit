# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
import struct
from pathlib import Path

import pytest
from mhfu import addresses
from mhfu.memory import Image
from mhfu.mips import Code
from mhfu_port import manifest, moves
from mhfu_port.brain import fatigue
from mhfu_port.cli import main

PORTS = Path(__file__).parents[3] / "ports"
CLASSES = {"flinch": 15, "fall": 50, "knockout": 200}
DONOR = fatigue.Donor(
    max=1000,
    starts=(400, 600),
    even=True,
    tired=2250,
    refill=500,
    low_hp=15,
    costs={(2, 10): 15, (7, 1): 5, (7, 13): 20},
    reactions=CLASSES,
)


def brain(**pairs: manifest.Pair | str) -> manifest.Brain:
    return manifest.Brain(23, "base_3", "base_12", pairs=pairs, fatigue=True)


def derive(b: manifest.Brain, hosts=None, hold=(), donor: fatigue.Donor = DONOR):
    """The table and the findings but the note on pairs `b` leaves unmapped."""
    fat, found = fatigue.derive(donor, hosts or {}, b, hold)
    return fat, [(f.level, f.code) for f in found if f.code != "P3_FATIGUE_UNMAPPED"]


# ---- the mapping


def test_a_donor_pair_costs_what_plays_in_its_place():
    fat, found = derive(brain(**{"2,10": (1, 5), "7,1": (3, 3), "7,13": (2, 8)}))
    assert fat.pairs == {(1, 5): 15, (3, 3): 5, (2, 8): 20} and not fat.own and not found
    assert (fat.max, fat.starts, fat.tired, fat.refill, fat.low_hp) == (
        1000,
        (400, 600),
        2250,
        500,
        15,
    )


def test_an_own_move_is_charged_by_its_head():
    fat, found = derive(brain(**{"7,13": "own", "2,10": "slam", "7,1": (3, 3)}))
    assert fat.own == {"p3_7_13": 20, "slam": 15} and fat.pairs == {(3, 3): 5} and not found


def test_two_donor_pairs_on_one_pair_take_the_larger():
    fat, found = fatigue.derive(DONOR, {}, brain(**{"2,10": (1, 5), "7,13": (1, 5)}), ())
    assert fat.pairs == {(1, 5): 20}
    assert [(f.level, f.code) for f in found] == [
        ("info", "P3_FATIGUE_UNMAPPED"),
        ("info", "P3_FATIGUE_SHARED"),
    ]
    assert found[1].text == "host pair 1,5 takes 2,10 (15), 7,13 (20): the larger, 20"


def test_equal_costs_on_one_pair_say_nothing():
    donor = fatigue.Donor(**{**vars(DONOR), "costs": {(2, 10): 15, (2, 12): 15}})
    fat, found = derive(brain(**{"2,10": (1, 5), "2,12": (1, 5)}), donor=donor)
    assert fat.pairs == {(1, 5): 15} and not found


def test_a_free_donor_pair_on_a_costed_pair_is_shared():
    fat, found = derive(brain(**{"2,10": (1, 5), "2,11": (1, 5), "7,1": (3, 3), "7,13": "own"}))
    assert fat.pairs == {(1, 5): 15, (3, 3): 5} and found == [("info", "P3_FATIGUE_SHARED")]


def test_own_moves_share_by_name():
    fat, found = derive(brain(**{"2,10": "slam", "7,13": "slam", "7,1": (3, 3)}))
    assert fat.own == {"slam": 20} and found == [("info", "P3_FATIGUE_SHARED")]


def test_a_costed_pair_with_no_move_is_noted():
    fat, found = fatigue.derive(DONOR, {}, brain(**{"7,1": (3, 3)}), ())
    assert fat.pairs == {(3, 3): 5}
    assert [(f.level, f.code) for f in found] == [("info", "P3_FATIGUE_UNMAPPED")]
    assert "2,10, 7,13" in found[0].text


def test_a_free_pair_is_not_in_the_table():
    fat, _ = derive(brain(**{"2,11": (1, 5), "7,1": (3, 3)}))
    assert fat.pairs == {(3, 3): 5}


def test_the_carrier_of_own_moves_costs_nothing():
    fat, found = fatigue.derive(DONOR, {}, brain(**{"2,10": (0, 2), "7,13": "own"}), {(0, 2)})
    assert fat.pairs == {} and fat.own == {"p3_7_13": 20}
    assert [(f.level, f.code) for f in found] == [
        ("info", "P3_FATIGUE_UNMAPPED"),
        ("warning", "P3_FATIGUE_HUB"),
    ]


def test_reactions_land_by_class():
    hosts = {(4, 1): "flinch", (4, 5): "fall", (4, 16): "knockout"}
    fat, found = derive(brain(**{"7,13": (2, 8)}), hosts)
    assert fat.pairs == {(2, 8): 20, (4, 1): 15, (4, 5): 50, (4, 16): 200} and not found


def test_a_pair_both_a_move_and_a_reaction_takes_the_larger():
    fat, found = derive(brain(**{"7,13": (4, 1)}), {(4, 1): "flinch", (4, 5): "fall"})
    assert fat.pairs == {(4, 1): 20, (4, 5): 50}
    assert found == [("info", "P3_FATIGUE_SHARED")]


def test_a_reaction_pair_that_carries_own_moves_is_dropped():
    fat, found = derive(brain(), {(4, 1): "flinch"}, hold={(4, 1)})
    assert fat.pairs == {} and found == [("warning", "P3_FATIGUE_HUB")]


def test_more_pairs_than_the_helper_holds():
    hosts = {(4, k): "flinch" for k in range(fatigue.POOL + 1)}
    _, found = derive(brain(), hosts)
    assert found == [("error", "P3_FATIGUE_PAIRS")]
    _, found = derive(brain(), dict(list(hosts.items())[: fatigue.POOL]))
    assert not found


def test_an_uneven_roll_is_noted():
    donor = fatigue.Donor(**{**vars(DONOR), "even": False, "costs": {}})
    assert derive(brain(), donor=donor)[1] == [("info", "P3_FATIGUE_STARTS")]


def test_the_module_table():
    fat, _ = derive(brain(**{"7,13": "own", "7,1": (3, 3)}), {(4, 1): "flinch"})
    assert fat.module() == {
        "max": 1000,
        "starts": [400, 600],
        "tired": 2250,
        "refill": 500,
        "low_hp": 15,
        "calm": True,
        "pairs": [[3, 3, 5], [4, 1, 15]],
        "own": {"p3_7_13": 20},
    }
    assert "own" not in derive(brain())[0].module()


# ---- the host's reactions


def test_reaction_classes():
    codes = {
        12: frozenset([(4, 0), (4, 5)]),
        15: frozenset([(4, 16), (4, 30)]),
        16: frozenset([(4, 16)]),
        7: frozenset([(4, 30), (4, 26)]),
    }
    clips = {(4, 0): 1, (4, 5): 3, (4, 30): 4, (4, 26): 1, (4, 16): 2}
    assert fatigue.classify(codes, clips) == {
        (4, 0): "flinch",
        (4, 5): "fall",
        (4, 30): "knockout",
        (4, 26): "flinch",
        (4, 16): "knockout",
    }
    assert fatigue.classify({}, {}) == {}
    assert fatigue.classify({3: frozenset([(4, 9)])}, {}) == {(4, 9): "flinch"}


def test_a_host_without_a_reaction_table_has_none():
    assert fatigue.host_reactions(None, 82) == {}  # type: ignore[arg-type]  # not read


BASE = 0x08804000  # noaddr: where the test code sits


def words(*ins: int) -> Code:
    data = struct.pack(f"<{len(ins)}I", *ins)
    return Code(Image(data, BASE), range(BASE, BASE + len(data)))


A1_M200 = 0x2405FF38  # addiu a1, zero, -200
A0_S0 = 0x02002021  # addu a0, s0, zero
NOP = 0
RA = 0x03E00008  # jr ra


def jump(op: int) -> int:
    return op << 26 | fatigue.STAMINA_ADD >> 2


def test_a_case_that_adds_stamina():
    assert fatigue._charge(words(A1_M200, A0_S0, jump(3), NOP), BASE) == -200
    assert fatigue._charge(words(A0_S0, A1_M200, jump(2), NOP), BASE) == -200
    assert fatigue._charge(words(A0_S0, jump(2), A1_M200), BASE) == -200


def test_a_case_that_adds_nothing():
    elsewhere = 3 << 26 | (BASE + 0x100) >> 2
    assert fatigue._charge(words(A0_S0, RA, NOP), BASE) is None
    assert fatigue._charge(words(A1_M200, elsewhere, NOP), BASE) is None
    assert fatigue._charge(words(A0_S0, NOP), BASE) is None


# ---- the table of a manifest

TEXT = """
schema = 2
[port]
name = "z"
host_species = 75
pac = "z.bin"
[source]
model = 5248
[clips.charge]
source = 61
[brain]
donor = 23
splice = "base_3"
entry = "base_12"
[brain.pairs]
"7,13" = "own"
"7,1" = [3, 3]
"""


def test_carriers_of_a_manifest():
    m = manifest.loads(TEXT)
    assert fatigue.carriers(m) == {(0, 2)}
    plain = manifest.loads(TEXT.replace('"7,13" = "own"\n', ""))
    assert fatigue.carriers(plain) == set()


def test_a_list_with_an_own_move_rides_the_hub():
    m = manifest.loads(TEXT.replace('"7,13" = "own"', '"7,13" = [[1, 2], "own"]'))
    assert fatigue.carriers(m) == {(0, 2)}
    m = manifest.loads(TEXT.replace('"7,13" = "own"', '"7,13" = [[1, 2], [1, 3]]'))
    assert fatigue.carriers(m) == set()


def test_a_port_without_the_flag_has_no_table():
    m = manifest.loads(TEXT)
    assert fatigue.table(m, None) == (None, [])  # type: ignore[arg-type]  # nothing read
    assert fatigue.expanded(m, None) is None  # type: ignore[arg-type]


def test_the_flag_round_trips():
    m = manifest.loads(TEXT.replace('entry = "base_12"', 'entry = "base_12"\nfatigue = true'))
    assert m.brain is not None and m.brain.fatigue
    assert manifest.loads(manifest.dumps(m)).brain == m.brain
    assert "fatigue" not in manifest.dumps(manifest.loads(TEXT))


# ---- with the games


@pytest.fixture(scope="module")
def brute(data):
    return fatigue.read(data.p3rd, 23)


def test_brute_parameters(brute):
    assert (brute.max, brute.starts, brute.even) == (1000, (400, 600), True)
    assert (brute.tired, brute.refill, brute.low_hp) == (2250, 500, 15)
    assert brute.reactions == CLASSES


def test_brute_costs(brute):
    assert len(brute.costs) == 14
    assert {p: brute.costs[p] for p in [(2, 10), (2, 22), (7, 0), (7, 15)]} == dict.fromkeys(
        [(2, 10), (2, 22), (7, 0), (7, 15)], 15
    )
    assert [brute.costs[7, s] for s in (1, 2, 4, 7, 13, 14)] == [5, 10, 12, 8, 20, 15]
    assert not {m for m, _ in brute.costs} - {2, 7}


def test_the_donor_reaction_classes(data):
    costs = fatigue.reaction_costs(data.p3rd)
    assert {costs[s] for s in (19, 30, 36, 104, 244)} == {15}
    assert {costs[s] for s in (31, 35, 64, 253)} == {50}
    assert {s for s, c in costs.items() if c == 200} == {106, 107}
    assert not {233, 236, 18, 254} & set(costs)
    assert fatigue.stamina_max(data.p3rd) == 1000


def test_a_species_without_a_row(data):
    with pytest.raises(ValueError, match="no fatigue row"):
        fatigue.read(data.p3rd, 10)
    b = manifest.Brain(10, "base_3", "base_12", fatigue=True)
    m = manifest.loads(TEXT)
    m.brain = b
    fat, found = fatigue.table(m, data)
    assert fat is None and [(f.level, f.code) for f in found] == [("error", "P3_FATIGUE_DONOR")]
    with pytest.raises(manifest.ManifestError, match="P3_FATIGUE_DONOR"):
        fatigue.expanded(m, data)


def test_em75_reaction_cases(data):
    codes = fatigue.entered(data.fu, 75)
    assert len(codes) == addresses.EM75_REACTIONS.count
    assert codes[11] == {(4, 4)}  # the tail cut
    assert {0, 1, 5, 6, 8} <= {s for _, s in codes[12]}  # the flinch, by part and posture
    assert not codes[0] and codes[15] == codes[16]
    assert all(m == fatigue.REACT_MAIN for pairs in codes.values() for m, _ in pairs)


def test_em75_reaction_pairs_by_class(data):
    got = fatigue.host_reactions(data.fu, 75)
    assert {got[4, s] for s in (0, 1, 4, 24)} == {"flinch"}
    assert {got[4, s] for s in (5, 6, 8, 20)} == {"fall"}
    assert {p for p, c in got.items() if c == "knockout"} == {(4, 16), (4, 29), (4, 30), (4, 31)}
    assert set(got.values()) == set(fatigue.CLASSES)


def brute_manifest(data):
    m = manifest.load(PORTS / "brute_tigrex.toml")
    assert m.brain is not None
    for key in ("7,13", "7,14", "7,15"):
        m.brain.pairs[key] = manifest.OWN
    m.brain.fatigue = True
    return m


def test_brute_table(data):
    fat, found = fatigue.table(brute_manifest(data), data)
    assert fat is not None and not [f for f in found if f.level == "error"]
    assert (fat.max, fat.starts, fat.tired, fat.refill, fat.low_hp, fat.calm) == (
        1000,
        (400, 600),
        2250,
        500,
        15,
        True,
    )
    assert fat.own == {"p3_7_13": 20, "p3_7_14": 15, "p3_7_15": 15}
    host = {p: c for p, c in fat.pairs.items() if p[0] != fatigue.REACT_MAIN}
    assert host == {
        (1, 4): 15,
        (1, 5): 15,
        (3, 0): 15,
        (3, 3): 5,
        (3, 4): 10,
        (3, 6): 12,
        (3, 34): 8,
    }
    assert {fat.pairs[4, s] for s in (0, 1)} == {15}
    assert {fat.pairs[4, s] for s in (5, 6, 8)} == {50}
    assert {fat.pairs[4, s] for s in (16, 29, 30, 31)} == {200}
    assert (0, 2) not in fat.pairs and len(fat.pairs) <= fatigue.POOL
    codes = {f.code for f in found}
    assert codes == {"P3_FATIGUE_UNMAPPED", "P3_FATIGUE_SHARED"}


def test_a_host_without_reactions_says_so(data):
    m = brute_manifest(data)
    m.port.host_species = 76
    _, found = fatigue.table(m, data)
    assert ("warning", "P3_FATIGUE_REACTIONS") in [(f.level, f.code) for f in found]


def test_the_moves_module_carries_the_table(data, tmp_path, capsys):
    path = tmp_path / "m.toml"
    path.write_text((PORTS / "brute_tigrex.toml").read_text())
    games = ["--data", str(data.fu.root), "--p3rd-data", str(data.p3rd.root)]
    assert main(["build", str(path), "-o", str(tmp_path / "out.bin"), *games]) == 0
    module = (tmp_path / moves.module_name(manifest.load(path))).read_text()
    assert "  fatigue = { max = 1000, starts = { 400, 600 }, tired = 2250, refill = 500," in module
    assert "low_hp = 15, calm = true, pairs = { { 1, 4, 15 }" in module
    assert "p3_7_14_a0 = 15, p3_7_14_a1 = 15, p3_7_14_a2 = 15" in module, (
        "a picker is charged by its variants"
    )
    assert "P3_FATIGUE_SHARED" in capsys.readouterr().err
