# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The studio's saved settings, toolkit-free: `store` is the window's QSettings once it is up."""

from __future__ import annotations

from typing import Protocol


class Store(Protocol):
    """Saved strings by key."""

    def get(self, key: str) -> str | None: ...

    def put(self, key: str, value: str | None) -> None:
        """None forgets `key`."""
        ...


class Memory:
    """A `Store` in a dict: the one in use until the window installs its own."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def put(self, key: str, value: str | None) -> None:
        if value is None:
            self.values.pop(key, None)
        else:
            self.values[key] = value


store: Store = Memory()


def use(s: Store) -> None:
    """Settings come from `s` from now on."""
    global store
    store = s
