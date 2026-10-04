# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The installed zip: it enables and disables, and its bundled wheels cover what it imports."""

import importlib
import pkgutil
from types import ModuleType
from typing import NoReturn


def _raise(error: BaseException) -> NoReturn:
    """For addon_utils' `handle_error`, which otherwise prints the error and carries on."""
    raise error


def _reraise(name: str) -> NoReturn:
    """For walk_packages' `onerror`, called inside its except block; it skips ImportError."""
    raise


def test_enable_disable(bpy: ModuleType, extension: str) -> None:
    import addon_utils

    addons = bpy.context.preferences.addons
    addon_utils.disable(extension, default_set=True, handle_error=_raise)
    assert extension not in addons
    addon_utils.enable(extension, default_set=True, handle_error=_raise)
    assert extension in addons


def test_every_module_imports(extension: str) -> None:
    package = importlib.import_module(extension)
    for module in pkgutil.walk_packages(package.__path__, f"{extension}.", onerror=_reraise):
        importlib.import_module(module.name)
