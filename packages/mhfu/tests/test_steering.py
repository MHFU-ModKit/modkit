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


def test_turns_round_trip() -> None:
    values = [0, 3000, -2, 32767, -32768]
    text = steering.turns_hex(values)
    assert text == "00000bb8fffe7fff8000"
    assert steering.parse_turns(text) == tuple(values) and steering.parse_turns("") == ()


@pytest.mark.parametrize("bad", ["0", "000", "0000000", "000g"])
def test_parse_turns_refuses(bad: str) -> None:
    with pytest.raises(ValueError, match="4 hex digits"):
        steering.parse_turns(bad)


def test_the_most_and_the_reach_of_a_code_turn_are_refused() -> None:
    assert steering.turns_hex([0] * steering.MOST_TURNS)
    with pytest.raises(ValueError, match=f"a move holds {steering.MOST_TURNS}"):
        steering.turn_values([0] * (steering.MOST_TURNS + 1))
    for v in (32768, -32769):
        with pytest.raises(ValueError, match="a code-turn value holds"):
            steering.turn_values([v])
