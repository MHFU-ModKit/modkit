# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Executor entries and animation streams: which clip each body part plays for an action id.

Standard library only, so the Blender add-on, which bundles no native wheels, can import it.
"""

from __future__ import annotations

ENTRY_BANK = 100
"""Slots per animation stream, and executor entries per bank."""
PART_STREAMS = 2
"""Streams per body part: `Bone.stream` k plays streams `PART_STREAMS * k` and the one after,
entries 0-99 the first and 100 up the second."""
ACTION_INPUT_BASE = 0x3E8
"""ENTITY.ANIM_INPUT[k] is the executor action id + ACTION_INPUT_BASE + k * ACTION_INPUT_STEP."""
ACTION_INPUT_STEP = PART_STREAMS * ENTRY_BANK


def entry_clip(entry: int, part: int) -> tuple[int, int]:
    """(stream, slot) body part `part` plays for executor entry `entry`."""
    return divmod(entry + part * ACTION_INPUT_STEP, ENTRY_BANK)


def input_action(value: int, part: int) -> int:
    """The executor action id in ENTITY.ANIM_INPUT[part]."""
    return value - ACTION_INPUT_BASE - part * ACTION_INPUT_STEP
