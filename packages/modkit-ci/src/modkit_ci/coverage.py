# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Coverage floors that only rise."""

from __future__ import annotations

import argparse
import tomllib
from collections.abc import Mapping

from modkit_ci import git
from modkit_ci.affected import all_units
from modkit_ci.findings import Finding, annotate, summary
from modkit_ci.registry import load


def floor(text: str | None) -> float | None:
    """`[tool.coverage.report] fail_under` of a pyproject's text, or None."""
    if text is None:
        return None
    value = tomllib.loads(text).get("tool", {}).get("coverage", {}).get("report", {})
    result = value.get("fail_under")
    return None if result is None else float(result)


_EXCLUDES = (
    ("run", "omit"),
    ("report", "omit"),
    ("report", "exclude_lines"),
    ("report", "exclude_also"),
)


def excludes(text: str | None) -> frozenset[str]:
    """Every omit and exclusion entry of a pyproject's coverage config, tagged by key."""
    if text is None:
        return frozenset()
    cfg = tomllib.loads(text).get("tool", {}).get("coverage", {})
    return frozenset(
        f"{section}.{key}: {entry}"
        for section, key in _EXCLUDES
        for entry in cfg.get(section, {}).get(key, [])
    )


def check_excludes(
    base: Mapping[str, frozenset[str]], head: Mapping[str, frozenset[str]]
) -> list[Finding]:
    """Findings for every unit that excludes more at head than at base."""
    return [
        Finding(unit, "break", f"coverage excludes grew: {entry}")
        for unit, after in head.items()
        if unit in base
        for entry in sorted(after - base[unit])
    ]


def check(base: Mapping[str, float | None], head: Mapping[str, float | None]) -> list[Finding]:
    """Findings for every unit whose floor fell or vanished (units new at head pass)."""
    findings = []
    for unit, before in base.items():
        if before is None or unit not in head:
            continue
        after = head[unit]
        if after is None:
            findings.append(Finding(unit, "break", f"coverage floor {before:g} was removed"))
        elif after < before:
            message = f"coverage floor fell from {before:g} to {after:g}"
            findings.append(Finding(unit, "break", message))
    return findings


def _cmd(args: argparse.Namespace) -> int:
    registry = load()
    base: dict[str, float | None] = {}
    head: dict[str, float | None] = {}
    base_ex: dict[str, frozenset[str]] = {}
    head_ex: dict[str, frozenset[str]] = {}
    for unit in all_units(registry, registry.root / "uv.lock"):
        if unit.pyproject is None:
            continue
        text = (registry.root / unit.pyproject).read_text(encoding="utf-8")
        old = git.show(args.base, unit.pyproject)
        head[unit.name], base[unit.name] = floor(text), floor(old)
        head_ex[unit.name], base_ex[unit.name] = excludes(text), excludes(old)
    findings = [*check(base, head), *check_excludes(base_ex, head_ex)]
    annotate(findings, "error")
    summary("Coverage floors", findings)
    return 1 if findings else 0


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `coverage` command."""
    p = sub.add_parser("coverage", help="fail when a coverage floor fell or the excludes grew")
    p.add_argument("--base", required=True)
    p.set_defaults(func=_cmd)
