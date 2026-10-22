# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import pytest
from mhfu.em.script import draw, edit


def pick(weights, *inside):
    """A pick of `weights`, the first branch opening with `inside`."""
    return edit.random([(w, list(inside) * (k == 0)) for k, w in enumerate(weights)])


@pytest.mark.parametrize(
    ("weights", "lo", "hi", "want"),
    [
        ([5, 27], 0, 32, [range(5), range(5, 32)]),
        ([5, 27], 0, 8, [range(5), range(5, 8)]),
        ([5, 27], 8, 16, [range(0), range(8, 16)]),
        (
            [8, 0, 8],
            0,
            32,
            [range(8), range(0), range(8, 32)],
        ),  # a 0 is skipped, the last takes the rest
        ([4, 255, 4], 0, 32, [range(4), range(4, 32), range(0)]),
    ],
)
def test_spans(weights, lo, hi, want):
    assert draw.spans(weights, lo, hi) == want
    assert [draw.take(weights, d) for d in range(lo, hi)] == [
        k for k, r in enumerate(want) for _ in r
    ]


def test_nested_randoms_see_their_branch():
    leaf = pick([16, 16])
    mid = pick([8, 24], leaf)
    top = pick([16, 16], mid)
    assert draw.nested([top]) == {id(mid): range(16), id(leaf): range(8)}


def test_a_random_not_first_in_its_branch_is_not_nested():
    inner = pick([16, 16])
    top = pick([16, 16], edit.insn("nop"), inner)
    assert draw.nested([top]) == {}
    assert draw.nested([edit.random([(16, []), (16, [])]), inner]) == {}


def test_a_branch_no_draw_reaches_holds_no_nested_random():
    inner = pick([16, 16])
    assert draw.nested([edit.random([(0, [inner]), (32, [])])]) == {}


def test_a_shifted_random_is_not_a_parent():
    inner = pick([16, 16])
    shifted = edit.random([(16, [inner]), (16, [])], shift=2)
    assert draw.nested([shifted]) == {}
