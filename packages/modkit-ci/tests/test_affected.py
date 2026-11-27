# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Affected units: owners, dependents, `all`, output formats."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from modkit_ci import affected, registry
from modkit_ci.cli import main

if TYPE_CHECKING:
    from conftest import GitRepo

LOCK = """\
version = 1

[manifest]
members = ["alpha", "beta-app", "sub", "tool"]

[[package]]
name = "alpha"
version = "0.1.0"
source = { editable = "packages/alpha" }

[[package]]
name = "beta-app"
version = "0.1.0"
source = { editable = "apps/beta" }
dependencies = [{ name = "alpha" }, { name = "requests" }]

[[package]]
name = "sub"
version = "0.1.0"
source = { editable = "packages/alpha/sub" }
dependencies = [{ name = "beta-app" }]

[package.optional-dependencies]
extra = [{ name = "alpha" }]

[[package]]
name = "tool"
version = "0.1.0"
source = { editable = "packages/tool" }
"""


@pytest.fixture
def reg(registry_root: Path) -> registry.Registry:
    (registry_root / "uv.lock").write_text(LOCK, encoding="utf-8")
    (registry_root / "packages/tool").mkdir(parents=True)
    (registry_root / "packages/alpha/src/alpha").mkdir(parents=True)
    (registry_root / "packages/alpha/src/alpha/__init__.py").write_text("", encoding="utf-8")
    return registry.load(registry_root)


def run(reg: registry.Registry, *files: str) -> affected.Affected:
    return affected.compute(reg, files, reg.root / "uv.lock")


def test_workspace_edges(reg: registry.Registry) -> None:
    ws = affected.workspace(reg, reg.root / "uv.lock")
    assert ws["beta-app"] == ("apps/beta", frozenset({"alpha"}))
    assert ws["sub"][1] == frozenset({"beta-app", "alpha"})
    assert "requests" not in ws["beta-app"][1]


def test_owner_and_dependents(reg: registry.Registry) -> None:
    result = run(reg, "packages/alpha/src/alpha/x.py")
    assert not result.all
    assert result.modules == {"alpha", "beta", "sub", "framework"}
    assert [u.name for u in result.units] == ["framework", "alpha", "sub", "beta"]


def test_transitive_dependents(reg: registry.Registry) -> None:
    assert run(reg, "apps/beta/a.py").modules == {"beta", "sub"}


def test_depends_key_edge(reg: registry.Registry) -> None:
    (reg.root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    assert run(reg, "packages/alpha/a.py").modules == {"alpha", "framework"}  # no lock edges


def test_extra_path_owner(reg: registry.Registry) -> None:
    assert run(reg, "data/x.bin").modules >= {"alpha"}


def test_longest_prefix(reg: registry.Registry) -> None:
    assert run(reg, "packages/alpha/sub/a.py").modules == {"sub"}


def test_deleted_path_still_owned(reg: registry.Registry) -> None:
    assert run(reg, "apps/beta/gone/old.py").modules == {"beta", "sub"}


def test_root_file_means_all(reg: registry.Registry) -> None:
    result = run(reg, "apps/beta/a.py", "pyproject.toml")
    assert result.all
    assert result.modules == {"framework", "alpha", "sub", "beta"}
    assert {u.name for u in result.units} == {"framework", "alpha", "sub", "beta", "tool"}


def test_non_module_member_means_all(reg: registry.Registry) -> None:
    assert run(reg, "packages/tool/src/x.py").all


def test_no_files_nothing(reg: registry.Registry) -> None:
    result = run(reg)
    assert not result.all
    assert result.units == ()


def test_unit_fields(reg: registry.Registry) -> None:
    unit = next(u for u in run(reg, "packages/alpha/a").units if u.name == "alpha")
    assert unit.tests == "packages/alpha/tests"
    assert unit.packages == ("alpha",)
    assert unit.pyproject == "packages/alpha/pyproject.toml"
    beta = next(u for u in run(reg, "apps/beta/a").units if u.name == "beta")
    assert beta.qt
    fw = next(u for u in run(reg, "framework/a").units if u.name == "framework")
    assert fw.pyproject is None


def test_cli_outputs(
    reg: registry.Registry,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(reg.root)
    assert main(["affected", "--all", "--output", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["all"] is True
    assert data["units"] == ["alpha", "beta", "framework", "sub", "tool"]
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert main(["affected", "--all", "--output", "github"]) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "all=true"
    assert lines[1] == 'modules=["alpha", "beta", "framework", "sub"]'
    assert lines[2].startswith("units=[")


def test_cli_base(
    git_repo: GitRepo, registry_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
    files["uv.lock"] = LOCK
    base = git_repo.commit(files, "init")
    git_repo.commit({"apps/beta/a.py": "x"}, "change")
    git_repo.branch("base", base)
    assert main(["affected", "--base", "base", "--output", "text"]) == 0
    assert capsys.readouterr().out.splitlines()[:2] == ["all: false", "modules: beta sub"]
