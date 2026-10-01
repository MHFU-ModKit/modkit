# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Small imgui helpers every panel uses; imgui is imported per call so tests can fake it."""

from __future__ import annotations

import os
from collections.abc import Sequence

from mhfu_studio.shell.camera import OrbitCamera
from mhfu_studio.shell.findings import Level

#: the default font covers Latin-1 and punctuation only; these would draw as boxes
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

LEVEL_COLORS: dict[Level, tuple[float, float, float, float]] = {
    "error": (0.95, 0.42, 0.40, 1.0),
    "warning": (0.98, 0.75, 0.30, 1.0),
    "info": (0.65, 0.72, 0.85, 1.0),
}


def plain(text: str) -> str:
    """Text the default font can draw, with the working and home directories shortened."""
    for bad, good in _GLYPHS.items():
        text = text.replace(bad, good)
    for root, short in ((os.getcwd() + os.sep, ""), (os.path.expanduser("~") + os.sep, "~/")):
        if root not in (os.sep, ""):
            text = text.replace(root, short)
    return text


def camera_line(cam: OrbitCamera) -> str:
    return f"yaw {cam.yaw:.0f}\u00b0  pitch {cam.pitch:.0f}\u00b0  dist {cam.distance:.0f}"


def overlay_text(origin: tuple[float, float], text: str) -> None:
    """Shadowed text over the viewport image, without a window that could take the mouse."""
    from imgui_bundle import imgui

    draw = imgui.get_window_draw_list()
    col = imgui.get_color_u32(imgui.ImVec4(0.92, 0.94, 0.98, 0.9))
    shadow = imgui.get_color_u32(imgui.ImVec4(0.0, 0.0, 0.0, 0.6))
    x, y = origin
    for i, line in enumerate(text.splitlines()):
        draw.add_text(imgui.ImVec2(x + 11, y + 9 + i * 16), shadow, line)
        draw.add_text(imgui.ImVec2(x + 10, y + 8 + i * 16), col, line)


def help_marker(text: str) -> None:
    from imgui_bundle import imgui

    imgui.text_disabled("(?)")
    if imgui.is_item_hovered():
        imgui.set_tooltip(text)


def no_scroll_flags() -> int:
    """Window flags for a panel that fills itself; padding would make it and its FBO disagree."""
    from imgui_bundle import imgui

    return int(
        imgui.WindowFlags_.no_scrollbar.value | imgui.WindowFlags_.no_scroll_with_mouse.value
    )


def mouse_buttons() -> int:
    """Button flags for a capture button answering to all three mouse buttons."""
    from imgui_bundle import imgui

    flags = imgui.ButtonFlags_
    return int(
        flags.mouse_button_left.value
        | flags.mouse_button_right.value
        | flags.mouse_button_middle.value
    )


def pick_folder(title: str) -> str | None:
    """A native folder dialog; blocks until it closes. None if cancelled."""
    from imgui_bundle import portable_file_dialogs as pfd

    return pfd.select_folder(title).result() or None


def pick_file(title: str, filters: Sequence[str] = ("All files", "*")) -> str | None:
    """A native open dialog; blocks until it closes. None if cancelled."""
    from imgui_bundle import portable_file_dialogs as pfd

    got = pfd.open_file(title, "", list(filters)).result()
    return got[0] if got else None
