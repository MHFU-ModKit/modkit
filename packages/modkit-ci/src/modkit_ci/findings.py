# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Findings of the API checks, as GitHub annotations and a step summary."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Kind = Literal["break", "doc", "warning"]
Level = Literal["error", "warning", "notice"]


@dataclass(frozen=True)
class Finding:
    module: str
    kind: Kind
    message: str
    path: str | None = None
    line: int | None = None


def _escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotate(findings: Iterable[Finding], level: Level) -> None:
    """Print each finding as a workflow command on GitHub, else as a stderr line."""
    on_github = os.environ.get("GITHUB_ACTIONS") == "true"
    for f in findings:
        if on_github:
            props = []
            if f.path:
                props.append(f"file={f.path}")
                if f.line is not None:
                    props.append(f"line={f.line}")
            props.append(f"title={f.module} {f.kind}")
            print(f"::{level} {','.join(props)}::{_escape(f.message)}", flush=True)
        else:
            where = f"{f.path}:{f.line}: " if f.path and f.line else f"{f.path}: " if f.path else ""
            print(f"{level}: {f.module} {f.kind}: {where}{f.message}", file=sys.stderr)


def summary(title: str, findings: Sequence[Finding]) -> None:
    """Append a Markdown table to $GITHUB_STEP_SUMMARY when it is set."""
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    lines = [f"### {title}", ""]
    if findings:
        lines += ["| Module | Kind | Where | Message |", "|---|---|---|---|"]
        for f in findings:
            where = f"{f.path}:{f.line}" if f.path and f.line else f.path or ""
            message = f.message.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {f.module} | {f.kind} | {where} | {message} |")
    else:
        lines.append("No findings.")
    with Path(target).open("a", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n\n")
