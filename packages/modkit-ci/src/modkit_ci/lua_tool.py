# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""The one loader of framework/tools/lua_api.py, a script and not a package."""

from __future__ import annotations

import importlib.util
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Final

# modkit-ci is never installed away from the checkout, so the script sits at a fixed distance
SCRIPT: Final = Path(__file__).resolve().parents[4] / "framework" / "tools" / "lua_api.py"


@cache
def load() -> ModuleType:
    """The `lua_api` module: `parse`, `examples`, `block_fences`."""
    spec = importlib.util.spec_from_file_location("lua_api", SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
