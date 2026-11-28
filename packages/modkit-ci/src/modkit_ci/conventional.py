# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Extraction from Conventional Commit messages; validity is `commits.check`'s."""

from __future__ import annotations

import re
from dataclasses import dataclass

from conventional_pre_commit.format import ConventionalCommit

# release-please's breaking rules, read wide: a note keyword at a line start (its parser's notes
# pattern, any case), or the hyphen form anywhere in the body (commit.ts)
_NOTE = re.compile(r"^[\s|*]*BREAKING[ -]CHANGE[:\s]", re.MULTILINE | re.IGNORECASE)
_HYPHEN = re.compile(r"BREAKING-CHANGE:")


@dataclass(frozen=True)
class Header:
    type: str
    scopes: tuple[str, ...]
    bang: bool
    subject: str


def header(message: str) -> Header | None:
    """The parsed first line, or None when it is not `type(scope)!: subject`."""
    match = ConventionalCommit(message.split("\n", 1)[0], scope_optional=True).match()
    if match is None or not match["delim"] or not (match["subject"] or "").strip():
        return None
    raw = match["scope"].strip("()")
    scopes = tuple(s.strip() for s in raw.split(",") if s.strip())
    return Header(match["type"], scopes, "!" in match["delim"], match["subject"].strip())


def is_breaking(message: str) -> bool:
    """Header bang, or a body that release-please reads as a breaking change."""
    parsed = header(message)
    if parsed is not None and parsed.bang:
        return True
    body = message.split("\n", 1)[1] if "\n" in message else ""
    return _NOTE.search(body) is not None or _HYPHEN.search(body) is not None
