# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 sp00ktober
"""An assembled AI script on the memory stick, for the framework to load.

    img = asm.assemble(prog)
    stick.deploy(prog, img)      # the container and the Lua mod that installs it, on the stick

The mod calls `mhfu.brain_install`: the framework stages the container in extra RAM and points
the row's header words at it after every overlay read, so the next quest runs it. A monster that
is running keeps its script. Extra RAM is PPSSPP's 64 MB mode; a real PSP installs nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

from ... import inject as sticks
from . import asm, ir
from .inject import container


def build(p: ir.Program, img: asm.Image) -> bytes:
    """The stick container of an assembled program: load 0, no maps for a live push."""
    return container(p, img, 0, origin=False)


def lua_module(row: int, path: str) -> str:
    """The Lua mod that installs the container at `path` (as the PRX opens it) and logs the
    result; `row` is the one the container is expected to name."""
    quoted = '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"""-- AI script of species row {row}: the framework points the row at this container
-- after every overlay read. Written by `mhfu brain deploy`.
local row, err = mhfu.brain_install({quoted})
if row == {row} then
  mhfu.log(string.format("[brain] row {row} installed, id %08X", mhfu.brain_status(row).id))
else
  mhfu.log("[brain] row {row}: " .. (err or ("the container is row " .. tostring(row) .. "'s")))
end
"""


def deploy(p: ir.Program, img: asm.Image, root: str | os.PathLike[str] | None = None) -> list[Path]:
    """Write `p`'s container and its mod to the memory stick (`inject.memstick(root)`), the
    container first so the mod finds it, each atomically; both paths. The framework's mods
    directory must exist: FileNotFoundError, and nothing written."""
    mods = sticks.default_mods_dir(root)
    brain = sticks.default_brain_dir(root=root)
    mod = lua_module(p.row, sticks.brain_path(p.row))
    return [
        sticks.write_file(brain, sticks.brain_filename(p.row), build(p, img)),
        sticks.write_file(mods, sticks.brain_module(p.row), mod.encode()),
    ]
