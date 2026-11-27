# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The coverage floor ratchet."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from modkit_ci import coverage
from modkit_ci.cli import main

if TYPE_CHECKING:
    from conftest import GitRepo


def toml(floor: int | None) -> str:
    if floor is None:
        return "[project]\n"
    return f"[project]\n\n[tool.coverage.report]\nfail_under = {floor}\n"


def test_floor_read() -> None:
    assert coverage.floor(toml(63)) == 63
    assert coverage.floor(toml(None)) is None
    assert coverage.floor(None) is None


def test_fell() -> None:
    (finding,) = coverage.check({"a": 63}, {"a": 62})
    assert finding.module == "a"
    assert "63" in finding.message
    assert "62" in finding.message


def test_removed() -> None:
    assert len(coverage.check({"a": 10}, {"a": None})) == 1


def test_rose_same_new_pass() -> None:
    base: dict[str, float | None] = {"a": 10, "b": 5, "c": None, "gone": 7}
    head: dict[str, float | None] = {"a": 11, "b": 5, "c": 1, "new": 0}
    assert coverage.check(base, head) == []


def test_cli(git_repo: GitRepo, registry_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    files = {
        p.relative_to(registry_root).as_posix(): p.read_text(encoding="utf-8")
        for p in registry_root.rglob("*")
        if p.is_file()
    }
    files |= {
        f"{p.relative_to(registry_root).as_posix()}/.keep": ""
        for p in registry_root.rglob("*")
        if p.is_dir()
    }
    files["packages/alpha/pyproject.toml"] = toml(50)
    base = git_repo.commit(files, "init")
    git_repo.commit({"packages/alpha/pyproject.toml": toml(40)}, "lower")
    assert main(["coverage", "--base", base]) == 1
    assert "fell from 50 to 40" in capsys.readouterr().err
    git_repo.commit({"packages/alpha/pyproject.toml": toml(60)}, "raise")
    assert main(["coverage", "--base", base]) == 0
