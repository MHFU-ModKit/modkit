# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""MHFU ModKit for Blender: import MHFU monster PACs, export them and push them into the game."""

import bpy

# operators and panels, registered in this order; bl_idname and prefs are keyed on __package__
CLASSES: tuple[type, ...] = ()


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
