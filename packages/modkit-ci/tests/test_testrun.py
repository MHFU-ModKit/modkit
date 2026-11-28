# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The pytest command line per unit and the runner around it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from modkit_ci import registry, testrun
from modkit_ci.affected import Unit
from modkit_ci.cli import main

UNIT = Unit(
    "alpha",
    "packages/alpha",
    "packages/alpha/tests",
    ("alpha", "alpha_x"),
    "packages/alpha/pyproject.toml",
    False,
)
QT = Unit("beta", "apps/beta", "apps/beta/tests", ("beta",), "apps/beta/pyproject.toml", True)
BARE = Unit("framework", "framework", "framework/tests", (), None, False)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "packages/alpha").mkdir(parents=True)
    (tmp_path / "apps/beta").mkdir(parents=True)
    for rel, bar in (("packages/alpha", 63), ("apps/beta", 0)):
        text = f"[tool.coverage.report]\nfail_under = {bar}\n"
        (tmp_path / rel / "pyproject.toml").write_text(text, encoding="utf-8")
    return tmp_path


def test_plain() -> None:
    assert testrun.pytest_argv(UNIT) == [sys.executable, "-m", "pytest", "packages/alpha/tests"]


def test_cov(root: Path) -> None:
    argv = testrun.pytest_argv(UNIT, cov=True, extra=["-x"], root=root)
    assert argv[3:] == [
        "packages/alpha/tests",
        "--cov=alpha",
        "--cov=alpha_x",
        "--cov-config=packages/alpha/pyproject.toml",
        "--cov-report=term-missing",
        "--cov-fail-under=63",
        "-x",
    ]


def test_cov_without_floor(root: Path) -> None:
    (root / "packages/alpha/pyproject.toml").write_text("[project]\n", encoding="utf-8")
    argv = testrun.pytest_argv(UNIT, cov=True, root=root)
    assert not any(a.startswith("--cov-fail-under") for a in argv)


def test_no_qt_drops_cov_for_qt_only(root: Path) -> None:
    assert "--cov=beta" in testrun.pytest_argv(QT, cov=True, root=root)
    assert testrun.pytest_argv(QT, cov=True, no_qt=True, root=root)[3:] == ["apps/beta/tests"]
    assert "--cov=alpha" in testrun.pytest_argv(UNIT, cov=True, no_qt=True, root=root)


def test_no_packages_no_cov() -> None:
    assert testrun.pytest_argv(BARE, cov=True)[3:] == ["framework/tests"]


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []

    def fake(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        seen.append(argv)
        code = 1 if "apps/beta/tests" in argv else 0
        return subprocess.CompletedProcess(argv, code)

    monkeypatch.setattr(testrun.subprocess, "run", fake)
    return seen


@pytest.fixture
def reg(registry_root: Path) -> registry.Registry:
    (registry_root / "packages/alpha/tests").mkdir()
    (registry_root / "apps/beta/tests").mkdir()
    return registry.load(registry_root)


def test_run_sequential_and_skip(reg: registry.Registry, calls: list[list[str]]) -> None:
    assert testrun.run(reg, ["alpha", "sub"]) == 0
    assert len(calls) == 1  # sub has no tests dir


def test_run_failure_continues(reg: registry.Registry, calls: list[list[str]]) -> None:
    assert testrun.run(reg, ["beta", "alpha"]) == 1
    assert len(calls) == 2


def test_run_unknown_unit(reg: registry.Registry, calls: list[list[str]]) -> None:
    assert testrun.run(reg, ["nope"]) == 1
    assert calls == []


def test_cli_passthrough(
    reg: registry.Registry, calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(reg.root)
    assert main(["test", "alpha", "--no-qt", "--", "-k", "x"]) == 0
    assert calls[0][-2:] == ["-k", "x"]


def test_cli_options_after_units(
    reg: registry.Registry, calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(reg.root)
    assert main(["test", "alpha", "--cov", "--no-qt", "--", "-x"]) == 0
    assert "--cov=alpha" not in calls[0]  # no packages in the fixture
    assert calls[0][-1] == "-x"
    with pytest.raises(SystemExit):
        main(["test", "alpha", "-x"])


def test_cli_double_dash_directly(
    reg: registry.Registry, calls: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(reg.root)
    assert main(["test", "alpha", "--", "-x"]) == 0
    assert calls[0][-1] == "-x"
    assert main(["test", "--cov", "alpha", "beta", "--", "-k", "x"]) == 1
    assert [c[-2:] for c in calls[1:]] == [["-k", "x"]] * 2
