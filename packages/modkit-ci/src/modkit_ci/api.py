# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The API check: breaks need a breaking commit, doc findings never pass."""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from modkit_ci import (
    affected,
    api_lua,
    api_python,
    api_schema,
    conventional,
    findings,
    git,
    registry,
)
from modkit_ci.findings import Finding


def marked_modules(reg: registry.Registry, base: str) -> set[str]:
    """Modules with a breaking non-merge commit since `base` touching their release path."""
    marked: set[str] = set()
    for commit in git.commits(base):
        if commit.is_merge or not conventional.is_breaking(commit.message):
            continue
        for path in commit.files:
            owner = reg.release_owner(path)
            if owner:
                marked.add(owner.name)
    return marked


def affected_modules(reg: registry.Registry, files: list[str]) -> set[str]:
    """Modules whose own path or a dependency changed; the schema checks run for these."""
    return set(affected.compute(reg, files, reg.root / "uv.lock").modules)


def _module_checks(
    module: registry.Module, base_root: Path, head_root: Path, *, own: bool, dependent: bool
) -> list[Finding]:
    out: list[Finding] = []
    if own and "python" in module.api:
        out += api_python.check(module, base_root, head_root)
    if own and "lua" in module.api:
        out += api_lua.check(module, base_root, head_root)
    if dependent and module.schemas:
        out += api_schema.check(module, base_root, head_root)
    return out


def run(base: str) -> int:
    """Check the modules changed since `base`; 1 on an unmarked break or any doc finding."""
    reg = registry.load()
    merge_base = git.merge_base(base)
    files = git.changed_files(merge_base)
    changed = {m for f in files if (m := reg.owner(f))}
    dependents = affected_modules(reg, files)
    marked = marked_modules(reg, merge_base)
    results: list[Finding] = []
    with tempfile.TemporaryDirectory(prefix="modkit-ci-base-") as tmp:
        base_root = git.materialize(merge_base, Path(tmp))
        for module in reg.modules:
            results += _module_checks(
                module,
                base_root,
                reg.root,
                own=module in changed,
                dependent=module in changed or module.name in dependents,
            )
    errors = [f for f in results if f.kind == "doc"]
    errors += [f for f in results if f.kind == "break" and f.module not in marked]
    accepted = [f for f in results if f.kind == "break" and f.module in marked]
    notices = [
        Finding(m, "warning", "marked breaking, but no break was found")
        for m in sorted(marked)
        if reg.get(m) in changed and not any(f.module == m for f in accepted)
    ]
    findings.annotate(errors, "error")
    findings.annotate([f for f in results if f.kind == "warning"], "warning")
    findings.annotate(accepted, "notice")
    findings.annotate(notices, "notice")
    findings.summary("API check", [*errors, *accepted, *notices])
    return 1 if errors else 0


def _cmd(args: argparse.Namespace) -> int:
    return run(args.base)


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `api` command."""
    p = sub.add_parser("api", help="fail on an unmarked break or a doc finding")
    p.add_argument("--base", required=True)
    p.set_defaults(func=_cmd)
