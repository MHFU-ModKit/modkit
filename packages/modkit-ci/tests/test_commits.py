# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""commits.check and the commit-msg and commits commands."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from modkit_ci import cli, commits, registry

if TYPE_CHECKING:
    from conftest import GitRepo

SCOPES = ("framework", "alpha", "mhfu-port", "repo")

VALID = [
    "feat(framework): add a hook",
    "fix(alpha, repo): both",
    "fix(alpha,repo): both",
    "feat(mhfu-port): port",
    "feat(alpha)!: drop it",
    "feat(alpha): x\n\nbody text",
    "feat(alpha): x\n\nBREAKING CHANGE: gone",
    "chore(repo): release mhfu 0.1.1",
    "chore(repo): refresh uv.lock",
    "Merge branch 'a' into b",
    "Merge pull request from x/y",
    "fixup! feat(alpha): x",
    "feat(alpha): x\n# comment line\n",
    "feat(alpha): x\n\nbody\n# Please enter the commit message\n",
]

INVALID = [
    "framework: x",
    "fix: x",
    "fix(unknown): x",
    "fix(alpha, nope): x",
    "wip(alpha): x",
    "fix(alpha) x",
    "fix(alpha):x",
    "fix(alpha): x\nno blank line",
    "feat(alpha): x\n\nBreaking change: gone",
    "",
    "# only a comment",
]


@pytest.mark.parametrize("message", VALID)
def test_hook_accepts(message: str) -> None:
    assert commits.check(message, SCOPES, ci=False) == []


@pytest.mark.parametrize("message", INVALID)
def test_hook_rejects(message: str) -> None:
    assert commits.check(message, SCOPES, ci=False)


def test_ci_rejects_autosquash() -> None:
    assert commits.check("fixup! feat(alpha): x", SCOPES, ci=True)
    assert commits.check("squash! feat(alpha): x", SCOPES, ci=True)


def test_ci_keeps_merges() -> None:
    assert commits.check("Merge branch 'a'", SCOPES, ci=True) == []


def test_hook_file(tmp_path: Path, registry_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(registry_root)
    file = tmp_path / "MSG"
    file.write_text("fix(alpha): x\n\nbody\n# comment\n", encoding="utf-8")
    assert cli.main(["commit-msg", str(file)]) == 0
    file.write_text("framework: x\n# comment\n", encoding="utf-8")
    assert cli.main(["commit-msg", str(file)]) == 1
    file.write_text("fix(gamma): x\n", encoding="utf-8")
    assert cli.main(["commit-msg", str(file)]) == 1


def test_hook_scopes_come_from_registry(
    tmp_path: Path, registry_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(registry_root)
    assert set(registry.load().scopes()) == {"framework", "alpha", "sub", "beta", "repo"}
    file = tmp_path / "MSG"
    file.write_text("fix(beta, sub): x\n", encoding="utf-8")
    assert cli.main(["commit-msg", str(file)]) == 0


@pytest.fixture
def repo(git_repo: GitRepo, registry_root: Path) -> GitRepo:
    files = {
        p.relative_to(registry_root).as_posix(): p.read_text(encoding="utf-8")
        for p in registry_root.rglob("*")
        if p.is_file()
    }
    for d in registry_root.rglob("*"):  # git keeps no empty dirs
        if d.is_dir():
            files[f"{d.relative_to(registry_root).as_posix()}/.keep"] = ""
    git_repo.commit(files, "chore(repo): base")
    return git_repo


def run(repo: GitRepo, base: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = cli.main(["commits", "--base", base])
    return code, capsys.readouterr().err


def test_ci_valid_range(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    base = repo.git("rev-parse", "HEAD")
    repo.commit({"packages/alpha/a.py": "1"}, "feat(alpha): a")
    repo.commit({"docs/x.md": "1"}, "docs(repo): x\n\nbody")
    assert run(repo, base, capsys) == (0, "")


def test_ci_skips_merge(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    base = repo.git("rev-parse", "HEAD")
    left = repo.commit({"packages/alpha/l.py": "1"}, "feat(alpha): left")
    right = repo.commit({"packages/alpha/r.py": "1"}, "feat(alpha): right", parents=[base])
    repo.commit({}, "Merge right into left", parents=[left, right])
    assert run(repo, base, capsys)[0] == 0


def test_ci_reports_each_bad_commit(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    base = repo.git("rev-parse", "HEAD")
    bad = repo.commit({"packages/alpha/a.py": "1"}, "framework: x")
    fix = repo.commit({"packages/alpha/b.py": "1"}, "fixup! feat(alpha): a")
    code, err = run(repo, base, capsys)
    assert code == 1
    assert f"{bad[:8]} framework: x" in err
    assert f"{fix[:8]} fixup! feat(alpha): a" in err


def test_ci_empty_breaking(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    base = repo.git("rev-parse", "HEAD")
    repo.commit({}, "feat(alpha)!: empty")
    code, err = run(repo, base, capsys)
    assert code == 1
    assert "must touch the module" in err


def test_ci_breaking_in_data_path_only(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    # `data` is alpha's CI path, not a release path
    base = repo.git("rev-parse", "HEAD")
    repo.commit({"data/x.txt": "1"}, "feat(alpha)!: data")
    assert run(repo, base, capsys)[0] == 1


def test_ci_breaking_in_module(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    base = repo.git("rev-parse", "HEAD")
    repo.commit({"framework/lua/a.lua": "1"}, "feat(framework)!: drop")
    repo.commit({"packages/alpha/a.py": "1"}, "fix(alpha): y\n\nBREAKING CHANGE: gone")
    assert run(repo, base, capsys) == (0, "")


def test_ci_breaking_footer_empty(repo: GitRepo, capsys: pytest.CaptureFixture[str]) -> None:
    base = repo.git("rev-parse", "HEAD")
    repo.commit({"README.md": "1"}, "fix(repo): y\n\nBREAKING CHANGE: gone")
    assert run(repo, base, capsys)[0] == 1
