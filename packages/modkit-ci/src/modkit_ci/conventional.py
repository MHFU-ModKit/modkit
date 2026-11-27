# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Extraction from Conventional Commit messages; validity is `commits.check`'s."""

from __future__ import annotations

import re
from dataclasses import dataclass

_HEADER = re.compile(
    r"^(?P<type>[A-Za-z]+)(?:\((?P<scopes>[^()]*)\))?(?P<bang>!)?: (?P<subject>.+)$"
)
_BREAKING = re.compile(r"^BREAKING[ -]CHANGE: ", re.MULTILINE)
_AUTOSQUASH = ("fixup!", "squash!", "amend!")


@dataclass(frozen=True)
class Header:
    type: str
    scopes: tuple[str, ...]
    bang: bool
    subject: str


def header(message: str) -> Header | None:
    """The parsed first line, or None when it is not `type(scope)!: subject`."""
    match = _HEADER.match(message.split("\n", 1)[0])
    if match is None:
        return None
    raw = match["scopes"]
    scopes = tuple(s.strip() for s in raw.split(",") if s.strip()) if raw else ()
    return Header(match["type"], scopes, bool(match["bang"]), match["subject"])


def is_breaking(message: str) -> bool:
    """Header bang, or a line starting `BREAKING CHANGE: ` or `BREAKING-CHANGE: `."""
    parsed = header(message)
    if parsed is not None and parsed.bang:
        return True
    return _BREAKING.search(message.split("\n", 1)[1] if "\n" in message else "") is not None


def is_autosquash(message: str) -> bool:
    """A `fixup!`, `squash!` or `amend!` commit."""
    return message.startswith(_AUTOSQUASH)
