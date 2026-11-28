# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Git queries in the registry root (the cwd when no registry is above it)."""

from __future__ import annotations

import io
import subprocess
import tarfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from modkit_ci import registry


class GitError(RuntimeError):
    """A git command failed."""


def _root() -> Path:
    try:
        return registry.find_root()
    except registry.RegistryError:
        return Path.cwd()


def _run(*args: str) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(["git", *args], cwd=_root(), capture_output=True, check=False)
    if proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", "replace").strip())
    return proc


def _out(*args: str) -> str:
    return _run(*args).stdout.decode("utf-8")


def merge_base(a: str, b: str = "HEAD") -> str:
    """The best common ancestor of two refs."""
    return _out("merge-base", a, b).strip()


def changed_files(base: str, head: str = "HEAD") -> list[str]:
    """Files changed on `head` since it left `base` (merge-base diff, no rename detection)."""
    out = _out("diff", "--no-renames", "--name-only", "-z", f"{base}...{head}")
    return [f for f in out.split("\0") if f]


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: tuple[str, ...]
    message: str
    files: tuple[str, ...]

    @property
    def is_merge(self) -> bool:
        """More than one parent."""
        return len(self.parents) > 1


_SEP = "\x1e"
_FIELD = "\x1f"


def commits(base: str, head: str = "HEAD") -> list[Commit]:
    """Commits in base..head, oldest first, merges flagged by their parents."""
    fmt = f"{_SEP}%H{_FIELD}%P{_FIELD}%B{_FIELD}"
    out = _out(
        "log",
        "--reverse",
        "--no-renames",
        f"--format={fmt}",
        "--name-only",
        "-z",
        f"{base}..{head}",
    )
    result: list[Commit] = []
    for chunk in out.split(_SEP):
        if not chunk.strip("\0\n"):
            continue
        sha, parents, message, files = chunk.split(_FIELD, 3)
        result.append(
            Commit(
                sha=sha.strip("\0\n"),
                parents=tuple(parents.split()),
                message=message.rstrip("\n"),
                files=tuple(f for f in files.lstrip("\0\n").split("\0") if f),
            )
        )
    return result


def materialize(ref: str, dest: Path, paths: Sequence[str] = ()) -> Path:
    """Extract `ref` (or only `paths` of it) into `dest` with git archive."""
    dest.mkdir(parents=True, exist_ok=True)
    data = _run("archive", "--format=tar", ref, "--", *paths).stdout
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    return dest


def show(ref: str, path: str) -> str | None:
    """The text of `path` at `ref`, or None if it does not exist there."""
    try:
        return _out("show", f"{ref}:{path}")
    except GitError:
        return None
