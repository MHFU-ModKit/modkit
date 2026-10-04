# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""MHFU ModKit for Blender: import MHFU monster PACs, export them and push them into the game.

bpy is imported only on registering, so the modules that need none (`curves`, `stored`, `write`)
import anywhere.
"""

from typing import Any


def classes() -> tuple[type, ...]:
    """Operators and panels, registered in this order; bl_idname and prefs keyed on __package__."""
    from . import exporter, importer

    return importer.CLASSES + exporter.CLASSES


def menus() -> tuple[tuple[str, Any], ...]:
    """`(menu type, draw function)` pairs appended to Blender's menus."""
    from . import exporter, importer

    return importer.MENUS + exporter.MENUS


def register() -> None:
    import bpy

    for cls in classes():
        bpy.utils.register_class(cls)
    for menu, draw in menus():
        getattr(bpy.types, menu).append(draw)


def unregister() -> None:
    import bpy

    for menu, draw in reversed(menus()):
        getattr(bpy.types, menu).remove(draw)
    for cls in reversed(classes()):
        bpy.utils.unregister_class(cls)
