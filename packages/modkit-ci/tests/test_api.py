# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from modkit_ci import api, api_lua, api_schema, cli
from modkit_ci.findings import Finding

if TYPE_CHECKING:
    from conftest import GitRepo  # the fixture's type; tests are not importable by name

INIT = "packages/alpha/src/alpha/__init__.py"
GOOD = 'def f(a):\n    """Doc."""\n'


@pytest.fixture
def repo(git_repo: GitRepo, registry_root: Path) -> GitRepo:
    shutil.copytree(registry_root, git_repo.path, dirs_exist_ok=True)
    base = git_repo.commit({INIT: GOOD, "framework/lua/meta/a.lua": "x"}, "chore(repo): base")
    git_repo.branch("base", base)
    return git_repo


@pytest.fixture(autouse=True)
def lua(monkeypatch: pytest.MonkeyPatch) -> list[Finding]:
    """The Lua check, replaced: what it returns is what the test sets."""
    found: list[Finding] = []
    monkeypatch.setattr(api_lua, "check", lambda module, base, head: list(found))
    return found


def out(capsys: pytest.CaptureFixture[str]) -> str:
    return capsys.readouterr().err


def test_no_change(repo: GitRepo) -> None:
    assert api.run("base") == 0


def test_clean_change(repo: GitRepo) -> None:
    repo.commit({INIT: GOOD + 'def g():\n    """Doc."""\n'}, "feat(alpha): g")
    assert api.run("base") == 0


def test_unmarked_break(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.commit({INIT: 'def f(a, b):\n    """Doc."""\n'}, "fix(alpha): sig")
    assert api.run("base") == 1
    assert "error: alpha break" in out(capsys)


def test_marked_break(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.commit({INIT: 'def f(a, b):\n    """Doc."""\n'}, "feat(alpha)!: sig")
    assert api.run("base") == 0
    assert "notice: alpha break" in out(capsys)


def test_footer_marks(repo: GitRepo) -> None:
    msg = "fix(alpha): sig\n\nBREAKING CHANGE: a is gone"
    repo.commit({INIT: "def f():\n    '''Doc.'''\n"}, msg)
    assert api.run("base") == 0


def test_marker_on_other_module(repo: GitRepo) -> None:
    repo.commit({"apps/beta/x.txt": "x"}, "feat(beta)!: other")
    repo.commit({INIT: 'def f(a, b):\n    """Doc."""\n'}, "fix(alpha): sig")
    assert api.run("base") == 1


def test_marker_on_extra_path_only(repo: GitRepo) -> None:
    repo.commit({"data/x.txt": "x"}, "feat(alpha)!: data")
    repo.commit({INIT: 'def f(a, b):\n    """Doc."""\n'}, "fix(alpha): sig")
    assert api.run("base") == 1


def test_marker_without_break(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.commit({INIT: GOOD + 'def g():\n    """Doc."""\n'}, "feat(alpha)!: nothing broke")
    assert api.run("base") == 0
    assert "notice: alpha warning" in out(capsys)


def test_merge_does_not_mark(repo: GitRepo) -> None:
    a = repo.commit({INIT: 'def f(a, b):\n    """Doc."""\n'}, "fix(alpha): sig")
    base = repo.git("rev-parse", "base")
    repo.commit({"x.txt": "x"}, "feat(alpha)!: merge", parents=[a, base])
    assert api.run("base") == 1


def test_doc_not_escapable(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.commit({INIT: GOOD + "def g():\n    pass\n"}, "feat(alpha)!: undocumented")
    assert api.run("base") == 1
    assert "error: alpha doc" in out(capsys)


def test_unchanged_module_unchecked(repo: GitRepo) -> None:
    repo.commit({"apps/beta/x.txt": "x"}, "chore(beta): file")
    assert api.run("base") == 0


def test_lua_break(repo: GitRepo, lua: list[Finding], capsys: pytest.CaptureFixture[str]) -> None:
    lua.append(Finding("framework", "break", "mhfu.f removed"))
    repo.commit({"framework/lua/meta/a.lua": "y"}, "fix(framework): lua")
    assert api.run("base") == 1
    repo.commit({"framework/lua/meta/a.lua": "z"}, "fix(framework)!: lua")
    assert api.run("base") == 0


def test_lua_warning_passes(
    repo: GitRepo, lua: list[Finding], capsys: pytest.CaptureFixture[str]
) -> None:
    lua.append(Finding("framework", "warning", "api_version did not rise"))
    repo.commit({"framework/lua/meta/a.lua": "y"}, "fix(framework): lua")
    assert api.run("base") == 0
    assert "warning: framework warning" in out(capsys)


def test_cli(repo: GitRepo) -> None:
    assert cli.main(["api", "--base", "base"]) == 0


@pytest.fixture
def schema_repo(git_repo: GitRepo, registry_root: Path) -> GitRepo:
    """beta carries a schema and, as the registry sees it, depends on alpha."""
    shutil.copytree(registry_root, git_repo.path, dirs_exist_ok=True)
    cfg = git_repo.path / "release-please-config.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    x = data["packages"]["apps/beta"]["x-modkit"]
    x["schemas"] = [{"name": "s", "load": "m:load", "corpus": ["apps/beta/*.toml"]}]
    x["depends"] = ["alpha"]
    cfg.write_text(json.dumps(data), encoding="utf-8")
    base = git_repo.commit({INIT: GOOD}, "chore(repo): base")
    git_repo.branch("base", base)
    return git_repo


@pytest.fixture
def schema_runs(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    runs: list[str] = []
    monkeypatch.setattr(api_schema, "check", lambda m, base, head: runs.append(m.name) or [])
    deps = {"packages/alpha/": {"alpha", "beta"}, "framework/": {"framework"}}
    monkeypatch.setattr(
        api,
        "affected_modules",
        lambda reg, files: {
            m for f in files for p, ms in deps.items() if f.startswith(p) for m in ms
        },
    )
    return runs


def test_schema_runs_for_dependent(schema_repo: GitRepo, schema_runs: list[str]) -> None:
    schema_repo.commit({INIT: GOOD + 'def g():\n    """Doc."""\n'}, "feat(alpha): g")
    assert api.run("base") == 0
    assert schema_runs == ["beta"]


def test_schema_own_change(schema_repo: GitRepo, schema_runs: list[str]) -> None:
    schema_repo.commit({"apps/beta/x.txt": "x"}, "chore(beta): file")
    assert api.run("base") == 0
    assert schema_runs == ["beta"]


def test_schema_skips_unaffected(schema_repo: GitRepo, schema_runs: list[str]) -> None:
    schema_repo.commit({"framework/x.txt": "x"}, "chore(framework): file")
    assert api.run("base") == 0
    assert schema_runs == []
