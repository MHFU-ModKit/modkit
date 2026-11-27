# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Commit message validity, one function for the commit-msg hook and for CI."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from conventional_pre_commit.format import ConventionalCommit

from modkit_ci import conventional, git, registry
from modkit_ci.registry import Registry

_FORMAT = "type(scope): summary; scope is a module name or 'repo'"


def check(
    message: str,
    scopes: Sequence[str],
    *,
    ci: bool,
    files: Sequence[str] = (),
    registry: Registry | None = None,
) -> list[str]:
    """Problems with one commit message; empty when it is valid.

    `ci` also rejects autosquash commits and a breaking one that touches no module
    path of `registry` (needs `registry` and the commit's `files`).
    """
    commit = ConventionalCommit(message, scope_optional=False, scopes=list(scopes))
    if commit.is_merge():
        return []
    if commit.has_autosquash_prefix():
        return ["fixup!/squash! commits must be squashed before merging"] if ci else []
    problems = []
    if not commit.is_valid():
        missing = ", ".join(commit.errors()) or "format"
        problems.append(f"not a valid commit message ({missing}): {_FORMAT}")
    elif ci and registry is not None and conventional.is_breaking(message):
        if not any(registry.release_owner(f) for f in files):
            problems.append(
                "a breaking change must touch the module it breaks; an empty one marks every module"
            )
    return problems


def _subject(message: str) -> str:
    return message.split("\n", 1)[0]


def _cmd(args: argparse.Namespace) -> int:
    reg = registry.load()
    if args.command == "commit-msg":
        message = Path(args.file).read_text(encoding="utf-8")
        problems = check(message, reg.scopes(), ci=False)
        for problem in problems:
            print(f"{_subject(message)}: {problem}", file=sys.stderr)
        return 1 if problems else 0
    failed = 0
    for commit in git.commits(args.base, args.head):
        if commit.is_merge:
            continue
        for problem in check(
            commit.message, reg.scopes(), ci=True, files=commit.files, registry=reg
        ):
            print(f"{commit.sha[:8]} {_subject(commit.message)}: {problem}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `commit-msg` and `commits` commands."""
    hook = sub.add_parser("commit-msg", help="check a commit message file")
    hook.add_argument("file")
    hook.set_defaults(func=_cmd)
    ci = sub.add_parser("commits", help="check every commit in base..head")
    ci.add_argument("--base", required=True)
    ci.add_argument("--head", default="HEAD")
    ci.set_defaults(func=_cmd)
