# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Fixtures: a throwaway git repo and a minimal registry tree."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from modkit_ci.registry import CONFIG, MANIFEST

_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_CONFIG_COUNT": "0",
}


class GitRepo:
    """A temporary repo; the cwd while the fixture lives."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str, env: dict[str, str] | None = None) -> str:
        """Run git in the repo and return stdout."""
        full = {**os.environ, **_ENV, **(env or {})}
        proc = subprocess.run(
            ["git", *args], cwd=self.path, env=full, capture_output=True, text=True, check=True
        )
        return proc.stdout.strip()

    def commit(
        self, files: dict[str, str | None], message: str, parents: list[str] | None = None
    ) -> str:
        """Write `files` (None deletes), commit on top of `parents` (default HEAD); the sha.

        With several parents the tree is the first parent's plus `files` (a merge).
        """
        if parents is None:
            heads = self.git("rev-parse", "-q", "--verify", "HEAD") if self._has_head() else ""
            parents = [heads] if heads else []
        if parents:
            self.git("checkout", "-q", "--detach", parents[0])
            self.git("read-tree", "--reset", "-u", parents[0])
        for rel, text in files.items():
            target = self.path / rel
            if text is None:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
        self.git("add", "-A")
        tree = self.git("write-tree")
        args = ["commit-tree", tree, "-m", message]
        for parent in parents:
            args += ["-p", parent]
        sha = self.git(*args)
        self.git("checkout", "-q", "--detach", sha)
        return sha

    def _has_head(self) -> bool:
        try:
            self.git("rev-parse", "-q", "--verify", "HEAD")
        except subprocess.CalledProcessError:
            return False
        return True

    def branch(self, name: str, sha: str) -> None:
        """Point branch `name` at `sha`."""
        self.git("branch", "-f", name, sha)


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[GitRepo]:
    """A fresh repo, entered as the cwd, committing detached; `commit(files, message)`."""
    path = tmp_path / "repo"
    path.mkdir()
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(path)
    yield GitRepo(path)


PACKAGES: dict[str, Any] = {
    "framework": {
        "component": "framework",
        "release-type": "simple",
        "x-modkit": {"api": ["lua"], "lua": ["framework/lua/meta"], "depends": ["alpha"]},
    },
    "packages/alpha": {
        "component": "alpha",
        "release-type": "python",
        "x-modkit": {"api": ["python"], "paths": ["data"]},
    },
    "packages/alpha/sub": {"component": "sub", "release-type": "python"},
    "apps/beta": {"component": "beta", "release-type": "python", "x-modkit": {"qt": True}},
}


def write_registry(root: Path, packages: dict[str, Any] | None = None) -> Path:
    """Write the config, manifest, `uv.lock` and the module dirs under `root`."""
    packages = PACKAGES if packages is None else packages
    config = {"bootstrap-sha": "0" * 40, "packages": packages}
    (root / CONFIG).write_text(json.dumps(config), encoding="utf-8")
    (root / MANIFEST).write_text(json.dumps({p: "0.1.0" for p in packages}), encoding="utf-8")
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    for path, entry in packages.items():
        (root / path).mkdir(parents=True, exist_ok=True)
        if entry.get("release-type") == "python":
            (root / path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        for extra in entry.get("x-modkit", {}).get("paths", []):
            (root / extra).mkdir(parents=True, exist_ok=True)
        for lua in entry.get("x-modkit", {}).get("lua", []):
            (root / lua).mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def registry_root(tmp_path: Path) -> Path:
    """A tmp tree with a minimal registry: framework, alpha (+data), alpha/sub, beta (qt)."""
    root = tmp_path / "reg"
    root.mkdir()
    return write_registry(root)
