# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Third-party notices of the framework zip, read from `framework/third_party/NOTICES.toml`."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Final

NOTICES: Final = "framework/third_party/NOTICES.toml"
MAKEFILE: Final = "framework/Makefile.psp"
THIRD_PARTY: Final = "framework/third_party"
OWN_LICENCE: Final = "MIT"
_LIB: Final = re.compile(r"(?<![\w$])-l(\w+)")
_SDK_OBJECT: Final = re.compile(r"\$\(PSPSDK\)/lib/(\w+)\.o")
_LIBS_ASSIGNMENT: Final = re.compile(r"^LIBS\s*[:+]?=(?P<value>(?:.*\\\n)*.*)$", re.MULTILINE)


class NoticeError(ValueError):
    """The notice file or the link line is unusable."""


@dataclass(frozen=True)
class Component:
    """One third-party component the PRXs carry."""

    name: str
    source: str
    spdx: str
    text: bool
    libs: tuple[str, ...]
    dirs: tuple[str, ...]
    copyright: tuple[str, ...]
    notice: str


def components(root: Path) -> tuple[Component, ...]:
    """The components named in NOTICES.toml."""
    path = root / NOTICES
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))["component"]
        return tuple(
            Component(
                name=c["name"],
                source=c["source"],
                spdx=c["spdx"],
                text=bool(c["text"]),
                libs=tuple(c.get("libs", ())),
                dirs=tuple(c.get("dirs", ())),
                copyright=tuple(c["copyright"]),
                notice=str(c.get("notice", "")).strip(),
            )
            for c in raw
        )
    except (OSError, KeyError, tomllib.TOMLDecodeError) as err:
        raise NoticeError(f"{path}: {err!r}") from err


def licence_ids(parts: tuple[Component, ...]) -> list[str]:
    """The ids whose texts the zip carries: the repo's own plus every component with `text`."""
    return sorted({OWN_LICENCE, *(c.spdx for c in parts if c.text)})


def render(parts: tuple[Component, ...]) -> str:
    """The text of THIRD_PARTY_NOTICES.txt."""
    out = [
        "The PRXs statically link the components below. The licence texts are in LICENSES/.",
        "",
    ]
    for c in parts:
        out += [
            f"{c.name} - {c.spdx}",
            f"  Source: {c.source}",
            *(f"  {line}" for line in c.copyright),
        ]
        if c.notice:
            out += ["", *(f"  {line}" if line else "" for line in c.notice.splitlines())]
        out.append("")
    return "\n".join(out)


def linked_libs(makefile: str) -> set[str]:
    """The `-l` libraries and `$(PSPSDK)/lib/*.o` objects of every `LIBS` assignment."""
    found: set[str] = set()
    for match in _LIBS_ASSIGNMENT.finditer(makefile):
        value = match["value"].replace("\\\n", " ")
        found.update(_LIB.findall(value))
        found.update(_SDK_OBJECT.findall(value))
    return found


def uncovered(root: Path) -> list[str]:
    """Linked libraries and third_party directories no component names."""
    parts = components(root)
    named = {lib for c in parts for lib in c.libs}
    dirs = {d for c in parts for d in c.dirs}
    linked = linked_libs((root / MAKEFILE).read_text(encoding="utf-8"))
    vendored = {p.name for p in (root / THIRD_PARTY).iterdir() if p.is_dir()}
    return sorted((linked - named) | {f"third_party/{d}" for d in vendored - dirs})
