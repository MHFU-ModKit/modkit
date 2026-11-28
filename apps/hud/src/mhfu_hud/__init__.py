# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""A live MHFU HUD next to PPSSPP: it attaches over the debugger, polls game memory on a thread
and draws it with pygame. QUEST_PREP's staged edits write game memory; the rest only reads."""

from importlib import metadata

__version__ = metadata.version("mhfu-hud")
