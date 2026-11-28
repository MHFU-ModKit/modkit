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


def snapshot(root: Path) -> dict[str, str | None]:
    files: dict[str, str | None] = {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in root.rglob("*")
        if p.is_file()
    }
    files |= {f"{p.relative_to(root).as_posix()}/.keep": "" for p in root.rglob("*") if p.is_dir()}
    return files


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


def test_excludes_read() -> None:
    text = "[tool.coverage.run]\nomit = ['a.py']\n[tool.coverage.report]\nexclude_also = ['x']\n"
    assert coverage.excludes(text) == {"run.omit: a.py", "report.exclude_also: x"}
    assert coverage.excludes(None) == frozenset()


def test_excludes_grew() -> None:
    base = {"a": frozenset({"run.omit: a.py"}), "b": frozenset[str]()}
    head = {
        "a": frozenset({"run.omit: a.py", "run.omit: b.py"}),
        "b": frozenset(),
        "n": frozenset("x"),
    }
    found = coverage.check_excludes(base, head)
    assert [(f.module, f.message) for f in found] == [
        ("a", "coverage excludes grew: run.omit: b.py")
    ]
    assert coverage.check_excludes(head, base) == []


def test_cli_excludes(
    git_repo: GitRepo, registry_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = snapshot(registry_root)
    files["packages/alpha/pyproject.toml"] = toml(50)
    base = git_repo.commit(files, "init")
    omit = toml(50) + "omit = ['x.py']\n"
    git_repo.commit({"packages/alpha/pyproject.toml": omit}, "omit")
    assert main(["coverage", "--base", base]) == 1
    assert "excludes grew" in capsys.readouterr().err


def test_cli(git_repo: GitRepo, registry_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    files = snapshot(registry_root)
    files["packages/alpha/pyproject.toml"] = toml(50)
    base = git_repo.commit(files, "init")
    git_repo.commit({"packages/alpha/pyproject.toml": toml(40)}, "lower")
    assert main(["coverage", "--base", base]) == 1
    assert "fell from 50 to 40" in capsys.readouterr().err
    git_repo.commit({"packages/alpha/pyproject.toml": toml(60)}, "raise")
    assert main(["coverage", "--base", base]) == 0
