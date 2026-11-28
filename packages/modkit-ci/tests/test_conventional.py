# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Header parsing and breaking detection."""

from __future__ import annotations

import pytest
from modkit_ci.conventional import Header, header, is_breaking


def test_header() -> None:
    assert header("feat(mhfu): add a thing") == Header("feat", ("mhfu",), False, "add a thing")


def test_header_multi_scope_bang() -> None:
    assert header("fix(a, b)!: x\n\nbody") == Header("fix", ("a", "b"), True, "x")


def test_header_needs_a_known_type() -> None:
    assert header("framework: x") is None


def test_header_no_scope() -> None:
    assert header("chore: tidy") == Header("chore", (), False, "tidy")


@pytest.mark.parametrize("message", ["feat(a)x", "feat(a):", "Merge branch 'x'", ""])
def test_header_invalid(message: str) -> None:
    assert header(message) is None


@pytest.mark.parametrize(
    "message",
    [
        "feat(a)!: x",
        "feat!: x",
        "fix(a): x\n\nBREAKING CHANGE: gone",
        "fix(a): x\n\nBREAKING-CHANGE: gone",
        "fix(a): x\n\nbody\n\nReviewed-by: me\nBREAKING CHANGE: gone",
        "fix(a): x\n\nBREAKING CHANGE gone",
        "fix(a): x\n\nbreaking change: gone",
        "fix(a): x\n\n  * BREAKING CHANGE: gone",
        "chore(repo): note\n\nsee BREAKING-CHANGE:x",
    ],
)
def test_breaking(message: str) -> None:
    assert is_breaking(message)


@pytest.mark.parametrize(
    "message",
    [
        "feat(a): x",
        "fix(a): x\n\nthis is not BREAKING CHANGE: inline",
        "fix(a): x\n\nbreaking changes: none",
        "feat(a): says BREAKING CHANGE: in its header",
    ],
)
def test_not_breaking(message: str) -> None:
    assert not is_breaking(message)
