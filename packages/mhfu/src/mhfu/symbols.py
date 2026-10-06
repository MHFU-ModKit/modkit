# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""What a code address is called: its `fn` or `code` entry in addresses.toml, else the name a
generated names table gives it.

The names table is JSON, made from the MHP2G decomp by matching code between the JP and EU
releases, and never committed (it is derived from the game):

    {"source": "<how it was made>",
     "names": {"0x0886...": {"name": "ObjBase::testAnimation(bool, unsigned char)",
                              "symbol": "testAnimation__7ObjBaseFbUc", "how": "<match kind>"}}}

`name` and `symbol` are required, anything else is the maker's. It lives at $MHFU_NAMES, by
default ~/.cache/modkit/names/ULES01213.json.
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any

from . import addresses

CODE_TYPES = frozenset({"fn", "code"})


def names_path() -> Path:
    default = f"~/.cache/modkit/names/{addresses.GAME_ID}.json"
    return Path(os.environ.get("MHFU_NAMES", default)).expanduser()


def name(va: int) -> str | None:
    """The addresses.toml name of `va`, else the names table's, else None."""
    if (own := _own().get(va)) is not None:
        return own
    entry = generated().get(va)
    return None if entry is None else str(entry["name"])


@functools.cache
def _own() -> dict[int, str]:
    return {int(a): n for n, a in addresses.table().addresses.items() if a.type in CODE_TYPES}


@functools.cache
def generated() -> dict[int, dict[str, Any]]:
    """The names table by address; empty when none has been made."""
    path = names_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {int(va, 16): entry for va, entry in data["names"].items()}


def own(va: int) -> str | None:
    """The addresses.toml name of `va` alone."""
    return _own().get(va)


@functools.cache
def functions() -> dict[int, dict[str, Any]]:
    """Every function the names table matched to the JP build, named or not, by address:
    `jp` (its address in the MHP2G decomp), `module`, `how`."""
    path = names_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {int(va, 16): entry for va, entry in data.get("functions", {}).items()}
