# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The Zinogre's charge state, read from em040's overlay. Needs MHP3rd's files."""

import pytest
from mhfu_port import p3charge
from mhfu_port.manifest import Source

ZINOGRE, ZINOGRE_MODEL = 40, 5339
BRUTE, BRUTE_MODEL = 23, 5248


def anim(data, model):
    return data.p3rd.read(Source(model=model).anim)


@pytest.fixture(scope="module")
def zinogre(data):
    return p3charge.read(data.p3rd, anim(data, ZINOGRE_MODEL), ZINOGRE)


def test_the_charge_ups_by_hp_band(zinogre):
    """Each pair's run from an empty gauge: keyframe adds by band, top-ups near full, the cap."""
    assert zinogre.ups == {
        (1, 6): (10, 15, 15, 20),
        (1, 15): (25, 35, 40, 55),
        (1, 16): (40, 55, 65, 100),
        (1, 17): (55, 75, 100, 100),
        (1, 3): (50, 50, 50, 50),
        (1, 20): (50, 50, 50, 50),
    }


def test_the_transform_chips_and_cooldowns(zinogre):
    assert zinogre.transform == (1, 4)
    assert (zinogre.gauge, zinogre.durability) == ((5, 10), (5, 10))
    assert (zinogre.cooldown_up, zinogre.cooldown_out) == (420, 900)


def test_a_species_without_a_charge_state(data):
    with pytest.raises(ValueError, match="monster 23 has no charge state"):
        p3charge.read(data.p3rd, anim(data, BRUTE_MODEL), BRUTE)


def test_a_monster_the_game_lacks(data):
    with pytest.raises(ValueError, match="monster 9999 has 0 slots"):
        p3charge.read(data.p3rd, anim(data, ZINOGRE_MODEL), 9999)
