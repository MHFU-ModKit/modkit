# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""`shell.settings.Store` on the window's QSettings."""

from __future__ import annotations

from PySide6.QtCore import QSettings

from mhfu_studio.shell import settings


class QtStore:
    def __init__(self, qs: QSettings) -> None:
        self.qs = qs

    def get(self, key: str) -> str | None:
        v = self.qs.value(key)
        return v if isinstance(v, str) and v else None

    def put(self, key: str, value: str | None) -> None:
        if value is None:
            self.qs.remove(key)
        else:
            self.qs.setValue(key, value)


def install(qs: QSettings) -> None:
    """The studio's settings are `qs` from now on."""
    settings.use(QtStore(qs))
