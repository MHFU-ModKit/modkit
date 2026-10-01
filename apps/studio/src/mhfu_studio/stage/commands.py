# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The `studio` commands of this area."""

from __future__ import annotations

from mhfu_studio.cli import Groups


def register(groups: Groups) -> None:
    """Adds this area's commands; nothing heavy may be imported at module level."""
