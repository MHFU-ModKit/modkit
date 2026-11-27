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
    for unit in all_units(registry, registry.root / "uv.lock"):
        if unit.pyproject is None:
            continue
        head[unit.name] = floor((registry.root / unit.pyproject).read_text(encoding="utf-8"))
        base[unit.name] = floor(git.show(args.base, unit.pyproject))
    findings = check(base, head)
    annotate(findings, "error")
    summary("Coverage floors", findings)
    return 1 if findings else 0


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add the `coverage` command."""
    p = sub.add_parser("coverage", help="fail when a coverage floor fell")
    p.add_argument("--base", required=True)
    p.set_defaults(func=_cmd)
