# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
import subprocess
import sys

from mhfu.entries import ACTION_INPUT_BASE, entry_clip, input_action

NATIVE = ("psutil", "rabbitizer")
"""Wheels the Blender add-on cannot bundle (`mhfu_port`'s side: test_model)."""


def test_entry_clip():
    assert (
        entry_clip(7, 0) == (0, 7)
        and entry_clip(117, 1) == (3, 17)
        and entry_clip(50, 2) == (4, 50)
    )


def test_input_action():
    assert input_action(ACTION_INPUT_BASE + 2 * 200 + 117, 2) == 117


def test_loads_without_native_wheels():
    """What the Blender add-on imports from mhfu."""
    block = "".join(f"sys.modules[{m!r}] = None; " for m in NATIVE)
    mods = "mhfu.entries, mhfu.inject"
    code = f"import sys; {block}import {mods}; mhfu.inject.lane({{}})"
    subprocess.run([sys.executable, "-c", code], check=True)
