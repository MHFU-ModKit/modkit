# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu import steering

PATH = ((0.0, 0.0), (-2.5, 40.25), (3.0, 100.0), (0.0, -8191.75))


def test_samples_are_quarter_units() -> None:
    assert steering.SCALE == 4 and steering.STEP == 2
    assert steering.samples(PATH) == [(0, 0), (-10, 161), (12, 400), (0, -32767)]
    assert steering.path_of(steering.samples(PATH)) == PATH


def test_hex_round_trips() -> None:
    text = steering.hex_of(PATH)
    assert text == "00000000" + "fff600a1" + "00000190"[:0] + "000c0190" + "00008001"
    assert steering.parse(text) == PATH
    assert steering.parse("") == ()


@pytest.mark.parametrize("bad", ["0", "0000000", "0000000g", "00000000000000"])
def test_parse_refuses(bad: str) -> None:
    with pytest.raises(ValueError, match="8 hex digits"):
        steering.parse(bad)


def test_the_most_and_the_reach_are_refused() -> None:
    assert steering.hex_of([(0, 0)] * steering.MOST)
    with pytest.raises(ValueError, match=f"a move holds {steering.MOST}"):
        steering.hex_of([(0, 0)] * (steering.MOST + 1))
    assert steering.sample(-steering.REACH) == -0x8000
    with pytest.raises(ValueError, match="a travel sample holds"):
        steering.sample(steering.REACH)


def test_the_curve_is_straight_lines_held_outside() -> None:
    p = ((0.0, 0.0), (10.0, 20.0), (10.0, 60.0))
    assert steering.at(p, 0) == (0, 0) and steering.at(p, -5) == (0, 0)
    assert steering.at(p, 1) == (5, 10) and steering.at(p, 2) == (10, 20)
    assert steering.at(p, 3) == (10, 40) and steering.at(p, 99) == (10, 60)
    assert steering.at(p, 25, start=24) == (5, 10)
    assert steering.at((), 5) == (0, 0)
