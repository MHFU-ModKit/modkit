# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Text as the window shows it: messages and the camera line."""

from __future__ import annotations

import os

from mhfu_studio.shell.camera import OrbitCamera

#: symbols the tools' messages carry, as text every font draws
_GLYPHS = {
    "\U0001f534": "[!]",
    "\U0001f7e2": "[ok]",
    "\u26a0\ufe0f": "[!]",
    "\u26a0": "[!]",
    "\u2716": "x",
    "\u2714": "ok",
    "\u25cf": "*",
    "\u2192": "->",
}


def plain(text: str) -> str:
    """`text` in plain glyphs, with the working and home directories shortened."""
    for bad, good in _GLYPHS.items():
        text = text.replace(bad, good)
    for root, short in ((os.getcwd() + os.sep, ""), (os.path.expanduser("~") + os.sep, "~/")):
        if root not in (os.sep, ""):
            text = text.replace(root, short)
    return text


def camera_line(cam: OrbitCamera) -> str:
    return f"yaw {cam.yaw:.0f}\u00b0  pitch {cam.pitch:.0f}\u00b0  dist {cam.distance:.0f}"
