# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""git.py against throwaway repos."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from modkit_ci import git

if TYPE_CHECKING:
    from conftest import GitRepo  # the fixture's type; tests are not importable by name


def history(repo: GitRepo) -> tuple[str, str, str]:
    base = repo.commit({"a.txt": "1", "keep.txt": "k"}, "feat(x): base")
    mid = repo.commit({"a.txt": "2", "sub/b.txt": "b"}, "fix(x): mid\n\nbody")
    top = repo.commit({"a.txt": None, "c.txt": "c"}, "chore(x): top")
    return base, mid, top


def test_commit_helper(git_repo: GitRepo) -> None:
    base, mid, _ = history(git_repo)
    assert git_repo.git("rev-parse", f"{mid}^") == base


def test_changed_files(git_repo: GitRepo) -> None:
    base, _, top = history(git_repo)
    assert sorted(git.changed_files(base, top)) == ["a.txt", "c.txt", "sub/b.txt"]


def test_changed_files_no_renames(git_repo: GitRepo) -> None:
    base = git_repo.commit({"old.txt": "same content\n" * 20}, "feat(x): a")
    top = git_repo.commit({"old.txt": None, "new.txt": "same content\n" * 20}, "feat(x): b")
    assert sorted(git.changed_files(base, top)) == ["new.txt", "old.txt"]


def test_changed_files_since_merge_base(git_repo: GitRepo) -> None:
    root = git_repo.commit({"r": "r"}, "feat(x): root")
    main = git_repo.commit({"m": "m"}, "feat(x): main side", parents=[root])
    side = git_repo.commit({"s": "s"}, "feat(x): side", parents=[root])
    assert git.merge_base(main, side) == root
    assert git.changed_files(main, side) == ["s"]


def test_commits(git_repo: GitRepo) -> None:
    base, mid, top = history(git_repo)
    found = git.commits(base, top)
    assert [c.sha for c in found] == [mid, top]
    assert found[0].message == "fix(x): mid\n\nbody"
    assert found[0].parents == (base,)
    assert sorted(found[0].files) == ["a.txt", "sub/b.txt"]
    assert sorted(found[1].files) == ["a.txt", "c.txt"]
    assert not any(c.is_merge for c in found)


def test_commits_empty_commit(git_repo: GitRepo) -> None:
    base = git_repo.commit({"a": "1"}, "feat(x): a")
    top = git_repo.commit({}, "feat(x)!: empty")
    (found,) = git.commits(base, top)
    assert found.files == ()
    assert found.message == "feat(x)!: empty"


def test_commits_flag_merges(git_repo: GitRepo) -> None:
    root = git_repo.commit({"r": "r"}, "feat(x): root")
    left = git_repo.commit({"l": "l"}, "feat(x): left", parents=[root])
    right = git_repo.commit({"g": "g"}, "feat(x): right", parents=[root])
    merge = git_repo.commit({}, "Merge right into left", parents=[left, right])
    found = {c.sha: c for c in git.commits(root, merge)}
    assert found[merge].is_merge
    assert found[merge].parents == (left, right)
    assert not found[left].is_merge
    assert set(found) == {left, right, merge}


def test_commits_oldest_first(git_repo: GitRepo) -> None:
    base, mid, top = history(git_repo)
    assert [c.sha for c in git.commits(base, top)] == [mid, top]


def test_materialize(git_repo: GitRepo, tmp_path: Path) -> None:
    base, _, top = history(git_repo)
    out = git.materialize(base, tmp_path / "base")
    assert (out / "a.txt").read_text() == "1"
    assert not (out / "c.txt").exists()
    part = git.materialize(top, tmp_path / "part", ["sub"])
    assert (part / "sub/b.txt").read_text() == "b"
    assert not (part / "keep.txt").exists()


def test_show(git_repo: GitRepo) -> None:
    base, _, top = history(git_repo)
    assert git.show(base, "a.txt") == "1"
    assert git.show(top, "a.txt") is None


def test_git_error(git_repo: GitRepo) -> None:
    git_repo.commit({"a": "1"}, "feat(x): a")
    with pytest.raises(git.GitError):
        git.merge_base("nonexistent")
