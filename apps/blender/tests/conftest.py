# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 sp00ktober
"""Tests taking `bpy` or `extension` skip outside Blender; `build.py test` runs them inside it.

fake-bpy-module installs only `bpy-stubs`, so `import bpy` fails anywhere but in Blender.
"""

import os
from types import ModuleType

import pytest


@pytest.fixture(scope="session")
def bpy() -> ModuleType:
    return pytest.importorskip("bpy", reason="needs Blender: uv run apps/blender/build.py test")


@pytest.fixture(scope="session")
def extension(bpy: ModuleType) -> str:
    """The installed extension's module name, enabled."""
    import addon_utils

    name = os.environ.get("MHFU_BLENDER_EXTENSION")
    if not name:
        pytest.skip("the extension is installed by: uv run apps/blender/build.py test")
    assert addon_utils.enable(name, default_set=True), "enable failed: traceback in stderr"
    return name
